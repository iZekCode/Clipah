"""Composition version 1: the one document a preview and a render both obey.

The composition is the durable record of a member's editing decisions. It is
deliberately strict: an unknown track type, an impossible time range, or an
unattributed B-roll placement is refused at save time, because the alternative is
an export that does not match the preview a reviewer approved.

Nothing in this module reads a database, a clock, or a provider. A composition is
decided entirely by its own contents; whether the assets it names may be used is a
separate question, answered by the caller with :func:`collect_asset_ids`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from enum import StrEnum
from itertools import pairwise
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic.alias_generators import to_camel

SCHEMA_VERSION = 1
MAX_COMPOSITION_DURATION_MS = 600_000
SUPPORTED_CANVAS_SIZES: frozenset[tuple[int, int]] = frozenset(
    {(1080, 1920), (1920, 1080), (1080, 1080), (1080, 1350)}
)
HEX_COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"
SUPPORTED_FONT_WEIGHTS = (300, 400, 500, 600, 700, 800, 900)

Finite = Annotated[float, Field(allow_inf_nan=False)]
Color = Annotated[str, Field(pattern=HEX_COLOR_PATTERN)]
Milliseconds = Annotated[int, Field(ge=0, le=MAX_COMPOSITION_DURATION_MS)]
ElementId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]


class CompositionValidationError(Exception):
    """A document that is not a valid version 1 composition, with the field at fault."""


class TrackType(StrEnum):
    """The kinds of timeline lane a composition may carry."""

    VIDEO = "video"
    AUDIO = "audio"
    MUSIC = "music"
    EXTRACTED_AUDIO = "extractedAudio"


class BlendMode(StrEnum):
    """The compositing modes both the browser preview and FFmpeg can reproduce."""

    NORMAL = "normal"
    MULTIPLY = "multiply"
    SCREEN = "screen"
    OVERLAY = "overlay"
    DARKEN = "darken"
    LIGHTEN = "lighten"


class MotionPreset(StrEnum):
    """The named animations an item or overlay may be given."""

    NONE = "none"
    KEN_BURNS_IN = "kenBurnsIn"
    KEN_BURNS_OUT = "kenBurnsOut"
    PAN_LEFT = "panLeft"
    PAN_RIGHT = "panRight"
    SLIDE_UP = "slideUp"
    FADE = "fade"


class Easing(StrEnum):
    """How a value travels between two keyframes."""

    LINEAR = "linear"
    EASE_IN = "easeIn"
    EASE_OUT = "easeOut"
    EASE_IN_OUT = "easeInOut"


class CaptionMode(StrEnum):
    """How caption words are drawn, if they are drawn at all."""

    OFF = "off"
    BLOCK = "block"
    KARAOKE = "karaoke"


class TextAlign(StrEnum):
    """Horizontal alignment for caption and text-overlay type."""

    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class TextDecoration(StrEnum):
    """The decorations a supported renderer can draw on text."""

    NONE = "none"
    UNDERLINE = "underline"
    STRIKETHROUGH = "strikethrough"


class FontFamily(StrEnum):
    """The fonts Clipah ships to both the browser preview and the render worker."""

    INTER = "Inter"
    MONTSERRAT = "Montserrat"
    POPPINS = "Poppins"
    ROBOTO = "Roboto"
    OPEN_SANS = "Open Sans"
    BEBAS_NEUE = "Bebas Neue"
    ANTON = "Anton"
    NUNITO = "Nunito"


class Placement(StrEnum):
    """Where an overlay sits inside the canvas."""

    COVER = "cover"
    PICTURE_IN_PICTURE = "pictureInPicture"
    LOWER_THIRD = "lowerThird"
    TOP = "top"
    CENTER = "center"


class OriginType(StrEnum):
    """Where the media in one item or overlay came from."""

    SOURCE = "source"
    BROLL_SUGGESTION = "brollSuggestion"
    USER_ASSET = "userAsset"
    GENERATED = "generated"


class CompositionModel(BaseModel):
    """The shared strictness every part of a composition inherits."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        str_strip_whitespace=False,
    )


class Canvas(CompositionModel):
    """The output frame every item is composited into."""

    width: int
    height: int
    background: Color

    @model_validator(mode="after")
    def _reject_unsupported_canvas(self) -> Self:
        """Refuse a frame size that maps to no export preset."""
        if (self.width, self.height) not in SUPPORTED_CANVAS_SIZES:
            raise ValueError("canvas must be one of the supported export presets")
        return self


