"""Integration contracts for Edits, immutable Edit Revisions, and their endpoints.

An Edit is the identity of an editable clip; its history is a sequence of immutable
Revisions. These tests drive that history through the public API, because the rules
that matter — optimistic concurrency, tenant scoping, and asset authorization — are
only true if every one of them holds at the HTTP boundary.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from threading import Barrier, Thread
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import Response
from sqlalchemy import Engine, text, update

from clipah.models import (
    Asset,
    AssetKind,
    AssetSourceType,
    ClipCandidate,
    Project,
    ProjectStatus,
    Transcript,
    WorkspaceRole,
)
from harness import (
    NOW,
    Browser,
    Clock,
    RecordingFlow,
    StubGoogleProvider,
    assert_error,
    build_app,
    sign_in,
)

CURRENT_REVISION_HEADER = "X-Clipah-Current-Revision"


@dataclass(frozen=True, slots=True)
class Stage:
    """One signed-in member, the application they drive, and the data they review."""

    browser: Browser
    flow: RecordingFlow
    provider: StubGoogleProvider
    fixture: EditFixture

    def peer(self) -> Browser:
        """Open a second client carrying the same Session, as a second tab would."""
        other = Browser(self.browser.app, origin=self.browser.origin)
        other.cookies = self.browser.cookies
        return other


@dataclass(frozen=True, slots=True)
class EditFixture:
    """One ready Project, its analysed candidate, and the assets an Edit may use."""

    workspace_id: UUID
    project_id: UUID
    candidate_id: UUID
    hidden_candidate_id: UUID
    source_asset_id: UUID
    broll_asset_id: UUID


@pytest.mark.integration
def test_an_edit_created_from_a_candidate_starts_at_revision_one(engine: Engine) -> None:
    """The first Revision must describe the reviewed moment without any further work."""
    stage = _reviewed_project(engine)
    browser, fixture = stage.browser, stage.fixture

    created = _create_edit(browser, fixture)

    assert created.status_code == 201
    body = created.json()
    assert body["currentRevision"] == 1
    assert body["candidateId"] == str(fixture.candidate_id)
    assert body["projectId"] == str(fixture.project_id)
    composition = body["composition"]
    assert composition["schemaVersion"] == 1
    assert composition["sourceAssetId"] == str(fixture.source_asset_id)
    assert composition["durationMs"] == 30_000
    assert composition["sourceRange"] == {"inMs": 5_000, "outMs": 35_000}
    assert composition["canvas"] == {"width": 1080, "height": 1920, "background": "#000000"}
    assert [word["text"] for word in composition["captions"]["words"]] == ["Kedua", "moment"]
    assert composition["captions"]["words"][0]["startMs"] == 0
    assert len(body["compositionHash"]) == 64


@pytest.mark.integration
def test_creating_an_edit_for_the_same_candidate_twice_returns_the_first_edit(
    engine: Engine,
) -> None:
    """A second click must reach the work already in progress, never a rival history."""
    stage = _reviewed_project(engine)
    browser, fixture = stage.browser, stage.fixture

    first = _create_edit(browser, fixture)
    second = _create_edit(browser, fixture)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["currentRevision"] == 1


@pytest.mark.integration
def test_two_clients_creating_an_edit_at_once_still_produce_one_edit(engine: Engine) -> None:
    """Two tabs opening the editor must not fork one clip into two histories."""
    stage = _reviewed_project(engine)
    browser, fixture = stage.browser, stage.fixture

    responses = _race(lambda client: _create_edit(client, fixture), browser, stage.peer())

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 201]
    assert len({response.json()["id"] for response in responses}) == 1


@pytest.mark.integration
def test_saving_a_revision_appends_immutable_history(engine: Engine) -> None:
    """Every save must add a Revision rather than overwrite the one being reviewed."""
    stage = _reviewed_project(engine)
    browser, fixture = stage.browser, stage.fixture
    created = _create_edit(browser, fixture).json()
    document = _edited(created["composition"], gain_db=-3.0)

    saved = _save(browser, fixture, created["id"], expected_revision=1, composition=document)

    assert saved.status_code == 200
    assert saved.json()["currentRevision"] == 2
    assert saved.json()["compositionHash"] != created["compositionHash"]

    history = browser.get(_path(f"/edits/{created['id']}/revisions", fixture))

    assert history.status_code == 200
    revisions = history.json()["revisions"]
    assert [revision["revision"] for revision in revisions] == [2, 1]
    assert revisions[1]["compositionHash"] == created["compositionHash"]
    assert revisions[0]["compositionHash"] == saved.json()["compositionHash"]
    assert all(revision["createdBy"] for revision in revisions)


@pytest.mark.integration
def test_reading_an_edit_returns_the_current_revision_and_its_composition(
    engine: Engine,
) -> None:
    """The editor loads the newest Revision; an older one would silently lose work."""
    stage = _reviewed_project(engine)
    browser, fixture = stage.browser, stage.fixture
    created = _create_edit(browser, fixture).json()
    document = _edited(created["composition"], gain_db=-6.0)
    _save(browser, fixture, created["id"], expected_revision=1, composition=document)

    read = browser.get(_path(f"/edits/{created['id']}", fixture))

    assert read.status_code == 200
    assert read.json()["currentRevision"] == 2
    assert read.json()["composition"]["audio"]["gainDb"] == -6.0


@pytest.mark.integration
def test_a_stale_expected_revision_is_refused_with_the_current_revision(engine: Engine) -> None:
    """A member editing an outdated document must be told exactly what to reconcile against."""
    stage = _reviewed_project(engine)
    browser, fixture = stage.browser, stage.fixture
    created = _create_edit(browser, fixture).json()
    _save(
        browser,
        fixture,
        created["id"],
        expected_revision=1,
        composition=_edited(created["composition"], gain_db=-3.0),
    )

    stale = _save(
        browser,
        fixture,
        created["id"],
        expected_revision=1,
        composition=_edited(created["composition"], gain_db=-9.0),
    )

    assert_error(stale, status_code=409, code="EDIT_REVISION_CONFLICT")
    assert stale.headers[CURRENT_REVISION_HEADER] == "2"

    unchanged = browser.get(_path(f"/edits/{created['id']}", fixture))
    assert unchanged.json()["composition"]["audio"]["gainDb"] == -3.0


@pytest.mark.integration
def test_two_clients_saving_revision_two_leave_exactly_one_winner(engine: Engine) -> None:
    """Concurrency is decided in Postgres, so two saves can never both become Revision 2."""
    stage = _reviewed_project(engine)
    browser, fixture = stage.browser, stage.fixture
    created = _create_edit(browser, fixture).json()
    first_document = _edited(created["composition"], gain_db=-3.0)
    second_document = _edited(created["composition"], gain_db=-9.0)
    documents = iter((first_document, second_document))

    responses = _race(
        lambda client: _save(
            client, fixture, created["id"], expected_revision=1, composition=next(documents)
        ),
        browser,
        stage.peer(),
    )

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "EDIT_REVISION_CONFLICT"
    assert conflict.headers[CURRENT_REVISION_HEADER] == "2"

    history = browser.get(_path(f"/edits/{created['id']}/revisions", fixture))
    assert [revision["revision"] for revision in history.json()["revisions"]] == [2, 1]


@pytest.mark.integration
def test_saving_an_unchanged_composition_does_not_create_a_revision(engine: Engine) -> None:
    """Autosave repeats itself constantly; identical work must not inflate the history."""
    stage = _reviewed_project(engine)
    browser, fixture = stage.browser, stage.fixture
    created = _create_edit(browser, fixture).json()

    resaved = _save(
        browser, fixture, created["id"], expected_revision=1, composition=created["composition"]
    )

    assert resaved.status_code == 200
    assert resaved.json()["currentRevision"] == 1
    history = browser.get(_path(f"/edits/{created['id']}/revisions", fixture))
    assert [revision["revision"] for revision in history.json()["revisions"]] == [1]


@pytest.mark.integration
def test_a_composition_that_is_not_valid_version_one_is_refused(engine: Engine) -> None:
    """An invalid document must be refused at save, not discovered during a render."""
    stage = _reviewed_project(engine)
    browser, fixture = stage.browser, stage.fixture
    created = _create_edit(browser, fixture).json()
    document = copy.deepcopy(created["composition"])
    document["tracks"][0]["items"][0]["sourceOutMs"] = document["tracks"][0]["items"][0][
        "sourceInMs"
    ]

    refused = _save(browser, fixture, created["id"], expected_revision=1, composition=document)

    assert_error(refused, status_code=422, code="COMPOSITION_INVALID")


@pytest.mark.integration
def test_a_composition_naming_an_asset_the_project_does_not_own_is_refused(
    engine: Engine,
) -> None:
    """A composition is a use of media; unowned media may never enter one."""
    stage = _reviewed_project(engine)
    browser, fixture = stage.browser, stage.fixture
    created = _create_edit(browser, fixture).json()
    document = copy.deepcopy(created["composition"])
    document["overlays"].append(_overlay(str(uuid4())))

    refused = _save(browser, fixture, created["id"], expected_revision=1, composition=document)

    assert_error(refused, status_code=422, code="COMPOSITION_ASSET_FORBIDDEN")


@pytest.mark.integration
def test_a_composition_may_use_an_asset_the_project_does_own(engine: Engine) -> None:
    """Authorization must accept the Project's own media, or B-roll could never land."""
    stage = _reviewed_project(engine)
    browser, fixture = stage.browser, stage.fixture
    created = _create_edit(browser, fixture).json()
    document = copy.deepcopy(created["composition"])
    document["overlays"].append(_overlay(str(fixture.broll_asset_id)))

    saved = _save(browser, fixture, created["id"], expected_revision=1, composition=document)

    assert saved.status_code == 200
    assert saved.json()["composition"]["overlays"][0]["assetId"] == str(fixture.broll_asset_id)


