"""Deterministic accessibility warnings for one immutable composition."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from brand_fixtures import caption_style, composition_document, text_overlay
from clipah.editor.accessibility import analyze_accessibility
from clipah.variants.models import Platform


def _document(**overrides: Any) -> dict[str, Any]:
    """Build a composition with no baseline accessibility warning."""
    document = composition_document(
        captions={
            "mode": "block",
            "words": [{"id": "w1", "startMs": 0, "endMs": 2_000, "text": "Good", "speaker": None}],
            "style": caption_style(color="#FFFFFF", backgroundColor="#000000"),
        },
        overlays=[],
    )
    document.update(overrides)
    return document


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "duration_ms", "expected"),
    [
        ("x" * 40, 2_000, ()),
        ("x" * 41, 2_000, ("caption_reading_speed",)),
        ("short", 999, ("caption_minimum_duration",)),
    ],
)
def test_caption_speed_and_duration_use_the_documented_boundaries(
    text: str, duration_ms: int, expected: tuple[str, ...]
) -> None:
    """A one-unit threshold regression must produce or remove the actionable warning."""
    document = _document()
    document["captions"]["words"] = [
        {"id": "w1", "startMs": 0, "endMs": duration_ms, "text": text, "speaker": None}
    ]

    warnings = analyze_accessibility(document)

    assert tuple(warning.code for warning in warnings) == expected


@pytest.mark.unit
def test_caption_contrast_and_line_count_are_reported_without_mutating_content() -> None:
    """Quality advice must describe the problem and never silently rewrite User text."""
    document = _document()
    document["captions"]["words"][0]["text"] = "one\ntwo\nthree"
    document["captions"]["style"].update({"color": "#000000", "backgroundColor": "#000000"})
    original = copy.deepcopy(document)

    warnings = analyze_accessibility(document)

    assert {warning.code for warning in warnings} == {
        "caption_contrast",
        "caption_line_count",
    }
    assert document == original


@pytest.mark.unit
def test_platform_controls_warn_about_a_lower_third_collision() -> None:
    """A platform control region must be called out before it obscures drawn text."""
    document = _document(
        overlays=[text_overlay(id="cta", placement="lowerThird", text="Follow for more")]
    )

    warnings = analyze_accessibility(document, platform=Platform.TIKTOK)

    assert [warning.code for warning in warnings] == [
        "platform_safe_zone",
        "platform_ui_collision",
        "geometry_unavailable",
    ]
    assert warnings[1].item_id == "cta"


@pytest.mark.unit
def test_top_text_warns_against_the_selected_platform_safe_inset() -> None:
    """Semantic placement still identifies a top-inset breach without fake pixel bounds."""
    document = _document(overlays=[text_overlay(id="title", placement="top", text="Watch this")])

    warnings = analyze_accessibility(document, platform=Platform.YOUTUBE_SHORTS)

    safe_zone = next(warning for warning in warnings if warning.code == "platform_safe_zone")
    assert safe_zone.item_id == "title"
    assert safe_zone.threshold == 6.0
