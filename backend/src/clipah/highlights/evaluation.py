"""Offline measurement of highlight quality against human-approved labels.

The harness never asks a provider anything. A runner produces ranked candidates for one
labeled case, and this module decides whether those candidates are timestamp-valid, duration-
valid, distinct, safe to publish, and actually the moments a human approved. The gates below
are the release criteria: a provider change that violates one is a regression, whatever its
prose looks like.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clipah.highlights.deduplicate import (
    DEFAULT_DEDUPLICATION_POLICY,
    DeduplicationPolicy,
    deduplicate,
)
from clipah.highlights.models import (
    DEFAULT_CANDIDATE_POLICY,
    CandidatePolicy,
    ClipCandidateDraft,
)
from clipah.highlights.rerank import RankedCandidate
from clipah.transcripts.evaluation import (
    EvaluationFixtureError,
    load_manifest,
    read_case_document,
)
from clipah.transcripts.models import (
    SpeakerSegment,
    TranscriptResult,
    TranscriptUtterance,
    TranscriptWord,
)

HIGHLIGHT_REPORT_VERSION = "highlight-eval/1"
HIGHLIGHT_MANIFEST_VERSION = "highlight-eval-cases/1"

GATE_TIMESTAMP_VALIDITY = "HIGHLIGHT_GATE_TIMESTAMP_VALIDITY"
GATE_DURATION_VALIDITY = "HIGHLIGHT_GATE_DURATION_VALIDITY"
GATE_DUPLICATE_RATE = "HIGHLIGHT_GATE_DUPLICATE_RATE"
GATE_CONTEXT_SAFETY_RECALL = "HIGHLIGHT_GATE_CONTEXT_SAFETY_RECALL"
GATE_TOP_THREE_ACCEPTANCE = "HIGHLIGHT_GATE_TOP_THREE_ACCEPTANCE"

_ACCEPTANCE_DEPTH = 3
_RATE_DIGITS = 4


@dataclass(frozen=True, slots=True)
class ApprovedClip:
    """One human-approved moment and the overlap a produced clip must reach to count."""

    label: str
    start_word_id: str
    end_word_id: str
    start_ms: int
    end_ms: int
    min_overlap: float
    max_overlap: float
    risky: bool


@dataclass(frozen=True, slots=True)
class HighlightCase:
    """One labeled highlight case: an authoritative transcript and its approved moments."""

    case_id: str
    language: str
    source_seconds: int
    transcript: TranscriptResult
    approved: tuple[ApprovedClip, ...]
    named_entities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HighlightMetrics:
    """The gated dimensions of one case, or of a whole checked-in case set."""

    case_count: int
    candidate_count: int
    timestamp_validity: float
    duration_validity: float
    duplicate_rate: float
    context_safety_recall: float
    top_three_acceptance: float


@dataclass(frozen=True, slots=True)
class HighlightGates:
    """The release criteria a highlight evaluation run must clear."""

    min_timestamp_validity: float
    min_duration_validity: float
    max_duplicate_rate: float
    min_context_safety_recall: float
    min_top_three_acceptance: float


DEFAULT_HIGHLIGHT_GATES = HighlightGates(
    min_timestamp_validity=1.0,
    min_duration_validity=1.0,
    max_duplicate_rate=0.10,
    min_context_safety_recall=0.90,
    min_top_three_acceptance=0.70,
)


@dataclass(frozen=True, slots=True)
class HighlightCaseReport:
    """One case's measured dimensions, kept beside its identity for later comparison."""

    case_id: str
    language: str
    metrics: HighlightMetrics


