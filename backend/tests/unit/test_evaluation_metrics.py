"""Contracts for the offline highlight and transcription evaluation metrics.

The harness is the only thing standing between a provider change and a silent quality
regression, so every metric is proved against a synthetic result set whose correct answer is
known by construction: a perfect run, a duplicate-heavy run, a run with invalid timestamps,
and a run that misses the human-approved moment entirely.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from clipah.highlights.evaluation import (
    DEFAULT_HIGHLIGHT_GATES,
    GATE_CONTEXT_SAFETY_RECALL,
    GATE_DUPLICATE_RATE,
    GATE_DURATION_VALIDITY,
    GATE_TIMESTAMP_VALIDITY,
    GATE_TOP_THREE_ACCEPTANCE,
    HIGHLIGHT_REPORT_VERSION,
    ApprovedClip,
    EvaluationFixtureError,
    HighlightCase,
    aggregate_highlight_metrics,
    build_highlight_report,
    check_highlight_gates,
    evaluate_highlight_case,
    load_highlight_cases,
)
from clipah.highlights.models import ClipCandidateDraft, ClipCategory, ScoreBreakdown
from clipah.highlights.rerank import RankedCandidate
from clipah.transcripts.evaluation import (
    TRANSCRIPTION_REPORT_VERSION,
    ReferenceWord,
    TranscriptionCase,
    TranscriptionObservation,
    aggregate_transcription_metrics,
    build_transcription_report,
    evaluate_transcription_case,
    load_transcription_cases,
)
from clipah.transcripts.models import (
    SpeakerSegment,
    TranscriptResult,
    TranscriptUtterance,
    TranscriptWord,
)

_WORD_MS = 500


def _word(index: int, text: str, *, speaker: str = "A", punctuation: str = "") -> TranscriptWord:
    """Build one canonical word on a fixed half-second grid."""
    return TranscriptWord(
        word_id=f"w{index:06d}",
        text=text,
        punctuation=punctuation,
        start_ms=index * _WORD_MS,
        end_ms=index * _WORD_MS + _WORD_MS,
        confidence=0.95,
        speaker=speaker,
    )


def _transcript(words: tuple[TranscriptWord, ...]) -> TranscriptResult:
    """Wrap words in the smallest transcript the metrics need."""
    return TranscriptResult(
        provider="fixture",
        provider_version="1",
        model="fixture",
        language="en",
        full_text=" ".join(word.text for word in words),
        words=words,
        speaker_segments=(
            SpeakerSegment(
                segment_id="s000001",
                speaker=words[0].speaker,
                start_ms=words[0].start_ms,
                end_ms=words[-1].end_ms,
                word_ids=tuple(word.word_id for word in words),
            ),
        ),
        utterances=(
            TranscriptUtterance(
                utterance_id="u000001",
                text=" ".join(word.text for word in words),
                start_ms=words[0].start_ms,
                end_ms=words[-1].end_ms,
                speaker=words[0].speaker,
                word_ids=tuple(word.word_id for word in words),
            ),
        ),
        duration_ms=words[-1].end_ms,
        raw_result={},
    )


_WORDS = tuple(_word(index, f"word{index}") for index in range(400))
_TRANSCRIPT = _transcript(_WORDS)


def _breakdown(context_safety: float = 0.5) -> ScoreBreakdown:
    """Build one neutral breakdown so a test can move a single dimension."""
    return ScoreBreakdown(
        hook=0.5,
        payoff=0.5,
        narrative_completeness=0.5,
        context_safety=context_safety,
        platform_fit=0.5,
        transcript_confidence=0.5,
        visual_opportunity=0.5,
    )


def _draft(
    *,
    first: int,
    last: int,
    start_ms: int | None = None,
    end_ms: int | None = None,
    warnings: tuple[str, ...] = (),
    excerpt: str | None = None,
) -> ClipCandidateDraft:
    """Build one candidate covering the given word indices, optionally lying about time."""
    resolved_start = _WORDS[first].start_ms if start_ms is None else start_ms
    resolved_end = _WORDS[last].end_ms if end_ms is None else end_ms
    return ClipCandidateDraft(
        hook="hook",
        payoff="payoff",
        reason="reason",
        category=ClipCategory.INSIGHT,
        tags=(),
        start_word_id=_WORDS[first].word_id,
        end_word_id=_WORDS[last].word_id,
        start_ms=resolved_start,
        end_ms=resolved_end,
        duration_ms=resolved_end - resolved_start,
        transcript_excerpt=excerpt or " ".join(word.text for word in _WORDS[first : last + 1]),
        context_dependencies=(),
        context_warnings=warnings,
        visual_opportunities=(),
        score_breakdown=_breakdown(),
    )


def _ranked(*drafts: ClipCandidateDraft) -> tuple[RankedCandidate, ...]:
    """Place drafts in the order a review surface would show them."""
    return tuple(
        RankedCandidate(rank=position + 1, score=1.0 - position / 100, draft=draft)
        for position, draft in enumerate(drafts)
    )


def _approved(
    *,
    first: int,
    last: int,
    risky: bool = False,
    min_overlap: float = 0.5,
    max_overlap: float = 1.0,
) -> ApprovedClip:
    """Describe one human-approved span with the overlap a clip must reach."""
    return ApprovedClip(
        label=f"approved-{first}",
        start_word_id=_WORDS[first].word_id,
        end_word_id=_WORDS[last].word_id,
        start_ms=_WORDS[first].start_ms,
        end_ms=_WORDS[last].end_ms,
        min_overlap=min_overlap,
        max_overlap=max_overlap,
        risky=risky,
    )


def _case(*approved: ApprovedClip) -> HighlightCase:
    """Build one labeled evaluation case over the shared transcript."""
    return HighlightCase(
        case_id="en-fixture",
        language="en",
        source_seconds=_TRANSCRIPT.duration_ms // 1000,
        transcript=_TRANSCRIPT,
        approved=approved,
        named_entities=(),
    )


@pytest.mark.unit
def test_scores_a_perfect_result_set_at_every_gate() -> None:
    """A run whose clips are valid, distinct, and human-approved must clear every gate."""
    case = _case(_approved(first=0, last=79), _approved(first=120, last=199))
    ranked = _ranked(_draft(first=0, last=79), _draft(first=120, last=199))

    metrics = evaluate_highlight_case(case=case, ranked=ranked)

    assert metrics.timestamp_validity == 1.0
    assert metrics.duration_validity == 1.0
    assert metrics.duplicate_rate == 0.0
    assert metrics.top_three_acceptance == 1.0
    assert check_highlight_gates(metrics, DEFAULT_HIGHLIGHT_GATES) == ()


@pytest.mark.unit
def test_counts_a_candidate_whose_timestamps_contradict_the_transcript() -> None:
    """A clip rendered from provider text instead of the words would cut the wrong audio."""
    case = _case(_approved(first=0, last=79))
    ranked = _ranked(
        _draft(first=0, last=79),
        _draft(first=200, last=279, start_ms=1_000, end_ms=41_000),
    )

    metrics = evaluate_highlight_case(case=case, ranked=ranked)

    assert metrics.timestamp_validity == 0.5
    assert GATE_TIMESTAMP_VALIDITY in check_highlight_gates(metrics, DEFAULT_HIGHLIGHT_GATES)


@pytest.mark.unit
def test_counts_a_candidate_whose_word_identity_is_not_in_the_transcript() -> None:
    """A candidate keyed to an unknown word cannot be resolved to real audio at all."""
    case = _case(_approved(first=0, last=79))
    unknown = _draft(first=0, last=79)
    stranger = ClipCandidateDraft(
        **{**unknown.model_dump(), "start_word_id": "w999999", "end_word_id": "w999998"}
    )

    metrics = evaluate_highlight_case(case=case, ranked=_ranked(unknown, stranger))

    assert metrics.timestamp_validity == 0.5


@pytest.mark.unit
def test_counts_a_candidate_outside_the_reviewable_duration_preset() -> None:
    """A clip shorter than the preset is not reviewable even when its timestamps are true."""
    case = _case(_approved(first=0, last=79))
    ranked = _ranked(_draft(first=0, last=79), _draft(first=200, last=209))

    metrics = evaluate_highlight_case(case=case, ranked=ranked)

    assert metrics.duration_validity == 0.5
    assert GATE_DURATION_VALIDITY in check_highlight_gates(metrics, DEFAULT_HIGHLIGHT_GATES)


@pytest.mark.unit
def test_reports_the_duplicate_rate_of_a_duplicate_heavy_result_set() -> None:
    """Overlapping windows repeat a moment, and review must never show it twice."""
    case = _case(_approved(first=0, last=79))
    ranked = _ranked(
        _draft(first=0, last=79),
        _draft(first=1, last=80),
        _draft(first=200, last=279),
        _draft(first=300, last=379),
    )

    metrics = evaluate_highlight_case(case=case, ranked=ranked)

    assert metrics.duplicate_rate == 0.25
    assert GATE_DUPLICATE_RATE in check_highlight_gates(metrics, DEFAULT_HIGHLIGHT_GATES)


@pytest.mark.unit
def test_scores_no_acceptance_when_the_top_three_miss_the_approved_moment() -> None:
    """A run that never surfaces the human-approved moment has failed its only job."""
    case = _case(_approved(first=0, last=79))
    ranked = _ranked(
        _draft(first=100, last=179),
        _draft(first=200, last=279),
        _draft(first=300, last=379),
    )

    metrics = evaluate_highlight_case(case=case, ranked=ranked)

    assert metrics.top_three_acceptance == 0.0
    assert GATE_TOP_THREE_ACCEPTANCE in check_highlight_gates(metrics, DEFAULT_HIGHLIGHT_GATES)


@pytest.mark.unit
def test_ignores_a_match_found_only_below_the_fourth_rank() -> None:
    """Acceptance is measured where a reviewer actually looks, not anywhere in the list."""
    case = _case(_approved(first=0, last=79))
    ranked = _ranked(
        _draft(first=100, last=179),
        _draft(first=200, last=279),
        _draft(first=300, last=379),
        _draft(first=0, last=79),
    )

    metrics = evaluate_highlight_case(case=case, ranked=ranked)

    assert metrics.top_three_acceptance == 0.0


@pytest.mark.unit
def test_rejects_a_match_whose_overlap_exceeds_the_labeled_acceptable_range() -> None:
    """An acceptable-overlap range has two ends; a label may refuse an exact repeat."""
    case = _case(_approved(first=0, last=79, min_overlap=0.3, max_overlap=0.8))
    ranked = _ranked(_draft(first=0, last=79))

    metrics = evaluate_highlight_case(case=case, ranked=ranked)

    assert metrics.top_three_acceptance == 0.0


@pytest.mark.unit
def test_misses_context_safety_recall_when_a_risky_cut_carries_no_warning() -> None:
    """A risky cut published without its warning is the failure this metric exists for."""
    case = _case(
        _approved(first=0, last=79, risky=True), _approved(first=120, last=199, risky=True)
    )
    ranked = _ranked(
        _draft(first=0, last=79, warnings=("needs the preceding question",)),
        _draft(first=120, last=199),
    )

    metrics = evaluate_highlight_case(case=case, ranked=ranked)

    assert metrics.context_safety_recall == 0.5
    assert GATE_CONTEXT_SAFETY_RECALL in check_highlight_gates(metrics, DEFAULT_HIGHLIGHT_GATES)


@pytest.mark.unit
def test_treats_an_unretrieved_risky_cut_as_a_recall_miss() -> None:
    """A risky moment nobody proposed is still a risky moment nobody warned about."""
    case = _case(_approved(first=0, last=79, risky=True))
    ranked = _ranked(_draft(first=200, last=279))

    metrics = evaluate_highlight_case(case=case, ranked=ranked)

    assert metrics.context_safety_recall == 0.0


@pytest.mark.unit
def test_scores_full_context_safety_recall_when_no_cut_is_labeled_risky() -> None:
    """A case with nothing risky must not drag the aggregate recall toward zero."""
    case = _case(_approved(first=0, last=79))

    metrics = evaluate_highlight_case(case=case, ranked=_ranked(_draft(first=0, last=79)))

    assert metrics.context_safety_recall == 1.0


@pytest.mark.unit
def test_scores_an_empty_result_set_as_a_total_miss_without_dividing_by_zero() -> None:
    """A provider that returns nothing must fail loudly rather than crash the harness."""
    case = _case(_approved(first=0, last=79, risky=True))

    metrics = evaluate_highlight_case(case=case, ranked=())

    assert metrics.candidate_count == 0
    assert metrics.timestamp_validity == 0.0
    assert metrics.duplicate_rate == 0.0
    assert metrics.top_three_acceptance == 0.0
    assert metrics.context_safety_recall == 0.0


@pytest.mark.unit
def test_weighs_candidate_level_metrics_by_candidate_count_across_cases() -> None:
    """One long case must not be outvoted by one short case when clips are counted."""
    perfect = evaluate_highlight_case(
        case=_case(_approved(first=0, last=79)),
        ranked=_ranked(
            _draft(first=0, last=79), _draft(first=120, last=199), _draft(first=240, last=319)
        ),
    )
    broken = evaluate_highlight_case(
        case=_case(_approved(first=0, last=79)),
        ranked=_ranked(_draft(first=0, last=79, start_ms=17, end_ms=40_017)),
    )

    combined = aggregate_highlight_metrics((perfect, broken))

    assert combined.candidate_count == 4
    assert combined.timestamp_validity == 0.75
    assert combined.top_three_acceptance == 0.5


@pytest.mark.unit
def test_writes_provider_model_and_prompt_versions_into_the_report() -> None:
    """A report nobody can attribute to a provider version proves nothing later."""
    metrics = evaluate_highlight_case(
        case=_case(_approved(first=0, last=79)), ranked=_ranked(_draft(first=0, last=79))
    )

    report = build_highlight_report(
        manifest_version="highlight-eval-cases/1",
        adapter="fake",
        provider="deterministic",
        model="deterministic",
        prompt_version="deterministic/1",
        schema_version="clip-candidate/1",
        cases=(("en-fixture", "en", metrics),),
    )
    document = report.to_json()

    assert document["reportVersion"] == HIGHLIGHT_REPORT_VERSION
    assert document["manifestVersion"] == "highlight-eval-cases/1"
    assert document["adapter"] == "fake"
    assert document["provider"] == "deterministic"
    assert document["model"] == "deterministic"
    assert document["promptVersion"] == "deterministic/1"
    assert document["schemaVersion"] == "clip-candidate/1"
    assert document["passed"] is True
    assert json.dumps(document, sort_keys=True) == json.dumps(report.to_json(), sort_keys=True)


@pytest.mark.unit
def test_marks_a_report_failed_when_any_gate_is_violated() -> None:
    """The report, not the reader, decides whether an evaluation run passed."""
    metrics = evaluate_highlight_case(
        case=_case(_approved(first=0, last=79)), ranked=_ranked(_draft(first=200, last=279))
    )

    report = build_highlight_report(
        manifest_version="highlight-eval-cases/1",
        adapter="fake",
        provider="deterministic",
        model="deterministic",
        prompt_version="deterministic/1",
        schema_version="clip-candidate/1",
        cases=(("en-fixture", "en", metrics),),
    )

    assert report.passed is False
    assert GATE_TOP_THREE_ACCEPTANCE in report.gate_violations


@pytest.mark.unit
def test_refuses_a_highlight_case_file_whose_digest_does_not_match_the_manifest(
    tmp_path: Path,
) -> None:
    """A fixture edited without the manifest would silently move the quality baseline."""
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "en-fixture.json").write_text('{"case_id": "en-fixture"}', encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": "highlight-eval-cases/1",
                "cases": [
                    {"case_id": "en-fixture", "path": "cases/en-fixture.json", "sha256": "0" * 64}
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(EvaluationFixtureError):
        load_highlight_cases(tmp_path)


@pytest.mark.unit
def test_loads_the_checked_in_highlight_cases_with_labels_in_three_languages() -> None:
    """The checked-in set is the baseline, so its shape is a contract of its own."""
    manifest_version, cases = load_highlight_cases(_evals_root() / "highlights")

    languages = [case.language for case in cases]

    assert manifest_version == "highlight-eval-cases/1"
    assert languages.count("en") >= 5
    assert languages.count("id") >= 5
    assert languages.count("mixed") >= 5
    assert all(case.approved for case in cases)
    assert any(clip.risky for case in cases for clip in case.approved)


@pytest.mark.unit
def test_aggregates_an_empty_set_of_cases_without_dividing_by_zero() -> None:
    """A run that loaded no case must report zeroes rather than crash the reporter."""
    combined = aggregate_highlight_metrics(())

    assert combined.case_count == 0
    assert combined.candidate_count == 0
    assert combined.timestamp_validity == 0.0
    assert combined.top_three_acceptance == 0.0


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param(lambda manifest: manifest.pop("manifest_version"), id="no-version"),
        pytest.param(lambda manifest: manifest.update(cases=[]), id="no-cases"),
        pytest.param(
            lambda manifest: manifest.update(cases=["cases/en.json"]), id="entry-not-object"
        ),
    ],
)
@pytest.mark.unit
def test_refuses_a_highlight_manifest_that_breaks_the_fixture_contract(
    tmp_path: Path, damage: Callable[[dict[str, Any]], None]
) -> None:
    """A manifest the loader cannot trust is a baseline nobody can compare against."""
    _write_highlight_fixture(tmp_path, _highlight_document(), damage_manifest=damage)

    with pytest.raises(EvaluationFixtureError):
        load_highlight_cases(tmp_path)


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param(lambda document: document.update(words=[]), id="no-words"),
        pytest.param(lambda document: document.update(approved_clips=[]), id="no-approved-clips"),
        pytest.param(lambda document: document.update(words="all of them"), id="words-not-a-list"),
        pytest.param(lambda document: document.update(words=["w000000"]), id="word-not-an-object"),
        pytest.param(
            lambda document: document.update(named_entities=[7]), id="entity-not-a-string"
        ),
        pytest.param(lambda document: document.update(case_id=""), id="case-id-empty"),
        pytest.param(
            lambda document: document.update(source_seconds="200"), id="seconds-not-an-int"
        ),
        pytest.param(
            lambda document: document["words"][0].update(punctuation=1), id="punctuation-not-text"
        ),
        pytest.param(
            lambda document: document["words"][0].update(confidence="high"),
            id="confidence-not-a-number",
        ),
        pytest.param(
            lambda document: document["approved_clips"][0].update(start_word_id="w999999"),
            id="clip-outside-the-transcript",
        ),
        pytest.param(
            lambda document: document["approved_clips"][0].update(risky="yes"),
            id="risky-not-a-boolean",
        ),
    ],
)
@pytest.mark.unit
def test_refuses_a_highlight_case_document_that_breaks_the_fixture_contract(
    tmp_path: Path, damage: Callable[[dict[str, Any]], None]
) -> None:
    """A case the loader half-understands would move the baseline without saying so."""
    document = _highlight_document()
    damage(document)
    _write_highlight_fixture(tmp_path, document)

    with pytest.raises(EvaluationFixtureError):
        load_highlight_cases(tmp_path)


def _highlight_document() -> dict[str, Any]:
    """Build the smallest highlight case document the loader accepts."""
    return {
        "case_id": "en-fixture",
        "language": "en",
        "source_seconds": 2,
        "named_entities": ["Jakarta"],
        "words": [
            {
                "word_id": f"w{index:06d}",
                "text": text,
                "punctuation": "",
                "start_ms": index * _WORD_MS,
                "end_ms": index * _WORD_MS + _WORD_MS,
                "confidence": 0.9,
                "speaker": "A",
            }
            for index, text in enumerate(("we", "opened", "Jakarta"))
        ],
        "approved_clips": [
            {
                "label": "approved-0",
                "start_word_id": "w000000",
                "end_word_id": "w000002",
                "min_overlap": 0.5,
                "max_overlap": 1.0,
                "risky": False,
            }
        ],
    }


def _write_highlight_fixture(
    directory: Path,
    document: dict[str, Any],
    *,
    damage_manifest: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Write one case file and the manifest that vouches for its bytes."""
    cases = directory / "cases"
    cases.mkdir(exist_ok=True)
    payload = json.dumps(document).encode("utf-8")
    (cases / "en.json").write_bytes(payload)
    manifest = {
        "manifest_version": "highlight-eval-cases/1",
        "cases": [
            {
                "case_id": "en-fixture",
                "path": "cases/en.json",
                "sha256": sha256(payload).hexdigest(),
            }
        ],
    }
    if damage_manifest is not None:
        damage_manifest(manifest)
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _evals_root() -> Path:
    """Locate the checked-in evaluation fixtures from this test file."""
    return Path(__file__).resolve().parents[2] / "evals"


