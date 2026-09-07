"""Brand constraints as data, and the one pure function that judges a composition by them.

A Brand Kit is a promise a Workspace makes about how its clips look and what they are
allowed to assert. Two rules run through everything here.

* **A constraint is data, never code.** The editor and the render compiler read the same
  published definition, so a clip that passes in the browser passes at export as well.
* **Nothing is corrected on the member's behalf.** Evaluation reports violations and
  returns; it never rewrites a colour, a font, or a word. A silently corrected clip is an
  export that no longer matches the preview somebody approved.

Nothing in this module reads a database, a clock, or a provider. Whether the assets a kit
names may be used is a separate question, answered by the caller with
:meth:`BrandKitDefinition.referenced_asset_ids`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from clipah.editor.models import (
    HEX_COLOR_PATTERN,
    BrandKitReference,
    CaptionMode,
    CaptionStyle,
    CitationOverlay,
    CompositionV1,
    FontFamily,
    ImageOverlay,
    Keyframe,
    Placement,
    TemplateReference,
    TextAlign,
    TextOverlay,
    TextStyle,
    VideoOverlay,
)

#: The element identifier a violation carries when the rule is about the whole clip
#: rather than one element a member could click on.
CANVAS_ELEMENT_ID = "canvas"
CAPTIONS_ELEMENT_ID = "captions"


class BrandModel(BaseModel):
    """The shared strictness every part of a Brand Kit definition inherits."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


class BrandViolationCode(StrEnum):
    """Every way a composition can contradict the Brand Kit it was built against.

    Closed rather than free text, because a member fixes a violation by acting on its
    code: an open vocabulary would leave the editor guessing which control to open.
    """

    COLOR_NOT_IN_KIT = "color_not_in_kit"
    FONT_NOT_IN_KIT = "font_not_in_kit"
    CAPTION_FONT_SIZE_OUT_OF_RANGE = "caption_font_size_out_of_range"
    CAPTION_ALIGNMENT_NOT_ALLOWED = "caption_alignment_not_allowed"
    TEXT_IN_RESERVED_AREA = "text_in_reserved_area"
    VISUAL_EXCLUDED = "visual_excluded"
    ATTRIBUTION_MISSING = "attribution_missing"
    FORBIDDEN_CLAIM = "forbidden_claim"


@dataclass(frozen=True, slots=True)
class BrandViolation:
    """One broken rule, named where a member can act on it.

    ``element_id`` is the composition element at fault, or nothing when the rule is about
    the clip as a whole — a missing attribution is not any one element's mistake.
    """

    code: BrandViolationCode
    element_id: str | None
    detail: str


class BrandColor(BrandModel):
    """One colour the brand publishes, and the name its members call it by."""

    name: Annotated[str, Field(min_length=1, max_length=60)]
    hex: Annotated[str, Field(pattern=HEX_COLOR_PATTERN)]


class BrandFont(BrandModel):
    """One face the brand draws type in, and the licensed file behind it, if any.

    ``asset_id`` names an uploaded font file the Workspace owns. It is deliberately not
    resolved here: this module has no standing to decide whether a Workspace may use an
    asset, so it reports the identifier and leaves the authorization to its caller.
    """

    family: FontFamily
    asset_id: UUID | None


class CaptionRules(BrandModel):
    """The band drawn type has to stay inside, and the areas it must stay out of.

    ``reserved_placements`` is the safe zone expressed the way a composition expresses
    position: a brand that keeps the lower third clear does so because a platform's own
    interface covers it, and a caption nobody can read is a caption that was not written.
    """

    min_font_size: Annotated[int, Field(ge=12, le=200)]
    max_font_size: Annotated[int, Field(ge=12, le=200)]
    allowed_alignments: tuple[TextAlign, ...] = Field(min_length=1)
    reserved_placements: tuple[Placement, ...]

    @model_validator(mode="after")
    def _reject_a_band_no_clip_could_satisfy(self) -> Self:
        """Refuse a floor above its ceiling, which would fail every composition."""
        if self.min_font_size > self.max_font_size:
            raise ValueError("minFontSize must not exceed maxFontSize")
        return self