@dataclass(frozen=True, slots=True)
class HighlightReport:
    """One versioned highlight evaluation run, attributable to a provider and prompt."""

    manifest_version: str
    adapter: str
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    cases: tuple[HighlightCaseReport, ...]
    aggregate: HighlightMetrics
    gates: HighlightGates
    gate_violations: tuple[str, ...]

    @property
    def passed(self) -> bool:
        """Report success only when no gate was violated."""
        return not self.gate_violations

    def to_json(self) -> dict[str, Any]:
        """Render the report as the deterministic document a CI artifact stores."""
        return {
            "reportVersion": HIGHLIGHT_REPORT_VERSION,
            "manifestVersion": self.manifest_version,
            "adapter": self.adapter,
            "provider": self.provider,
            "model": self.model,
            "promptVersion": self.prompt_version,
            "schemaVersion": self.schema_version,
            "gates": {
                "minTimestampValidity": self.gates.min_timestamp_validity,
                "minDurationValidity": self.gates.min_duration_validity,
                "maxDuplicateRate": self.gates.max_duplicate_rate,
                "minContextSafetyRecall": self.gates.min_context_safety_recall,
                "minTopThreeAcceptance": self.gates.min_top_three_acceptance,
            },
            "aggregate": _metrics_document(self.aggregate),
            "cases": [
                {
                    "caseId": case.case_id,
                    "language": case.language,
                    **_metrics_document(case.metrics),
                }
                for case in self.cases
            ],
            "gateViolations": list(self.gate_violations),
            "passed": self.passed,
        }


def evaluate_highlight_case(
    *,
    case: HighlightCase,
    ranked: Sequence[RankedCandidate],
    candidate_policy: CandidatePolicy = DEFAULT_CANDIDATE_POLICY,
    deduplication: DeduplicationPolicy = DEFAULT_DEDUPLICATION_POLICY,
) -> HighlightMetrics:
    """Measure one ranked result set against one case's human-approved labels."""
    words = {word.word_id: word for word in case.transcript.words}
    drafts = [candidate.draft for candidate in ranked]
    valid = [draft for draft in drafts if _timestamps_hold(draft, words)]
    reviewable = [draft for draft in drafts if _duration_holds(draft, candidate_policy)]
    usable = [draft for draft in valid if _duration_holds(draft, candidate_policy)]
    accepted = _accepts_a_top_moment(case, ranked, candidate_policy, words)
    return HighlightMetrics(
        case_count=1,
        candidate_count=len(drafts),
        timestamp_validity=_rate(len(valid), len(drafts)),
        duration_validity=_rate(len(reviewable), len(drafts)),
        duplicate_rate=_duplicate_rate(drafts, deduplication),
        context_safety_recall=_context_safety_recall(case, usable),
        top_three_acceptance=1.0 if accepted else 0.0,
    )


def aggregate_highlight_metrics(metrics: Sequence[HighlightMetrics]) -> HighlightMetrics:
    """Combine case metrics, weighing clip-level rates by the clips each case produced."""
    candidates = sum(entry.candidate_count for entry in metrics)
    cases = sum(entry.case_count for entry in metrics)
    return HighlightMetrics(
        case_count=cases,
        candidate_count=candidates,
        timestamp_validity=_weighted(metrics, "timestamp_validity", candidates),
        duration_validity=_weighted(metrics, "duration_validity", candidates),
        duplicate_rate=_weighted(metrics, "duplicate_rate", candidates),
        context_safety_recall=_mean(entry.context_safety_recall for entry in metrics),
        top_three_acceptance=_mean(entry.top_three_acceptance for entry in metrics),
    )


def check_highlight_gates(metrics: HighlightMetrics, gates: HighlightGates) -> tuple[str, ...]:
    """Name every release criterion the measured run failed, in a stable order."""
    violations: list[str] = []
    if metrics.timestamp_validity < gates.min_timestamp_validity:
        violations.append(GATE_TIMESTAMP_VALIDITY)
    if metrics.duration_validity < gates.min_duration_validity:
        violations.append(GATE_DURATION_VALIDITY)
    if metrics.duplicate_rate >= gates.max_duplicate_rate:
        violations.append(GATE_DUPLICATE_RATE)
    if metrics.context_safety_recall < gates.min_context_safety_recall:
        violations.append(GATE_CONTEXT_SAFETY_RECALL)
    if metrics.top_three_acceptance < gates.min_top_three_acceptance:
        violations.append(GATE_TOP_THREE_ACCEPTANCE)
    return tuple(violations)