@pytest.mark.integration
def test_a_save_without_csrf_proof_is_refused(engine: Engine) -> None:
    """Composition writes are state-changing and cookie-authenticated."""
    stage = _reviewed_project(engine)
    browser, fixture = stage.browser, stage.fixture
    created = _create_edit(browser, fixture).json()

    refused = browser.request(
        "PUT",
        _path(f"/edits/{created['id']}", fixture),
        csrf_token="",
        json={"expectedRevision": 1, "composition": created["composition"]},
    )

    assert_error(refused, status_code=403, code="CSRF_FAILED")


@pytest.mark.integration
def test_a_reviewer_may_read_an_edit_but_not_save_one(engine: Engine) -> None:
    """Review authority is not editing authority, whatever the browser chooses to show."""
    stage = _reviewed_project(engine)
    browser, fixture = stage.browser, stage.fixture
    created = _create_edit(browser, fixture).json()
    _set_role(engine, browser, fixture, WorkspaceRole.REVIEWER)

    read = browser.get(_path(f"/edits/{created['id']}", fixture))
    refused = _save(
        browser,
        fixture,
        created["id"],
        expected_revision=1,
        composition=_edited(created["composition"], gain_db=-3.0),
    )

    assert read.status_code == 200
    assert_error(refused, status_code=403, code="FORBIDDEN")


