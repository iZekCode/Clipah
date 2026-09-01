"""Contracts for punctuation-, speaker-, and silence-aware transcript windowing."""

from __future__ import annotations

from itertools import pairwise

import pytest

from clipah.highlights.models import DEFAULT_WINDOWING_POLICY, WindowingPolicy
from clipah.highlights.windowing import build_windows
from clipah.transcripts.models import TranscriptResult, TranscriptWord


def _word(
    index: int,
    *,
    start_ms: int,
    end_ms: int,
    punctuation: str = "",
    speaker: str = "A",
    text: str = "word",
) -> TranscriptWord:
    """Build one authoritative word without hiding the timing under test."""
    return TranscriptWord(
        word_id=f"w{index:06d}",
        text=text,
        punctuation=punctuation,
        start_ms=start_ms,
        end_ms=end_ms,
        confidence=0.9,
        speaker=speaker,
    )


def _transcript(words: tuple[TranscriptWord, ...]) -> TranscriptResult:
    """Wrap words in a transcript so windowing reads only authoritative values."""
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


def _even_words(
    count: int, *, step_ms: int = 500, speaker: str = "A"
) -> tuple[TranscriptWord, ...]:
    """Produce evenly spaced speech so a test can isolate one boundary rule at a time."""
    return tuple(
        _word(
            index + 1, start_ms=index * step_ms, end_ms=index * step_ms + step_ms, speaker=speaker
        )
        for index in range(count)
    )


@pytest.mark.unit
def test_returns_no_windows_for_a_transcript_without_words() -> None:
    """An empty transcript must not reach an extraction provider at all."""
    assert build_windows(_transcript(())) == []


@pytest.mark.unit
def test_excludes_a_transcript_below_the_configured_word_count() -> None:
    """Too little speech cannot support a complete thought, so it is not analysed."""
    words = _even_words(DEFAULT_WINDOWING_POLICY.min_words - 1)

    assert build_windows(_transcript(words)) == []


@pytest.mark.unit
def test_returns_one_window_for_a_transcript_shorter_than_the_target() -> None:
    """A six-minute source and a ninety-second source must both produce usable windows."""
    words = _even_words(200)  # 100 seconds of speech.

    windows = build_windows(_transcript(words))

    assert len(windows) == 1
    assert windows[0].start_ms == 0
    assert windows[0].end_ms == words[-1].end_ms
    assert windows[0].word_ids == tuple(word.word_id for word in words)


@pytest.mark.unit
def test_keeps_every_non_final_window_within_the_target_duration_band() -> None:
    """Windows outside 120-180 seconds either waste context or lose narrative shape."""
    words = _even_words(2_000)  # 1_000 seconds of speech.
    policy = DEFAULT_WINDOWING_POLICY

    windows = build_windows(_transcript(words))

    assert len(windows) > 1
    for window in windows[:-1]:
        duration_ms = window.end_ms - window.start_ms
        assert policy.target_min_ms <= duration_ms <= policy.target_max_ms


@pytest.mark.unit
def test_overlaps_consecutive_windows_by_the_configured_amount() -> None:
    """Overlap is what stops a moment that straddles a boundary from being lost."""
    words = _even_words(2_000)
    policy = DEFAULT_WINDOWING_POLICY

    windows = build_windows(_transcript(words))

    for previous, following in pairwise(windows):
        overlap_ms = previous.end_ms - following.start_ms
        assert 0 < overlap_ms <= policy.overlap_ms
        assert set(previous.word_ids) & set(following.word_ids)


@pytest.mark.unit
def test_covers_every_word_without_repeating_one_inside_a_window() -> None:
    """A dropped word is an unreachable moment; a repeated one corrupts excerpt checks."""
    words = _even_words(2_000)

    windows = build_windows(_transcript(words))

    covered: list[str] = []
    for window in windows:
        assert len(set(window.word_ids)) == len(window.word_ids)
        covered.extend(window.word_ids)
    assert set(covered) == {word.word_id for word in words}


@pytest.mark.unit
def test_prefers_a_sentence_end_inside_the_target_band() -> None:
    """A window that stops mid-sentence gives the model an incomplete thought."""
    words = list(_even_words(2_000))
    sentence_end_index = 279  # Ends at 140 seconds, inside the 120-180 second band.
    words[sentence_end_index] = _word(
        sentence_end_index + 1,
        start_ms=words[sentence_end_index].start_ms,
        end_ms=words[sentence_end_index].end_ms,
        punctuation=".",
    )

    windows = build_windows(_transcript(tuple(words)))

    assert windows[0].word_ids[-1] == words[sentence_end_index].word_id
    assert windows[0].end_ms == words[sentence_end_index].end_ms