def build_highlight_report(
    *,
    manifest_version: str,
    adapter: str,
    provider: str,
    model: str,
    prompt_version: str,
    schema_version: str,
    cases: Sequence[tuple[str, str, HighlightMetrics]],
    gates: HighlightGates = DEFAULT_HIGHLIGHT_GATES,
) -> HighlightReport:
    """Assemble one attributable report over the cases a runner measured."""
    aggregate = aggregate_highlight_metrics([metrics for _, _, metrics in cases])
    return HighlightReport(
        manifest_version=manifest_version,
        adapter=adapter,
        provider=provider,
        model=model,
        prompt_version=prompt_version,
        schema_version=schema_version,
        cases=tuple(
            HighlightCaseReport(case_id=case_id, language=language, metrics=metrics)
            for case_id, language, metrics in cases
        ),
        aggregate=aggregate,
        gates=gates,
        gate_violations=check_highlight_gates(aggregate, gates),
    )


def load_highlight_cases(directory: Path) -> tuple[str, tuple[HighlightCase, ...]]:
    """Read the checked-in highlight cases, refusing any file the manifest disowns."""
    manifest = load_manifest(directory)
    version = manifest.get("manifest_version")
    if not isinstance(version, str) or not version:
        raise EvaluationFixtureError("evaluation manifest declares no manifest_version")
    entries = manifest.get("cases")
    if not isinstance(entries, list) or not entries:
        raise EvaluationFixtureError("evaluation manifest declares no cases")
    cases = tuple(
        _highlight_case(read_case_document(directory, _entry(entry))) for entry in entries
    )
    return version, cases


def _entry(value: object) -> dict[str, Any]:
    """Accept only a manifest entry object."""
    if not isinstance(value, dict):
        raise EvaluationFixtureError("evaluation manifest entry must be an object")
    return value


def _highlight_case(document: Any) -> HighlightCase:
    """Build one labeled case, resolving approved spans through the case's own words."""
    words = tuple(_word(entry) for entry in _list(document, "words"))
    if not words:
        raise EvaluationFixtureError("a highlight case must contain words")
    index = {word.word_id: word for word in words}
    approved = tuple(_approved(entry, index) for entry in _list(document, "approved_clips"))
    if not approved:
        raise EvaluationFixtureError("a highlight case must approve at least one clip")
    return HighlightCase(
        case_id=_text(document, "case_id"),
        language=_text(document, "language"),
        source_seconds=_integer(document, "source_seconds"),
        transcript=_transcript(document, words),
        approved=approved,
        named_entities=tuple(_list_of_strings(document, "named_entities")),
    )


def _word(entry: Any) -> TranscriptWord:
    """Build one authoritative word from its checked-in labels."""
    punctuation = entry.get("punctuation", "")
    if not isinstance(punctuation, str):
        raise EvaluationFixtureError("word punctuation must be a string")
    return TranscriptWord(
        word_id=_text(entry, "word_id"),
        text=_text(entry, "text"),
        punctuation=punctuation,
        start_ms=_integer(entry, "start_ms"),
        end_ms=_integer(entry, "end_ms"),
        confidence=_number(entry, "confidence"),
        speaker=_text(entry, "speaker"),
    )


def _approved(entry: Any, index: dict[str, TranscriptWord]) -> ApprovedClip:
    """Resolve one approved span's bounds from the transcript rather than from prose."""
    start = index.get(_text(entry, "start_word_id"))
    end = index.get(_text(entry, "end_word_id"))
    if start is None or end is None:
        raise EvaluationFixtureError("an approved clip references a word the case does not have")
    risky = entry.get("risky", False)
    if not isinstance(risky, bool):
        raise EvaluationFixtureError("an approved clip's risky label must be a boolean")
    return ApprovedClip(
        label=_text(entry, "label"),
        start_word_id=start.word_id,
        end_word_id=end.word_id,
        start_ms=start.start_ms,
        end_ms=end.end_ms,
        min_overlap=_number(entry, "min_overlap"),
        max_overlap=_number(entry, "max_overlap"),
        risky=risky,
    )


