"""Immutable values shared across transcript providers and durable use cases."""

from __future__ import annotations

from dataclasses import dataclass

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class RawWord:
    """One provider word before Clipah assigns its canonical identity."""

    text: str
    start_ms: int
    end_ms: int
    confidence: float
    speaker: str


@dataclass(frozen=True, slots=True)
class RawUtterance:
    """One provider-declared uninterrupted speaker utterance."""

    text: str
    start_ms: int
    end_ms: int
    speaker: str


@dataclass(frozen=True, slots=True)
class TranscriptWord:
    """One canonical word used as the authoritative timestamp boundary."""

    word_id: str
    text: str
    punctuation: str
    start_ms: int
    end_ms: int
    confidence: float
    speaker: str


@dataclass(frozen=True, slots=True)
class TranscriptUtterance:
    """One provider speaker turn expressed through canonical word identities."""

    utterance_id: str
    text: str
    start_ms: int
    end_ms: int
    speaker: str
    word_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SpeakerSegment:
    """One maximal contiguous run of words carrying the same opaque speaker label."""

    segment_id: str
    speaker: str
    start_ms: int
    end_ms: int
    word_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TranscriptResult:
    """One complete normalized transcription result independent of its provider SDK."""

    provider: str
    provider_version: str
    model: str
    language: str
    full_text: str
    words: tuple[TranscriptWord, ...]
    speaker_segments: tuple[SpeakerSegment, ...]
    utterances: tuple[TranscriptUtterance, ...]
    duration_ms: int
    raw_result: dict[str, JsonValue]