@pytest.mark.integration
def test_another_workspace_edit_is_indistinguishable_from_one_that_never_existed(
    engine: Engine,
) -> None:
    """A guessed Edit identifier must answer exactly like an unissued one."""
    stage = _reviewed_project(engine)
    browser, fixture = stage.browser, stage.fixture
    created = _create_edit(browser, fixture).json()
    stage.provider.identify(subject="edit-stranger", email="stranger@example.com", name="Stranger")
    stranger = Browser(browser.app)
    sign_in(stranger, stage.flow)
    stranger_workspace = UUID(stranger.get("/api/v1/workspaces").json()["workspaces"][0]["id"])

    guessed = stranger.get(
        f"/api/v1/edits/{created['id']}?workspace_id={stranger_workspace}",
    )
    missing = stranger.get(f"/api/v1/edits/{uuid4()}?workspace_id={stranger_workspace}")

    assert_error(guessed, status_code=404, code="NOT_FOUND")
    assert_error(missing, status_code=404, code="NOT_FOUND")
    assert guessed.json()["error"]["message"] == missing.json()["error"]["message"]


@pytest.mark.integration
def test_an_edit_cannot_be_created_from_a_candidate_the_member_may_not_review(
    engine: Engine,
) -> None:
    """The hidden reranking tail is not review evidence, so it cannot become an Edit."""
    stage = _reviewed_project(engine)
    browser, fixture = stage.browser, stage.fixture

    hidden = browser.request(
        "POST",
        _path(
            f"/projects/{fixture.project_id}/candidates/{fixture.hidden_candidate_id}/edits",
            fixture,
        ),
        json=None,
    )
    missing = browser.request(
        "POST",
        _path(f"/projects/{fixture.project_id}/candidates/{uuid4()}/edits", fixture),
        json=None,
    )

    assert_error(hidden, status_code=404, code="NOT_FOUND")
    assert_error(missing, status_code=404, code="NOT_FOUND")
    assert hidden.json()["error"]["message"] == missing.json()["error"]["message"]


