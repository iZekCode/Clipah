"""Composition version 1 is the contract every editor and renderer must agree on.

These tests describe the document itself: what it may contain, what it must refuse,
and the canonical bytes its hash is taken over. Nothing here touches Postgres, an
object store, or a provider, because a composition is decided by its own contents.
"""

from __future__ import annotations

import copy
import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from clipah.editor.models import (
    CompositionValidationError,
    canonical_json,
    collect_asset_ids,
    composition_hash,
    parse_composition,
)

SOURCE_ASSET_ID = UUID("11111111-1111-4111-8111-111111111111")
BROLL_ASSET_ID = UUID("22222222-2222-4222-8222-222222222222")
LOGO_ASSET_ID = UUID("33333333-3333-4333-8333-333333333333")
SUGGESTION_ID = UUID("44444444-4444-4444-8444-444444444444")
EVIDENCE_ID = UUID("55555555-5555-4555-8555-555555555555")
TEMPLATE_ID = UUID("66666666-6666-4666-8666-666666666666")
BRAND_KIT_ID = UUID("77777777-7777-4777-8777-777777777777")
PROVENANCE_ID = UUID("88888888-8888-4888-8888-888888888888")


def composition_document() -> dict[str, Any]:
    """Build one fully featured composition that exercises every supported capability."""
    return {
        "schemaVersion": 1,
        "sourceAssetId": str(SOURCE_ASSET_ID),
        "durationMs": 30_000,
        "canvas": {"width": 1080, "height": 1920, "background": "#000000"},
        "sourceRange": {"inMs": 12_000, "outMs": 42_000},
        "template": {"id": str(TEMPLATE_ID), "version": 3},
        "brandKit": {"id": str(BRAND_KIT_ID), "version": 2, "logoAssetId": str(LOGO_ASSET_ID)},
        "tracks": [
            {
                "id": "main-video",
                "type": "video",
                "items": [
                    {
                        "id": "scene-1",
                        "sourceAssetId": str(SOURCE_ASSET_ID),
                        "timelineStartMs": 0,
                        "sourceInMs": 12_000,
                        "sourceOutMs": 24_000,
                        "transform": {"x": 0.5, "y": 0.5, "scale": 1.0, "rotation": 0.0},
                        "crop": {"x": 0.25, "y": 0.0, "width": 0.5, "height": 1.0},
                        "opacity": 1.0,
                        "blendMode": "normal",
                        "motion": "kenBurnsIn",
                        "origin": {
                            "type": "source",
                            "suggestionId": None,
                            "provenanceId": None,
                        },
                        "keyframes": [
                            {
                                "atMs": 0,
                                "easing": "linear",
                                "transform": None,
                                "opacity": 1.0,
                                "style": None,
                            },
                            {
                                "atMs": 4_000,
                                "easing": "easeInOut",
                                "transform": {
                                    "x": 0.5,
                                    "y": 0.4,
                                    "scale": 1.2,
                                    "rotation": 0.0,
                                },
                                "opacity": None,
                                "style": None,
                            },
                        ],
                    },
                    {
                        "id": "scene-2",
                        "sourceAssetId": str(SOURCE_ASSET_ID),
                        "timelineStartMs": 12_000,
                        "sourceInMs": 24_000,
                        "sourceOutMs": 42_000,
                        "transform": {"x": 0.5, "y": 0.5, "scale": 1.0, "rotation": 0.0},
                        "crop": None,
                        "opacity": 1.0,
                        "blendMode": "normal",
                        "motion": "none",
                        "origin": {
                            "type": "source",
                            "suggestionId": None,
                            "provenanceId": None,
                        },
                        "keyframes": [],
                    },
                ],
            },
            {
                "id": "extracted-audio",
                "type": "extractedAudio",
                "items": [
                    {
                        "id": "dialogue-1",
                        "sourceAssetId": str(SOURCE_ASSET_ID),
                        "timelineStartMs": 0,
                        "sourceInMs": 12_000,
                        "sourceOutMs": 42_000,
                        "transform": {"x": 0.5, "y": 0.5, "scale": 1.0, "rotation": 0.0},
                        "crop": None,
                        "opacity": 1.0,
                        "blendMode": "normal",
                        "motion": "none",
                        "origin": {
                            "type": "source",
                            "suggestionId": None,
                            "provenanceId": None,
                        },
                        "keyframes": [],
                    }
                ],
            },
        ],
        "captions": {
            "mode": "karaoke",
            "words": [
                {"id": "word-1", "startMs": 0, "endMs": 420, "text": "Example", "speaker": "A"},
                {"id": "word-2", "startMs": 420, "endMs": 900, "text": "moment", "speaker": "A"},
            ],
            "style": {
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
            },
        },
        "overlays": [
            {
                "id": "broll-1",
                "type": "video",
                "assetId": str(BROLL_ASSET_ID),
                "timelineStartMs": 8_000,
                "timelineEndMs": 12_000,
                "sourceInMs": 0,
                "sourceOutMs": 4_000,
                "placement": "cover",
                "opacity": 1.0,
                "blendMode": "normal",
                "motion": "panLeft",
                "preserveDialogueAudio": True,
                "origin": {
                    "type": "brollSuggestion",
                    "suggestionId": str(SUGGESTION_ID),
                    "provenanceId": str(PROVENANCE_ID),
                },
                "keyframes": [],
            },
            {
                "id": "citation-1",
                "type": "citation",
                "timelineStartMs": 14_000,
                "timelineEndMs": 18_000,
                "placement": "lowerThird",
                "opacity": 1.0,
                "claimEvidenceId": str(EVIDENCE_ID),
                "text": "Source: Example Journal, 2026",
                "style": {
                    "fontFamily": "Inter",
                    "fontSize": 36,
                    "color": "#FFFFFF",
                    "align": "left",
                    "weight": 500,
                    "italic": False,
                    "decoration": "none",
                    "letterSpacing": 0.0,
                    "lineHeight": 1.2,
                    "backgroundEnabled": True,
                    "backgroundColor": "#101010",
                },
                "keyframes": [],
            },
        ],
        "audio": {"gainDb": 0.0, "musicGainDb": -18.0},
        "bookmarks": [{"id": "bookmark-1", "timelineMs": 6_000, "label": "hook lands"}],
    }


