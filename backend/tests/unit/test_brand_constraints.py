"""Brand constraints as data, evaluated against one composition.

A Brand Kit is a promise a Workspace makes about how its clips look and what they are
allowed to assert. This module proves the two rules that promise is worth anything for:
a violation is reported with enough detail to fix it, and nothing here ever rewrites the
member's own editing decisions to make a composition comply.

Nothing in this suite reads a database, a clock, or a provider.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from brand_fixtures import (
    BROLL_ASSET_ID,
    caption_style,
    compliant_definition,
    composition_document,
    text_overlay,
    text_style,
    video_overlay,
)
from clipah.brands.models import (
    BrandFont,
    BrandViolationCode,
    CaptionRules,
    ClaimRules,
    evaluate_brand_constraints,
)
from clipah.editor.models import FontFamily, TextAlign, canonical_json, parse_composition


def codes(violations: Any) -> tuple[BrandViolationCode, ...]:
    """Report only which rules were broken, for assertions that do not care where."""
    return tuple(violation.code for violation in violations)


@pytest.mark.unit
def test_a_composition_that_honours_every_rule_reports_no_violation() -> None:
    """A kit that refused a compliant clip would make the whole check untrustworthy."""
    composition = parse_composition(composition_document())

    assert evaluate_brand_constraints(composition, definition=compliant_definition()) == ()


@pytest.mark.unit
def test_a_colour_the_kit_never_published_is_reported_against_the_element_using_it() -> None:
    """A member fixes a colour by knowing which element carries it."""
    document = composition_document(
        captions={
            "mode": "block",
            "words": composition_document()["captions"]["words"],
            "style": caption_style(color="#FF00FF"),
        }
    )
    composition = parse_composition(document)

    violations = evaluate_brand_constraints(composition, definition=compliant_definition())

    assert codes(violations) == (BrandViolationCode.COLOR_NOT_IN_KIT,)
    assert violations[0].element_id == "captions"
    assert "#FF00FF" in violations[0].detail


@pytest.mark.unit
def test_a_canvas_background_outside_the_kit_is_a_violation_of_its_own() -> None:
    """The frame behind everything is as much the brand as the type on top of it."""
    document = composition_document(canvas={"width": 1080, "height": 1920, "background": "#123456"})
    composition = parse_composition(document)

    violations = evaluate_brand_constraints(composition, definition=compliant_definition())

    assert codes(violations) == (BrandViolationCode.COLOR_NOT_IN_KIT,)
    assert violations[0].element_id == "canvas"


@pytest.mark.unit
def test_a_font_the_kit_never_licensed_is_refused_wherever_it_is_drawn() -> None:
    """A licensed face is a legal obligation, not a preference."""
    document = composition_document(overlays=[text_overlay(style=text_style(fontFamily="Anton"))])
    composition = parse_composition(document)

    violations = evaluate_brand_constraints(composition, definition=compliant_definition())

    assert codes(violations) == (BrandViolationCode.FONT_NOT_IN_KIT,)
    assert violations[0].element_id == "title-1"
    assert "Anton" in violations[0].detail


@pytest.mark.unit
@pytest.mark.parametrize("size", [24, 96])
def test_caption_type_outside_the_kit_size_band_is_reported(size: int) -> None:
    """Type too small to read and type that swallows the frame are both off-brand."""
    document = composition_document(
        captions={
            "mode": "block",
            "words": composition_document()["captions"]["words"],
            "style": caption_style(fontSize=size),
        }
    )
    composition = parse_composition(document)

    violations = evaluate_brand_constraints(composition, definition=compliant_definition())

    assert codes(violations) == (BrandViolationCode.CAPTION_FONT_SIZE_OUT_OF_RANGE,)


@pytest.mark.unit
def test_an_alignment_the_kit_does_not_allow_is_reported() -> None:
    """A brand that centres its captions does not centre only some of them."""
    document = composition_document(
        captions={
            "mode": "block",
            "words": composition_document()["captions"]["words"],
            "style": caption_style(align="left"),
        }
    )
    composition = parse_composition(document)

    violations = evaluate_brand_constraints(composition, definition=compliant_definition())

    assert codes(violations) == (BrandViolationCode.CAPTION_ALIGNMENT_NOT_ALLOWED,)


@pytest.mark.unit
def test_type_placed_in_an_area_the_kit_keeps_clear_is_reported() -> None:
    """A safe zone exists because a platform's own interface covers that part of the frame."""
    document = composition_document(overlays=[text_overlay(placement="lowerThird")])
    composition = parse_composition(document)

    violations = evaluate_brand_constraints(composition, definition=compliant_definition())

    assert codes(violations) == (BrandViolationCode.TEXT_IN_RESERVED_AREA,)
    assert violations[0].element_id == "title-1"


