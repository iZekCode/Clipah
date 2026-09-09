"""Cancellable shell-free FFmpeg execution for provider-specific renditions."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from clipah.assets.ffmpeg import (
    FFMPEG_TIMEOUT_SECONDS,
    CancellationCheck,
    CommandExecutor,
    SubprocessExecutor,
)
from clipah.publishing.preflight import MediaFacts
from clipah.publishing.profiles import ProviderProfile

RENDITION_CONFIG_VERSION = "social-rendition-v1"
RENDITION_OUTPUT_NAME = "social-rendition.mp4"
HASH_CHUNK_BYTES = 1024 * 1024

RenditionProbe = Callable[[Path, CancellationCheck], MediaFacts]


@dataclass(frozen=True, slots=True)
class RenderedRendition:
    """One locally encoded result with measured facts and reproducibility evidence."""

    path: Path
    media: MediaFacts
    sha256: bytes
    ffmpeg_arguments: tuple[str, ...]
    config_version: str


def build_rendition_arguments(
    profile: ProviderProfile,
    *,
    source: Path,
    output: Path,
    ffmpeg_path: str = "ffmpeg",
) -> tuple[str, ...]:
    """Build one fixed argument vector containing no member-controlled text."""
    scale = (
        f"scale={profile.output_width}:{profile.output_height}:"
        "force_original_aspect_ratio=decrease,"
        f"pad={profile.output_width}:{profile.output_height}:(ow-iw)/2:(oh-ih)/2"
    )
    return (
        ffmpeg_path,
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(source),
        "-vf",
        scale,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(profile.output_frame_rate),
        "-b:v",
        profile.output_video_bitrate,
        "-c:a",
        "aac",
        "-b:a",
        profile.output_audio_bitrate,
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        "-y",
        str(output),
    )


class SocialRenditionRenderer:
    """Encode and measure one provider rendition inside a caller-owned Job workspace."""

    def __init__(
        self,
        *,
        probe: RenditionProbe,
        executor: CommandExecutor | None = None,
        ffmpeg_path: str = "ffmpeg",
        timeout_seconds: float = FFMPEG_TIMEOUT_SECONDS,
    ) -> None:
        """Bind the native boundaries while leaving time and cancellation injectable."""
        self._probe = probe
        self._executor = executor or SubprocessExecutor()
        self._ffmpeg_path = ffmpeg_path
        self._timeout_seconds = timeout_seconds

    def render(
        self,
        *,
        profile: ProviderProfile,
        source: Path,
        workspace: Path,
        cancellation_check: CancellationCheck,
    ) -> RenderedRendition:
        """Run one fixed transcode and return facts measured from its actual bytes."""
        cancellation_check()
        output = workspace / RENDITION_OUTPUT_NAME
        arguments = build_rendition_arguments(
            profile, source=source, output=output, ffmpeg_path=self._ffmpeg_path
        )
        self._executor.run(
            arguments,
            timeout_seconds=self._timeout_seconds,
            cancellation_check=cancellation_check,
        )
        cancellation_check()
        media = self._probe(output, cancellation_check)
        return RenderedRendition(
            path=output,
            media=media,
            sha256=_digest(output),
            ffmpeg_arguments=arguments,
            config_version=RENDITION_CONFIG_VERSION,
        )


def _digest(path: Path) -> bytes:
    """Hash one output incrementally so large provider media stays bounded in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.digest()
