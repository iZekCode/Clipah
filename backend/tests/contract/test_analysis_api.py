"""Contract tests for starting one Workspace-scoped highlight analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from clipah.auth.limits import RateLimitBucket, RateLimitDecision, RateLimiter
from clipah.jobs import use_cases as job_use_cases
from clipah.jobs.use_cases import (
    fail_job,
    request_job_cancellation,
    start_job,
    succeed_job,
)
from clipah.models import (
    Asset,
    AssetKind,
    AssetSourceType,
    Job,
    JobEvent,
    JobKind,
    JobStatus,
    Project,
    ProjectStatus,
    QuotaReservationStatus,
    QuotaResource,
    Transcript,
    WorkspaceQuotaReservation,
)
from clipah.source_imports.dispatch import RecordingJobDispatcher
from harness import NOW, Browser, Clock, StubGoogleProvider, assert_error, build_app, sign_in


@dataclass(frozen=True, slots=True)
class ProjectFixture:
    """Identifiers for one Project owned by the signed-in browser."""

    user_id: UUID
    workspace_id: UUID
    project_id: UUID


class RejectAnalysisLimiter(RateLimiter):
    """Permit ordinary requests while refusing the separate analysis allowance."""

    def check(
        self, *, subject: str, bucket: str, limit: int, window: timedelta
    ) -> RateLimitDecision:
        """Return a stable refusal only for the analysis-specific bucket."""
        del subject, limit, window
        if bucket == RateLimitBucket.ANALYSIS:
            return RateLimitDecision(
                allowed=False,
                remaining=0,
                retry_after=timedelta(seconds=17),
            )
        return RateLimitDecision(allowed=True, remaining=99, retry_after=timedelta())


class SingleAnalysisLimiter(RateLimiter):
    """Spend one real analysis allowance while leaving request buckets unconstrained."""

    def __init__(self) -> None:
        """Start with exactly one analysis admission available."""
        self.remaining = 1

    def check(
        self, *, subject: str, bucket: str, limit: int, window: timedelta
    ) -> RateLimitDecision:
        """Consume the sole allowance only when analysis admission reaches this boundary."""
        del subject, limit, window
        if bucket != RateLimitBucket.ANALYSIS:
            return RateLimitDecision(allowed=True, remaining=99, retry_after=timedelta())
        if self.remaining:
            self.remaining -= 1
            return RateLimitDecision(allowed=True, remaining=0, retry_after=timedelta())
        return RateLimitDecision(
            allowed=False,
            remaining=0,
            retry_after=timedelta(hours=1),
        )


@pytest.mark.integration
def test_analysis_creates_one_queued_job_and_reserves_quota_before_dispatch(
    engine: Engine, clean_database: None
) -> None:
    """Losing the broker after the response must not lose the already durable intent."""
    del clean_database
    clock = Clock(NOW)
    dispatcher = RecordingJobDispatcher(fail=True)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), job_dispatcher=dispatcher)
    browser = Browser(app)
    sign_in(browser, flow)
    fixture = _project_with_transcript(engine, browser, status=ProjectStatus.TRANSCRIBING)

    response = browser.request(
        "POST",
        _analysis_path(fixture),
        headers={"Idempotency-Key": "analysis-one"},
    )

    assert response.status_code == 202
    assert set(response.json()) == {"jobId", "status"}
    assert response.json()["status"] == "queued"
    job_id = UUID(response.json()["jobId"])
    assert dispatcher.calls == [(job_id, fixture.workspace_id, fixture.user_id)]
    assert dispatcher.kinds == [JobKind.ANALYZE]
    with engine.connect() as connection:
        job = connection.execute(
            select(Job.workspace_id, Job.project_id, Job.kind, Job.idempotency_key).where(
                Job.id == job_id
            )
        ).one()
        assert tuple(job) == (
            fixture.workspace_id,
            fixture.project_id,
            JobKind.ANALYZE,
            "analysis-one",
        )
        assert connection.scalar(select(func.count()).select_from(JobEvent)) == 1
        reservation_resource = connection.scalar(
            select(WorkspaceQuotaReservation.resource).where(
                WorkspaceQuotaReservation.reference_id == job_id
            )
        )
        assert reservation_resource is QuotaResource.ANALYSES
        assert connection.scalar(
            select(Project.status).where(Project.id == fixture.project_id)
        ) is (ProjectStatus.ANALYZING)


@pytest.mark.integration
def test_exact_analysis_replay_reuses_rows_but_key_cannot_move_to_another_project(
    engine: Engine, clean_database: None
) -> None:
    """An idempotency key must identify one analysis intent, not merely any Workspace Job."""
    del clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(
        clock, StubGoogleProvider(clock), job_dispatcher=RecordingJobDispatcher()
    )
    browser = Browser(app)
    sign_in(browser, flow)
    first_project = _project_with_transcript(
        engine, browser, status=ProjectStatus.TRANSCRIBING, key="first-project"
    )
    second_project = _project_with_transcript(
        engine, browser, status=ProjectStatus.TRANSCRIBING, key="second-project"
    )
    headers = {"Idempotency-Key": "analysis-replay"}

    first = browser.request("POST", _analysis_path(first_project), headers=headers)
    replay = browser.request("POST", _analysis_path(first_project), headers=headers)
    mismatch = browser.request("POST", _analysis_path(second_project), headers=headers)

    assert first.status_code == replay.status_code == 202
    assert replay.json() == first.json()
    assert_error(mismatch, status_code=409, code="CONFLICT")
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Job)) == 1
        assert connection.scalar(select(func.count()).select_from(WorkspaceQuotaReservation)) == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    "status",
    [
        ProjectStatus.CREATED,
        ProjectStatus.UPLOADING,
        ProjectStatus.INGESTING,
        ProjectStatus.ANALYZING,
        ProjectStatus.READY,
        ProjectStatus.FAILED,
    ],
)
def test_analysis_refuses_projects_outside_the_transcribed_precondition(
    engine: Engine, clean_database: None, status: ProjectStatus
) -> None:
    """Starting analysis early, twice, or after a terminal Project state must not spend quota."""
    del clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    fixture = _project_with_transcript(engine, browser, status=status)

    response = browser.request(
        "POST",
        _analysis_path(fixture),
        headers={"Idempotency-Key": f"analysis-{status.value}"},
    )

    assert_error(response, status_code=409, code="CONFLICT")
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Job)) == 0
        assert connection.scalar(select(func.count()).select_from(WorkspaceQuotaReservation)) == 0


@pytest.mark.integration
def test_analysis_requires_the_canonical_transcript(engine: Engine, clean_database: None) -> None:
    """A status label alone cannot authorize paid work without timestamp evidence."""
    del clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    fixture = _project_with_transcript(
        engine, browser, status=ProjectStatus.TRANSCRIBING, include_transcript=False
    )

    response = browser.request(
        "POST",
        _analysis_path(fixture),
        headers={"Idempotency-Key": "analysis-no-transcript"},
    )

    assert_error(response, status_code=409, code="CONFLICT")


@pytest.mark.integration
def test_analysis_maps_monthly_and_hourly_allowance_refusals_without_creating_work(
    engine: Engine, clean_database: None
) -> None:
    """Both plan budget and per-User pacing must fail before a Job becomes durable."""
    del clean_database
    clock = Clock(NOW)

    quota_app, quota_flow, _ = build_app(
        clock,
        StubGoogleProvider(clock),
        monthly_analyses=0,
    )
    quota_browser = Browser(quota_app)
    sign_in(quota_browser, quota_flow)
    quota_project = _project_with_transcript(
        engine, quota_browser, status=ProjectStatus.TRANSCRIBING, key="quota-project"
    )
    quota_response = quota_browser.request(
        "POST",
        _analysis_path(quota_project),
        headers={"Idempotency-Key": "analysis-quota"},
    )

    assert_error(quota_response, status_code=429, code="QUOTA_EXCEEDED")
    assert int(quota_response.headers["Retry-After"]) > 0

    limiter_app, limiter_flow, _ = build_app(
        clock,
        StubGoogleProvider(clock, subject="limiter-user", email="limiter@example.com"),
        rate_limiter=RejectAnalysisLimiter(),
    )
    limiter_browser = Browser(limiter_app)
    sign_in(limiter_browser, limiter_flow)
    limiter_project = _project_with_transcript(
        engine, limiter_browser, status=ProjectStatus.TRANSCRIBING, key="limiter-project"
    )
    limiter_response = limiter_browser.request(
        "POST",
        _analysis_path(limiter_project),
        headers={"Idempotency-Key": "analysis-hourly"},
    )

    assert_error(limiter_response, status_code=429, code="RATE_LIMITED")
    assert limiter_response.headers["Retry-After"] == "17"
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Job)) == 0


@pytest.mark.integration
def test_analysis_maps_workspace_concurrency_refusal_without_spending_quota(
    engine: Engine, clean_database: None
) -> None:
    """A full Workspace must retain the transcribed Project and create no sixth Job."""
    del clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), concurrent_jobs_per_workspace=5)
    browser = Browser(app)
    sign_in(browser, flow)
    fixture = _project_with_transcript(engine, browser, status=ProjectStatus.TRANSCRIBING)
    with engine.begin() as connection:
        connection.execute(
            Job.__table__.insert(),
            [
                {
                    "workspace_id": fixture.workspace_id,
                    "project_id": fixture.project_id,
                    "kind": JobKind.SOURCE_IMPORT,
                    "status": JobStatus.QUEUED,
                    "stage": "queued",
                    "progress": 0,
                    "attempt": 0,
                    "idempotency_key": f"active-{index}",
                }
                for index in range(5)
            ],
        )

    response = browser.request(
        "POST",
        _analysis_path(fixture),
        headers={"Idempotency-Key": "analysis-concurrency"},
    )

    assert_error(response, status_code=429, code="CONCURRENCY_LIMIT")
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Job)) == 5
        assert connection.scalar(select(func.count()).select_from(WorkspaceQuotaReservation)) == 0
        assert (
            connection.scalar(select(Project.status).where(Project.id == fixture.project_id))
            is ProjectStatus.TRANSCRIBING
        )


@pytest.mark.integration
def test_analysis_hides_a_foreign_project_exactly_like_a_missing_project(
    engine: Engine, clean_database: None
) -> None:
    """A guessed Project UUID must disclose neither ownership nor analysis readiness."""
    del clean_database
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    app, flow, _ = build_app(clock, provider)
    owner = Browser(app)
    sign_in(owner, flow)
    owned = _project_with_transcript(engine, owner, status=ProjectStatus.TRANSCRIBING)

    provider.identify(subject="stranger", email="stranger@example.com", name="Stranger")
    stranger = Browser(app)
    sign_in(stranger, flow)
    stranger_workspace = UUID(stranger.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    headers = {"Idempotency-Key": "analysis-guess"}

    valid = owner.request(
        "POST",
        _analysis_path(owned),
        headers={"Idempotency-Key": "analysis-owner-control"},
    )

    guessed = stranger.request(
        "POST",
        f"/api/v1/projects/{owned.project_id}/analysis?workspace_id={stranger_workspace}",
        headers=headers,
    )
    missing = stranger.request(
        "POST",
        f"/api/v1/projects/{uuid4()}/analysis?workspace_id={stranger_workspace}",
        headers={"Idempotency-Key": "analysis-missing"},
    )

    assert valid.status_code == 202
    assert_error(guessed, status_code=404, code="NOT_FOUND")
    assert_error(missing, status_code=404, code="NOT_FOUND")
    assert guessed.json()["error"]["message"] == missing.json()["error"]["message"]


@pytest.mark.integration
def test_concurrency_refusal_does_not_consume_the_hourly_analysis_allowance(
    engine: Engine, clean_database: None
) -> None:
    """Capacity becoming available must let the same User spend their untouched hourly slot."""
    del clean_database
    clock = Clock(NOW)
    limiter = SingleAnalysisLimiter()
    app, flow, _ = build_app(
        clock,
        StubGoogleProvider(clock),
        rate_limiter=limiter,
        concurrent_jobs_per_workspace=1,
        analyses_per_hour=1,
    )
    browser = Browser(app)
    sign_in(browser, flow)
    fixture = _project_with_transcript(engine, browser, status=ProjectStatus.TRANSCRIBING)
    blocking_job_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            Job.__table__.insert().values(
                id=blocking_job_id,
                workspace_id=fixture.workspace_id,
                project_id=fixture.project_id,
                kind=JobKind.SOURCE_IMPORT,
                status=JobStatus.QUEUED,
                stage="queued",
                progress=0,
                attempt=0,
                idempotency_key="blocking-job",
            )
        )

    refused = browser.request(
        "POST",
        _analysis_path(fixture),
        headers={"Idempotency-Key": "analysis-refused-capacity"},
    )
    with engine.begin() as connection:
        connection.execute(
            update(Job)
            .where(Job.id == blocking_job_id)
            .values(status=JobStatus.SUCCEEDED, finished_at=NOW)
        )
    admitted = browser.request(
        "POST",
        _analysis_path(fixture),
        headers={"Idempotency-Key": "analysis-after-capacity"},
    )

    assert_error(refused, status_code=429, code="CONCURRENCY_LIMIT")
    assert admitted.status_code == 202


@pytest.mark.integration
def test_analysis_terminal_outcomes_reconcile_reserved_quota(
    engine: Engine, clean_database: None
) -> None:
    """Finished work must neither hold budget forever nor refund a completed analysis."""
    del clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    successful = _project_with_transcript(
        engine, browser, status=ProjectStatus.TRANSCRIBING, key="settled-project"
    )
    failed = _project_with_transcript(
        engine, browser, status=ProjectStatus.TRANSCRIBING, key="released-project"
    )
    successful_response = browser.request(
        "POST",
        _analysis_path(successful),
        headers={"Idempotency-Key": "analysis-settled"},
    )
    failed_response = browser.request(
        "POST",
        _analysis_path(failed),
        headers={"Idempotency-Key": "analysis-released"},
    )
    successful_job_id = UUID(successful_response.json()["jobId"])
    failed_job_id = UUID(failed_response.json()["jobId"])

    with Session(engine) as session, session.begin():
        start_job(
            session,
            workspace_id=successful.workspace_id,
            job_id=successful_job_id,
            now=NOW,
        )
        succeed_job(
            session,
            workspace_id=successful.workspace_id,
            job_id=successful_job_id,
            now=NOW,
        )
        start_job(
            session,
            workspace_id=failed.workspace_id,
            job_id=failed_job_id,
            now=NOW,
        )
        fail_job(
            session,
            workspace_id=failed.workspace_id,
            job_id=failed_job_id,
            error_code="ANALYSIS_INSUFFICIENT_CANDIDATES",
            retryable=False,
            now=NOW,
        )

    with engine.connect() as connection:
        reservations = {
            row.reference_id: (row.status, row.actual_units)
            for row in connection.execute(
                select(
                    WorkspaceQuotaReservation.reference_id,
                    WorkspaceQuotaReservation.status,
                    WorkspaceQuotaReservation.actual_units,
                ).where(
                    WorkspaceQuotaReservation.reference_id.in_([successful_job_id, failed_job_id])
                )
            )
        }
        failed_status = connection.scalar(
            select(Project.status).where(Project.id == failed.project_id)
        )

    assert reservations[successful_job_id] == (
        QuotaReservationStatus.SETTLED,
        Decimal(1),
    )
    assert reservations[failed_job_id] == (
        QuotaReservationStatus.RELEASED,
        Decimal(0),
    )
    assert failed_status is ProjectStatus.FAILED


@pytest.mark.integration
def test_late_analysis_cancellation_finishes_canceled_instead_of_staying_nonterminal(
    engine: Engine, clean_database: None
) -> None:
    """A cancel request arriving after stage work must still release quota and end the Job."""
    del clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    fixture = _project_with_transcript(engine, browser, status=ProjectStatus.TRANSCRIBING)
    response = browser.request(
        "POST",
        _analysis_path(fixture),
        headers={"Idempotency-Key": "analysis-late-cancel"},
    )
    job_id = UUID(response.json()["jobId"])

    with Session(engine) as session, session.begin():
        start_job(session, workspace_id=fixture.workspace_id, job_id=job_id, now=NOW)
        request_job_cancellation(
            session,
            workspace_id=fixture.workspace_id,
            job_id=job_id,
            now=NOW,
        )
        completed = job_use_cases.complete_job_after_runner(
            session,
            workspace_id=fixture.workspace_id,
            job_id=job_id,
            now=NOW,
        )

    assert completed.status is JobStatus.CANCELED
    with engine.connect() as connection:
        reservation = connection.execute(
            select(
                WorkspaceQuotaReservation.status,
                WorkspaceQuotaReservation.actual_units,
            ).where(WorkspaceQuotaReservation.reference_id == job_id)
        ).one()
        project_status = connection.scalar(
            select(Project.status).where(Project.id == fixture.project_id)
        )
    assert tuple(reservation) == (QuotaReservationStatus.RELEASED, Decimal(0))
    assert project_status is ProjectStatus.FAILED


@pytest.mark.unit
def test_analysis_openapi_declares_a_strict_job_response_schema() -> None:
    """Task 17's generated client must receive a concrete analysis response contract."""
    clock = Clock(NOW)
    app, _, _ = build_app(clock, StubGoogleProvider(clock))

    response_schema = app.openapi()["paths"]["/api/v1/projects/{project_id}/analysis"]["post"][
        "responses"
    ]["202"]["content"]["application/json"]["schema"]

    assert response_schema["$ref"].endswith("/AnalysisJobResponse")