@pytest.mark.unit
def test_a_caption_layer_that_draws_nothing_is_judged_on_nothing() -> None:
    """Captions that are switched off cannot break a rule about how captions look."""
    document = composition_document(
        captions={
            "mode": "off",
            "words": composition_document()["captions"]["words"],
            "style": caption_style(align="left", fontSize=24),
        }
    )
    composition = parse_composition(document)

    assert evaluate_brand_constraints(composition, definition=compliant_definition()) == ()


@pytest.mark.unit
def test_an_excluded_subject_is_reported_against_the_media_that_shows_it() -> None:
    """A member cannot see what a stock clip depicts, so the description is the evidence."""
    document = composition_document(overlays=[text_overlay(), video_overlay()])
    composition = parse_composition(document)

    violations = evaluate_brand_constraints(
        composition,
        definition=compliant_definition(),
        asset_descriptions={BROLL_ASSET_ID: "Friends toasting with alcohol at a bar"},
    )

    assert codes(violations) == (BrandViolationCode.VISUAL_EXCLUDED,)
    assert violations[0].element_id == "broll-1"
    assert "alcohol" in violations[0].detail


@pytest.mark.unit
def test_media_nobody_described_is_never_accused_of_showing_an_excluded_subject() -> None:
    """An absent description is unknown, and unknown is not evidence of a breach."""
    document = composition_document(overlays=[text_overlay(), video_overlay()])
    composition = parse_composition(document)

    assert evaluate_brand_constraints(composition, definition=compliant_definition()) == ()


@pytest.mark.unit
def test_an_excluded_subject_written_on_screen_is_reported_too() -> None:
    """Type is content: a word the brand excludes is excluded when it is drawn as well."""
    document = composition_document(overlays=[text_overlay(text="Sponsored by Alcohol Co")])
    composition = parse_composition(document)

    violations = evaluate_brand_constraints(composition, definition=compliant_definition())

    assert codes(violations) == (BrandViolationCode.VISUAL_EXCLUDED,)
    assert violations[0].element_id == "title-1"


@pytest.mark.unit
def test_a_kit_that_requires_attribution_refuses_a_clip_that_omits_it() -> None:
    """Attribution the brand owes somebody else is not optional."""
    definition = compliant_definition(
        claim_rules=ClaimRules(
            required_attribution="Sumber: Bank Indonesia", forbidden_claim_phrases=()
        )
    )
    composition = parse_composition(composition_document())

    violations = evaluate_brand_constraints(composition, definition=definition)

    assert codes(violations) == (BrandViolationCode.ATTRIBUTION_MISSING,)
    assert violations[0].element_id is None


@pytest.mark.unit
def test_attribution_drawn_anywhere_on_screen_satisfies_the_requirement() -> None:
    """Where the notice sits is the member's decision; that it is shown is the brand's."""
    definition = compliant_definition(
        claim_rules=ClaimRules(
            required_attribution="Sumber: Bank Indonesia", forbidden_claim_phrases=()
        )
    )
    document = composition_document(
        overlays=[text_overlay(text="Sumber: Bank Indonesia", placement="top")]
    )
    composition = parse_composition(document)

    assert evaluate_brand_constraints(composition, definition=definition) == ()


@pytest.mark.unit
def test_a_forbidden_claim_is_reported_wherever_it_is_said_or_drawn() -> None:
    """The phrases a brand may not assert are the ones its lawyers already refused."""
    definition = compliant_definition(
        claim_rules=ClaimRules(
            required_attribution=None, forbidden_claim_phrases=("guaranteed returns",)
        )
    )
    document = composition_document(overlays=[text_overlay(text="GUARANTEED RETURNS every month")])
    composition = parse_composition(document)

    violations = evaluate_brand_constraints(composition, definition=definition)

    assert codes(violations) == (BrandViolationCode.FORBIDDEN_CLAIM,)
    assert violations[0].element_id == "title-1"
    assert "guaranteed returns" in violations[0].detail


@pytest.mark.unit
def test_a_forbidden_claim_spoken_across_caption_words_is_still_found() -> None:
    """A claim is drawn one word at a time, and a rule that missed that would be evaded."""
    definition = compliant_definition(
        claim_rules=ClaimRules(
            required_attribution=None, forbidden_claim_phrases=("dijamin untung",)
        )
    )
    document = composition_document(
        captions={
            "mode": "block",
            "words": [
                {"id": "w1", "startMs": 0, "endMs": 900, "text": "Dijamin", "speaker": None},
                {"id": "w2", "startMs": 900, "endMs": 1_800, "text": "untung", "speaker": None},
            ],
            "style": caption_style(),
        }
    )
    composition = parse_composition(document)

    violations = evaluate_brand_constraints(composition, definition=definition)

    assert codes(violations) == (BrandViolationCode.FORBIDDEN_CLAIM,)
    assert violations[0].element_id == "captions"


