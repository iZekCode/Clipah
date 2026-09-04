"""The built-in looks a member can apply, and the motion each element may be given.

Two things live here, and both are published as data with an explicit version.

* **Templates** are complete looks: caption type, caption mode, and the type drawn text
  is written in. Applying one writes those values into the composition itself and records
  which template version it came from, so a Revision renders identically forever even if
  the template is later replaced. The reference is provenance, never a lookup the renderer
  depends on.
* **Motion presets** name how an element moves, and the window in which that movement is
  legible. A drift too short to notice or long enough to crawl is not the effect it names,
  so each preset publishes the duration bounds the renderer will accept.

Nothing here reads a database, a clock, or a provider. Workspace-owned templates are rows
rather than built-ins and belong to the task that creates them; this module deliberately
refuses to judge a reference it did not publish.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from clipah.editor.models import (
    CaptionMode,
    CaptionStyle,
    CompositionV1,
    FontFamily,
    MotionPreset,
    TemplateReference,
    TextAlign,
    TextDecoration,
    TextOverlay,
    TextStyle,
)

# The version of this published document itself, so a browser reading it can tell whether
# it understands the shape it was given.
TEMPLATES_DOCUMENT_VERSION = 1
# Nothing is allowed to move for longer than one composition may last.
MAX_MOTION_DURATION_MS = 600_000


class TemplateNotFoundError(Exception):
    """A built-in template version that was never published."""


@dataclass(frozen=True, slots=True)
class MotionDefinition:
    """One named movement and the window in which it reads as that movement."""

    preset: MotionPreset
    min_duration_ms: int
    max_duration_ms: int
    description: str


@dataclass(frozen=True, slots=True)
class TemplateDefinition:
    """One complete look, at one immutable version."""

    id: UUID
    version: int
    name: str
    description: str
    caption_mode: CaptionMode
    caption_style: CaptionStyle
    text_style: TextStyle


MOTION_DEFINITIONS: Mapping[MotionPreset, MotionDefinition] = {
    MotionPreset.NONE: MotionDefinition(
        preset=MotionPreset.NONE,
        min_duration_ms=0,
        max_duration_ms=MAX_MOTION_DURATION_MS,
        description="The element is still.",
    ),
    MotionPreset.FADE: MotionDefinition(
        preset=MotionPreset.FADE,
        # A fade is 300ms in and 300ms out, so anything shorter has no middle at all.
        min_duration_ms=800,
        max_duration_ms=MAX_MOTION_DURATION_MS,
        description="The element fades in and back out.",
    ),
    MotionPreset.KEN_BURNS_IN: MotionDefinition(
        preset=MotionPreset.KEN_BURNS_IN,
        # Below about half a second a drift reads as a lurch rather than a movement.
        min_duration_ms=600,
        max_duration_ms=30_000,
        description="A still drifts slowly towards the viewer.",
    ),
    MotionPreset.KEN_BURNS_OUT: MotionDefinition(
        preset=MotionPreset.KEN_BURNS_OUT,
        min_duration_ms=600,
        max_duration_ms=30_000,
        description="A still drifts slowly away from the viewer.",
    ),
    MotionPreset.PAN_LEFT: MotionDefinition(
        preset=MotionPreset.PAN_LEFT,
        min_duration_ms=600,
        max_duration_ms=30_000,
        description="A still travels to the left.",
    ),
    MotionPreset.PAN_RIGHT: MotionDefinition(
        preset=MotionPreset.PAN_RIGHT,
        min_duration_ms=600,
        max_duration_ms=30_000,
        description="A still travels to the right.",
    ),
    MotionPreset.SLIDE_UP: MotionDefinition(
        preset=MotionPreset.SLIDE_UP,
        min_duration_ms=600,
        max_duration_ms=15_000,
        description="The element arrives from below.",
    ),
}


def _caption_style(**overrides: Any) -> CaptionStyle:
    """One caption style, written out from the defaults a template starts from."""
    values: dict[str, Any] = {
        "font_family": FontFamily.MONTSERRAT,
        "font_size": 64,
        "color": "#FFFFFF",
        "align": TextAlign.CENTER,
        "weight": 700,
        "italic": False,
        "decoration": TextDecoration.NONE,
        "letter_spacing": 0.0,
        "line_height": 1.2,
        "background_enabled": False,
        "background_color": "#000000",
        "highlight_color": "#FFD166",
    }
    values.update(overrides)
    return CaptionStyle(**values)


def _text_style(**overrides: Any) -> TextStyle:
    """One drawn-text style, written out from the defaults a template starts from."""
    values: dict[str, Any] = {
        "font_family": FontFamily.MONTSERRAT,
        "font_size": 48,
        "color": "#FFFFFF",
        "align": TextAlign.CENTER,
        "weight": 600,
        "italic": False,
        "decoration": TextDecoration.NONE,
        "letter_spacing": 0.0,
        "line_height": 1.2,
        "background_enabled": False,
        "background_color": "#000000",
    }
    values.update(overrides)
    return TextStyle(**values)


BUILT_IN_TEMPLATES: tuple[TemplateDefinition, ...] = (
    TemplateDefinition(
        id=UUID("0f2a1b6c-0000-4000-8000-000000000001"),
        version=1,
        name="Clean captions",
        description="Quiet type that stays out of the way of the picture.",
        caption_mode=CaptionMode.BLOCK,
        caption_style=_caption_style(
            font_family=FontFamily.INTER,
            font_size=56,
            weight=600,
            background_enabled=True,
            background_color="#101010",
        ),
        text_style=_text_style(font_family=FontFamily.INTER, font_size=42, weight=500),
    ),
    TemplateDefinition(
        id=UUID("0f2a1b6c-0000-4000-8000-000000000002"),
        version=1,
        name="Bold karaoke",
        description="Heavy type that highlights each word as it is said.",
        caption_mode=CaptionMode.KARAOKE,
        caption_style=_caption_style(
            font_family=FontFamily.ANTON,
            font_size=72,
            weight=900,
            letter_spacing=1.0,
            highlight_color="#FFD166",
        ),
        text_style=_text_style(font_family=FontFamily.ANTON, font_size=56, weight=900),
    ),
    TemplateDefinition(
        id=UUID("0f2a1b6c-0000-4000-8000-000000000003"),
        version=1,
        name="Documentary",
        description="Left-aligned captions and a lower-third title face.",
        caption_mode=CaptionMode.BLOCK,
        caption_style=_caption_style(
            font_family=FontFamily.ROBOTO,
            font_size=48,
            align=TextAlign.LEFT,
            weight=400,
            line_height=1.35,
            background_enabled=True,
            background_color="#000000",
        ),
        text_style=_text_style(
            font_family=FontFamily.ROBOTO,
            font_size=36,
            align=TextAlign.LEFT,
            weight=400,
            background_enabled=True,
            background_color="#101010",
        ),
    ),
)

_BY_REFERENCE: Mapping[tuple[UUID, int], TemplateDefinition] = {
    (template.id, template.version): template for template in BUILT_IN_TEMPLATES
}
_BUILT_IN_IDS: frozenset[UUID] = frozenset(template.id for template in BUILT_IN_TEMPLATES)


def template_definition(template_id: UUID, version: int) -> TemplateDefinition:
    """Resolve one published template version, or refuse to approximate it."""
    template = _BY_REFERENCE.get((template_id, version))
    if template is None:
        raise TemplateNotFoundError(f"{template_id} v{version}")
    return template


def motion_definition(preset: MotionPreset) -> MotionDefinition:
    """Report the window one movement is legible within."""
    return MOTION_DEFINITIONS[preset]


def apply_template(composition: CompositionV1, template: TemplateDefinition) -> CompositionV1:
    """Write one look into a composition, and record the version it came from.

    Only type and colour change. The clip, its items, its caption timings, and its words
    are the member's own work, and a look is never allowed to touch them.
    """
    overlays = tuple(
        overlay.model_copy(update={"style": template.text_style})
        if isinstance(overlay, TextOverlay)
        else overlay
        for overlay in composition.overlays
    )
    return composition.model_copy(
        update={
            "template": TemplateReference(id=template.id, version=template.version),
            "captions": composition.captions.model_copy(
                update={"mode": template.caption_mode, "style": template.caption_style}
            ),
            "overlays": overlays,
        }
    )


def reject_unknown_template(composition: CompositionV1) -> None:
    """Refuse a composition that names a built-in template version nobody published.

    A reference to a Workspace-owned template is left alone: those are rows, owned by the
    task that creates them, and this module has no standing to judge them.
    """
    reference = composition.template
    if reference is None or reference.id not in _BUILT_IN_IDS:
        return
    template_definition(reference.id, reference.version)


def templates_document() -> dict[str, Any]:
    """Publish every template and motion preset as the data both sides read."""
    return {
        "templatesVersion": TEMPLATES_DOCUMENT_VERSION,
        "templates": [
            {
                "id": str(template.id),
                "version": template.version,
                "name": template.name,
                "description": template.description,
                "captionMode": template.caption_mode.value,
                "captionStyle": template.caption_style.model_dump(mode="json", by_alias=True),
                "textStyle": template.text_style.model_dump(mode="json", by_alias=True),
            }
            for template in BUILT_IN_TEMPLATES
        ],
        "motions": [
            {
                "preset": definition.preset.value,
                "minDurationMs": definition.min_duration_ms,
                "maxDurationMs": definition.max_duration_ms,
                "description": definition.description,
            }
            for definition in MOTION_DEFINITIONS.values()
        ],
    }
