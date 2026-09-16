"""Sentence spans offered to an extraction provider as its only choice of clip boundary.

A provider that names a span this module built cannot invent a word, reverse a range, or
propose a clip outside the duration preset: every offered pair was derived from the stored
words and checked here first. Resolution returns authoritative word IDs, never text.
"""

from __future__ import annotations

from dataclasses import dataclass

from clipah.highlights.extractor import CandidateValidationError
from clipah.highlights.models import (
    DEFAULT_CANDIDATE_POLICY,
    CandidatePolicy,
    TranscriptWindow,
)
from clipah.highlights.windowing import window_text
from clipah.transcripts.models import TranscriptWord

UNKNOWN_SPAN_LABEL = "CANDIDATE_UNKNOWN_SPAN_LABEL"
_TERMINAL = (".", "!", "?")
_CLOSERS = "\"')\u201d\u2019"
_PAUSE_MS = 800


@dataclass(frozen=True, slots=True)
class SentenceSpan:
    """One sentence a clip may start or end on, with the ends that keep it reviewable."""

    label: str
    start_word_id: str
    end_word_id: str
    start_ms: int
    end_ms: int
    text: str
    valid_ends: tuple[str, ...]


def sentence_spans(
    window: TranscriptWindow,
    *,
    policy: CandidatePolicy = DEFAULT_CANDIDATE_POLICY,
    pause_ms: int = _PAUSE_MS,
) -> tuple[SentenceSpan, ...]:
    """Partition one window into sentences and record every duration-valid end per start.

    Ends are computed here rather than asked for, so a provider never does the arithmetic
    that decides whether a clip satisfies the duration preset.
    """
    sentences = _sentences(window.words, pause_ms=pause_ms)
    spans = []
    for index, words in enumerate(sentences):
        valid_ends = tuple(
            f"S{other}"
            for other, later in enumerate(sentences)
            if later[-1].end_ms - words[0].start_ms >= policy.min_duration_ms
            and later[-1].end_ms - words[0].start_ms <= policy.max_duration_ms
            and other >= index
        )
        spans.append(
            SentenceSpan(
                label=f"S{index}",
                start_word_id=words[0].word_id,
                end_word_id=words[-1].word_id,
                start_ms=words[0].start_ms,
                end_ms=words[-1].end_ms,
                text=window_text(words),
                valid_ends=valid_ends,
            )
        )
    return tuple(spans)


def span_labels(spans: tuple[SentenceSpan, ...]) -> tuple[str, ...]:
    """Name every start-end pair a provider may choose, and no other."""
    return tuple(f"{span.label}-{end}" for span in spans for end in span.valid_ends)


def resolve_span(label: str, spans: tuple[SentenceSpan, ...]) -> tuple[str, str]:
    """Translate one offered ``Sx-Sy`` label into the authoritative words it covers."""
    by_label = {span.label: (position, span) for position, span in enumerate(spans)}
    start_label, separator, end_label = label.partition("-")
    if not separator or start_label not in by_label or end_label not in by_label:
        raise CandidateValidationError(UNKNOWN_SPAN_LABEL)
    start_position, start = by_label[start_label]
    end_position, end = by_label[end_label]
    if end_position < start_position:
        raise CandidateValidationError("CANDIDATE_RANGE_REVERSED")
    if end_label not in start.valid_ends:
        raise CandidateValidationError("CANDIDATE_DURATION_OUT_OF_RANGE")
    return start.start_word_id, end.end_word_id


def _sentences(
    words: tuple[TranscriptWord, ...], *, pause_ms: int
) -> list[tuple[TranscriptWord, ...]]:
    """Group offered words at the boundaries a listener would hear as a full stop."""
    sentences: list[tuple[TranscriptWord, ...]] = []
    start = 0
    for index, word in enumerate(words):
        following = words[index + 1] if index + 1 < len(words) else None
        if (
            following is None
            or word.punctuation.rstrip(_CLOSERS).endswith(_TERMINAL)
            or following.speaker != word.speaker
            or following.start_ms - word.end_ms >= pause_ms
        ):
            sentences.append(words[start : index + 1])
            start = index + 1
    return sentences
