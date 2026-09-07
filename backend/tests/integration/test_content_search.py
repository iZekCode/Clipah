"""Integration contracts for the searchable creator content library.

Three promises are held here. A member finds their own past work by the words that were
actually spoken, in Indonesian or in English, and never finds anybody else's. What comes
back is evidence a person can read and act on — a fragment, a deep link, a timecode — and
never a storage key or a provider payload. And the index is derived: it can be rebuilt
from the durable domain rows at any time without changing one of them.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import Response
from sqlalchemy import Engine, delete, select, text, update
from sqlalchemy.orm import Session

from clipah.db import RuntimeRole, session_scope
from clipah.models import (
    Asset,
    AssetKind,
    AssetSourceType,
    ClipCandidate,
    ClipEditRevision,
    Project,
    ProjectStatus,
    RenderArtifact,
    SearchDocument,
    Transcript,
    WorkspaceRole,
)
from clipah.search.indexer import index_project, rebuild_workspace
from clipah.search.indexer import main as indexer_main
from harness import NOW, Browser, Clock, StubGoogleProvider, assert_error, build_app, sign_in
from support import API_RUNTIME_DATABASE_URL, runtime_settings

WORD_MS = 1_000

INDONESIAN_SENTENCE = (
    "Formulir pendaftaran itu yang bikin orang berhenti di tengah jalan sebelum aktivasi"
)
ENGLISH_SENTENCE = (
    "The onboarding form is what makes people stop halfway before the activation runs"
)


@dataclass(frozen=True, slots=True)
class _Fixture:
    """One signed-in member and the Workspace whose library they are searching."""

    browser: Browser
    workspace_id: UUID
    user_id: UUID


def _search(fixture: _Fixture, query: str, **filters: Any) -> Response:
    """Search one Workspace's library the way the dashboard's global search does."""
    params = "".join(f"&{name}={value}" for name, value in filters.items())
    return fixture.browser.get(
        f"/api/v1/search?workspace_id={fixture.workspace_id}&q={query}{params}"
    )


def _titles(response: Response) -> list[str]:
    """Name the results in the order the backend ranked them."""
    return [result["title"] for result in response.json()["results"]]


@pytest.mark.integration
def test_an_indonesian_word_finds_the_moment_it_was_spoken_in(
    engine: Engine, clean_database: None
) -> None:
    """A creator recalls the word, not the project it happened to be filed under."""
    del clean_database
    fixture = _signed_in(engine)
    _indexed_project(engine, fixture, name="Rapat Produk", language="id")

    response = _search(fixture, "daftar")

    assert response.status_code == 200
    assert any(result["type"] == "transcript" for result in response.json()["results"])


@pytest.mark.integration
def test_an_english_word_finds_the_moment_in_its_own_stem(
    engine: Engine, clean_database: None
) -> None:
    """Search that only matched exact forms would miss most of what people type."""
    del clean_database
    fixture = _signed_in(engine)
    _indexed_project(engine, fixture, name="Product Sync", language="en")

    response = _search(fixture, "running")

    assert response.status_code == 200
    assert any(result["type"] == "transcript" for result in response.json()["results"])


@pytest.mark.integration
def test_a_quoted_phrase_matches_only_where_the_words_are_adjacent(
    engine: Engine, clean_database: None
) -> None:
    """A phrase in quotes is a promise about order, and a search that ignores it lies."""
    del clean_database
    fixture = _signed_in(engine)
    _indexed_project(engine, fixture, name="Product Sync", language="en")

    adjacent = _search(fixture, "%22onboarding%20form%22")
    scattered = _search(fixture, "%22form%20onboarding%22")

    assert adjacent.status_code == scattered.status_code == 200
    assert adjacent.json()["results"] != []
    assert scattered.json()["results"] == []


@pytest.mark.integration
def test_a_project_is_found_by_its_normalized_name(engine: Engine, clean_database: None) -> None:
    """Nobody reproduces the punctuation and accents of a name they typed months ago."""
    del clean_database
    fixture = _signed_in(engine)
    _indexed_project(engine, fixture, name="Café  Kopi — Sesi #2", language="id", key="cafe")

    response = _search(fixture, "cafe%20kopi")

    assert response.status_code == 200
    # The indexed title is the name with its runs of whitespace collapsed, which is what
    # a member sees on every other screen too.
    assert "Café Kopi — Sesi #2" in _titles(response)


@pytest.mark.integration
def test_a_mistyped_project_name_still_finds_the_project(
    engine: Engine, clean_database: None
) -> None:
    """A transposed letter is the common case, not an exceptional one."""
    del clean_database
    fixture = _signed_in(engine)
    _indexed_project(engine, fixture, name="Aktivasi Pengguna", language="id")

    response = _search(fixture, "aktivsi")

    assert response.status_code == 200
    assert "Aktivasi Pengguna" in _titles(response)


@pytest.mark.integration
def test_results_carry_readable_evidence_and_never_storage_or_provider_detail(
    engine: Engine, clean_database: None
) -> None:
    """A result a member cannot act on is a result that wasted their attention."""
    del clean_database
    fixture = _signed_in(engine)
    project_id = _indexed_project(engine, fixture, name="Rapat Produk", language="id")

    results = _search(fixture, "berhenti").json()["results"]

    transcript = next(result for result in results if result["type"] == "transcript")
    assert transcript["projectId"] == str(project_id)
    assert transcript["projectName"] == "Rapat Produk"
    assert transcript["deepLink"] == f"/dashboard/projects/{project_id}?t={transcript['startMs']}"
    assert transcript["endMs"] > transcript["startMs"]
    assert transcript["speaker"] == "SPEAKER_00"
    assert transcript["language"] == "id"
    assert any(fragment["highlighted"] for fragment in transcript["fragments"])
    assert "berhenti" in "".join(fragment["text"] for fragment in transcript["fragments"])
    serialized = str(results)
    assert "storage" not in serialized.lower()
    assert "raw.json" not in serialized
    assert "assemblyai" not in serialized


@pytest.mark.integration
def test_a_clip_result_deep_links_to_the_clip_and_a_campaign_output_to_its_copy(
    engine: Engine, clean_database: None
) -> None:
    """Every result type must open the screen that lets a member finish the thought."""
    del clean_database
    fixture = _signed_in(engine)
    seeded = _indexed_project(engine, fixture, name="Rapat Produk", language="id")
    candidate_id = _candidate_id(engine, seeded)

    results = _search(fixture, "formulir", type="clip").json()["results"]

    assert [result["type"] for result in results] == ["clip"]
    assert results[0]["deepLink"] == f"/dashboard/clips/{candidate_id}"
    assert results[0]["startMs"] is not None


@pytest.mark.integration
def test_untrusted_text_is_returned_as_text_and_never_as_markup(
    engine: Engine, clean_database: None
) -> None:
    """Every indexed word was written by a language model or a stranger's microphone."""
    del clean_database
    fixture = _signed_in(engine)
    _indexed_project(
        engine, fixture, name="<img src=x onerror=alert(1)> Rapat", language="id", key="markup"
    )

    results = _search(fixture, "rapat").json()["results"]

    project = next(result for result in results if result["type"] == "project")
    assert project["title"] == "<img src=x onerror=alert(1)> Rapat"
    for fragment in project["fragments"]:
        assert "<mark>" not in fragment["text"]
        assert "<b>" not in fragment["text"]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("query", "filter_name", "filter_value", "expected_types"),
    (
        ("rapat", "type", "project", {"project"}),
        ("formulir", "type", "transcript", {"transcript"}),
        ("formulir", "speaker", "SPEAKER_00", {"transcript"}),
        ("formulir", "topic", "produk", {"clip"}),
        ("formulir", "language", "id", {"transcript", "clip"}),
        ("formulir", "exportState", "not_exported", {"transcript", "clip"}),
    ),
)
def test_every_filter_narrows_the_library_to_what_was_asked_for(
    engine: Engine,
    clean_database: None,
    query: str,
    filter_name: str,
    filter_value: str,
    expected_types: set[str],
) -> None:
    """A filter that quietly widens its own result set is worse than no filter."""
    del clean_database
    fixture = _signed_in(engine)
    _indexed_project(engine, fixture, name="Rapat Produk", language="id")

    results = _search(fixture, query, **{filter_name: filter_value}).json()["results"]

    assert results != []
    assert {result["type"] for result in results} <= expected_types


