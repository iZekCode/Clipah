"""What a caller may influence about where their bytes land, and for how long.

Object keys are the tenant boundary inside the bucket: a caller who can choose part of a
key can write into another Workspace's prefix, and a caller who learns a key can ask a
provider for it directly. So the rules this suite holds are that no value a client sends
reaches the key, that no response repeats the key or the provider's upload identifier, and
that every capability the API signs expires when the plan says it does.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text

from clipah.assets.keys import derived_asset_key, render_artifact_key, source_upload_key
from clipah.assets.storage import FakeObjectStore, StoredObject
from clipah.models import AssetKind
from harness import NOW, Browser, Clock, StubGoogleProvider, build_app, sign_in

# Each of these is a filename a member can legitimately type, and each one is a path the
# object store would honour if the key were ever built from it.
HOSTILE_FILENAMES: tuple[str, ...] = (
    "../../../etc/passwd",
    "..\\..\\windows\\system32\\config\\sam",
    "/absolute/elsewhere.mp4",
    "video.mp4/../../../another-workspace/source",
    "%2e%2e%2fescaped.mp4",
)


@pytest.mark.integration
@pytest.mark.parametrize("filename", HOSTILE_FILENAMES)
def test_no_part_of_a_client_filename_reaches_the_object_key(
    engine: Engine, clean_database: None, filename: str
) -> None:
    """The key is built from server-owned identifiers, so a filename cannot steer it."""
    del clean_database
    clock = Clock(NOW)
    store = FakeObjectStore(now=clock)
    browser, workspace_id, project_id = _project(clock, store)

    created = browser.request(
        "POST",
        f"/api/v1/projects/{project_id}/uploads?workspace_id={workspace_id}",
        json={"filename": filename, "contentType": "video/mp4", "contentLength": 1_024},
    )

    assert created.status_code == 201, created.text
    upload_id = UUID(created.json()["id"])
    key = _stored_key(engine, upload_id=upload_id)
    assert key == f"workspaces/{workspace_id}/projects/{project_id}/source/{upload_id}"
    assert key in store.upload_keys.values()


@pytest.mark.integration
@pytest.mark.parametrize("filename", ["coffee\x00.mp4", "talk\r\n.mp4", "talk\x07.mp4"])
def test_a_filename_carrying_control_characters_is_refused_rather_than_stored(
    engine: Engine, clean_database: None, filename: str
) -> None:
    """A filename is display metadata, and a control character has no display meaning.

    It does have meaning elsewhere: a NUL cannot be stored in Postgres text at all, and a
    newline is how a value is smuggled into a header or a log line. Refusing it here keeps
    the refusal a member's mistake rather than an unhandled server failure.
    """
    del engine, clean_database
    clock = Clock(NOW)
    store = FakeObjectStore(now=clock)
    browser, workspace_id, project_id = _project(clock, store)

    response = browser.request(
        "POST",
        f"/api/v1/projects/{project_id}/uploads?workspace_id={workspace_id}",
        json={"filename": filename, "contentType": "video/mp4", "contentLength": 1_024},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert store.upload_keys == {}


@pytest.mark.integration
def test_the_creating_response_repeats_neither_the_key_nor_the_provider_upload_identifier(
    engine: Engine, clean_database: None
) -> None:
    """A key or a provider upload id here is a capability the browser was not meant to hold.

    The signed part URL is exempt by construction: a presigned S3 part URL *is* the key
    and the provider's upload identifier, which is why it is issued one part at a time and
    expires in five minutes rather than being handed over as data.
    """
    del clean_database
    clock = Clock(NOW)
    store = FakeObjectStore(now=clock)
    browser, workspace_id, project_id = _project(clock, store)

    created = browser.request(
        "POST",
        f"/api/v1/projects/{project_id}/uploads?workspace_id={workspace_id}",
        json={"filename": "talk.mp4", "contentType": "video/mp4", "contentLength": 1_024},
    )
    upload_id = UUID(created.json()["id"])
    signed = browser.request(
        "POST",
        f"/api/v1/projects/{project_id}/uploads/{upload_id}/parts/1?workspace_id={workspace_id}",
    )

    assert created.json().keys() == {"id", "expiresAt"}
    assert _stored_key(engine, upload_id=upload_id) not in created.text
    assert _stored_provider_upload_id(engine, upload_id=upload_id) not in created.text
    assert signed.json().keys() == {"partNumber", "url", "expiresAt"}


@pytest.mark.integration
def test_one_upload_cannot_be_completed_with_another_uploads_parts(
    engine: Engine, clean_database: None
) -> None:
    """Parts are proof about one upload, so they must not be portable between uploads."""
    del clean_database
    clock = Clock(NOW)
    store = FakeObjectStore(now=clock)
    browser, workspace_id, project_id = _project(clock, store)
    first = _upload(browser, workspace_id=workspace_id, project_id=project_id, name="first.mp4")
    second = _upload(browser, workspace_id=workspace_id, project_id=project_id, name="second.mp4")
    browser.request(
        "POST",
        f"/api/v1/projects/{project_id}/uploads/{second}/parts/1?workspace_id={workspace_id}",
    )

    completed = browser.request(
        "POST",
        f"/api/v1/projects/{project_id}/uploads/{first}/complete?workspace_id={workspace_id}",
        json={"parts": [{"partNumber": 1, "etag": "an-etag-from-the-other-upload"}]},
    )

    assert completed.status_code in {404, 409, 422}, completed.text
    assert _stored_key(engine, upload_id=first) not in completed.text


@pytest.mark.integration
def test_a_signed_part_capability_lasts_exactly_the_five_minutes_it_promises(
    engine: Engine, clean_database: None
) -> None:
    """A capability that outlived its promise would still be live in a browser's history."""
    del engine, clean_database
    clock = Clock(NOW)
    store = FakeObjectStore(now=clock)
    browser, workspace_id, project_id = _project(clock, store)
    upload_id = _upload(browser, workspace_id=workspace_id, project_id=project_id, name="talk.mp4")

    signed = browser.request(
        "POST",
        f"/api/v1/projects/{project_id}/uploads/{upload_id}/parts/1?workspace_id={workspace_id}",
    )

    assert signed.status_code == 200, signed.text
    assert signed.json()["expiresAt"] == (clock() + timedelta(minutes=5)).isoformat()