def _transcript(document: Any, words: tuple[TranscriptWord, ...]) -> TranscriptResult:
    """Derive the transcript shape from the words so a fixture stays readable."""
    segments = _speaker_segments(words)
    return TranscriptResult(
        provider="evaluation-fixture",
        provider_version=HIGHLIGHT_MANIFEST_VERSION,
        model="evaluation-fixture",
        language=_text(document, "language"),
        full_text=" ".join(f"{word.text}{word.punctuation}" for word in words).strip(),
        words=words,
        speaker_segments=segments,
        utterances=tuple(
            TranscriptUtterance(
                utterance_id=f"u{position + 1:06d}",
                text=" ".join(
                    f"{word.text}{word.punctuation}"
                    for word in words
                    if word.word_id in set(segment.word_ids)
                ).strip(),
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                speaker=segment.speaker,
                word_ids=segment.word_ids,
            )
            for position, segment in enumerate(segments)
        ),
        duration_ms=words[-1].end_ms,
        raw_result={},
    )


def _speaker_segments(words: tuple[TranscriptWord, ...]) -> tuple[SpeakerSegment, ...]:
    """Collapse the words into maximal contiguous runs of one speaker."""
    segments: list[SpeakerSegment] = []
    run: list[TranscriptWord] = []
    for word in words:
        if run and word.speaker != run[0].speaker:
            segments.append(_segment(run, len(segments) + 1))
            run = []
        run.append(word)
    segments.append(_segment(run, len(segments) + 1))
    return tuple(segments)


def _segment(run: list[TranscriptWord], position: int) -> SpeakerSegment:
    """Describe one contiguous speaker run."""
    return SpeakerSegment(
        segment_id=f"s{position:06d}",
        speaker=run[0].speaker,
        start_ms=run[0].start_ms,
        end_ms=run[-1].end_ms,
        word_ids=tuple(word.word_id for word in run),
    )


def _timestamps_hold(draft: ClipCandidateDraft, words: dict[str, TranscriptWord]) -> bool:
    """Decide whether a candidate's bounds are the transcript's own, not a provider's."""
    start = words.get(draft.start_word_id)
    end = words.get(draft.end_word_id)
    if start is None or end is None:
        return False
    return (
        draft.start_ms == start.start_ms
        and draft.end_ms == end.end_ms
        and draft.start_ms < draft.end_ms
        and draft.duration_ms == draft.end_ms - draft.start_ms
    )


def _duration_holds(draft: ClipCandidateDraft, policy: CandidatePolicy) -> bool:
    """Decide whether a candidate falls inside the reviewable duration preset."""
    return policy.min_duration_ms <= draft.duration_ms <= policy.max_duration_ms


def _duplicate_rate(drafts: Sequence[ClipCandidateDraft], policy: DeduplicationPolicy) -> float:
    """Measure how many produced candidates repeat a moment already on the list."""
    if not drafts:
        return 0.0
    survivors = deduplicate(drafts, policy=policy)
    return _rate(len(drafts) - len(survivors), len(drafts))


def _context_safety_recall(case: HighlightCase, usable: Sequence[ClipCandidateDraft]) -> float:
    """Measure how many labeled risky cuts were surfaced carrying a context warning."""
    risky = [clip for clip in case.approved if clip.risky]
    if not risky:
        return 1.0
    warned = sum(
        1
        for clip in risky
        if any(draft.context_warnings and _matches(draft, clip) for draft in usable)
    )
    return _rate(warned, len(risky))


