"""Offline measurement of transcription quality, speed, reliability, and price.

Nothing here calls a provider. A live or offline runner produces one observation per labeled
case, and this module turns those observations into the versioned numbers a provider choice
must be defended with: word error rate, named-entity accuracy, word-timestamp drift,
diarization error, completion time, failure rate, and cost per source hour.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from math import ceil
from pathlib import Path
from statistics import median
from typing import Any

from clipah.transcripts.models import TranscriptResult

TRANSCRIPTION_REPORT_VERSION = "transcription-eval/1"
TRANSCRIPTION_MANIFEST_VERSION = "transcription-eval-cases/1"

_MICROS_PER_HOUR_DIVISOR = 3_600_000.0
_RATE_DIGITS = 4
_NON_COMPARABLE = re.compile(r"[^\w\s]", flags=re.UNICODE)


class EvaluationFixtureError(Exception):
    """Raised when a checked-in evaluation fixture is missing, malformed, or altered."""


@dataclass(frozen=True, slots=True)
class ReferenceWord:
    """One human-labeled word with the time and speaker a provider must reproduce."""

    text: str
    start_ms: int
    end_ms: int
    speaker: str


@dataclass(frozen=True, slots=True)
class TranscriptionCase:
    """One labeled transcription case, independent of the provider being measured."""

    case_id: str
    language: str
    duration_ms: int
    words: tuple[ReferenceWord, ...]
    named_entities: tuple[str, ...]
    audio_key: str | None = None


@dataclass(frozen=True, slots=True)
class TranscriptionObservation:
    """What one adapter produced for one case, including how long it took and cost."""

    case_id: str
    transcript: TranscriptResult | None
    error_code: str | None
    latency_ms: int
    cost_micros: int


@dataclass(frozen=True, slots=True)
class TranscriptionAccuracy:
    """How closely one observation reproduced the labels of one case."""

    word_error_rate: float
    named_entity_accuracy: float
    median_timestamp_drift_ms: int
    p95_timestamp_drift_ms: int
    diarization_error_rate: float


@dataclass(frozen=True, slots=True)
class TranscriptionMetrics:
    """The whole comparable picture of one adapter over one checked-in case set."""

    case_count: int
    word_error_rate: float
    named_entity_accuracy: float
    median_timestamp_drift_ms: int
    p95_timestamp_drift_ms: int
    diarization_error_rate: float
    p95_completion_ms: int
    failure_rate: float
    cost_per_source_hour_micros: float


@dataclass(frozen=True, slots=True)
class TranscriptionReport:
    """One versioned transcription evaluation run, attributable to a provider build."""

    manifest_version: str
    adapter: str
    provider: str
    provider_version: str
    model: str
    cases: tuple[tuple[TranscriptionCase, TranscriptionObservation, TranscriptionAccuracy], ...]
    aggregate: TranscriptionMetrics
    failed_cases: tuple[str, ...]

    @property
    def passed(self) -> bool:
        """Report success only when every labeled case was actually measured."""
        return not self.failed_cases

    def to_json(self) -> dict[str, Any]:
        """Render the report as the deterministic document a CI artifact stores."""
        return {
            "reportVersion": TRANSCRIPTION_REPORT_VERSION,
            "manifestVersion": self.manifest_version,
            "adapter": self.adapter,
            "provider": self.provider,
            "providerVersion": self.provider_version,
            "model": self.model,
            "aggregate": _metrics_document(self.aggregate),
            "cases": [
                {
                    "caseId": case.case_id,
                    "language": case.language,
                    "durationMs": case.duration_ms,
                    "errorCode": observation.error_code,
                    "latencyMs": observation.latency_ms,
                    "costMicros": observation.cost_micros,
                    "wordErrorRate": round(accuracy.word_error_rate, _RATE_DIGITS),
                    "namedEntityAccuracy": round(accuracy.named_entity_accuracy, _RATE_DIGITS),
                    "medianTimestampDriftMs": accuracy.median_timestamp_drift_ms,
                    "p95TimestampDriftMs": accuracy.p95_timestamp_drift_ms,
                    "diarizationErrorRate": round(accuracy.diarization_error_rate, _RATE_DIGITS),
                }
                for case, observation, accuracy in self.cases
            ],
            "failedCases": list(self.failed_cases),
            "passed": self.passed,
        }


def evaluate_transcription_case(
    *, case: TranscriptionCase, observation: TranscriptionObservation
) -> TranscriptionAccuracy:
    """Measure one observation against one case's labels, counting a failure as total error."""
    if observation.transcript is None:
        return TranscriptionAccuracy(
            word_error_rate=1.0,
            named_entity_accuracy=0.0,
            median_timestamp_drift_ms=0,
            p95_timestamp_drift_ms=0,
            diarization_error_rate=1.0,
        )
    reference = [_normalize(word.text) for word in case.words]
    hypothesis = [_normalize(word.text) for word in observation.transcript.words]
    matches = _alignment(reference, hypothesis)
    spoken = observation.transcript.words
    drifts = [
        abs(spoken[hypothesis_index].start_ms - case.words[reference_index].start_ms)
        for reference_index, hypothesis_index in matches
    ]
    return TranscriptionAccuracy(
        word_error_rate=_word_error_rate(reference, hypothesis),
        named_entity_accuracy=_named_entity_accuracy(case, observation.transcript),
        median_timestamp_drift_ms=int(median(drifts)) if drifts else 0,
        p95_timestamp_drift_ms=_percentile(drifts, 0.95),
        diarization_error_rate=_diarization_error_rate(case, observation.transcript, matches),
    )


