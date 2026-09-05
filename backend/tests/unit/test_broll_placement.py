"""Contracts for deterministic B-roll placement over accepted visual beats.

Placement is the half of B-roll planning that a language model is not allowed to decide.
The model proposes where a visual would help; this code decides the milliseconds, honours
the coverage density, refuses to cover a moment the viewer must see, and returns nothing
rather than failing when no beat earns a shot.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from clipah.broll.models import (
    DEFAULT_PLACEMENT_POLICY,
    BeatProtection,
    BrollCoverage,
    CandidateSpan,
    PlacementPolicy,
    VisualBeat,
    VisualIntent,
)
from clipah.broll.placement import place_suggestions, scene_boundaries
from clipah.transcripts.models import TranscriptWord

WORD_MS = 500
CANDIDATE_END_MS = 60_000
SENTENCE = ("We", "cut", "the", "signup", "form", "and", "activation", "doubled")


def _words(count: int = 120) -> tuple[TranscriptWord, ...]:
    """Lay one word every 500 ms so every beat boundary is exact by construction."""
    return tuple(
        TranscriptWord(
            word_id=f"w{index + 1:06d}",
            text=SENTENCE[index % len(SENTENCE)],
            punctuation="." if index % len(SENTENCE) == len(SENTENCE) - 1 else "",
            start_ms=index * WORD_MS,
            end_ms=index * WORD_MS + WORD_MS,
            confidence=0.9,
            speaker="A",
        )
        for index in range(count)
    )


WORDS = _words()
CANDIDATE = CandidateSpan(
    start_word_id=WORDS[0].word_id,
    end_word_id=WORDS[-1].word_id,
    start_ms=0,
    end_ms=CANDIDATE_END_MS,
)


def _intent(*, subject: str = "a shortened signup form", confidence: float = 0.8) -> VisualIntent:
    """Build one schema-valid intent, varying only what a test is about."""
    return VisualIntent(
        subject=subject,
        action="a hand deleting form fields",
        setting="a laptop screen on a desk",
        mood="focused",
        search_terms_id=("formulir pendaftaran", "layar laptop"),
        search_terms_en=("signup form", "laptop screen"),
        portrait_suitable=True,
        exclusions=("stock office handshake",),
        factual_risk_flags=(),
        confidence=confidence,
    )


def _beat(
    start_ms: int,
    end_ms: int,
    *,
    subject: str | None = None,
    confidence: float = 0.8,
    protection: BeatProtection | None = None,
) -> VisualBeat:
    """Place one beat on the word grid, naming a distinct subject unless a test shares one."""
    return VisualBeat(
        start_word_id=WORDS[start_ms // WORD_MS].word_id,
        end_word_id=WORDS[(end_ms // WORD_MS) - 1].word_id,
        start_ms=start_ms,
        end_ms=end_ms,
        intent=_intent(subject=subject or f"visual at {start_ms}", confidence=confidence),
        placement_reason="The sentence names a concrete object the viewer cannot see",
        protection=protection,
    )


def _evenly_spaced_beats() -> tuple[VisualBeat, ...]:
    """Offer one two-second beat every two seconds, so only density can thin them out."""
    return tuple(_beat(start, start + 2_000) for start in range(0, CANDIDATE_END_MS, 2_000))


def _place(
    beats: tuple[VisualBeat, ...],
    coverage: BrollCoverage = BrollCoverage.BALANCED,
    *,
    boundaries: tuple[int, ...] = (),
) -> tuple[object, ...]:
    """Run placement with the production policy so tests measure the shipped defaults."""
    return place_suggestions(
        beats=beats,
        candidate=CANDIDATE,
        coverage=coverage,
        boundaries=boundaries,
        policy=DEFAULT_PLACEMENT_POLICY,
    )


@pytest.mark.unit
def test_minimal_coverage_caps_suggestions_at_one_per_fifteen_seconds() -> None:
    """A member who asked for minimal coverage must not receive a busier cut than that."""
    placed = _place(_evenly_spaced_beats(), BrollCoverage.MINIMAL)

    starts = [suggestion.start_ms for suggestion in placed]
    assert starts == [4_000, 20_000, 36_000, 52_000]


@pytest.mark.unit
def test_balanced_coverage_caps_suggestions_at_one_per_eight_seconds() -> None:
    """Balanced is the default, so its density is the one most members will live with."""
    placed = _place(_evenly_spaced_beats(), BrollCoverage.BALANCED)

    starts = [suggestion.start_ms for suggestion in placed]
    assert starts == [4_000, 12_000, 20_000, 28_000, 36_000, 44_000, 52_000]


@pytest.mark.unit
def test_dynamic_coverage_caps_suggestions_at_one_per_five_seconds() -> None:
    """Dynamic is the busiest cut the product offers and still must respect its own floor."""
    placed = _place(_evenly_spaced_beats(), BrollCoverage.DYNAMIC)

    starts = [suggestion.start_ms for suggestion in placed]
    assert starts == [4_000, 10_000, 16_000, 22_000, 28_000, 34_000, 40_000, 46_000, 52_000, 58_000]
    assert all(second - first >= 5_000 for first, second in pairwise(starts)), (
        "consecutive suggestions must honour the dynamic spacing floor"
    )


@pytest.mark.unit
def test_a_short_beat_is_widened_to_the_two_second_minimum_shot() -> None:
    """A shot below two seconds reads as a glitch rather than as a deliberate cut."""
    placed = _place((_beat(10_000, 10_500),))

    assert [(item.start_ms, item.end_ms) for item in placed] == [(10_000, 12_000)]


@pytest.mark.unit
def test_a_long_beat_is_trimmed_to_the_five_second_maximum_shot() -> None:
    """B-roll supports the dialogue; it never takes the clip over for half a minute."""
    placed = _place((_beat(10_000, 40_000),))

    assert [(item.start_ms, item.end_ms) for item in placed] == [(10_000, 15_000)]


@pytest.mark.unit
def test_widening_a_short_beat_never_runs_past_the_candidate_end() -> None:
    """A suggestion outliving its own clip would be placed on media that does not exist."""
    inside = _place((_beat(57_500, 58_000),))
    assert [(item.start_ms, item.end_ms) for item in inside] == [(57_500, 59_500)]

    every_beat = _place(_evenly_spaced_beats(), BrollCoverage.DYNAMIC)
    assert every_beat
    assert all(item.end_ms <= CANDIDATE_END_MS for item in every_beat)


@pytest.mark.unit
def test_a_beat_too_close_to_the_candidate_end_for_a_full_shot_is_dropped() -> None:
    """Rather than emit a one-second flash, placement declines the beat entirely."""
    assert _place((_beat(59_000, 60_000),)) == ()


@pytest.mark.unit
def test_a_beat_outside_the_candidate_is_never_placed() -> None:
    """A beat the planner resolved outside these bounds cannot become a timeline item."""
    outside = _beat(10_000, 12_000)
    narrow = CandidateSpan(
        start_word_id=WORDS[40].word_id,
        end_word_id=WORDS[-1].word_id,
        start_ms=20_000,
        end_ms=CANDIDATE_END_MS,
    )

    assert (
        place_suggestions(
            beats=(outside,),
            candidate=narrow,
            coverage=BrollCoverage.BALANCED,
            boundaries=(),
            policy=DEFAULT_PLACEMENT_POLICY,
        )
        == ()
    )


@pytest.mark.unit
def test_a_shot_stops_at_a_scene_boundary_rather_than_straddling_it() -> None:
    """Cutting away across a scene change hides the change the viewer needs to see."""
    placed = _place((_beat(10_000, 15_000),), boundaries=(13_000,))

    assert [(item.start_ms, item.end_ms) for item in placed] == [(10_000, 13_000)]


@pytest.mark.unit
def test_a_scene_boundary_leaving_less_than_a_minimum_shot_drops_the_beat() -> None:
    """A boundary one second in leaves no room for an honest shot, so nothing is offered."""
    assert _place((_beat(10_000, 15_000),), boundaries=(11_000,)) == ()


@pytest.mark.unit
def test_the_opening_hook_is_never_covered() -> None:
    """The first seconds carry the face reveal that earns the viewer's attention."""
    placed = _place((_beat(0, 2_500), _beat(6_000, 9_000)))

    assert [item.start_ms for item in placed] == [6_000]