@pytest.mark.integration
def test_revision_history_is_append_only_at_the_database_boundary(engine: Engine) -> None:
    """Immutability is a grant, not a convention: the API role may not rewrite history."""
    stage = _reviewed_project(engine)
    browser, fixture = stage.browser, stage.fixture
    created = _create_edit(browser, fixture).json()

    with engine.begin() as connection:
        privileges = connection.execute(
            text(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE table_name = 'clip_edit_revisions' AND grantee = 'clipah_api'"
            )
        ).scalars()

    assert sorted(privileges) == ["INSERT", "SELECT"]
    assert created["currentRevision"] == 1


@pytest.mark.unit
def test_edit_openapi_declares_strict_request_and_response_schemas() -> None:
    """Generated clients need concrete Edit shapes rather than arbitrary dictionaries."""
    clock = Clock(NOW)
    app, _, _ = build_app(clock, StubGoogleProvider(clock))
    paths = app.openapi()["paths"]

    detail = paths["/api/v1/edits/{edit_id}"]["get"]["responses"]["200"]
    save = paths["/api/v1/edits/{edit_id}"]["put"]
    history = paths["/api/v1/edits/{edit_id}/revisions"]["get"]["responses"]["200"]

    assert detail["content"]["application/json"]["schema"]["$ref"].endswith("/EditResponse")
    assert save["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/SaveRevisionRequest"
    )
    assert history["content"]["application/json"]["schema"]["$ref"].endswith(
        "/RevisionHistoryResponse"
    )


def _path(suffix: str, fixture: EditFixture) -> str:
    """Build one Edit URL carrying the Workspace selection the caller is authorized for."""
    separator = "&" if "?" in suffix else "?"
    return f"/api/v1{suffix}{separator}workspace_id={fixture.workspace_id}"


def _create_edit(browser: Browser, fixture: EditFixture) -> Response:
    """Create, or reach, the one Edit belonging to the reviewed candidate."""
    return browser.request(
        "POST",
        _path(
            f"/projects/{fixture.project_id}/candidates/{fixture.candidate_id}/edits",
            fixture,
        ),
        json=None,
    )


def _save(
    browser: Browser,
    fixture: EditFixture,
    edit_id: str,
    *,
    expected_revision: int,
    composition: dict[str, Any],
) -> Response:
    """Save one composition against the Revision the caller believes is current."""
    return browser.request(
        "PUT",
        _path(f"/edits/{edit_id}", fixture),
        json={"expectedRevision": expected_revision, "composition": composition},
    )


def _edited(composition: dict[str, Any], *, gain_db: float) -> dict[str, Any]:
    """Return the same composition with one audible change a member could have made."""
    document = copy.deepcopy(composition)
    document["audio"]["gainDb"] = gain_db
    return document


def _overlay(asset_id: str) -> dict[str, Any]:
    """Build one B-roll overlay placed inside the clip."""
    return {
        "id": "broll-1",
        "type": "video",
        "assetId": asset_id,
        "timelineStartMs": 1_000,
        "timelineEndMs": 4_000,
        "sourceInMs": 0,
        "sourceOutMs": 3_000,
        "placement": "cover",
        "opacity": 1.0,
        "blendMode": "normal",
        "motion": "none",
        "preserveDialogueAudio": True,
        "origin": {"type": "userAsset", "suggestionId": None, "provenanceId": None},
        "keyframes": [],
    }