def rejects(document: dict[str, Any]) -> str:
    """Assert one document is refused and return the stable reason it carries."""
    with pytest.raises(CompositionValidationError) as refusal:
        parse_composition(document)
    return str(refusal.value)


@pytest.mark.unit
def test_reference_composition_carries_every_capability_the_editor_must_support() -> None:
    """Section 9's editor cannot be built on a schema that cannot express its own output."""
    composition = parse_composition(composition_document())

    assert composition.schema_version == 1
    assert composition.source_asset_id == SOURCE_ASSET_ID
    assert composition.template is not None
    assert composition.template.version == 3
    assert composition.brand_kit is not None
    assert composition.brand_kit.logo_asset_id == LOGO_ASSET_ID
    assert [track.type for track in composition.tracks] == ["video", "extractedAudio"]
    assert composition.captions.mode == "karaoke"
    assert composition.captions.words[0].speaker == "A"
    assert [overlay.type for overlay in composition.overlays] == ["video", "citation"]
    assert composition.overlays[0].preserve_dialogue_audio is True
    assert composition.bookmarks[0].label == "hook lands"


@pytest.mark.unit
def test_round_trip_preserves_every_field_the_document_arrived_with() -> None:
    """A parsed composition that loses a field would silently drop a member's work."""
    document = composition_document()

    restored = json.loads(canonical_json(parse_composition(document)))

    assert restored == json.loads(
        json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    )