def _reference(*, texts: tuple[str, ...], speakers: tuple[str, ...]) -> TranscriptionCase:
    """Build one labeled transcription case on the same half-second grid."""
    words = tuple(
        ReferenceWord(
            text=text,
            start_ms=index * _WORD_MS,
            end_ms=index * _WORD_MS + _WORD_MS,
            speaker=speakers[index],
        )
        for index, text in enumerate(texts)
    )
    return TranscriptionCase(
        case_id="en-fixture",
        language="en",
        duration_ms=len(texts) * _WORD_MS,
        words=words,
        named_entities=("Jakarta",),
    )


def _hypothesis(
    *, texts: tuple[str, ...], speakers: tuple[str, ...], drift_ms: int = 0
) -> TranscriptResult:
    """Build one provider transcript that may disagree with the reference."""
    words = tuple(
        TranscriptWord(
            word_id=f"h{index:06d}",
            text=text,
            punctuation="",
            start_ms=index * _WORD_MS + drift_ms,
            end_ms=index * _WORD_MS + _WORD_MS + drift_ms,
            confidence=0.9,
            speaker=speakers[index],
        )
        for index, text in enumerate(texts)
    )
    return _transcript(words)


_TEXTS = ("we", "opened", "an", "office", "in", "Jakarta", "last", "year")
_SPEAKERS = ("A", "A", "A", "A", "B", "B", "B", "B")


