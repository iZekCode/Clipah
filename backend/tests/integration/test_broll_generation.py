"""Integration contracts for generated B-roll admission, confirmation, and quotas.

Three promises are held here. A confirmation a member never saw cannot admit work,
because the token is sealed with the deployment secret and bound to one User, one
suggestion, and one complete request. A video costs two budgets or none, because both
reservations are written inside the same transaction as the Job. And one idempotency key
buys one generation however many times it is submitted.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import PIL.Image
import pytest
from httpx import Response
from sqlalchemy import Engine, select, text, update
from sqlalchemy.orm import Session

from clipah.assets.probe import SourceMetadata
from clipah.assets.storage import StoredObject
from clipah.broll.generation import (
    FakeGenerativeMediaProvider,
    GenerationMediaKind,
    GenerationModerationResult,
    GenerationRequest,
    GenerationResult,
    GenerationStatus,
    GenerationUsage,
    open_generation_confirmation,
    seal_generation_confirmation,
)
from clipah.broll.models import (
    PLANNER_VERSION,
    BrollCoverage,
    BrollSourceType,
    BrollSuggestionStatus,
)
from clipah.db import RuntimeRole, session_scope
from clipah.jobs.broll_generate_task import (
    MEDIA_INVALID_CODE,
    POLL_TIMEOUT_CODE,
    BrollGenerateStageRunner,
    GenerationDependencies,
)
from clipah.jobs.models import JobCancelledError, JobContext, RetryableJobError, TerminalJobError
from clipah.jobs.tasks import stage_runners
from clipah.jobs.use_cases import fail_job
from clipah.models import (
    Asset,
    AssetKind,
    AssetProvenance,
    AssetSourceType,
    BrollSuggestion,
    ClipCandidate,
    Job,
    JobKind,
    JobStatus,
    Project,
    ProjectStatus,
    QuotaReservationStatus,
    QuotaResource,
    Transcript,
    WorkspaceQuotaReservation,
    WorkspaceRole,
)
from harness import (
    NOW,
    SESSION_SECRET,
    Browser,
    Clock,
    StubGoogleProvider,
    assert_error,
    build_app,
    sign_in,
)
from support import fake_generation_providers, runtime_settings


@dataclass(frozen=True, slots=True)
class _Fixture:
    """One signed-in member, their clip, and the proposal they may illustrate."""

    browser: Browser
    clock: Clock
    workspace_id: UUID
    project_id: UUID
    candidate_id: UUID
    suggestion_id: UUID


def _estimate_path(fixture: _Fixture, suggestion_id: UUID | None = None) -> str:
    """Name the estimate endpoint for one suggestion inside its Workspace."""
    target = suggestion_id or fixture.suggestion_id
    return (
        f"/api/v1/broll-suggestions/{target}/generation-estimates"
        f"?workspace_id={fixture.workspace_id}"
    )


def _generate_path(fixture: _Fixture, suggestion_id: UUID | None = None) -> str:
    """Name the generation endpoint for one suggestion inside its Workspace."""
    target = suggestion_id or fixture.suggestion_id
    return f"/api/v1/broll-suggestions/{target}/generate?workspace_id={fixture.workspace_id}"


def _estimate(
    fixture: _Fixture, *, media_kind: str = "image", suggestion_id: UUID | None = None
) -> Response:
    """Ask for the price of one still or one clip without spending anything."""
    return fixture.browser.request(
        "POST", _estimate_path(fixture, suggestion_id), json={"mediaKind": media_kind}
    )


def _confirmation_token(
    fixture: _Fixture, *, media_kind: str = "image", suggestion_id: UUID | None = None
) -> str:
    """Read the sealed confirmation a member's dialog would hold."""
    response = _estimate(fixture, media_kind=media_kind, suggestion_id=suggestion_id)
    assert response.status_code == 200
    token = response.json()["confirmationToken"]
    assert isinstance(token, str)
    return token


def _generate(
    fixture: _Fixture,
    *,
    key: str,
    token: str | None = None,
    media_kind: str = "image",
    video_confirmed: bool = False,
    suggestion_id: UUID | None = None,
) -> Response:
    """Submit one confirmed generation under an idempotency key."""
    body: dict[str, Any] = {
        "confirmationToken": token
        if token is not None
        else _confirmation_token(fixture, media_kind=media_kind, suggestion_id=suggestion_id),
        "videoConfirmed": video_confirmed,
    }
    return fixture.browser.request(
        "POST",
        _generate_path(fixture, suggestion_id),
        headers={"Idempotency-Key": key},
        json=body,
    )


def _reserved_resources(engine: Engine, job_id: UUID) -> dict[QuotaResource, Decimal]:
    """Read every budget one admitted Job currently holds."""
    with Session(engine) as session:
        rows = session.execute(
            select(
                WorkspaceQuotaReservation.resource,
                WorkspaceQuotaReservation.estimated_units,
            ).where(
                WorkspaceQuotaReservation.reference_kind == "job",
                WorkspaceQuotaReservation.reference_id == job_id,
                WorkspaceQuotaReservation.status == QuotaReservationStatus.RESERVED,
            )
        ).all()
    return {resource: Decimal(units) for resource, units in rows}