class SourceRange(CompositionModel):
    """The span of the source asset this clip was cut from."""

    in_ms: Milliseconds
    out_ms: Milliseconds

    @model_validator(mode="after")
    def _reject_reversed_range(self) -> Self:
        """Refuse a range that ends before it starts."""
        if self.out_ms <= self.in_ms:
            raise ValueError("sourceRange must end after it starts")
        return self


class Transform(CompositionModel):
    """The position, scale, and rotation of one visual element."""

    x: Finite
    y: Finite
    scale: Annotated[Finite, Field(gt=0)]
    rotation: Finite


class Crop(CompositionModel):
    """A normalized crop rectangle inside the source frame."""

    x: Annotated[Finite, Field(ge=0, le=1)]
    y: Annotated[Finite, Field(ge=0, le=1)]
    width: Annotated[Finite, Field(gt=0, le=1)]
    height: Annotated[Finite, Field(gt=0, le=1)]

    @model_validator(mode="after")
    def _reject_crop_outside_the_frame(self) -> Self:
        """Refuse a rectangle that leaves the source frame."""
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("crop must stay inside the source frame")
        return self


class TextStyle(CompositionModel):
    """Every type attribute Section 9 requires the editor to expose."""

    font_family: FontFamily
    font_size: Annotated[int, Field(ge=12, le=200)]
    color: Color
    align: TextAlign
    weight: int
    italic: bool
    decoration: TextDecoration
    letter_spacing: Annotated[Finite, Field(ge=-10, le=40)]
    line_height: Annotated[Finite, Field(ge=0.5, le=3)]
    background_enabled: bool
    background_color: Color

    @model_validator(mode="after")
    def _reject_unsupported_weight(self) -> Self:
        """Refuse a weight no shipped font face can render."""
        if self.weight not in SUPPORTED_FONT_WEIGHTS:
            raise ValueError("weight must be a supported font weight")
        return self


class CaptionStyle(TextStyle):
    """Caption type, plus the colour karaoke highlighting paints the active word."""

    highlight_color: Color


class Origin(CompositionModel):
    """Where one element's media came from, and the records that attribute it."""

    type: OriginType
    suggestion_id: UUID | None
    provenance_id: UUID | None

    @model_validator(mode="after")
    def _require_honest_attribution(self) -> Self:
        """Refuse a suggestion reference that contradicts the declared origin."""
        if self.type is OriginType.BROLL_SUGGESTION and self.suggestion_id is None:
            raise ValueError("a brollSuggestion origin must name its suggestion")
        if self.type is not OriginType.BROLL_SUGGESTION and self.suggestion_id is not None:
            raise ValueError("only a brollSuggestion origin may name a suggestion")
        return self


class Keyframe(CompositionModel):
    """One animated value at one instant, relative to the element that carries it."""

    at_ms: Milliseconds
    easing: Easing
    transform: Transform | None
    opacity: Annotated[Finite, Field(ge=0, le=1)] | None
    style: TextStyle | None

    @model_validator(mode="after")
    def _require_an_animated_property(self) -> Self:
        """Refuse a keyframe that instructs the renderer to change nothing."""
        if self.transform is None and self.opacity is None and self.style is None:
            raise ValueError("a keyframe must animate at least one property")
        return self


class TrackItem(CompositionModel):
    """One slice of a source asset placed on a timeline lane."""

    id: ElementId
    source_asset_id: UUID
    timeline_start_ms: Milliseconds
    source_in_ms: Milliseconds
    source_out_ms: Milliseconds
    transform: Transform
    crop: Crop | None
    opacity: Annotated[Finite, Field(ge=0, le=1)]
    blend_mode: BlendMode
    motion: MotionPreset
    origin: Origin
    keyframes: tuple[Keyframe, ...]

    @model_validator(mode="after")
    def _reject_impossible_media_bounds(self) -> Self:
        """Refuse reversed media bounds and keyframes the item never reaches."""
        if self.source_out_ms <= self.source_in_ms:
            raise ValueError("an item must end after it starts in its source")
        _reject_unordered_keyframes(self.keyframes, self.duration_ms)
        return self

    @property
    def duration_ms(self) -> int:
        """Report how long this item occupies the timeline."""
        return self.source_out_ms - self.source_in_ms

    @property
    def timeline_end_ms(self) -> int:
        """Report where this item stops playing on the timeline."""
        return self.timeline_start_ms + self.duration_ms