@pytest.mark.integration
def test_a_project_filter_excludes_every_other_project(
    engine: Engine, clean_database: None
) -> None:
    """Searching inside one Project is how a member reads one conversation again."""
    del clean_database
    fixture = _signed_in(engine)
    wanted = _indexed_project(engine, fixture, name="Rapat Produk", language="id")
    _indexed_project(engine, fixture, name="Rapat Lainnya", language="id", key="other")

    results = _search(fixture, "formulir", projectId=str(wanted)).json()["results"]

    assert results != []
    assert {result["projectId"] for result in results} == {str(wanted)}


@pytest.mark.integration
def test_a_date_window_excludes_work_from_outside_it(engine: Engine, clean_database: None) -> None:
    """ "Last week" is the filter creators actually reach for."""
    del clean_database
    fixture = _signed_in(engine)
    _indexed_project(engine, fixture, name="Rapat Produk", language="id")
    _age_documents(engine, days=90)
    recent = _indexed_project(engine, fixture, name="Rapat Baru", language="id", key="recent")
    boundary = (NOW - timedelta(days=7)).isoformat().replace("+", "%2B")

    after = _search(fixture, "formulir", createdAfter=boundary).json()["results"]
    before = _search(fixture, "formulir", createdBefore=boundary).json()["results"]

    assert after != []
    assert {result["projectId"] for result in after} == {str(recent)}
    assert before != []
    assert str(recent) not in {result["projectId"] for result in before}