@pytest.mark.unit
def test_prefers_a_speaker_change_over_a_later_arbitrary_cut() -> None:
    """Cutting inside a speaker turn splits one voice across two windows."""
    words = [
        _word(
            index + 1,
            start_ms=index * 500,
            end_ms=index * 500 + 500,
            speaker="A" if index < 300 else "B",
        )
        for index in range(2_000)
    ]

    windows = build_windows(_transcript(tuple(words)))

    assert windows[0].word_ids[-1] == words[299].word_id


@pytest.mark.unit
def test_ends_a_window_at_a_silence_gap_inside_the_target_band() -> None:
    """A long silence is the safest cut available, so it outranks the other boundaries."""
    gap_index = 260  # Ends at 130 seconds, inside the 120-180 second band.
    words: list[TranscriptWord] = []
    offset_ms = 0
    for index in range(2_000):
        start_ms = index * 500 + offset_ms
        words.append(_word(index + 1, start_ms=start_ms, end_ms=start_ms + 500, punctuation="."))
        if index == gap_index:
            offset_ms += DEFAULT_WINDOWING_POLICY.silence_gap_ms

    windows = build_windows(_transcript(tuple(words)))

    assert windows[0].word_ids[-1] == words[gap_index].word_id


@pytest.mark.unit
def test_honours_an_explicit_policy_instead_of_the_default_band() -> None:
    """Window shape is configuration, so no caller has to edit windowing code to change it."""
    words = _even_words(2_000)
    policy = WindowingPolicy(
        target_min_ms=60_000,
        target_max_ms=90_000,
        overlap_ms=10_000,
        silence_gap_ms=1_500,
        min_words=10,
    )

    windows = build_windows(_transcript(words), policy=policy)

    for window in windows[:-1]:
        duration_ms = window.end_ms - window.start_ms
        assert policy.target_min_ms <= duration_ms <= policy.target_max_ms


@pytest.mark.unit
def test_produces_adjacent_windows_when_overlap_is_disabled() -> None:
    """With no overlap the next window must still start exactly where the last one ended."""
    words = _even_words(400)
    policy = WindowingPolicy(
        target_min_ms=60_000,
        target_max_ms=90_000,
        overlap_ms=0,
        silence_gap_ms=1_500,
        min_words=10,
    )

    windows = build_windows(_transcript(words), policy=policy)

    assert len(windows) > 1
    for previous, following in pairwise(windows):
        assert not set(previous.word_ids) & set(following.word_ids)
    covered = [word_id for window in windows for word_id in window.word_ids]
    assert covered == [word.word_id for word in words]


@pytest.mark.unit
def test_grows_a_short_final_window_instead_of_dropping_the_tail() -> None:
    """A thin tail window would be excluded by the word count, losing the last moments."""
    words = _even_words(12, step_ms=1_000)
    policy = WindowingPolicy(
        target_min_ms=4_000,
        target_max_ms=5_000,
        overlap_ms=1_000,
        silence_gap_ms=1_500,
        min_words=5,
    )

    windows = build_windows(_transcript(words), policy=policy)

    assert len(windows[-1].word_ids) >= policy.min_words
    assert windows[-1].word_ids[-1] == words[-1].word_id
    covered = {word_id for window in windows for word_id in window.word_ids}
    assert covered == {word.word_id for word in words}


@pytest.mark.unit
def test_numbers_windows_in_transcript_order() -> None:
    """A stable index lets an extraction failure name the window that failed."""
    words = _even_words(2_000)

    windows = build_windows(_transcript(words))

    assert [window.index for window in windows] == list(range(len(windows)))
    assert [window.start_ms for window in windows] == sorted(window.start_ms for window in windows)


@pytest.mark.unit
def test_window_text_reproduces_authoritative_words_and_punctuation() -> None:
    """The extraction prompt must show exactly the transcript the word IDs describe."""
    words = (
        _word(1, start_ms=0, end_ms=400, text="Hello", punctuation=","),
        _word(2, start_ms=400, end_ms=900, text="world", punctuation="!"),
    )
    policy = WindowingPolicy(
        target_min_ms=60_000,
        target_max_ms=90_000,
        overlap_ms=10_000,
        silence_gap_ms=1_500,
        min_words=2,
    )

    windows = build_windows(_transcript(words), policy=policy)

    assert windows[0].text == "Hello, world!"
