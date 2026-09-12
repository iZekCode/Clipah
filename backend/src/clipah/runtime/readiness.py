"""Fail-closed native capability checks shared by media worker images."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol

from clipah.assets.ffmpeg import MediaProcessError, SubprocessExecutor

EXPECTED_MEDIA_VERSION = "7.1.5"
MAX_READINESS_OUTPUT_BYTES = 256 * 1024
READINESS_TIMEOUT_SECONDS = 20.0
REQUIRED_ENCODERS = frozenset({"libx264", "aac"})
REQUIRED_FILTERS = frozenset({"subtitles", "drawtext", "zoompan"})
PINNED_FONT_FAMILY = "Noto Sans"


class RuntimeReadinessError(Exception):
    """A deployed process lacks one reviewed runtime capability."""


class ProbeRunner(Protocol):
    """Run one fixed readiness probe without shell interpretation."""

    def run(self, arguments: Sequence[str]) -> str:
        """Return bounded standard output for one exact argument vector."""
        ...


class SubprocessProbeRunner:
    """Adapt the bounded media subprocess runner to startup probes."""

    def __init__(self, executor: SubprocessExecutor | None = None) -> None:
        """Use the hardened process-group runner unless a test supplies one."""
        self._executor = executor or SubprocessExecutor()

    def run(self, arguments: Sequence[str]) -> str:
        """Execute one probe with a fixed timeout and bounded decoded output."""
        output = self._executor.run(
            arguments,
            timeout_seconds=READINESS_TIMEOUT_SECONDS,
            cancellation_check=lambda: None,
        )
        if len(output) > MAX_READINESS_OUTPUT_BYTES:
            raise RuntimeReadinessError("media runtime unavailable")
        return output.decode("utf-8", errors="strict")


class MediaCapabilityVerifier:
    """Verify the exact native runtime required for ingest and rendering."""

    def __init__(self, *, runner: ProbeRunner | None = None) -> None:
        """Bind an injectable shell-free probe runner."""
        self._runner = runner or SubprocessProbeRunner()

    def verify(self) -> None:
        """Reject version, encoder, filter, or font drift before accepting work."""
        try:
            ffmpeg_version = self._runner.run(("ffmpeg", "-version"))
            ffprobe_version = self._runner.run(("ffprobe", "-version"))
            encoders = self._runner.run(("ffmpeg", "-hide_banner", "-encoders"))
            filters = self._runner.run(("ffmpeg", "-hide_banner", "-filters"))
            fonts = self._runner.run(("fc-match", "--format", "%{family}\n", PINNED_FONT_FAMILY))
        except (KeyError, MediaProcessError, OSError, UnicodeError) as error:
            raise RuntimeReadinessError("media runtime unavailable") from error

        expected_ffmpeg = f"ffmpeg version {EXPECTED_MEDIA_VERSION}"
        expected_ffprobe = f"ffprobe version {EXPECTED_MEDIA_VERSION}"
        observed_encoders = _listed_names(encoders)
        observed_filters = _listed_names(filters)
        observed_fonts = {family.strip() for family in fonts.split(",")}
        if (
            not ffmpeg_version.startswith(expected_ffmpeg)
            or not ffprobe_version.startswith(expected_ffprobe)
            or not observed_encoders >= REQUIRED_ENCODERS
            or not observed_filters >= REQUIRED_FILTERS
            or PINNED_FONT_FAMILY not in observed_fonts
        ):
            raise RuntimeReadinessError("media runtime unavailable")


@lru_cache(maxsize=1)
def validate_media_runtime() -> None:
    """Verify one process's immutable media runtime at most once."""
    MediaCapabilityVerifier().verify()


def _listed_names(output: str) -> set[str]:
    """Extract capability names from FFmpeg's stable column-oriented listings."""
    names: set[str] = set()
    for line in output.splitlines():
        columns = line.split()
        if len(columns) >= 2:
            names.add(columns[1])
    return names


def main(arguments: Sequence[str] | None = None) -> int:
    """Run one named readiness profile without printing inspected configuration."""
    selected = tuple(arguments if arguments is not None else sys.argv[1:])
    if selected != ("media",):
        raise RuntimeReadinessError("runtime readiness profile unavailable")
    validate_media_runtime()
    print("ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