@pytest.mark.parametrize(
    "protection",
    [
        BeatProtection.FACE_REVEAL,
        BeatProtection.PUNCHLINE,
        BeatProtection.DEMONSTRATION,
        BeatProtection.EMOTIONAL_PAUSE,
        BeatProtection.CULTURALLY_SENSITIVE,
    ],
)
@pytest.mark.unit
def test_a_protected_beat_is_never_covered(protection: BeatProtection) -> None:
    """Every protection the planner can raise must stop a shot, not merely warn about it."""
    assert _place((_beat(20_000, 23_000, protection=protection),)) == ()


@pytest.mark.unit
def test_a_repeated_visual_intent_is_suggested_only_once() -> None:
    """The same picture twice in one clip reads as a mistake rather than as emphasis."""
    repeated = "a shortened signup form"
    placed = _place(
        (
            _beat(10_000, 12_000, subject=repeated),
            _beat(30_000, 32_000, subject=repeated),
            _beat(50_000, 52_000, subject="a rising activation chart"),
        )
    )

    assert [item.start_ms for item in placed] == [10_000, 50_000]


@pytest.mark.unit
def test_a_repeated_intent_is_matched_ignoring_case_and_spacing() -> None:
    """A model that recapitalizes its own subject has not proposed a different picture."""
    placed = _place(
        (
            _beat(10_000, 12_000, subject="A Shortened  Signup Form"),
            _beat(30_000, 32_000, subject="a shortened signup form"),
        )
    )

    assert [item.start_ms for item in placed] == [10_000]