class ClaimRules(BrandModel):
    """What this brand always says, and what it may never say.

    Forbidden claims are exact phrases rather than patterns. A Workspace-authored regular
    expression would be a denial-of-service surface evaluated on every save, and a phrase
    is what a legal review actually produces.
    """

    required_attribution: Annotated[str, Field(min_length=1, max_length=200)] | None
    forbidden_claim_phrases: tuple[Annotated[str, Field(min_length=1, max_length=200)], ...]


class BrandKitDefinition(BrandModel):
    """One immutable version of everything a Brand Kit constrains."""

    logo_asset_id: UUID | None
    fonts: tuple[BrandFont, ...] = Field(min_length=1)
    colors: tuple[BrandColor, ...] = Field(min_length=1)
    caption_rules: CaptionRules
    visual_exclusions: tuple[Annotated[str, Field(min_length=1, max_length=120)], ...]
    claim_rules: ClaimRules

    def referenced_asset_ids(self) -> frozenset[UUID]:
        """Report every Workspace asset this kit depends on, for the caller to authorize."""
        assets = {font.asset_id for font in self.fonts if font.asset_id is not None}
        if self.logo_asset_id is not None:
            assets.add(self.logo_asset_id)
        return frozenset(assets)

    def allowed_colors(self) -> frozenset[str]:
        """Report the published colours, compared without regard to letter case."""
        return frozenset(color.hex.upper() for color in self.colors)

    def allowed_font_families(self) -> frozenset[FontFamily]:
        """Report the faces this brand draws type in."""
        return frozenset(font.family for font in self.fonts)


class TemplateKind(StrEnum):
    """The kinds of reusable look a Workspace may publish.

    Version 1 publishes one kind. The vocabulary is closed rather than free text so a
    later kind arrives as a value the API already refuses today, rather than as a column
    nobody validates.
    """

    CLIP_LOOK = "clip_look"


class WorkspaceTemplateDefinition(BrandModel):
    """One immutable version of a Workspace-owned look."""

    kind: TemplateKind
    caption_mode: CaptionMode
    caption_style: CaptionStyle
    text_style: TextStyle


def apply_workspace_template(
    composition: CompositionV1,
    *,
    template_id: UUID,
    version: int,
    definition: WorkspaceTemplateDefinition,
) -> CompositionV1:
    """Write one Workspace look into a composition, recording the version it came from.

    Only type and colour change. The clip, its items, its caption timings, and its words
    are the member's own work, and a look is never allowed to touch them. The reference is
    provenance rather than a lookup: the returned composition renders identically forever,
    even once this template has been replaced.
    """
    overlays = tuple(
        overlay.model_copy(update={"style": definition.text_style})
        if isinstance(overlay, TextOverlay)
        else overlay
        for overlay in composition.overlays
    )
    return composition.model_copy(
        update={
            "template": TemplateReference(id=template_id, version=version),
            "captions": composition.captions.model_copy(
                update={"mode": definition.caption_mode, "style": definition.caption_style}
            ),
            "overlays": overlays,
        }
    )


def apply_brand_kit(
    composition: CompositionV1,
    *,
    brand_kit_id: UUID,
    version: int,
    logo_asset_id: UUID | None,
) -> CompositionV1:
    """Record which Brand Kit version this composition is judged against.

    A kit says what a look may be, never what it is, so applying one restyles nothing. The
    version is written down because a kit edited next month must not silently re-judge a
    Revision that was approved under the rules of this one.
    """
    return composition.model_copy(
        update={
            "brand_kit": BrandKitReference(
                id=brand_kit_id, version=version, logo_asset_id=logo_asset_id
            )
        }
    )


