"""Contract for the Workspace overview the dashboard's first screen is built from.

One read answers "what is happening in this Workspace right now": the Workspace itself,
its recent Projects, the Jobs still running, the monthly budget already charged, and the
best candidates waiting for review. Everything it returns obeys the same tenant rules as
the endpoints it summarizes.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from clipah.db import RuntimeRole, session_scope
from clipah.jobs.admission import QuotaLedger, admission_policy
from clipah.jobs.use_cases import create_job, fail_job, start_job
from clipah.models import (
    Asset,
    AssetKind,
    AssetSourceType,
    ClipCandidate,
    JobKind,
    Project,
    ProjectStatus,
    PublishingRolePolicy,
    QuotaResource,
    Transcript,
    WorkspaceMembership,
    WorkspaceRole,
)
from clipah.workspaces.models import WorkspaceAccess
from harness import NOW, Browser, Clock, StubGoogleProvider, build_app, sign_in
from support import provision_identity, runtime_settings

SUMMARY_PATH = "/api/v1/dashboard/summary"


@dataclass(frozen=True, slots=True)
class WorkspaceFixture:
    """One signed-in Workspace and the actor who owns it."""

    browser: Browser
    workspace_id: UUID
    user_id: UUID
    clock: Clock


@pytest.mark.integration
def test_the_summary_reports_the_workspace_its_projects_jobs_usage_and_top_candidates(
    engine: Engine, clean_database: None
) -> None:
    """The overview screen has to render from one read, not from six racing ones."""
    del clean_database
    fixture = _signed_in_workspace(engine)
    project_id = _project(fixture, name="Season one")
    _ready_with_candidates(engine, fixture, project_id, ranks=(1, 2))
    job_id = _running_job(fixture, project_id, key="summary-active")

    response = fixture.browser.get(f"{SUMMARY_PATH}?workspace_id={fixture.workspace_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["workspace"]["id"] == str(fixture.workspace_id)
    assert body["workspace"]["role"] == "owner"
    assert body["projects"]["activeCount"] == 1
    assert [project["id"] for project in body["projects"]["recent"]] == [str(project_id)]
    assert [job["id"] for job in body["jobs"]["active"]] == [str(job_id)]
    assert body["jobs"]["active"][0]["status"] == "running"
    assert [candidate["rank"] for candidate in body["topCandidates"]] == [1, 2]
    assert body["topCandidates"][0]["projectName"] == "Season one"
    assert {usage["resource"] for usage in body["usage"]} == {
        resource.value for resource in QuotaResource
    }


@pytest.mark.integration
def test_the_summary_charges_usage_against_the_configured_monthly_limit(
    engine: Engine, clean_database: None
) -> None:
    """Usage cards exist to warn before a refusal, so they read the same ledger admission does."""
    del clean_database
    fixture = _signed_in_workspace(engine)
    _reserve_quota(fixture, QuotaResource.ANALYSES, units=Decimal(2))

    response = fixture.browser.get(f"{SUMMARY_PATH}?workspace_id={fixture.workspace_id}")

    assert response.status_code == 200
    analyses = _usage_for(response.json(), QuotaResource.ANALYSES)
    assert analyses["consumed"] == 2
    assert analyses["limit"] == runtime_settings().monthly_analyses


@pytest.mark.integration
def test_the_summary_omits_archived_projects_finished_jobs_and_unexposed_candidates(
    engine: Engine, clean_database: None
) -> None:
    """An overview that shows deleted work or private reranking rows is a leak, not a summary."""
    del clean_database
    fixture = _signed_in_workspace(engine)
    visible_project = _project(fixture, name="Visible")
    hidden_candidate_id = _ready_with_candidates(
        engine, fixture, visible_project, ranks=(1,), hidden_rank=2
    )
    archived_project = _project(fixture, name="Archived", key="archived")
    finished_job = _failed_job(fixture, visible_project, key="summary-terminal")
    assert (
        fixture.browser.request(
            "DELETE",
            f"/api/v1/projects/{archived_project}?workspace_id={fixture.workspace_id}",
        ).status_code
        == 204
    )

    body = fixture.browser.get(f"{SUMMARY_PATH}?workspace_id={fixture.workspace_id}").json()

    assert [project["id"] for project in body["projects"]["recent"]] == [str(visible_project)]
    assert body["projects"]["activeCount"] == 1
    assert [job["id"] for job in body["jobs"]["active"]] == []
    assert str(finished_job) not in response_text(body)
    assert [candidate["id"] for candidate in body["topCandidates"]] != []
    assert str(hidden_candidate_id) not in response_text(body)


@pytest.mark.integration
def test_the_summary_never_reports_another_workspaces_work(
    engine: Engine, clean_database: None
) -> None:
    """Two Workspaces sharing one database must never share one overview."""
    del clean_database
    fixture = _signed_in_workspace(engine)
    mine = _project(fixture, name="Mine")
    stranger_user, stranger_workspace = provision_identity(engine, suffix=f"dash-{uuid4().hex[:8]}")
    _foreign_project(stranger_workspace, stranger_user, name="Theirs")

    body = fixture.browser.get(f"{SUMMARY_PATH}?workspace_id={fixture.workspace_id}").json()

    assert [project["id"] for project in body["projects"]["recent"]] == [str(mine)]
    assert "Theirs" not in response_text(body)


@pytest.mark.integration
def test_a_summary_of_a_workspace_without_membership_is_refused_like_a_missing_one(
    engine: Engine, clean_database: None
) -> None:
    """A guessed Workspace identifier must not tell a caller that the Workspace exists."""
    del clean_database
    fixture = _signed_in_workspace(engine)
    _, stranger_workspace = provision_identity(engine, suffix=f"dash-{uuid4().hex[:8]}")

    known = fixture.browser.get(f"{SUMMARY_PATH}?workspace_id={stranger_workspace}")
    unknown = fixture.browser.get(f"{SUMMARY_PATH}?workspace_id={uuid4()}")

    assert known.status_code == unknown.status_code == 404
    assert known.json()["error"]["code"] == unknown.json()["error"]["code"] == "NOT_FOUND"
    assert known.json()["error"]["message"] == unknown.json()["error"]["message"]


@pytest.mark.integration
def test_an_anonymous_visitor_cannot_read_a_dashboard_summary(
    engine: Engine, clean_database: None
) -> None:
    """The overview is private Workspace data even though it renders the first screen."""
    del clean_database
    fixture = _signed_in_workspace(engine)
    anonymous = Browser(fixture.browser.app)

    response = anonymous.get(f"{SUMMARY_PATH}?workspace_id={fixture.workspace_id}")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def response_text(body: dict[str, Any]) -> str:
    """Render one response body as text so a leak anywhere in it fails a test."""
    return repr(body)


def _usage_for(body: dict[str, Any], resource: QuotaResource) -> dict[str, Any]:
    """Return the usage card for one metered resource."""
    return next(usage for usage in body["usage"] if usage["resource"] == resource.value)


def _signed_in_workspace(engine: Engine) -> WorkspaceFixture:
    """Drive one login ceremony and return its personal Workspace."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    with Session(engine) as session:
        user_id = session.scalars(
            select(WorkspaceMembership.user_id).where(
                WorkspaceMembership.workspace_id == workspace_id
            )
        ).one()
    return WorkspaceFixture(
        browser=browser, workspace_id=workspace_id, user_id=user_id, clock=clock
    )


