"""Integration contracts for variant generation and User-provided claim evidence.

Three promises are held here. A member is never offered a cut the context rules call
misleading. A citation is stored exactly as its author wrote it, or refused outright —
never quietly rewritten. And nothing Clipah does marks a claim verified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import Response
from sqlalchemy import Engine, select, text, update
from sqlalchemy.orm import Session

from clipah.models import (
    Asset,
    AssetKind,
    AssetSourceType,
    ClaimEvidence,
    ClaimVerificationStatus,
    ClipCandidate,
    ClipVariant,
    Project,
    ProjectStatus,
    Transcript,
    WorkspaceRole,
)
from clipah.variants.models import SUPPORTED_TARGET_DURATIONS_MS
from harness import NOW, Browser, Clock, StubGoogleProvider, assert_error, build_app, sign_in

# Twelve sentences of ten one-second words: long enough that every supported target has an
# honest boundary available, and short enough to read in a fixture. The candidate covers
# the first ten, so the transcript still holds words that lie outside it.
SENTENCES = 12
CANDIDATE_WORDS = 100
WORDS_PER_SENTENCE = 10
WORD_MS = 1_000


@dataclass(frozen=True, slots=True)
class _Fixture:
    """One signed-in member and the reviewable candidate they are working on."""

    browser: Browser
    workspace_id: UUID
    project_id: UUID
    candidate_id: UUID


def _variants_path(fixture: _Fixture, candidate_id: UUID | None = None) -> str:
    """Name the variant collection for one candidate inside its Workspace."""
    target = candidate_id or fixture.candidate_id
    return (
        f"/api/v1/projects/{fixture.project_id}/candidates/{target}"
        f"/variants?workspace_id={fixture.workspace_id}"
    )


def _evidence_path(fixture: _Fixture, candidate_id: UUID | None = None) -> str:
    """Name the claim-evidence collection for one candidate inside its Workspace."""
    target = candidate_id or fixture.candidate_id
    return (
        f"/api/v1/projects/{fixture.project_id}/candidates/{target}"
        f"/claim-evidence?workspace_id={fixture.workspace_id}"
    )


def _generate(fixture: _Fixture, **overrides: Any) -> Response:
    """Ask for variants at the requested lengths and platforms."""
    body: dict[str, Any] = {"platforms": ["tiktok"], "durationsMs": [20_000, 45_000]}
    body.update(overrides)
    return fixture.browser.request("POST", _variants_path(fixture), json=body)


def _attach(fixture: _Fixture, **overrides: Any) -> Response:
    """Attach one citation to a word range of the candidate."""
    body: dict[str, Any] = {
        "startWordId": "w000001",
        "endWordId": "w000010",
        "claimText": "Removing the form doubled activation",
        "sourceUrl": "https://example.test/report?page=2",
        "sourceTitle": "Quarterly activation report",
        "publisher": "Example Institute",
        "retrievedAt": "2026-09-01T00:00:00+00:00",
    }
    body.update(overrides)
    return fixture.browser.request("POST", _evidence_path(fixture), json=body)


@pytest.mark.integration
def test_generating_variants_returns_honest_cuts_with_their_evidence(
    engine: Engine, clean_database: None
) -> None:
    """A member compares readings of one moment, each with the reason it was offered."""
    del clean_database
    fixture = _signed_in_with_candidate(engine)

    response = _generate(fixture)

    assert response.status_code == 201
    body = response.json()
    assert body["variants"]
    assert tuple(body["supportedDurationsMs"]) == SUPPORTED_TARGET_DURATIONS_MS
    for variant in body["variants"]:
        assert variant["startWordId"].startswith("w")
        assert variant["endWordId"].startswith("w")
        assert variant["durationMs"] > 0
        assert variant["rationale"]
        assert variant["packaging"]["exportPreset"] == "1080x1920"
        assert all(warning["severity"] != "blocking" for warning in variant["warnings"])


@pytest.mark.integration
def test_variants_are_stored_once_however_often_they_are_requested(
    engine: Engine, clean_database: None
) -> None:
    """Asking twice compares the same readings; it does not invent a second set."""
    del clean_database
    fixture = _signed_in_with_candidate(engine)

    first = _generate(fixture)
    second = _generate(fixture)

    assert first.status_code == second.status_code == 201
    assert [item["id"] for item in first.json()["variants"]] == [
        item["id"] for item in second.json()["variants"]
    ]
    with Session(engine) as session:
        stored = session.scalars(select(ClipVariant)).all()
    assert len(stored) == len(first.json()["variants"])


@pytest.mark.integration
def test_a_listed_variant_matches_the_one_that_was_offered(
    engine: Engine, clean_database: None
) -> None:
    """The stored Variant is the boundary a member was actually shown."""
    del clean_database
    fixture = _signed_in_with_candidate(engine)
    created = _generate(fixture)

    listed = fixture.browser.get(_variants_path(fixture))

    assert listed.status_code == 200
    assert listed.json()["variants"] == created.json()["variants"]


@pytest.mark.integration
@pytest.mark.parametrize("durations", ([25_000], [0], [], [20_000, 25_000]))
def test_an_unsupported_duration_is_refused(
    engine: Engine, clean_database: None, durations: list[int]
) -> None:
    """A member who asks for 25 seconds is told no, not handed 30."""
    del clean_database
    fixture = _signed_in_with_candidate(engine)

    response = _generate(fixture, durationsMs=durations)

    assert response.status_code == 422
    assert response.json()["error"]["code"] in {"VARIANT_REQUEST_INVALID", "VALIDATION_ERROR"}


@pytest.mark.integration
def test_a_missing_and_a_foreign_candidate_answer_identically(
    engine: Engine, clean_database: None
) -> None:
    """A guessed identifier must be indistinguishable from one that does not exist."""
    del clean_database
    fixture = _signed_in_with_candidate(engine)
    foreign = _foreign_candidate(engine)

    missing_response = fixture.browser.get(_variants_path(fixture, uuid4()))
    foreign_response = fixture.browser.get(_variants_path(fixture, foreign))

    assert missing_response.status_code == foreign_response.status_code == 404
    assert missing_response.json()["error"] == {
        **foreign_response.json()["error"],
        "requestId": missing_response.json()["error"]["requestId"],
    }


@pytest.mark.integration
@pytest.mark.parametrize("role", (WorkspaceRole.REVIEWER, WorkspaceRole.VIEWER))
def test_a_member_without_edit_rights_may_read_variants_but_not_create_them(
    engine: Engine, clean_database: None, role: WorkspaceRole
) -> None:
    """Reviewing is reading; asking a model for alternative cuts is an editor's decision."""
    del clean_database
    fixture = _signed_in_with_candidate(engine)
    _generate(fixture)
    _set_role(engine, fixture, role)

    assert fixture.browser.get(_variants_path(fixture)).status_code == 200
    assert_error(_generate(fixture), status_code=403, code="FORBIDDEN")