@pytest.mark.unit
def test_insufficient_visual_opportunity_returns_no_suggestions_rather_than_failing() -> None:
    """A clip that does not want B-roll is a normal outcome, not an error to report."""
    assert _place((_beat(10_000, 12_000, confidence=0.2),)) == ()


@pytest.mark.unit
def test_no_beats_at_all_returns_no_suggestions() -> None:
    """A planner that proposed nothing must not be turned into a failure by placement."""
    assert _place(()) == ()


@pytest.mark.unit
def test_a_placed_suggestion_keeps_its_beat_evidence() -> None:
    """A reviewer must be able to read why a shot was proposed where it was."""
    beat = _beat(10_000, 12_000)

    placed = _place((beat,))

    assert len(placed) == 1
    assert placed[0].beat == beat
    assert placed[0].placement_reason == beat.placement_reason


@pytest.mark.unit
def test_placement_is_identical_across_repeated_runs() -> None:
    """A replayed planning Job must converge on the suggestions already stored."""
    beats = _evenly_spaced_beats()

    assert _place(beats) == _place(beats)


@pytest.mark.unit
def test_scene_boundaries_are_derived_from_silence_gaps() -> None:
    """A pause long enough to hear is the closest thing the transcript has to a cut."""
    words = (
        TranscriptWord(
            word_id="w000001",
            text="We",
            punctuation="",
            start_ms=0,
            end_ms=500,
            confidence=0.9,
            speaker="A",
        ),
        TranscriptWord(
            word_id="w000002",
            text="cut",
            punctuation="",
            start_ms=2_000,
            end_ms=2_500,
            confidence=0.9,
            speaker="A",
        ),
    )

    assert scene_boundaries(words, silence_gap_ms=1_200) == (2_000,)


@pytest.mark.unit
def test_scene_boundaries_are_derived_from_speaker_changes() -> None:
    """A new speaker is a visual change even when nobody paused to make one."""
    words = (
        TranscriptWord(
            word_id="w000001",
            text="We",
            punctuation="",
            start_ms=0,
            end_ms=500,
            confidence=0.9,
            speaker="A",
        ),
        TranscriptWord(
            word_id="w000002",
            text="cut",
            punctuation="",
            start_ms=500,
            end_ms=1_000,
            confidence=0.9,
            speaker="B",
        ),
    )

    assert scene_boundaries(words, silence_gap_ms=1_200) == (500,)


@pytest.mark.unit
def test_uninterrupted_speech_by_one_speaker_has_no_scene_boundary() -> None:
    """Inventing a cut where none exists would truncate honest shots for no reason."""
    assert scene_boundaries(WORDS[:10], silence_gap_ms=1_200) == ()


@pytest.mark.unit
def test_two_shots_never_overlap_even_where_the_configured_shot_outruns_the_spacing() -> None:
    """Shot length and coverage spacing are configured apart, so they can disagree."""
    generous = PlacementPolicy(
        min_shot_ms=2_000,
        max_shot_ms=12_000,
        hook_guard_ms=3_000,
        min_confidence=0.5,
        silence_gap_ms=1_200,
    )

    placed = place_suggestions(
        beats=(_beat(10_000, 22_000), _beat(20_000, 24_000)),
        candidate=CANDIDATE,
        coverage=BrollCoverage.BALANCED,
        boundaries=(),
        policy=generous,
    )

    assert [(item.start_ms, item.end_ms) for item in placed] == [(10_000, 22_000)]