def _suggestion(engine: Engine, fixture: _Fixture) -> BrollSuggestion:
    """Read back the proposal the API acted on."""
    with Session(engine) as session:
        row = session.scalar(
            select(BrollSuggestion).where(BrollSuggestion.id == fixture.suggestion_id)
        )
    assert row is not None
    return row


@pytest.mark.integration
def test_an_estimate_prices_a_still_without_reserving_any_budget(
    engine: Engine, clean_database: None
) -> None:
    """A member must see the price before a Workspace is charged anything at all."""
    del clean_database
    fixture = _signed_in_with_proposal(engine)

    response = _estimate(fixture)

    body = response.json()
    assert response.status_code == 200
    assert body["available"] is True
    assert body["reason"] is None
    assert body["estimate"]["mediaKind"] == "image"
    assert body["estimate"]["outputCount"] == 1
    assert body["estimate"]["width"] == 1080
    assert body["estimate"]["height"] == 1920
    assert body["estimate"]["costUsd"] == "0.08"
    assert body["estimate"]["imageUnits"] == "1"
    assert isinstance(body["confirmationToken"], str)
    with Session(engine) as session:
        reservations = session.scalars(select(WorkspaceQuotaReservation)).all()
    assert reservations == []


@pytest.mark.integration
def test_a_confirmed_still_admits_one_job_and_reserves_one_image(
    engine: Engine, clean_database: None
) -> None:
    """Admission must commit the Job and its budget together, before any dispatch."""
    del clean_database
    fixture = _signed_in_with_proposal(engine)

    response = _generate(fixture, key="generate-still")

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    job_id = UUID(body["jobId"])
    assert _reserved_resources(engine, job_id) == {QuotaResource.GENERATED_IMAGES: Decimal(1)}
    with Session(engine) as session:
        job = session.scalar(select(Job).where(Job.id == job_id))
    assert job is not None
    assert job.kind is JobKind.BROLL_GENERATE
    suggestion = _suggestion(engine, fixture)
    assert suggestion.status is BrollSuggestionStatus.GENERATION_REQUESTED
    generation = suggestion.provider_metadata["generation"]
    assert generation["media_kind"] == "image"
    assert generation["job_id"] == str(job_id)
    assert generation["requested_by_user_id"]
    assert "prompt" not in generation


@pytest.mark.integration
def test_a_video_confirmation_atomically_reserves_video_and_seconds(
    engine: Engine, clean_database: None
) -> None:
    """A clip spends two budgets, and a Workspace must never be charged for half of one."""
    del clean_database
    fixture = _signed_in_with_proposal(engine, generative_video_enabled=True)

    response = _generate(fixture, key="generate-video", media_kind="video", video_confirmed=True)

    assert response.status_code == 202
    assert _reserved_resources(engine, UUID(response.json()["jobId"])) == {
        QuotaResource.GENERATED_VIDEOS: Decimal(1),
        QuotaResource.GENERATED_SECONDS: Decimal(5),
    }


@pytest.mark.integration
def test_a_video_without_its_second_confirmation_is_refused(
    engine: Engine, clean_database: None
) -> None:
    """There is no one-click path from a suggestion card to a billable video Job."""
    del clean_database
    fixture = _signed_in_with_proposal(engine, generative_video_enabled=True)

    response = _generate(fixture, key="generate-unconfirmed", media_kind="video")

    assert_error(response, status_code=400, code="GENERATION_CONFIRMATION_REQUIRED")
    with Session(engine) as session:
        assert session.scalars(select(Job)).all() == []


@pytest.mark.integration
def test_video_generation_is_unavailable_while_the_feature_gate_is_closed(
    engine: Engine, clean_database: None
) -> None:
    """A deployment that has not enabled video must price nothing and admit nothing."""
    del clean_database
    fixture = _signed_in_with_proposal(engine)

    estimate = _estimate(fixture, media_kind="video")

    assert estimate.status_code == 200
    assert estimate.json() == {
        "available": False,
        "reason": "video_disabled",
        "videoOffered": False,
        "estimate": None,
        "confirmationToken": None,
    }


@pytest.mark.integration
def test_generation_is_unavailable_without_a_configured_provider(
    engine: Engine, clean_database: None
) -> None:
    """Missing credentials are an ordinary unavailable capability, not a failure."""
    del clean_database
    fixture = _signed_in_with_proposal(engine, providers={})

    estimate = _estimate(fixture)

    assert estimate.json()["available"] is False
    assert estimate.json()["reason"] == "provider_unavailable"
    assert estimate.json()["estimate"] is None


@pytest.mark.integration
def test_a_sufficiently_relevant_stock_picture_suppresses_generation(
    engine: Engine, clean_database: None
) -> None:
    """Generation is the fallback for a beat stock could not illustrate, never the default."""
    del clean_database
    fixture = _signed_in_with_proposal(engine)
    _attach_stock_asset(engine, fixture, relevance=0.91)

    estimate = _estimate(fixture)
    admission = _generate(fixture, key="generate-suppressed", token="unused")

    assert estimate.json()["available"] is False
    assert estimate.json()["reason"] == "stock_sufficient"
    assert_error(admission, status_code=409, code="GENERATION_NOT_ELIGIBLE")


