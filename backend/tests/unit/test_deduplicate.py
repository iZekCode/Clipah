"""Contracts for removing candidates that describe the same moment twice."""

from __future__ import annotations

import pytest

from clipah.highlights.deduplicate import DEFAULT_DEDUPLICATION_POLICY, deduplicate
from clipah.highlights.models import ClipCandidateDraft, ClipCategory, ScoreBreakdown


def _draft(
    *,
    hook: str,
    start_ms: int,
    end_ms: int,
    excerpt: str,
    score: float = 0.5,
) -> ClipCandidateDraft:
    """Build one accepted candidate, varying only what a duplication rule reads."""
    return ClipCandidateDraft(
        hook=hook,
        payoff="payoff",
        reason="reason",
        category=ClipCategory.INSIGHT,
        tags=(),
        start_word_id=f"w{start_ms // 500 + 1:06d}",
        end_word_id=f"w{end_ms // 500:06d}",
        start_ms=start_ms,
        end_ms=end_ms,
        duration_ms=end_ms - start_ms,
        transcript_excerpt=excerpt,
        context_dependencies=(),
        context_warnings=(),
        visual_opportunities=(),
        score_breakdown=ScoreBreakdown(
            hook=score,
            payoff=score,
            narrative_completeness=score,
            context_safety=score,
            platform_fit=score,
            transcript_confidence=score,
            visual_opportunity=score,
        ),
    )


@pytest.mark.unit
def test_keeps_candidates_that_share_no_time_and_no_wording() -> None:
    """Two genuinely different moments must both survive to the ranking stage."""
    first = _draft(hook="first", start_ms=0, end_ms=30_000, excerpt="growth stalled until we cut")
    second = _draft(
        hook="second", start_ms=100_000, end_ms=130_000, excerpt="pricing changed everything later"
    )

    assert deduplicate([first, second]) == [first, second]


@pytest.mark.unit
def test_removes_the_weaker_candidate_when_temporal_overlap_reaches_the_threshold() -> None:
    """Overlapping windows propose the same moment twice; only the better one is kept."""
    stronger = _draft(
        hook="strong", start_ms=0, end_ms=30_000, excerpt="alpha beta gamma", score=0.9
    )
    weaker = _draft(
        hook="weak", start_ms=2_000, end_ms=32_000, excerpt="delta epsilon zeta", score=0.4
    )

    assert deduplicate([weaker, stronger]) == [stronger]


@pytest.mark.unit
def test_keeps_both_candidates_just_below_the_temporal_threshold() -> None:
    """Neighbouring but distinct moments must not be collapsed into one."""
    first = _draft(hook="first", start_ms=0, end_ms=30_000, excerpt="alpha beta gamma")
    second = _draft(hook="second", start_ms=12_000, end_ms=42_000, excerpt="delta epsilon zeta")

    intersection = 18_000
    union = 42_000
    assert intersection / union < DEFAULT_DEDUPLICATION_POLICY.min_temporal_iou
    assert deduplicate([first, second]) == [first, second]


@pytest.mark.unit
def test_removes_a_candidate_whose_excerpt_repeats_another_at_the_similarity_threshold() -> None:
    """The same sentence recovered at a different offset is still one moment."""
    words = " ".join(f"word{index}" for index in range(20))
    stronger = _draft(hook="strong", start_ms=0, end_ms=30_000, excerpt=words, score=0.8)
    weaker = _draft(hook="weak", start_ms=200_000, end_ms=230_000, excerpt=words, score=0.3)

    assert deduplicate([stronger, weaker]) == [stronger]


@pytest.mark.unit
def test_ignores_case_and_punctuation_when_comparing_excerpts() -> None:
    """Provider formatting differences are not evidence of two separate moments."""
    stronger = _draft(
        hook="strong", start_ms=0, end_ms=30_000, excerpt="Growth stalled, until we cut!", score=0.7
    )
    weaker = _draft(
        hook="weak",
        start_ms=300_000,
        end_ms=330_000,
        excerpt="GROWTH STALLED UNTIL WE CUT",
        score=0.2,
    )

    assert deduplicate([stronger, weaker]) == [stronger]


@pytest.mark.unit
def test_keeps_candidates_whose_wording_only_partly_overlaps() -> None:
    """Half-shared vocabulary is normal in one conversation and must not merge moments."""
    first = _draft(
        hook="first", start_ms=0, end_ms=30_000, excerpt="growth stalled until we cut the form"
    )
    second = _draft(
        hook="second",
        start_ms=300_000,
        end_ms=330_000,
        excerpt="pricing doubled after we raised the plan",
    )

    assert deduplicate([first, second]) == [first, second]


@pytest.mark.unit
def test_breaks_a_score_tie_by_the_earlier_start() -> None:
    """Two equal duplicates must resolve the same way on every run of the same analysis."""
    earlier = _draft(hook="earlier", start_ms=0, end_ms=30_000, excerpt="alpha beta", score=0.5)
    later = _draft(hook="later", start_ms=1_000, end_ms=31_000, excerpt="gamma delta", score=0.5)

    assert deduplicate([later, earlier]) == [earlier]


@pytest.mark.unit
def test_returns_candidates_in_transcript_order() -> None:
    """A stable order keeps the reranking prompt and its evaluation reproducible."""
    first = _draft(hook="first", start_ms=0, end_ms=30_000, excerpt="alpha beta")
    second = _draft(hook="second", start_ms=200_000, end_ms=230_000, excerpt="gamma delta")
    third = _draft(hook="third", start_ms=400_000, end_ms=430_000, excerpt="epsilon zeta")

    assert deduplicate([third, first, second]) == [first, second, third]


@pytest.mark.unit
def test_returns_nothing_for_no_candidates() -> None:
    """An analysis that extracted nothing must not fail inside deduplication."""
    assert deduplicate([]) == []


@pytest.mark.unit
def test_keeps_two_wordless_excerpts_that_do_not_overlap_in_time() -> None:
    """Excerpts without comparable words carry no similarity evidence at all."""
    first = _draft(hook="first", start_ms=0, end_ms=30_000, excerpt="...")
    second = _draft(hook="second", start_ms=200_000, end_ms=230_000, excerpt="!!!")

    assert deduplicate([first, second]) == [first, second]