@pytest.mark.integration
def test_pagination_walks_the_whole_ranking_without_repeating_or_losing_a_result(
    engine: Engine, clean_database: None
) -> None:
    """A cursor that drifts turns one library into two incomplete ones."""
    del clean_database
    fixture = _signed_in(engine)
    _indexed_project(engine, fixture, name="Rapat Produk", language="id")

    whole = _search(fixture, "formulir", limit=50).json()["results"]
    walked: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(len(whole)):
        page = _search(
            fixture, "formulir", limit=1, **({} if cursor is None else {"cursor": cursor})
        ).json()
        walked.extend(page["results"])
        cursor = page["nextCursor"]
        if cursor is None:
            break

    assert cursor is None
    assert [result["id"] for result in walked] == [result["id"] for result in whole]


@pytest.mark.integration
def test_an_unreadable_cursor_is_refused_rather_than_silently_restarted(
    engine: Engine, clean_database: None
) -> None:
    """Restarting from the top would hand a member the same page forever."""
    del clean_database
    fixture = _signed_in(engine)
    _indexed_project(engine, fixture, name="Rapat Produk", language="id")

    response = _search(fixture, "formulir", cursor="not-a-cursor")

    assert response.status_code == 422


@pytest.mark.integration
def test_a_soft_deleted_project_leaves_the_library_with_everything_it_held(
    engine: Engine, clean_database: None
) -> None:
    """Deleted work that keeps surfacing in search has not been deleted at all."""
    del clean_database
    fixture = _signed_in(engine)
    project_id = _indexed_project(engine, fixture, name="Rapat Produk", language="id")

    deleted = fixture.browser.request(
        "DELETE", f"/api/v1/projects/{project_id}?workspace_id={fixture.workspace_id}"
    )

    assert deleted.status_code in {200, 204}
    assert _search(fixture, "formulir").json()["results"] == []


@pytest.mark.integration
def test_a_restored_project_returns_to_the_library(engine: Engine, clean_database: None) -> None:
    """Recovery that leaves the work unfindable is only half a recovery."""
    del clean_database
    fixture = _signed_in(engine)
    project_id = _indexed_project(engine, fixture, name="Rapat Produk", language="id")
    fixture.browser.request(
        "DELETE", f"/api/v1/projects/{project_id}?workspace_id={fixture.workspace_id}"
    )

    restored = fixture.browser.request(
        "POST", f"/api/v1/projects/{project_id}/restore?workspace_id={fixture.workspace_id}"
    )

    assert restored.status_code == 200
    assert _search(fixture, "formulir").json()["results"] != []


