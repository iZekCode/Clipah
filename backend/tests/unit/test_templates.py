"""Built-in templates and motion presets: immutable, versioned, and self-describing.

A template is how a member gets a whole look in one action, and a motion preset is how
one element is allowed to move. Both are published as data with an explicit version, and
a composition records the exact version it was built against, so a later change to a
template can never rewrite a Revision somebody already approved.

Nothing here reads a database, a clock, or a provider: a template is decided entirely by
its own definition, and applying one is a pure transformation of one composition.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from clipah.editor.models import (
    CaptionMode,
    MotionPreset,
    canonical_json,
    parse_composition,
)
from clipah.renders.templates import (
    BUILT_IN_TEMPLATES,
    MOTION_DEFINITIONS,
    TemplateNotFoundError,
    apply_template,
    motion_definition,
    reject_unknown_template,
    template_definition,
    templates_document,
)

SOURCE_ASSET_ID = UUID("11111111-1111-4111-8111-111111111111")


def composition_document(**overrides: Any) -> dict[str, Any]:
    """One plain composition, before any template has been applied to it."""
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
            "style": {
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
            },
        },
        "overlays": [
            {
                "id": "title-1",
                "type": "text",
                "timelineStartMs": 1_000,
                "timelineEndMs": 5_000,
                "placement": "top",
                "opacity": 1.0,
                "keyframes": [],
                "motion": "none",
                "text": "A written title",
                "style": {
                    "fontFamily": "Inter",
                    "fontSize": 36,
                    "color": "#FFFFFF",
                    "align": "left",
                    "weight": 400,
                    "italic": False,
                    "decoration": "none",
                    "letterSpacing": 0.0,
                    "lineHeight": 1.2,
                    "backgroundEnabled": False,
                    "backgroundColor": "#101010",
                },
            }
        ],
        "audio": {"gainDb": 0.0, "musicGainDb": -18.0},
        "bookmarks": [],
    }
    document.update(overrides)
    return document


@pytest.mark.unit
def test_every_built_in_template_names_its_own_identity_and_version() -> None:
    """A composition records both, so a later template edit cannot rewrite a Revision."""
    assert BUILT_IN_TEMPLATES

    for template in BUILT_IN_TEMPLATES:
        assert template.version > 0
        assert template.name
        assert template_definition(template.id, template.version) is template


@pytest.mark.unit
def test_two_built_in_templates_never_share_one_identity_and_version() -> None:
    """Resolving a reference has to have exactly one answer."""
    references = [(template.id, template.version) for template in BUILT_IN_TEMPLATES]

    assert len(set(references)) == len(references)


@pytest.mark.unit
def test_a_template_that_was_never_published_is_refused_rather_than_guessed() -> None:
    """A near miss on a version is a different look, so it may not be approximated."""
    known = BUILT_IN_TEMPLATES[0]

    with pytest.raises(TemplateNotFoundError):
        template_definition(known.id, known.version + 1)
    with pytest.raises(TemplateNotFoundError):
        template_definition(uuid4(), 1)


@pytest.mark.unit
def test_applying_a_template_writes_its_look_into_the_composition_itself() -> None:
    """A Revision has to be renderable on its own, without resolving a template again."""
    template = template_definition(BUILT_IN_TEMPLATES[0].id, BUILT_IN_TEMPLATES[0].version)
    composition = parse_composition(composition_document())

    styled = apply_template(composition, template)

    assert styled.template is not None
    assert (styled.template.id, styled.template.version) == (template.id, template.version)
    assert styled.captions.style == template.caption_style
    assert styled.captions.mode is template.caption_mode
    assert styled.overlays[0].style == template.text_style  # type: ignore[union-attr]


@pytest.mark.unit
def test_applying_a_template_changes_the_look_and_nothing_else() -> None:
    """A look is type and colour; it is never a different clip."""
    template = template_definition(BUILT_IN_TEMPLATES[0].id, BUILT_IN_TEMPLATES[0].version)
    composition = parse_composition(composition_document())

    styled = apply_template(composition, template)

    assert styled.tracks == composition.tracks
    assert styled.duration_ms == composition.duration_ms
    assert styled.source_range == composition.source_range
    assert [word.start_ms for word in styled.captions.words] == [
        word.start_ms for word in composition.captions.words
    ]
    assert [word.text for word in styled.captions.words] == [
        word.text for word in composition.captions.words
    ]


@pytest.mark.unit
def test_applying_one_template_twice_produces_exactly_the_same_document() -> None:
    """Re-applying is how a member undoes an experiment, so it may not accumulate."""
    template = template_definition(BUILT_IN_TEMPLATES[1].id, BUILT_IN_TEMPLATES[1].version)
    composition = parse_composition(composition_document())

    once = apply_template(composition, template)
    twice = apply_template(once, template)

    assert canonical_json(once) == canonical_json(twice)


@pytest.mark.unit
@pytest.mark.parametrize("template_index", range(3))
def test_every_built_in_template_produces_a_document_the_validator_accepts(
    template_index: int,
) -> None:
    """A look nobody could save would be a look nobody could use."""
    template = BUILT_IN_TEMPLATES[template_index]
    composition = parse_composition(composition_document())

    styled = apply_template(composition, template)

    assert parse_composition(json.loads(canonical_json(styled))) == styled


@pytest.mark.unit
def test_a_composition_naming_an_unpublished_built_in_template_is_refused() -> None:
    """The reference is provenance, and provenance that names nothing is not provenance."""
    known = BUILT_IN_TEMPLATES[0]
    document = composition_document(
        template={"id": str(known.id), "version": known.version + 5},
    )

    with pytest.raises(TemplateNotFoundError):
        reject_unknown_template(parse_composition(document))


@pytest.mark.unit
def test_a_composition_naming_a_workspace_template_is_left_to_its_own_owner() -> None:
    """Workspace templates are rows rather than built-ins; this module may not judge them."""
    document = composition_document(template={"id": str(uuid4()), "version": 3})

    reject_unknown_template(parse_composition(document))


@pytest.mark.unit
def test_a_composition_with_no_template_reference_is_accepted() -> None:
    """Most clips are edited without a template, and that is not an error."""
    reject_unknown_template(parse_composition(composition_document()))


@pytest.mark.unit
@pytest.mark.parametrize("preset", list(MotionPreset))
def test_every_motion_preset_publishes_the_bounds_it_is_legible_within(
    preset: MotionPreset,
) -> None:
    """A drift too short to see, or long enough to crawl, is not the effect it names."""
    definition = motion_definition(preset)

    assert definition.preset is preset
    assert definition.min_duration_ms <= definition.max_duration_ms
    assert definition.description
    if preset is MotionPreset.NONE:
        assert definition.min_duration_ms == 0
    else:
        assert definition.min_duration_ms > 0


@pytest.mark.unit
def test_the_motion_table_covers_every_preset_the_schema_allows() -> None:
    """A preset with no definition would be a preset with no rules at all."""
    assert set(MOTION_DEFINITIONS) == set(MotionPreset)


@pytest.mark.unit
def test_the_published_document_carries_every_template_and_motion_by_version() -> None:
    """The editor reads this document, so one definition serves both sides."""
    document = templates_document()

    assert document["templatesVersion"] == 1
    assert [entry["id"] for entry in document["templates"]] == [
        str(template.id) for template in BUILT_IN_TEMPLATES
    ]
    assert [entry["version"] for entry in document["templates"]] == [
        template.version for template in BUILT_IN_TEMPLATES
    ]
    assert {entry["preset"] for entry in document["motions"]} == {
        preset.value for preset in MotionPreset
    }
    assert json.loads(json.dumps(document)) == document


@pytest.mark.unit
def test_the_published_captions_carry_a_karaoke_look_and_a_plain_one() -> None:
    """Section 9 requires both, and a member picks between them by name."""
    modes = {template.caption_mode for template in BUILT_IN_TEMPLATES}

    assert CaptionMode.KARAOKE in modes
    assert CaptionMode.BLOCK in modes