@pytest.mark.unit
def test_scores_a_perfect_transcription_at_zero_error() -> None:
    """A provider that matches the labels exactly must show no error anywhere."""
    case = _reference(texts=_TEXTS, speakers=_SPEAKERS)
    observation = TranscriptionObservation(
        case_id=case.case_id,
        transcript=_hypothesis(texts=_TEXTS, speakers=_SPEAKERS),
        error_code=None,
        latency_ms=4_000,
        cost_micros=1_000,
    )

    accuracy = evaluate_transcription_case(case=case, observation=observation)

    assert accuracy.word_error_rate == 0.0
    assert accuracy.named_entity_accuracy == 1.0
    assert accuracy.median_timestamp_drift_ms == 0
    assert accuracy.p95_timestamp_drift_ms == 0
    assert accuracy.diarization_error_rate == 0.0


@pytest.mark.unit
def test_counts_substituted_deleted_and_inserted_words_in_the_error_rate() -> None:
    """Word error rate must count all three edits, not only the words that changed."""
    case = _reference(texts=_TEXTS, speakers=_SPEAKERS)
    spoken = ("we", "opened", "an", "office", "in", "Djakarta", "last", "year", "again")
    observation = TranscriptionObservation(
        case_id=case.case_id,
        transcript=_hypothesis(texts=spoken, speakers=(*_SPEAKERS, "B")),
        error_code=None,
        latency_ms=4_000,
        cost_micros=1_000,
    )

    accuracy = evaluate_transcription_case(case=case, observation=observation)

    assert accuracy.word_error_rate == 0.25
    assert accuracy.named_entity_accuracy == 0.0