@pytest.mark.integration
def test_creating_and_renaming_a_project_keeps_the_library_current(
    engine: Engine, clean_database: None
) -> None:
    """An index nobody maintains is a screen that answers yesterday's question."""
    del clean_database
    fixture = _signed_in(engine)
    created = fixture.browser.request(
        "POST",
        f"/api/v1/projects?workspace_id={fixture.workspace_id}",
        headers={"Idempotency-Key": "search-live"},
        json={"name": "Wawancara Perdana", "sourceKind": "upload"},
    )
    assert created.status_code == 201
    project_id = created.json()["id"]

    assert _titles(_search(fixture, "wawancara")) == ["Wawancara Perdana"]

    renamed = fixture.browser.request(
        "PATCH",
        f"/api/v1/projects/{project_id}?workspace_id={fixture.workspace_id}",
        json={"name": "Wawancara Kedua"},
    )

    assert renamed.status_code == 200
    assert _titles(_search(fixture, "kedua")) == ["Wawancara Kedua"]
    assert _search(fixture, "perdana").json()["results"] == []


@pytest.mark.integration
def test_another_workspaces_library_is_never_reachable(
    engine: Engine, clean_database: None
) -> None:
    """One Workspace's words are the strongest tenancy signal this product holds."""
    del clean_database
    fixture = _signed_in(engine)
    _indexed_project(engine, fixture, name="Rapat Produk", language="id")
    foreign = _signed_in(engine, subject="7" * 21, email="foreign@example.com")
    _indexed_project(engine, foreign, name="Rahasia Dagang", language="en", key="foreign")

    mine = _search(fixture, "rahasia")
    theirs = _search(foreign, "formulir")

    assert mine.json()["results"] == []
    assert theirs.json()["results"] == []


@pytest.mark.integration
def test_a_non_member_is_refused_exactly_like_a_workspace_that_does_not_exist(
    engine: Engine, clean_database: None
) -> None:
    """A guessed Workspace identifier must not be distinguishable from a missing one."""
    del clean_database
    fixture = _signed_in(engine)
    foreign = _signed_in(engine, subject="7" * 21, email="foreign@example.com")

    guessed = fixture.browser.get(f"/api/v1/search?workspace_id={foreign.workspace_id}&q=formulir")
    missing = fixture.browser.get(f"/api/v1/search?workspace_id={uuid4()}&q=formulir")

    assert guessed.status_code == missing.status_code == 404
    assert guessed.json()["error"] == {
        **missing.json()["error"],
        "requestId": guessed.json()["error"]["requestId"],
    }


@pytest.mark.integration
@pytest.mark.parametrize("role", (WorkspaceRole.REVIEWER, WorkspaceRole.VIEWER))
def test_every_member_who_may_read_projects_may_search_them(
    engine: Engine, clean_database: None, role: WorkspaceRole
) -> None:
    """Search is a read, and a reviewer who cannot search cannot review."""
    del clean_database
    fixture = _signed_in(engine)
    _indexed_project(engine, fixture, name="Rapat Produk", language="id")
    _set_role(engine, fixture, role)

    assert _search(fixture, "formulir").json()["results"] != []


@pytest.mark.integration
def test_an_anonymous_caller_is_refused(engine: Engine, clean_database: None) -> None:
    """The library is private, and an unauthenticated read of it is not a read at all."""
    del clean_database
    fixture = _signed_in(engine)
    _indexed_project(engine, fixture, name="Rapat Produk", language="id")
    anonymous = Browser(fixture.browser.app)

    response = anonymous.get(f"/api/v1/search?workspace_id={fixture.workspace_id}&q=formulir")

    assert_error(response, status_code=401, code="UNAUTHENTICATED")