@pytest.mark.unit
@pytest.mark.parametrize("version", [0, 2, "1"])
def test_a_composition_of_another_schema_version_is_refused_rather_than_coerced(
    version: object,
) -> None:
    """Version 1 must never be guessed at, because a later shape means different rendering."""
    document = composition_document()
    document["schemaVersion"] = version

    assert "schemaVersion" in rejects(document)


@pytest.mark.unit
def test_an_unknown_field_is_refused_instead_of_being_dropped() -> None:
    """Silently discarding an unknown field would lose editor work without saying so."""
    document = composition_document()
    document["motionBlur"] = True

    assert rejects(document)


@pytest.mark.unit
def test_an_unknown_track_type_is_refused() -> None:
    """A renderer that meets a track type it cannot draw must fail at save, not at export."""
    document = composition_document()
    document["tracks"][0]["type"] = "hologram"

    assert rejects(document)


@pytest.mark.unit
@pytest.mark.parametrize("track_type", ["video", "audio", "music", "extractedAudio"])
def test_every_supported_track_type_is_accepted(track_type: str) -> None:
    """Music and extracted-audio tracks are named by Section 4 and must round-trip."""
    document = composition_document()
    document["tracks"][1]["type"] = track_type

    assert parse_composition(document).tracks[1].type == track_type


@pytest.mark.unit
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_number_is_refused_anywhere_it_appears(value: float) -> None:
    """A NaN opacity cannot be hashed, compared, or rendered, so it may never be stored."""
    document = composition_document()
    document["tracks"][0]["items"][0]["opacity"] = value

    assert rejects(document)


@pytest.mark.unit
def test_a_reversed_source_range_is_refused() -> None:
    """A clip that ends before it starts describes no media at all."""
    document = composition_document()
    document["sourceRange"] = {"inMs": 42_000, "outMs": 12_000}

    assert rejects(document)


@pytest.mark.unit
def test_an_item_that_runs_past_the_composition_duration_is_refused() -> None:
    """A timeline longer than the composition would render bytes no preview ever showed."""
    document = composition_document()
    document["tracks"][0]["items"][1]["sourceOutMs"] = 60_000

    assert rejects(document)


@pytest.mark.unit
def test_two_items_overlapping_inside_one_track_are_refused() -> None:
    """One track plays one item at a time; overlapping items hide a member's edit."""
    document = composition_document()
    document["tracks"][0]["items"][1]["timelineStartMs"] = 6_000

    assert rejects(document)


@pytest.mark.unit
def test_an_item_whose_source_range_is_reversed_is_refused() -> None:
    """Reversed media bounds are the most common way a bad split reaches durable state."""
    document = composition_document()
    document["tracks"][0]["items"][0]["sourceOutMs"] = 12_000

    assert rejects(document)


@pytest.mark.unit
def test_an_overlay_that_ends_before_it_starts_is_refused() -> None:
    """A B-roll placement with no duration is a defect, not an invisible overlay."""
    document = composition_document()
    document["overlays"][0]["timelineEndMs"] = 8_000

    assert rejects(document)


@pytest.mark.unit
def test_an_overlay_beyond_the_composition_duration_is_refused() -> None:
    """An overlay outside the clip cannot be reviewed, so it must not be saved."""
    document = composition_document()
    document["overlays"][1]["timelineEndMs"] = 31_000

    assert rejects(document)


@pytest.mark.unit
def test_overlapping_caption_words_are_refused() -> None:
    """Two words claiming the same instant make karaoke highlighting undefined."""
    document = composition_document()
    document["captions"]["words"][1]["startMs"] = 300

    assert rejects(document)


