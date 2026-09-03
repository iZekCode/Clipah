"""Contract tests for the bounded proxy capability clip review plays against."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, update

from clipah.assets.storage import FakeObjectStore, StoredObject
from clipah.models import Asset, AssetKind, AssetSourceType, Project, ProjectStatus
from harness import NOW, Browser, Clock, StubGoogleProvider, assert_error, build_app, sign_in


@dataclass(frozen=True, slots=True)
class ProjectFixture:
    """One Project of the signed-in member's Workspace, and its proxy storage key."""

    workspace_id: UUID
    project_id: UUID
    proxy_key: str


@pytest.mark.integration
def test_proxy_returns_one_five_minute_capability_and_the_media_it_describes(
    engine: Engine, clean_database: None
) -> None:
    """A reviewer needs playable media and its shape, and needs both to expire."""
    del clean_database
    clock = Clock(NOW)
    store = FakeObjectStore(now=clock)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), object_store=store)
    browser = Browser(app)
    sign_in(browser, flow)
    fixture = _project_with_proxy(engine, browser, store)

    response = browser.get(_path(fixture))

    assert response.status_code == 200
    assert response.json() == {
        "url": f"fake://download/{fixture.proxy_key}",
        "expiresAt": (NOW + timedelta(minutes=5)).isoformat(),
        "contentType": "video/mp4",
        "durationMs": 60_000,
        "width": 1280,
        "height": 720,
    }


@pytest.mark.integration
def test_proxy_hides_a_foreign_project_exactly_like_a_missing_one(
    engine: Engine, clean_database: None
) -> None:
    """Membership is proven before ownership is queried, so a guess reveals nothing."""
    del clean_database
    clock = Clock(NOW)
    store = FakeObjectStore(now=clock)
    provider = StubGoogleProvider(clock)
    app, flow, _ = build_app(clock, provider, object_store=store)
    owner = Browser(app)
    sign_in(owner, flow)
    owned = _project_with_proxy(engine, owner, store)

    provider.identify(subject="proxy-stranger", email="stranger@example.com", name="Stranger")
    stranger = Browser(app)
    sign_in(stranger, flow)
    stranger_workspace = UUID(stranger.get("/api/v1/workspaces").json()["workspaces"][0]["id"])

    guessed = stranger.get(
        f"/api/v1/projects/{owned.project_id}/proxy?workspace_id={stranger_workspace}"
    )
    missing = stranger.get(f"/api/v1/projects/{uuid4()}/proxy?workspace_id={stranger_workspace}")

    assert_error(guessed, status_code=404, code="NOT_FOUND")
    assert_error(missing, status_code=404, code="NOT_FOUND")
    assert guessed.json()["error"]["message"] == missing.json()["error"]["message"]


@pytest.mark.integration
def test_proxy_that_has_not_been_produced_yet_answers_like_a_missing_project(
    engine: Engine, clean_database: None
) -> None:
    """Absence is one answer: a reviewer learns there is nothing to play, and no more."""
    del clean_database
    clock = Clock(NOW)
    store = FakeObjectStore(now=clock)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), object_store=store)
    browser = Browser(app)
    sign_in(browser, flow)
    fixture = _project_without_proxy(engine, browser)

    unproduced = browser.get(_path(fixture))
    missing = browser.get(f"/api/v1/projects/{uuid4()}/proxy?workspace_id={fixture.workspace_id}")

    assert_error(unproduced, status_code=404, code="NOT_FOUND")
    assert unproduced.json()["error"]["message"] == missing.json()["error"]["message"]


@pytest.mark.integration
def test_proxy_of_a_deleted_project_is_refused(engine: Engine, clean_database: None) -> None:
    """A soft-deleted Project stops being playable the moment it is deleted."""
    del clean_database
    clock = Clock(NOW)
    store = FakeObjectStore(now=clock)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), object_store=store)
    browser = Browser(app)
    sign_in(browser, flow)
    fixture = _project_with_proxy(engine, browser, store)
    with engine.begin() as connection:
        connection.execute(
            update(Project).where(Project.id == fixture.project_id).values(archived_at=NOW)
        )

    response = browser.get(_path(fixture))

    assert_error(response, status_code=404, code="NOT_FOUND")


@pytest.mark.integration
def test_proxy_refuses_a_caller_without_a_session(engine: Engine, clean_database: None) -> None:
    """Signed media is never handed to an anonymous caller."""
    del clean_database
    clock = Clock(NOW)
    store = FakeObjectStore(now=clock)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), object_store=store)
    browser = Browser(app)
    sign_in(browser, flow)
    fixture = _project_with_proxy(engine, browser, store)

    anonymous = Browser(app)
    response = anonymous.get(_path(fixture))

    assert_error(response, status_code=401, code="UNAUTHENTICATED")


@pytest.mark.unit
def test_proxy_openapi_declares_a_strict_response_schema() -> None:
    """A generated client needs the real playback fields, not an arbitrary dictionary."""
    clock = Clock(NOW)
    app, _, _ = build_app(clock, StubGoogleProvider(clock))
    operation = app.openapi()["paths"]["/api/v1/projects/{project_id}/proxy"]["get"]

    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]

    assert schema["$ref"].endswith("/ProxyPlaybackResponse")


def _project_with_proxy(
    engine: Engine, browser: Browser, store: FakeObjectStore, *, key: str = "proxy-project"
) -> ProjectFixture:
    """Create one ready Project whose ingest already produced a proxy rendition."""
    fixture = _project_without_proxy(engine, browser, key=key)
    proxy_key = f"workspaces/{fixture.workspace_id}/projects/{fixture.project_id}/derived/proxy.mp4"
    with engine.begin() as connection:
        connection.execute(
            Asset.__table__.insert().values(
                id=uuid4(),
                workspace_id=fixture.workspace_id,
                project_id=fixture.project_id,
                kind=AssetKind.PROXY,
                source_type=AssetSourceType.DERIVED,
                storage_key=proxy_key,
                content_type="video/mp4",
                size_bytes=2_048,
                duration_ms=60_000,
                width=1280,
                height=720,
                sha256=b"p" * 32,
            )
        )
    store.objects[proxy_key] = StoredObject(
        key=proxy_key, content_type="video/mp4", content_length=2_048, sha256=None
    )
    return ProjectFixture(
        workspace_id=fixture.workspace_id,
        project_id=fixture.project_id,
        proxy_key=proxy_key,
    )


def _project_without_proxy(
    engine: Engine, browser: Browser, *, key: str = "proxy-project"
) -> ProjectFixture:
    """Create one ready Project that ingest has not produced a proxy for."""
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
    return ProjectFixture(workspace_id=workspace_id, project_id=project_id, proxy_key="")


def _path(fixture: ProjectFixture) -> str:
    """Build the proxy playback URL for one selected Workspace."""
    return f"/api/v1/projects/{fixture.project_id}/proxy?workspace_id={fixture.workspace_id}"
