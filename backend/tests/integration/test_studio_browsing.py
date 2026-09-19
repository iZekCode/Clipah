"""Contracts for the creator studio's browsing reads.

The redesigned product walks one creator from a Project to a moment, to an Edit, to an
export, and on to a publication. Every step needs a read that answers "what do I have,
and where does it live?" without the member typing an identifier. These reads are all
derived from records the pipeline already keeps, and every one of them obeys the same
tenant rule as the endpoints it summarizes: another Workspace's row is indistinguishable
from one that never existed.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, update

from clipah.assets.storage import StoredObject
from clipah.models import (
    Asset,
    AssetKind,
    AssetProvenance,
    AssetSourceType,
    ClipCandidate,
    ClipEdit,
    Project,
)
from clipah.renders.models import RenderPreset
from harness import NOW, Browser, Clock, StubGoogleProvider, assert_error, build_app, sign_in
from integration.test_render_pipeline import (
    Stage,
    _path,
    _request,
    _staged,
    _store_artifact,
)


@pytest.mark.integration
def test_the_clip_browser_lists_every_moment_with_its_project_and_how_far_it_has_got(
    engine: Engine,
) -> None:
    """A creator browses clips before searching, so each one must say where it stands."""
    stage = _staged(engine)
    candidate_id = _candidate_id(stage)

    edited = stage.browser.get(_path(stage, "/clips")).json()
    _store_artifact(stage, preset=RenderPreset.PORTRAIT)
    exported = stage.browser.get(_path(stage, "/clips")).json()

    assert [clip["id"] for clip in edited["clips"]] == [str(candidate_id)]
    first = edited["clips"][0]
    assert first["projectId"] == str(stage.project_id)
    assert first["projectName"] == "Render project"
    assert first["hook"] == "Hook"
    assert first["durationMs"] == 30_000
    assert first["stage"] == "edited"
    assert first["editId"] == str(stage.edit_id)
    assert first["exportCount"] == 0
    assert edited["nextCursor"] is None
    assert exported["clips"][0]["stage"] == "exported"
    assert exported["clips"][0]["exportCount"] == 1


@pytest.mark.integration
def test_the_clip_browser_filters_by_stage_and_project(engine: Engine) -> None:
    """Suggested moments, saved Edits, and exports are different piles of work."""
    stage = _staged(engine)

    suggested = stage.browser.get(_path(stage, "/clips?stage=suggested")).json()
    edited = stage.browser.get(_path(stage, "/clips?stage=edited")).json()
    elsewhere = stage.browser.get(_path(stage, f"/clips?projectId={uuid4()}")).json()
    here = stage.browser.get(_path(stage, f"/clips?projectId={stage.project_id}")).json()

    assert suggested["clips"] == []
    assert len(edited["clips"]) == 1
    assert elsewhere["clips"] == []
    assert len(here["clips"]) == 1


@pytest.mark.integration
def test_the_clip_browser_pages_with_an_opaque_cursor_and_refuses_a_forged_one(
    engine: Engine,
) -> None:
    """A Workspace can hold hundreds of moments, so the browser reads them a page at a time."""
    stage = _staged(engine)
    _second_candidate(stage)

    first = stage.browser.get(_path(stage, "/clips?limit=1")).json()
    second = stage.browser.get(_path(stage, f"/clips?limit=1&cursor={first['nextCursor']}")).json()
    forged = stage.browser.get(_path(stage, "/clips?cursor=not-a-cursor"))

    assert len(first["clips"]) == 1
    assert first["nextCursor"] is not None
    assert len(second["clips"]) == 1
    assert second["clips"][0]["id"] != first["clips"][0]["id"]
    assert second["nextCursor"] is None
    assert_error(forged, status_code=422, code="VALIDATION_ERROR")


@pytest.mark.integration
def test_the_clip_browser_omits_a_deleted_project(engine: Engine) -> None:
    """A soft-deleted Project stops contributing clips the moment it is deleted."""
    stage = _staged(engine)
    with engine.begin() as connection:
        connection.execute(
            update(Project).where(Project.id == stage.project_id).values(archived_at=NOW)
        )

    listed = stage.browser.get(_path(stage, "/clips")).json()

    assert listed["clips"] == []


@pytest.mark.integration
def test_a_clip_resolves_its_own_project_edits_and_exports(engine: Engine) -> None:
    """A clip link carries one identifier; everything else is the backend's to resolve."""
    stage = _staged(engine)
    candidate_id = _candidate_id(stage)
    _request(stage, RenderPreset.SQUARE)
    render_id = _store_artifact(stage, preset=RenderPreset.SQUARE)

    response = stage.browser.get(_path(stage, f"/clips/{candidate_id}"))

    assert response.status_code == 200
    body = response.json()
    assert body["candidate"]["id"] == str(candidate_id)
    assert body["candidate"]["scoreBreakdown"]["hook"] == 0.9
    assert body["project"] == {
        "id": str(stage.project_id),
        "name": "Render project",
        "status": "ready",
    }
    assert [edit["id"] for edit in body["edits"]] == [str(stage.edit_id)]
    assert body["edits"][0]["currentRevision"] == 1
    assert [export["renderId"] for export in body["exports"]] == [str(render_id)]


