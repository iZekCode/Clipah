"""Contract tests for the Project media the advanced editor may place on a timeline.

The editor's assets panel exists so a member can add media to a clip, and a composition
may only name media the owning Project already holds. This endpoint is therefore the
list the save-time authorization check would accept, and nothing wider: a derived
rendition is machinery, another Project's media is invisible, and a Project the caller
has no standing on is indistinguishable from one that never existed.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, update

from clipah.models import Asset, AssetKind, AssetSourceType, Project, ProjectStatus
from harness import NOW, Browser, Clock, StubGoogleProvider, assert_error, build_app, sign_in


@dataclass(frozen=True, slots=True)
class ProjectFixture:
    """One Project of the signed-in member's Workspace."""

    workspace_id: UUID
    project_id: UUID


@pytest.mark.integration
def test_assets_list_the_media_a_composition_may_place(
    engine: Engine, clean_database: None
) -> None:
    """The panel needs each asset's identity and shape before it can offer to add it."""
    del clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    fixture = _ready_project(engine, browser)
    source_id = _asset(engine, fixture, kind=AssetKind.SOURCE, name="episode.mp4")

    response = browser.get(_path(fixture))

    assert response.status_code == 200
    assert response.json() == {
        "assets": [
            {
                "id": str(source_id),
                "kind": "source",
                "contentType": "video/mp4",
                "sizeBytes": 4_096,
                "durationMs": 60_000,
                "width": 1920,
                "height": 1080,
                "createdAt": NOW.isoformat(),
            }
        ]
    }


@pytest.mark.integration
def test_derived_renditions_are_not_offered_as_editable_media(
    engine: Engine, clean_database: None
) -> None:
    """A proxy, a thumbnail, and a transcription track are machinery, not member media."""
    del clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    fixture = _ready_project(engine, browser)
    source_id = _asset(engine, fixture, kind=AssetKind.SOURCE, name="episode.mp4")
    for kind in (
        AssetKind.PROXY,
        AssetKind.THUMBNAIL,
        AssetKind.WAVEFORM,
        AssetKind.TRANSCRIPTION_AUDIO,
        AssetKind.RENDER,
    ):
        _asset(engine, fixture, kind=kind, name=f"{kind.value}.bin")

    response = browser.get(_path(fixture))

    assert [entry["id"] for entry in response.json()["assets"]] == [str(source_id)]


@pytest.mark.integration
def test_retrieved_broll_is_offered_as_editable_media(engine: Engine, clean_database: None) -> None:
    """Replacing an accepted suggestion's picture means naming another asset this clip holds."""
    del clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    fixture = _ready_project(engine, browser)
    source_id = _asset(engine, fixture, kind=AssetKind.SOURCE, name="episode.mp4")
    broll_id = _asset(engine, fixture, kind=AssetKind.BROLL, name="cutaway.mp4")
    _asset(engine, fixture, kind=AssetKind.BROLL_PROXY, name="cutaway-proxy.mp4")

    response = browser.get(_path(fixture))

    assert {entry["id"] for entry in response.json()["assets"]} == {
        str(source_id),
        str(broll_id),
    }


@pytest.mark.integration
def test_another_project_of_the_same_workspace_contributes_nothing(
    engine: Engine, clean_database: None
) -> None:
    """Composition authorization is per Project, so the panel may not widen it."""
    del clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    edited = _ready_project(engine, browser, key="edited-project")
    other = _ready_project(engine, browser, key="other-project")
    mine = _asset(engine, edited, kind=AssetKind.SOURCE, name="mine.mp4")
    _asset(engine, other, kind=AssetKind.SOURCE, name="theirs.mp4")

    response = browser.get(_path(edited))

    assert [entry["id"] for entry in response.json()["assets"]] == [str(mine)]