@pytest.mark.integration
def test_a_below_threshold_picture_may_still_be_replaced_by_generation(
    engine: Engine, clean_database: None
) -> None:
    """A weak match is exactly the case generated media exists to answer."""
    del clean_database
    fixture = _signed_in_with_proposal(engine)
    _attach_stock_asset(engine, fixture, relevance=0.10)

    assert _estimate(fixture).json()["available"] is True


@pytest.mark.integration
@pytest.mark.parametrize(("video_enabled", "offered"), ((False, False), (True, True)))
def test_a_still_estimate_says_whether_a_clip_may_even_be_priced(
    engine: Engine, clean_database: None, video_enabled: bool, offered: bool
) -> None:
    """A panel cannot know a deployment's video gate, so the still estimate reports it."""
    del clean_database
    fixture = _signed_in_with_proposal(engine, generative_video_enabled=video_enabled)

    assert _estimate(fixture).json()["videoOffered"] is offered


@pytest.mark.integration
@pytest.mark.parametrize("status", (BrollSuggestionStatus.ACCEPTED, BrollSuggestionStatus.REMOVED))
def test_a_decided_suggestion_cannot_start_another_generation(
    engine: Engine, clean_database: None, status: BrollSuggestionStatus
) -> None:
    """Only a proposal still under review may be sent to a generative provider."""
    del clean_database
    fixture = _signed_in_with_proposal(engine)
    with engine.begin() as connection:
        connection.execute(
            update(BrollSuggestion)
            .where(BrollSuggestion.id == fixture.suggestion_id)
            .values(status=status)
        )

    assert _estimate(fixture).json()["reason"] == "not_reviewable"


@pytest.mark.integration
def test_a_missing_and_a_foreign_suggestion_answer_identically(
    engine: Engine, clean_database: None
) -> None:
    """A guessed identifier must be indistinguishable from one that does not exist."""
    del clean_database
    fixture = _signed_in_with_proposal(engine)
    foreign = _foreign_suggestion(engine, fixture)

    missing_response = fixture.browser.request(
        "POST", _estimate_path(fixture, uuid4()), json={"mediaKind": "image"}
    )
    foreign_response = fixture.browser.request(
        "POST", _estimate_path(fixture, foreign), json={"mediaKind": "image"}
    )

    assert missing_response.status_code == foreign_response.status_code == 404
    assert (
        missing_response.json()["error"]["message"] == (foreign_response.json()["error"]["message"])
    )


@pytest.mark.integration
def test_a_stale_confirmation_cannot_admit_work(engine: Engine, clean_database: None) -> None:
    """A price a member saw an hour ago is not a price this Workspace agreed to pay."""
    del clean_database
    fixture = _signed_in_with_proposal(engine)
    token = _confirmation_token(fixture)
    fixture.clock.advance(timedelta(hours=1))

    response = _generate(fixture, key="generate-stale", token=token)

    assert_error(response, status_code=400, code="GENERATION_CONFIRMATION_INVALID")


@pytest.mark.integration
def test_a_tampered_confirmation_cannot_admit_work(engine: Engine, clean_database: None) -> None:
    """The sealed request is the only request the server will ever act on."""
    del clean_database
    fixture = _signed_in_with_proposal(engine)
    token = _confirmation_token(fixture)

    response = _generate(fixture, key="generate-tampered", token=f"{token}x")

    assert_error(response, status_code=400, code="GENERATION_CONFIRMATION_INVALID")
    with Session(engine) as session:
        assert session.scalars(select(Job)).all() == []


@pytest.mark.integration
def test_a_confirmation_sealed_for_another_user_cannot_be_spent(
    engine: Engine, clean_database: None
) -> None:
    """A confirmation is one User's agreement, not a bearer credential for a Workspace."""
    del clean_database
    fixture = _signed_in_with_proposal(engine)
    sealed = open_generation_confirmation(
        _confirmation_token(fixture), secret=SESSION_SECRET, now=NOW
    )
    forged = seal_generation_confirmation(
        sealed.model_copy(update={"user_id": uuid4()}), secret=SESSION_SECRET
    )

    response = _generate(fixture, key="generate-cross-user", token=forged)

    assert_error(response, status_code=400, code="GENERATION_CONFIRMATION_INVALID")


@pytest.mark.integration
def test_a_confirmation_sealed_by_another_deployment_cannot_be_spent(
    engine: Engine, clean_database: None
) -> None:
    """Only this deployment's secret may agree a price on this deployment's behalf."""
    del clean_database
    fixture = _signed_in_with_proposal(engine)
    sealed = open_generation_confirmation(
        _confirmation_token(fixture), secret=SESSION_SECRET, now=NOW
    )
    forged = seal_generation_confirmation(sealed, secret=f"{SESSION_SECRET}-other")

    response = _generate(fixture, key="generate-foreign-secret", token=forged)

    assert_error(response, status_code=400, code="GENERATION_CONFIRMATION_INVALID")


