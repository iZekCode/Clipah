"""Contracts for provider-neutral diarized transcript normalization."""

from __future__ import annotations

from uuid import UUID

import pytest

from clipah.transcripts.models import RawUtterance, RawWord
from clipah.transcripts.use_cases import (
    TranscriptValidationError,
    normalize_transcript,
    raw_transcript_key,
    transcript_document,
)


def _normalize(
    *,
    words: tuple[RawWord, ...],
    utterances: tuple[RawUtterance, ...],
    language: str = "en",
    model: str = "universal-3-pro",
    duration_ms: int = 2_000,
):
    """Build one normalized result while keeping every behavior-specific input visible."""
    return normalize_transcript(
        provider="assemblyai",
        provider_version="1.0.0",
        model=model,
        language=language,
        duration_ms=duration_ms,
        words=words,
        utterances=utterances,
        raw_result={"id": "provider-id"},
    )


@pytest.mark.unit
def test_normalizes_english_words_with_deterministic_ids_and_punctuation() -> None:
    """Later candidate bounds depend on stable word IDs and lossless provider punctuation."""
    result = _normalize(
        words=(RawWord("Hello,", 0, 400, 0.99, "A"), RawWord("world!", 450, 900, 0.97, "A")),
        utterances=(RawUtterance("Hello, world!", 0, 900, "A"),),
    )

    assert [(word.word_id, word.text, word.punctuation) for word in result.words] == [
        ("w000001", "Hello", ","),
        ("w000002", "world", "!"),
    ]
    assert result.full_text == "Hello, world!"
    assert result.utterances[0].word_ids == ("w000001", "w000002")


@pytest.mark.unit
def test_normalizes_indonesian_without_changing_provider_text() -> None:
    """Indonesia-first routing must preserve the words returned by Universal-2."""
    result = _normalize(
        words=(RawWord("Apa", 0, 300, 0.96, "A"), RawWord("kabar?", 350, 700, 0.95, "A")),
        utterances=(RawUtterance("Apa kabar?", 0, 700, "A"),),
        language="id",
        model="universal-2",
    )

    assert result.language == "id"
    assert result.model == "universal-2"
    assert result.full_text == "Apa kabar?"


@pytest.mark.unit
def test_speaker_changes_create_maximal_contiguous_segments() -> None:
    """Speaker segments must be derived from word evidence rather than guessed identities."""
    result = _normalize(
        words=(
            RawWord("one", 0, 200, 0.9, "A"),
            RawWord("two", 220, 400, 0.9, "B"),
            RawWord("three", 420, 600, 0.9, "A"),
        ),
        utterances=(
            RawUtterance("one", 0, 200, "A"),
            RawUtterance("two", 220, 400, "B"),
            RawUtterance("three", 420, 600, "A"),
        ),
    )

    assert [
        (segment.segment_id, segment.speaker, segment.word_ids)
        for segment in result.speaker_segments
    ] == [
        ("s000001", "A", ("w000001",)),
        ("s000002", "B", ("w000002",)),
        ("s000003", "A", ("w000003",)),
    ]


@pytest.mark.unit
def test_missing_punctuation_and_overlapping_speakers_remain_authoritative() -> None:
    """Diarized overlap is valid and normalization must not invent sentence punctuation."""
    result = _normalize(
        words=(RawWord("one", 0, 600, 0.9, "A"), RawWord("two", 500, 900, 0.9, "B")),
        utterances=(RawUtterance("one", 0, 600, "A"), RawUtterance("two", 500, 900, "B")),
    )

    assert [word.punctuation for word in result.words] == ["", ""]
    assert result.full_text == "one two"
    assert result.words[1].start_ms == 500


@pytest.mark.unit
@pytest.mark.parametrize(
    ("words", "code"),
    [
        ((), "TRANSCRIPT_EMPTY"),
        ((RawWord("bad", 100, 99, 0.9, "A"),), "TRANSCRIPT_TIMESTAMP_INVALID"),
        (
            (RawWord("one", 500, 700, 0.9, "A"), RawWord("two", 400, 800, 0.9, "A")),
            "TRANSCRIPT_TIMESTAMP_REGRESSION",
        ),
        ((RawWord("late", 1_900, 2_100, 0.9, "A"),), "TRANSCRIPT_DURATION_EXCEEDED"),
        ((RawWord("", 0, 100, 0.9, "A"),), "TRANSCRIPT_WORD_INVALID"),
        ((RawWord("word", 0, 100, 1.1, "A"),), "TRANSCRIPT_CONFIDENCE_INVALID"),
        ((RawWord("word", 0, 100, 0.9, ""),), "TRANSCRIPT_SPEAKER_INVALID"),
    ],
)
def test_rejects_invalid_provider_word_sequences(words: tuple[RawWord, ...], code: str) -> None:
    """Malformed provider evidence must fail before it becomes authoritative timestamps."""
    with pytest.raises(TranscriptValidationError) as raised:
        _normalize(words=words, utterances=())

    assert raised.value.code == code
    assert str(raised.value) == code


@pytest.mark.unit
def test_rejects_words_without_exactly_one_matching_utterance() -> None:
    """Persisted utterances may not omit or claim another speaker's word."""
    with pytest.raises(TranscriptValidationError) as raised:
        _normalize(
            words=(RawWord("hello", 0, 100, 0.9, "A"),),
            utterances=(RawUtterance("hello", 0, 100, "B"),),
        )

    assert raised.value.code == "TRANSCRIPT_UTTERANCE_INVALID"


@pytest.mark.unit
def test_serializes_the_canonical_document_and_deterministic_private_key() -> None:
    """Storage and later analysis need an intentional stable JSON schema and tenant key."""
    result = _normalize(
        words=(RawWord("Hello!", 0, 100, 0.75, "A"),),
        utterances=(RawUtterance("Hello!", 0, 100, "A"),),
    )

    document = transcript_document(result)
    assert document["words"] == [
        {
            "word_id": "w000001",
            "text": "Hello",
            "punctuation": "!",
            "start_ms": 0,
            "end_ms": 100,
            "confidence": 0.75,
            "speaker": "A",
        }
    ]
    assert document["utterances"] == [
        {
            "utterance_id": "u000001",
            "text": "Hello!",
            "start_ms": 0,
            "end_ms": 100,
            "speaker": "A",
            "word_ids": ["w000001"],
        }
    ]
    assert raw_transcript_key(
        UUID("00000000-0000-0000-0000-000000000001"),
        UUID("00000000-0000-0000-0000-000000000002"),
        UUID("00000000-0000-0000-0000-000000000003"),
    ) == (
        "workspaces/00000000-0000-0000-0000-000000000001/"
        "projects/00000000-0000-0000-0000-000000000002/"
        "transcripts/00000000-0000-0000-0000-000000000003/assemblyai.json"
    )
