"""Integration contracts for campaign copy derived from an approved Edit Revision.

Three promises are held here. Copy is derived from one immutable Revision and says which
one, so copy can never outlive the cut it describes. Nothing is published: a Campaign
Output is text a member reads and decides about. And a quote stays in the language it was
spoken in, however the copy around it is written.
"""

from __future__ import annotations

import copy
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
    CampaignOutput,
    ClipCandidate,
    Job,
    Project,
    ProjectStatus,
    Transcript,
    WorkspaceRole,
)
from harness import NOW, Browser, Clock, StubGoogleProvider, assert_error, build_app, sign_in

QUOTE = "Formulir pendaftaran itu yang bikin orang berhenti di tengah jalan"
WORD_MS = 1_000
WORDS = 20


@dataclass(frozen=True, slots=True)
class _Fixture:
    """One signed-in member and the approved Edit they are packaging."""

    browser: Browser
    workspace_id: UUID
    project_id: UUID
    candidate_id: UUID
    edit_id: UUID
    revision: int


def _outputs_path(fixture: _Fixture, edit_id: UUID | None = None) -> str:
    """Name the campaign-output collection of one Edit inside its Workspace."""
    target = edit_id or fixture.edit_id
    return f"/api/v1/edits/{target}/campaign-outputs?workspace_id={fixture.workspace_id}"


def _generate(fixture: _Fixture, **overrides: Any) -> Response:
    """Ask for copy derived from one exact immutable Revision."""
    body: dict[str, Any] = {
        "revision": fixture.revision,
        "platforms": ["tiktok", "youtube_shorts"],
        "languages": ["id", "en"],
    }
    body.update(overrides)
    return fixture.browser.request("POST", _outputs_path(fixture), json=body)


@pytest.mark.integration
def test_copy_is_derived_from_one_revision_and_carries_the_whole_contract(
    engine: Engine, clean_database: None
) -> None:
    """A member posts from this, so anything missing is work they finish by hand."""
    del clean_database
    fixture = _signed_in_with_edit(engine)

    response = _generate(fixture)

    assert response.status_code == 201
    outputs = response.json()["campaignOutputs"]
    assert len(outputs) == 4
    for output in outputs:
        assert output["revision"] == fixture.revision
        assert output["platform"] in {"tiktok", "youtube_shorts"}
        assert output["language"] in {"id", "en"}
        assert output["title"]
        assert output["postCopy"]
        assert output["cta"]
        assert output["hashtags"]
        assert output["thumbnailBrief"]["text"]
        assert output["thumbnailBrief"]["visualDirection"]
        assert output["modelMetadata"]["generator"]
        assert output["modelMetadata"]["deterministic"] is True


@pytest.mark.integration
def test_the_speakers_words_are_quoted_in_the_language_they_were_spoken_in(
    engine: Engine, clean_database: None
) -> None:
    """English copy about an Indonesian clip still quotes Indonesian."""
    del clean_database
    fixture = _signed_in_with_edit(engine)

    outputs = _generate(fixture).json()["campaignOutputs"]

    english = [output for output in outputs if output["language"] == "en"]
    assert english
    for output in english:
        assert QUOTE in output["postCopy"]


@pytest.mark.integration
def test_asking_twice_reaches_the_copy_a_member_already_read(
    engine: Engine, clean_database: None
) -> None:
    """Copy that changed between two readings would not be the copy anybody approved."""
    del clean_database
    fixture = _signed_in_with_edit(engine)

    first = _generate(fixture)
    second = _generate(fixture)

    assert first.status_code == second.status_code == 201
    assert first.json()["campaignOutputs"] == second.json()["campaignOutputs"]
    with Session(engine) as session:
        assert len(session.scalars(select(CampaignOutput)).all()) == 4


@pytest.mark.integration
def test_generating_copy_publishes_nothing_and_starts_no_job(
    engine: Engine, clean_database: None
) -> None:
    """A Campaign Output is not a Publication, and nothing here may make it one."""
    del clean_database
    fixture = _signed_in_with_edit(engine)

    _generate(fixture)

    with Session(engine) as session:
        assert session.scalars(select(Job)).all() == []


@pytest.mark.integration
def test_copy_is_listed_for_the_revision_it_was_derived_from(
    engine: Engine, clean_database: None
) -> None:
    """A member who saves a new cut must not see yesterday's copy as this cut's copy."""
    del clean_database
    fixture = _signed_in_with_edit(engine)
    created = _generate(fixture, platforms=["tiktok"], languages=["id"])
    _save_another_revision(fixture)

    listed = fixture.browser.get(_outputs_path(fixture))

    assert listed.status_code == 200
    assert [output["revision"] for output in listed.json()["campaignOutputs"]] == [fixture.revision]
    assert listed.json()["campaignOutputs"] == created.json()["campaignOutputs"]