@pytest.mark.integration
def test_repeating_one_idempotency_key_returns_the_first_job(
    engine: Engine, clean_database: None
) -> None:
    """A member who submits twice must buy one generation, not two."""
    del clean_database
    fixture = _signed_in_with_proposal(engine)
    token = _confirmation_token(fixture)

    first = _generate(fixture, key="generate-repeat", token=token)
    second = _generate(fixture, key="generate-repeat", token=token)

    assert first.status_code == second.status_code == 202
    assert first.json()["jobId"] == second.json()["jobId"]
    with Session(engine) as session:
        assert len(session.scalars(select(Job)).all()) == 1


@pytest.mark.integration
def test_one_idempotency_key_cannot_be_reused_for_another_suggestion(
    engine: Engine, clean_database: None
) -> None:
    """A key bound to one beat's generation must never admit work on a different beat."""
    del clean_database
    fixture = _signed_in_with_proposal(engine)
    other = _second_proposal(engine, fixture)
    _generate(fixture, key="generate-bound")

    response = _generate(fixture, key="generate-bound", suggestion_id=other)

    assert_error(response, status_code=409, code="CONFLICT")


@pytest.mark.integration
def test_an_exhausted_image_budget_refuses_admission_and_leaves_no_job(
    engine: Engine, clean_database: None
) -> None:
    """A Workspace at its monthly allowance must be refused before any provider call."""
    del clean_database
    fixture = _signed_in_with_proposal(engine, monthly_generated_images=1)
    _generate(fixture, key="generate-first")
    other = _second_proposal(engine, fixture)

    response = _generate(fixture, key="generate-second", suggestion_id=other)

    assert_error(response, status_code=429, code="QUOTA_EXCEEDED")
    assert response.headers["Retry-After"]
    with Session(engine) as session:
        assert len(session.scalars(select(Job)).all()) == 1


@pytest.mark.integration
def test_an_exhausted_second_budget_reserves_no_video_either(
    engine: Engine, clean_database: None
) -> None:
    """Two budgets are one decision: a refusal on either must roll the other back."""
    del clean_database
    fixture = _signed_in_with_proposal(
        engine, generative_video_enabled=True, monthly_generated_seconds=2
    )

    response = _generate(
        fixture, key="generate-video-overrun", media_kind="video", video_confirmed=True
    )

    assert_error(response, status_code=429, code="QUOTA_EXCEEDED")
    with Session(engine) as session:
        assert session.scalars(select(WorkspaceQuotaReservation)).all() == []
        assert session.scalars(select(Job)).all() == []


@pytest.mark.integration
def test_a_workspace_at_its_concurrency_limit_admits_no_generation(
    engine: Engine, clean_database: None
) -> None:
    """Generated media is durable work, so it queues behind the same slot limit as the rest."""
    del clean_database
    fixture = _signed_in_with_proposal(engine, concurrent_jobs_per_workspace=1)
    _generate(fixture, key="generate-slot")
    other = _second_proposal(engine, fixture)

    response = _generate(fixture, key="generate-blocked", suggestion_id=other)

    assert_error(response, status_code=429, code="CONCURRENCY_LIMIT")


@pytest.mark.integration
@pytest.mark.parametrize("role", (WorkspaceRole.REVIEWER, WorkspaceRole.VIEWER))
def test_a_member_without_edit_rights_can_neither_price_nor_generate(
    engine: Engine, clean_database: None, role: WorkspaceRole
) -> None:
    """Spending a Workspace's generation budget is an editor's decision, not a reader's."""
    del clean_database
    fixture = _signed_in_with_proposal(engine)
    token = _confirmation_token(fixture)
    _set_role(engine, fixture, role)

    estimate = _estimate(fixture)
    admission = _generate(fixture, key="generate-forbidden", token=token)

    assert_error(estimate, status_code=403, code="FORBIDDEN")
    assert_error(admission, status_code=403, code="FORBIDDEN")


@pytest.mark.integration
def test_generation_requires_csrf_proof(engine: Engine, clean_database: None) -> None:
    """Generation spends a Workspace's budget, so it is a state-changing method."""
    del clean_database
    fixture = _signed_in_with_proposal(engine)
    token = _confirmation_token(fixture)

    response = fixture.browser.request(
        "POST",
        _generate_path(fixture),
        headers={"Idempotency-Key": "generate-csrf"},
        json={"confirmationToken": token, "videoConfirmed": False},
        csrf_token="",
    )

    assert response.status_code in {401, 403}


def _set_role(engine: Engine, fixture: _Fixture, role: WorkspaceRole) -> None:
    """Move the signed-in member to another role inside their own Workspace."""
    user_id = fixture.browser.get("/api/v1/me").json()["id"]
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workspace_memberships SET role = :role "
                "WHERE workspace_id = :workspace_id AND user_id = :user_id"
            ),
            {"role": role.value, "workspace_id": fixture.workspace_id, "user_id": user_id},
        )


def _signed_in_with_proposal(engine: Engine, **overrides: Any) -> _Fixture:
    """Sign one member in and give them a ready clip with one empty proposal."""
    clock = Clock(NOW)
    providers = overrides.pop("providers", None)
    app, flow, _ = build_app(
        clock,
        StubGoogleProvider(clock),
        generation_providers=(fake_generation_providers(clock) if providers is None else providers),
        **overrides,
    )
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    project_id = _ready_project(engine, browser, workspace_id, key="generation-project")
    candidate_id = _clip(engine, workspace_id, project_id)
    suggestion_id = _proposal(engine, workspace_id, project_id, candidate_id, beat=21)
    return _Fixture(
        browser=browser,
        clock=clock,
        workspace_id=workspace_id,
        project_id=project_id,
        candidate_id=candidate_id,
        suggestion_id=suggestion_id,
    )


