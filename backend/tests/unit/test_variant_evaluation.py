"""Contracts for measuring context warnings and variant boundaries.

The harness exists to answer one question honestly: how often do the rules catch what a
human labeled, and how often do they cry wolf? A metric that flatters itself is worse than
no metric, so precision is measured alongside recall and the two dimensions nobody can
measure yet are reported as unmeasured rather than assumed perfect.
"""

from __future__ import annotations

import pytest

from clipah.transcripts.models import TranscriptWord
from clipah.variants.evaluation import (
    ContextCase,
    LabeledBoundary,
    VariantGates,
    VariantMetrics,
    aggregate_variant_metrics,
    check_variant_gates,
    evaluate_context_case,
    evaluate_variant_boundaries,
    metrics_document,
)
from clipah.variants.generator import generate_variants
from clipah.variants.models import Platform

WORD_MS = 1_000


def _words(text: str) -> tuple[TranscriptWord, ...]:
    """Build a transcript from plain speech, one second per word."""
    words: list[TranscriptWord] = []
    for index, token in enumerate(text.split(), start=1):
        stripped = token.rstrip(".,!?;:")
        words.append(
            TranscriptWord(
                word_id=f"w{index:06d}",
                text=stripped,
                punctuation=token[len(stripped) :],
                start_ms=(index - 1) * WORD_MS,
                end_ms=index * WORD_MS,
                confidence=0.9,
                speaker="A",
            )
        )
    return tuple(words)


SPEECH = "So here is the thing. What happens when the funding runs out? Nothing good."


def _case(**overrides: object) -> ContextCase:
    """One labeled case whose single boundary severs a question."""
    values: dict[str, object] = {
        "case_id": "en-question",
        "language": "en",
        "words": _words(SPEECH),
        "boundaries": (
            LabeledBoundary(
                label="question severed",
                start_word_id="w000005",
                end_word_id="w000011",
                expected_types=("cut_off_question",),
            ),
        ),
    }
    values.update(overrides)
    return ContextCase(**values)  # type: ignore[arg-type]


@pytest.mark.unit
def test_a_caught_label_counts_toward_recall() -> None:
    """The point of the harness is knowing whether the rules find what humans found."""
    metrics = evaluate_context_case(_case())

    assert metrics.warning_recall == 1.0
    assert metrics.boundary_count == 1


@pytest.mark.unit
def test_a_label_the_rules_miss_lowers_recall_rather_than_being_ignored() -> None:
    """A rule that cannot see a labeled problem must show up as a number, not a silence."""
    metrics = evaluate_context_case(
        _case(
            boundaries=(
                LabeledBoundary(
                    label="unwritten rule",
                    start_word_id="w000005",
                    end_word_id="w000011",
                    expected_types=("cut_off_question", "claim_needs_source"),
                ),
            )
        )
    )

    assert metrics.warning_recall == 0.5


@pytest.mark.unit
def test_warnings_nobody_labeled_lower_precision() -> None:
    """Crying wolf is measured, because a panel nobody trusts is a panel nobody reads."""
    metrics = evaluate_context_case(
        _case(
            boundaries=(
                LabeledBoundary(
                    label="a cut labeled honest",
                    start_word_id="w000006",
                    end_word_id="w000011",
                    expected_types=(),
                ),
            )
        )
    )

    assert metrics.warning_precision < 1.0
    assert metrics.warning_recall == 1.0


@pytest.mark.unit
def test_boundary_validity_accepts_what_the_generator_actually_offers() -> None:
    """Every variant the generator returns must satisfy the harness that scores it."""
    words = _words(" ".join("word " * 9 + f"end{index}." for index in range(24)))
    variants = generate_variants(
        words=words,
        start_word_id=words[0].word_id,
        end_word_id=words[-1].word_id,
        hook="A complete thought",
        platforms=(Platform.TIKTOK,),
        durations_ms=(30_000,),
    )

    assert variants
    assert evaluate_variant_boundaries(words=words, variants=variants) == 1.0


@pytest.mark.unit
def test_a_run_with_no_variants_is_not_treated_as_a_failure() -> None:
    """Refusing every unsafe cut is a correct outcome, not a zero score."""
    assert evaluate_variant_boundaries(words=_words(SPEECH), variants=()) == 1.0


@pytest.mark.unit
def test_aggregate_weights_each_case_by_the_boundaries_it_labeled() -> None:
    """A case with ten labels must not count the same as one with a single label."""
    strong = VariantMetrics(
        boundary_count=9,
        warning_recall=1.0,
        warning_precision=1.0,
        variant_count=0,
        variant_boundary_validity=1.0,
    )
    weak = VariantMetrics(
        boundary_count=1,
        warning_recall=0.0,
        warning_precision=0.0,
        variant_count=0,
        variant_boundary_validity=1.0,
    )

    aggregate = aggregate_variant_metrics((strong, weak))

    assert aggregate.boundary_count == 10
    assert aggregate.warning_recall == pytest.approx(0.9)


@pytest.mark.unit
def test_gates_name_exactly_what_a_run_violated() -> None:
    """A failing run has to say which promise it broke."""
    metrics = VariantMetrics(
        boundary_count=4,
        warning_recall=0.5,
        warning_precision=0.9,
        variant_count=2,
        variant_boundary_validity=0.5,
    )

    violations = check_variant_gates(metrics, VariantGates())

    assert violations == ("warning_recall", "variant_boundary_validity")


@pytest.mark.unit
def test_the_report_says_plainly_what_was_never_measured() -> None:
    """Semantic preservation and acceptance need people and real audio; neither exists here."""
    document = metrics_document(evaluate_context_case(_case()))

    assert document["semantic_preservation"] == "unmeasured"
    assert document["user_acceptance"] == "unmeasured"