@pytest.mark.unit
def test_a_forbidden_phrase_is_never_matched_inside_an_unrelated_word() -> None:
    """A rule that fires on a substring of another word would be noise a member learns to ignore."""
    definition = compliant_definition(
        claim_rules=ClaimRules(required_attribution=None, forbidden_claim_phrases=("gain",))
    )
    document = composition_document(overlays=[text_overlay(text="Against the odds")])
    composition = parse_composition(document)

    assert evaluate_brand_constraints(composition, definition=definition) == ()


@pytest.mark.unit
def test_every_broken_rule_is_reported_rather_than_only_the_first() -> None:
    """A member who fixes one violation and finds another has been made to work twice."""
    definition = compliant_definition(
        claim_rules=ClaimRules(
            required_attribution="Sumber: BPS", forbidden_claim_phrases=("pasti cuan",)
        )
    )
    document = composition_document(
        overlays=[
            text_overlay(
                text="Pasti cuan",
                placement="lowerThird",
                style=text_style(fontFamily="Anton", color="#FF00FF"),
            )
        ]
    )
    composition = parse_composition(document)

    violations = evaluate_brand_constraints(composition, definition=definition)

    assert set(codes(violations)) == {
        BrandViolationCode.COLOR_NOT_IN_KIT,
        BrandViolationCode.FONT_NOT_IN_KIT,
        BrandViolationCode.TEXT_IN_RESERVED_AREA,
        BrandViolationCode.FORBIDDEN_CLAIM,
        BrandViolationCode.ATTRIBUTION_MISSING,
    }


@pytest.mark.unit
def test_evaluating_the_same_composition_twice_reports_the_same_order() -> None:
    """A violation list a member reads twice has to say the same thing twice."""
    definition = compliant_definition(
        claim_rules=ClaimRules(
            required_attribution="Sumber: BPS", forbidden_claim_phrases=("pasti cuan",)
        )
    )
    document = composition_document(
        overlays=[
            text_overlay(text="Pasti cuan", style=text_style(fontFamily="Anton", color="#FF00FF")),
            text_overlay(id="title-2", placement="lowerThird"),
        ]
    )
    composition = parse_composition(document)

    first = evaluate_brand_constraints(composition, definition=definition)
    second = evaluate_brand_constraints(composition, definition=definition)

    assert first == second


@pytest.mark.unit
def test_evaluation_never_changes_the_composition_it_judged() -> None:
    """Silently correcting a member's clip would make the preview a lie."""
    definition = compliant_definition(
        claim_rules=ClaimRules(required_attribution="Sumber: BPS", forbidden_claim_phrases=())
    )
    composition = parse_composition(
        composition_document(overlays=[text_overlay(style=text_style(fontFamily="Anton"))])
    )
    before = canonical_json(composition)

    assert evaluate_brand_constraints(composition, definition=definition)
    assert canonical_json(composition) == before


@pytest.mark.unit
def test_a_kit_cannot_be_published_without_a_colour_or_a_font() -> None:
    """A kit that constrains nothing would pass everything, which is worse than no kit."""
    with pytest.raises(ValueError):
        compliant_definition(colors=())
    with pytest.raises(ValueError):
        compliant_definition(fonts=())


@pytest.mark.unit
def test_a_caption_size_band_that_excludes_every_size_is_refused() -> None:
    """A band whose floor is above its ceiling could never be satisfied by any clip."""
    with pytest.raises(ValueError):
        CaptionRules(
            min_font_size=80,
            max_font_size=40,
            allowed_alignments=(TextAlign.CENTER,),
            reserved_placements=(),
        )


@pytest.mark.unit
def test_a_kit_that_allows_no_alignment_is_refused() -> None:
    """Every drawn word has some alignment, so an empty list forbids captions entirely."""
    with pytest.raises(ValueError):
        CaptionRules(
            min_font_size=32, max_font_size=72, allowed_alignments=(), reserved_placements=()
        )


@pytest.mark.unit
def test_a_kit_definition_is_frozen_once_it_is_published() -> None:
    """A version is a promise, and a promise that can be edited in place is not one."""
    definition = compliant_definition()

    with pytest.raises(ValueError):
        definition.visual_exclusions = ()  # type: ignore[misc]