def aggregate_transcription_metrics(
    *, cases: Sequence[TranscriptionCase], observations: Sequence[TranscriptionObservation]
) -> TranscriptionMetrics:
    """Combine one observation per case into the comparable provider-selection numbers."""
    accuracies = [
        evaluate_transcription_case(case=case, observation=observation)
        for case, observation in _paired(cases, observations)
    ]
    measured = [
        accuracy
        for accuracy, observation in zip(accuracies, observations, strict=True)
        if observation.transcript is not None
    ]
    failures = sum(1 for observation in observations if observation.transcript is None)
    source_hours = sum(case.duration_ms for case in cases) / _MICROS_PER_HOUR_DIVISOR
    return TranscriptionMetrics(
        case_count=len(accuracies),
        word_error_rate=_mean(accuracy.word_error_rate for accuracy in accuracies),
        named_entity_accuracy=_mean(accuracy.named_entity_accuracy for accuracy in accuracies),
        median_timestamp_drift_ms=(
            int(median([accuracy.median_timestamp_drift_ms for accuracy in measured]))
            if measured
            else 0
        ),
        p95_timestamp_drift_ms=_percentile(
            [accuracy.p95_timestamp_drift_ms for accuracy in measured], 0.95
        ),
        diarization_error_rate=_mean(accuracy.diarization_error_rate for accuracy in measured),
        p95_completion_ms=_percentile(
            [observation.latency_ms for observation in observations], 0.95
        ),
        failure_rate=round(failures / len(observations), _RATE_DIGITS) if observations else 0.0,
        cost_per_source_hour_micros=(
            round(sum(observation.cost_micros for observation in observations) / source_hours, 1)
            if source_hours > 0
            else 0.0
        ),
    )


def build_transcription_report(
    *,
    manifest_version: str,
    adapter: str,
    provider: str,
    provider_version: str,
    model: str,
    cases: Sequence[TranscriptionCase],
    observations: Sequence[TranscriptionObservation],
) -> TranscriptionReport:
    """Assemble one attributable report over the observations an adapter produced."""
    paired = _paired(cases, observations)
    return TranscriptionReport(
        manifest_version=manifest_version,
        adapter=adapter,
        provider=provider,
        provider_version=provider_version,
        model=model,
        cases=tuple(
            (case, observation, evaluate_transcription_case(case=case, observation=observation))
            for case, observation in paired
        ),
        aggregate=aggregate_transcription_metrics(cases=cases, observations=observations),
        failed_cases=tuple(
            case.case_id for case, observation in paired if observation.transcript is None
        ),
    )


def load_transcription_cases(directory: Path) -> tuple[str, tuple[TranscriptionCase, ...]]:
    """Read the checked-in transcription cases, refusing any file the manifest disowns."""
    manifest = load_manifest(directory)
    entries = _entries(manifest)
    return _required_string(manifest, "manifest_version"), tuple(
        _transcription_case(read_case_document(directory, entry)) for entry in entries
    )


def load_manifest(directory: Path) -> Mapping[str, Any]:
    """Read one evaluation manifest, refusing anything that is not a manifest object."""
    path = directory / "manifest.json"
    if not path.is_file():
        raise EvaluationFixtureError(f"evaluation manifest is missing at {path}")
    return _json_object(path)