def _accepts_a_top_moment(
    case: HighlightCase,
    ranked: Sequence[RankedCandidate],
    policy: CandidatePolicy,
    words: dict[str, TranscriptWord],
) -> bool:
    """Decide whether a reviewer would find an approved moment in the first three clips."""
    top = [
        candidate.draft
        for candidate in sorted(ranked, key=lambda candidate: candidate.rank)[:_ACCEPTANCE_DEPTH]
        if _timestamps_hold(candidate.draft, words) and _duration_holds(candidate.draft, policy)
    ]
    return any(_matches(draft, clip) for draft in top for clip in case.approved)


def _matches(draft: ClipCandidateDraft, clip: ApprovedClip) -> bool:
    """Decide whether one candidate covers one approved span within its overlap range."""
    overlap = _overlap(draft.start_ms, draft.end_ms, clip.start_ms, clip.end_ms)
    return clip.min_overlap <= overlap <= clip.max_overlap


def _overlap(first_start: int, first_end: int, second_start: int, second_end: int) -> float:
    """Measure how much of the combined span two intervals share."""
    intersection = min(first_end, second_end) - max(first_start, second_start)
    if intersection <= 0:
        return 0.0
    union = max(first_end, second_end) - min(first_start, second_start)
    return intersection / union


def _rate(part: int, whole: int) -> float:
    """Express one count as a share of another, treating an empty whole as zero."""
    if whole <= 0:
        return 0.0
    return round(part / whole, _RATE_DIGITS)


def _weighted(metrics: Sequence[HighlightMetrics], field: str, candidates: int) -> float:
    """Average a clip-level rate by the number of clips each case produced."""
    if candidates <= 0:
        return 0.0
    total = sum(float(getattr(entry, field)) * entry.candidate_count for entry in metrics)
    return round(total / candidates, _RATE_DIGITS)


def _mean(values: Iterable[float]) -> float:
    """Average a case-level rate, treating an empty set as zero."""
    collected = list(values)
    if not collected:
        return 0.0
    return round(sum(collected) / len(collected), _RATE_DIGITS)


def _metrics_document(metrics: HighlightMetrics) -> dict[str, Any]:
    """Render metrics with stable key names and stable rounding."""
    return {
        "caseCount": metrics.case_count,
        "candidateCount": metrics.candidate_count,
        "timestampValidity": round(metrics.timestamp_validity, _RATE_DIGITS),
        "durationValidity": round(metrics.duration_validity, _RATE_DIGITS),
        "duplicateRate": round(metrics.duplicate_rate, _RATE_DIGITS),
        "contextSafetyRecall": round(metrics.context_safety_recall, _RATE_DIGITS),
        "topThreeAcceptance": round(metrics.top_three_acceptance, _RATE_DIGITS),
    }


def _list(document: Any, field: str) -> tuple[Any, ...]:
    """Read a required list of objects from one case document."""
    value = document.get(field)
    if not isinstance(value, list):
        raise EvaluationFixtureError(f"evaluation fixture field {field} must be a list")
    for item in value:
        if not isinstance(item, dict):
            raise EvaluationFixtureError(f"evaluation fixture field {field} must hold objects")
    return tuple(value)


def _list_of_strings(document: Any, field: str) -> tuple[str, ...]:
    """Read a required list of strings from one case document."""
    value = document.get(field)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise EvaluationFixtureError(f"evaluation fixture field {field} must be a list of strings")
    return tuple(str(item) for item in value)


def _text(document: Any, field: str) -> str:
    """Read a required non-empty string from one case document."""
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise EvaluationFixtureError(f"evaluation fixture field {field} must be a non-empty string")
    return value


def _integer(document: Any, field: str) -> int:
    """Read a required integer from one case document."""
    value = document.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise EvaluationFixtureError(f"evaluation fixture field {field} must be an integer")
    return value


def _number(document: Any, field: str) -> float:
    """Read a required number from one case document."""
    value = document.get(field)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise EvaluationFixtureError(f"evaluation fixture field {field} must be a number")
    return float(value)
