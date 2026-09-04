"""Propose where a vertical clip should look, from evidence rather than from a guess.

A clip cut from a landscape recording has to keep part of the frame and throw the rest
away. This module proposes which part, using two pieces of evidence that already exist:
face boxes detected in the source, and the speaker the transcript says is talking. The
proposal is expressed as keyframes on the item, so a member sees exactly what was
suggested and can move, replace, or delete any of it.

Three rules hold everywhere here.

* **A member's work is never overwritten.** An item that already carries keyframes is
  returned untouched, with the reason it was left alone.
* **No evidence means no invention.** With no face detected — or none the detector was
  confident about — the proposal is the centred framing the editor already applies.
* **The proposal is bounded.** The window never leaves the source frame, and never
  travels faster than a viewer can follow, because a swing across the picture reads as a
  mistake rather than as a camera move.

Nothing here detects a face. Detection is a provider's job; this module takes the boxes
it is given, and is therefore decided entirely by its own inputs.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from clipah.editor.models import Crop, Easing, Keyframe, TrackItem, Transform
from clipah.transcripts.models import SpeakerSegment

# How far across the source frame the window may travel in one second. A full frame in
# roughly three seconds is a deliberate move; anything quicker reads as a jump.
MAX_PAN_PER_SECOND = 0.35
# How often the proposal is allowed to place a keyframe, so a detector sampled every
# frame does not produce a keyframe every frame.
KEYFRAME_INTERVAL_MS = 500


@dataclass(frozen=True, slots=True)
class FaceBox:
    """One detected face at one instant, normalized inside the source frame."""

    at_ms: int
    x: float
    y: float
    width: float
    height: float
    confidence: float
    speaker: str | None = None

    @property
    def centre_x(self) -> float:
        """Where the middle of this face sits across the frame."""
        return self.x + self.width / 2


@dataclass(frozen=True, slots=True)
class SmartCropPolicy:
    """The thresholds a proposal is made under."""

    minimum_confidence: float = 0.35
    keyframe_interval_ms: int = KEYFRAME_INTERVAL_MS
    max_pan_per_second: float = MAX_PAN_PER_SECOND


@dataclass(frozen=True, slots=True)
class CropSuggestion:
    """One proposal: a window, how it moves, and why it says what it says."""

    crop: Crop | None
    crop_width: float
    keyframes: tuple[Keyframe, ...] = field(default=())
    reason: str = "framed on the speaker"


def suggest_crop_keyframes(
    item: TrackItem,
    *,
    faces: Sequence[FaceBox],
    segments: Sequence[SpeakerSegment],
    source_aspect: float,
    target_aspect: float = 9 / 16,
    policy: SmartCropPolicy | None = None,
) -> CropSuggestion:
    """Propose a framing for one item, or say plainly why there is nothing to propose."""
    rules = policy or SmartCropPolicy()
    if source_aspect <= target_aspect + 1e-6:
        return CropSuggestion(
            crop=None, crop_width=1.0, reason="the source already matches the target frame"
        )
    width = min(target_aspect / source_aspect, 1.0)
    centred = Crop(x=(1 - width) / 2, y=0.0, width=width, height=1.0)
    if item.keyframes:
        return CropSuggestion(
            crop=centred, crop_width=width, reason="this item already carries keyframes"
        )
    confident = [face for face in faces if face.confidence >= rules.minimum_confidence]
    if not confident:
        return CropSuggestion(crop=centred, crop_width=width, reason="no face was detected")
    positions = _positions(item, confident, segments, rules)
    if not positions:
        return CropSuggestion(crop=centred, crop_width=width, reason="no face was detected")
    bounded = _bounded(positions, width, rules)
    return CropSuggestion(
        crop=centred,
        crop_width=width,
        keyframes=tuple(
            Keyframe(
                at_ms=at_ms,
                easing=Easing.EASE_IN_OUT,
                transform=Transform(x=x, y=0.5, scale=1.0, rotation=0.0),
                opacity=None,
                style=None,
            )
            for at_ms, x in bounded
        ),
    )


def _positions(
    item: TrackItem,
    faces: Sequence[FaceBox],
    segments: Sequence[SpeakerSegment],
    rules: SmartCropPolicy,
) -> list[tuple[int, float]]:
    """Sample where the window should sit, at the interval the policy allows.

    The face followed at each instant is the one belonging to whoever the transcript says
    is talking. When that speaker has no detected face — a detection gap, not a reason to
    move — the last framing that *was* evidence is held.
    """
    duration_ms = item.duration_ms
    held: float | None = None
    positions: list[tuple[int, float]] = []
    for at_ms in range(0, duration_ms + 1, rules.keyframe_interval_ms):
        speaker = _speaker_at(segments, at_ms)
        centre = _face_centre(faces, at_ms, speaker)
        if centre is None:
            centre = held
        if centre is None:
            continue
        held = centre
        positions.append((at_ms, centre))
    return positions


def _speaker_at(segments: Sequence[SpeakerSegment], at_ms: int) -> str | None:
    """Whoever the transcript says is talking at one instant, if it says anything."""
    for segment in segments:
        if segment.start_ms <= at_ms < segment.end_ms:
            return segment.speaker
    return None


def _face_centre(faces: Sequence[FaceBox], at_ms: int, speaker: str | None) -> float | None:
    """The middle of the nearest detected face belonging to one speaker."""
    candidates = [face for face in faces if speaker is None or face.speaker in {speaker, None}]
    if not candidates:
        return None
    nearest = min(candidates, key=lambda face: (abs(face.at_ms - at_ms), face.at_ms))
    return nearest.centre_x


def _bounded(
    positions: Sequence[tuple[int, float]], width: float, rules: SmartCropPolicy
) -> list[tuple[int, float]]:
    """Keep the window inside the frame and slow enough to follow, in that order."""
    # The stored value is rounded, so the bounds are rounded inwards first: a window
    # rounded a ten-thousandth outside the frame is still outside the frame.
    lowest = math.ceil(width / 2 * 10_000) / 10_000
    highest = math.floor((1 - width / 2) * 10_000) / 10_000
    bounded: list[tuple[int, float]] = []
    for at_ms, centre in positions:
        wanted = min(max(centre, lowest), highest)
        previous = bounded[-1] if bounded else None
        if previous is not None:
            seconds = max((at_ms - previous[0]) / 1000, 0.001)
            limit = rules.max_pan_per_second * seconds
            wanted = min(max(wanted, previous[1] - limit), previous[1] + limit)
        bounded.append((at_ms, min(max(round(wanted, 4), lowest), highest)))
    return _without_still_stretches(bounded)


def _without_still_stretches(
    positions: Sequence[tuple[int, float]],
) -> list[tuple[int, float]]:
    """Drop a keyframe that repeats the one before and after it, which animates nothing."""
    kept: list[tuple[int, float]] = []
    for index, entry in enumerate(positions):
        previous = positions[index - 1] if index > 0 else None
        following = positions[index + 1] if index + 1 < len(positions) else None
        if (
            previous is not None
            and following is not None
            and previous[1] == entry[1] == following[1]
        ):
            continue
        kept.append(entry)
    return kept