@pytest.mark.unit
def test_a_caption_word_outside_the_composition_is_refused() -> None:
    """A word timed past the clip would be burned into no frame at all."""
    document = composition_document()
    document["captions"]["words"][1]["endMs"] = 40_000

    assert rejects(document)


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutation",
    [
        ("tracks", "duplicate track id"),
        ("items", "duplicate item id"),
        ("overlays", "duplicate overlay id"),
        ("words", "duplicate caption word id"),
        ("bookmarks", "duplicate bookmark id"),
    ],
)
def test_every_identifier_in_one_composition_is_unique(mutation: tuple[str, str]) -> None:
    """Editor operations address items by ID; a repeated ID makes an edit ambiguous."""
    kind, _ = mutation
    document = composition_document()
    if kind == "tracks":
        document["tracks"][1]["id"] = "main-video"
    elif kind == "items":
        document["tracks"][1]["items"][0]["id"] = "scene-1"
    elif kind == "overlays":
        document["overlays"][1]["id"] = "broll-1"
    elif kind == "words":
        document["captions"]["words"][1]["id"] = "word-1"
    else:
        document["bookmarks"].append(
            {"id": "bookmark-1", "timelineMs": 9_000, "label": "second mark"}
        )

    assert rejects(document)


@pytest.mark.unit
def test_keyframes_must_be_ordered_by_strictly_increasing_time() -> None:
    """Interpolation between two keyframes at the same instant has no defined answer."""
    document = composition_document()
    document["tracks"][0]["items"][0]["keyframes"][1]["atMs"] = 0

    assert rejects(document)


@pytest.mark.unit
def test_a_keyframe_outside_its_own_item_is_refused() -> None:
    """A keyframe past the item it animates would never be reached during playback."""
    document = composition_document()
    document["tracks"][0]["items"][0]["keyframes"][1]["atMs"] = 20_000

    assert rejects(document)


@pytest.mark.unit
def test_a_keyframe_that_animates_nothing_is_refused() -> None:
    """A keyframe carrying no property is an empty instruction the renderer cannot honour."""
    document = composition_document()
    document["tracks"][0]["items"][0]["keyframes"][0] = {
        "atMs": 0,
        "easing": "linear",
        "transform": None,
        "opacity": None,
        "style": None,
    }

    assert rejects(document)


@pytest.mark.unit
def test_an_unsupported_font_is_refused() -> None:
    """A font the renderer cannot load would export captions that no preview showed."""
    document = composition_document()
    document["captions"]["style"]["fontFamily"] = "Comic Sans MS"

    assert rejects(document)


@pytest.mark.unit
def test_an_unsupported_blend_mode_is_refused() -> None:
    """Blend modes are an engine capability, not free text."""
    document = composition_document()
    document["tracks"][0]["items"][0]["blendMode"] = "hard-light-ish"

    assert rejects(document)


@pytest.mark.unit
def test_an_unsupported_motion_preset_is_refused() -> None:
    """Motion presets must resolve to a defined animation in both preview and export."""
    document = composition_document()
    document["overlays"][0]["motion"] = "barrel-roll"

    assert rejects(document)


@pytest.mark.unit
@pytest.mark.parametrize(
    "canvas",
    [
        {"width": 1080, "height": 1920, "background": "#000000"},
        {"width": 1920, "height": 1080, "background": "#000000"},
        {"width": 1080, "height": 1080, "background": "#FFFFFF"},
        {"width": 1080, "height": 1350, "background": "#123456"},
    ],
)
def test_every_supported_aspect_preset_is_accepted(canvas: dict[str, Any]) -> None:
    """Section 9 names four export presets; each one must be expressible."""
    document = composition_document()
    document["canvas"] = canvas

    assert parse_composition(document).canvas.width == canvas["width"]


@pytest.mark.unit
def test_a_canvas_outside_the_supported_presets_is_refused() -> None:
    """An arbitrary canvas has no export preset, so it could never be rendered."""
    document = composition_document()
    document["canvas"] = {"width": 999, "height": 1001, "background": "#000000"}

    assert rejects(document)


@pytest.mark.unit
def test_a_colour_that_is_not_a_hex_triplet_is_refused() -> None:
    """Colours reach both a browser canvas and FFmpeg; only one spelling can do both."""
    document = composition_document()
    document["captions"]["style"]["color"] = "white"

    assert rejects(document)