@pytest.mark.integration
def test_a_foreign_project_answers_exactly_like_a_missing_one(
    engine: Engine, clean_database: None
) -> None:
    """A guessed Project identifier may not be distinguished from an invented one."""
    del clean_database
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    app, flow, _ = build_app(clock, provider)
    owner = Browser(app)
    sign_in(owner, flow)
    owned = _ready_project(engine, owner)
    _asset(engine, owned, kind=AssetKind.SOURCE, name="episode.mp4")

    provider.identify(subject="assets-stranger", email="stranger@example.com", name="Stranger")
    stranger = Browser(app)
    sign_in(stranger, flow)
    stranger_workspace = UUID(stranger.get("/api/v1/workspaces").json()["workspaces"][0]["id"])

    guessed = stranger.get(
        f"/api/v1/projects/{owned.project_id}/assets?workspace_id={stranger_workspace}"
    )
    missing = stranger.get(f"/api/v1/projects/{uuid4()}/assets?workspace_id={stranger_workspace}")

    assert_error(guessed, status_code=404, code="NOT_FOUND")
    assert_error(missing, status_code=404, code="NOT_FOUND")
    assert guessed.json()["error"]["message"] == missing.json()["error"]["message"]


@pytest.mark.integration
def test_a_deleted_project_stops_offering_its_media(engine: Engine, clean_database: None) -> None:
    """A soft-deleted Project is not editable, so its media is not placeable either."""
    del clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    fixture = _ready_project(engine, browser)
    _asset(engine, fixture, kind=AssetKind.SOURCE, name="episode.mp4")
    with engine.begin() as connection:
        connection.execute(
            update(Project).where(Project.id == fixture.project_id).values(archived_at=NOW)
        )

    response = browser.get(_path(fixture))

    assert_error(response, status_code=404, code="NOT_FOUND")


@pytest.mark.integration
def test_assets_refuse_a_caller_without_a_session(engine: Engine, clean_database: None) -> None:
    """Media inventory is Workspace data, and an anonymous caller has no Workspace."""
    del clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    fixture = _ready_project(engine, browser)

    anonymous = Browser(app)
    response = anonymous.get(_path(fixture))

    assert_error(response, status_code=401, code="UNAUTHENTICATED")


@pytest.mark.unit
def test_project_assets_openapi_declares_a_strict_response_schema() -> None:
    """A generated client needs the real asset fields, not an arbitrary dictionary."""
    clock = Clock(NOW)
    app, _, _ = build_app(clock, StubGoogleProvider(clock))
    operation = app.openapi()["paths"]["/api/v1/projects/{project_id}/assets"]["get"]

    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]

    assert schema["$ref"].endswith("/ProjectAssetsResponse")


def _path(fixture: ProjectFixture) -> str:
    """The assets path of one Project, inside the Workspace that owns it."""
    return f"/api/v1/projects/{fixture.project_id}/assets?workspace_id={fixture.workspace_id}"


def _ready_project(
    engine: Engine, browser: Browser, *, key: str = "assets-project"
) -> ProjectFixture:
    """Create one Project that has finished analysis and can therefore be edited."""
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
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
            update(Project)
            .where(Project.id == project_id)
            .values(status=ProjectStatus.READY, updated_at=NOW)
        )
    return ProjectFixture(workspace_id=workspace_id, project_id=project_id)


def _asset(engine: Engine, fixture: ProjectFixture, *, kind: AssetKind, name: str) -> UUID:
    """Give one Project a stored asset of one kind."""
    asset_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            Asset.__table__.insert().values(
                id=asset_id,
                workspace_id=fixture.workspace_id,
                project_id=fixture.project_id,
                kind=kind,
                source_type=AssetSourceType.USER_UPLOAD,
                storage_key=(
                    f"workspaces/{fixture.workspace_id}/projects/{fixture.project_id}/{name}"
                ),
                content_type="video/mp4",
                size_bytes=4_096,
                duration_ms=60_000,
                width=1920,
                height=1080,
                sha256=bytes([kind.value.encode()[0]]) * 32,
                created_at=NOW,
            )
        )
    return asset_id