def _second_proposal(engine: Engine, fixture: _Fixture) -> UUID:
    """Add one more empty proposal to the same clip."""
    return _proposal(
        engine, fixture.workspace_id, fixture.project_id, fixture.candidate_id, beat=41
    )


def _foreign_suggestion(engine: Engine, fixture: _Fixture) -> UUID:
    """Create one proposal belonging to a Workspace this member has no standing in."""
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    provider.identify(subject="3" * 21, email="foreign@example.com", name="Foreign Example")
    app, flow, _ = build_app(clock, provider)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    project_id = _ready_project(engine, browser, workspace_id, key="foreign-project")
    candidate_id = _clip(engine, workspace_id, project_id)
    return _proposal(engine, workspace_id, project_id, candidate_id, beat=21)


def _ready_project(engine: Engine, browser: Browser, workspace_id: UUID, *, key: str) -> UUID:
    """Create one Project through the API and mark it ready for review."""
    created = browser.request(
        "POST",
        f"/api/v1/projects?workspace_id={workspace_id}",
        headers={"Idempotency-Key": key},
        json={"name": key, "sourceKind": "upload"},
    )
    assert created.status_code == 201
    project_id = UUID(created.json()["id"])
    with engine.begin() as connection:
        connection.execute(
            update(Project).where(Project.id == project_id).values(status=ProjectStatus.READY)
        )
    return project_id


def _clip(engine: Engine, workspace_id: UUID, project_id: UUID) -> UUID:
    """Write the source, Transcript, and exposed Clip Candidate a proposal needs."""
    source_id = uuid4()
    transcript_id = uuid4()
    candidate_id = uuid4()
    with engine.begin() as connection:
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
                language="id",
                full_text="",
                words=[],
                speaker_segments=[],
                utterances=[],
                duration_ms=120_000,
                raw_result_storage_key="raw.json",
            )
        )
        connection.execute(
            ClipCandidate.__table__.insert().values(
                id=candidate_id,
                workspace_id=workspace_id,
                project_id=project_id,
                transcript_id=transcript_id,
                rank=1,
                score=0.9,
                hook="hook",
                payoff="payoff",
                reason="reason",
                category="insight",
                tags=[],
                start_ms=0,
                end_ms=60_000,
                start_word_id="w000001",
                end_word_id="w000120",
                transcript_excerpt="",
                context_dependencies=[],
                score_breakdown={},
                context_warnings=[],
                visual_opportunities=[],
                model_metadata={"exposed": True},
            )
        )
    return candidate_id


def _proposal(
    engine: Engine, workspace_id: UUID, project_id: UUID, candidate_id: UUID, *, beat: int
) -> UUID:
    """Write one proposed suggestion with no picture behind it yet."""
    suggestion_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            BrollSuggestion.__table__.insert().values(
                id=suggestion_id,
                workspace_id=workspace_id,
                project_id=project_id,
                candidate_id=candidate_id,
                planner_version=PLANNER_VERSION,
                coverage=BrollCoverage.BALANCED,
                beat_start_word_id=f"w{beat:06d}",
                beat_end_word_id=f"w{beat + 3:06d}",
                start_ms=10_000 + beat * 100,
                end_ms=15_000 + beat * 100,
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
                status=BrollSuggestionStatus.PROPOSED,
                placement_reason="The sentence names an object the viewer cannot see",
                provider_metadata={},
                created_at=NOW,
            )
        )
    return suggestion_id


def _attach_stock_asset(engine: Engine, fixture: _Fixture, *, relevance: float) -> None:
    """Point one proposal at a stock picture scored at the given relevance."""
    asset_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            Asset.__table__.insert().values(
                id=asset_id,
                workspace_id=fixture.workspace_id,
                project_id=fixture.project_id,
                kind=AssetKind.BROLL,
                source_type=AssetSourceType.STOCK,
                storage_key=(
                    f"workspaces/{fixture.workspace_id}/projects/{fixture.project_id}/broll/x"
                ),
                content_type="video/mp4",
                size_bytes=100,
                duration_ms=9_000,
                sha256=b"b" * 32,
            )
        )
        connection.execute(
            update(BrollSuggestion)
            .where(BrollSuggestion.id == fixture.suggestion_id)
            .values(asset_id=asset_id, relevance_score=relevance, source_type="stock")
        )


@pytest.mark.integration
def test_a_queued_generation_job_is_dispatched_only_after_the_commit(
    engine: Engine, clean_database: None
) -> None:
    """A worker must never read a Job the admitting transaction has not committed."""
    del clean_database
    fixture = _signed_in_with_proposal(engine)

    response = _generate(fixture, key="generate-dispatch")

    assert response.status_code == 202
    with Session(engine) as session:
        job = session.scalar(select(Job).where(Job.id == UUID(response.json()["jobId"])))
    assert job is not None
    assert job.status is JobStatus.QUEUED