def _race(
    action: Callable[[Browser], Response], first: Browser, second: Browser
) -> tuple[Response, Response]:
    """Run one action from two browsers that start at the same instant."""
    barrier = Barrier(2)
    responses: list[Response] = []

    def run(client: Browser) -> None:
        barrier.wait()
        responses.append(action(client))

    threads = [Thread(target=run, args=(first,)), Thread(target=run, args=(second,))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return responses[0], responses[1]


def _set_role(engine: Engine, browser: Browser, fixture: EditFixture, role: WorkspaceRole) -> None:
    """Move the signed-in member to another role inside their own Workspace."""
    user_id = browser.get("/api/v1/me").json()["id"]
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workspace_memberships SET role = :role "
                "WHERE workspace_id = :workspace_id AND user_id = :user_id"
            ),
            {"role": role.value, "workspace_id": fixture.workspace_id, "user_id": user_id},
        )


def _reviewed_project(engine: Engine) -> Stage:
    """Sign in and stage a ready Project whose analysis produced reviewable candidates."""
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    app, flow, _ = build_app(clock, provider)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    created = browser.request(
        "POST",
        f"/api/v1/projects?workspace_id={workspace_id}",
        headers={"Idempotency-Key": "edit-project"},
        json={"name": "Edit project", "sourceKind": "upload"},
    )
    assert created.status_code == 201
    project_id = UUID(created.json()["id"])
    source_asset_id = uuid4()
    broll_asset_id = uuid4()
    transcript_id = uuid4()
    candidate_id = uuid4()
    hidden_candidate_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(status=ProjectStatus.READY, updated_at=NOW)
        )
        connection.execute(
            Asset.__table__.insert(),
            [
                _asset_values(
                    asset_id=source_asset_id,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    kind=AssetKind.SOURCE,
                    name="original",
                ),
                _asset_values(
                    asset_id=broll_asset_id,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    kind=AssetKind.RENDER,
                    name="broll",
                ),
            ],
        )
        connection.execute(
            Transcript.__table__.insert().values(
                id=transcript_id,
                workspace_id=workspace_id,
                project_id=project_id,
                asset_id=source_asset_id,
                provider="assemblyai",
                provider_version="1.0.0",
                model="universal-2",
                language="id",
                full_text="Pertama Kedua moment Terakhir",
                words=[
                    _word("w000001", "Pertama", 1_000, 2_000),
                    _word("w000002", "Kedua", 5_000, 6_000),
                    _word("w000003", "moment", 6_000, 7_000),
                    _word("w000004", "Terakhir", 36_000, 37_000),
                ],
                speaker_segments=[],
                utterances=[],
                duration_ms=60_000,
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
                    candidate_id=hidden_candidate_id,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    transcript_id=transcript_id,
                    rank=2,
                    exposed=False,
                ),
            ],
        )
    return Stage(
        browser=browser,
        flow=flow,
        provider=provider,
        fixture=EditFixture(
            workspace_id=workspace_id,
            project_id=project_id,
            candidate_id=candidate_id,
            hidden_candidate_id=hidden_candidate_id,
            source_asset_id=source_asset_id,
            broll_asset_id=broll_asset_id,
        ),
    )


def _asset_values(
    *, asset_id: UUID, workspace_id: UUID, project_id: UUID, kind: AssetKind, name: str
) -> dict[str, Any]:
    """Describe one durable Asset row belonging to the reviewed Project."""
    return {
        "id": asset_id,
        "workspace_id": workspace_id,
        "project_id": project_id,
        "kind": kind,
        "source_type": AssetSourceType.USER_UPLOAD,
        "storage_key": f"workspaces/{workspace_id}/projects/{project_id}/{name}",
        "content_type": "video/mp4",
        "size_bytes": 1_024,
        "duration_ms": 60_000,
        "width": 1920,
        "height": 1080,
        "sha256": bytes([len(name)]) * 32,
    }


