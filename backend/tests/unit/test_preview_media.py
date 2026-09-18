"""Unit contracts for storyboard geometry and waveform peaks.

A poster, a filmstrip, and a timeline waveform are only honest if every tile and every peak
maps to a known moment of the source. These tests pin that mapping.
"""

from __future__ import annotations

import io
import struct
import wave

import pytest

from clipah.assets.preview_media import (
    STORYBOARD_V1,
    WaveformInputError,
    storyboard_layout,
    storyboard_sheet_index,
    storyboard_sheet_name,
    tile_size,
    waveform_peaks,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (1280, 720, (160, 90)),
        (720, 1280, (90, 160)),
        (1080, 1080, (160, 160)),
        (640, 480, (160, 120)),
        (1920, 20, (160, 2)),
    ],
)
def test_a_tile_keeps_the_proxy_shape_with_its_long_side_at_160_pixels(
    width: int, height: int, expected: tuple[int, int]
) -> None:
    """Tiles cropped to the wrong shape would stretch every poster drawn from them."""
    assert tile_size(width=width, height=height, long_side=160) == expected


@pytest.mark.unit
def test_a_layout_counts_one_frame_every_two_seconds_and_one_sheet_per_hundred_frames() -> None:
    """A twelve-minute source needs 362 frames across four sheets, the last one partial."""
    layout = storyboard_layout(duration_ms=722_588, width=1280, height=720)

    assert layout.frame_count == 362
    assert layout.sheet_count == 4
    assert (layout.tile_width, layout.tile_height) == (160, 90)
    assert [layout.sheet_start_ms(index) for index in range(4)] == [0, 200_000, 400_000, 600_000]
    assert [layout.sheet_tile_count(index) for index in range(4)] == [100, 100, 100, 62]
    assert layout.sheet_duration_ms(3) == 122_588


@pytest.mark.unit
def test_a_source_exactly_one_sheet_long_needs_exactly_one_sheet() -> None:
    """The frame at the very end of a source does not exist, so it must not open a sheet."""
    layout = storyboard_layout(duration_ms=200_000, width=1280, height=720)

    assert layout.frame_count == 100
    assert layout.sheet_count == 1


@pytest.mark.unit
def test_a_layout_refuses_an_empty_source() -> None:
    """Zero duration or size is a broken proxy, not a zero-tile storyboard."""
    with pytest.raises(ValueError):
        storyboard_layout(duration_ms=0, width=1280, height=720)


@pytest.mark.unit
def test_sheet_names_round_trip_through_storage_keys() -> None:
    """The read endpoint recovers each sheet's position from its key alone."""
    key = f"workspaces/w/projects/p/derived/s/{storyboard_sheet_name(7)}"

    assert storyboard_sheet_name(7) == "storyboard-v1/sheet-0007.jpg"
    assert storyboard_sheet_index(key) == 7
    assert storyboard_sheet_index("workspaces/w/projects/p/derived/s/thumbnail") is None
    with pytest.raises(ValueError):
        storyboard_sheet_name(10_000)


def _wav(
    samples: list[int], *, rate: int = 16_000, channels: int = 1, width: int = 2
) -> io.BytesIO:
    frames = struct.pack(f"<{len(samples)}h", *samples) if width == 2 else bytes(len(samples))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        writer.writeframes(frames)
    buffer.seek(0)
    return buffer


@pytest.mark.unit
def test_peaks_are_one_byte_per_fifty_milliseconds_scaled_to_the_loudest_sample() -> None:
    """Silence, full scale, and a partial final window each map to a predictable byte."""
    silence = [0] * 800
    loud = [0] * 799 + [-32_768]
    half = [16_384] * 400

    peaks = waveform_peaks(_wav(silence + loud + half))

    assert peaks == bytes([0, 255, 128])


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rate", "channels", "width"),
    [(16_000, 2, 2), (16_000, 1, 1), (44_100, 1, 2)],
)
def test_peaks_refuse_audio_that_is_not_the_mono_16_bit_ingest_output(
    rate: int, channels: int, width: int
) -> None:
    """Silently accepting another layout would draw a waveform at the wrong speed."""
    with pytest.raises(WaveformInputError):
        waveform_peaks(_wav([0] * 800, rate=rate, channels=channels, width=width))


@pytest.mark.unit
def test_peaks_refuse_bytes_that_are_not_a_wav_file() -> None:
    """A corrupted artifact is a terminal input problem, not an empty waveform."""
    with pytest.raises(WaveformInputError):
        waveform_peaks(io.BytesIO(b"not audio"))


@pytest.mark.unit
def test_the_version_one_policy_is_the_one_the_spec_names() -> None:
    """Changing geometry must be a deliberate new version, never an edit to this one."""
    assert (STORYBOARD_V1.interval_ms, STORYBOARD_V1.columns, STORYBOARD_V1.rows) == (2_000, 10, 10)
    assert STORYBOARD_V1.long_side_px == 160
    assert STORYBOARD_V1.frames_per_sheet == 100
