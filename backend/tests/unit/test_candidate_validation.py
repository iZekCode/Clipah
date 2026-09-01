"""Contracts for validating model-proposed clip candidates against authoritative words."""

from __future__ import annotations

from typing import Any

import pytest

from clipah.highlights.extractor import CandidateValidationError, validate_candidate
from clipah.highlights.models import DEFAULT_CANDIDATE_POLICY, CandidatePolicy, ClipCategory
from clipah.transcripts.models import TranscriptResult, TranscriptWord

SENTENCE = ("Growth", "stalled", "until", "we", "cut", "the", "onboarding", "form")


def _words(count: int, *, step_ms: int = 500) -> tuple[TranscriptWord, ...]:
    """Produce one word every ``step_ms`` so a candidate duration is exact by construction."""
    return tuple(
        TranscriptWord(
            word_id=f"w{index + 1:06d}",
            text=SENTENCE[index % len(SENTENCE)],
            punctuation="." if index % len(SENTENCE) == len(SENTENCE) - 1 else "",
            start_ms=index * step_ms,
            end_ms=index * step_ms + step_ms,
            confidence=0.9,
            speaker="A",
        )
        for index in range(count)
    )


def _transcript(words: tuple[TranscriptWord, ...]) -> TranscriptResult:
    """Wrap words as the only authority for candidate timestamps and excerpts."""
    return TranscriptResult(
        provider="assemblyai",
        provider_version="1.0.0",
        model="universal-3-pro",
        language="en",
        full_text=" ".join(word.text for word in words),
        words=words,
        speaker_segments=(),
        utterances=(),
        duration_ms=words[-1].end_ms if words else 0,
        raw_result={},
    )


def _excerpt(words: tuple[TranscriptWord, ...], start_index: int, end_index: int) -> str:
    """Render the authoritative excerpt a truthful model would have echoed back."""
    return " ".join(
        f"{word.text}{word.punctuation}" for word in words[start_index : end_index + 1]
    ).strip()


TRANSCRIPT = _transcript(_words(400))  # 200 seconds of speech.


def _payload(**overrides: Any) -> dict[str, Any]:
    """Build one schema-valid 30-second candidate, overriding only the field under test."""
    start_index, end_index = 0, 59
    payload: dict[str, Any] = {
        "hook": "The onboarding form was the growth ceiling",
        "payoff": "Removing it doubled activation",
        "reason": "A complete problem, decision, and result inside thirty seconds",
        "category": "insight",
        "tags": ["growth", "onboarding"],
        "start_word_id": TRANSCRIPT.words[start_index].word_id,
        "end_word_id": TRANSCRIPT.words[end_index].word_id,
        "transcript_excerpt": _excerpt(TRANSCRIPT.words, start_index, end_index),
        "context_dependencies": [],
        "context_warnings": [],
        "visual_opportunities": ["Show the shortened form"],
        "score_breakdown": {
            "hook": 0.8,
            "payoff": 0.7,
            "narrative_completeness": 0.9,
            "context_safety": 0.85,
            "platform_fit": 0.75,
            "transcript_confidence": 0.9,
            "visual_opportunity": 0.6,
        },
    }
    payload.update(overrides)
    return payload


def _expect_rejection(**overrides: Any) -> CandidateValidationError:
    """Validate a deliberately invalid candidate and return the raised failure."""
    with pytest.raises(CandidateValidationError) as raised:
        validate_candidate(_payload(**overrides), transcript=TRANSCRIPT)
    return raised.value


@pytest.mark.unit
def test_accepts_a_candidate_and_resolves_timestamps_from_the_transcript() -> None:
    """Timestamps come from the transcript, never from anything the model wrote."""
    draft = validate_candidate(_payload(), transcript=TRANSCRIPT)

    assert draft.start_word_id == "w000001"
    assert draft.end_word_id == "w000060"
    assert draft.start_ms == 0
    assert draft.end_ms == 30_000
    assert draft.duration_ms == 30_000
    assert draft.category is ClipCategory.INSIGHT


@pytest.mark.unit
def test_rejects_model_supplied_timestamps() -> None:
    """Only word IDs may set a clip boundary, so free-form provider timing is refused."""
    error = _expect_rejection(start_ms=999_000, end_ms=1_000_000)

    assert error.code == "CANDIDATE_SCHEMA_INVALID"


@pytest.mark.unit
def test_rejects_an_unknown_start_word_id() -> None:
    """A hallucinated word ID has no timestamp, so it can never become a clip."""
    assert _expect_rejection(start_word_id="w999999").code == "CANDIDATE_UNKNOWN_WORD_ID"


@pytest.mark.unit
def test_rejects_an_unknown_end_word_id() -> None:
    """Both ends of the range must resolve to the same authoritative transcript."""
    assert _expect_rejection(end_word_id="not-a-word-id").code == "CANDIDATE_UNKNOWN_WORD_ID"


@pytest.mark.unit
def test_rejects_a_reversed_range() -> None:
    """An end before its start would render as an empty or negative clip."""
    reversed_payload = _payload(
        start_word_id=TRANSCRIPT.words[59].word_id,
        end_word_id=TRANSCRIPT.words[0].word_id,
        transcript_excerpt=_excerpt(TRANSCRIPT.words, 0, 59),
    )
    with pytest.raises(CandidateValidationError) as raised:
        validate_candidate(reversed_payload, transcript=TRANSCRIPT)

    assert raised.value.code == "CANDIDATE_RANGE_REVERSED"


@pytest.mark.unit
def test_rejects_a_nineteen_second_candidate() -> None:
    """Below the twenty-second floor there is not enough room for a complete thought."""
    end_index = 37  # 19_000 ms.
    error = _expect_rejection(
        end_word_id=TRANSCRIPT.words[end_index].word_id,
        transcript_excerpt=_excerpt(TRANSCRIPT.words, 0, end_index),
    )

    assert error.code == "CANDIDATE_DURATION_OUT_OF_RANGE"


