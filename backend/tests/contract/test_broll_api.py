"""Contract tests for B-roll plan admission and review-safe suggestion reads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, update

from clipah.models import (
    Asset,
    AssetKind,
    AssetSourceType,
    BrollSuggestion,
    ClipCandidate,
    Job,
    JobKind,
    Project,
    ProjectStatus,
    Transcript,
)
from harness import NOW, Browser, Clock, StubGoogleProvider, assert_error, build_app, sign_in


@dataclass(frozen=True, slots=True)
class ClipFixture:
    """Identifiers for one ready Project, its exposed clip, and its hidden one."""

    workspace_id: UUID
    project_id: UUID
    candidate_id: UUID
    hidden_candidate_id: UUID


@pytest.mark.integration
def test_planning_admits_one_durable_job_for_a_reviewable_clip(
    engine: Engine, clean_database: None
) -> None:
    """Planning must commit a Job a member can follow, not run inside the request."""
    del clean_database
    browser, fixture = _signed_in_with_clip(engine)

    response = _plan(browser, fixture, key="plan-1")

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    with engine.begin() as connection:
        kind = connection.execute(
            Job.__table__.select().where(Job.id == UUID(response.json()["jobId"]))
        ).one()
    assert kind.kind is JobKind.BROLL_PLAN


@pytest.mark.integration
def test_repeating_one_idempotency_key_returns_the_first_job(
    engine: Engine, clean_database: None
) -> None:
    """A member who submits twice must buy one plan, not two."""
    del clean_database
    browser, fixture = _signed_in_with_clip(engine)

    first = _plan(browser, fixture, key="plan-repeat")
    second = _plan(browser, fixture, key="plan-repeat")

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["jobId"] == second.json()["jobId"]


@pytest.mark.integration
def test_one_idempotency_key_cannot_be_reused_for_another_project(
    engine: Engine, clean_database: None
) -> None:
    """A key bound to one clip's planning must never admit work on a different Project."""
    del clean_database
    browser, fixture = _signed_in_with_clip(engine)
    other = _ready_clip(engine, browser, key="broll-other-project")
    _plan(browser, fixture, key="plan-bound")

    response = _plan(browser, other, key="plan-bound")

    assert_error(response, status_code=409, code="CONFLICT")


@pytest.mark.integration
def test_planning_requires_csrf_proof(engine: Engine, clean_database: None) -> None:
    """Planning spends a Workspace's concurrency, so it is a state-changing method."""
    del clean_database
    browser, fixture = _signed_in_with_clip(engine)

    response = browser.request(
        "POST",
        _plan_path(fixture),
        headers={"Idempotency-Key": "plan-csrf"},
        json={"coverage": "balanced"},
        csrf_token="",
    )

    assert response.status_code in {401, 403}


@pytest.mark.integration
def test_planning_refuses_an_unlisted_coverage(engine: Engine, clean_database: None) -> None:
    """A coverage the placement code cannot enforce must not reach a Job."""
    del clean_database
    browser, fixture = _signed_in_with_clip(engine)

    response = browser.request(
        "POST",
        _plan_path(fixture),
        headers={"Idempotency-Key": "plan-coverage"},
        json={"coverage": "cinematic"},
    )

    assert_error(response, status_code=422, code="VALIDATION_ERROR")


@pytest.mark.integration
def test_planning_hides_a_hidden_guessed_and_missing_clip_identically(
    engine: Engine, clean_database: None
) -> None:
    """A guessed identifier must be indistinguishable from one that does not exist."""
    del clean_database
    browser, fixture = _signed_in_with_clip(engine)

    hidden = browser.request(
        "POST",
        f"/api/v1/projects/{fixture.project_id}/candidates/{fixture.hidden_candidate_id}"
        f"/broll-plans?workspace_id={fixture.workspace_id}",
        headers={"Idempotency-Key": "plan-hidden"},
        json={"coverage": "balanced"},
    )
    missing = browser.request(
        "POST",
        f"/api/v1/projects/{fixture.project_id}/candidates/{uuid4()}"
        f"/broll-plans?workspace_id={fixture.workspace_id}",
        headers={"Idempotency-Key": "plan-missing"},
        json={"coverage": "balanced"},
    )

    assert_error(hidden, status_code=404, code="NOT_FOUND")
    assert_error(missing, status_code=404, code="NOT_FOUND")
    assert hidden.json()["error"]["message"] == missing.json()["error"]["message"]


