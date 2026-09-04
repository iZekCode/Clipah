"""The compositions the golden-frame gate renders, and what each one is asked to prove.

Each scenario is one composition, one instant, and the filters FFmpeg must have to render
it. Keeping them here rather than inside the test means the generator that writes the
golden frames and the suite that checks them are looking at exactly the same documents —
a golden frame produced from a different composition would certify nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from perceptual import MaskedRegion

SOURCE_ASSET_ID = "11111111-1111-4111-8111-111111111111"
BROLL_IMAGE_ID = "33333333-3333-4333-8333-333333333333"
PROVENANCE_ID = "88888888-8888-4888-8888-888888888888"


@dataclass(frozen=True, slots=True)
class GoldenScenario:
    """One rendered frame the gate compares, and the reason it exists."""

    name: str
    document: dict[str, Any]
    at_seconds: float
    required_filters: tuple[str, ...] = ()
    needs_image: bool = False
    masked: tuple[MaskedRegion, ...] = field(default=())
    intent: str = ""


def _caption_style(**overrides: Any) -> dict[str, Any]:
    """The caption type these scenarios draw in."""
    values: dict[str, Any] = {
        "fontFamily": "Montserrat",
        "fontSize": 64,
        "color": "#FFFFFF",
        "highlightColor": "#FFD166",
        "align": "center",
        "weight": 700,
        "italic": False,
        "decoration": "none",
        "letterSpacing": 0.0,
        "lineHeight": 1.2,
        "backgroundEnabled": False,
        "backgroundColor": "#000000",
    }
    values.update(overrides)
    return values


def _document(**overrides: Any) -> dict[str, Any]:
    """One second of the landscape fixture, framed for a portrait export."""
    document: dict[str, Any] = {
        "schemaVersion": 1,
        "sourceAssetId": SOURCE_ASSET_ID,
        "durationMs": 1_000,
        "canvas": {"width": 1080, "height": 1920, "background": "#000000"},
        "sourceRange": {"inMs": 0, "outMs": 1_000},
        "template": None,
        "brandKit": None,
        "tracks": [
            {
                "id": "main-video",
                "type": "video",
                "items": [
                    {
                        "id": "scene-1",
                        "sourceAssetId": SOURCE_ASSET_ID,
                        "timelineStartMs": 0,
                        "sourceInMs": 0,
                        "sourceOutMs": 1_000,
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
        "captions": {"mode": "off", "words": [], "style": _caption_style()},
        "overlays": [],
        "audio": {"gainDb": 0.0, "musicGainDb": -18.0},
        "bookmarks": [],
    }
    document.update(overrides)
    return document


def _keyframe(at_ms: int, x: float) -> dict[str, Any]:
    """One framing keyframe, of the kind a smart-crop suggestion produces."""
    return {
        "atMs": at_ms,
        "easing": "easeInOut",
        "transform": {"x": x, "y": 0.5, "scale": 1.0, "rotation": 0.0},
        "opacity": None,
        "style": None,
    }


def _image_overlay(**overrides: Any) -> dict[str, Any]:
    """One still placed over the whole frame."""
    values: dict[str, Any] = {
        "id": "still-1",
        "type": "image",
        "assetId": BROLL_IMAGE_ID,
        "timelineStartMs": 100,
        "timelineEndMs": 900,
        "placement": "cover",
        "opacity": 1.0,
        "blendMode": "normal",
        "motion": "none",
        "origin": {
            "type": "userAsset",
            "suggestionId": None,
            "provenanceId": PROVENANCE_ID,
        },
        "keyframes": [],
    }
    values.update(overrides)
    return values


def _words() -> list[dict[str, Any]]:
    """Two caption words, long enough to be drawn at the sampled instant."""
    return [
        {"id": "w1", "startMs": 0, "endMs": 500, "text": "Ini", "speaker": "SPEAKER_00"},
        {"id": "w2", "startMs": 500, "endMs": 1_000, "text": "cara", "speaker": "SPEAKER_00"},
    ]


GOLDEN_SCENARIOS: tuple[GoldenScenario, ...] = (
    GoldenScenario(
        name="plain",
        document=_document(),
        at_seconds=0.5,
        intent="The base timeline, framed and scaled into the export preset.",
    ),
    GoldenScenario(
        name="smart-crop",
        document=_document(
            tracks=[
                {
                    "id": "main-video",
                    "type": "video",
                    "items": [
                        {
                            **_document()["tracks"][0]["items"][0],
                            "crop": {"x": 0.2, "y": 0.0, "width": 0.5, "height": 1.0},
                            "keyframes": [_keyframe(0, 0.3), _keyframe(1_000, 0.7)],
                        }
                    ],
                }
            ]
        ),
        # Sampled early, where the window is still left of centre: at the midpoint an
        # eased move from 0.3 to 0.7 sits exactly where a centred crop would.
        at_seconds=0.15,
        intent="A crop window that travels, which is what a smart-crop suggestion is.",
    ),
    GoldenScenario(
        name="ken-burns",
        document=_document(overlays=[_image_overlay(motion="kenBurnsIn")]),
        at_seconds=0.5,
        required_filters=("zoompan",),
        needs_image=True,
        intent="A still given the drift a static frame otherwise lacks.",
    ),
    GoldenScenario(
        name="overlay-opacity",
        document=_document(overlays=[_image_overlay(opacity=0.5)]),
        at_seconds=0.5,
        needs_image=True,
        intent="An overlay blended over the picture at its own opacity.",
    ),
    GoldenScenario(
        name="captions",
        document=_document(
            captions={"mode": "block", "words": _words(), "style": _caption_style()}
        ),
        at_seconds=0.25,
        required_filters=("subtitles",),
        masked=(MaskedRegion(top=0.55, bottom=1.0, reason="font rasterization"),),
        intent="Burned-in captions, with the band they are drawn in excluded.",
    ),
    GoldenScenario(
        name="karaoke",
        document=_document(
            captions={"mode": "karaoke", "words": _words(), "style": _caption_style()}
        ),
        at_seconds=0.25,
        required_filters=("subtitles",),
        masked=(MaskedRegion(top=0.55, bottom=1.0, reason="font rasterization"),),
        intent="Karaoke captions, which differ from block captions only in the type.",
    ),
    GoldenScenario(
        name="drawn-text",
        document=_document(
            overlays=[
                {
                    "id": "text-1",
                    "type": "text",
                    "timelineStartMs": 0,
                    "timelineEndMs": 1_000,
                    "placement": "center",
                    "opacity": 1.0,
                    "keyframes": [],
                    "motion": "none",
                    "text": "A written title",
                    "style": {
                        key: value
                        for key, value in _caption_style().items()
                        if key != "highlightColor"
                    },
                }
            ]
        ),
        at_seconds=0.5,
        required_filters=("drawtext",),
        masked=(MaskedRegion(top=0.3, bottom=0.7, reason="font rasterization"),),
        intent="Drawn text, with the band it is written into excluded.",
    ),
)
