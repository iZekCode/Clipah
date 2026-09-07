"""Contracts for hook and duration variants, and the packaging that travels with them.

A Variant is a promise that this narrower cut still says what the speaker said. The tests
here are mostly about what the generator refuses: a target it cannot hit honestly, a
boundary that severs a payoff, a duration nobody asked for.
"""

from __future__ import annotations

import pytest

from clipah.transcripts.models import TranscriptWord
from clipah.variants.generator import generate_variants
from clipah.variants.models import (
    SUPPORTED_TARGET_DURATIONS_MS,
    ContextWarningSeverity,
    HookStrategy,
    Platform,
)
from clipah.variants.packaging import packaging_for

#: One second per word makes every expected duration readable in the fixtures below.
WORD_MS = 1_000


def _words(text: str) -> tuple[TranscriptWord, ...]:
    """Build a transcript from plain speech, one second per word."""
    words: list[TranscriptWord] = []
    for index, token in enumerate(text.split(), start=1):
        stripped = token.rstrip(".,!?;:")
        words.append(
            TranscriptWord(
                word_id=f"w{index:06d}",
                text=stripped,
                punctuation=token[len(stripped) :],
                start_ms=(index - 1) * WORD_MS,
                end_ms=index * WORD_MS,
                confidence=0.9,
                speaker="A",
            )
        )
    return tuple(words)


def _speech(sentences: int, words_each: int = 10) -> str:
    """Compose plain declarative speech of a known length, one sentence at a time."""
    return " ".join(
        " ".join(["word"] * (words_each - 1)) + f" end{index}." for index in range(sentences)
    )


def _generate(**overrides: object) -> tuple[object, ...]:
    """Generate variants over a long, safe candidate unless a test says otherwise."""
    speech = str(overrides.pop("speech", _speech(12)))
    words = _words(speech)
    values: dict[str, object] = {
        "words": words,
        "start_word_id": words[0].word_id,
        "end_word_id": words[-1].word_id,
        "hook": "The form was the ceiling",
        "platforms": (Platform.TIKTOK,),
        "durations_ms": (20_000, 45_000),
    }
    values.update(overrides)
    return generate_variants(**values)  # type: ignore[arg-type]


@pytest.mark.unit
def test_every_variant_names_real_word_boundaries_of_the_same_transcript() -> None:
    """A Variant that cannot be resolved to words cannot be previewed or rendered."""
    words = _words(_speech(12))
    known = {word.word_id for word in words}

    variants = _generate()

    assert variants
    for variant in variants:
        assert variant.start_word_id in known  # type: ignore[attr-defined]
        assert variant.end_word_id in known  # type: ignore[attr-defined]


@pytest.mark.unit
def test_no_more_than_three_hook_strategies_are_ever_offered() -> None:
    """The plan caps hooks at three; more is a menu nobody reads."""
    variants = _generate(durations_ms=SUPPORTED_TARGET_DURATIONS_MS)

    strategies = {variant.hook_strategy for variant in variants}  # type: ignore[attr-defined]
    assert len(strategies) <= 3
    assert strategies <= set(HookStrategy)


@pytest.mark.unit
@pytest.mark.parametrize("target", SUPPORTED_TARGET_DURATIONS_MS)
def test_a_variant_lands_within_tolerance_of_the_target_it_was_asked_for(target: int) -> None:
    """A 30-second variant that runs 51 seconds is not the thing that was requested."""
    variants = _generate(speech=_speech(24), durations_ms=(target,))

    for variant in variants:
        assert variant.target_duration_ms == target  # type: ignore[attr-defined]
        actual = variant.end_ms - variant.start_ms  # type: ignore[attr-defined]
        assert abs(actual - target) <= target * 0.25


@pytest.mark.unit
@pytest.mark.parametrize("target", (25_000, 0, -1, 120_000))
def test_an_unsupported_duration_is_refused_rather_than_rounded(target: int) -> None:
    """Silently serving 30 seconds to someone who asked for 25 tells them something untrue."""
    with pytest.raises(ValueError):
        _generate(durations_ms=(target,))


@pytest.mark.unit
def test_a_target_that_cannot_be_cut_honestly_yields_no_variant() -> None:
    """Returning nothing is the correct answer when every cut of that length misleads."""
    # One long unbroken sentence: no interior boundary preserves a complete thought.
    speech = " ".join(["word"] * 90) + " end."
    words = _words(speech)

    variants = generate_variants(
        words=words,
        start_word_id=words[0].word_id,
        end_word_id=words[-1].word_id,
        hook="A single unbroken thought",
        platforms=(Platform.TIKTOK,),
        durations_ms=(20_000,),
    )

    assert variants == ()


