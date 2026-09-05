"""Offline measurement of B-roll retrieval and reranking against labeled intents.

The harness never asks a provider anything. A runner produces a ranked candidate list for
one labeled intent, and this module decides whether the pictures it chose were traceable,
safe, and actually the ones a reviewer marked as relevant. The three gates below are the
release criteria the plan names before B-roll may reach the editor: a ranking change that
violates one is a regression, whatever its prose looks like.

Two of the gates are absolute on purpose. Provenance completeness and unsafe selections are
not quality dimensions to be traded against relevance — an untraceable or unsafe picture in
a published clip is a different kind of failure from an unhelpful one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from clipah.broll.models import VisualIntent
from clipah.broll.retriever import (
    ExternalAssetCandidate,
    LicenseTerms,
    MediaKind,
    ProvenanceError,
    provenance_of,
)
from clipah.transcripts.evaluation import (
    EvaluationFixtureError,
    load_manifest,
    read_case_document,
)

BROLL_REPORT_VERSION = "broll-eval/1"
BROLL_MANIFEST_VERSION = "broll-eval-cases/1"

GATE_PROVENANCE_COMPLETENESS = "BROLL_GATE_PROVENANCE_COMPLETENESS"
GATE_UNSAFE_SELECTIONS = "BROLL_GATE_UNSAFE_SELECTIONS"
GATE_TOP_THREE_RELEVANCE = "BROLL_GATE_TOP_THREE_RELEVANCE"

MIN_PROVENANCE_COMPLETENESS = 1.0
MAX_UNSAFE_SELECTIONS = 0
MIN_TOP_THREE_RELEVANCE = 0.8

_SELECTION_DEPTH = 3
_RATE_DIGITS = 4
_RETRIEVED_AT = "2026-09-05T00:00:00+00:00"


class CandidateLabel(StrEnum):
    """What a reviewer decided about one candidate offered for one intent."""

    RELEVANT = "relevant"
    IRRELEVANT = "irrelevant"
    CULTURALLY_MISMATCHED = "culturally_mismatched"
    UNSAFE = "unsafe"
    REPETITIVE = "repetitive"
    PORTRAIT_INCOMPATIBLE = "portrait_incompatible"


@dataclass(frozen=True, slots=True)
class LabeledCandidate:
    """One candidate offered for one intent, and the judgement a reviewer made of it."""

    candidate: ExternalAssetCandidate
    label: CandidateLabel


@dataclass(frozen=True, slots=True)
class BrollCase:
    """One labeled visual intent and the pool a retriever is expected to choose from."""

    case_id: str
    language: str
    intent: VisualIntent
    candidates: tuple[LabeledCandidate, ...]

    def label_of(self, candidate: ExternalAssetCandidate) -> CandidateLabel | None:
        """Name the judgement a reviewer made about one candidate of this case."""
        for labeled in self.candidates:
            if labeled.candidate.identity == candidate.identity:
                return labeled.label
        return None


@dataclass(frozen=True, slots=True)
class BrollObservation:
    """What one adapter actually chose for one case, best first."""

    case_id: str
    selected: tuple[ExternalAssetCandidate, ...]


@dataclass(frozen=True, slots=True)
class BrollMetrics:
    """The gated dimensions of one case, or of a whole checked-in case set."""

    case_count: int
    selection_count: int
    provenance_completeness: float
    unsafe_selections: int
    top_three_relevance: float

    @property
    def violations(self) -> tuple[str, ...]:
        """Name every release gate these numbers fail."""
        failures: list[str] = []
        if self.provenance_completeness < MIN_PROVENANCE_COMPLETENESS:
            failures.append(GATE_PROVENANCE_COMPLETENESS)
        if self.unsafe_selections > MAX_UNSAFE_SELECTIONS:
            failures.append(GATE_UNSAFE_SELECTIONS)
        if self.top_three_relevance < MIN_TOP_THREE_RELEVANCE:
            failures.append(GATE_TOP_THREE_RELEVANCE)
        return tuple(failures)


@dataclass(frozen=True, slots=True)
class BrollReport:
    """One scored run, attributable to whatever produced it."""

    manifest_version: str
    adapter: str
    reranker_model: str
    reranker_model_version: str
    metrics: BrollMetrics
    per_case: tuple[tuple[str, BrollMetrics], ...]

    @property
    def passed(self) -> bool:
        """Decide the run's outcome here rather than leaving it to a reader."""
        return not self.metrics.violations

    def as_document(self) -> dict[str, Any]:
        """Render the report as the JSON artifact a run is expected to keep."""
        return {
            "report_version": BROLL_REPORT_VERSION,
            "manifest_version": self.manifest_version,
            "adapter": self.adapter,
            "reranker_model": self.reranker_model,
            "reranker_model_version": self.reranker_model_version,
            "passed": self.passed,
            "violations": list(self.metrics.violations),
            "metrics": _metrics_document(self.metrics),
            "cases": [
                {"case_id": case_id, **_metrics_document(metrics)}
                for case_id, metrics in self.per_case
            ],
        }


