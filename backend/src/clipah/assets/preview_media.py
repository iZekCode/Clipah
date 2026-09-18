"""Previews a creator sees before pressing play: storyboard sheets and waveform peaks.

A storyboard is a grid of small frames taken from the proxy at a fixed interval, so any
moment of a source can be drawn from one cached image by offset. Waveform peaks are one byte
per fixed window of the transcription audio. Both are versioned: the geometry lives here, is
named in every storage key, and changes only by adding a new version.
"""

from __future__ import annotations

import re
import sys
import wave
from array import array
from dataclasses import dataclass
from typing import BinaryIO

WAVEFORM_V1_NAME = "waveform-v1.bin"
WAVEFORM_PEAKS_PER_SECOND = 20
_SAMPLE_WIDTH_BYTES = 2
_INGEST_SAMPLE_RATE = 16_000
_FULL_SCALE = 32_767
_SHEET_KEY = re.compile(r"storyboard-v1/sheet-(\d{4})\.jpg$")


class WaveformInputError(ValueError):
    """The transcription audio is not the mono 16-bit PCM WAV ingest produces."""


@dataclass(frozen=True, slots=True)
class StoryboardPolicy:
    """One immutable storyboard geometry."""

    version: int
    interval_ms: int
    columns: int
    rows: int
    long_side_px: int

    @property
    def frames_per_sheet(self) -> int:
        """How many tiles one sheet holds."""
        return self.columns * self.rows


STORYBOARD_V1 = StoryboardPolicy(
    version=1, interval_ms=2_000, columns=10, rows=10, long_side_px=160
)


@dataclass(frozen=True, slots=True)
class StoryboardLayout:
    """Where every frame of one source lands across its storyboard sheets."""

    policy: StoryboardPolicy
    duration_ms: int
    tile_width: int
    tile_height: int
    frame_count: int
    sheet_count: int

    def sheet_start_ms(self, index: int) -> int:
        """The source time of the first tile on one sheet."""
        return index * self.policy.frames_per_sheet * self.policy.interval_ms

    def sheet_tile_count(self, index: int) -> int:
        """How many tiles on one sheet hold a real frame."""
        remaining = self.frame_count - index * self.policy.frames_per_sheet
        return max(0, min(self.policy.frames_per_sheet, remaining))

    def sheet_duration_ms(self, index: int) -> int:
        """How much source time one sheet covers."""
        span = self.policy.frames_per_sheet * self.policy.interval_ms
        return max(0, min(span, self.duration_ms - self.sheet_start_ms(index)))


def storyboard_layout(
    *, duration_ms: int, width: int, height: int, policy: StoryboardPolicy = STORYBOARD_V1
) -> StoryboardLayout:
    """Lay out one source's storyboard from its proxy duration and frame size."""
    if duration_ms <= 0 or width <= 0 or height <= 0:
        raise ValueError("a storyboard needs a positive duration and frame size")
    tile_width, tile_height = tile_size(width=width, height=height, long_side=policy.long_side_px)
    frame_count = -(-duration_ms // policy.interval_ms)
    sheet_count = -(-frame_count // policy.frames_per_sheet)
    return StoryboardLayout(
        policy=policy,
        duration_ms=duration_ms,
        tile_width=tile_width,
        tile_height=tile_height,
        frame_count=frame_count,
        sheet_count=sheet_count,
    )


def tile_size(*, width: int, height: int, long_side: int) -> tuple[int, int]:
    """Scale a frame so its longer side is `long_side`, keeping both sides even."""
    if width >= height:
        return long_side, _even(height * long_side, width)
    return _even(width * long_side, height), long_side


def storyboard_sheet_name(index: int) -> str:
    """The version-one storage name of one sheet."""
    if not 0 <= index <= 9_999:
        raise ValueError("a storyboard sheet index must fit four digits")
    return f"storyboard-v1/sheet-{index:04d}.jpg"


def storyboard_sheet_index(storage_key: str) -> int | None:
    """Recover a sheet's index from its storage key, or nothing for any other object."""
    match = _SHEET_KEY.search(storage_key)
    return None if match is None else int(match.group(1))


def waveform_peaks(source: BinaryIO, *, peaks_per_second: int = WAVEFORM_PEAKS_PER_SECOND) -> bytes:
    """Read mono 16-bit 16 kHz PCM and return one loudness byte per window, in order."""
    try:
        with wave.open(source, "rb") as reader:
            return _read_peaks(reader, peaks_per_second)
    except (wave.Error, EOFError) as error:
        raise WaveformInputError("the audio is not a readable WAV file") from error


def _read_peaks(reader: wave.Wave_read, peaks_per_second: int) -> bytes:
    """Fold every window of samples into the loudest absolute value, scaled to one byte."""
    if reader.getnchannels() != 1 or reader.getsampwidth() != _SAMPLE_WIDTH_BYTES:
        raise WaveformInputError("expected mono 16-bit PCM audio")
    # Any other rate is not the transcription audio ingest wrote, and would draw the
    # waveform at the wrong speed even when it happens to divide into whole windows.
    if reader.getframerate() != _INGEST_SAMPLE_RATE or _INGEST_SAMPLE_RATE % peaks_per_second:
        raise WaveformInputError("expected the 16 kHz transcription audio")
    window = _INGEST_SAMPLE_RATE // peaks_per_second
    peaks = bytearray()
    while frames := reader.readframes(window):
        samples = array("h")
        samples.frombytes(frames[: len(frames) - len(frames) % _SAMPLE_WIDTH_BYTES])
        if sys.byteorder == "big":
            samples.byteswap()
        loudest = max(max(samples, default=0), -min(samples, default=0))
        peaks.append(min(255, (loudest * 255 + _FULL_SCALE // 2) // _FULL_SCALE))
    return bytes(peaks)


def _even(numerator: int, denominator: int) -> int:
    """Round `numerator / denominator` to the nearest even integer, never below two."""
    return max(2, 2 * ((numerator + denominator) // (2 * denominator)))
