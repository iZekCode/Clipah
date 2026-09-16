"""Contracts for the sentence spans an extraction provider may choose between.

A provider that can only name a span this module built cannot invent a boundary, a
duration, or a word, so these rules are what keeps a clip's edges authoritative.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from test_candidate_validation import TRANSCRIPT

from clipah.highlights.extractor import CandidateValidationError
from clipah.highlights.sentences import resolve_span, sentence_spans, span_labels
from clipah.highlights.windowing import build_windows, single_window


def _spans():
    """Build the spans offered for the whole fixture transcript."""
    return sentence_spans(single_window(TRANSCRIPT))


def test_sentences_break_at_terminal_punctuation():
    """A clip may only start and end where a thought does."""
    spans = _spans()
    assert (spans[0].label, spans[0].start_word_id, spans[0].end_word_id) == (
        "S0",
        "w000001",
        "w000008",
    )
    assert sum(len(span.text.split()) for span in spans) == len(TRANSCRIPT.words)
    assert spans[-1].end_word_id == TRANSCRIPT.words[-1].word_id


@pytest.mark.parametrize("boundary", ["speaker", "pause"])
def test_sentences_break_at_speaker_changes_and_long_pauses(boundary):
    """Unpunctuated speech must still be divided where the audio divides it."""
    words = tuple(replace(word, punctuation="") for word in TRANSCRIPT.words[:25])
    if boundary == "speaker":
        words = tuple(
            replace(word, speaker="B" if index >= 5 else "A") for index, word in enumerate(words)
        )
    else:
        words = tuple(
            replace(
                word,
                start_ms=word.start_ms + (900 if index >= 5 else 0),
                end_ms=word.end_ms + (900 if index >= 5 else 0),
            )
            for index, word in enumerate(words)
        )
    spans = sentence_spans(single_window(replace(TRANSCRIPT, words=words)))
    assert spans[0].end_word_id == "w000005"
    assert spans[-1].end_word_id == "w000025"


def test_each_start_offers_only_ends_inside_the_duration_preset():
    """A model choosing an offered end can never propose a 19-second or 91-second clip."""
    spans = _spans()
    # Every fixture sentence lasts four seconds: S4 ends at 20 s, S21 at 88 s, S22 at 92 s.
    assert spans[0].valid_ends == tuple(f"S{index}" for index in range(4, 22))
    assert spans[-1].valid_ends == ()
    labels = span_labels(spans)
    assert "S0-S4" in labels and "S0-S3" not in labels and "S0-S22" not in labels
    assert len(labels) == sum(len(span.valid_ends) for span in spans)


def test_a_span_resolves_to_the_authoritative_words_it_covers():
    """Resolution must yield stored word IDs, never text the provider supplied."""
    assert resolve_span("S0-S7", _spans()) == ("w000001", "w000064")


@pytest.mark.parametrize(
    "label,code",
    [
        ("S0-S99", "CANDIDATE_UNKNOWN_SPAN_LABEL"),
        ("S99-S0", "CANDIDATE_UNKNOWN_SPAN_LABEL"),
        ("S0 to S7", "CANDIDATE_UNKNOWN_SPAN_LABEL"),
        ("", "CANDIDATE_UNKNOWN_SPAN_LABEL"),
        ("S7-S0", "CANDIDATE_RANGE_REVERSED"),
        ("S0-S2", "CANDIDATE_DURATION_OUT_OF_RANGE"),
        ("S0-S22", "CANDIDATE_DURATION_OUT_OF_RANGE"),
    ],
)
def test_an_unoffered_span_is_refused_with_a_stable_code(label, code):
    """A label the offer did not contain must never reach candidate validation."""
    with pytest.raises(CandidateValidationError, match=code):
        resolve_span(label, _spans())


def test_spans_are_confined_to_the_window_they_were_built_from():
    """Windowed analysis must not let one window's span name another window's words."""
    windows = build_windows(TRANSCRIPT)
    spans = sentence_spans(windows[0])
    offered = set(windows[0].word_ids)
    assert {span.start_word_id for span in spans} <= offered
    assert {span.end_word_id for span in spans} <= offered