@pytest.mark.integration
def test_the_generation_runner_is_registered_for_its_job_kind() -> None:
    """Queued BROLL_GENERATE Jobs must not fall through to the unsupported-kind failure."""
    assert JobKind.BROLL_GENERATE in stage_runners()


@pytest.mark.integration
def test_a_generated_still_is_stored_with_complete_provenance(
    engine: Engine, clean_database: None
) -> None:
    """A picture a model drew must be traceable to the prompt and model that drew it."""
    del clean_database
    stage = _running_generation(engine)
    store = _Store()

    _runner(_generation_dependencies(stage.clock, store=store))(stage.context)

    with Session(engine) as session:
        asset = session.scalar(
            select(Asset).where(
                Asset.workspace_id == stage.fixture.workspace_id, Asset.kind == AssetKind.BROLL
            )
        )
        provenance = session.scalar(
            select(AssetProvenance).where(
                AssetProvenance.workspace_id == stage.fixture.workspace_id
            )
        )
    assert asset is not None
    assert asset.source_type is AssetSourceType.GENERATED
    assert asset.content_type == "image/png"
    assert (asset.width, asset.height) == (8, 8)
    assert asset.sha256 == sha256(IMAGE_BYTES).digest()
    assert provenance is not None
    assert provenance.asset_id == asset.id
    assert provenance.provider == "fake"
    assert provenance.provider_asset_id == "fake-request-1"
    assert provenance.model == "fake/image"
    assert provenance.model_version == "test-1"
    assert provenance.prompt and "Subject:" in provenance.prompt
    assert provenance.moderation_result == "approved"
    assert provenance.checksum == sha256(IMAGE_BYTES).digest()
    assert "provider.invalid" not in provenance.terms_snapshot
    assert store.keys == [
        f"workspaces/{stage.fixture.workspace_id}/projects/{stage.fixture.project_id}"
        f"/generated/{asset.id}/broll"
    ]


@pytest.mark.integration
def test_a_generated_picture_returns_its_suggestion_to_review(
    engine: Engine, clean_database: None
) -> None:
    """Generated media is a proposal: a member still decides whether it goes in the clip."""
    del clean_database
    stage = _running_generation(engine)

    _runner(_generation_dependencies(stage.clock))(stage.context)

    suggestion = _suggestion(engine, stage.fixture)
    assert suggestion.status is BrollSuggestionStatus.PROPOSED
    assert suggestion.source_type is BrollSourceType.GENERATED
    assert suggestion.asset_id is not None


@pytest.mark.integration
def test_a_success_settles_the_usage_the_provider_actually_reported(
    engine: Engine, clean_database: None
) -> None:
    """A Workspace is charged what it used, not what it was quoted."""
    del clean_database
    stage = _running_generation(engine)

    _runner(_generation_dependencies(stage.clock))(stage.context)

    assert _settled(engine, stage.job_id) == {QuotaResource.GENERATED_IMAGES: Decimal(1)}


@pytest.mark.integration
def test_a_redelivered_job_polls_its_stored_request_and_never_submits_twice(
    engine: Engine, clean_database: None
) -> None:
    """The provider request ID is the idempotency anchor for every later attempt."""
    del clean_database
    stage = _running_generation(engine)
    providers = fake_generation_providers(stage.clock)
    image = providers(GenerationMediaKind.IMAGE)
    assert isinstance(image, FakeGenerativeMediaProvider)
    runner = _runner(_generation_dependencies(stage.clock, providers=providers))

    runner(stage.context)
    runner(stage.context)

    assert image.submit_count == 1
    with Session(engine) as session:
        assets = session.scalars(select(Asset).where(Asset.kind == AssetKind.BROLL)).all()
    assert len(assets) == 1
    assert _settled(engine, stage.job_id) == {QuotaResource.GENERATED_IMAGES: Decimal(1)}


@pytest.mark.integration
def test_a_provider_still_working_at_the_deadline_is_retried_later(
    engine: Engine, clean_database: None
) -> None:
    """A slow generation must free the worker rather than hold it for the whole render."""
    del clean_database
    stage = _running_generation(engine)
    ticks = iter([0.0, 0.0, 1.0, 61.0, 62.0, 63.0])
    dependencies = _generation_dependencies(
        stage.clock,
        providers=_pending_providers(stage.clock),
        monotonic=lambda: next(ticks),
    )

    with pytest.raises(RetryableJobError) as raised:
        _runner(dependencies)(stage.context)

    assert str(raised.value) == POLL_TIMEOUT_CODE


@pytest.mark.integration
def test_a_moderation_rejection_is_terminal_and_visible_to_the_member(
    engine: Engine, clean_database: None
) -> None:
    """A refused prompt must not be retried, and must not look like an outage."""
    del clean_database
    stage = _running_generation(engine)
    dependencies = _generation_dependencies(
        stage.clock, providers=_rejecting_providers(stage.clock)
    )

    with pytest.raises(TerminalJobError):
        _runner(dependencies)(stage.context)

    assert _suggestion(engine, stage.fixture).status is BrollSuggestionStatus.FAILED
    with Session(engine) as session:
        assert session.scalars(select(Asset).where(Asset.kind == AssetKind.BROLL)).all() == []