@pytest.mark.integration
def test_a_proxy_capability_lasts_exactly_the_five_minutes_it_promises(
    engine: Engine, clean_database: None
) -> None:
    """Playback is signed for the same short window, and is re-signed rather than reused."""
    del clean_database
    clock = Clock(NOW)
    store = FakeObjectStore(now=clock)
    browser, workspace_id, project_id = _project(clock, store)
    _seed_proxy(engine, store, workspace_id=workspace_id, project_id=project_id)

    first = browser.get(f"/api/v1/projects/{project_id}/proxy?workspace_id={workspace_id}")
    clock.advance(timedelta(minutes=4))
    second = browser.get(f"/api/v1/projects/{project_id}/proxy?workspace_id={workspace_id}")

    assert first.json()["expiresAt"] == (NOW + timedelta(minutes=5)).isoformat()
    assert second.json()["expiresAt"] == (clock() + timedelta(minutes=5)).isoformat()
    assert second.json()["expiresAt"] != first.json()["expiresAt"]


@pytest.mark.unit
@pytest.mark.parametrize("filename", HOSTILE_FILENAMES)
def test_the_key_builder_itself_discards_the_filename_it_is_given(filename: str) -> None:
    """State the rule where it is decided, so no caller has to remember it."""
    workspace_id, project_id, object_id = uuid4(), uuid4(), uuid4()

    key = source_upload_key(
        workspace_id=workspace_id,
        project_id=project_id,
        object_id=object_id,
        client_filename=filename,
    )

    assert key == f"workspaces/{workspace_id}/projects/{project_id}/source/{object_id}"


@pytest.mark.unit
@pytest.mark.parametrize("preset", ["../../etc/passwd", "1080x1920/../..", "mp4;rm -rf /", ""])
def test_a_render_preset_that_is_not_a_frame_size_is_refused(preset: str) -> None:
    """The preset is the one non-UUID part of a key, so it is the one worth constraining."""
    with pytest.raises(ValueError, match="frame size"):
        render_artifact_key(
            workspace_id=uuid4(), project_id=uuid4(), revision_id=uuid4(), preset=preset
        )


@pytest.mark.unit
def test_a_key_builder_refuses_anything_that_is_not_an_identifier() -> None:
    """A string where a UUID belongs is the shape every key-injection attempt takes."""
    with pytest.raises(TypeError, match="UUID"):
        derived_asset_key(
            workspace_id="../another-workspace",  # type: ignore[arg-type]
            project_id=uuid4(),
            source_asset_id=uuid4(),
            kind=AssetKind.PROXY,
        )


def _project(clock: Clock, store: FakeObjectStore) -> tuple[Browser, UUID, UUID]:
    """Sign one member in and give them one active Project to upload into."""
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), object_store=store)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    created = browser.request(
        "POST",
        f"/api/v1/projects?workspace_id={workspace_id}",
        headers={"Idempotency-Key": "object-access"},
        json={"name": "Object access", "sourceKind": "upload"},
    )
    assert created.status_code == 201, created.text
    return browser, workspace_id, UUID(created.json()["id"])


def _upload(browser: Browser, *, workspace_id: UUID, project_id: UUID, name: str) -> UUID:
    """Create one pending upload and return the only identifier the browser is given."""
    created = browser.request(
        "POST",
        f"/api/v1/projects/{project_id}/uploads?workspace_id={workspace_id}",
        json={"filename": name, "contentType": "video/mp4", "contentLength": 1_024},
    )
    assert created.status_code == 201, created.text
    return UUID(created.json()["id"])


def _seed_proxy(
    engine: Engine, store: FakeObjectStore, *, workspace_id: UUID, project_id: UUID
) -> None:
    """Give the Project a proxy so a playback capability has something to sign."""
    key = f"workspaces/{workspace_id}/projects/{project_id}/derived/proxy"
    store.objects[key] = StoredObject(key=key, content_type="video/mp4", content_length=1_024)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO assets (
                    workspace_id, project_id, kind, source_type, storage_key,
                    content_type, size_bytes, duration_ms, width, height, sha256
                )
                VALUES (
                    :workspace_id, :project_id, 'proxy', 'derived', :key,
                    'video/mp4', 1024, 120000, 720, 1280, :sha256
                )
                """
            ),
            {
                "workspace_id": workspace_id,
                "project_id": project_id,
                "key": key,
                "sha256": b"p" * 32,
            },
        )


def _stored_key(engine: Engine, *, upload_id: UUID) -> str:
    """Read the key the server chose, which no client is ever shown."""
    return _stored_column(engine, upload_id=upload_id, column="storage_key")


def _stored_provider_upload_id(engine: Engine, *, upload_id: UUID) -> str:
    """Read the provider's own upload identifier, which no client is ever shown."""
    return _stored_column(engine, upload_id=upload_id, column="storage_upload_id")


def _stored_column(engine: Engine, *, upload_id: UUID, column: str) -> str:
    """Read one private column of one upload record."""
    with engine.connect() as connection:
        value: Any = connection.execute(
            text(f"SELECT {column} FROM multipart_uploads WHERE id = :id"), {"id": upload_id}
        ).scalar_one()
    return str(value)
