"""Deterministic placement of accepted visual beats onto one candidate's timeline.

This module owns every millisecond a B-roll suggestion will ever carry. A planning model
proposes that a moment would benefit from a picture; nothing it returns reaches the
timeline until this code has decided the shot fits the coverage the member asked for, ends
inside the clip, stops at a scene change, and does not cover a moment the viewer must see.

Placement never fails. A clip that earns no suggestions produces none, because "this clip
does not want B-roll" is an ordinary answer and not an error to report to a member.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

from clipah.broll.models import (
    DEFAULT_PLACEMENT_POLICY,
    DENSITY_POLICIES,
    BrollCoverage,
    CandidateSpan,
    PlacedSuggestion,
    PlacementPolicy,
    VisualBeat,
)
from clipah.transcripts.models import TranscriptWord


def scene_boundaries(words: Sequence[TranscriptWord], *, silence_gap_ms: int) -> tuple[int, ...]:
    """Derive the visual changes the transcript can prove, in ascending milliseconds.

    Nothing in the pipeline detects shot changes on the proxy yet, so the two changes a
    transcript can evidence stand in for them: a pause long enough for an editor to hear,
    and a new speaker. Both are places a viewer expects the picture to change anyway.
    """
    boundaries: list[int] = []
    for previous, word in pairwise(words):
        if word.start_ms - previous.end_ms >= silence_gap_ms or word.speaker != previous.speaker:
            boundaries.append(word.start_ms)
    return tuple(boundaries)


def place_suggestions(
    *,
    beats: Sequence[VisualBeat],
    candidate: CandidateSpan,
    coverage: BrollCoverage,
    boundaries: Sequence[int] = (),
    policy: PlacementPolicy = DEFAULT_PLACEMENT_POLICY,
) -> tuple[PlacedSuggestion, ...]:
    """Turn accepted beats into the shots one clip may actually carry."""
    density = DENSITY_POLICIES[coverage]
    ordered = sorted(beats, key=lambda beat: (beat.start_ms, beat.end_ms, beat.start_word_id))
    cuts = tuple(sorted(boundaries))

    placed: list[PlacedSuggestion] = []
    seen_intents: set[str] = set()
    for beat in ordered:
        if not _is_coverable(beat, candidate=candidate, policy=policy):
            continue
        key = _intent_key(beat)
        if key in seen_intents:
            continue
        shot = _shot(beat, candidate=candidate, cuts=cuts, policy=policy)
        if shot is None:
            continue
        start_ms, end_ms = shot
        if placed and start_ms - placed[-1].start_ms < density.min_spacing_ms:
            continue
        if placed and start_ms < placed[-1].end_ms:
            continue
        seen_intents.add(key)
        placed.append(
            PlacedSuggestion(
                beat=beat,
                start_ms=start_ms,
                end_ms=end_ms,
                placement_reason=beat.placement_reason,
            )
        )
    return tuple(placed)


def _is_coverable(beat: VisualBeat, *, candidate: CandidateSpan, policy: PlacementPolicy) -> bool:
    """Refuse any beat the product has promised never to cover with a picture."""
    if beat.protection is not None:
        return False
    if beat.intent.confidence < policy.min_confidence:
        return False
    if beat.start_ms < candidate.start_ms or beat.end_ms > candidate.end_ms:
        return False
    return beat.start_ms >= candidate.start_ms + policy.hook_guard_ms


def _shot(
    beat: VisualBeat,
    *,
    candidate: CandidateSpan,
    cuts: Sequence[int],
    policy: PlacementPolicy,
) -> tuple[int, int] | None:
    """Size one shot inside its beat, its clip, and the next scene change."""
    start_ms = beat.start_ms
    wanted = min(max(beat.end_ms - beat.start_ms, policy.min_shot_ms), policy.max_shot_ms)
    end_ms = min(start_ms + wanted, candidate.end_ms)
    next_cut = next((cut for cut in cuts if cut > start_ms), None)
    if next_cut is not None:
        end_ms = min(end_ms, next_cut)
    if end_ms - start_ms < policy.min_shot_ms:
        return None
    return start_ms, end_ms


def _intent_key(beat: VisualBeat) -> str:
    """Name the picture a beat asks for, so the same picture is offered only once."""
    intent = beat.intent
    parts = (intent.subject, intent.action, intent.setting)
    return "|".join(" ".join(part.split()).casefold() for part in parts)