@pytest.mark.integration
def test_media_that_is_not_a_supported_image_creates_no_asset(
    engine: Engine, clean_database: None
) -> None:
    """Whatever a provider returns, only real decodable media may become an Asset."""
    del clean_database
    stage = _running_generation(engine)
    dependencies = _generation_dependencies(
        stage.clock, downloader=_Downloader(payload=b"not-an-image")
    )

    with pytest.raises(TerminalJobError) as raised:
        _runner(dependencies)(stage.context)

    assert str(raised.value) == MEDIA_INVALID_CODE
    with Session(engine) as session:
        assert session.scalars(select(Asset).where(Asset.kind == AssetKind.BROLL)).all() == []
        assert session.scalars(select(AssetProvenance)).all() == []


@pytest.mark.integration
def test_a_cancelled_generation_stops_the_provider_and_keeps_no_media(
    engine: Engine, clean_database: None
) -> None:
    """A member who cancels must not keep paying for work nobody will look at."""
    del clean_database
    stage = _running_generation(engine)
    _request_cancellation(engine, stage)
    providers = fake_generation_providers(stage.clock)

    with pytest.raises(JobCancelledError):
        _runner(_generation_dependencies(stage.clock, providers=providers))(stage.context)

    with Session(engine) as session:
        assert session.scalars(select(Asset).where(Asset.kind == AssetKind.BROLL)).all() == []


@pytest.mark.integration
def test_a_permanently_failed_job_releases_its_budget_and_fails_its_suggestion(
    engine: Engine, clean_database: None
) -> None:
    """Budget a Workspace never spent must come back the moment the work is given up."""
    del clean_database
    stage = _running_generation(engine)

    with runtime_session(stage) as session:
        fail_job(
            session,
            workspace_id=stage.fixture.workspace_id,
            job_id=stage.job_id,
            error_code="GENERATION_PROVIDER_REJECTED",
            retryable=False,
            now=NOW,
        )

    assert _reserved_resources(engine, stage.job_id) == {}
    assert _released(engine, stage.job_id) == {QuotaResource.GENERATED_IMAGES}
    assert _suggestion(engine, stage.fixture).status is BrollSuggestionStatus.FAILED


@dataclass(frozen=True, slots=True)
class _Stage:
    """One admitted generation, its Job, and the worker context that will run it."""

    fixture: _Fixture
    clock: Clock
    context: JobContext
    job_id: UUID


class _Downloader:
    """Write fixed bytes and record every ephemeral capability a generation fetched."""

    def __init__(self, *, payload: bytes = b"") -> None:
        """Bind the bytes every download produces."""
        self.payload = payload or IMAGE_BYTES
        self.urls: list[str] = []

    def download(
        self,
        url: str,
        destination: Any,
        *,
        expected_size: int,
        max_bytes: int,
        cancellation_check: Any,
    ) -> Any:
        """Record the URL, write the bytes, and report the digest ingest would."""
        del expected_size, max_bytes, cancellation_check
        self.urls.append(url)
        destination.write(self.payload)

        @dataclass(frozen=True, slots=True)
        class _Downloaded:
            size_bytes: int
            sha256: bytes

        return _Downloaded(size_bytes=len(self.payload), sha256=sha256(self.payload).digest())


class _Media:
    """A media processor that reports fixed metadata and writes a fixed proxy."""

    def probe(self, source: Path, *, cancellation_check: Any) -> SourceMetadata:
        """Report one short portrait clip."""
        del source, cancellation_check
        return SourceMetadata(
            duration_ms=5_000,
            width=720,
            height=1280,
            video_codecs=("h264",),
            audio_codecs=(),
            variable_frame_rate=False,
        )

    def generate_proxy(
        self,
        source: Path,
        output: Path,
        *,
        duration_ms: int,
        cancellation_check: Any,
        progress: Any,
    ) -> None:
        """Write one small normalized rendition beside the original."""
        del source, duration_ms, cancellation_check, progress
        output.write_bytes(b"proxy-bytes")


class _Store:
    """An object store that records every key written."""

    def __init__(self) -> None:
        """Start with nothing stored."""
        self.keys: list[str] = []

    def put_file(
        self, *, key: str, content_type: str, file: Any, sha256: bytes | None = None
    ) -> StoredObject:
        """Record one stored object exactly as the runner named it."""
        del sha256
        body = file.read()
        self.keys.append(key)
        return StoredObject(key=key, content_type=content_type, content_length=len(body))