@pytest.mark.unit
def test_reports_median_and_p95_word_timestamp_drift_over_matched_words() -> None:
    """A transcript with the right words at the wrong time cuts the wrong audio."""
    case = _reference(texts=_TEXTS, speakers=_SPEAKERS)
    observation = TranscriptionObservation(
        case_id=case.case_id,
        transcript=_hypothesis(texts=_TEXTS, speakers=_SPEAKERS, drift_ms=40),
        error_code=None,
        latency_ms=4_000,
        cost_micros=1_000,
    )

    accuracy = evaluate_transcription_case(case=case, observation=observation)

    assert accuracy.median_timestamp_drift_ms == 40
    assert accuracy.p95_timestamp_drift_ms == 40


@pytest.mark.unit
def test_scores_a_consistently_renamed_speaker_as_no_diarization_error() -> None:
    """Provider speaker labels are opaque, so only the partition they imply is judged."""
    case = _reference(texts=_TEXTS, speakers=_SPEAKERS)
    observation = TranscriptionObservation(
        case_id=case.case_id,
        transcript=_hypothesis(texts=_TEXTS, speakers=("C", "C", "C", "C", "D", "D", "D", "D")),
        error_code=None,
        latency_ms=4_000,
        cost_micros=1_000,
    )

    accuracy = evaluate_transcription_case(case=case, observation=observation)

    assert accuracy.diarization_error_rate == 0.0