class Track(CompositionModel):
    """One lane of the timeline, playing one item at a time."""

    id: ElementId
    type: TrackType
    items: tuple[TrackItem, ...]

    @model_validator(mode="after")
    def _reject_overlapping_items(self) -> Self:
        """Refuse two items that would claim the same instant on one lane."""
        ordered = sorted(self.items, key=lambda item: item.timeline_start_ms)
        for earlier, later in pairwise(ordered):
            if later.timeline_start_ms < earlier.timeline_end_ms:
                raise ValueError("items on one track may not overlap")
        return self


class CaptionWord(CompositionModel):
    """One transcript word as it is drawn, timed against the composition."""

    id: ElementId
    start_ms: Milliseconds
    end_ms: Milliseconds
    text: Annotated[str, Field(min_length=1, max_length=200)]
    speaker: Annotated[str, Field(max_length=64)] | None

    @model_validator(mode="after")
    def _reject_reversed_word(self) -> Self:
        """Refuse a word that ends before it starts."""
        if self.end_ms <= self.start_ms:
            raise ValueError("a caption word must end after it starts")
        return self


class Captions(CompositionModel):
    """The caption layer, its words, and the type they are drawn in."""

    mode: CaptionMode
    words: tuple[CaptionWord, ...]
    style: CaptionStyle

    @model_validator(mode="after")
    def _reject_overlapping_words(self) -> Self:
        """Refuse words that overlap, because karaoke would have two active words."""
        ordered = sorted(self.words, key=lambda word: word.start_ms)
        for earlier, later in pairwise(ordered):
            if later.start_ms < earlier.end_ms:
                raise ValueError("caption words may not overlap")
        return self


class BaseOverlay(CompositionModel):
    """What every overlay carries, whatever it draws."""

    id: ElementId
    timeline_start_ms: Milliseconds
    timeline_end_ms: Milliseconds
    placement: Placement
    opacity: Annotated[Finite, Field(ge=0, le=1)]
    keyframes: tuple[Keyframe, ...]

    @model_validator(mode="after")
    def _reject_impossible_timeline_bounds(self) -> Self:
        """Refuse an overlay with no duration and keyframes it never reaches."""
        if self.timeline_end_ms <= self.timeline_start_ms:
            raise ValueError("an overlay must end after it starts")
        _reject_unordered_keyframes(self.keyframes, self.duration_ms)
        return self

    @property
    def duration_ms(self) -> int:
        """Report how long this overlay is on screen."""
        return self.timeline_end_ms - self.timeline_start_ms


class VideoOverlay(BaseOverlay):
    """A B-roll or supporting video placed over the main timeline."""

    type: Literal["video"]
    asset_id: UUID
    source_in_ms: Milliseconds
    source_out_ms: Milliseconds
    blend_mode: BlendMode
    motion: MotionPreset
    preserve_dialogue_audio: bool
    origin: Origin

    @model_validator(mode="after")
    def _reject_reversed_media_bounds(self) -> Self:
        """Refuse reversed media bounds inside the overlay's own asset."""
        if self.source_out_ms <= self.source_in_ms:
            raise ValueError("an overlay must end after it starts in its source")
        return self


class ImageOverlay(BaseOverlay):
    """A still image placed over the main timeline."""

    type: Literal["image"]
    asset_id: UUID
    blend_mode: BlendMode
    motion: MotionPreset
    origin: Origin


class TextOverlay(BaseOverlay):
    """Type the member wrote, drawn rather than loaded."""

    type: Literal["text"]
    text: Annotated[str, Field(min_length=1, max_length=2_000)]
    style: TextStyle
    motion: MotionPreset


class CitationOverlay(BaseOverlay):
    """An on-screen source note bound to the evidence record it cites."""

    type: Literal["citation"]
    claim_evidence_id: UUID
    text: Annotated[str, Field(min_length=1, max_length=2_000)]
    style: TextStyle


Overlay = Annotated[
    VideoOverlay | ImageOverlay | TextOverlay | CitationOverlay,
    Field(discriminator="type"),
]


class AudioMix(CompositionModel):
    """The two gains a version 1 composition can set."""

    gain_db: Annotated[Finite, Field(ge=-60, le=12)]
    music_gain_db: Annotated[Finite, Field(ge=-60, le=12)]


class Bookmark(CompositionModel):
    """A member's marker on the timeline."""

    id: ElementId
    timeline_ms: Milliseconds
    label: Annotated[str, Field(min_length=1, max_length=200)]


class TemplateReference(CompositionModel):
    """The exact template version this composition was built against."""

    id: UUID
    version: Annotated[int, Field(gt=0)]