@pytest.mark.integration
def test_a_foreign_clip_is_refused_exactly_like_a_missing_one(engine: Engine) -> None:
    """Resolving a clip must not become a way to learn another Workspace's Projects."""
    stage = _staged(engine)
    candidate_id = _candidate_id(stage)
    stranger, stranger_workspace = _stranger(stage)

    guessed = stranger.get(f"/api/v1/clips/{candidate_id}?workspace_id={stranger_workspace}")
    missing = stranger.get(f"/api/v1/clips/{uuid4()}?workspace_id={stranger_workspace}")
    browsed = stranger.get(f"/api/v1/clips?workspace_id={stranger_workspace}").json()

    assert_error(guessed, status_code=404, code="NOT_FOUND")
    assert_error(missing, status_code=404, code="NOT_FOUND")
    assert guessed.json()["error"]["message"] == missing.json()["error"]["message"]
    assert browsed["clips"] == []


@pytest.mark.integration
def test_exports_list_the_render_in_progress_and_then_the_finished_file(engine: Engine) -> None:
    """Exports must survive a refresh, including the one that is still being encoded."""
    stage = _staged(engine)
    accepted = _request(stage, RenderPreset.PORTRAIT)
    job_id = accepted.json()["jobId"]

    pending = stage.browser.get(_path(stage, f"/exports?editId={stage.edit_id}")).json()
    _store_artifact(stage, preset=RenderPreset.PORTRAIT)
    finished = stage.browser.get(_path(stage, f"/exports?editId={stage.edit_id}")).json()

    assert len(pending["exports"]) == 1
    waiting = pending["exports"][0]
    assert waiting["jobId"] == job_id
    assert waiting["status"] == "queued"
    assert waiting["renderId"] is None
    assert waiting["editId"] == str(stage.edit_id)
    assert waiting["revision"] == 1
    assert waiting["preset"] == RenderPreset.PORTRAIT.value
    assert waiting["projectName"] == "Render project"
    ready = finished["exports"][0]
    assert ready["status"] == "ready"
    assert ready["renderId"] is not None
    assert ready["durationMs"] == 30_000
    assert ready["sizeBytes"] == 4_096


@pytest.mark.integration
def test_exports_filter_by_state_and_project_and_hide_another_workspace(engine: Engine) -> None:
    """Publishing starts from finished exports only, and never from someone else's."""
    stage = _staged(engine)
    _request(stage, RenderPreset.PORTRAIT)
    _request(stage, RenderPreset.SQUARE)
    _store_artifact(stage, preset=RenderPreset.SQUARE)
    stranger, stranger_workspace = _stranger(stage)

    ready = stage.browser.get(_path(stage, "/exports?state=ready")).json()
    pending = stage.browser.get(_path(stage, "/exports?state=in_progress")).json()
    by_project = stage.browser.get(_path(stage, f"/exports?projectId={stage.project_id}")).json()
    foreign = stranger.get(
        f"/api/v1/exports?workspace_id={stranger_workspace}&editId={stage.edit_id}"
    ).json()
    paged = stage.browser.get(_path(stage, "/exports?limit=1")).json()
    rest = stage.browser.get(_path(stage, f"/exports?limit=1&cursor={paged['nextCursor']}")).json()
    forged = stage.browser.get(_path(stage, "/exports?cursor=%%%"))

    assert [export["preset"] for export in ready["exports"]] == [RenderPreset.SQUARE.value]
    assert [export["preset"] for export in pending["exports"]] == [RenderPreset.PORTRAIT.value]
    assert len(by_project["exports"]) == 2
    assert foreign["exports"] == []
    assert len(paged["exports"]) == 1
    assert len(rest["exports"]) == 1
    assert rest["exports"][0]["id"] != paged["exports"][0]["id"]
    assert_error(forged, status_code=422, code="VALIDATION_ERROR")