@pytest.mark.unit
def test_counts_words_attributed_to_the_wrong_speaker() -> None:
    """A misattributed quote is a publishing risk, so the partition must be scored."""
    case = _reference(texts=_TEXTS, speakers=_SPEAKERS)
    observation = TranscriptionObservation(
        case_id=case.case_id,
        transcript=_hypothesis(texts=_TEXTS, speakers=("A", "A", "A", "A", "A", "A", "B", "B")),
        error_code=None,
        latency_ms=4_000,
        cost_micros=1_000,
    )

    accuracy = evaluate_transcription_case(case=case, observation=observation)

    assert accuracy.diarization_error_rate == 0.25


@pytest.mark.unit
def test_reports_failure_rate_completion_time_and_cost_per_source_hour() -> None:
    """Quality is only half a provider choice; speed, reliability, and price are the rest."""
    case = _reference(texts=_TEXTS, speakers=_SPEAKERS)
    observations = (
        TranscriptionObservation(
            case_id=case.case_id,
            transcript=_hypothesis(texts=_TEXTS, speakers=_SPEAKERS),
            error_code=None,
            latency_ms=4_000,
            cost_micros=1_000,
        ),
        TranscriptionObservation(
            case_id=case.case_id,
            transcript=None,
            error_code="TRANSCRIPTION_PROVIDER_UNAVAILABLE",
            latency_ms=30_000,
            cost_micros=0,
        ),
    )

    metrics = aggregate_transcription_metrics(cases=(case, case), observations=observations)

    assert metrics.failure_rate == 0.5
    assert metrics.p95_completion_ms == 30_000
    assert metrics.cost_per_source_hour_micros == pytest.approx(450_000.0)