@pytest.mark.unit
def test_rejects_a_ninety_one_second_candidate() -> None:
    """Above the ninety-second ceiling the preset stops being a short-form clip."""
    end_index = 181  # 91_000 ms.
    error = _expect_rejection(
        end_word_id=TRANSCRIPT.words[end_index].word_id,
        transcript_excerpt=_excerpt(TRANSCRIPT.words, 0, end_index),
    )

    assert error.code == "CANDIDATE_DURATION_OUT_OF_RANGE"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("end_index", "expected_duration_ms"),
    [
        (39, DEFAULT_CANDIDATE_POLICY.min_duration_ms),
        (179, DEFAULT_CANDIDATE_POLICY.max_duration_ms),
    ],
)
def test_accepts_both_duration_boundaries(end_index: int, expected_duration_ms: int) -> None:
    """The twenty- and ninety-second limits are inclusive, so neither edge is lost."""
    draft = validate_candidate(
        _payload(
            end_word_id=TRANSCRIPT.words[end_index].word_id,
            transcript_excerpt=_excerpt(TRANSCRIPT.words, 0, end_index),
        ),
        transcript=TRANSCRIPT,
    )

    assert draft.duration_ms == expected_duration_ms


@pytest.mark.unit
def test_honours_an_explicit_duration_policy() -> None:
    """Duration presets are configuration, so a later preset needs no code change here."""
    policy = CandidatePolicy(min_duration_ms=10_000, max_duration_ms=15_000)
    end_index = 23  # 12_000 ms.

    draft = validate_candidate(
        _payload(
            end_word_id=TRANSCRIPT.words[end_index].word_id,
            transcript_excerpt=_excerpt(TRANSCRIPT.words, 0, end_index),
        ),
        transcript=TRANSCRIPT,
        policy=policy,
    )

    assert draft.duration_ms == 12_000


@pytest.mark.unit
def test_rejects_an_excerpt_that_does_not_match_the_authoritative_words() -> None:
    """An excerpt that drifts from the transcript would caption words nobody said."""
    error = _expect_rejection(transcript_excerpt="We removed the paywall entirely")

    assert error.code == "CANDIDATE_EXCERPT_MISMATCH"


@pytest.mark.unit
def test_rejects_an_excerpt_that_extends_beyond_the_word_range() -> None:
    """A longer excerpt means the model scored text the clip will not contain."""
    error = _expect_rejection(transcript_excerpt=_excerpt(TRANSCRIPT.words, 0, 70))

    assert error.code == "CANDIDATE_EXCERPT_MISMATCH"


@pytest.mark.unit
def test_stores_the_authoritative_excerpt_when_only_formatting_differs() -> None:
    """Providers reformat whitespace and casing, but stored text follows the transcript."""
    noisy = f"  {_excerpt(TRANSCRIPT.words, 0, 59).upper().replace('.', ' ')}  "

    draft = validate_candidate(_payload(transcript_excerpt=noisy), transcript=TRANSCRIPT)

    assert draft.transcript_excerpt == _excerpt(TRANSCRIPT.words, 0, 59)


@pytest.mark.unit
@pytest.mark.parametrize("score", [-0.1, 1.1])
def test_rejects_a_score_outside_the_unit_interval(score: float) -> None:
    """Scores outside 0-1 would silently dominate every later ranking comparison."""
    breakdown = dict(_payload()["score_breakdown"], hook=score)

    assert _expect_rejection(score_breakdown=breakdown).code == "CANDIDATE_SCHEMA_INVALID"


@pytest.mark.unit
def test_rejects_a_missing_score_dimension() -> None:
    """Every explainable dimension is required, so a partial breakdown is not usable."""
    breakdown = dict(_payload()["score_breakdown"])
    del breakdown["context_safety"]

    assert _expect_rejection(score_breakdown=breakdown).code == "CANDIDATE_SCHEMA_INVALID"


@pytest.mark.unit
def test_rejects_an_unknown_category() -> None:
    """Categories drive review filters, so an invented one cannot be stored."""
    assert _expect_rejection(category="viral-banger").code == "CANDIDATE_SCHEMA_INVALID"


@pytest.mark.unit
def test_rejects_a_missing_required_field() -> None:
    """A candidate without a hook cannot be reviewed, so it is refused at the boundary."""
    payload = _payload()
    del payload["hook"]

    with pytest.raises(CandidateValidationError) as raised:
        validate_candidate(payload, transcript=TRANSCRIPT)

    assert raised.value.code == "CANDIDATE_SCHEMA_INVALID"


@pytest.mark.unit
def test_rejects_an_unknown_field() -> None:
    """Strict schemas keep provider extras out of durable candidate state."""
    assert _expect_rejection(final_timestamps="00:10-00:40").code == "CANDIDATE_SCHEMA_INVALID"


@pytest.mark.unit
def test_preserves_context_warnings_verbatim() -> None:
    """Context warnings must survive validation; reranking may never discard them."""
    draft = validate_candidate(
        _payload(
            context_warnings=["missing attribution"],
            context_dependencies=["earlier definition of activation"],
        ),
        transcript=TRANSCRIPT,
    )

    assert draft.context_warnings == ("missing attribution",)
    assert draft.context_dependencies == ("earlier definition of activation",)


@pytest.mark.unit
def test_validation_failures_carry_no_provider_text() -> None:
    """Failure codes reach durable job state, so they must stay free of model output."""
    error = _expect_rejection(start_word_id="w999999")

    assert str(error) == "CANDIDATE_UNKNOWN_WORD_ID"