@pytest.mark.unit
def test_a_broll_origin_must_name_the_suggestion_it_came_from() -> None:
    """Provenance is the whole point of the origin field; an unattributed B-roll item is not."""
    document = composition_document()
    document["overlays"][0]["origin"] = {
        "type": "brollSuggestion",
        "suggestionId": None,
        "provenanceId": None,
    }

    assert rejects(document)


@pytest.mark.unit
def test_a_source_origin_may_not_claim_a_suggestion() -> None:
    """Source footage was never suggested, so a suggestion reference there is a lie."""
    document = composition_document()
    document["tracks"][0]["items"][0]["origin"] = {
        "type": "source",
        "suggestionId": str(SUGGESTION_ID),
        "provenanceId": None,
    }

    assert rejects(document)


@pytest.mark.unit
def test_a_citation_overlay_must_reference_the_evidence_it_cites() -> None:
    """A citation with no evidence record is an unverifiable on-screen claim."""
    document = composition_document()
    document["overlays"][1]["claimEvidenceId"] = None

    assert rejects(document)


@pytest.mark.unit
def test_a_media_overlay_must_name_its_asset() -> None:
    """An image or video overlay with no asset would render as nothing."""
    document = composition_document()
    document["overlays"][0]["assetId"] = None

    assert rejects(document)


@pytest.mark.unit
def test_a_text_overlay_may_not_carry_an_asset() -> None:
    """Text is drawn, never loaded; an asset there would smuggle unowned media in."""
    document = composition_document()
    document["overlays"][1]["type"] = "text"
    document["overlays"][1]["assetId"] = str(BROLL_ASSET_ID)

    assert rejects(document)


@pytest.mark.unit
def test_a_template_or_brand_kit_reference_must_carry_its_version() -> None:
    """An unversioned template silently changes every clip that ever used it."""
    document = composition_document()
    document["template"] = {"id": str(TEMPLATE_ID)}

    assert rejects(document)


@pytest.mark.unit
def test_a_composition_may_omit_optional_template_and_brand_kit_references() -> None:
    """The first edit of a candidate has neither, and must still be a valid composition."""
    document = composition_document()
    document["template"] = None
    document["brandKit"] = None

    composition = parse_composition(document)

    assert composition.template is None
    assert composition.brand_kit is None


@pytest.mark.unit
def test_every_asset_a_composition_depends_on_can_be_collected_for_authorization() -> None:
    """Authorization is decided against the whole asset set, so none of it may hide."""
    composition = parse_composition(composition_document())

    assert collect_asset_ids(composition) == frozenset(
        {SOURCE_ASSET_ID, BROLL_ASSET_ID, LOGO_ASSET_ID}
    )


@pytest.mark.unit
def test_canonical_bytes_are_stable_across_key_order() -> None:
    """A hash that depends on key order would mark identical work as a change."""
    document = composition_document()
    shuffled = dict(reversed(list(document.items())))
    shuffled["canvas"] = dict(reversed(list(document["canvas"].items())))

    assert canonical_json(parse_composition(shuffled)) == canonical_json(
        parse_composition(document)
    )
    assert composition_hash(parse_composition(shuffled)) == composition_hash(
        parse_composition(document)
    )