@pytest.mark.unit
def test_counts_a_failed_observation_as_total_error_rather_than_a_perfect_score() -> None:
    """A provider outage must not be laundered into a perfect accuracy score."""
    case = _reference(texts=_TEXTS, speakers=_SPEAKERS)
    observations = (
        TranscriptionObservation(
            case_id=case.case_id,
            transcript=None,
            error_code="TRANSCRIPTION_PROVIDER_UNAVAILABLE",
            latency_ms=1_000,
            cost_micros=0,
        ),
    )

    metrics = aggregate_transcription_metrics(cases=(case,), observations=observations)

    assert metrics.failure_rate == 1.0
    assert metrics.word_error_rate == 1.0
    assert metrics.named_entity_accuracy == 0.0


@pytest.mark.unit
def test_writes_provider_and_model_versions_into_the_transcription_report() -> None:
    """A transcription comparison is only usable when every run names its provider build."""
    case = _reference(texts=_TEXTS, speakers=_SPEAKERS)
    observation = TranscriptionObservation(
        case_id=case.case_id,
        transcript=_hypothesis(texts=_TEXTS, speakers=_SPEAKERS),
        error_code=None,
        latency_ms=4_000,
        cost_micros=1_000,
    )

    report = build_transcription_report(
        manifest_version="transcription-eval-cases/1",
        adapter="fake",
        provider="fixture",
        provider_version="1",
        model="fixture",
        cases=(case,),
        observations=(observation,),
    )
    document = report.to_json()

    assert document["reportVersion"] == TRANSCRIPTION_REPORT_VERSION
    assert document["manifestVersion"] == "transcription-eval-cases/1"
    assert document["adapter"] == "fake"
    assert document["provider"] == "fixture"
    assert document["providerVersion"] == "1"
    assert document["model"] == "fixture"
    assert document["passed"] is True
    assert document["cases"][0]["caseId"] == case.case_id