@pytest.mark.integration
def test_rebuilding_the_index_changes_the_index_and_nothing_else(
    engine: Engine, clean_database: None
) -> None:
    """A derived index earns its keep only if it can be thrown away and rebuilt."""
    del clean_database
    fixture = _signed_in(engine)
    project_id = _indexed_project(engine, fixture, name="Rapat Produk", language="id")
    before = _document_snapshot(engine)
    sources = _source_snapshot(engine, project_id)

    with _api_session(fixture) as session:
        rebuild_workspace(session, workspace_id=fixture.workspace_id)

    assert _document_snapshot(engine) == before
    assert _source_snapshot(engine, project_id) == sources
    assert _search(fixture, "formulir").json()["results"] != []


@pytest.mark.integration
def test_a_rebuild_removes_documents_whose_source_row_is_gone(
    engine: Engine, clean_database: None
) -> None:
    """An index that only ever grows eventually answers with things that no longer exist."""
    del clean_database
    fixture = _signed_in(engine)
    project_id = _indexed_project(engine, fixture, name="Rapat Produk", language="id")
    with engine.begin() as connection:
        connection.execute(
            ClipCandidate.__table__.delete().where(ClipCandidate.project_id == project_id)
        )

    with _api_session(fixture) as session:
        rebuild_workspace(session, workspace_id=fixture.workspace_id)

    assert _search(fixture, "formulir", type="clip").json()["results"] == []
    assert _search(fixture, "formulir", type="transcript").json()["results"] != []


def _api_session(fixture: _Fixture) -> AbstractContextManager[Session]:
    """Open one least-privilege API transaction inside this member's Workspace."""
    return session_scope(
        settings=runtime_settings(),
        workspace_id=fixture.workspace_id,
        user_id=fixture.user_id,
        runtime_role=RuntimeRole.API,
    )


@pytest.mark.integration
def test_campaign_copy_is_findable_and_opens_on_the_clip_it_was_written_for(
    engine: Engine, clean_database: None
) -> None:
    """Copy a member cannot find again is copy they will sit down and write twice."""
    del clean_database
    fixture = _signed_in(engine)
    project_id = _indexed_project(engine, fixture, name="Rapat Produk", language="id")
    candidate_id = _candidate_id(engine, project_id)
    edit_id = _open_edit(fixture, project_id, candidate_id)
    outputs = fixture.browser.request(
        "POST",
        f"/api/v1/edits/{edit_id}/campaign-outputs?workspace_id={fixture.workspace_id}",
        json={"revision": 1, "platforms": ["tiktok"], "languages": ["id"]},
    )
    assert outputs.status_code == 201

    results = _search(fixture, "formulir", type="campaign_output").json()["results"]

    assert [result["type"] for result in results] == ["campaign_output"]
    assert results[0]["deepLink"].startswith(f"/dashboard/clips/{candidate_id}?output=")
    assert results[0]["title"] == outputs.json()["campaignOutputs"][0]["title"]


@pytest.mark.integration
def test_a_clip_that_has_been_rendered_is_marked_as_exported(
    engine: Engine, clean_database: None
) -> None:
    """ "What have I already published?" is the question a filter on export state answers."""
    del clean_database
    fixture = _signed_in(engine)
    project_id = _indexed_project(engine, fixture, name="Rapat Produk", language="id")
    candidate_id = _candidate_id(engine, project_id)
    edit_id = _open_edit(fixture, project_id, candidate_id)
    _render(engine, fixture, edit_id)

    with _api_session(fixture) as session:
        index_project(session, workspace_id=fixture.workspace_id, project_id=project_id)

    exported = _search(fixture, "formulir", type="clip", exportState="exported").json()["results"]
    unexported = _search(fixture, "formulir", type="clip", exportState="not_exported").json()
    assert [result["entityId"] for result in exported] == [str(candidate_id)]
    assert unexported["results"] == []