def read_case_document(directory: Path, entry: Mapping[str, Any]) -> Mapping[str, Any]:
    """Read one case file only after its bytes match the digest the manifest recorded."""
    path = directory / _required_string(entry, "path")
    if not path.is_file():
        raise EvaluationFixtureError(f"evaluation case is missing at {path}")
    digest = sha256(path.read_bytes()).hexdigest()
    if digest != _required_string(entry, "sha256"):
        raise EvaluationFixtureError(
            f"evaluation case at {path} does not match its manifest digest"
        )
    return _json_object(path)


def _entries(manifest: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """Read the case list a manifest declares."""
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvaluationFixtureError("evaluation manifest declares no cases")
    return tuple(_mapping(entry) for entry in cases)


def _transcription_case(document: Mapping[str, Any]) -> TranscriptionCase:
    """Build one labeled case from its checked-in document."""
    words = tuple(
        ReferenceWord(
            text=_required_string(word, "text"),
            start_ms=_required_int(word, "start_ms"),
            end_ms=_required_int(word, "end_ms"),
            speaker=_required_string(word, "speaker"),
        )
        for word in _sequence(document, "reference_words")
    )
    if not words:
        raise EvaluationFixtureError("a transcription case must label at least one word")
    audio_key = document.get("audio_key")
    return TranscriptionCase(
        case_id=_required_string(document, "case_id"),
        language=_required_string(document, "language"),
        duration_ms=_required_int(document, "duration_ms"),
        words=words,
        named_entities=tuple(_strings(document, "named_entities")),
        audio_key=audio_key if isinstance(audio_key, str) else None,
    )


def _paired(
    cases: Sequence[TranscriptionCase], observations: Sequence[TranscriptionObservation]
) -> list[tuple[TranscriptionCase, TranscriptionObservation]]:
    """Pair each case with the observation taken for it, in the order they were taken."""
    return list(zip(cases, observations, strict=True))


def _word_error_rate(reference: Sequence[str], hypothesis: Sequence[str]) -> float:
    """Count substitutions, deletions, and insertions against the labeled word count."""
    if not reference:
        return 0.0
    return round(_edit_distance(reference, hypothesis) / len(reference), _RATE_DIGITS)


def _named_entity_accuracy(case: TranscriptionCase, transcript: TranscriptResult) -> float:
    """Measure how many labeled entities survive transcription intact."""
    if not case.named_entities:
        return 1.0
    spoken = [_normalize(word.text) for word in transcript.words]
    found = sum(1 for entity in case.named_entities if _contains(spoken, entity))
    return round(found / len(case.named_entities), _RATE_DIGITS)


def _diarization_error_rate(
    case: TranscriptionCase,
    transcript: TranscriptResult,
    matches: Sequence[tuple[int, int]],
) -> float:
    """Score the speaker partition alone, so an opaque relabeling costs nothing."""
    if not matches:
        return 1.0
    tally: dict[str, Counter[str]] = defaultdict(Counter)
    for reference_index, hypothesis_index in matches:
        tally[transcript.words[hypothesis_index].speaker][case.words[reference_index].speaker] += 1
    mapping = {
        speaker: min(counts.items(), key=lambda item: (-item[1], item[0]))[0]
        for speaker, counts in tally.items()
    }
    errors = sum(
        1
        for reference_index, hypothesis_index in matches
        if mapping[transcript.words[hypothesis_index].speaker]
        != case.words[reference_index].speaker
    )
    return round(errors / len(matches), _RATE_DIGITS)


def _alignment(reference: Sequence[str], hypothesis: Sequence[str]) -> list[tuple[int, int]]:
    """Return the index pairs a minimal edit path matches without substitution."""
    costs = _cost_table(reference, hypothesis)
    matches: list[tuple[int, int]] = []
    row, column = len(reference), len(hypothesis)
    while row > 0 and column > 0:
        if (
            reference[row - 1] == hypothesis[column - 1]
            and costs[row][column] == costs[row - 1][column - 1]
        ):
            matches.append((row - 1, column - 1))
            row, column = row - 1, column - 1
        elif costs[row][column] == costs[row - 1][column - 1] + 1:
            row, column = row - 1, column - 1
        elif costs[row][column] == costs[row - 1][column] + 1:
            row -= 1
        else:
            column -= 1
    matches.reverse()
    return matches


def _edit_distance(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
    """Measure the minimal number of word edits between the two sequences."""
    return _cost_table(reference, hypothesis)[len(reference)][len(hypothesis)]


def _cost_table(reference: Sequence[str], hypothesis: Sequence[str]) -> list[list[int]]:
    """Build the edit-cost table both the distance and the alignment read."""
    costs = [[0] * (len(hypothesis) + 1) for _ in range(len(reference) + 1)]
    for row in range(len(reference) + 1):
        costs[row][0] = row
    for column in range(len(hypothesis) + 1):
        costs[0][column] = column
    for row in range(1, len(reference) + 1):
        for column in range(1, len(hypothesis) + 1):
            substitution = costs[row - 1][column - 1] + (
                0 if reference[row - 1] == hypothesis[column - 1] else 1
            )
            costs[row][column] = min(
                substitution, costs[row - 1][column] + 1, costs[row][column - 1] + 1
            )
    return costs


def _contains(spoken: Sequence[str], entity: str) -> bool:
    """Decide whether the transcript says one labeled entity, word for word."""
    tokens = [_normalize(part) for part in entity.split() if _normalize(part)]
    if not tokens:
        return False
    return any(
        list(spoken[index : index + len(tokens)]) == tokens
        for index in range(len(spoken) - len(tokens) + 1)
    )


def _normalize(text: str) -> str:
    """Reduce one word to the form a comparison may read."""
    return _NON_COMPARABLE.sub("", text).casefold()


def _percentile(values: Sequence[int], fraction: float) -> int:
    """Return the nearest-rank percentile, which needs no interpolation to explain."""
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def _mean(values: Iterable[float]) -> float:
    """Average an iterable of rates, treating an empty set as zero."""
    collected = list(values)
    if not collected:
        return 0.0
    return round(sum(collected) / len(collected), _RATE_DIGITS)


def _metrics_document(metrics: TranscriptionMetrics) -> dict[str, Any]:
    """Render aggregate metrics with stable key names and stable rounding."""
    return {
        "caseCount": metrics.case_count,
        "wordErrorRate": round(metrics.word_error_rate, _RATE_DIGITS),
        "namedEntityAccuracy": round(metrics.named_entity_accuracy, _RATE_DIGITS),
        "medianTimestampDriftMs": metrics.median_timestamp_drift_ms,
        "p95TimestampDriftMs": metrics.p95_timestamp_drift_ms,
        "diarizationErrorRate": round(metrics.diarization_error_rate, _RATE_DIGITS),
        "p95CompletionMs": metrics.p95_completion_ms,
        "failureRate": round(metrics.failure_rate, _RATE_DIGITS),
        "costPerSourceHourMicros": round(metrics.cost_per_source_hour_micros, 1),
    }


def _json_object(path: Path) -> Mapping[str, Any]:
    """Read one JSON object, refusing anything else the file may contain."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise EvaluationFixtureError(f"evaluation fixture at {path} is not valid JSON") from error
    return _mapping(document)


def _mapping(value: object) -> Mapping[str, Any]:
    """Accept only a JSON object where the fixture format promises one."""
    if not isinstance(value, dict):
        raise EvaluationFixtureError("evaluation fixture expected a JSON object")
    return value


def _sequence(document: Mapping[str, Any], field: str) -> tuple[Mapping[str, Any], ...]:
    """Read a required list of objects from one fixture document."""
    value = document.get(field)
    if not isinstance(value, list):
        raise EvaluationFixtureError(f"evaluation fixture field {field} must be a list")
    return tuple(_mapping(item) for item in value)


def _strings(document: Mapping[str, Any], field: str) -> tuple[str, ...]:
    """Read a required list of strings from one fixture document."""
    value = document.get(field)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise EvaluationFixtureError(f"evaluation fixture field {field} must be a list of strings")
    return tuple(str(item) for item in value)


def _required_string(document: Mapping[str, Any], field: str) -> str:
    """Read a required string from one fixture document."""
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise EvaluationFixtureError(f"evaluation fixture field {field} must be a non-empty string")
    return value


def _required_int(document: Mapping[str, Any], field: str) -> int:
    """Read a required integer from one fixture document."""
    value = document.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise EvaluationFixtureError(f"evaluation fixture field {field} must be an integer")
    return value