@pytest.mark.unit
def test_marks_a_transcription_report_failed_when_any_case_failed() -> None:
    """A run that could not transcribe a case has not measured that case."""
    case = _reference(texts=_TEXTS, speakers=_SPEAKERS)
    observation = TranscriptionObservation(
        case_id=case.case_id,
        transcript=None,
        error_code="TRANSCRIPTION_PROVIDER_UNAVAILABLE",
        latency_ms=1_000,
        cost_micros=0,
    )

    report = build_transcription_report(
        manifest_version="transcription-eval-cases/1",
        adapter="fake",
        provider="fixture",
        provider_version="1",
        model="fixture",
        cases=(case,),
        observations=(observation,),
    )

    assert report.passed is False
    assert report.failed_cases == (case.case_id,)


@pytest.mark.unit
def test_refuses_a_transcription_case_file_whose_digest_does_not_match_the_manifest(
    tmp_path: Path,
) -> None:
    """The transcription baseline is as tamper-evident as the highlight baseline."""
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "en-fixture.json").write_text('{"case_id": "en-fixture"}', encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": "transcription-eval-cases/1",
                "cases": [
                    {"case_id": "en-fixture", "path": "cases/en-fixture.json", "sha256": "0" * 64}
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(EvaluationFixtureError):
        load_transcription_cases(tmp_path)


@pytest.mark.unit
def test_loads_the_checked_in_transcription_cases_with_labels_in_three_languages() -> None:
    """Indonesian and code-switched speech is the product's hard case, so it is labeled."""
    manifest_version, cases = load_transcription_cases(_evals_root() / "transcription")

    languages = [case.language for case in cases]

    assert manifest_version == "transcription-eval-cases/1"
    assert languages.count("en") >= 5
    assert languages.count("id") >= 5
    assert languages.count("mixed") >= 5
    assert all(case.named_entities for case in cases)
    assert all(len({word.speaker for word in case.words}) >= 2 for case in cases)


@pytest.mark.unit
def test_counts_trailing_words_a_provider_never_returned_as_deletions() -> None:
    """A transcript that stops early loses the end of the episode, so it must score as loss."""
    case = _reference(texts=_TEXTS, speakers=_SPEAKERS)
    observation = TranscriptionObservation(
        case_id=case.case_id,
        transcript=_hypothesis(texts=_TEXTS[:5], speakers=_SPEAKERS[:5]),
        error_code=None,
        latency_ms=2_000,
        cost_micros=500,
    )

    accuracy = evaluate_transcription_case(case=case, observation=observation)

    assert accuracy.word_error_rate == 0.375
    assert accuracy.named_entity_accuracy == 0.0
    assert accuracy.diarization_error_rate == 0.0


@pytest.mark.unit
def test_scores_full_entity_accuracy_when_a_case_labels_no_entity() -> None:
    """A case with nothing to preserve must not be scored as having lost everything."""
    case = TranscriptionCase(
        case_id="en-fixture",
        language="en",
        duration_ms=len(_TEXTS) * _WORD_MS,
        words=_reference(texts=_TEXTS, speakers=_SPEAKERS).words,
        named_entities=(),
    )
    observation = TranscriptionObservation(
        case_id=case.case_id,
        transcript=_hypothesis(texts=_TEXTS, speakers=_SPEAKERS),
        error_code=None,
        latency_ms=2_000,
        cost_micros=500,
    )

    accuracy = evaluate_transcription_case(case=case, observation=observation)

    assert accuracy.named_entity_accuracy == 1.0


@pytest.mark.unit
def test_counts_an_entity_with_no_comparable_word_as_never_preserved() -> None:
    """An entity made only of punctuation cannot be found, and must not be credited as found."""
    case = TranscriptionCase(
        case_id="en-fixture",
        language="en",
        duration_ms=len(_TEXTS) * _WORD_MS,
        words=_reference(texts=_TEXTS, speakers=_SPEAKERS).words,
        named_entities=("--",),
    )
    observation = TranscriptionObservation(
        case_id=case.case_id,
        transcript=_hypothesis(texts=_TEXTS, speakers=_SPEAKERS),
        error_code=None,
        latency_ms=2_000,
        cost_micros=500,
    )

    accuracy = evaluate_transcription_case(case=case, observation=observation)

    assert accuracy.named_entity_accuracy == 0.0


@pytest.mark.unit
def test_scores_a_case_with_no_matched_word_as_total_diarization_error() -> None:
    """Speakers cannot be judged right when not one labeled word came back."""
    case = _reference(texts=_TEXTS, speakers=_SPEAKERS)
    observation = TranscriptionObservation(
        case_id=case.case_id,
        transcript=_hypothesis(texts=("nothing", "matches"), speakers=("A", "A")),
        error_code=None,
        latency_ms=2_000,
        cost_micros=500,
    )

    accuracy = evaluate_transcription_case(case=case, observation=observation)

    assert accuracy.diarization_error_rate == 1.0
    assert accuracy.median_timestamp_drift_ms == 0


@pytest.mark.unit
def test_scores_a_case_with_no_labeled_word_without_dividing_by_zero() -> None:
    """The checked-in loader rejects an unlabeled case, so the metric only has to stay safe."""
    case = TranscriptionCase(
        case_id="en-fixture", language="en", duration_ms=0, words=(), named_entities=()
    )
    observation = TranscriptionObservation(
        case_id=case.case_id,
        transcript=_hypothesis(texts=_TEXTS, speakers=_SPEAKERS),
        error_code=None,
        latency_ms=2_000,
        cost_micros=500,
    )

    accuracy = evaluate_transcription_case(case=case, observation=observation)

    assert accuracy.word_error_rate == 0.0
    assert accuracy.diarization_error_rate == 1.0


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param(lambda manifest: manifest.pop("manifest_version"), id="no-version"),
        pytest.param(lambda manifest: manifest.update(cases=[]), id="no-cases"),
        pytest.param(
            lambda manifest: manifest["cases"][0].update(path="cases/missing.json"),
            id="case-file-missing",
        ),
    ],
)
@pytest.mark.unit
def test_refuses_a_transcription_manifest_that_breaks_the_fixture_contract(
    tmp_path: Path, damage: Callable[[dict[str, Any]], None]
) -> None:
    """The transcription baseline must refuse a manifest it cannot follow to real bytes."""
    _write_transcription_fixture(tmp_path, _transcription_document(), damage_manifest=damage)

    with pytest.raises(EvaluationFixtureError):
        load_transcription_cases(tmp_path)


