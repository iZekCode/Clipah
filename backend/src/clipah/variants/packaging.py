"""Platform packaging metadata: what a clip needs to survive each destination.

This is a table, not an integration. Nothing here uploads, authenticates, or calls a
platform API — Tasks 36-43 own publishing. What a member gets before exporting is the
geometry the destination crops to, the area its own interface covers, how much title text
survives, and which caption style stays readable there.

The safe areas are deliberately generous: a caption hidden behind a platform's own buttons
is a caption nobody reads, and the cost of a wider margin is a little screen space.
"""

from __future__ import annotations

from dataclasses import dataclass

from clipah.variants.models import Platform


@dataclass(frozen=True, slots=True)
class PlatformPackaging:
    """Everything one destination asks of a clip, before anyone tries to publish it."""

    platform: Platform
    aspect_ratio: str
    safe_area_top_percent: float
    safe_area_bottom_percent: float
    safe_area_horizontal_percent: float
    max_title_characters: int
    caption_style: str
    export_preset: str


_PACKAGING: dict[Platform, PlatformPackaging] = {
    Platform.TIKTOK: PlatformPackaging(
        platform=Platform.TIKTOK,
        aspect_ratio="9:16",
        # The caption, the handle, and the action rail all sit low on the right.
        safe_area_top_percent=8.0,
        safe_area_bottom_percent=22.0,
        safe_area_horizontal_percent=12.0,
        max_title_characters=150,
        caption_style="karaoke_bold",
        export_preset="1080x1920",
    ),
    Platform.INSTAGRAM_REELS: PlatformPackaging(
        platform=Platform.INSTAGRAM_REELS,
        aspect_ratio="9:16",
        safe_area_top_percent=10.0,
        safe_area_bottom_percent=20.0,
        safe_area_horizontal_percent=10.0,
        max_title_characters=125,
        caption_style="karaoke_bold",
        export_preset="1080x1920",
    ),
    Platform.YOUTUBE_SHORTS: PlatformPackaging(
        platform=Platform.YOUTUBE_SHORTS,
        aspect_ratio="9:16",
        safe_area_top_percent=6.0,
        safe_area_bottom_percent=18.0,
        safe_area_horizontal_percent=8.0,
        max_title_characters=100,
        caption_style="clean_centered",
        export_preset="1080x1920",
    ),
}


def packaging_for(platform: Platform) -> PlatformPackaging:
    """Return what one destination requires of a clip."""
    return _PACKAGING[platform]
