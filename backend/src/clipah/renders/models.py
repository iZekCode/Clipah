"""Render presets and the plan one composition compiles into.

A render plan is a complete description of one FFmpeg invocation: which files are read,
which filter graph is applied, which text files that graph reads, and which arguments the
encoder is given. It carries no shell string, no user text in an argument, and no path
outside the job workspace it was compiled for — those are properties the compiler is
tested on, because they are what makes running FFmpeg over member-supplied text safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from clipah.models import AssetKind


# Section 9's four export presets, named by the frame they produce.
class RenderPreset(StrEnum):
    """The export presets a member may render a clip into."""

    PORTRAIT = "1080x1920"
    LANDSCAPE = "1920x1080"
    SQUARE = "1080x1080"
    VERTICAL = "1080x1350"


PRESET_CANVAS: dict[RenderPreset, tuple[int, int]] = {
    RenderPreset.PORTRAIT: (1080, 1920),
    RenderPreset.LANDSCAPE: (1920, 1080),
    RenderPreset.SQUARE: (1080, 1080),
    RenderPreset.VERTICAL: (1080, 1350),
}

RENDER_FRAME_RATE = 30
RENDER_AUDIO_SAMPLE_RATE = 48_000
# How far a rendered file may drift from the duration the composition promised.
RENDER_DURATION_TOLERANCE_MS = 250

ASSET_MISSING = "RENDER_ASSET_MISSING"
FEATURE_UNSUPPORTED = "RENDER_FEATURE_UNSUPPORTED"
TEMPLATE_UNKNOWN = "RENDER_TEMPLATE_UNKNOWN"
DURATION_MISMATCH = "RENDER_DURATION_MISMATCH"
RENDER_FAILED = "RENDER_FAILED"


class RenderCompilationError(Exception):
    """A composition that this renderer cannot turn into a faithful export."""

    def __init__(self, code: str, detail: str) -> None:
        """Carry a stable public code and an internal-only explanation."""
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class RenderAsset:
    """One asset the compiler may place, described without a storage key or a URL."""

    asset_id: UUID
    kind: AssetKind
    content_type: str
    duration_ms: int | None
    width: int | None
    height: int | None

    @property
    def is_image(self) -> bool:
        """Whether this asset is a still that has to be looped to occupy time."""
        return self.content_type.startswith("image/")


@dataclass(frozen=True, slots=True)
class RenderInput:
    """One file FFmpeg opens, and the arguments that precede it."""

    asset_id: UUID
    path: Path
    loop_image: bool
    duration_ms: int | None

    def arguments(self) -> tuple[str, ...]:
        """Return this input's own argument vector, in the order FFmpeg reads it."""
        prefix: tuple[str, ...] = ()
        if self.loop_image:
            seconds = f"{(self.duration_ms or 0) / 1000:.3f}"
            prefix = ("-loop", "1", "-t", seconds)
        return (*prefix, "-i", str(self.path))


@dataclass(frozen=True, slots=True)
class RenderFile:
    """One UTF-8 file the filter graph reads instead of carrying text in an argument."""

    path: Path
    contents: str


@dataclass(frozen=True, slots=True)
class Watermark:
    """The brand mark a plan may burn into the corner of an export."""

    text: str
    font_size: int = 28


@dataclass(frozen=True, slots=True)
class RenderPlan:
    """Everything one export needs, resolved before a single process is started."""

    preset: RenderPreset
    width: int
    height: int
    frame_rate: int
    duration_ms: int
    inputs: tuple[RenderInput, ...]
    filter_script: str
    files: tuple[RenderFile, ...]
    video_label: str
    audio_label: str

    def paths(self) -> tuple[Path, ...]:
        """Every path this plan names, so a caller can prove where they all live."""
        return (*(entry.path for entry in self.inputs), *(entry.path for entry in self.files))


@dataclass(frozen=True, slots=True)
class RenderOutput:
    """One finished export, as the worker persists it."""

    path: Path
    duration_ms: int
    size_bytes: int
    sha256: bytes
