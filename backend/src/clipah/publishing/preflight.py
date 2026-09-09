"""Pure deterministic validation of frozen media and Publication evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from clipah.publishing.profiles import ProviderProfile
from clipah.renders.models import PRESET_CANVAS, RENDER_FRAME_RATE, RenderPreset


@dataclass(frozen=True, slots=True)
class MediaFacts:
    """Normalized facts measured from one immutable master or rendition."""

    size_bytes: int
    duration_ms: int
    container: str
    video_codec: str
    audio_codec: str | None
    width: int
    height: int
    frame_rate: Fraction


@dataclass(frozen=True, slots=True)
class OverlayBounds:
    """One important overlay rectangle expressed in output pixels."""

    left: int
    top: int
    right: int
    bottom: int


@dataclass(frozen=True, slots=True)
class PublicationEvidence:
    """Frozen non-secret choices and composition evidence checked for one destination."""

    captions_attached: bool
    thumbnail_attached: bool
    promotional_watermarks: tuple[str, ...]
    overlays: tuple[OverlayBounds, ...]
    metadata: Mapping[str, object]
    disclosures: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class PreflightViolation:
    """One stable public reason a Publication cannot yet be confirmed or dispatched."""

    code: str
    field: str
    message: str
    remediation: str

    def as_dict(self) -> dict[str, str]:
        """Serialize only the fixed public shape used by APIs and durable evidence."""
        return {
            "code": self.code,
            "field": self.field,
            "message": self.message,
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """Reproducible result for one provider profile and frozen input set."""

    provider: str
    profile_version: str
    violations: tuple[PreflightViolation, ...]

    @property
    def passed(self) -> bool:
        """Return whether no blocking provider mismatch was found."""
        return not self.violations

    def as_dict(self) -> dict[str, Any]:
        """Serialize the report without media paths, keys, or provider diagnostics."""
        return {
            "passed": self.passed,
            "profileVersion": self.profile_version,
            "provider": self.provider,
            "violations": [item.as_dict() for item in self.violations],
        }


def render_artifact_media(*, preset: str, size_bytes: int, duration_ms: int) -> MediaFacts:
    """Describe a Render Artifact through the renderer's fixed output contract."""
    selected = RenderPreset(preset)
    width, height = PRESET_CANVAS[selected]
    return MediaFacts(
        size_bytes=size_bytes,
        duration_ms=duration_ms,
        container="mp4",
        video_codec="h264",
        audio_codec="aac",
        width=width,
        height=height,
        frame_rate=Fraction(RENDER_FRAME_RATE, 1),
    )


def publication_evidence_from_snapshots(
    *,
    metadata: Mapping[str, object],
    provider_options: Mapping[str, object],
    consent: Mapping[str, object],
    watermark_text: str | None,
) -> PublicationEvidence:
    """Normalize only frozen persisted facts into provider preflight evidence."""
    disclosures = consent.get("disclosures", {})
    return PublicationEvidence(
        captions_attached=bool(provider_options.get("captionsAttached", False)),
        thumbnail_attached=bool(provider_options.get("thumbnailAttached", False)),
        promotional_watermarks=() if watermark_text is None else (watermark_text,),
        overlays=(),
        metadata=metadata,
        disclosures=disclosures if isinstance(disclosures, Mapping) else {},
    )