@pytest.mark.integration
def test_an_export_bound_to_a_stale_revision_is_refused_rather_than_rendering_newer_work(
    engine: Engine,
) -> None:
    """Export saves first and then names the Revision it saved; a newer one is not that cut."""
    stage = _staged(engine)

    stale = stage.browser.request(
        "POST",
        _path(stage, f"/edits/{stage.edit_id}/renders"),
        headers={"Idempotency-Key": "render-stale"},
        json={"preset": RenderPreset.PORTRAIT.value, "expectedRevision": 7},
    )
    current = stage.browser.request(
        "POST",
        _path(stage, f"/edits/{stage.edit_id}/renders"),
        headers={"Idempotency-Key": "render-current"},
        json={"preset": RenderPreset.PORTRAIT.value, "expectedRevision": 1},
    )

    assert_error(stale, status_code=409, code="EDIT_REVISION_CONFLICT")
    assert stale.headers["X-Clipah-Current-Revision"] == "1"
    assert current.status_code == 202


@pytest.mark.integration
def test_the_asset_browser_lists_member_media_with_provenance_and_filters(
    engine: Engine,
) -> None:
    """The library shows what a creator owns and where it came from, never pipeline machinery."""
    stage = _staged(engine)
    broll_id = _broll_asset(stage)
    _machinery_asset(stage, AssetKind.WAVEFORM)

    everything = stage.browser.get(_path(stage, "/assets")).json()
    broll = stage.browser.get(_path(stage, "/assets?kind=broll")).json()
    elsewhere = stage.browser.get(_path(stage, f"/assets?projectId={uuid4()}")).json()
    paged = stage.browser.get(_path(stage, "/assets?limit=1")).json()
    rest = stage.browser.get(_path(stage, f"/assets?limit=1&cursor={paged['nextCursor']}")).json()
    forged = stage.browser.get(_path(stage, "/assets?cursor=abc"))

    kinds = sorted(asset["kind"] for asset in everything["assets"])
    assert kinds == ["broll", "source"]
    assert [asset["id"] for asset in broll["assets"]] == [str(broll_id)]
    listed = broll["assets"][0]
    assert listed["projectName"] == "Render project"
    assert listed["sourceType"] == "stock"
    assert listed["provenance"] == {
        "provider": "pexels",
        "author": "A. Photographer",
        "licenseName": "Pexels License",
        "licenseUrl": "https://www.pexels.com/license/",
        "sourceUrl": "https://www.pexels.com/video/1/",
        "attributionText": "Video by A. Photographer",
        "generated": False,
    }
    assert elsewhere["assets"] == []
    assert len(paged["assets"]) == 1
    assert len(rest["assets"]) == 1
    assert_error(forged, status_code=422, code="VALIDATION_ERROR")


@pytest.mark.integration
def test_an_asset_preview_is_a_short_lived_capability_hidden_from_strangers(
    engine: Engine,
) -> None:
    """A preview is signed on demand for five minutes, and a guess learns nothing."""
    stage = _staged(engine)
    broll_id = _broll_asset(stage)
    waveform_id = _machinery_asset(stage, AssetKind.WAVEFORM)
    stranger, stranger_workspace = _stranger(stage)

    preview = stage.browser.get(_path(stage, f"/assets/{broll_id}/preview-url"))
    machinery = stage.browser.get(_path(stage, f"/assets/{waveform_id}/preview-url"))
    guessed = stranger.get(
        f"/api/v1/assets/{broll_id}/preview-url?workspace_id={stranger_workspace}"
    )

    assert preview.status_code == 200
    assert preview.json()["url"].startswith("fake://download/")
    assert preview.json()["contentType"] == "video/mp4"
    assert_error(machinery, status_code=404, code="NOT_FOUND")
    assert_error(guessed, status_code=404, code="NOT_FOUND")


@pytest.mark.integration
def test_a_stock_clip_is_previewed_from_its_small_proxy_rather_than_the_full_download(
    engine: Engine,
) -> None:
    """A 4K stock file can be hundreds of megabytes; the editor plays the proxy made of it."""
    stage = _staged(engine)
    broll_id = _broll_asset(stage)
    proxy_key = _broll_proxy(stage, broll_id)

    preview = stage.browser.get(_path(stage, f"/assets/{broll_id}/preview-url"))

    assert preview.status_code == 200
    assert proxy_key in preview.json()["url"]