def _project(fixture: WorkspaceFixture, *, name: str, key: str | None = None) -> UUID:
    """Create one Project through the public API."""
    created = fixture.browser.request(
        "POST",
        f"/api/v1/projects?workspace_id={fixture.workspace_id}",
        headers={"Idempotency-Key": key or name.lower().replace(" ", "-")},
        json={"name": name, "sourceKind": "upload"},
    )
    assert created.status_code == 201
    return UUID(created.json()["id"])


def _foreign_project(workspace_id: UUID, user_id: UUID, *, name: str) -> UUID:
    """Create one Project owned by a Workspace the caller has no standing in."""
    project_id = uuid4()
    with _api_session(workspace_id, user_id) as session:
        session.add(
            Project(
                id=project_id,
                workspace_id=workspace_id,
                created_by_user_id=user_id,
                name=name,
                source_kind="upload",
            )
        )
    return project_id


def _running_job(fixture: WorkspaceFixture, project_id: UUID, *, key: str) -> UUID:
    """Create one admitted Job and move it into its running stage."""
    with _api_session(fixture.workspace_id, fixture.user_id) as session:
        job_id = create_job(
            session,
            policy=admission_policy(runtime_settings()),
            access=_access(fixture),
            project_id=project_id,
            kind=JobKind.INGEST,
            idempotency_key=key,
            now=fixture.clock(),
        ).job_id
    with _worker_session(fixture.workspace_id, fixture.user_id) as session:
        start_job(session, workspace_id=fixture.workspace_id, job_id=job_id, now=fixture.clock())
    return job_id


def _failed_job(fixture: WorkspaceFixture, project_id: UUID, *, key: str) -> UUID:
    """Create one Job that has already reached a terminal state."""
    job_id = _running_job(fixture, project_id, key=key)
    with _worker_session(fixture.workspace_id, fixture.user_id) as session:
        fail_job(
            session,
            workspace_id=fixture.workspace_id,
            job_id=job_id,
            error_code="INGEST_FAILED",
            retryable=False,
            now=fixture.clock(),
        )
    return job_id


