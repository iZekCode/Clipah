"""Contract tests for review-safe ranked Clip Candidate reads."""

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
    ClipCandidate,
    Project,
    ProjectStatus,
    Transcript,
)
from harness import NOW, Browser, Clock, StubGoogleProvider, assert_error, build_app, sign_in


@dataclass(frozen=True, slots=True)
class CandidateFixture:
    """Identifiers for one ready Project and its ranked candidates."""

    workspace_id: UUID
    project_id: UUID
    candidate_ids: tuple[UUID, ...]
    hidden_candidate_id: UUID


@pytest.mark.integration
def test_candidate_collection_is_ranked_paginated_and_excludes_internal_candidates(
    engine: Engine, clean_database: None
) -> None:
    """Pagination must preserve global rank without exposing the stored internal tail."""
    del clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    fixture = _ready_project_with_candidates(engine, browser)

    first = browser.get(f"{_collection_path(fixture)}&limit=2")

    assert first.status_code == 200
    assert [candidate["rank"] for candidate in first.json()["candidates"]] == [1, 2]
    assert first.json()["nextCursor"] is not None

    second = browser.get(f"{_collection_path(fixture)}&limit=2&cursor={first.json()['nextCursor']}")

    assert second.status_code == 200
    assert [candidate["rank"] for candidate in second.json()["candidates"]] == [3]
    assert second.json()["nextCursor"] is None
    assert all(
        candidate["id"] != str(fixture.hidden_candidate_id)
        for candidate in first.json()["candidates"] + second.json()["candidates"]
    )


@pytest.mark.integration
def test_candidate_response_exposes_review_evidence_but_no_provider_or_storage_secrets(
    engine: Engine, clean_database: None
) -> None:
    """Reviewers need explainability, never the private provider and storage evidence behind it."""
    del clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    fixture = _ready_project_with_candidates(engine, browser)

    response = browser.get(
        f"/api/v1/projects/{fixture.project_id}/candidates/{fixture.candidate_ids[0]}"
        f"?workspace_id={fixture.workspace_id}"
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": str(fixture.candidate_ids[0]),
        "projectId": str(fixture.project_id),
        "rank": 1,
        "score": 0.91,
        "hook": "The surprising opening",
        "payoff": "The useful resolution",
        "reason": "A complete and useful moment",
        "category": "insight",
        "tags": ["creator", "workflow"],
        "startMs": 1_000,
        "endMs": 31_000,
        "durationMs": 30_000,
        "transcriptExcerpt": "A complete and useful moment.",
        "contextDependencies": ["The speaker is discussing editing."],
        "scoreBreakdown": {
            "hook": 0.9,
            "payoff": 0.92,
            "narrativeCompleteness": 0.93,
            "contextSafety": 0.94,
            "platformFit": 0.88,
            "transcriptConfidence": 0.97,
            "visualOpportunity": 0.8,
        },
        "contextWarnings": ["Needs a source overlay."],
        "visualOpportunities": ["Show the workflow chart."],
        "createdAt": NOW.isoformat(),
    }
    serialized = response.text
    for forbidden in (
        "storageKey",
        "rawResultStorageKey",
        "modelMetadata",
        "provider",
        "providerRequestId",
        "request-secret",
        "w000001",
        "workspaces/",
    ):
        assert forbidden not in serialized


@pytest.mark.integration
def test_candidate_detail_scopes_identity_to_project_and_hides_internal_rows(
    engine: Engine, clean_database: None
) -> None:
    """A candidate from another Project or the hidden tail must look exactly missing."""
    del clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    first = _ready_project_with_candidates(engine, browser, key="candidate-first")
    second = _ready_project_with_candidates(engine, browser, key="candidate-second")

    valid = browser.get(
        f"/api/v1/projects/{first.project_id}/candidates/{first.candidate_ids[0]}"
        f"?workspace_id={first.workspace_id}"
    )

    wrong_project = browser.get(
        f"/api/v1/projects/{second.project_id}/candidates/{first.candidate_ids[0]}"
        f"?workspace_id={first.workspace_id}"
    )
    hidden = browser.get(
        f"/api/v1/projects/{first.project_id}/candidates/{first.hidden_candidate_id}"
        f"?workspace_id={first.workspace_id}"
    )
    missing = browser.get(
        f"/api/v1/projects/{first.project_id}/candidates/{uuid4()}"
        f"?workspace_id={first.workspace_id}"
    )

    assert valid.status_code == 200
    for response in (wrong_project, hidden, missing):
        assert_error(response, status_code=404, code="NOT_FOUND")
    assert wrong_project.json()["error"]["message"] == missing.json()["error"]["message"]
    assert hidden.json()["error"]["message"] == missing.json()["error"]["message"]


