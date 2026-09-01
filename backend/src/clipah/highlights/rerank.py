"""Global ranking of surviving candidates into one stable, explainable order.

Ranking is pure. A quality model may propose an order, but this module decides whether the
proposal is usable, falls back to explainable local weights when it is not, and never drops
a candidate or its context warnings while doing so.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from clipah.highlights.models import ClipCandidateDraft, ScoreBreakdown

RERANK_WEIGHTS: dict[str, float] = {
    "narrative_completeness": 0.22,
    "context_safety": 0.20,
    "hook": 0.18,
    "payoff": 0.16,
    "platform_fit": 0.10,
    "transcript_confidence": 0.09,
    "visual_opportunity": 0.05,
}

_SCORE_DIGITS = 5


@dataclass(frozen=True, slots=True)
class RankingPolicy:
    """How many candidates an analysis stores and how many review shows by default."""

    keep: int
    expose: int


DEFAULT_RANKING_POLICY = RankingPolicy(keep=30, expose=10)


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    """One candidate placed in the final order with the score that justifies it."""

    rank: int
    score: float
    draft: ClipCandidateDraft


def local_score(breakdown: ScoreBreakdown) -> float:
    """Weigh the explainable dimensions into one score inside the unit interval."""
    total = sum(weight * float(getattr(breakdown, name)) for name, weight in RERANK_WEIGHTS.items())
    return round(total, _SCORE_DIGITS)


def rank_candidates(
    candidates: Sequence[ClipCandidateDraft],
    *,
    order: Sequence[int] | None = None,
    policy: RankingPolicy = DEFAULT_RANKING_POLICY,
) -> list[RankedCandidate]:
    """Return the kept candidates in final order, best first, with monotonic scores."""
    ordered = _ordered(candidates, order)[: policy.keep]
    scores = sorted(
        (local_score(candidate.score_breakdown) for candidate in candidates), reverse=True
    )
    return [
        RankedCandidate(rank=position + 1, score=scores[position], draft=candidate)
        for position, candidate in enumerate(ordered)
    ]


def exposed(
    ranked: Sequence[RankedCandidate], *, policy: RankingPolicy = DEFAULT_RANKING_POLICY
) -> list[RankedCandidate]:
    """Return only the candidates review shows before anyone asks for more."""
    return list(ranked[: policy.expose])


def _ordered(
    candidates: Sequence[ClipCandidateDraft], order: Sequence[int] | None
) -> list[ClipCandidateDraft]:
    """Follow a complete provider permutation, or fall back to explainable local weights."""
    if order is not None and _is_permutation(order, len(candidates)):
        return [candidates[position] for position in order]
    return sorted(
        candidates,
        key=lambda candidate: (
            -local_score(candidate.score_breakdown),
            candidate.start_ms,
            candidate.start_word_id,
        ),
    )


def _is_permutation(order: Sequence[int], count: int) -> bool:
    """Accept an order only when it places every candidate exactly once."""
    return len(order) == count and sorted(order) == list(range(count))