def _project_with_transcript(
    engine: Engine,
    browser: Browser,
    *,
    status: ProjectStatus,
    key: str = "analysis-project",
    include_transcript: bool = True,
) -> ProjectFixture:
    """Create one Project publicly, then install the completed transcription evidence."""
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    user_id = UUID(browser.get("/api/v1/me").json()["id"])
    created = browser.request(
        "POST",
        f"/api/v1/projects?workspace_id={workspace_id}",
        headers={"Idempotency-Key": key},
        json={"name": key, "sourceKind": "upload"},
    )
    assert created.status_code == 201
    project_id = UUID(created.json()["id"])
    source_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            update(Project).where(Project.id == project_id).values(status=status, updated_at=NOW)
        )
        connection.execute(
            Asset.__table__.insert().values(
                id=source_id,
                workspace_id=workspace_id,
                project_id=project_id,
                kind=AssetKind.SOURCE,
                source_type=AssetSourceType.USER_UPLOAD,
                storage_key=f"workspaces/{workspace_id}/projects/{project_id}/source/original",
                content_type="video/mp4",
                size_bytes=100,
                duration_ms=60_000,
                sha256=b"s" * 32,
            )
        )
        if include_transcript:
            connection.execute(
                Transcript.__table__.insert().values(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    asset_id=source_id,
                    provider="assemblyai",
                    provider_version="1.0.0",
                    model="universal-3-pro",
                    language="en",
                    full_text="A complete thought.",
                    words=[
                        {
                            "word_id": "w000001",
                            "text": "A",
                            "punctuation": "",
                            "start_ms": 0,
                            "end_ms": 500,
                            "confidence": 0.99,
                            "speaker": "A",
                        },
                        {
                            "word_id": "w000002",
                            "text": "complete thought",
                            "punctuation": ".",
                            "start_ms": 500,
                            "end_ms": 1_500,
                            "confidence": 0.99,
                            "speaker": "A",
                        },
                    ],
                    speaker_segments=[],
                    utterances=[],
                    duration_ms=60_000,
                    raw_result_storage_key=(
                        f"workspaces/{workspace_id}/projects/{project_id}/transcripts/raw.json"
                    ),
                )
            )
    return ProjectFixture(user_id=user_id, workspace_id=workspace_id, project_id=project_id)


def _analysis_path(fixture: ProjectFixture) -> str:
    """Build the analysis URL under the Workspace selection already proven by membership."""
    return f"/api/v1/projects/{fixture.project_id}/analysis?workspace_id={fixture.workspace_id}"