@pytest.mark.integration
def test_another_workspaces_clip_cannot_be_planned_or_read(
    engine: Engine, clean_database: None
) -> None:
    """Workspace Membership must be proven before clip ownership is even queried."""
    del clean_database
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    app, flow, _ = build_app(clock, provider)
    owner = Browser(app)
    sign_in(owner, flow)
    owned = _ready_clip(engine, owner, key="broll-owned")

    provider.identify(subject="broll-stranger", email="stranger@example.com", name="Stranger")
    stranger = Browser(app)
    sign_in(stranger, flow)
    stranger_workspace = UUID(stranger.get("/api/v1/workspaces").json()["workspaces"][0]["id"])

    guessed_plan = stranger.request(
        "POST",
        f"/api/v1/projects/{owned.project_id}/candidates/{owned.candidate_id}"
        f"/broll-plans?workspace_id={stranger_workspace}",
        headers={"Idempotency-Key": "plan-stranger"},
        json={"coverage": "balanced"},
    )
    guessed_read = stranger.get(
        f"/api/v1/projects/{owned.project_id}/candidates/{owned.candidate_id}"
        f"/broll-suggestions?workspace_id={stranger_workspace}"
    )
    missing_read = stranger.get(
        f"/api/v1/projects/{uuid4()}/candidates/{uuid4()}"
        f"/broll-suggestions?workspace_id={stranger_workspace}"
    )

    assert_error(guessed_plan, status_code=404, code="NOT_FOUND")
    assert_error(guessed_read, status_code=404, code="NOT_FOUND")
    assert guessed_read.json()["error"]["message"] == missing_read.json()["error"]["message"]


@pytest.mark.integration
def test_a_clip_with_no_plan_yet_reads_as_an_empty_list_rather_than_an_error(
    engine: Engine, clean_database: None
) -> None:
    """Before planning runs there is nothing to show, which is not a failure."""
    del clean_database
    browser, fixture = _signed_in_with_clip(engine)

    response = browser.get(_suggestions_path(fixture))

    assert response.status_code == 200
    assert response.json() == {"suggestions": []}


@pytest.mark.integration
def test_a_suggestion_exposes_review_evidence_and_no_private_provider_metadata(
    engine: Engine, clean_database: None
) -> None:
    """A reviewer needs the intent and the reason, never the provider's raw evidence."""
    del clean_database
    browser, fixture = _signed_in_with_clip(engine)
    suggestion_id = _store_suggestion(engine, fixture)

    response = browser.get(_suggestions_path(fixture))

    assert response.status_code == 200
    body = response.json()["suggestions"]
    assert body == [
        {
            "id": str(suggestion_id),
            "projectId": str(fixture.project_id),
            "candidateId": str(fixture.candidate_id),
            "coverage": "balanced",
            "plannerVersion": "broll-plan/1",
            "beatStartWordId": "w000021",
            "beatEndWordId": "w000024",
            "startMs": 10_000,
            "endMs": 12_000,
            "durationMs": 2_000,
            "visualIntent": {
                "subject": "a shortened signup form",
                "action": "a hand deleting form fields",
                "setting": "a laptop screen on a desk",
                "mood": "focused",
                "portraitSuitable": True,
                "factualRiskFlags": [],
                "confidence": 0.8,
            },
            "searchTerms": {"id": ["formulir pendaftaran"], "en": ["signup form"]},
            "exclusions": ["stock office handshake"],
            "status": "proposed",
            "placementReason": "The sentence names an object the viewer cannot see",
            "sourceType": None,
            "relevanceScore": None,
            "createdAt": NOW.isoformat(),
            "decidedAt": None,
        }
    ]
    assert "request-secret" not in response.text
    assert "must never leave" not in response.text


@pytest.mark.integration
def test_reading_suggestions_requires_a_signed_in_member(
    engine: Engine, clean_database: None
) -> None:
    """An anonymous caller has no Workspace and must not learn a clip exists."""
    del clean_database
    browser, fixture = _signed_in_with_clip(engine)
    anonymous = Browser(browser.app)

    response = anonymous.get(_suggestions_path(fixture))

    assert response.status_code in {401, 403, 404}


def _plan_path(fixture: ClipFixture) -> str:
    """Name the planning endpoint for one clip inside its Workspace."""
    return (
        f"/api/v1/projects/{fixture.project_id}/candidates/{fixture.candidate_id}"
        f"/broll-plans?workspace_id={fixture.workspace_id}"
    )


def _suggestions_path(fixture: ClipFixture) -> str:
    """Name the suggestion collection for one clip inside its Workspace."""
    return (
        f"/api/v1/projects/{fixture.project_id}/candidates/{fixture.candidate_id}"
        f"/broll-suggestions?workspace_id={fixture.workspace_id}"
    )


def _plan(browser: Browser, fixture: ClipFixture, *, key: str) -> Any:
    """Ask for balanced coverage under one idempotency key."""
    return browser.request(
        "POST",
        _plan_path(fixture),
        headers={"Idempotency-Key": key},
        json={"coverage": "balanced"},
    )