@pytest.mark.unit
def test_a_logo_asset_the_kit_names_is_reported_for_the_caller_to_authorize() -> None:
    """The kit cannot read the asset table, so it names what the caller must check."""
    logo_asset_id = uuid4()
    definition = compliant_definition()

    assert definition.referenced_asset_ids() == frozenset()
    assert compliant_definition(
        logo_asset_id=logo_asset_id,
        fonts=(BrandFont(family=FontFamily.INTER, asset_id=None),),
    ).referenced_asset_ids() == frozenset({logo_asset_id})


@pytest.mark.unit
def test_a_font_file_the_kit_uploads_is_named_for_authorization_too() -> None:
    """A licensed font file is a Workspace asset like any other, and is checked like one."""
    font_asset_id = uuid4()
    definition = compliant_definition(
        fonts=(BrandFont(family=FontFamily.INTER, asset_id=font_asset_id),)
    )

    assert definition.referenced_asset_ids() == frozenset({font_asset_id})


@pytest.mark.unit
def test_karaoke_highlighting_is_judged_only_where_it_is_actually_drawn() -> None:
    """A highlight colour matters when a word lights up, and not otherwise."""
    words = composition_document()["captions"]["words"]
    style = caption_style(highlightColor="#FF00FF")
    karaoke = parse_composition(
        composition_document(captions={"mode": "karaoke", "words": words, "style": style})
    )
    block = parse_composition(
        composition_document(captions={"mode": "block", "words": words, "style": style})
    )

    assert codes(evaluate_brand_constraints(karaoke, definition=compliant_definition())) == (
        BrandViolationCode.COLOR_NOT_IN_KIT,
    )
    assert evaluate_brand_constraints(block, definition=compliant_definition()) == ()


@pytest.mark.unit
def test_a_caption_background_is_judged_only_when_it_is_switched_on() -> None:
    """A colour behind nothing is a setting, not something anybody sees."""
    words = composition_document()["captions"]["words"]
    hidden = caption_style(backgroundColor="#FF00FF")
    drawn = caption_style(backgroundColor="#FF00FF", backgroundEnabled=True)

    assert (
        evaluate_brand_constraints(
            parse_composition(
                composition_document(captions={"mode": "block", "words": words, "style": hidden})
            ),
            definition=compliant_definition(),
        )
        == ()
    )
    assert codes(
        evaluate_brand_constraints(
            parse_composition(
                composition_document(captions={"mode": "block", "words": words, "style": drawn})
            ),
            definition=compliant_definition(),
        )
    ) == (BrandViolationCode.COLOR_NOT_IN_KIT,)


@pytest.mark.unit
def test_type_a_keyframe_animates_towards_is_judged_like_any_other_type() -> None:
    """A rule a member could dodge with one keyframe is not a rule."""
    overlay = text_overlay(
        keyframes=[
            {
                "atMs": 0,
                "easing": "linear",
                "transform": None,
                "opacity": None,
                "style": text_style(
                    fontFamily="Anton",
                    color="#FF00FF",
                    backgroundEnabled=True,
                    backgroundColor="#123456",
                ),
            }
        ]
    )
    composition = parse_composition(composition_document(overlays=[overlay]))

    violations = evaluate_brand_constraints(composition, definition=compliant_definition())

    assert set(codes(violations)) == {
        BrandViolationCode.COLOR_NOT_IN_KIT,
        BrandViolationCode.FONT_NOT_IN_KIT,
    }
    assert sum(code is BrandViolationCode.COLOR_NOT_IN_KIT for code in codes(violations)) == 2
    assert all(violation.element_id == "title-1" for violation in violations)


@pytest.mark.unit
def test_type_a_track_item_keyframe_carries_is_judged_too() -> None:
    """A timeline item can animate type as readily as an overlay can."""
    document = composition_document()
    document["tracks"][0]["items"][0]["keyframes"] = [
        {
            "atMs": 0,
            "easing": "linear",
            "transform": None,
            "opacity": None,
            "style": text_style(fontFamily="Anton"),
        }
    ]
    composition = parse_composition(document)

    violations = evaluate_brand_constraints(composition, definition=compliant_definition())

    assert codes(violations) == (BrandViolationCode.FONT_NOT_IN_KIT,)
    assert violations[0].element_id == "scene-1"


@pytest.mark.unit
def test_a_background_drawn_behind_an_overlay_is_judged_like_any_other_colour() -> None:
    """A panel behind type is as visible as the type on top of it."""
    overlay = text_overlay(style=text_style(backgroundEnabled=True, backgroundColor="#123456"))
    composition = parse_composition(composition_document(overlays=[overlay]))

    violations = evaluate_brand_constraints(composition, definition=compliant_definition())

    assert codes(violations) == (BrandViolationCode.COLOR_NOT_IN_KIT,)
    assert "#123456" in violations[0].detail
