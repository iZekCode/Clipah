"""Unit contracts for decoding a member's uploaded picture into the PNG the server keeps."""

from __future__ import annotations

import io
from uuid import uuid4

import pytest
from PIL import Image

from clipah.assets.keys import picture_asset_key
from clipah.assets.pictures import (
    MAX_PICTURE_BYTES,
    MAX_PICTURE_SIDE,
    PictureInvalidError,
    PictureTooLargeError,
    decode_picture,
)

pytestmark = pytest.mark.unit


def _encoded(image_format: str, size: tuple[int, int] = (40, 20), mode: str = "RGB") -> bytes:
    """One small image in the given format, as a browser would send it."""
    buffer = io.BytesIO()
    Image.new(mode, size, 200 if mode == "L" else (200, 30, 30)).save(buffer, format=image_format)
    return buffer.getvalue()


@pytest.mark.parametrize("image_format", ["PNG", "JPEG", "WEBP"])
def test_a_common_still_is_kept_as_an_rgba_png_of_its_own_size(image_format: str) -> None:
    """Every accepted format ends as one PNG, so the renderer reads a single kind of file."""
    picture = decode_picture(_encoded(image_format))

    assert (picture.width, picture.height) == (40, 20)
    with Image.open(io.BytesIO(picture.png)) as stored:
        assert stored.format == "PNG"
        assert stored.mode == "RGBA"
        assert stored.size == (40, 20)


def test_transparency_survives_the_re_encoding() -> None:
    """A logo is usually cut out; losing its alpha would draw a box around it."""
    buffer = io.BytesIO()
    Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(buffer, format="PNG")

    picture = decode_picture(buffer.getvalue())

    with Image.open(io.BytesIO(picture.png)) as stored:
        assert stored.getpixel((0, 0)) == (0, 0, 0, 0)


@pytest.mark.parametrize(
    "data",
    [b"", b"not an image at all", _encoded("GIF"), _encoded("BMP"), _encoded("PNG")[:40]],
)
def test_anything_but_a_whole_png_jpeg_or_webp_is_refused(data: bytes) -> None:
    """Only formats the server decodes on purpose are read; a truncated file is not."""
    with pytest.raises(PictureInvalidError):
        decode_picture(data)


def test_a_picture_wider_than_the_bound_is_refused_before_it_is_kept() -> None:
    """A huge canvas costs memory to decode, and a watermark never needs one."""
    with pytest.raises(PictureInvalidError):
        decode_picture(_encoded("PNG", size=(MAX_PICTURE_SIDE + 1, 1), mode="L"))


def test_a_body_over_the_byte_bound_is_refused_unread() -> None:
    """The byte bound is checked before any decoder sees the upload."""
    with pytest.raises(PictureTooLargeError):
        decode_picture(b"\x89PNG" + b"\0" * MAX_PICTURE_BYTES)


def test_a_picture_is_stored_under_its_own_project_prefix() -> None:
    """The key is server-made from identifiers, never from a filename the browser sent."""
    workspace_id, project_id, asset_id = uuid4(), uuid4(), uuid4()

    key = picture_asset_key(workspace_id=workspace_id, project_id=project_id, asset_id=asset_id)

    assert key == f"workspaces/{workspace_id}/projects/{project_id}/pictures/{asset_id}.png"
    with pytest.raises(TypeError):
        picture_asset_key(workspace_id=str(workspace_id), project_id=project_id, asset_id=asset_id)  # type: ignore[arg-type]