def _png_bytes() -> bytes:
    """Render the smallest real PNG the image validator can actually decode."""
    buffer = io.BytesIO()
    PIL.Image.new("RGB", (8, 8), color=(20, 30, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


IMAGE_BYTES = _png_bytes()


def _generation_dependencies(
    clock: Clock,
    *,
    providers: Any = None,
    store: _Store | None = None,
    downloader: _Downloader | None = None,
    monotonic: Any = None,
) -> GenerationDependencies:
    """Compose deterministic capabilities in place of every external service."""
    return GenerationDependencies(
        providers=providers or fake_generation_providers(clock),
        object_store=store or _Store(),
        downloader=downloader or _Downloader(),
        media=_Media(),
        sleep=lambda _: None,
        monotonic=monotonic or (lambda: 0.0),
    )


def _runner(dependencies: GenerationDependencies) -> BrollGenerateStageRunner:
    """Build the runner with a frozen clock so provenance timestamps are exact."""
    return BrollGenerateStageRunner(dependencies_factory=lambda _: dependencies, clock=lambda: NOW)


def _pending_providers(clock: Clock) -> Any:
    """Offer a provider whose work never finishes inside one attempt."""
    pending = _StuckProvider(clock)
    return lambda _: pending


def _rejecting_providers(clock: Clock) -> Any:
    """Offer a provider that refuses the request on safety grounds."""
    rejecting = _RejectingProvider(clock)
    return lambda _: rejecting


class _StuckProvider(FakeGenerativeMediaProvider):
    """A provider whose submitted request stays queued for the whole attempt."""

    def __init__(self, clock: Clock) -> None:
        """Reuse the deterministic fake, answering every poll as still queued."""
        base = fake_generation_providers(clock)(GenerationMediaKind.IMAGE)
        assert isinstance(base, FakeGenerativeMediaProvider)
        super().__init__(
            estimate=base.estimate(request=_image_request()),
            result=GenerationResult(
                status=GenerationStatus.QUEUED,
                output=None,
                usage=GenerationUsage.zero(),
                seed=None,
                moderation=GenerationModerationResult.PENDING,
            ),
            utc_clock=clock,
        )


class _RejectingProvider(FakeGenerativeMediaProvider):
    """A provider that returns one moderation refusal instead of media."""

    def __init__(self, clock: Clock) -> None:
        """Reuse the deterministic fake, answering with an output refusal."""
        base = fake_generation_providers(clock)(GenerationMediaKind.IMAGE)
        assert isinstance(base, FakeGenerativeMediaProvider)
        super().__init__(
            estimate=base.estimate(request=_image_request()),
            result=GenerationResult(
                status=GenerationStatus.MODERATION_REJECTED,
                output=None,
                usage=GenerationUsage.zero(),
                seed=None,
                moderation=GenerationModerationResult.OUTPUT_REJECTED,
            ),
            utc_clock=clock,
        )


def _image_request() -> GenerationRequest:
    """Name the still request every fake provider in this module is asked for."""
    return GenerationRequest(
        prompt="Subject: a shortened signup form.",
        media_kind=GenerationMediaKind.IMAGE,
        output_count=1,
        duration_ms=None,
        width=1080,
        height=1920,
        model_alias="image-default",
        seed=None,
    )


def _running_generation(engine: Engine, **overrides: Any) -> _Stage:
    """Admit one still through the API, then claim its Job the way a worker would."""
    fixture = _signed_in_with_proposal(engine, **overrides)
    response = _generate(fixture, key="worker-generate")
    assert response.status_code == 202
    job_id = UUID(response.json()["jobId"])
    with engine.begin() as connection:
        connection.execute(
            update(Job).where(Job.id == job_id).values(status=JobStatus.RUNNING, attempt=1)
        )
    user_id = UUID(fixture.browser.get("/api/v1/me").json()["id"])
    return _Stage(
        fixture=fixture,
        clock=fixture.clock,
        context=JobContext(
            job_id=job_id,
            workspace_id=fixture.workspace_id,
            project_id=fixture.project_id,
            user_id=user_id,
            attempt=1,
            settings=runtime_settings(RuntimeRole.WORKER),
        ),
        job_id=job_id,
    )


@contextmanager
def runtime_session(stage: _Stage) -> Iterator[Any]:
    """Open one worker transaction the way the durable job machinery does."""
    with session_scope(
        settings=stage.context.settings,
        workspace_id=stage.fixture.workspace_id,
        user_id=stage.context.user_id,
        runtime_role=RuntimeRole.WORKER,
    ) as session:
        yield session


def _request_cancellation(engine: Engine, stage: _Stage) -> None:
    """Record the durable cancel a member's request would have written."""
    with engine.begin() as connection:
        connection.execute(
            update(Job)
            .where(Job.id == stage.job_id)
            .values(status=JobStatus.CANCEL_REQUESTED, cancel_requested_at=NOW)
        )


def _settled(engine: Engine, job_id: UUID) -> dict[QuotaResource, Decimal]:
    """Read the usage every settled reservation of one Job was finally charged."""
    with Session(engine) as session:
        rows = session.execute(
            select(
                WorkspaceQuotaReservation.resource,
                WorkspaceQuotaReservation.actual_units,
            ).where(
                WorkspaceQuotaReservation.reference_id == job_id,
                WorkspaceQuotaReservation.status == QuotaReservationStatus.SETTLED,
            )
        ).all()
    return {resource: Decimal(units) for resource, units in rows}


def _released(engine: Engine, job_id: UUID) -> set[QuotaResource]:
    """Name every budget of one Job that was handed back unspent."""
    with Session(engine) as session:
        rows = session.scalars(
            select(WorkspaceQuotaReservation.resource).where(
                WorkspaceQuotaReservation.reference_id == job_id,
                WorkspaceQuotaReservation.status == QuotaReservationStatus.RELEASED,
            )
        ).all()
    return set(rows)