@pytest.mark.integration
def test_the_rebuild_command_runs_as_a_member_and_refuses_anybody_else(
    engine: Engine, clean_database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A maintenance task that invents an actor is the hole tenancy rules exist to close."""
    del clean_database
    # The command reads its connection from the environment, exactly as an operator's
    # shell would supply it.
    monkeypatch.setenv("CLIPAH_ENVIRONMENT", "test")
    monkeypatch.setenv("CLIPAH_PROCESS_ROLE", "api")
    monkeypatch.setenv("CLIPAH_DATABASE_URL", API_RUNTIME_DATABASE_URL)
    fixture = _signed_in(engine)
    _indexed_project(engine, fixture, name="Rapat Produk", language="id")
    with engine.begin() as connection:
        connection.execute(delete(SearchDocument))

    assert (
        indexer_main(["--workspace", str(fixture.workspace_id), "--actor", str(fixture.user_id)])
        == 0
    )
    assert _search(fixture, "formulir").json()["results"] != []

    with pytest.raises(SystemExit):
        indexer_main(["--workspace", str(fixture.workspace_id), "--actor", str(uuid4())])


def _open_edit(fixture: _Fixture, project_id: UUID, candidate_id: UUID) -> UUID:
    """Open the one Edit a clip's copy and renders both hang from."""
    created = fixture.browser.request(
        "POST",
        f"/api/v1/projects/{project_id}/candidates/{candidate_id}/edits"
        f"?workspace_id={fixture.workspace_id}",
        json=None,
    )
    assert created.status_code == 201, created.text
    return UUID(created.json()["id"])


def _render(engine: Engine, fixture: _Fixture, edit_id: UUID) -> None:
    """Record one rendered artifact for the current Revision of an Edit."""
    with Session(engine) as session:
        revision = session.scalars(
            select(ClipEditRevision).where(ClipEditRevision.clip_edit_id == edit_id)
        ).one()
        revision_id = revision.id
        composition_hash = revision.composition_hash
    with engine.begin() as connection:
        connection.execute(
            RenderArtifact.__table__.insert().values(
                id=uuid4(),
                workspace_id=fixture.workspace_id,
                clip_edit_revision_id=revision_id,
                job_id=None,
                preset="vertical_1080p",
                composition_hash=composition_hash,
                storage_key="renders/one.mp4",
                size_bytes=1_024,
                duration_ms=10_000,
            )
        )


def _signed_in(
    engine: Engine, *, subject: str = "1" * 21, email: str = "member@example.com"
) -> _Fixture:
    """Sign one member in and open the personal Workspace their library lives in."""
    del engine
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    provider.identify(subject=subject, email=email, name="Member Example")
    app, flow, _ = build_app(clock, provider)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    return _Fixture(
        browser=browser,
        workspace_id=workspace_id,
        user_id=UUID(browser.get("/api/v1/me").json()["id"]),
    )


def _indexed_project(
    engine: Engine,
    fixture: _Fixture,
    *,
    name: str,
    language: str,
    key: str | None = None,
) -> UUID:
    """Create one Project with a Transcript and one exposed Clip Candidate, then index it."""
    created = fixture.browser.request(
        "POST",
        f"/api/v1/projects?workspace_id={fixture.workspace_id}",
        headers={"Idempotency-Key": key or name},
        json={"name": name, "sourceKind": "upload"},
    )
    assert created.status_code == 201, created.text
    project_id = UUID(created.json()["id"])
    _seed_transcript_and_candidate(engine, fixture, project_id, language=language)
    with engine.begin() as connection:
        connection.execute(
            update(Project).where(Project.id == project_id).values(status=ProjectStatus.READY)
        )
    with _api_session(fixture) as session:
        index_project(session, workspace_id=fixture.workspace_id, project_id=project_id)
    return project_id


def _seed_transcript_and_candidate(
    engine: Engine, fixture: _Fixture, project_id: UUID, *, language: str
) -> None:
    """Write the durable rows an analysed Project would already hold."""
    sentence = INDONESIAN_SENTENCE if language == "id" else ENGLISH_SENTENCE
    words = [
        {
            "word_id": f"w{index:06d}",
            "text": token,
            "punctuation": "",
            "start_ms": index * WORD_MS,
            "end_ms": (index + 1) * WORD_MS,
            "confidence": 0.99,
            "speaker": "SPEAKER_00",
        }
        for index, token in enumerate(sentence.split() * 2)
    ]
    asset_id = uuid4()
    transcript_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            Asset.__table__.insert().values(
                id=asset_id,
                workspace_id=fixture.workspace_id,
                project_id=project_id,
                kind=AssetKind.SOURCE,
                source_type=AssetSourceType.USER_UPLOAD,
                storage_key=(
                    f"workspaces/{fixture.workspace_id}/projects/{project_id}/source/original"
                ),
                content_type="video/mp4",
                size_bytes=100,
                duration_ms=len(words) * WORD_MS,
                sha256=b"s" * 32,
            )
        )
        connection.execute(
            Transcript.__table__.insert().values(
                id=transcript_id,
                workspace_id=fixture.workspace_id,
                project_id=project_id,
                asset_id=asset_id,
                provider="assemblyai",
                provider_version="1.0.0",
                model="universal-2",
                language=language,
                full_text=" ".join(word["text"] for word in words),
                words=words,
                speaker_segments=[
                    {
                        "speaker": "SPEAKER_00",
                        "start_ms": 0,
                        "end_ms": len(words) * WORD_MS,
                        "start_word_id": words[0]["word_id"],
                        "end_word_id": words[-1]["word_id"],
                    }
                ],
                utterances=[],
                duration_ms=len(words) * WORD_MS,
                raw_result_storage_key="raw.json",
            )
        )
        connection.execute(
            ClipCandidate.__table__.insert().values(
                id=uuid4(),
                workspace_id=fixture.workspace_id,
                project_id=project_id,
                transcript_id=transcript_id,
                rank=1,
                score=0.9,
                hook=sentence,
                payoff="Menghapusnya melipatgandakan aktivasi",
                reason="Satu keputusan lengkap",
                category="insight",
                tags=["produk", "aktivasi"],
                start_ms=0,
                end_ms=10 * WORD_MS,
                start_word_id=words[0]["word_id"],
                end_word_id=words[9]["word_id"],
                transcript_excerpt=sentence,
                context_dependencies=[],
                score_breakdown={},
                context_warnings=[],
                visual_opportunities=[],
                model_metadata={"exposed": True},
            )
        )


