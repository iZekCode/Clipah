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
from clipah.models import Asset, AssetKind, AssetSourceType, RetentionTombstone
from clipah.retention.policy import RetentionEntityKind
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


def _mark_with(stage: Stage, picture_id: str | None) -> Any:
    """Save the staged clip with the picture as its watermark, or with Clipah's mark."""
    edit = stage.browser.get(f"/api/v1/edits/{stage.edit_id}?workspace_id={stage.workspace_id}")
    document = edit.json()["composition"]
    document["watermark"] = (
        {"kind": "clipah", "position": "bottomRight", "size": 0.2, "opacity": 0.9}
        if picture_id is None
        else {
            "kind": "image",
            "position": "topLeft",
            "size": 0.2,
            "opacity": 1,
            "assetId": picture_id,
        }
    )
    return stage.browser.request(
        "PUT",
        f"/api/v1/edits/{stage.edit_id}?workspace_id={stage.workspace_id}",
        json={"expectedRevision": edit.json()["currentRevision"], "composition": document},
    )


def _remove(stage: Stage, picture_id: str) -> Any:
    """Remove one uploaded picture from the staged Project."""
    return stage.browser.request(
        "DELETE",
        f"/api/v1/projects/{stage.project_id}/pictures/{picture_id}"
        f"?workspace_id={stage.workspace_id}",
    )


def test_a_clip_can_be_saved_with_the_uploaded_picture_as_its_watermark(engine: Engine) -> None:
    """The picture belongs to the Project, so a composition may name it."""
    stage = _staged(engine)
    picture_id = _upload(stage, _logo()).json()["id"]

    saved = _mark_with(stage, picture_id)

    assert saved.status_code == 200, saved.json()
    assert saved.json()["composition"]["watermark"]["assetId"] == picture_id


def test_a_removed_picture_is_no_longer_offered_signed_or_usable(engine: Engine) -> None:
    """Removal is the member's word that the picture is gone, everywhere at once."""
    stage = _staged(engine)
    picture = _upload(stage, _logo()).json()

    removed = _remove(stage, picture["id"])

    assert removed.status_code == 204
    offered = stage.browser.get(
        f"/api/v1/projects/{stage.project_id}/assets?workspace_id={stage.workspace_id}"
    ).json()["assets"]
    assert picture["id"] not in {item["id"] for item in offered}
    listed = stage.browser.get(f"/api/v1/assets?workspace_id={stage.workspace_id}").json()
    assert picture["id"] not in {item["id"] for item in listed["assets"]}
    signed = stage.browser.get(
        f"/api/v1/assets/{picture['id']}/preview-url?workspace_id={stage.workspace_id}"
    )
    assert signed.status_code == 404
    refused = _mark_with(stage, picture["id"])
    assert refused.json()["error"]["code"] == "COMPOSITION_ASSET_FORBIDDEN"
    assert _remove(stage, picture["id"]).status_code == 404


def test_removal_schedules_exactly_the_pictures_bytes_for_deletion(engine: Engine) -> None:
    """Retention may delete the one object the picture was, and nothing beside it."""
    stage = _staged(engine)
    picture_id = UUID(_upload(stage, _logo()).json()["id"])

    _remove(stage, str(picture_id))

    with Session(engine) as session:
        key = session.scalars(select(Asset.storage_key).where(Asset.id == picture_id)).one()
        tombstone = session.scalars(
            select(RetentionTombstone).where(RetentionTombstone.entity_id == picture_id)
        ).one()
    assert tombstone.entity_kind == RetentionEntityKind.REMOVED_PICTURE.value
    assert tombstone.storage_prefix == key
    assert tombstone.deleted_at is None


def test_a_picture_a_clip_still_draws_cannot_be_removed(engine: Engine) -> None:
    """Removing it would leave that clip naming media that is gone."""
    stage = _staged(engine)
    picture_id = _upload(stage, _logo()).json()["id"]
    assert _mark_with(stage, picture_id).status_code == 200

    refused = _remove(stage, picture_id)

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "PICTURE_IN_USE"
    assert _mark_with(stage, None).status_code == 200
    assert _remove(stage, picture_id).status_code == 204


def test_only_an_uploaded_picture_can_be_removed_this_way(engine: Engine) -> None:
    """The source video, or media the pipeline made, is not a picture a member uploaded."""
    stage = _staged(engine)

    refused = _remove(stage, str(stage.source_asset_id))

    assert refused.status_code == 404


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