def score_case(case: BrollCase, observation: BrollObservation) -> BrollMetrics:
    """Score one case's selections against the labels a reviewer gave them.

    Only the candidates actually offered to a member are scored. A pool entry nobody
    selected is neither a success nor a failure: it is the harness's job to notice what
    was chosen, not to grade what was ignored.
    """
    selected = observation.selected[:_SELECTION_DEPTH]
    complete = sum(1 for candidate in selected if _has_complete_provenance(candidate))
    unsafe = sum(1 for candidate in selected if case.label_of(candidate) is CandidateLabel.UNSAFE)
    relevant = sum(
        1 for candidate in selected if case.label_of(candidate) is CandidateLabel.RELEVANT
    )
    return BrollMetrics(
        case_count=1,
        selection_count=len(selected),
        provenance_completeness=_rate(complete, len(selected)),
        unsafe_selections=unsafe,
        top_three_relevance=_rate(relevant, len(selected)),
    )


def aggregate(metrics: Sequence[BrollMetrics]) -> BrollMetrics:
    """Combine per-case metrics, weighting rates by the selections they were measured over.

    Weighting by selection count keeps a case that offered one picture from outvoting one
    that offered three, which is the difference between measuring the ranking and
    measuring the shape of the case set.
    """
    if not metrics:
        return BrollMetrics(
            case_count=0,
            selection_count=0,
            provenance_completeness=1.0,
            unsafe_selections=0,
            top_three_relevance=1.0,
        )
    selections = sum(item.selection_count for item in metrics)
    return BrollMetrics(
        case_count=sum(item.case_count for item in metrics),
        selection_count=selections,
        provenance_completeness=_weighted(
            metrics, selections, lambda item: item.provenance_completeness
        ),
        unsafe_selections=sum(item.unsafe_selections for item in metrics),
        top_three_relevance=_weighted(metrics, selections, lambda item: item.top_three_relevance),
    )


def build_report(
    *,
    cases: Sequence[BrollCase],
    observations: Sequence[BrollObservation],
    manifest_version: str,
    adapter: str,
    reranker_model: str = "",
    reranker_model_version: str = "",
) -> BrollReport:
    """Score every observed case and decide whether the run met its release gates."""
    by_case = {observation.case_id: observation for observation in observations}
    per_case: list[tuple[str, BrollMetrics]] = []
    for case in cases:
        observation = by_case.get(case.case_id)
        if observation is None:
            raise EvaluationFixtureError(f"no observation for evaluation case {case.case_id}")
        per_case.append((case.case_id, score_case(case, observation)))
    return BrollReport(
        manifest_version=manifest_version,
        adapter=adapter,
        reranker_model=reranker_model,
        reranker_model_version=reranker_model_version,
        metrics=aggregate([metrics for _, metrics in per_case]),
        per_case=tuple(per_case),
    )


def load_cases(directory: Path) -> tuple[str, tuple[BrollCase, ...]]:
    """Read the checked-in case set, refusing any case whose bytes have been altered."""
    manifest = load_manifest(directory)
    version = manifest.get("version")
    if not isinstance(version, str) or not version:
        raise EvaluationFixtureError("B-roll evaluation manifest declares no version")
    entries = manifest.get("cases")
    if not isinstance(entries, list) or not entries:
        raise EvaluationFixtureError("B-roll evaluation manifest declares no cases")
    cases = tuple(
        _case(read_case_document(directory, entry)) for entry in entries if isinstance(entry, dict)
    )
    if len(cases) != len(entries):
        raise EvaluationFixtureError("B-roll evaluation manifest lists a malformed case entry")
    return version, cases


