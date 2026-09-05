"""Immutable values and strict schemas shared by B-roll planning and placement.

Nothing here talks to a provider or a database. A beat is a span of authoritative
transcript words that would benefit from a picture, and a suggestion is where deterministic
code decided that picture may actually go. The separation is the point: a language model
proposes meaning, and only this product's own arithmetic turns meaning into milliseconds.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field

#: The planning contract stored beside every suggestion, so a replay can be recognized.
PLANNER_VERSION = "broll-plan/1"


class BrollCoverage(StrEnum):
    """How busy a member wants the finished cut to be."""

    MINIMAL = "minimal"
    BALANCED = "balanced"
    DYNAMIC = "dynamic"


class BeatProtection(StrEnum):
    """A reason the viewer must keep watching the speaker rather than a cutaway."""

    FACE_REVEAL = "face_reveal"
    PUNCHLINE = "punchline"
    DEMONSTRATION = "demonstration"
    EMOTIONAL_PAUSE = "emotional_pause"
    CULTURALLY_SENSITIVE = "culturally_sensitive"


class BrollSuggestionStatus(StrEnum):
    """The lifecycle a suggestion moves through once a member starts deciding on it."""

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    PLACED = "placed"
    REPLACED = "replaced"
    REMOVED = "removed"
    REJECTED = "rejected"
    GENERATION_REQUESTED = "generation_requested"
    GENERATING = "generating"
    FAILED = "failed"


class BrollSourceType(StrEnum):
    """Where the media behind an accepted suggestion came from."""

    USER_ASSET = "user_asset"
    STOCK = "stock"
    GENERATED = "generated"


class VisualIntent(BaseModel):
    """Exactly what a planner may claim about the picture one beat wants.

    Every field is something a retriever will search, filter, or refuse on, so a partial
    intent is refused rather than searched as if the missing half did not matter.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str = Field(min_length=1, strict=True)
    action: str = Field(min_length=1, strict=True)
    setting: str = Field(min_length=1, strict=True)
    mood: str = Field(min_length=1, strict=True)
    search_terms_id: tuple[str, ...] = Field(min_length=1)
    search_terms_en: tuple[str, ...] = Field(min_length=1)
    portrait_suitable: bool = Field(strict=True)
    exclusions: tuple[str, ...]
    factual_risk_flags: tuple[str, ...]
    confidence: float = Field(ge=0.0, le=1.0, strict=True)


class VisualBeatProposal(BaseModel):
    """One beat as a provider may state it: word IDs and meaning, never a timestamp."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start_word_id: str = Field(min_length=1, strict=True)
    end_word_id: str = Field(min_length=1, strict=True)
    placement_reason: str = Field(min_length=1, strict=True)
    protection: BeatProtection | None
    intent: VisualIntent


@dataclass(frozen=True, slots=True)
class CandidateSpan:
    """The clip a plan covers, in both the word IDs and the milliseconds it owns."""

    start_word_id: str
    end_word_id: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class VisualBeat:
    """One accepted beat whose bounds were resolved from the authoritative transcript."""

    start_word_id: str
    end_word_id: str
    start_ms: int
    end_ms: int
    intent: VisualIntent
    placement_reason: str
    protection: BeatProtection | None


@dataclass(frozen=True, slots=True)
class DensityPolicy:
    """The spacing band one coverage level promises, in start-to-start milliseconds."""

    min_spacing_ms: int
    max_spacing_ms: int


#: Section 7 of the plan: minimal is one shot per 15s, balanced one per 8-12s, dynamic one
#: per 5-8s. The lower bound of each band is the floor placement actually enforces.
DENSITY_POLICIES: Mapping[BrollCoverage, DensityPolicy] = MappingProxyType(
    {
        BrollCoverage.MINIMAL: DensityPolicy(min_spacing_ms=15_000, max_spacing_ms=15_000),
        BrollCoverage.BALANCED: DensityPolicy(min_spacing_ms=8_000, max_spacing_ms=12_000),
        BrollCoverage.DYNAMIC: DensityPolicy(min_spacing_ms=5_000, max_spacing_ms=8_000),
    }
)


@dataclass(frozen=True, slots=True)
class PlacementPolicy:
    """Every number placement is allowed to use, kept as data so a caller configures it."""

    min_shot_ms: int
    max_shot_ms: int
    hook_guard_ms: int
    min_confidence: float
    silence_gap_ms: int


DEFAULT_PLACEMENT_POLICY = PlacementPolicy(
    min_shot_ms=2_000,
    max_shot_ms=5_000,
    hook_guard_ms=3_000,
    min_confidence=0.5,
    silence_gap_ms=1_200,
)


@dataclass(frozen=True, slots=True)
class PlacedSuggestion:
    """One beat and the milliseconds deterministic code decided it may occupy."""

    beat: VisualBeat
    start_ms: int
    end_ms: int
    placement_reason: str