@pytest.mark.unit
def test_canonical_bytes_are_compact_sorted_utf8() -> None:
    """Render artifacts are keyed by this hash, so the bytes behind it must be pinned."""
    raw = canonical_json(parse_composition(composition_document()))

    assert raw.startswith(b'{"audio":{"gainDb":0.0,"musicGainDb":-18.0},"bookmarks":[')
    assert raw == json.dumps(
        json.loads(raw), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


@pytest.mark.unit
def test_a_changed_value_changes_the_composition_hash() -> None:
    """Deduplicating renders by hash is only safe when the hash follows the content."""
    document = composition_document()
    changed = copy.deepcopy(document)
    changed["audio"]["gainDb"] = 1.0

    assert composition_hash(parse_composition(changed)) != composition_hash(
        parse_composition(document)
    )
    assert len(composition_hash(parse_composition(document))) == 32


@pytest.mark.unit
def test_unicode_text_survives_canonicalization_without_escaping() -> None:
    """Indonesian captions are ordinary content; escaping them would change the hash."""
    document = composition_document()
    document["captions"]["words"][0]["text"] = "Selamat—pagi"

    raw = canonical_json(parse_composition(document))

    assert "Selamat—pagi".encode() in raw


@pytest.mark.unit
def test_a_document_that_is_not_an_object_is_refused() -> None:
    """A list or a string reaching the parser is a client defect, not a composition."""
    with pytest.raises(CompositionValidationError):
        parse_composition([1, 2, 3])  # type: ignore[arg-type]


@pytest.mark.unit
def test_an_unparseable_asset_identifier_is_refused() -> None:
    """Asset references are UUIDs because authorization is decided by identity."""
    document = composition_document()
    document["tracks"][0]["items"][0]["sourceAssetId"] = "not-a-uuid"

    assert rejects(document)


@pytest.mark.unit
def test_an_empty_composition_still_names_its_source_and_duration() -> None:
    """A composition with no tracks is legal only if it is still a complete document."""
    document = composition_document()
    document["tracks"] = []
    document["overlays"] = []
    document["captions"]["words"] = []
    document["bookmarks"] = []

    composition = parse_composition(document)

    assert composition.duration_ms == 30_000
    assert collect_asset_ids(composition) == frozenset({SOURCE_ASSET_ID, LOGO_ASSET_ID})


@pytest.mark.unit
def test_a_composition_of_no_duration_is_refused() -> None:
    """A zero-length clip cannot be previewed, reviewed, or published."""
    document = composition_document()
    document["durationMs"] = 0

    assert rejects(document)


@pytest.mark.unit
def test_an_unknown_asset_reference_is_reported_by_identity() -> None:
    """The caller must learn which asset to refuse, not merely that one of them exists."""
    composition = parse_composition(composition_document())

    unauthorized = collect_asset_ids(composition) - {SOURCE_ASSET_ID}

    assert unauthorized == frozenset({BROLL_ASSET_ID, LOGO_ASSET_ID})
    assert uuid4() not in collect_asset_ids(composition)


@pytest.mark.unit
def test_a_crop_that_leaves_the_source_frame_is_refused() -> None:
    """A crop reaching outside the frame would render bars nobody asked for."""
    document = composition_document()
    document["tracks"][0]["items"][0]["crop"] = {
        "x": 0.6,
        "y": 0.0,
        "width": 0.5,
        "height": 1.0,
    }

    assert rejects(document)


@pytest.mark.unit
def test_a_font_weight_no_shipped_face_carries_is_refused() -> None:
    """A weight the renderer would synthesize looks different in preview and export."""
    document = composition_document()
    document["captions"]["style"]["weight"] = 650

    assert rejects(document)


@pytest.mark.unit
def test_a_caption_word_that_ends_before_it_starts_is_refused() -> None:
    """A reversed word has no highlight interval at all."""
    document = composition_document()
    document["captions"]["words"][0]["endMs"] = 0

    assert rejects(document)


@pytest.mark.unit
def test_an_overlay_whose_own_media_bounds_are_reversed_is_refused() -> None:
    """The overlay's source range is a second, independent way to describe no media."""
    document = composition_document()
    document["overlays"][0]["sourceOutMs"] = 0

    assert rejects(document)


@pytest.mark.unit
def test_a_bookmark_past_the_composition_is_refused() -> None:
    """A marker outside the clip could never be reached on the timeline."""
    document = composition_document()
    document["bookmarks"][0]["timelineMs"] = 31_000

    assert rejects(document)
