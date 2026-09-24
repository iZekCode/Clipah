"""A member's own still, such as a logo, uploaded into one Project to mark its clips with.

The browser's bytes are never stored as they arrive. They are decoded, checked against a
fixed bound, and re-encoded as a PNG, so what the renderer and the cover draw from is an
image the server made — not a file whose declared type, metadata, or trailing bytes a
member chose.
"""

from __future__ import annotations

import hashlib
import io
import warnings
from dataclasses import dataclass
from uuid import UUID, uuid4

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.assets.keys import picture_asset_key
from clipah.assets.library import LibraryAsset, ProjectNotFoundError
from clipah.assets.storage import ObjectStore
from clipah.models import Asset, AssetKind, AssetSourceType, Project
from clipah.workspaces.models import WorkspaceAccess

MAX_PICTURE_BYTES = 5 * 1024 * 1024
MAX_PICTURE_SIDE = 4096
PICTURE_CONTENT_TYPE = "image/png"
_ACCEPTED_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})


class PictureInvalidError(Exception):
    """The upload is not a still this Project can hold."""


class PictureTooLargeError(Exception):
    """The upload is more bytes than a picture may be."""


@dataclass(frozen=True, slots=True)
class DecodedPicture:
    """One upload re-encoded as the PNG the server stores, with its measured shape."""

    png: bytes
    width: int
    height: int


def decode_picture(data: bytes) -> DecodedPicture:
    """Decode one upload within fixed bounds and re-encode it as an RGBA PNG.

    Only PNG, JPEG, and WebP are read. Anything larger than the bound on either side is
    refused before its pixels are decoded, and an animated image keeps its first frame.
    """
    if len(data) > MAX_PICTURE_BYTES:
        raise PictureTooLargeError
    if not data:
        raise PictureInvalidError
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=sorted(_ACCEPTED_FORMATS)) as opened:
                width, height = opened.size
                if not (0 < width <= MAX_PICTURE_SIDE and 0 < height <= MAX_PICTURE_SIDE):
                    raise PictureInvalidError
                opened.seek(0)
                picture = opened.convert("RGBA")
    except PictureInvalidError:
        raise
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        ValueError,
        SyntaxError,
    ) as error:
        raise PictureInvalidError from error
    encoded = io.BytesIO()
    picture.save(encoded, format="PNG", optimize=True)
    return DecodedPicture(png=encoded.getvalue(), width=width, height=height)


def store_picture(
    session: Session,
    store: ObjectStore,
    *,
    access: WorkspaceAccess,
    project_id: UUID,
    data: bytes,
) -> LibraryAsset:
    """Keep one uploaded picture as an Asset of an active Project the caller may write.

    A Project that is missing, archived, or another Workspace's ends the same way, so a
    guessed identifier teaches nothing; the bytes are decoded only after that is known.
    """
    exists = session.scalar(
        select(Project.id).where(
            Project.workspace_id == access.workspace_id,
            Project.id == project_id,
            Project.archived_at.is_(None),
        )
    )
    if exists is None:
        raise ProjectNotFoundError(str(project_id))
    picture = decode_picture(data)
    digest = hashlib.sha256(picture.png).digest()
    asset_id = uuid4()
    key = picture_asset_key(
        workspace_id=access.workspace_id, project_id=project_id, asset_id=asset_id
    )
    store.put_file(
        key=key,
        content_type=PICTURE_CONTENT_TYPE,
        file=io.BytesIO(picture.png),
        sha256=digest,
    )
    asset = Asset(
        id=asset_id,
        workspace_id=access.workspace_id,
        project_id=project_id,
        kind=AssetKind.PICTURE,
        source_type=AssetSourceType.USER_UPLOAD,
        storage_key=key,
        content_type=PICTURE_CONTENT_TYPE,
        size_bytes=len(picture.png),
        width=picture.width,
        height=picture.height,
        sha256=digest,
    )
    session.add(asset)
    session.flush()
    session.refresh(asset)
    return LibraryAsset(
        asset_id=asset.id,
        kind=asset.kind,
        content_type=asset.content_type,
        size_bytes=asset.size_bytes,
        duration_ms=None,
        width=asset.width,
        height=asset.height,
        created_at=asset.created_at,
    )
