"""Checked-in provider media constraints identified by immutable versions."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from clipah.social_accounts.models import SocialProvider

CURRENT_PROFILE_VERSION = "2026-09-09"


class UnknownProviderProfileError(Exception):
    """A requested provider policy cannot be reproduced by this deployment."""


@dataclass(frozen=True, slots=True)
class SafeZone:
    """Pixel margins inside which important overlays must remain."""

    top: int
    right: int
    bottom: int
    left: int


@dataclass(frozen=True, slots=True)
class MetadataLimit:
    """Inclusive character bounds for one frozen provider metadata field."""

    field: str
    minimum: int
    maximum: int


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    """One reproducible provider media and publication-policy snapshot."""

    provider: SocialProvider
    version: str
    containers: frozenset[str]
    video_codecs: frozenset[str]
    audio_codecs: frozenset[str]
    max_file_size_bytes: int
    min_duration_ms: int
    max_duration_ms: int
    min_width: int
    max_width: int
    min_height: int
    max_height: int
    min_aspect_ratio: Fraction
    max_aspect_ratio: Fraction
    min_frame_rate: Fraction
    max_frame_rate: Fraction
    audio_required: bool
    captions_required: bool
    thumbnail_required: bool
    required_disclosures: frozenset[str]
    safe_zone: SafeZone | None
    promotional_watermarks_forbidden: bool
    metadata_limits: tuple[MetadataLimit, ...]
    output_width: int
    output_height: int
    output_frame_rate: int
    output_video_bitrate: str
    output_audio_bitrate: str


_PROFILES = {
    (SocialProvider.YOUTUBE, CURRENT_PROFILE_VERSION): ProviderProfile(
        provider=SocialProvider.YOUTUBE,
        version=CURRENT_PROFILE_VERSION,
        containers=frozenset({"mp4"}),
        video_codecs=frozenset({"h264"}),
        audio_codecs=frozenset({"aac"}),
        max_file_size_bytes=256 * 1024 * 1024 * 1024,
        min_duration_ms=1_000,
        max_duration_ms=12 * 60 * 60 * 1_000,
        min_width=360,
        max_width=7_680,
        min_height=360,
        max_height=7_680,
        min_aspect_ratio=Fraction(9, 16),
        max_aspect_ratio=Fraction(9, 16),
        min_frame_rate=Fraction(24, 1),
        max_frame_rate=Fraction(60, 1),
        audio_required=True,
        captions_required=False,
        thumbnail_required=False,
        required_disclosures=frozenset(),
        safe_zone=None,
        promotional_watermarks_forbidden=False,
        metadata_limits=(
            MetadataLimit(field="title", minimum=1, maximum=100),
            MetadataLimit(field="description", minimum=0, maximum=5_000),
        ),
        output_width=1_080,
        output_height=1_920,
        output_frame_rate=30,
        output_video_bitrate="8M",
        output_audio_bitrate="192k",
    ),
    (SocialProvider.INSTAGRAM, CURRENT_PROFILE_VERSION): ProviderProfile(
        provider=SocialProvider.INSTAGRAM,
        version=CURRENT_PROFILE_VERSION,
        containers=frozenset({"mp4", "mov"}),
        video_codecs=frozenset({"h264", "hevc"}),
        audio_codecs=frozenset({"aac"}),
        max_file_size_bytes=1024 * 1024 * 1024,
        min_duration_ms=3_000,
        max_duration_ms=15 * 60 * 1_000,
        min_width=360,
        max_width=1_920,
        min_height=360,
        max_height=4_096,
        min_aspect_ratio=Fraction(9, 16),
        max_aspect_ratio=Fraction(9, 16),
        min_frame_rate=Fraction(23, 1),
        max_frame_rate=Fraction(60, 1),
        audio_required=True,
        captions_required=False,
        thumbnail_required=False,
        required_disclosures=frozenset(),
        safe_zone=None,
        promotional_watermarks_forbidden=False,
        metadata_limits=(MetadataLimit(field="caption", minimum=0, maximum=2_200),),
        output_width=1_080,
        output_height=1_920,
        output_frame_rate=30,
        output_video_bitrate="8M",
        output_audio_bitrate="128k",
    ),
    (SocialProvider.TIKTOK, CURRENT_PROFILE_VERSION): ProviderProfile(
        provider=SocialProvider.TIKTOK,
        version=CURRENT_PROFILE_VERSION,
        containers=frozenset({"mp4", "mov", "webm"}),
        video_codecs=frozenset({"h264", "h265", "vp8", "vp9"}),
        audio_codecs=frozenset({"aac"}),
        max_file_size_bytes=4 * 1024 * 1024 * 1024,
        min_duration_ms=1_000,
        max_duration_ms=10 * 60 * 1_000,
        min_width=360,
        max_width=4_096,
        min_height=360,
        max_height=4_096,
        min_aspect_ratio=Fraction(9, 16),
        max_aspect_ratio=Fraction(9, 16),
        min_frame_rate=Fraction(23, 1),
        max_frame_rate=Fraction(60, 1),
        audio_required=True,
        captions_required=False,
        thumbnail_required=False,
        required_disclosures=frozenset(),
        safe_zone=None,
        promotional_watermarks_forbidden=True,
        metadata_limits=(MetadataLimit(field="title", minimum=0, maximum=2_200),),
        output_width=1_080,
        output_height=1_920,
        output_frame_rate=30,
        output_video_bitrate="8M",
        output_audio_bitrate="128k",
    ),
}


def profile_for(provider: SocialProvider, *, version: str | None = None) -> ProviderProfile:
    """Return one exact checked-in profile or fail closed when it is unavailable."""
    selected_version = version or CURRENT_PROFILE_VERSION
    try:
        return _PROFILES[(provider, selected_version)]
    except KeyError as error:
        raise UnknownProviderProfileError(f"{provider.value}:{selected_version}") from error
