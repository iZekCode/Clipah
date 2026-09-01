"""Removal of candidates that describe one moment twice.

Overlapping windows are expected to propose the same moment more than once. Two candidates
are the same moment when they occupy nearly the same time or repeat nearly the same words;
the weaker one is dropped so review never shows a duplicate.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from math import sqrt

from clipah.highlights.models import ClipCandidateDraft
from clipah.highlights.rerank import local_score

_NON_COMPARABLE = re.compile(r"[^\w\s]", flags=re.UNICODE)


@dataclass(frozen=True, slots=True)
class DeduplicationPolicy:
    """The thresholds at which two candidates count as the same moment."""

    min_temporal_iou: float
    min_excerpt_cosine: float


DEFAULT_DEDUPLICATION_POLICY = DeduplicationPolicy(
    min_temporal_iou=0.65,
    min_excerpt_cosine=0.90,
)


def deduplicate(
    candidates: Sequence[ClipCandidateDraft],
    *,
    policy: DeduplicationPolicy = DEFAULT_DEDUPLICATION_POLICY,
) -> list[ClipCandidateDraft]:
    """Keep the strongest candidate for each moment, in transcript order."""
    survivors: list[ClipCandidateDraft] = []
    for candidate in sorted(candidates, key=_strength):
        if not any(_same_moment(candidate, kept, policy) for kept in survivors):
            survivors.append(candidate)
    return sorted(survivors, key=lambda kept: (kept.start_ms, kept.start_word_id))


def _strength(candidate: ClipCandidateDraft) -> tuple[float, int, str]:
    """Order candidates strongest first, resolving ties deterministically."""
    return (-local_score(candidate.score_breakdown), candidate.start_ms, candidate.start_word_id)


def _same_moment(
    candidate: ClipCandidateDraft, kept: ClipCandidateDraft, policy: DeduplicationPolicy
) -> bool:
    """Decide whether two candidates cover the same moment of the source."""
    return (
        _temporal_iou(candidate, kept) >= policy.min_temporal_iou
        or _excerpt_cosine(candidate.transcript_excerpt, kept.transcript_excerpt)
        >= policy.min_excerpt_cosine
    )


def _temporal_iou(first: ClipCandidateDraft, second: ClipCandidateDraft) -> float:
    """Measure how much of the combined span the two candidates share."""
    intersection = min(first.end_ms, second.end_ms) - max(first.start_ms, second.start_ms)
    if intersection <= 0:
        return 0.0
    union = max(first.end_ms, second.end_ms) - min(first.start_ms, second.start_ms)
    return intersection / union


def _excerpt_cosine(first: str, second: str) -> float:
    """Compare wording alone, ignoring case, spacing, and punctuation."""
    left = Counter(_tokens(first))
    right = Counter(_tokens(second))
    if not left or not right:
        return 0.0
    shared = sum(count * right[token] for token, count in left.items())
    magnitude = sqrt(sum(count * count for count in left.values())) * sqrt(
        sum(count * count for count in right.values())
    )
    return shared / magnitude


def _tokens(text: str) -> list[str]:
    """Reduce an excerpt to the words a similarity comparison may read."""
    return _NON_COMPARABLE.sub(" ", text).casefold().split()