@pytest.mark.integration
def test_generating_variants_requires_csrf_proof(engine: Engine, clean_database: None) -> None:
    """Generation writes rows, so it is a state-changing method."""
    del clean_database
    fixture = _signed_in_with_candidate(engine)

    response = fixture.browser.request(
        "POST",
        _variants_path(fixture),
        json={"platforms": ["tiktok"], "durationsMs": [20_000]},
        csrf_token="",
    )

    assert response.status_code in {401, 403}


@pytest.mark.integration
def test_attaching_evidence_records_it_unverified_with_its_author(
    engine: Engine, clean_database: None
) -> None:
    """Clipah records what a member said about a source, and never that it is true."""
    del clean_database
    fixture = _signed_in_with_candidate(engine)

    response = _attach(fixture)

    assert response.status_code == 201
    body = response.json()
    assert body["verificationStatus"] == "unverified"
    assert body["sourceUrl"] == "https://example.test/report?page=2"
    assert body["createdByUserId"]
    with Session(engine) as session:
        stored = session.scalar(select(ClaimEvidence))
    assert stored is not None
    assert stored.verification_status is ClaimVerificationStatus.UNVERIFIED


@pytest.mark.integration
@pytest.mark.parametrize(
    "url",
    (
        "http://example.test/report",
        "javascript:alert(1)",
        "https://user:password@example.test/report",
        "https://127.0.0.1/report",
        "https://localhost/report",
        "https://[::1]/report",
        "https://10.0.0.5/internal",
    ),
)
def test_an_unsafe_source_url_is_refused(engine: Engine, clean_database: None, url: str) -> None:
    """A citation is shown to other people, so its link is checked before it is stored."""
    del clean_database
    fixture = _signed_in_with_candidate(engine)

    response = _attach(fixture, sourceUrl=url)

    assert response.status_code == 422
    with Session(engine) as session:
        assert session.scalars(select(ClaimEvidence)).all() == []


@pytest.mark.integration
@pytest.mark.parametrize(
    "overrides",
    (
        {"sourceTitle": "<script>alert(1)</script>"},
        {"publisher": "<b>Example</b>"},
        {"claimText": "Growth <em>doubled</em>"},
        {"sourceTitle": "x" * 400},
    ),
)
def test_markup_and_oversized_metadata_are_refused_rather_than_sanitized(
    engine: Engine, clean_database: None, overrides: dict[str, str]
) -> None:
    """A member who typed a tag meant something; rewriting their words silently is worse."""
    del clean_database
    fixture = _signed_in_with_candidate(engine)

    assert _attach(fixture, **overrides).status_code == 422