def evaluate_brand_constraints(
    composition: CompositionV1,
    *,
    definition: BrandKitDefinition,
    asset_descriptions: Mapping[UUID, str] | None = None,
) -> tuple[BrandViolation, ...]:
    """Report every rule this composition breaks, in a stable order, changing nothing.

    ``asset_descriptions`` carries what the placed media is known to depict, because a
    member cannot see inside a stock clip and this module cannot open one. Media nobody
    described is left alone: an absent description is unknown, and unknown is not evidence
    that a brand's exclusion was breached.
    """
    return tuple(_violations(composition, definition, asset_descriptions or {}))


def _violations(
    composition: CompositionV1,
    definition: BrandKitDefinition,
    descriptions: Mapping[UUID, str],
) -> Iterator[BrandViolation]:
    """Walk the composition once, in the order a member reads it."""
    yield from _canvas_violations(composition, definition)
    yield from _caption_violations(composition, definition)
    for overlay in composition.overlays:
        yield from _overlay_violations(overlay, definition, descriptions)
    for track in composition.tracks:
        for item in track.items:
            yield from _keyframe_violations(item.id, item.keyframes, definition)
    yield from _claim_violations(composition, definition)


def _canvas_violations(
    composition: CompositionV1, definition: BrandKitDefinition
) -> Iterator[BrandViolation]:
    """Judge the frame behind everything, which is as much the brand as the type on it."""
    yield from _color_violations(CANVAS_ELEMENT_ID, (composition.canvas.background,), definition)


def _caption_violations(
    composition: CompositionV1, definition: BrandKitDefinition
) -> Iterator[BrandViolation]:
    """Judge the caption layer, unless this clip draws no captions at all."""
    captions = composition.captions
    if captions.mode is CaptionMode.OFF:
        return
    style = captions.style
    colors = [style.color]
    if captions.mode is CaptionMode.KARAOKE:
        colors.append(style.highlight_color)
    if style.background_enabled:
        colors.append(style.background_color)
    yield from _color_violations(CAPTIONS_ELEMENT_ID, colors, definition)
    yield from _type_violations(CAPTIONS_ELEMENT_ID, style, definition)


def _overlay_violations(
    overlay: VideoOverlay | ImageOverlay | TextOverlay | CitationOverlay,
    definition: BrandKitDefinition,
    descriptions: Mapping[UUID, str],
) -> Iterator[BrandViolation]:
    """Judge one overlay: its type where it draws type, its subject where it draws media."""
    if isinstance(overlay, TextOverlay | CitationOverlay):
        colors = [overlay.style.color]
        if overlay.style.background_enabled:
            colors.append(overlay.style.background_color)
        yield from _color_violations(overlay.id, colors, definition)
        yield from _type_violations(overlay.id, overlay.style, definition)
        if overlay.placement in definition.caption_rules.reserved_placements:
            yield BrandViolation(
                code=BrandViolationCode.TEXT_IN_RESERVED_AREA,
                element_id=overlay.id,
                detail=f"this brand keeps the {overlay.placement.value} area clear of type",
            )
        # Type a member wrote is content this brand publishes, so an excluded subject
        # written on screen breaches the same rule as one shown on screen. The caption
        # layer is deliberately not judged this way: those are the speaker's own words,
        # and a brand exclusion is not a licence to rewrite what somebody said.
        yield from _exclusion_violations(overlay.id, overlay.text, definition)
    if isinstance(overlay, VideoOverlay | ImageOverlay):
        described = descriptions.get(overlay.asset_id)
        if described is not None:
            yield from _exclusion_violations(overlay.id, described, definition)
    yield from _keyframe_violations(overlay.id, overlay.keyframes, definition)


def _keyframe_violations(
    element_id: str, keyframes: Iterable[Keyframe], definition: BrandKitDefinition
) -> Iterator[BrandViolation]:
    """Judge the type a keyframe animates towards, which is drawn like any other type."""
    for keyframe in keyframes:
        if keyframe.style is None:
            continue
        colors = [keyframe.style.color]
        if keyframe.style.background_enabled:
            colors.append(keyframe.style.background_color)
        yield from _color_violations(element_id, colors, definition)
        yield from _type_violations(element_id, keyframe.style, definition)