@pytest.mark.integration
def test_a_revision_nobody_saved_is_refused_rather_than_approximated(
    engine: Engine, clean_database: None
) -> None:
    """Copy derived from a Revision that does not exist would describe no cut at all."""
    del clean_database
    fixture = _signed_in_with_edit(engine)

    response = _generate(fixture, revision=fixture.revision + 5)

    assert response.status_code == 404


@pytest.mark.integration
def test_another_workspace_s_edit_answers_exactly_like_one_that_never_existed(
    engine: Engine, clean_database: None
) -> None:
    """A guessed identifier must be indistinguishable from one that does not exist."""
    del clean_database
    fixture = _signed_in_with_edit(engine)
    foreign_edit_id = _foreign_edit(engine)

    missing = fixture.browser.get(_outputs_path(fixture, uuid4()))
    foreign = fixture.browser.get(_outputs_path(fixture, foreign_edit_id))

    assert missing.status_code == foreign.status_code == 404
    assert missing.json()["error"] == {
        **foreign.json()["error"],
        "requestId": missing.json()["error"]["requestId"],
    }


@pytest.mark.integration
@pytest.mark.parametrize("role", (WorkspaceRole.REVIEWER, WorkspaceRole.VIEWER))
def test_a_member_without_write_rights_may_read_copy_but_not_generate_it(
    engine: Engine, clean_database: None, role: WorkspaceRole
) -> None:
    """Reading the copy is everybody's; deriving it is an editor's decision."""
    del clean_database
    fixture = _signed_in_with_edit(engine)
    _generate(fixture)
    _set_role(engine, fixture, role)

    assert fixture.browser.get(_outputs_path(fixture)).status_code == 200
    assert_error(_generate(fixture), status_code=403, code="FORBIDDEN")


@pytest.mark.integration
def test_generating_copy_requires_csrf_proof(engine: Engine, clean_database: None) -> None:
    """Deriving copy writes rows, so it is a state-changing method."""
    del clean_database
    fixture = _signed_in_with_edit(engine)

    response = fixture.browser.request(
        "POST",
        _outputs_path(fixture),
        json={"revision": fixture.revision, "platforms": ["tiktok"], "languages": ["id"]},
        csrf_token="",
    )

    assert response.status_code in {401, 403}


@pytest.mark.integration
@pytest.mark.parametrize(
    "body",
    (
        {"platforms": [], "languages": ["id"]},
        {"platforms": ["tiktok"], "languages": []},
        {"platforms": ["threads"], "languages": ["id"]},
        {"platforms": ["tiktok"], "languages": ["fr"]},
    ),
)
def test_copy_for_nowhere_or_in_a_language_nobody_localized_is_refused(
    engine: Engine, clean_database: None, body: dict[str, Any]
) -> None:
    """A language this product has not localized would be copy nobody proofread."""
    del clean_database
    fixture = _signed_in_with_edit(engine)

    response = _generate(fixture, **body)

    assert response.status_code == 422
    with Session(engine) as session:
        assert session.scalars(select(CampaignOutput)).all() == []


@pytest.mark.integration
def test_a_clip_carrying_context_warnings_carries_them_into_the_copy(
    engine: Engine, clean_database: None
) -> None:
    """Copy and caveat are read together, or the caveat may as well not exist."""
    del clean_database
    fixture = _signed_in_with_edit(engine, context_warnings=["missing_attribution"])

    outputs = _generate(fixture, platforms=["tiktok"], languages=["id"]).json()["campaignOutputs"]

    assert [warning["type"] for warning in outputs[0]["warnings"]] == ["context_dependent"]


@pytest.mark.integration
def test_copy_is_checked_against_the_brand_kit_the_cut_declares(
    engine: Engine, clean_database: None
) -> None:
    """A member sees the sentence their brand refused before they post it."""
    del clean_database
    fixture = _signed_in_with_edit(engine)
    revision = _declare_brand_kit(fixture, forbidden="Formulir itu")

    outputs = fixture.browser.request(
        "POST",
        _outputs_path(fixture),
        json={"revision": revision, "platforms": ["tiktok"], "languages": ["id"]},
    ).json()["campaignOutputs"]

    assert [warning["type"] for warning in outputs[0]["warnings"]] == ["claim_needs_source"]
    assert outputs[0]["thumbnailBrief"]["avoid"] == ["alkohol"]