def _candidate_id(engine: Engine, project_id: UUID) -> UUID:
    """Name the one exposed Clip Candidate seeded for a Project."""
    with Session(engine) as session:
        candidate_id = session.scalar(
            select(ClipCandidate.id).where(ClipCandidate.project_id == project_id)
        )
    assert candidate_id is not None
    return candidate_id


def _document_snapshot(engine: Engine) -> list[tuple[Any, ...]]:
    """Read every derived document in a form two rebuilds can be compared through."""
    with Session(engine) as session:
        documents = session.scalars(select(SearchDocument).order_by(SearchDocument.id)).all()
    return [
        (
            document.id,
            document.workspace_id,
            document.project_id,
            document.entity_type,
            document.entity_id,
            document.segment_ordinal,
            document.title,
            document.body,
            document.speaker,
            tuple(document.topics),
            tuple(document.tags),
            document.language,
            document.start_ms,
            document.end_ms,
            document.export_state,
        )
        for document in documents
    ]


def _source_snapshot(engine: Engine, project_id: UUID) -> tuple[Any, ...]:
    """Read the durable rows a rebuild is forbidden to touch."""
    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        transcripts = session.scalars(
            select(Transcript).where(Transcript.project_id == project_id)
        ).all()
        candidates = session.scalars(
            select(ClipCandidate).where(ClipCandidate.project_id == project_id)
        ).all()
    return (
        (project.name, project.status, project.updated_at),
        tuple((transcript.id, transcript.full_text) for transcript in transcripts),
        tuple((candidate.id, candidate.hook, candidate.rank) for candidate in candidates),
    )


def _age_documents(engine: Engine, *, days: int) -> None:
    """Move every document already indexed into the past, as older work would be."""
    with engine.begin() as connection:
        connection.execute(
            update(SearchDocument).values(
                source_created_at=datetime.now(tz=UTC) - timedelta(days=days)
            )
        )


def _set_role(engine: Engine, fixture: _Fixture, role: WorkspaceRole) -> None:
    """Move the signed-in member to another role inside their own Workspace."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workspace_memberships SET role = :role "
                "WHERE workspace_id = :workspace_id AND user_id = :user_id"
            ),
            {
                "role": role.value,
                "workspace_id": fixture.workspace_id,
                "user_id": fixture.user_id,
            },
        )