def _word(word_id: str, text_value: str, start_ms: int, end_ms: int) -> dict[str, Any]:
    """Describe one persisted transcript word in its canonical stored shape."""
    return {
        "word_id": word_id,
        "text": text_value,
        "punctuation": "",
        "start_ms": start_ms,
        "end_ms": end_ms,
        "confidence": 0.98,
        "speaker": "SPEAKER_00",
    }


def _candidate_values(
    *,
    candidate_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    transcript_id: UUID,
    rank: int,
    exposed: bool,
) -> dict[str, Any]:
    """Describe one durable Clip Candidate the review surface may or may not expose."""
    return {
        "id": candidate_id,
        "workspace_id": workspace_id,
        "project_id": project_id,
        "transcript_id": transcript_id,
        "rank": rank,
        "score": 0.9,
        "hook": "The surprising opening",
        "payoff": "The useful resolution",
        "reason": "A complete and useful moment",
        "category": "insight",
        "tags": ["creator"],
        "start_ms": 5_000,
        "end_ms": 35_000,
        "start_word_id": "w000002",
        "end_word_id": "w000003",
        "transcript_excerpt": "Kedua moment",
        "context_dependencies": [],
        "score_breakdown": {
            "hook": 0.9,
            "payoff": 0.9,
            "narrative_completeness": 0.9,
            "context_safety": 0.9,
            "platform_fit": 0.9,
            "transcript_confidence": 0.9,
            "visual_opportunity": 0.9,
        },
        "context_warnings": [],
        "visual_opportunities": [],
        "model_metadata": {"exposed": exposed},
        "created_at": NOW,
    }


@pytest.mark.integration
def test_saving_or_reading_an_unknown_edit_answers_like_a_missing_one(engine: Engine) -> None:
    """Every Edit route must hide an unissued identifier behind the same absence."""
    stage = _reviewed_project(engine)
    browser, fixture = stage.browser, stage.fixture
    created = _create_edit(browser, fixture).json()
    unknown = uuid4()

    saved = _save(
        browser,
        fixture,
        str(unknown),
        expected_revision=1,
        composition=created["composition"],
    )
    history = browser.get(_path(f"/edits/{unknown}/revisions", fixture))

    assert_error(saved, status_code=404, code="NOT_FOUND")
    assert_error(history, status_code=404, code="NOT_FOUND")


@pytest.mark.integration
def test_a_candidate_whose_source_asset_is_gone_cannot_become_an_edit(engine: Engine) -> None:
    """An Edit describes media; without the source Asset there is nothing to describe."""
    stage = _reviewed_project(engine)
    browser, fixture = stage.browser, stage.fixture
    elsewhere = browser.request(
        "POST",
        f"/api/v1/projects?workspace_id={fixture.workspace_id}",
        headers={"Idempotency-Key": "edit-elsewhere"},
        json={"name": "Elsewhere", "sourceKind": "upload"},
    )
    assert elsewhere.status_code == 201
    foreign_asset_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            Asset.__table__.insert(),
            [
                _asset_values(
                    asset_id=foreign_asset_id,
                    workspace_id=fixture.workspace_id,
                    project_id=UUID(elsewhere.json()["id"]),
                    kind=AssetKind.SOURCE,
                    name="elsewhere",
                )
            ],
        )
        connection.execute(
            update(Transcript)
            .where(Transcript.project_id == fixture.project_id)
            .values(asset_id=foreign_asset_id)
        )

    refused = _create_edit(browser, fixture)

    assert_error(refused, status_code=404, code="NOT_FOUND")


@pytest.mark.integration
@pytest.mark.parametrize(
    "words",
    [
        pytest.param({}, id="not-a-list"),
        pytest.param([{"text": "unreadable"}], id="unreadable-word"),
    ],
)
def test_an_unreadable_transcript_still_produces_an_editable_clip(
    engine: Engine, words: object
) -> None:
    """Captions are recoverable evidence; a damaged word list must not block editing."""
    stage = _reviewed_project(engine)
    browser, fixture = stage.browser, stage.fixture
    with engine.begin() as connection:
        connection.execute(
            update(Transcript)
            .where(Transcript.project_id == fixture.project_id)
            .values(words=words)
        )

    created = _create_edit(browser, fixture)

    assert created.status_code == 201
    assert created.json()["composition"]["captions"]["words"] == []
