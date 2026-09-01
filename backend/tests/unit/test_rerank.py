"""Contracts for globally ranking surviving candidates into a stable order."""

from __future__ import annotations

import pytest

from clipah.highlights.models import ClipCandidateDraft, ClipCategory, ScoreBreakdown
from clipah.highlights.rerank import (
    DEFAULT_RANKING_POLICY,
    RankingPolicy,
    exposed,
    local_score,
    rank_candidates,
)


def _breakdown(**overrides: float) -> ScoreBreakdown:
    """Build one neutral breakdown so a test can move a single dimension."""
    values = {
        "hook": 0.5,
        "payoff": 0.5,
        "narrative_completeness": 0.5,
        "context_safety": 0.5,
        "platform_fit": 0.5,
        "transcript_confidence": 0.5,
        "visual_opportunity": 0.5,
    }
    values.update(overrides)
    return ScoreBreakdown(**values)


def _draft(
    *,
    hook: str,
    start_ms: int = 0,
    breakdown: ScoreBreakdown | None = None,
    warnings: tuple[str, ...] = (),
) -> ClipCandidateDraft:
    """Build one deduplicated candidate ready for global ranking."""
    return ClipCandidateDraft(
        hook=hook,
        payoff="payoff",
        reason="reason",
        category=ClipCategory.INSIGHT,
        tags=(),
        start_word_id=f"w{start_ms // 500 + 1:06d}",
        end_word_id=f"w{start_ms // 500 + 60:06d}",
        start_ms=start_ms,
        end_ms=start_ms + 30_000,
        duration_ms=30_000,
        transcript_excerpt=f"excerpt for {hook}",
        context_dependencies=(),
        context_warnings=warnings,
        visual_opportunities=(),
        score_breakdown=breakdown or _breakdown(),
    )


@pytest.mark.unit
def test_scores_a_perfect_and_an_empty_breakdown_at_the_interval_edges() -> None:
    """A local score outside 0-1 would break the stored candidate score constraint."""
    assert local_score(_breakdown(**dict.fromkeys(ScoreBreakdown.model_fields, 1.0))) == 1.0
    assert local_score(_breakdown(**dict.fromkeys(ScoreBreakdown.model_fields, 0.0))) == 0.0


@pytest.mark.unit
def test_weighs_narrative_completeness_and_context_safety_above_visual_opportunity() -> None:
    """A complete, context-safe moment must outrank a merely photogenic one."""
    complete = local_score(_breakdown(narrative_completeness=1.0))
    safe = local_score(_breakdown(context_safety=1.0))
    visual = local_score(_breakdown(visual_opportunity=1.0))

    assert complete > visual
    assert safe > visual


@pytest.mark.unit
def test_ranks_by_descending_score_when_no_provider_order_is_available() -> None:
    """Without a quality model the local weights must still produce a usable order."""
    weak = _draft(hook="weak", start_ms=0, breakdown=_breakdown(hook=0.1, payoff=0.1))
    strong = _draft(hook="strong", start_ms=60_000, breakdown=_breakdown(hook=1.0, payoff=1.0))

    ranked = rank_candidates([weak, strong])

    assert [item.draft.hook for item in ranked] == ["strong", "weak"]
    assert [item.rank for item in ranked] == [1, 2]
    assert ranked[0].score > ranked[1].score


@pytest.mark.unit
def test_breaks_a_score_tie_by_the_earlier_start() -> None:
    """Equal scores must resolve identically on every run of the same analysis."""
    later = _draft(hook="later", start_ms=60_000)
    earlier = _draft(hook="earlier", start_ms=0)

    ranked = rank_candidates([later, earlier])

    assert [item.draft.hook for item in ranked] == ["earlier", "later"]


@pytest.mark.unit
def test_applies_a_valid_provider_order() -> None:
    """The quality model decides the final order when it returns a complete permutation."""
    first = _draft(hook="first", start_ms=0, breakdown=_breakdown(hook=1.0))
    second = _draft(hook="second", start_ms=60_000)
    third = _draft(hook="third", start_ms=120_000)

    ranked = rank_candidates([first, second, third], order=[2, 0, 1])

    assert [item.draft.hook for item in ranked] == ["third", "first", "second"]
    assert [item.rank for item in ranked] == [1, 2, 3]


@pytest.mark.unit
@pytest.mark.parametrize("order", [[0, 1], [0, 1, 1], [0, 1, 3], [0, 1, -1]])
def test_ignores_an_incomplete_or_invalid_provider_order(order: list[int]) -> None:
    """A malformed ranking must degrade to local scores, never drop or repeat a candidate."""
    first = _draft(hook="first", start_ms=0, breakdown=_breakdown(hook=1.0))
    second = _draft(hook="second", start_ms=60_000)
    third = _draft(hook="third", start_ms=120_000)

    ranked = rank_candidates([first, second, third], order=order)

    assert [item.draft.hook for item in ranked] == ["first", "second", "third"]


@pytest.mark.unit
def test_scores_decrease_monotonically_with_rank_under_a_provider_order() -> None:
    """A stored score that contradicts its rank would confuse every later comparison."""
    drafts = [_draft(hook=f"h{index}", start_ms=index * 60_000) for index in range(4)]

    ranked = rank_candidates(drafts, order=[3, 2, 1, 0])

    assert [item.score for item in ranked] == sorted((item.score for item in ranked), reverse=True)


@pytest.mark.unit
def test_keeps_at_most_thirty_candidates() -> None:
    """Storing more than the internal ceiling wastes Workspace storage for no review value."""
    drafts = [_draft(hook=f"h{index}", start_ms=index * 60_000) for index in range(45)]

    ranked = rank_candidates(drafts)

    assert len(ranked) == DEFAULT_RANKING_POLICY.keep == 30
    assert [item.rank for item in ranked] == list(range(1, 31))


@pytest.mark.unit
def test_exposes_the_top_ten_by_default() -> None:
    """Review shows ten; the rest stay available for later reranking."""
    drafts = [_draft(hook=f"h{index}", start_ms=index * 60_000) for index in range(45)]

    ranked = rank_candidates(drafts)

    assert DEFAULT_RANKING_POLICY.expose == 10
    assert [item.rank for item in exposed(ranked)] == list(range(1, 11))


@pytest.mark.unit
def test_honours_an_explicit_ranking_policy() -> None:
    """The kept and exposed counts are configuration, not constants inside ranking code."""
    drafts = [_draft(hook=f"h{index}", start_ms=index * 60_000) for index in range(10)]
    policy = RankingPolicy(keep=4, expose=2)

    ranked = rank_candidates(drafts, policy=policy)

    assert len(ranked) == 4
    assert len(exposed(ranked, policy=policy)) == 2


@pytest.mark.unit
def test_never_discards_a_context_warning() -> None:
    """Reranking may reorder candidates but may not quietly hide an unsafe one."""
    warned = _draft(
        hook="warned",
        start_ms=0,
        breakdown=_breakdown(hook=0.1),
        warnings=("missing attribution",),
    )
    clean = _draft(hook="clean", start_ms=60_000, breakdown=_breakdown(hook=1.0))

    ranked = rank_candidates([warned, clean])

    assert ranked[-1].draft.context_warnings == ("missing attribution",)


@pytest.mark.unit
def test_ranks_no_candidates_without_failing() -> None:
    """An empty survivor list is the analyzer's decision to report, not ranking's."""
    assert rank_candidates([]) == []