@pytest.mark.integration
def test_candidate_collection_hides_a_foreign_project_like_a_missing_project(
    engine: Engine, clean_database: None
) -> None:
    """Workspace Membership must be proven before candidate ownership is queried."""
    del clean_database
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    app, flow, _ = build_app(clock, provider)
    owner = Browser(app)
    sign_in(owner, flow)
    owned = _ready_project_with_candidates(engine, owner)

    provider.identify(subject="candidate-stranger", email="stranger@example.com", name="Stranger")
    stranger = Browser(app)
    sign_in(stranger, flow)
    stranger_workspace = UUID(stranger.get("/api/v1/workspaces").json()["workspaces"][0]["id"])

    valid = owner.get(
        f"/api/v1/projects/{owned.project_id}/candidates?workspace_id={owned.workspace_id}"
    )

    guessed = stranger.get(
        f"/api/v1/projects/{owned.project_id}/candidates?workspace_id={stranger_workspace}"
    )
    missing = stranger.get(
        f"/api/v1/projects/{uuid4()}/candidates?workspace_id={stranger_workspace}"
    )

    assert valid.status_code == 200
    assert_error(guessed, status_code=404, code="NOT_FOUND")
    assert_error(missing, status_code=404, code="NOT_FOUND")
    assert guessed.json()["error"]["message"] == missing.json()["error"]["message"]


@pytest.mark.integration
@pytest.mark.parametrize(
    "cursor", ["not-a-cursor", "MHxhYWFhYWFhYS1hYWFhLWFhYWEtYWFhYS1hYWFhYWFhYWFh"]
)
def test_candidate_collection_rejects_malformed_and_nonpositive_cursors(
    engine: Engine, clean_database: None, cursor: str
) -> None:
    """An invalid pagination boundary must fail before it can alter the ranked query."""
    del clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    fixture = _ready_project_with_candidates(engine, browser)

    response = browser.get(f"{_collection_path(fixture)}&cursor={cursor}")

    assert_error(response, status_code=422, code="VALIDATION_ERROR")


@pytest.mark.integration
def test_candidate_collection_hides_results_when_analysis_did_not_finish_ready(
    engine: Engine, clean_database: None
) -> None:
    """Persisted rows from canceled or failed work must not become reviewable output."""
    del clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    fixture = _ready_project_with_candidates(engine, browser)
    with engine.begin() as connection:
        connection.execute(
            update(Project)
            .where(Project.id == fixture.project_id)
            .values(status=ProjectStatus.FAILED)
        )

    response = browser.get(_collection_path(fixture))

    assert_error(response, status_code=404, code="NOT_FOUND")


@pytest.mark.unit
def test_candidate_openapi_declares_strict_collection_and_detail_schemas() -> None:
    """Generated clients need concrete candidate fields rather than arbitrary dictionaries."""
    clock = Clock(NOW)
    app, _, _ = build_app(clock, StubGoogleProvider(clock))
    paths = app.openapi()["paths"]

    collection = paths["/api/v1/projects/{project_id}/candidates"]["get"]["responses"]["200"]
    detail = paths["/api/v1/projects/{project_id}/candidates/{candidate_id}"]["get"]["responses"][
        "200"
    ]

    assert collection["content"]["application/json"]["schema"]["$ref"].endswith(
        "/CandidatePageResponse"
    )
    assert detail["content"]["application/json"]["schema"]["$ref"].endswith("/CandidateResponse")


def _ready_project_with_candidates(
    engine: Engine, browser: Browser, *, key: str = "candidate-project"
) -> CandidateFixture:
    """Create a ready Project with three exposed rows and one private reranking row."""
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
    candidate_ids = tuple(uuid4() for _ in range(3))
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
                duration_ms=60_000,
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
                duration_ms=60_000,
                raw_result_storage_key=(
                    f"workspaces/{workspace_id}/projects/{project_id}/transcripts/raw.json"
                ),
            )
        )
        rows = [
            _candidate_values(
                candidate_id=candidate_id,
                workspace_id=workspace_id,
                project_id=project_id,
                transcript_id=transcript_id,
                rank=rank,
                exposed=True,
            )
            for rank, candidate_id in enumerate(candidate_ids, start=1)
        ]
        rows.append(
            _candidate_values(
                candidate_id=hidden_id,
                workspace_id=workspace_id,
                project_id=project_id,
                transcript_id=transcript_id,
                rank=4,
                exposed=False,
            )
        )
        connection.execute(ClipCandidate.__table__.insert(), rows)
    return CandidateFixture(
        workspace_id=workspace_id,
        project_id=project_id,
        candidate_ids=candidate_ids,
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
    """Build one literal persisted candidate whose private metadata is deliberately toxic."""
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
        "tags": ["creator", "workflow"],
        "start_ms": 1_000,
        "end_ms": 31_000,
        "start_word_id": "w000001",
        "end_word_id": "w000099",
        "transcript_excerpt": "A complete and useful moment.",
        "context_dependencies": ["The speaker is discussing editing."],
        "score_breakdown": {
            "hook": 0.9,
            "payoff": 0.92,
            "narrative_completeness": 0.93,
            "context_safety": 0.94,
            "platform_fit": 0.88,
            "transcript_confidence": 0.97,
            "visual_opportunity": 0.8,
        },
        "context_warnings": ["Needs a source overlay."],
        "visual_opportunities": ["Show the workflow chart."],
        "model_metadata": {
            "exposed": exposed,
            "provider": "private-provider",
            "providerRequestId": "request-secret",
            "rawOutput": "must never leave",
        },
        "created_at": NOW,
    }


def _collection_path(fixture: CandidateFixture) -> str:
    """Build the ranked candidate collection URL for one selected Workspace."""
    return f"/api/v1/projects/{fixture.project_id}/candidates?workspace_id={fixture.workspace_id}"
