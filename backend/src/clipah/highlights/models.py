"""Immutable values and strict schemas shared by highlight windowing and extraction.

Nothing here talks to a provider. A window is a slice of authoritative transcript words,
and a candidate draft is only what a provider is allowed to claim about such a slice; the
timestamps a clip is eventually rendered from are resolved from the transcript, never from
provider text.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True, slots=True)
class WindowingPolicy:
    """The window shape an analysis uses, kept as data so callers configure it."""

    target_min_ms: int
    target_max_ms: int
    overlap_ms: int
    silence_gap_ms: int
    min_words: int


DEFAULT_WINDOWING_POLICY = WindowingPolicy(
    target_min_ms=120_000,
    target_max_ms=180_000,
    overlap_ms=20_000,
    silence_gap_ms=1_200,
    min_words=25,
)


@dataclass(frozen=True, slots=True)
class CandidatePolicy:
    """The duration preset a candidate must satisfy to be reviewable."""

    min_duration_ms: int
    max_duration_ms: int


DEFAULT_CANDIDATE_POLICY = CandidatePolicy(min_duration_ms=20_000, max_duration_ms=90_000)


@dataclass(frozen=True, slots=True)
class TranscriptWindow:
    """One overlapping narrative window offered to an extraction provider."""

    index: int
    start_ms: int
    end_ms: int
    word_ids: tuple[str, ...]
    text: str


class ClipCategory(StrEnum):
    """The review filters version one of the product supports."""

    STORY = "story"
    INSIGHT = "insight"
    HOW_TO = "how_to"
    OPINION = "opinion"
    QUESTION_ANSWER = "question_answer"
    HUMOUR = "humour"
    DATA = "data"
    ANNOUNCEMENT = "announcement"


class ScoreBreakdown(BaseModel):
    """The explainable dimensions every candidate must score, each within 0-1."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hook: float = Field(ge=0.0, le=1.0)
    payoff: float = Field(ge=0.0, le=1.0)
    narrative_completeness: float = Field(ge=0.0, le=1.0)
    context_safety: float = Field(ge=0.0, le=1.0)
    platform_fit: float = Field(ge=0.0, le=1.0)
    transcript_confidence: float = Field(ge=0.0, le=1.0)
    visual_opportunity: float = Field(ge=0.0, le=1.0)


class ClipCandidateProposal(BaseModel):
    """Exactly what an extraction provider may claim, with no room for extra fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hook: str = Field(min_length=1)
    payoff: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    category: ClipCategory
    tags: tuple[str, ...] = ()
    start_word_id: str = Field(min_length=1)
    end_word_id: str = Field(min_length=1)
    transcript_excerpt: str = Field(min_length=1)
    context_dependencies: tuple[str, ...] = ()
    context_warnings: tuple[str, ...] = ()
    visual_opportunities: tuple[str, ...] = ()
    score_breakdown: ScoreBreakdown


class ClipCandidateDraft(BaseModel):
    """One accepted candidate whose bounds and excerpt come from the transcript."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hook: str
    payoff: str
    reason: str
    category: ClipCategory
    tags: tuple[str, ...]
    start_word_id: str
    end_word_id: str
    start_ms: int
    end_ms: int
    duration_ms: int
    transcript_excerpt: str
    context_dependencies: tuple[str, ...]
    context_warnings: tuple[str, ...]
    visual_opportunities: tuple[str, ...]
    score_breakdown: ScoreBreakdown
