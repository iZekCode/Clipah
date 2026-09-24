"""Integration contracts for uploading a picture into a Project and marking a clip with it."""

from __future__ import annotations

import io
from typing import Any
from uuid import UUID, uuid4

import pytest
from PIL import Image
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from clipah.assets.pictures import MAX_PICTURE_BYTES
from clipah.models import Asset, AssetKind, AssetSourceType
from integration.test_render_pipeline import Stage, _staged

pytestmark = pytest.mark.integration


def _logo() -> bytes:
    """A small cut-out logo, as a browser would send the file a member picked."""
    buffer = io.BytesIO()
    Image.new("RGBA", (64, 32), (198, 255, 61, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _upload(stage: Stage, data: bytes, project_id: UUID | None = None) -> Any:
    """Upload one picture into the staged Project, or into another one named."""
    return stage.browser.request(
        "POST",
        f"/api/v1/projects/{project_id or stage.project_id}/pictures"
        f"?workspace_id={stage.workspace_id}",
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )


def test_an_uploaded_picture_is_kept_and_offered_to_the_editor(engine: Engine) -> None:
    """A fresh Project has no stills, so without an upload the watermark has nothing to use."""
    stage = _staged(engine)

    uploaded = _upload(stage, _logo())

    assert uploaded.status_code == 201, uploaded.json()
    body = uploaded.json()
    assert body["kind"] == "picture"
    assert body["contentType"] == "image/png"
    assert (body["width"], body["height"]) == (64, 32)
    with Session(engine) as session:
        asset = session.scalars(select(Asset).where(Asset.id == UUID(body["id"]))).one()
    assert asset.source_type is AssetSourceType.USER_UPLOAD
    assert asset.kind is AssetKind.PICTURE
    stored = stage.store.objects[asset.storage_key]
    assert stored.content_type == "image/png"
    assert stored.sha256 == asset.sha256
    offered = stage.browser.get(
        f"/api/v1/projects/{stage.project_id}/assets?workspace_id={stage.workspace_id}"
    ).json()["assets"]
    assert body["id"] in {item["id"] for item in offered}
    signed = stage.browser.get(
        f"/api/v1/assets/{body['id']}/preview-url?workspace_id={stage.workspace_id}"
    )
    assert signed.status_code == 200


def test_a_clip_can_be_saved_with_the_uploaded_picture_as_its_watermark(engine: Engine) -> None:
    """The picture belongs to the Project, so a composition may name it."""
    stage = _staged(engine)
    picture_id = _upload(stage, _logo()).json()["id"]
    edit = stage.browser.get(f"/api/v1/edits/{stage.edit_id}?workspace_id={stage.workspace_id}")
    document = edit.json()["composition"]
    document["watermark"] = {
        "kind": "image",
        "position": "topLeft",
        "size": 0.2,
        "opacity": 1,
        "assetId": picture_id,
    }

    saved = stage.browser.request(
        "PUT",
        f"/api/v1/edits/{stage.edit_id}?workspace_id={stage.workspace_id}",
        json={"expectedRevision": edit.json()["currentRevision"], "composition": document},
    )

    assert saved.status_code == 200, saved.json()
    assert saved.json()["composition"]["watermark"]["assetId"] == picture_id


def test_a_file_that_is_not_a_picture_is_refused_and_nothing_is_kept(engine: Engine) -> None:
    """A refused upload must leave neither an Asset row nor an object behind."""
    stage = _staged(engine)
    before = set(stage.store.objects)

    refused = _upload(stage, b"GIF89a but not really")

    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "PICTURE_INVALID"
    assert set(stage.store.objects) == before
    with Session(engine) as session:
        kinds = session.scalars(select(Asset.kind)).all()
    assert AssetKind.PICTURE not in kinds


def test_a_picture_over_the_byte_bound_is_refused(engine: Engine) -> None:
    """The body is counted as it arrives, whatever length the client declared."""
    stage = _staged(engine)

    refused = _upload(stage, b"\0" * (MAX_PICTURE_BYTES + 1))

    assert refused.status_code == 413
    assert refused.json()["error"]["code"] == "PICTURE_TOO_LARGE"


def test_an_upload_into_a_project_the_caller_cannot_see_reads_as_missing(engine: Engine) -> None:
    """A guessed Project identifier teaches nothing, even to someone with a picture."""
    stage = _staged(engine)

    missing = _upload(stage, _logo(), project_id=uuid4())

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


def test_an_upload_without_csrf_proof_is_refused(engine: Engine) -> None:
    """Uploading changes state, so it needs the same proof every other write does."""
    stage = _staged(engine)

    refused = stage.browser.request(
        "POST",
        f"/api/v1/projects/{stage.project_id}/pictures?workspace_id={stage.workspace_id}",
        content=_logo(),
        csrf_token="",
        headers={"X-CSRF-Token": "wrong"},
    )

    assert refused.status_code == 403