def _reserve_quota(fixture: WorkspaceFixture, resource: QuotaResource, *, units: Decimal) -> None:
    """Charge the Workspace budget the way admission charges it."""
    settings = runtime_settings()
    with _api_session(fixture.workspace_id, fixture.user_id) as session:
        QuotaLedger(session, limits=admission_policy(settings).quota_limits).reserve(
            workspace_id=fixture.workspace_id,
            resource=resource,
            units=units,
            reference_kind="job",
            reference_id=uuid4(),
            now=fixture.clock(),
        )


def _ready_with_candidates(
    engine: Engine,
    fixture: WorkspaceFixture,
    project_id: UUID,
    *,
    ranks: tuple[int, ...],
    hidden_rank: int | None = None,
) -> UUID:
    """Make one Project ready and give it exposed candidates plus an optional private row."""
    source_id = uuid4()
    transcript_id = uuid4()
    hidden_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(status=ProjectStatus.READY, updated_at=NOW)
        )
        connection.execute(
            Asset.__table__.insert().values(
                id=source_id,
                workspace_id=fixture.workspace_id,
                project_id=project_id,
                kind=AssetKind.SOURCE,
                source_type=AssetSourceType.USER_UPLOAD,
                storage_key=(
                    f"workspaces/{fixture.workspace_id}/projects/{project_id}/source/original"
                ),
                content_type="video/mp4",
                size_bytes=100,
                duration_ms=60_000,
                sha256=b"s" * 32,
            )
        )
        connection.execute(
            Transcript.__table__.insert().values(
                id=transcript_id,
                workspace_id=fixture.workspace_id,
                project_id=project_id,
                asset_id=source_id,
                provider="assemblyai",
                provider_version="1.0.0",
                model="universal-3-pro",
                language="en",
                full_text="A complete and useful moment.",
                words=[],
                speaker_segments=[],
                utterances=[],
                duration_ms=60_000,
                raw_result_storage_key=(
                    f"workspaces/{fixture.workspace_id}/projects/{project_id}/transcripts/raw.json"
                ),
            )
        )
        rows = [
            _candidate_values(
                candidate_id=uuid4(),
                workspace_id=fixture.workspace_id,
                project_id=project_id,
                transcript_id=transcript_id,
                rank=rank,
                exposed=True,
            )
            for rank in ranks
        ]
        if hidden_rank is not None:
            rows.append(
                _candidate_values(
                    candidate_id=hidden_id,
                    workspace_id=fixture.workspace_id,
                    project_id=project_id,
                    transcript_id=transcript_id,
                    rank=hidden_rank,
                    exposed=False,
                )
            )
        connection.execute(ClipCandidate.__table__.insert(), rows)
    return hidden_id


def _candidate_values(
    *,
    candidate_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    transcript_id: UUID,
    rank: int,
    exposed: bool,
) -> dict[str, Any]:
    """Build one persisted candidate whose private metadata must never be summarized."""
    return {
        "id": candidate_id,
        "workspace_id": workspace_id,
        "project_id": project_id,
        "transcript_id": transcript_id,
        "rank": rank,
        "score": 0.91 - (rank - 1) * 0.01,
        "hook": "The surprising opening",
        "payoff": "The useful resolution",
        "reason": "A complete and useful moment",
        "category": "insight",
        "tags": ["creator"],
        "start_ms": 1_000,
        "end_ms": 31_000,
        "start_word_id": "w000001",
        "end_word_id": "w000099",
        "transcript_excerpt": "A complete and useful moment.",
        "context_dependencies": [],
        "score_breakdown": {
            "hook": 0.9,
            "payoff": 0.92,
            "narrative_completeness": 0.93,
            "context_safety": 0.94,
            "platform_fit": 0.88,
            "transcript_confidence": 0.97,
            "visual_opportunity": 0.8,
        },
        "context_warnings": [],
        "visual_opportunities": [],
        "model_metadata": {
            "exposed": exposed,
            "provider": "private-provider",
            "providerRequestId": "request-secret",
            "rawOutput": "must never leave",
        },
        "created_at": NOW,
    }


def _access(fixture: WorkspaceFixture) -> WorkspaceAccess:
    """Return the proven Workspace standing a domain call needs."""
    return WorkspaceAccess(
        workspace_id=fixture.workspace_id,
        user_id=fixture.user_id,
        role=WorkspaceRole.OWNER,
        publishing_role_policy=PublishingRolePolicy.OWNER_ADMIN_EDITOR,
    )


def _api_session(workspace_id: UUID, user_id: UUID) -> AbstractContextManager[Session]:
    """Open one API-role transaction already holding this tenant's row context."""
    return session_scope(settings=runtime_settings(), workspace_id=workspace_id, user_id=user_id)


def _worker_session(workspace_id: UUID, user_id: UUID) -> AbstractContextManager[Session]:
    """Open one worker-role transaction already holding this tenant's row context."""
    return session_scope(
        settings=runtime_settings(RuntimeRole.WORKER),
        workspace_id=workspace_id,
        user_id=user_id,
        runtime_role=RuntimeRole.WORKER,
    )