@pytest.mark.integration
def test_a_project_thumbnail_is_signed_when_ingest_made_one_and_absent_otherwise(
    engine: Engine,
) -> None:
    """Media cards show a real frame when there is one and a placeholder when there is not."""
    stage = _staged(engine)

    before = stage.browser.get(_path(stage, f"/projects/{stage.project_id}/thumbnail"))
    _machinery_asset(stage, AssetKind.THUMBNAIL)
    after = stage.browser.get(_path(stage, f"/projects/{stage.project_id}/thumbnail"))

    assert_error(before, status_code=404, code="NOT_FOUND")
    assert after.status_code == 200
    assert after.json()["contentType"] == "image/jpeg"


@pytest.mark.unit
def test_the_studio_reads_declare_strict_response_schemas() -> None:
    """The generated client needs real fields for every new read, not arbitrary objects."""
    clock = Clock(NOW)
    app, _, _ = build_app(clock, StubGoogleProvider(clock))
    paths = app.openapi()["paths"]
    expected = {
        "/api/v1/clips": "ClipPageResponse",
        "/api/v1/clips/{candidate_id}": "ClipDetailResponse",
        "/api/v1/exports": "ExportPageResponse",
        "/api/v1/assets": "AssetPageResponse",
        "/api/v1/assets/{asset_id}/preview-url": "MediaPreviewResponse",
        "/api/v1/projects/{project_id}/thumbnail": "MediaPreviewResponse",
        "/api/v1/projects/{project_id}/storyboard": "StoryboardResponse",
        "/api/v1/projects/{project_id}/waveform": "WaveformResponse",
        "/api/v1/projects/{project_id}/transcript": "TranscriptResponse",
    }

    for path, component in expected.items():
        schema = paths[path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["$ref"].endswith(f"/{component}")


@pytest.mark.integration
def test_recent_order_puts_the_most_recently_edited_clip_first_as_a_single_page(
    engine: Engine,
) -> None:
    """Home continues the work touched last, which is not the analysis order."""
    stage = _staged(engine)
    first = _candidate_id(stage)
    second = _second_candidate(stage)
    opened = stage.browser.request(
        "POST",
        _path(stage, f"/projects/{stage.project_id}/candidates/{second}/edits"),
        json=None,
    )
    assert opened.status_code == 201
    # The harness clock is frozen, so both Edits were saved at the same instant; date the
    # staged one earlier so "most recently edited" has one right answer.
    with stage.engine.begin() as connection:
        connection.execute(
            update(ClipEdit.__table__)
            .where(ClipEdit.__table__.c.candidate_id == first)
            .values(updated_at=NOW - timedelta(hours=1))
        )

    recent = stage.browser.get(_path(stage, "/clips?stage=edited&order=recent&limit=5")).json()
    created = stage.browser.get(_path(stage, "/clips?stage=edited&limit=5")).json()

    assert [clip["id"] for clip in recent["clips"]] == [str(second), str(first)]
    assert recent["nextCursor"] is None
    assert {clip["id"] for clip in created["clips"]} == {str(first), str(second)}
    assert all(clip["editUpdatedAt"] is not None for clip in recent["clips"])
    suggested = stage.browser.get(_path(stage, "/clips?stage=suggested")).json()
    assert all(clip["editUpdatedAt"] is None for clip in suggested["clips"])


@pytest.mark.integration
def test_recent_order_refuses_a_cursor(engine: Engine) -> None:
    """Recent is a top-N read; paging it would promise an order edits can reshuffle."""
    stage = _staged(engine)
    first = stage.browser.get(_path(stage, "/clips?limit=1")).json()

    refused = stage.browser.get(
        _path(stage, f"/clips?order=recent&cursor={first['nextCursor'] or 'x'}")
    )

    assert_error(refused, status_code=422, code="VALIDATION_ERROR")


def _candidate_id(stage: Stage) -> UUID:
    """Read the candidate the staged Edit was opened from."""
    return UUID(stage.browser.get(_path(stage, f"/edits/{stage.edit_id}")).json()["candidateId"])


def _second_candidate(stage: Stage) -> UUID:
    """Add one more exposed candidate to the staged Project, with no Edit yet."""
    candidate_id = _candidate_id(stage)
    copied = uuid4()
    table = ClipCandidate.__table__
    with stage.engine.begin() as connection:
        row = connection.execute(table.select().where(table.c.id == candidate_id)).one()
        values: dict[str, Any] = dict(row._mapping)
        values.update(id=copied, rank=2, start_ms=40_000, end_ms=70_000)
        connection.execute(table.insert().values(**values))
    return copied


def _broll_asset(stage: Stage) -> UUID:
    """Store one licensed stock clip the staged Project retrieved."""
    asset_id = uuid4()
    key = f"workspaces/{stage.workspace_id}/projects/{stage.project_id}/broll/{asset_id}.mp4"
    with stage.engine.begin() as connection:
        connection.execute(
            Asset.__table__.insert().values(
                id=asset_id,
                workspace_id=stage.workspace_id,
                project_id=stage.project_id,
                kind=AssetKind.BROLL,
                source_type=AssetSourceType.STOCK,
                storage_key=key,
                content_type="video/mp4",
                size_bytes=2_048,
                duration_ms=8_000,
                width=1920,
                height=1080,
                sha256=b"b" * 32,
            )
        )
        connection.execute(
            AssetProvenance.__table__.insert().values(
                workspace_id=stage.workspace_id,
                asset_id=asset_id,
                provider="pexels",
                provider_asset_id="1",
                source_url="https://www.pexels.com/video/1/",
                author="A. Photographer",
                license_name="Pexels License",
                license_url="https://www.pexels.com/license/",
                terms_snapshot="Free to use.",
                retrieved_at=NOW,
                moderation_result="approved",
                attribution_text="Video by A. Photographer",
                checksum=b"c" * 32,
            )
        )
    stage.store.objects[key] = StoredObject(key=key, content_type="video/mp4", content_length=2_048)
    return asset_id


def _broll_proxy(stage: Stage, broll_id: UUID) -> str:
    """Store the playback proxy retrieval makes beside one stock clip."""
    key = f"workspaces/{stage.workspace_id}/projects/{stage.project_id}/broll/{broll_id}/proxy"
    with stage.engine.begin() as connection:
        connection.execute(
            Asset.__table__.insert().values(
                id=uuid4(),
                workspace_id=stage.workspace_id,
                project_id=stage.project_id,
                kind=AssetKind.BROLL_PROXY,
                source_type=AssetSourceType.DERIVED,
                storage_key=key,
                content_type="video/mp4",
                size_bytes=512,
                duration_ms=8_000,
                width=720,
                height=1280,
                sha256=b"b" * 32,
            )
        )
    stage.store.objects[key] = StoredObject(key=key, content_type="video/mp4", content_length=512)
    return key


def _machinery_asset(stage: Stage, kind: AssetKind) -> UUID:
    """Store one rendition the pipeline produced for its own use."""
    asset_id = uuid4()
    content_type = "image/jpeg" if kind is AssetKind.THUMBNAIL else "application/json"
    key = f"workspaces/{stage.workspace_id}/projects/{stage.project_id}/derived/{asset_id}"
    with stage.engine.begin() as connection:
        connection.execute(
            Asset.__table__.insert().values(
                id=asset_id,
                workspace_id=stage.workspace_id,
                project_id=stage.project_id,
                kind=kind,
                source_type=AssetSourceType.DERIVED,
                storage_key=key,
                content_type=content_type,
                size_bytes=512,
                sha256=b"d" * 32,
            )
        )
    stage.store.objects[key] = StoredObject(key=key, content_type=content_type, content_length=512)
    return asset_id


def _stranger(stage: Stage) -> tuple[Browser, UUID]:
    """Sign in a second User who belongs only to their own personal Workspace."""
    stage.provider.identify(
        subject=f"studio-stranger-{uuid4().hex[:8]}",
        email=f"stranger-{uuid4().hex[:8]}@example.com",
        name="Stranger",
    )
    stranger = Browser(stage.browser.app)
    sign_in(stranger, stage.flow)
    workspace_id = UUID(stranger.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    return stranger, workspace_id


@pytest.mark.integration
def test_a_storyboard_describes_every_sheet_and_signs_each_one(engine: Engine) -> None:
    """A poster is drawn by offset, so every sheet must say where its frames start."""
    stage = _staged(engine)
    before = stage.browser.get(_path(stage, f"/projects/{stage.project_id}/storyboard"))
    _preview_asset(
        stage, name="storyboard-v1/sheet-0001.jpg", kind=AssetKind.STORYBOARD, duration_ms=5_000
    )
    _preview_asset(
        stage, name="storyboard-v1/sheet-0000.jpg", kind=AssetKind.STORYBOARD, duration_ms=200_000
    )

    response = stage.browser.get(_path(stage, f"/projects/{stage.project_id}/storyboard"))

    assert_error(before, status_code=404, code="NOT_FOUND")
    assert response.status_code == 200
    body = response.json()
    assert {
        key: body[key]
        for key in (
            "version",
            "intervalMs",
            "tileWidth",
            "tileHeight",
            "columns",
            "rows",
            "durationMs",
        )
    } == {
        "version": 1,
        "intervalMs": 2_000,
        "tileWidth": 160,
        "tileHeight": 90,
        "columns": 10,
        "rows": 10,
        "durationMs": 205_000,
    }
    assert [(sheet["index"], sheet["startMs"], sheet["tileCount"]) for sheet in body["sheets"]] == [
        (0, 0, 100),
        (1, 200_000, 3),
    ]
    assert all(sheet["url"].startswith("fake://download/") for sheet in body["sheets"])
    assert "storageKey" not in str(body)


@pytest.mark.integration
def test_a_waveform_is_a_short_lived_capability_with_its_geometry(engine: Engine) -> None:
    """The timeline must know how many peaks make a second before it can draw them."""
    stage = _staged(engine)
    _preview_asset(stage, name="waveform-v1.bin", kind=AssetKind.WAVEFORM, duration_ms=60_000)

    body = stage.browser.get(_path(stage, f"/projects/{stage.project_id}/waveform")).json()

    assert body["version"] == 1
    assert body["peaksPerSecond"] == 20
    assert body["durationMs"] == 60_000
    assert body["url"].startswith("fake://download/")


@pytest.mark.integration
def test_a_transcript_lists_its_words_in_order_with_speakers(engine: Engine) -> None:
    """Review mode and the Project page show what was said around every moment."""
    stage = _staged(engine)

    body = stage.browser.get(_path(stage, f"/projects/{stage.project_id}/transcript")).json()

    assert body == {
        "language": "id",
        "durationMs": 60_000,
        "words": [
            {
                "id": "w000001",
                "text": "Satu",
                "punctuation": "",
                "startMs": 5_000,
                "endMs": 5_900,
                "speaker": "SPEAKER_00",
            }
        ],
    }


@pytest.mark.integration
@pytest.mark.parametrize("suffix", ["storyboard", "waveform", "transcript"])
def test_previews_and_transcripts_of_another_workspace_answer_like_missing_ones(
    engine: Engine, suffix: str
) -> None:
    """A guessed Project must not reveal that its media exists."""
    stage = _staged(engine)
    _preview_asset(
        stage, name="storyboard-v1/sheet-0000.jpg", kind=AssetKind.STORYBOARD, duration_ms=200_000
    )
    _preview_asset(stage, name="waveform-v1.bin", kind=AssetKind.WAVEFORM, duration_ms=60_000)
    stranger, stranger_workspace = _stranger(stage)

    guessed = stranger.get(
        f"/api/v1/projects/{stage.project_id}/{suffix}?workspace_id={stranger_workspace}"
    )
    missing = stranger.get(f"/api/v1/projects/{uuid4()}/{suffix}?workspace_id={stranger_workspace}")

    assert_error(guessed, status_code=404, code="NOT_FOUND")
    assert_error(missing, status_code=404, code="NOT_FOUND")
    assert guessed.json()["error"]["message"] == missing.json()["error"]["message"]


def _preview_asset(stage: Stage, *, name: str, kind: AssetKind, duration_ms: int) -> UUID:
    """Record one preview artifact the way the preview runner would have."""
    asset_id = uuid4()
    key = (
        f"workspaces/{stage.workspace_id}/projects/{stage.project_id}"
        f"/derived/{stage.source_asset_id}/{name}"
    )
    content_type = "image/jpeg" if kind is AssetKind.STORYBOARD else "application/octet-stream"
    with stage.engine.begin() as connection:
        connection.execute(
            Asset.__table__.insert().values(
                id=asset_id,
                workspace_id=stage.workspace_id,
                project_id=stage.project_id,
                kind=kind,
                source_type=AssetSourceType.DERIVED,
                storage_key=key,
                content_type=content_type,
                size_bytes=512,
                duration_ms=duration_ms,
                width=1600 if kind is AssetKind.STORYBOARD else None,
                height=900 if kind is AssetKind.STORYBOARD else None,
                sha256=b"p" * 32,
            )
        )
    stage.store.objects[key] = StoredObject(key=key, content_type=content_type, content_length=512)
    return asset_id