def preflight(
    *, profile: ProviderProfile, media: MediaFacts, evidence: PublicationEvidence
) -> PreflightReport:
    """Validate fixed inputs in a stable order and return public remediation."""
    violations: list[PreflightViolation] = []
    if media.size_bytes < 0 or media.size_bytes > profile.max_file_size_bytes:
        violations.append(_violation("file_size", "media", "The video file size is not supported."))
    if not profile.min_duration_ms <= media.duration_ms <= profile.max_duration_ms:
        violations.append(_violation("duration", "media", "The video duration is not supported."))
    if media.container.lower() not in profile.containers:
        violations.append(_violation("container", "media", "The video container is not supported."))
    if media.video_codec.lower() not in profile.video_codecs:
        violations.append(_violation("video_codec", "media", "The video codec is not supported."))
    if media.audio_codec is None:
        if profile.audio_required:
            violations.append(_violation("audio_required", "media", "An audio stream is required."))
    elif media.audio_codec.lower() not in profile.audio_codecs:
        violations.append(_violation("audio_codec", "media", "The audio codec is not supported."))
    if not (
        profile.min_width <= media.width <= profile.max_width
        and profile.min_height <= media.height <= profile.max_height
    ):
        violations.append(
            _violation("resolution", "media", "The video resolution is not supported.")
        )
    if media.height <= 0 or not (
        profile.min_aspect_ratio <= Fraction(media.width, media.height) <= profile.max_aspect_ratio
    ):
        violations.append(
            _violation("aspect_ratio", "media", "The video aspect ratio is not supported.")
        )
    if not profile.min_frame_rate <= media.frame_rate <= profile.max_frame_rate:
        violations.append(
            _violation("frame_rate", "media", "The video frame rate is not supported.")
        )
    if profile.captions_required and not evidence.captions_attached:
        violations.append(_violation("captions_required", "captions", "Captions are required."))
    if profile.thumbnail_required and not evidence.thumbnail_attached:
        violations.append(_violation("thumbnail_required", "thumbnail", "A thumbnail is required."))
    for disclosure in sorted(profile.required_disclosures):
        if evidence.disclosures.get(disclosure) is not True:
            violations.append(
                PreflightViolation(
                    code="disclosure_required",
                    field=f"disclosures.{disclosure}",
                    message="A provider disclosure is required.",
                    remediation="Review and explicitly confirm the required disclosure.",
                )
            )
    if profile.safe_zone is not None and any(
        not _inside_safe_zone(bounds, media=media, profile=profile) for bounds in evidence.overlays
    ):
        violations.append(
            PreflightViolation(
                code="safe_zone",
                field="overlays",
                message="Important content extends outside the provider safe zone.",
                remediation="Move the overlay inside the displayed safe zone.",
            )
        )
    if profile.promotional_watermarks_forbidden and evidence.promotional_watermarks:
        violations.append(
            PreflightViolation(
                code="promotional_watermark",
                field="watermarks",
                message="Promotional branding is not permitted for this destination.",
                remediation="Render a clean master without promotional branding.",
            )
        )
    for limit in profile.metadata_limits:
        value = evidence.metadata.get(limit.field, "")
        length = len(value) if isinstance(value, str) else -1
        if length < limit.minimum:
            violations.append(
                PreflightViolation(
                    code="metadata_minimum",
                    field=f"metadata.{limit.field}",
                    message="Required publication metadata is missing.",
                    remediation=f"Provide {limit.field} before publishing.",
                )
            )
        elif length > limit.maximum:
            violations.append(
                PreflightViolation(
                    code="metadata_maximum",
                    field=f"metadata.{limit.field}",
                    message="Publication metadata is too long.",
                    remediation=f"Shorten {limit.field} to {limit.maximum} characters or fewer.",
                )
            )
    return PreflightReport(
        provider=profile.provider.value,
        profile_version=profile.version,
        violations=tuple(violations),
    )


def _inside_safe_zone(
    bounds: OverlayBounds, *, media: MediaFacts, profile: ProviderProfile
) -> bool:
    """Check one rectangle against the profile's inset output boundary."""
    zone = profile.safe_zone
    if zone is None:
        return True
    return (
        bounds.left >= zone.left
        and bounds.top >= zone.top
        and bounds.right <= media.width - zone.right
        and bounds.bottom <= media.height - zone.bottom
        and bounds.left < bounds.right
        and bounds.top < bounds.bottom
    )


def _violation(code: str, field: str, message: str) -> PreflightViolation:
    """Build the shared safe remediation for a media-format mismatch."""
    return PreflightViolation(
        code=code,
        field=field,
        message=message,
        remediation="Create a provider-compatible rendition from the approved master.",
    )