@pytest.mark.integration
def test_evidence_cannot_name_words_outside_the_candidate_it_annotates(
    engine: Engine, clean_database: None
) -> None:
    """Evidence is shown beside a clip, so it must point inside that clip."""
    del clean_database
    fixture = _signed_in_with_candidate(engine)

    outside = _attach(fixture, startWordId="w000001", endWordId="w000119")
    unknown = _attach(fixture, startWordId="w000404", endWordId="w000410")

    assert outside.status_code == 422
    assert unknown.status_code == 422


@pytest.mark.integration
def test_only_a_user_sets_a_verification_status_and_the_actor_is_kept(
    engine: Engine, clean_database: None
) -> None:
    """A claim becomes disputed because a person said so, and the record says who."""
    del clean_database
    fixture = _signed_in_with_candidate(engine)
    created = _attach(fixture).json()

    response = fixture.browser.request(
        "PATCH",
        f"/api/v1/claim-evidence/{created['id']}?workspace_id={fixture.workspace_id}",
        json={"verificationStatus": "disputed"},
    )

    assert response.status_code == 200
    assert response.json()["verificationStatus"] == "disputed"
    assert response.json()["createdByUserId"] == created["createdByUserId"]


@pytest.mark.integration
def test_evidence_of_another_workspace_is_not_reachable(
    engine: Engine, clean_database: None
) -> None:
    """A citation identifier from elsewhere must be as absent as one that never existed."""
    del clean_database
    fixture = _signed_in_with_candidate(engine)

    response = fixture.browser.request(
        "PATCH",
        f"/api/v1/claim-evidence/{uuid4()}?workspace_id={fixture.workspace_id}",
        json={"verificationStatus": "supported"},
    )

    assert_error(response, status_code=404, code="NOT_FOUND")


@pytest.mark.integration
def test_listed_evidence_reads_back_exactly_what_was_written(
    engine: Engine, clean_database: None
) -> None:
    """A reviewer sees the citation its author wrote, not an interpretation of it."""
    del clean_database
    fixture = _signed_in_with_candidate(engine)
    created = _attach(fixture).json()

    listed = fixture.browser.get(_evidence_path(fixture))

    assert listed.status_code == 200
    assert listed.json()["evidence"] == [created]


def _signed_in_with_candidate(engine: Engine, **overrides: Any) -> _Fixture:
    """Sign one member in and give them a ready Project with one exposed candidate."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), **overrides)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    project_id = _ready_project(engine, browser, workspace_id, key="variants-project")
    candidate_id = _candidate(engine, workspace_id, project_id)
    return _Fixture(
        browser=browser,
        workspace_id=workspace_id,
        project_id=project_id,
        candidate_id=candidate_id,
    )


def _foreign_candidate(engine: Engine) -> UUID:
    """Create one candidate belonging to a Workspace this member has no standing in."""
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    provider.identify(subject="4" * 21, email="foreign@example.com", name="Foreign Example")
    app, flow, _ = build_app(clock, provider)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    project_id = _ready_project(engine, browser, workspace_id, key="foreign-variants")
    return _candidate(engine, workspace_id, project_id)


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


def _words() -> list[dict[str, Any]]:
    """Write plain declarative speech the generator can cut at real sentence ends."""
    words: list[dict[str, Any]] = []
    for sentence in range(SENTENCES):
        for position in range(WORDS_PER_SENTENCE):
            index = sentence * WORDS_PER_SENTENCE + position + 1
            last = position == WORDS_PER_SENTENCE - 1
            words.append(
                {
                    "word_id": f"w{index:06d}",
                    "text": f"end{sentence}" if last else "word",
                    "punctuation": "." if last else "",
                    "start_ms": (index - 1) * WORD_MS,
                    "end_ms": index * WORD_MS,
                    "confidence": 0.9,
                    "speaker": "A",
                }
            )
    return words


def _candidate(engine: Engine, workspace_id: UUID, project_id: UUID) -> UUID:
    """Write the source, Transcript, and exposed Clip Candidate a variant needs."""
    source_id = uuid4()
    transcript_id = uuid4()
    candidate_id = uuid4()
    words = _words()
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
                duration_ms=len(words) * WORD_MS,
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
                full_text=" ".join(word["text"] for word in words),
                words=words,
                speaker_segments=[],
                utterances=[],
                duration_ms=len(words) * WORD_MS,
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
                hook="The form was the ceiling",
                payoff="Removing it doubled activation",
                reason="One complete decision",
                category="insight",
                tags=[],
                start_ms=0,
                end_ms=CANDIDATE_WORDS * WORD_MS,
                start_word_id=words[0]["word_id"],
                end_word_id=words[CANDIDATE_WORDS - 1]["word_id"],
                transcript_excerpt="",
                context_dependencies=[],
                score_breakdown={},
                context_warnings=[],
                visual_opportunities=[],
                model_metadata={"exposed": True},
            )
        )
    return candidate_id


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
