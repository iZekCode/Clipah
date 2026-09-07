"""Composition and Brand Kit fixtures shared by the brand suites.

One composition, one Brand Kit it complies with, and the overrides each test bends.
Keeping them here means a suite that adds a rule cannot quietly change what "compliant"
meant for every other suite.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from clipah.brands.models import (
    BrandColor,
    BrandFont,
    BrandKitDefinition,
    CaptionRules,
    ClaimRules,
)
from clipah.editor.models import FontFamily, Placement, TextAlign

SOURCE_ASSET_ID = UUID("11111111-1111-4111-8111-111111111111")
BROLL_ASSET_ID = UUID("22222222-2222-4222-8222-222222222222")


def composition_document(**overrides: Any) -> dict[str, Any]:
    """One composition that complies with :func:`compliant_definition` exactly."""
    document: dict[str, Any] = {
        "schemaVersion": 1,
        "sourceAssetId": str(SOURCE_ASSET_ID),
        "durationMs": 30_000,
        "canvas": {"width": 1080, "height": 1920, "background": "#000000"},
        "sourceRange": {"inMs": 5_000, "outMs": 35_000},
        "template": None,
        "brandKit": None,
        "tracks": [
            {
                "id": "main-video",
                "type": "video",
                "items": [
                    {
                        "id": "scene-1",
                        "sourceAssetId": str(SOURCE_ASSET_ID),
                        "timelineStartMs": 0,
                        "sourceInMs": 5_000,
                        "sourceOutMs": 35_000,
                        "transform": {"x": 0.5, "y": 0.5, "scale": 1.0, "rotation": 0.0},
                        "crop": None,
                        "opacity": 1.0,
                        "blendMode": "normal",
                        "motion": "none",
                        "origin": {"type": "source", "suggestionId": None, "provenanceId": None},
                        "keyframes": [],
                    }
                ],
            }
        ],
        "captions": {
            "mode": "block",
            "words": [
                {"id": "w1", "startMs": 0, "endMs": 900, "text": "Ini", "speaker": "SPEAKER_00"},
                {"id": "w2", "startMs": 900, "endMs": 1_800, "text": "cara", "speaker": None},
            ],
            "style": caption_style(),
        },
        "overlays": [text_overlay()],
        "audio": {"gainDb": 0.0, "musicGainDb": -18.0},
        "bookmarks": [],
    }
    document.update(overrides)
    return document


def caption_style(**overrides: Any) -> dict[str, Any]:
    """The caption type the compliant kit allows."""
    style: dict[str, Any] = {
        "fontFamily": "Inter",
        "fontSize": 48,
        "color": "#FFFFFF",
        "highlightColor": "#FFD166",
        "align": "center",
        "weight": 400,
        "italic": False,
        "decoration": "none",
        "letterSpacing": 0.0,
        "lineHeight": 1.2,
        "backgroundEnabled": False,
        "backgroundColor": "#000000",
    }
    style.update(overrides)
    return style


def text_style(**overrides: Any) -> dict[str, Any]:
    """The drawn-text type the compliant kit allows."""
    style: dict[str, Any] = {
        "fontFamily": "Inter",
        "fontSize": 36,
        "color": "#FFFFFF",
        "align": "center",
        "weight": 400,
        "italic": False,
        "decoration": "none",
        "letterSpacing": 0.0,
        "lineHeight": 1.2,
        "backgroundEnabled": False,
        "backgroundColor": "#000000",
    }
    style.update(overrides)
    return style


def text_overlay(**overrides: Any) -> dict[str, Any]:
    """One drawn title, placed where the compliant kit allows type."""
    overlay: dict[str, Any] = {
        "id": "title-1",
        "type": "text",
        "timelineStartMs": 1_000,
        "timelineEndMs": 5_000,
        "placement": "top",
        "opacity": 1.0,
        "keyframes": [],
        "motion": "none",
        "text": "Rekaman lengkap ada di deskripsi",
        "style": text_style(),
    }
    overlay.update(overrides)
    return overlay


def video_overlay(**overrides: Any) -> dict[str, Any]:
    """One placed B-roll clip, which a visual exclusion is judged against."""
    overlay: dict[str, Any] = {
        "id": "broll-1",
        "type": "video",
        "timelineStartMs": 2_000,
        "timelineEndMs": 6_000,
        "placement": "cover",
        "opacity": 1.0,
        "keyframes": [],
        "motion": "none",
        "assetId": str(BROLL_ASSET_ID),
        "sourceInMs": 0,
        "sourceOutMs": 4_000,
        "blendMode": "normal",
        "preserveDialogueAudio": True,
        "origin": {
            "type": "brollSuggestion",
            "suggestionId": "33333333-3333-4333-8333-333333333333",
            "provenanceId": None,
        },
    }
    overlay.update(overrides)
    return overlay


def compliant_definition(**overrides: Any) -> BrandKitDefinition:
    """One Brand Kit the fixture composition satisfies in every respect."""
    values: dict[str, Any] = {
        "logo_asset_id": None,
        "fonts": (BrandFont(family=FontFamily.INTER, asset_id=None),),
        "colors": (
            BrandColor(name="Paper", hex="#FFFFFF"),
            BrandColor(name="Ink", hex="#000000"),
            BrandColor(name="Highlight", hex="#FFD166"),
        ),
        "caption_rules": CaptionRules(
            min_font_size=32,
            max_font_size=72,
            allowed_alignments=(TextAlign.CENTER,),
            reserved_placements=(Placement.LOWER_THIRD,),
        ),
        "visual_exclusions": ("alcohol",),
        "claim_rules": ClaimRules(required_attribution=None, forbidden_claim_phrases=()),
    }
    values.update(overrides)
    return BrandKitDefinition(**values)
