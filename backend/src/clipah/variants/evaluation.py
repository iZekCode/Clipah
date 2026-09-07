"""Measuring context warnings and variant boundaries against labeled cases.

This sits beside `highlights/evaluation.py` and follows the same shape: labeled fixtures
in, one versioned report out, with gates that either hold or name what they violated.

It is a separate module rather than another field on `HighlightMetrics` because the
highlight fixtures carry no per-warning labels. Adding some would either invent labels
nobody wrote or make every checked-in highlight run unreadable, and neither is worth the
convenience of one report shape.

Two of the four dimensions Task 32 names are reported as **unmeasured** rather than
estimated. Semantic preservation and user acceptance need a human reading clips and real
source audio, and this repository has neither; a number invented for them would be worse
than the honest absence of one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from clipah.transcripts.models import TranscriptWord
from clipah.variants.context_safety import assess_context
from clipah.variants.generator import ClipVariantDraft
from clipah.variants.models import SUPPORTED_TARGET_DURATIONS_MS, ContextWarningSeverity

#: How far a variant may sit from its target and still be counted as hitting it.
BOUNDARY_TOLERANCE = 0.25


@dataclass(frozen=True, slots=True)
class LabeledBoundary:
    """One boundary a human labeled, and the warnings they said it deserves."""

    label: str
    start_word_id: str
    end_word_id: str
    expected_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContextCase:
    """One labeled transcript and every boundary somebody judged inside it."""

    case_id: str
    language: str
    words: tuple[TranscriptWord, ...]
    boundaries: tuple[LabeledBoundary, ...]


@dataclass(frozen=True, slots=True)
class VariantMetrics:
    """The measured dimensions of one case, or of a whole labeled set."""

    boundary_count: int
    warning_recall: float
    warning_precision: float
    variant_count: int
    variant_boundary_validity: float
    #: Named, and deliberately not scored. See this module's docstring.
    semantic_preservation: str = "unmeasured"
    user_acceptance: str = "unmeasured"


@dataclass(frozen=True, slots=True)
class VariantGates:
    """The release criteria a context-safety run must clear."""

    min_warning_recall: float = 0.90
    min_warning_precision: float = 0.70
    min_boundary_validity: float = 1.0


def evaluate_context_case(case: ContextCase) -> VariantMetrics:
    """Score one labeled case: what the rules caught, and what they invented."""
    matched = 0
    expected = 0
    produced = 0
    for boundary in case.boundaries:
        found = {
            warning.type.value
            for warning in assess_context(
                words=case.words,
                start_word_id=boundary.start_word_id,
                end_word_id=boundary.end_word_id,
            )
        }
        wanted = set(boundary.expected_types)
        matched += len(found & wanted)
        expected += len(wanted)
        produced += len(found)
    return VariantMetrics(
        boundary_count=len(case.boundaries),
        warning_recall=_rate(matched, expected),
        warning_precision=_rate(matched, produced),
        variant_count=0,
        variant_boundary_validity=1.0,
    )


def evaluate_variant_boundaries(
    *,
    words: Sequence[TranscriptWord],
    variants: Sequence[ClipVariantDraft],
) -> float:
    """Report the share of variants whose boundaries a member could actually trust.

    A variant is valid when its word IDs resolve in order, its duration sits inside the
    tolerance of the target it was offered for, that target is one Clipah supports, and it
    carries nothing blocking.
    """
    if not variants:
        return 1.0
    known = {word.word_id: position for position, word in enumerate(words)}
    valid = sum(1 for variant in variants if _boundary_holds(variant, known))
    return _rate(valid, len(variants))


def aggregate_variant_metrics(metrics: Sequence[VariantMetrics]) -> VariantMetrics:
    """Combine per-case metrics, weighting each case by the boundaries it labeled."""
    boundaries = sum(item.boundary_count for item in metrics)
    variants = sum(item.variant_count for item in metrics)
    return VariantMetrics(
        boundary_count=boundaries,
        warning_recall=_weighted(metrics, "warning_recall", boundaries),
        warning_precision=_weighted(metrics, "warning_precision", boundaries),
        variant_count=variants,
        variant_boundary_validity=_weighted(metrics, "variant_boundary_validity", boundaries),
    )


def check_variant_gates(metrics: VariantMetrics, gates: VariantGates) -> tuple[str, ...]:
    """Name every gate this run violated, or return nothing at all."""
    violations: list[str] = []
    if metrics.warning_recall < gates.min_warning_recall:
        violations.append("warning_recall")
    if metrics.warning_precision < gates.min_warning_precision:
        violations.append("warning_precision")
    if metrics.variant_boundary_validity < gates.min_boundary_validity:
        violations.append("variant_boundary_validity")
    return tuple(violations)


def metrics_document(metrics: VariantMetrics) -> dict[str, Any]:
    """Render one run for a checked-in report, unmeasured dimensions included."""
    return {
        "boundary_count": metrics.boundary_count,
        "warning_recall": round(metrics.warning_recall, 4),
        "warning_precision": round(metrics.warning_precision, 4),
        "variant_count": metrics.variant_count,
        "variant_boundary_validity": round(metrics.variant_boundary_validity, 4),
        "semantic_preservation": metrics.semantic_preservation,
        "user_acceptance": metrics.user_acceptance,
    }


def _boundary_holds(variant: ClipVariantDraft, known: dict[str, int]) -> bool:
    """Report whether one variant's boundary is one a member could act on."""
    start = known.get(variant.start_word_id)
    end = known.get(variant.end_word_id)
    if start is None or end is None or end < start:
        return False
    if variant.target_duration_ms not in SUPPORTED_TARGET_DURATIONS_MS:
        return False
    actual = variant.end_ms - variant.start_ms
    if abs(actual - variant.target_duration_ms) > variant.target_duration_ms * BOUNDARY_TOLERANCE:
        return False
    return not any(
        warning.severity is ContextWarningSeverity.BLOCKING for warning in variant.warnings
    )


def _rate(part: int, whole: int) -> float:
    """Report a rate, treating "nothing to measure" as a clean result."""
    return 1.0 if whole == 0 else part / whole


def _weighted(metrics: Sequence[VariantMetrics], field: str, boundaries: int) -> float:
    """Average one dimension across cases, weighted by labeled boundaries."""
    if boundaries == 0:
        return 1.0
    total = sum(float(getattr(item, field)) * item.boundary_count for item in metrics)
    return total / boundaries