def _declare_brand_kit(fixture: _Fixture, *, forbidden: str) -> int:
    """Publish one Brand Kit and save a Revision that declares it was judged by it."""
    created = fixture.browser.request(
        "POST",
        f"/api/v1/brand-kits?workspace_id={fixture.workspace_id}",
        json={
            "name": "Kanal Utama",
            "definition": {
                "logoAssetId": None,
                "fonts": [{"family": "Montserrat", "assetId": None}],
                "colors": [
                    {"name": "Paper", "hex": "#FFFFFF"},
                    {"name": "Ink", "hex": "#000000"},
                    {"name": "Highlight", "hex": "#FFD166"},
                ],
                "captionRules": {
                    "minFontSize": 12,
                    "maxFontSize": 200,
                    "allowedAlignments": ["left", "center", "right"],
                    "reservedPlacements": [],
                },
                "visualExclusions": ["alkohol"],
                "claimRules": {
                    "requiredAttribution": None,
                    "forbiddenClaimPhrases": [forbidden],
                },
            },
        },
    )
    assert created.status_code == 201
    edit = fixture.browser.get(
        f"/api/v1/edits/{fixture.edit_id}?workspace_id={fixture.workspace_id}"
    ).json()
    composition = copy.deepcopy(edit["composition"])
    composition["brandKit"] = {
        "id": created.json()["id"],
        "version": 1,
        "logoAssetId": None,
    }
    saved = fixture.browser.request(
        "PUT",
        f"/api/v1/edits/{fixture.edit_id}?workspace_id={fixture.workspace_id}",
        json={"expectedRevision": edit["currentRevision"], "composition": composition},
    )
    assert saved.status_code == 200
    revision: int = saved.json()["currentRevision"]
    return revision


def _signed_in_with_edit(engine: Engine, **candidate_overrides: Any) -> _Fixture:
    """Sign one member in and open the Edit their copy will be derived from."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    project_id = _ready_project(engine, browser, workspace_id, key="campaign-project")
    candidate_id = _candidate(engine, workspace_id, project_id, **candidate_overrides)
    created = browser.request(
        "POST",
        f"/api/v1/projects/{project_id}/candidates/{candidate_id}/edits"
        f"?workspace_id={workspace_id}",
        json=None,
    )
    assert created.status_code == 201
    return _Fixture(
        browser=browser,
        workspace_id=workspace_id,
        project_id=project_id,
        candidate_id=candidate_id,
        edit_id=UUID(created.json()["id"]),
        revision=created.json()["currentRevision"],
    )


def _foreign_edit(engine: Engine) -> UUID:
    """Open one Edit inside a Workspace this member has no standing in."""
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    provider.identify(subject="7" * 21, email="foreign@example.com", name="Foreign Example")
    app, flow, _ = build_app(clock, provider)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    project_id = _ready_project(engine, browser, workspace_id, key="foreign-campaign")
    candidate_id = _candidate(engine, workspace_id, project_id)
    created = browser.request(
        "POST",
        f"/api/v1/projects/{project_id}/candidates/{candidate_id}/edits"
        f"?workspace_id={workspace_id}",
        json=None,
    )
    assert created.status_code == 201
    return UUID(created.json()["id"])


def _save_another_revision(fixture: _Fixture) -> None:
    """Save one further Revision, which the copy above was not derived from."""
    edit = fixture.browser.get(
        f"/api/v1/edits/{fixture.edit_id}?workspace_id={fixture.workspace_id}"
    ).json()
    composition = copy.deepcopy(edit["composition"])
    composition["audio"]["gainDb"] = -3.0
    saved = fixture.browser.request(
        "PUT",
        f"/api/v1/edits/{fixture.edit_id}?workspace_id={fixture.workspace_id}",
        json={"expectedRevision": edit["currentRevision"], "composition": composition},
    )
    assert saved.status_code == 200


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


def _candidate(engine: Engine, workspace_id: UUID, project_id: UUID, **overrides: Any) -> UUID:
    """Write the source, Transcript, and exposed Clip Candidate the copy is derived from."""
    source_id = uuid4()
    transcript_id = uuid4()
    candidate_id = uuid4()
    words = [
        {
            "word_id": f"w{index:06d}",
            "text": text_value,
            "punctuation": "",
            "start_ms": index * WORD_MS,
            "end_ms": (index + 1) * WORD_MS,
            "confidence": 0.99,
            "speaker": "SPEAKER_00",
        }
        for index, text_value in enumerate((QUOTE + " ").split() * 2)
    ]
    values: dict[str, Any] = {
        "hook": "Formulir itu batasnya",
        "payoff": "Menghapusnya melipatgandakan aktivasi",
        "reason": "Satu keputusan lengkap dari awal sampai akhir",
        "category": "insight",
        "tags": ["produk", "aktivasi"],
        "context_warnings": [],
    }
    values.update(overrides)
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
                model="universal-2",
                language="id",
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
                start_ms=0,
                end_ms=WORDS * WORD_MS,
                start_word_id=words[0]["word_id"],
                end_word_id=words[WORDS - 1]["word_id"],
                transcript_excerpt=QUOTE,
                context_dependencies=[],
                score_breakdown={},
                visual_opportunities=[],
                model_metadata={"exposed": True},
                **values,
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