class BrandKitReference(CompositionModel):
    """The exact Brand Kit version this composition was built against."""

    id: UUID
    version: Annotated[int, Field(gt=0)]
    logo_asset_id: UUID | None


class CompositionV1(CompositionModel):
    """One immutable editing decision set, valid on its own terms."""

    # The published schema carries the version as a constant, so a browser generating
    # types from it cannot express a document this validator would refuse.
    schema_version: Annotated[int, Field(strict=True, json_schema_extra={"const": SCHEMA_VERSION})]
    source_asset_id: UUID
    duration_ms: Annotated[int, Field(gt=0, le=MAX_COMPOSITION_DURATION_MS)]
    canvas: Canvas
    source_range: SourceRange
    template: TemplateReference | None
    brand_kit: BrandKitReference | None
    tracks: tuple[Track, ...]
    captions: Captions
    overlays: tuple[Overlay, ...]
    audio: AudioMix
    bookmarks: tuple[Bookmark, ...]

    @model_validator(mode="after")
    def _reject_an_inconsistent_document(self) -> Self:
        """Refuse a composition whose parts contradict each other or its duration."""
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("schemaVersion must be 1")
        self._reject_repeated_identifiers()
        self._reject_content_outside_the_duration()
        return self

    def _reject_repeated_identifiers(self) -> None:
        """Refuse a repeated identifier, which makes an editor operation ambiguous."""
        identifiers = [
            *(track.id for track in self.tracks),
            *(item.id for track in self.tracks for item in track.items),
            *(overlay.id for overlay in self.overlays),
            *(word.id for word in self.captions.words),
            *(bookmark.id for bookmark in self.bookmarks),
        ]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("every identifier in one composition must be unique")

    def _reject_content_outside_the_duration(self) -> None:
        """Refuse anything timed past the clip, which no preview could ever show."""
        for track in self.tracks:
            for item in track.items:
                if item.timeline_end_ms > self.duration_ms:
                    raise ValueError("an item may not run past the composition duration")
        for overlay in self.overlays:
            if overlay.timeline_end_ms > self.duration_ms:
                raise ValueError("an overlay may not run past the composition duration")
        for word in self.captions.words:
            if word.end_ms > self.duration_ms:
                raise ValueError("a caption word may not run past the composition duration")
        for bookmark in self.bookmarks:
            if bookmark.timeline_ms > self.duration_ms:
                raise ValueError("a bookmark may not sit past the composition duration")


def _reject_unordered_keyframes(keyframes: Sequence[Keyframe], duration_ms: int) -> None:
    """Refuse keyframes out of order, at one instant twice, or beyond their element."""
    for earlier, later in pairwise(keyframes):
        if later.at_ms <= earlier.at_ms:
            raise ValueError("keyframes must be ordered by strictly increasing time")
    for keyframe in keyframes:
        if keyframe.at_ms > duration_ms:
            raise ValueError("a keyframe may not sit past the element it animates")


def parse_composition(document: Mapping[str, Any]) -> CompositionV1:
    """Validate one untrusted document as a version 1 composition."""
    if not isinstance(document, Mapping):
        raise CompositionValidationError("a composition must be a JSON object")
    try:
        return CompositionV1.model_validate(dict(document))
    except ValidationError as error:
        raise CompositionValidationError(_first_reason(error)) from error


def canonical_json(composition: CompositionV1) -> bytes:
    """Serialize one composition to the exact bytes its hash is taken over."""
    return json.dumps(
        composition.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def composition_hash(composition: CompositionV1) -> bytes:
    """Hash one composition so identical editing decisions share one identity."""
    return hashlib.sha256(canonical_json(composition)).digest()


def collect_asset_ids(composition: CompositionV1) -> frozenset[UUID]:
    """Report every asset this composition depends on, for the caller to authorize."""
    assets = {composition.source_asset_id}
    assets.update(item.source_asset_id for track in composition.tracks for item in track.items)
    assets.update(
        overlay.asset_id
        for overlay in composition.overlays
        if isinstance(overlay, VideoOverlay | ImageOverlay)
    )
    if composition.brand_kit is not None and composition.brand_kit.logo_asset_id is not None:
        assets.add(composition.brand_kit.logo_asset_id)
    return frozenset(assets)


def _first_reason(error: ValidationError) -> str:
    """Name the field at fault without leaking the submitted values back to a client."""
    first = error.errors()[0]
    location = ".".join(str(part) for part in first["loc"]) or "composition"
    return f"{location}: {first['msg']}"