def _case(document: Any) -> BrollCase:
    """Build one labeled case from its checked-in document."""
    try:
        return BrollCase(
            case_id=str(document["case_id"]),
            language=str(document["language"]),
            intent=VisualIntent(**document["intent"]),
            candidates=tuple(
                LabeledCandidate(
                    candidate=_candidate(entry),
                    label=CandidateLabel(entry["label"]),
                )
                for entry in document["candidates"]
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise EvaluationFixtureError("B-roll evaluation case is malformed") from error


def _candidate(entry: Any) -> ExternalAssetCandidate:
    """Build one labeled candidate exactly as a normalized provider result would look."""
    license_entry = entry["license"]
    return ExternalAssetCandidate(
        provider=entry["provider"],
        provider_asset_id=entry["provider_asset_id"],
        media_kind=MediaKind(entry["media_kind"]),
        source_url=entry["source_url"],
        download_url=entry["download_url"],
        author=entry["author"],
        author_url=entry["author_url"],
        license=LicenseTerms(
            name=license_entry["name"],
            url=license_entry["url"],
            attribution_required=bool(license_entry["attribution_required"]),
            snapshot=license_entry["snapshot"],
        ),
        width=int(entry["width"]),
        height=int(entry["height"]),
        duration_ms=None if entry["duration_ms"] is None else int(entry["duration_ms"]),
        attribution_text=entry["attribution_text"],
        query=entry["query"],
        safe=bool(entry["safe"]),
        description=entry.get("description", ""),
        tags=tuple(entry.get("tags", ())),
    )


def _has_complete_provenance(candidate: ExternalAssetCandidate) -> bool:
    """Report whether this candidate could lawfully have been stored at all."""
    try:
        provenance_of(candidate, retrieved_at_iso=_RETRIEVED_AT)
    except ProvenanceError:
        return False
    return True


def _weighted(
    metrics: Sequence[BrollMetrics],
    selections: int,
    read: Callable[[BrollMetrics], float],
) -> float:
    """Average one rate across cases, weighted by the selections it was measured over."""
    if not selections:
        return 1.0
    total = sum(read(item) * item.selection_count for item in metrics)
    return round(total / selections, _RATE_DIGITS)


def _rate(numerator: int, denominator: int) -> float:
    """Report a rate, treating "nothing was selected" as a clean rather than a failed case."""
    if denominator <= 0:
        return 1.0
    return round(numerator / denominator, _RATE_DIGITS)


def _metrics_document(metrics: BrollMetrics) -> dict[str, Any]:
    """Render one set of metrics as the JSON a report keeps."""
    return {
        "case_count": metrics.case_count,
        "selection_count": metrics.selection_count,
        "provenance_completeness": metrics.provenance_completeness,
        "unsafe_selections": metrics.unsafe_selections,
        "top_three_relevance": metrics.top_three_relevance,
        "violations": list(metrics.violations),
    }


def observations_from(cases: Iterable[BrollCase], *, select: Any) -> tuple[BrollObservation, ...]:
    """Run one selection strategy over every case, which is all a runner has to do."""
    return tuple(
        BrollObservation(case_id=case.case_id, selected=tuple(select(case))) for case in cases
    )


def select_with_reranker(
    case: BrollCase,
    *,
    reranker: Any,
    policy: Any = None,
) -> tuple[ExternalAssetCandidate, ...]:
    """Rank one case's whole pool with the shipped reranking rules, best first.

    The selection strategy lives here rather than in the runner because it is what is
    being measured. A runner that decided how to choose would be grading itself.
    """
    from clipah.broll.reranker import DEFAULT_RANKING_POLICY, rerank_candidates

    ranked = rerank_candidates(
        candidates=[labeled.candidate for labeled in case.candidates],
        intent=case.intent,
        reranker=reranker,
        policy=policy or DEFAULT_RANKING_POLICY,
    )
    return tuple(item.candidate for item in ranked)


def fake_vision_for(case: BrollCase) -> Any:
    """Build a deterministic stand-in for a vision model over one case's own pool."""
    from clipah.broll.reranker import FakeFrameRelevanceProvider

    return FakeFrameRelevanceProvider(
        scores={
            labeled.candidate.provider_asset_id: (
                0.95 if labeled.label is CandidateLabel.RELEVANT else 0.2
            )
            for labeled in case.candidates
        },
        model="fake-vision",
        model_version="1",
    )
