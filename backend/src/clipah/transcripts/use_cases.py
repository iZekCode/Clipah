"""Normalize provider evidence into one timestamp-safe canonical Transcript."""

from __future__ import annotations

import unicodedata
from uuid import UUID

from clipah.transcripts.models import (
    JsonValue,
    RawUtterance,
    RawWord,
    SpeakerSegment,
    TranscriptResult,
    TranscriptUtterance,
    TranscriptWord,
)


class TranscriptValidationError(Exception):
    """Reject provider evidence through one stable terminal-safe code."""

    def __init__(self, code: str) -> None:
        """Retain only the fixed code, never provider diagnostics."""
        self.code = code
        super().__init__(code)


def normalize_transcript(
    *,
    provider: str,
    provider_version: str,
    model: str,
    language: str,
    duration_ms: int,
    words: tuple[RawWord, ...],
    utterances: tuple[RawUtterance, ...],
    raw_result: dict[str, JsonValue],
) -> TranscriptResult:
    """Validate and assign stable identities to one provider result."""
    if not words:
        raise TranscriptValidationError("TRANSCRIPT_EMPTY")
    if duration_ms <= 0:
        raise TranscriptValidationError("TRANSCRIPT_DURATION_INVALID")

    normalized_words: list[TranscriptWord] = []
    previous_start = -1
    for index, word in enumerate(words, start=1):
        _validate_word(word, previous_start=previous_start, duration_ms=duration_ms)
        text, punctuation = _split_punctuation(word.text)
        if not text:
            raise TranscriptValidationError("TRANSCRIPT_WORD_INVALID")
        normalized_words.append(
            TranscriptWord(
                word_id=f"w{index:06d}",
                text=text,
                punctuation=punctuation,
                start_ms=word.start_ms,
                end_ms=word.end_ms,
                confidence=word.confidence,
                speaker=word.speaker,
            )
        )
        previous_start = word.start_ms

    normalized = tuple(normalized_words)
    normalized_utterances = _normalize_utterances(normalized, utterances)
    return TranscriptResult(
        provider=provider,
        provider_version=provider_version,
        model=model,
        language=language,
        full_text=" ".join(f"{word.text}{word.punctuation}" for word in normalized),
        words=normalized,
        speaker_segments=_speaker_segments(normalized),
        utterances=normalized_utterances,
        duration_ms=duration_ms,
        raw_result=raw_result,
    )


def _validate_word(word: RawWord, *, previous_start: int, duration_ms: int) -> None:
    """Enforce timestamp, confidence, text, and diarization invariants on one word."""
    if not word.text.strip():
        raise TranscriptValidationError("TRANSCRIPT_WORD_INVALID")
    if word.start_ms < 0 or word.end_ms < word.start_ms:
        raise TranscriptValidationError("TRANSCRIPT_TIMESTAMP_INVALID")
    if word.start_ms < previous_start:
        raise TranscriptValidationError("TRANSCRIPT_TIMESTAMP_REGRESSION")
    if word.end_ms > duration_ms:
        raise TranscriptValidationError("TRANSCRIPT_DURATION_EXCEEDED")
    if not 0 <= word.confidence <= 1:
        raise TranscriptValidationError("TRANSCRIPT_CONFIDENCE_INVALID")
    if not word.speaker.strip():
        raise TranscriptValidationError("TRANSCRIPT_SPEAKER_INVALID")


def _split_punctuation(token: str) -> tuple[str, str]:
    """Separate only trailing Unicode punctuation without guessing missing marks."""
    boundary = len(token)
    while boundary > 0 and unicodedata.category(token[boundary - 1]).startswith("P"):
        boundary -= 1
    return token[:boundary], token[boundary:]


def _normalize_utterances(
    words: tuple[TranscriptWord, ...], utterances: tuple[RawUtterance, ...]
) -> tuple[TranscriptUtterance, ...]:
    """Map each word to exactly one compatible provider utterance."""
    normalized: list[TranscriptUtterance] = []
    membership_counts = {word.word_id: 0 for word in words}
    for index, utterance in enumerate(utterances, start=1):
        if (
            not utterance.text.strip()
            or not utterance.speaker.strip()
            or utterance.start_ms < 0
            or utterance.end_ms < utterance.start_ms
        ):
            raise TranscriptValidationError("TRANSCRIPT_UTTERANCE_INVALID")
        members = tuple(
            word
            for word in words
            if word.speaker == utterance.speaker
            and word.start_ms >= utterance.start_ms
            and word.end_ms <= utterance.end_ms
        )
        if not members:
            raise TranscriptValidationError("TRANSCRIPT_UTTERANCE_INVALID")
        for word in members:
            membership_counts[word.word_id] += 1
        normalized.append(
            TranscriptUtterance(
                utterance_id=f"u{index:06d}",
                text=utterance.text,
                start_ms=utterance.start_ms,
                end_ms=utterance.end_ms,
                speaker=utterance.speaker,
                word_ids=tuple(word.word_id for word in members),
            )
        )
    if any(count != 1 for count in membership_counts.values()):
        raise TranscriptValidationError("TRANSCRIPT_UTTERANCE_INVALID")
    return tuple(normalized)


def _speaker_segments(words: tuple[TranscriptWord, ...]) -> tuple[SpeakerSegment, ...]:
    """Coalesce adjacent equal-speaker words into deterministic segments."""
    groups: list[list[TranscriptWord]] = []
    for word in words:
        if not groups or groups[-1][-1].speaker != word.speaker:
            groups.append([word])
        else:
            groups[-1].append(word)
    return tuple(
        SpeakerSegment(
            segment_id=f"s{index:06d}",
            speaker=group[0].speaker,
            start_ms=group[0].start_ms,
            end_ms=group[-1].end_ms,
            word_ids=tuple(word.word_id for word in group),
        )
        for index, group in enumerate(groups, start=1)
    )


def transcript_document(result: TranscriptResult) -> dict[str, JsonValue]:
    """Serialize the canonical transcript through an explicit durable JSON schema."""
    return {
        "provider": result.provider,
        "provider_version": result.provider_version,
        "model": result.model,
        "language": result.language,
        "full_text": result.full_text,
        "duration_ms": result.duration_ms,
        "words": [
            {
                "word_id": word.word_id,
                "text": word.text,
                "punctuation": word.punctuation,
                "start_ms": word.start_ms,
                "end_ms": word.end_ms,
                "confidence": word.confidence,
                "speaker": word.speaker,
            }
            for word in result.words
        ],
        "speaker_segments": [
            {
                "segment_id": segment.segment_id,
                "speaker": segment.speaker,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "word_ids": list(segment.word_ids),
            }
            for segment in result.speaker_segments
        ],
        "utterances": [
            {
                "utterance_id": utterance.utterance_id,
                "text": utterance.text,
                "start_ms": utterance.start_ms,
                "end_ms": utterance.end_ms,
                "speaker": utterance.speaker,
                "word_ids": list(utterance.word_ids),
            }
            for utterance in result.utterances
        ],
    }


def raw_transcript_key(workspace_id: UUID, project_id: UUID, asset_id: UUID) -> str:
    """Build the sole private raw-provider object identity for one source Asset."""
    return f"workspaces/{workspace_id}/projects/{project_id}/transcripts/{asset_id}/assemblyai.json"