def _color_violations(
    element_id: str, colors: Iterable[str], definition: BrandKitDefinition
) -> Iterator[BrandViolation]:
    """Report each colour this brand never published, named so a member can swap it."""
    allowed = definition.allowed_colors()
    for color in colors:
        if color.upper() not in allowed:
            yield BrandViolation(
                code=BrandViolationCode.COLOR_NOT_IN_KIT,
                element_id=element_id,
                detail=f"{color} is not one of this brand's colours",
            )


def _type_violations(
    element_id: str, style: TextStyle, definition: BrandKitDefinition
) -> Iterator[BrandViolation]:
    """Report a face, a size, or an alignment this brand does not draw type in."""
    rules = definition.caption_rules
    if style.font_family not in definition.allowed_font_families():
        yield BrandViolation(
            code=BrandViolationCode.FONT_NOT_IN_KIT,
            element_id=element_id,
            detail=f"{style.font_family.value} is not one of this brand's fonts",
        )
    if not rules.min_font_size <= style.font_size <= rules.max_font_size:
        yield BrandViolation(
            code=BrandViolationCode.CAPTION_FONT_SIZE_OUT_OF_RANGE,
            element_id=element_id,
            detail=(
                f"this brand draws type between {rules.min_font_size} and "
                f"{rules.max_font_size}, not {style.font_size}"
            ),
        )
    if style.align not in rules.allowed_alignments:
        allowed = ", ".join(alignment.value for alignment in rules.allowed_alignments)
        yield BrandViolation(
            code=BrandViolationCode.CAPTION_ALIGNMENT_NOT_ALLOWED,
            element_id=element_id,
            detail=f"this brand aligns type {allowed}, not {style.align.value}",
        )


def _exclusion_violations(
    element_id: str, text: str, definition: BrandKitDefinition
) -> Iterator[BrandViolation]:
    """Report each excluded subject this text names."""
    for exclusion in definition.visual_exclusions:
        if _mentions(text, exclusion):
            yield BrandViolation(
                code=BrandViolationCode.VISUAL_EXCLUDED,
                element_id=element_id,
                detail=f"this brand excludes {exclusion}",
            )


def _claim_violations(
    composition: CompositionV1, definition: BrandKitDefinition
) -> Iterator[BrandViolation]:
    """Judge what this clip asserts, and whether it says what the brand owes."""
    rules = definition.claim_rules
    drawn = tuple(
        (overlay.id, overlay.text)
        for overlay in composition.overlays
        if isinstance(overlay, TextOverlay | CitationOverlay)
    )
    spoken = " ".join(word.text for word in composition.captions.words)
    for phrase in rules.forbidden_claim_phrases:
        for element_id, text in drawn:
            if _mentions(text, phrase):
                yield BrandViolation(
                    code=BrandViolationCode.FORBIDDEN_CLAIM,
                    element_id=element_id,
                    detail=f"this brand may not claim {phrase}",
                )
        if composition.captions.mode is not CaptionMode.OFF and _mentions(spoken, phrase):
            yield BrandViolation(
                code=BrandViolationCode.FORBIDDEN_CLAIM,
                element_id=CAPTIONS_ELEMENT_ID,
                detail=f"this brand may not claim {phrase}",
            )
    if rules.required_attribution is not None and not any(
        _mentions(text, rules.required_attribution) for _, text in drawn
    ):
        yield BrandViolation(
            code=BrandViolationCode.ATTRIBUTION_MISSING,
            element_id=None,
            detail=f"this brand requires {rules.required_attribution} on screen",
        )


def _mentions(text: str, phrase: str) -> bool:
    """Report whether one phrase is said in this text, as a phrase rather than a fragment.

    Whole words only: a rule that fired on a fragment of an unrelated word would be noise
    a member learns to click past, which is the same as having no rule.
    """
    pattern = r"\s+".join(re.escape(part) for part in phrase.split())
    return re.search(rf"(?<!\w){pattern}(?!\w)", text, flags=re.IGNORECASE) is not None