def _signed_in_with_clip(engine: Engine) -> tuple[Browser, ClipFixture]:
    """Sign one member in and give them a ready Project with one exposed clip."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    return browser, _ready_clip(engine, browser)


def _store_suggestion(engine: Engine, fixture: ClipFixture) -> UUID:
    """Persist one proposal whose private provider metadata is deliberately toxic."""
    suggestion_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            BrollSuggestion.__table__.insert().values(
                id=suggestion_id,
                workspace_id=fixture.workspace_id,
                project_id=fixture.project_id,
                candidate_id=fixture.candidate_id,
                planner_version="broll-plan/1",
                coverage="balanced",
                beat_start_word_id="w000021",
                beat_end_word_id="w000024",
                start_ms=10_000,
                end_ms=12_000,
                visual_intent={
                    "subject": "a shortened signup form",
                    "action": "a hand deleting form fields",
                    "setting": "a laptop screen on a desk",
                    "mood": "focused",
                    "portrait_suitable": True,
                    "factual_risk_flags": [],
                    "confidence": 0.8,
                },
                search_terms={"id": ["formulir pendaftaran"], "en": ["signup form"]},
                exclusions=["stock office handshake"],
                status="proposed",
                placement_reason="The sentence names an object the viewer cannot see",
                provider_metadata={
                    "provider": "private-provider",
                    "request_ids": ["request-secret"],
                    "rawOutput": "must never leave",
                },
                created_at=NOW,
            )
        )
    return suggestion_id


def _ready_clip(engine: Engine, browser: Browser, *, key: str = "broll-project") -> ClipFixture:
    """Create a ready Project with one exposed clip and one deliberately hidden clip."""
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    created = browser.request(
        "POST",
        f"/api/v1/projects?workspace_id={workspace_id}",
        headers={"Idempotency-Key": key},
        json={"name": key, "sourceKind": "upload"},
    )
    assert created.status_code == 201
    project_id = UUID(created.json()["id"])
    source_id = uuid4()
    transcript_id = uuid4()
    candidate_id = uuid4()
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
                workspace_id=workspace_id,
                project_id=project_id,
                kind=AssetKind.SOURCE,
                source_type=AssetSourceType.USER_UPLOAD,
                storage_key=f"workspaces/{workspace_id}/projects/{project_id}/source/original",
                content_type="video/mp4",
                size_bytes=100,
                duration_ms=120_000,
                sha256=b"s" * 32,
            )
        )
        connection.execute(
            Transcript.__table__.insert().values(
                id=transcript_id,
                workspace_id=workspace_id,
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
                duration_ms=120_000,
                raw_result_storage_key=(
                    f"workspaces/{workspace_id}/projects/{project_id}/transcripts/raw.json"
                ),
            )
        )
        connection.execute(
            ClipCandidate.__table__.insert(),
            [
                _candidate_values(
                    candidate_id=candidate_id,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    transcript_id=transcript_id,
                    rank=1,
                    exposed=True,
                ),
                _candidate_values(
                    candidate_id=hidden_id,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    transcript_id=transcript_id,
                    rank=2,
                    exposed=False,
                ),
            ],
        )
    return ClipFixture(
        workspace_id=workspace_id,
        project_id=project_id,
        candidate_id=candidate_id,
        hidden_candidate_id=hidden_id,
    )


def _candidate_values(
    *,
    candidate_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    transcript_id: UUID,
    rank: int,
    exposed: bool,
) -> dict[str, Any]:
    """Build one persisted candidate spanning the first minute of its source."""
    return {
        "id": candidate_id,
        "workspace_id": workspace_id,
        "project_id": project_id,
        "transcript_id": transcript_id,
        "rank": rank,
        "score": 0.91,
        "hook": "The surprising opening",
        "payoff": "The useful resolution",
        "reason": "A complete and useful moment",
        "category": "insight",
        "tags": ["creator"],
        "start_ms": 0,
        "end_ms": 60_000,
        "start_word_id": "w000001",
        "end_word_id": "w000120",
        "transcript_excerpt": "A complete and useful moment.",
        "context_dependencies": [],
        "score_breakdown": {},
        "context_warnings": [],
        "visual_opportunities": [],
        "model_metadata": {"exposed": exposed, "providerRequestId": "request-secret"},
        "created_at": NOW,
    }


@pytest.mark.integration
def test_planning_is_refused_when_the_workspace_is_already_at_its_job_limit(
    engine: Engine, clean_database: None
) -> None:
    """A full Workspace must be told to wait rather than quietly admitted anyway."""
    del clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), concurrent_jobs_per_workspace=1)
    browser = Browser(app)
    sign_in(browser, flow)
    fixture = _ready_clip(engine, browser)
    with engine.begin() as connection:
        connection.execute(
            Job.__table__.insert().values(
                id=uuid4(),
                workspace_id=fixture.workspace_id,
                project_id=fixture.project_id,
                kind=JobKind.INGEST,
                status="running",
                stage="queued",
                progress=0,
                attempt=1,
                idempotency_key="occupying-job",
            )
        )

    response = _plan(browser, fixture, key="plan-concurrency")

    assert_error(response, status_code=429, code="CONCURRENCY_LIMIT")


@pytest.mark.integration
def test_replaying_a_key_whose_job_already_started_does_not_dispatch_it_again(
    engine: Engine, clean_database: None
) -> None:
    """A Job a worker already claimed must be reported, not queued a second time."""
    del clean_database
    browser, fixture = _signed_in_with_clip(engine)
    first = _plan(browser, fixture, key="plan-running")
    with engine.begin() as connection:
        connection.execute(
            Job.__table__.update()
            .where(Job.id == UUID(first.json()["jobId"]))
            .values(status="running")
        )

    second = _plan(browser, fixture, key="plan-running")

    assert second.status_code == 202
    assert second.json() == {"jobId": first.json()["jobId"], "status": "running"}
