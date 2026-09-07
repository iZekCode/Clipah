"""Applying a Workspace-owned template or Brand Kit to one composition.

A template is how a member gets a whole look in one action, and a Brand Kit is what that
look is allowed to be. Both are versioned, and both are written into the composition by
value: a Revision records the exact version it was built against, so replacing a template
tomorrow can never rewrite a clip somebody approved today.

Nothing in this suite reads a database, a clock, or a provider.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from brand_fixtures import caption_style, composition_document, text_overlay, text_style
from clipah.brands.models import (
    TemplateKind,
    WorkspaceTemplateDefinition,
    apply_brand_kit,
    apply_workspace_template,
)
from clipah.editor.models import CaptionMode, TextOverlay, parse_composition
from clipah.renders.templates import reject_unknown_template

TEMPLATE_ID = UUID("44444444-4444-4444-8444-444444444444")


def template_definition(**overrides: Any) -> WorkspaceTemplateDefinition:
    """One Workspace-owned look, written out from a caption and a text style."""
    values: dict[str, Any] = {
        "kind": TemplateKind.CLIP_LOOK,
        "caption_mode": CaptionMode.KARAOKE,
        "caption_style": caption_style(fontFamily="Anton", fontSize=64, color="#FFD166"),
        "text_style": text_style(fontFamily="Anton", fontSize=52),
    }
    values.update(overrides)
    return WorkspaceTemplateDefinition.model_validate(values)


@pytest.mark.unit
def test_applying_a_template_writes_its_look_into_the_composition_itself() -> None:
    """A Revision has to render on its own, without resolving a template row again."""
    composition = parse_composition(composition_document())

    applied = apply_workspace_template(
        composition, template_id=TEMPLATE_ID, version=3, definition=template_definition()
    )

    assert applied.captions.mode is CaptionMode.KARAOKE
    assert applied.captions.style.font_family.value == "Anton"
    assert applied.captions.style.color == "#FFD166"
    overlay = applied.overlays[0]
    assert isinstance(overlay, TextOverlay)
    assert overlay.style.font_family.value == "Anton"


@pytest.mark.unit
def test_applying_a_template_records_the_exact_version_it_came_from() -> None:
    """A later version of the same template is a different look, and says so."""
    composition = parse_composition(composition_document())

    applied = apply_workspace_template(
        composition, template_id=TEMPLATE_ID, version=3, definition=template_definition()
    )

    assert applied.template is not None
    assert applied.template.id == TEMPLATE_ID
    assert applied.template.version == 3


@pytest.mark.unit
def test_a_template_never_touches_the_member_s_own_editing_decisions() -> None:
    """Type and colour are the template's; the cut, the timings, and the words are not."""
    composition = parse_composition(composition_document())

    applied = apply_workspace_template(
        composition, template_id=TEMPLATE_ID, version=1, definition=template_definition()
    )

    assert applied.tracks == composition.tracks
    assert applied.source_range == composition.source_range
    assert applied.duration_ms == composition.duration_ms
    assert tuple(word.text for word in applied.captions.words) == tuple(
        word.text for word in composition.captions.words
    )
    assert tuple(
        overlay.text for overlay in applied.overlays if isinstance(overlay, TextOverlay)
    ) == (text_overlay()["text"],)


@pytest.mark.unit
def test_applying_a_template_leaves_the_composition_it_was_given_alone() -> None:
    """The caller decides whether the new look is saved, so the old one has to survive."""
    composition = parse_composition(composition_document())

    apply_workspace_template(
        composition, template_id=TEMPLATE_ID, version=1, definition=template_definition()
    )

    assert composition.template is None
    assert composition.captions.mode is CaptionMode.BLOCK


@pytest.mark.unit
def test_a_workspace_template_reference_is_not_judged_against_the_built_in_catalogue() -> None:
    """Built-ins and Workspace rows are different namespaces, and one may not veto the other."""
    composition = parse_composition(composition_document())
    applied = apply_workspace_template(
        composition, template_id=TEMPLATE_ID, version=9, definition=template_definition()
    )

    reject_unknown_template(applied)


@pytest.mark.unit
def test_applying_a_brand_kit_records_its_version_without_restyling_the_clip() -> None:
    """A kit says what a look may be, not what it is: applying one changes no type."""
    logo_asset_id = uuid4()
    composition = parse_composition(composition_document())

    applied = apply_brand_kit(
        composition, brand_kit_id=TEMPLATE_ID, version=2, logo_asset_id=logo_asset_id
    )

    assert applied.brand_kit is not None
    assert applied.brand_kit.id == TEMPLATE_ID
    assert applied.brand_kit.version == 2
    assert applied.brand_kit.logo_asset_id == logo_asset_id
    assert applied.captions == composition.captions
    assert applied.overlays == composition.overlays


@pytest.mark.unit
def test_a_template_definition_is_frozen_once_it_is_published() -> None:
    """A version is a promise, and a promise that can be edited in place is not one."""
    definition = template_definition()

    with pytest.raises(ValueError):
        definition.caption_mode = CaptionMode.OFF  # type: ignore[misc]
