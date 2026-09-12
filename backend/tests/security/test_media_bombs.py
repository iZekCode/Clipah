"""Small files that cost a great deal to open.

A media bomb is a few hundred bytes that expands into gigabytes once something decodes it:
a PNG header declaring a 60,000-pixel square, a stream whose declared length is nothing
like what arrives. The defence is the same in both cases — believe the header only far
enough to refuse it, and count bytes while they arrive rather than afterwards.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Iterable
from pathlib import Path

import PIL.Image
import pytest

from clipah.assets.ingest import MAX_MEDIA_BYTES, write_download
from clipah.assets.probe import MediaValidationError
from clipah.jobs.broll_generate_task import MAX_IMAGE_PIXELS, _validated_image

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@pytest.mark.unit
@pytest.mark.parametrize("side", [60_000, int(MAX_IMAGE_PIXELS**0.5) + 1])
def test_a_tiny_file_declaring_an_enormous_canvas_is_refused(tmp_path: Path, side: int) -> None:
    """Decoding it would allocate gigabytes for a file that arrived in one packet.

    Which of the two guards refuses it is deliberately not asserted: a header-only bomb is
    refused while it is being decoded, and a structurally complete one is refused by the
    pixel ceiling. Both are refusals, and neither produces an Asset.
    """
    path = tmp_path / "bomb.png"
    path.write_bytes(_png_header(width=side, height=side))

    with pytest.raises(MediaValidationError):
        _validated_image(path)


@pytest.mark.unit
def test_an_ordinary_still_is_still_accepted(tmp_path: Path) -> None:
    """A guard that refused everything would pass every test above and ship nothing."""
    path = tmp_path / "ordinary.png"
    PIL.Image.new("RGB", (1080, 1920), color=(20, 20, 20)).save(path)

    assert _validated_image(path) == ("image/png", 1080, 1920)


@pytest.mark.unit
def test_an_image_that_is_not_an_image_at_all_is_refused(tmp_path: Path) -> None:
    """A provider's output is bytes until something proves what they are."""
    path = tmp_path / "not-an-image.png"
    path.write_bytes(b"<svg onload=alert(1)></svg>")

    with pytest.raises(MediaValidationError, match="decoded"):
        _validated_image(path)


@pytest.mark.unit
def test_a_stream_that_keeps_arriving_is_cut_off_at_the_ceiling(tmp_path: Path) -> None:
    """The ceiling is counted while bytes arrive, so an endless response cannot fill a disk."""
    written = 0

    def endless() -> Iterable[bytes]:
        nonlocal written
        while True:
            written += 1_024
            yield b"\x00" * 1_024

    with (
        (tmp_path / "source").open("wb") as destination,
        pytest.raises(MediaValidationError, match="ASSET_TOO_LARGE"),
    ):
        write_download(
            endless(),
            destination,
            expected_size=0,
            max_bytes=8_192,
            cancellation_check=lambda: None,
        )

    assert written <= 8_192 + 1_024
    assert (tmp_path / "source").stat().st_size <= 8_192 + 1_024


@pytest.mark.unit
def test_the_media_ceiling_is_the_two_gibibytes_the_plan_states() -> None:
    """Every caller inherits this number, so it is worth stating once in a test."""
    assert MAX_MEDIA_BYTES == 2 * 1024 * 1024 * 1024


def _png_header(*, width: int, height: int) -> bytes:
    """Write a structurally valid PNG that only ever declares its enormous size."""
    header = struct.pack(">II", width, height) + bytes([8, 2, 0, 0, 0])
    chunk = struct.pack(">I", len(header)) + b"IHDR" + header
    return PNG_SIGNATURE + chunk + struct.pack(">I", zlib.crc32(b"IHDR" + header))