@pytest.mark.unit
def test_no_variant_carries_a_blocking_warning() -> None:
    """A blocking warning is exactly the statement that this cut must not be offered."""
    variants = _generate(speech=_speech(24), durations_ms=SUPPORTED_TARGET_DURATIONS_MS)

    for variant in variants:
        severities = {warning.severity for warning in variant.warnings}  # type: ignore[attr-defined]
        assert ContextWarningSeverity.BLOCKING not in severities


@pytest.mark.unit
def test_a_variant_preserves_a_complete_thought_at_both_ends() -> None:
    """Beginning mid-sentence reads as an accusation the speaker never made."""
    words = _words(_speech(24))
    positions = {word.word_id: index for index, word in enumerate(words)}

    variants = _generate(speech=_speech(24), durations_ms=(30_000,))

    assert variants
    for variant in variants:
        start = positions[variant.start_word_id]  # type: ignore[attr-defined]
        end = positions[variant.end_word_id]  # type: ignore[attr-defined]
        assert start == 0 or words[start - 1].punctuation.endswith((".", "!", "?"))
        assert words[end].punctuation.endswith((".", "!", "?"))


@pytest.mark.unit
def test_a_question_opening_is_offered_when_the_candidate_contains_one() -> None:
    """The strongest hook is often the question the clip goes on to answer."""
    # Long enough that a 20-second cut starting at the question is actually available.
    speech = (
        "We tried everything first here. "
        "What actually moved the number in the end here? "
        "Removing the form did it for us all quickly after that change."
    )
    words = _words(speech)

    variants = generate_variants(
        words=words,
        start_word_id=words[0].word_id,
        end_word_id=words[-1].word_id,
        hook="What moved the number",
        platforms=(Platform.TIKTOK,),
        durations_ms=(20_000,),
    )

    assert HookStrategy.QUESTION_FIRST in {
        variant.hook_strategy
        for variant in variants  # type: ignore[attr-defined]
    }


@pytest.mark.unit
def test_a_variant_carries_its_platform_packaging_and_a_stated_rationale() -> None:
    """A member choosing between variants is choosing on the evidence shown beside them."""
    variants = _generate(platforms=(Platform.YOUTUBE_SHORTS,))

    assert variants
    for variant in variants:
        assert variant.platform is Platform.YOUTUBE_SHORTS  # type: ignore[attr-defined]
        assert variant.packaging.export_preset  # type: ignore[attr-defined]
        assert variant.rationale  # type: ignore[attr-defined]


@pytest.mark.unit
def test_variants_are_generated_for_each_requested_platform_without_duplicating_media() -> None:
    """One candidate, three destinations: the difference is packaging, not another cut."""
    variants = _generate(platforms=tuple(Platform), durations_ms=(45_000,))

    by_platform: dict[object, set[tuple[str, str]]] = {}
    for variant in variants:
        key = variant.platform  # type: ignore[attr-defined]
        bounds = (variant.start_word_id, variant.end_word_id)  # type: ignore[attr-defined]
        by_platform.setdefault(key, set()).add(bounds)
    assert set(by_platform) == set(Platform)
    assert len({frozenset(bounds) for bounds in by_platform.values()}) == 1


@pytest.mark.unit
@pytest.mark.parametrize("platform", tuple(Platform))
def test_every_platform_states_its_aspect_safe_zones_and_guidance(platform: Platform) -> None:
    """Packaging is what a member needs before exporting, not after a rejection."""
    packaging = packaging_for(platform)

    assert packaging.aspect_ratio == "9:16"
    assert packaging.safe_area_top_percent > 0
    assert packaging.safe_area_bottom_percent > 0
    assert packaging.max_title_characters > 0
    assert packaging.caption_style
    assert packaging.export_preset


@pytest.mark.unit
def test_generation_refuses_a_span_it_cannot_resolve() -> None:
    """An unknown boundary is refused here for the same reason it is in assessment."""
    words = _words(_speech(12))

    with pytest.raises(ValueError):
        generate_variants(
            words=words,
            start_word_id="w000404",
            end_word_id=words[-1].word_id,
            hook="Unresolvable",
            platforms=(Platform.TIKTOK,),
            durations_ms=(20_000,),
        )
