"""Pure, deterministic accessibility checks for immutable compositions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from typing import Any

from clipah.editor.models import CaptionMode, Placement, TextOverlay, parse_composition
from clipah.variants.models import Platform
from clipah.variants.packaging import packaging_for

MAX_CAPTION_CHARACTERS_PER_SECOND = 20.0
MIN_CAPTION_DURATION_MS = 1_000
MAX_TEXT_LINES = 2
NORMAL_TEXT_CONTRAST_RATIO = 4.5
LARGE_TEXT_CONTRAST_RATIO = 3.0
LARGE_TEXT_SIZE_PX = 24


class AccessibilitySeverity(StrEnum):
    """The stable urgency levels shown by the editor quality panel."""

    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class AccessibilityWarning:
    """One measured, actionable quality issue that changes no content."""

    code: str
    severity: AccessibilitySeverity
    action: str
    item_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    measured: float | None = None
    threshold: float | None = None


def analyze_accessibility(
    document: dict[str, Any], *, platform: Platform | None = None
) -> tuple[AccessibilityWarning, ...]:
    """Return stable warnings without mutating one version-one composition."""
    composition = parse_composition(document)
    warnings: list[AccessibilityWarning] = []
    if composition.captions.mode is not CaptionMode.OFF:
        background = (
            composition.captions.style.background_color
            if composition.captions.style.background_enabled
            else composition.canvas.background
        )
        contrast = _contrast_ratio(composition.captions.style.color, background)
        threshold = (
            LARGE_TEXT_CONTRAST_RATIO
            if composition.captions.style.font_size >= LARGE_TEXT_SIZE_PX
            else NORMAL_TEXT_CONTRAST_RATIO
        )
        if contrast < threshold:
            warnings.append(
                AccessibilityWarning(
                    code="caption_contrast",
                    severity=AccessibilitySeverity.WARNING,
                    action="Increase the contrast between caption text and its background.",
                    measured=round(contrast, 2),
                    threshold=threshold,
                )
            )
        for word in composition.captions.words:
            duration_ms = word.end_ms - word.start_ms
            speed = len(word.text) / (duration_ms / 1_000)
            if speed > MAX_CAPTION_CHARACTERS_PER_SECOND:
                warnings.append(
                    AccessibilityWarning(
                        code="caption_reading_speed",
                        severity=AccessibilitySeverity.WARNING,
                        action="Shorten the caption text or leave it visible longer.",
                        item_id=word.id,
                        start_ms=word.start_ms,
                        end_ms=word.end_ms,
                        measured=round(speed, 2),
                        threshold=MAX_CAPTION_CHARACTERS_PER_SECOND,
                    )
                )
            if duration_ms < MIN_CAPTION_DURATION_MS:
                warnings.append(
                    AccessibilityWarning(
                        code="caption_minimum_duration",
                        severity=AccessibilitySeverity.WARNING,
                        action="Leave this caption visible for at least one second.",
                        item_id=word.id,
                        start_ms=word.start_ms,
                        end_ms=word.end_ms,
                        measured=float(duration_ms),
                        threshold=float(MIN_CAPTION_DURATION_MS),
                    )
                )
            lines = word.text.count("\n") + 1
            if lines > MAX_TEXT_LINES:
                warnings.append(
                    AccessibilityWarning(
                        code="caption_line_count",
                        severity=AccessibilitySeverity.WARNING,
                        action="Use no more than two caption lines.",
                        item_id=word.id,
                        start_ms=word.start_ms,
                        end_ms=word.end_ms,
                        measured=float(lines),
                        threshold=float(MAX_TEXT_LINES),
                    )
                )
        for earlier, later in pairwise(
            sorted(composition.captions.words, key=lambda word: word.start_ms)
        ):
            if later.start_ms < earlier.end_ms:
                warnings.append(
                    AccessibilityWarning(
                        code="caption_overlap",
                        severity=AccessibilitySeverity.WARNING,
                        action="Move these captions so only one is visible at a time.",
                        item_id=later.id,
                        start_ms=later.start_ms,
                        end_ms=earlier.end_ms,
                    )
                )

    if platform is not None:
        packaging = packaging_for(platform)
        for overlay in composition.overlays:
            if not isinstance(overlay, TextOverlay):
                continue
            inset = {
                Placement.TOP: packaging.safe_area_top_percent,
                Placement.LOWER_THIRD: packaging.safe_area_bottom_percent,
            }.get(overlay.placement)
            if inset is not None:
                warnings.append(
                    AccessibilityWarning(
                        code="platform_safe_zone",
                        severity=AccessibilitySeverity.WARNING,
                        action=f"Move this text inside the {platform.value} safe area.",
                        item_id=overlay.id,
                        start_ms=overlay.timeline_start_ms,
                        end_ms=overlay.timeline_end_ms,
                        threshold=inset,
                    )
                )
            if overlay.placement is Placement.LOWER_THIRD:
                warnings.append(
                    AccessibilityWarning(
                        code="platform_ui_collision",
                        severity=AccessibilitySeverity.WARNING,
                        action=f"Move this text away from {platform.value} controls.",
                        item_id=overlay.id,
                        start_ms=overlay.timeline_start_ms,
                        end_ms=overlay.timeline_end_ms,
                    )
                )
        if composition.captions.mode is not CaptionMode.OFF:
            warnings.append(
                AccessibilityWarning(
                    code="geometry_unavailable",
                    severity=AccessibilitySeverity.INFO,
                    action=(
                        "Preview caption bounds before export; this revision stores timing "
                        "and style but no measurable caption rectangle."
                    ),
                )
            )
    return tuple(sorted(warnings, key=_warning_key))


def _warning_key(warning: AccessibilityWarning) -> tuple[int, int, str, str]:
    """Order warnings consistently by urgency, time, item, and code."""
    severity = 0 if warning.severity is AccessibilitySeverity.WARNING else 1
    return (severity, warning.start_ms or -1, warning.item_id or "", warning.code)


def _contrast_ratio(foreground: str, background: str) -> float:
    """Calculate the WCAG relative-luminance contrast ratio for two RGB colours."""
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _luminance(color: str) -> float:
    """Convert one validated hexadecimal RGB colour to relative luminance."""
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]

    def linear(channel: float) -> float:
        """Linearize one sRGB channel according to WCAG 2.2."""
        if channel <= 0.04045:
            return channel / 12.92
        return float(((channel + 0.055) / 1.055) ** 2.4)

    red, green, blue = (linear(channel) for channel in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue
