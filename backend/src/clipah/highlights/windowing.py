"""Pure, deterministic transcript windowing.

Windows exist so a long transcript can be extracted in bounded prompts without losing a
moment that straddles a boundary. Every window is a contiguous slice of authoritative
words, so a window can never contain a word twice, and consecutive windows overlap.
"""

from __future__ import annotations

from clipah.highlights.models import (
    DEFAULT_WINDOWING_POLICY,
    TranscriptWindow,
    WindowingPolicy,
)
from clipah.transcripts.models import TranscriptResult, TranscriptWord

_SENTENCE_ENDINGS = frozenset({".", "!", "?"})

_SILENCE_PREFERENCE = 4
_SPEAKER_PREFERENCE = 2
_SENTENCE_PREFERENCE = 1


def build_windows(
    transcript: TranscriptResult,
    *,
    policy: WindowingPolicy = DEFAULT_WINDOWING_POLICY,
) -> list[TranscriptWindow]:
    """Split one transcript into overlapping windows aligned to natural speech boundaries."""
    words = transcript.words
    if len(words) < policy.min_words:
        return []

    windows: list[TranscriptWindow] = []
    start_index = 0
    while True:
        end_index = _window_end(words, start_index, policy)
        if end_index == len(words) - 1:
            start_index = _extended_start(start_index, end_index, policy)
            windows.append(_window(len(windows), words, start_index, end_index))
            return windows
        windows.append(_window(len(windows), words, start_index, end_index))
        start_index = _next_start(words, start_index, end_index, policy)


def _window_end(
    words: tuple[TranscriptWord, ...], start_index: int, policy: WindowingPolicy
) -> int:
    """Return the last word index of the window that opens at ``start_index``."""
    window_start_ms = words[start_index].start_ms
    latest_index = start_index
    while (
        latest_index + 1 < len(words)
        and words[latest_index + 1].end_ms - window_start_ms <= policy.target_max_ms
    ):
        latest_index += 1
    if latest_index == len(words) - 1:
        return latest_index
    return _preferred_boundary(words, start_index, latest_index, policy)


def _preferred_boundary(
    words: tuple[TranscriptWord, ...],
    start_index: int,
    latest_index: int,
    policy: WindowingPolicy,
) -> int:
    """Choose the most natural cut inside the target band, preferring the longest window."""
    window_start_ms = words[start_index].start_ms
    best_index = latest_index
    best_preference = 0
    for index in range(start_index, latest_index + 1):
        if words[index].end_ms - window_start_ms < policy.target_min_ms:
            continue
        preference = _boundary_preference(words, index, policy)
        if preference >= best_preference and preference > 0:
            best_index = index
            best_preference = preference
    return best_index


def _boundary_preference(
    words: tuple[TranscriptWord, ...], index: int, policy: WindowingPolicy
) -> int:
    """Score how safe it is to end a window immediately after ``index``."""
    following = words[index + 1]
    preference = 0
    if following.start_ms - words[index].end_ms >= policy.silence_gap_ms:
        preference += _SILENCE_PREFERENCE
    if following.speaker != words[index].speaker:
        preference += _SPEAKER_PREFERENCE
    if words[index].punctuation in _SENTENCE_ENDINGS:
        preference += _SENTENCE_PREFERENCE
    return preference


def _next_start(
    words: tuple[TranscriptWord, ...],
    start_index: int,
    end_index: int,
    policy: WindowingPolicy,
) -> int:
    """Open the next window inside the overlap without skipping or repeating progress."""
    overlap_start_ms = words[end_index].end_ms - policy.overlap_ms
    next_index = end_index + 1
    for index in range(start_index + 1, end_index + 1):
        if words[index].start_ms >= overlap_start_ms:
            next_index = index
            break
    return next_index


def _extended_start(start_index: int, end_index: int, policy: WindowingPolicy) -> int:
    """Grow a short final window backwards so no tail of speech is dropped."""
    if end_index - start_index + 1 >= policy.min_words:
        return start_index
    return max(0, end_index - policy.min_words + 1)


def _window(
    index: int, words: tuple[TranscriptWord, ...], start_index: int, end_index: int
) -> TranscriptWindow:
    """Materialize one window from a contiguous run of authoritative words."""
    included = words[start_index : end_index + 1]
    return TranscriptWindow(
        index=index,
        start_ms=included[0].start_ms,
        end_ms=included[-1].end_ms,
        word_ids=tuple(word.word_id for word in included),
        text=window_text(included),
    )


def window_text(words: tuple[TranscriptWord, ...]) -> str:
    """Render words exactly as the transcript recorded them, punctuation included."""
    return " ".join(f"{word.text}{word.punctuation}" for word in words)