@pytest.mark.unit
def test_refuses_a_transcription_fixture_directory_with_no_manifest(tmp_path: Path) -> None:
    """Without a manifest there is nothing vouching for any case file in the directory."""
    with pytest.raises(EvaluationFixtureError):
        load_transcription_cases(tmp_path)


@pytest.mark.unit
def test_refuses_a_transcription_manifest_that_is_not_an_object(tmp_path: Path) -> None:
    """A manifest holding a bare list has no version and no digests to check."""
    (tmp_path / "manifest.json").write_text("[]", encoding="utf-8")

    with pytest.raises(EvaluationFixtureError):
        load_transcription_cases(tmp_path)


@pytest.mark.unit
def test_refuses_a_transcription_case_file_that_is_not_valid_json(tmp_path: Path) -> None:
    """A truncated case file matches its own digest and still means nothing."""
    _write_transcription_fixture(tmp_path, _transcription_document(), payload=b"{not json")

    with pytest.raises(EvaluationFixtureError):
        load_transcription_cases(tmp_path)


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param(lambda document: document.update(reference_words=[]), id="no-words"),
        pytest.param(
            lambda document: document.update(reference_words="all of them"), id="words-not-a-list"
        ),
        pytest.param(
            lambda document: document.update(named_entities=[7]), id="entity-not-a-string"
        ),
        pytest.param(lambda document: document.update(case_id=""), id="case-id-empty"),
        pytest.param(
            lambda document: document.update(duration_ms="1500"), id="duration-not-an-int"
        ),
    ],
)
@pytest.mark.unit
def test_refuses_a_transcription_case_document_that_breaks_the_fixture_contract(
    tmp_path: Path, damage: Callable[[dict[str, Any]], None]
) -> None:
    """Labels the loader half-understands would quietly change what the provider is measured on."""
    document = _transcription_document()
    damage(document)
    _write_transcription_fixture(tmp_path, document)

    with pytest.raises(EvaluationFixtureError):
        load_transcription_cases(tmp_path)


def _transcription_document() -> dict[str, Any]:
    """Build the smallest transcription case document the loader accepts."""
    return {
        "case_id": "en-fixture",
        "language": "en",
        "duration_ms": len(_TEXTS) * _WORD_MS,
        "named_entities": ["Jakarta"],
        "reference_words": [
            {
                "text": text,
                "start_ms": index * _WORD_MS,
                "end_ms": index * _WORD_MS + _WORD_MS,
                "speaker": _SPEAKERS[index],
            }
            for index, text in enumerate(_TEXTS)
        ],
    }


def _write_transcription_fixture(
    directory: Path,
    document: dict[str, Any],
    *,
    payload: bytes | None = None,
    damage_manifest: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Write one case file and the manifest that vouches for its bytes."""
    cases = directory / "cases"
    cases.mkdir(exist_ok=True)
    written = json.dumps(document).encode("utf-8") if payload is None else payload
    (cases / "en.json").write_bytes(written)
    manifest = {
        "manifest_version": "transcription-eval-cases/1",
        "cases": [
            {
                "case_id": "en-fixture",
                "path": "cases/en.json",
                "sha256": sha256(written).hexdigest(),
            }
        ],
    }
    if damage_manifest is not None:
        damage_manifest(manifest)
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
