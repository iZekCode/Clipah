"""Smart crop: a suggestion about where to look, never a decision a member cannot undo.

A vertical clip cut from a landscape recording has to choose which part of the frame to
keep. This module makes that choice from evidence — detected face boxes and the speaker
the transcript says is talking — and expresses it as keyframes on the item, so a member
sees exactly what it proposed and can move, replace, or delete any of it.

Three rules run through everything here. **Work a member did is never overwritten**: an
item that already carries keyframes is left exactly as it is. **No evidence means no
invention**: with no faces detected the suggestion is the centred framing the editor
already uses, not a guess. And **the suggestion is bounded**: a proposed window never
leaves the source frame, and never moves faster than a viewer can follow.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from clipah.assets.smart_crop import (
    MAX_PAN_PER_SECOND,
    FaceBox,
    SmartCropPolicy,
    suggest_crop_keyframes,
)
from clipah.editor.models import (
    BlendMode,
    Crop,
    Keyframe,
    MotionPreset,
    Origin,
    OriginType,
    TrackItem,
    Transform,
)
from clipah.transcripts.models import SpeakerSegment

SOURCE_ASSET_ID = UUID("11111111-1111-4111-8111-111111111111")


def item(**overrides: object) -> TrackItem:
    """One base timeline item, thirty seconds of a landscape source."""
    values: dict[str, object] = {
        "id": "scene-1",
        "source_asset_id": SOURCE_ASSET_ID,
        "timeline_start_ms": 0,
        "source_in_ms": 0,
        "source_out_ms": 30_000,
        "transform": Transform(x=0.5, y=0.5, scale=1.0, rotation=0.0),
        "crop": None,
        "opacity": 1.0,
        "blend_mode": BlendMode.NORMAL,
        "motion": MotionPreset.NONE,
        "origin": Origin(type=OriginType.SOURCE, suggestion_id=None, provenance_id=None),
        "keyframes": (),
    }
    values.update(overrides)
    return TrackItem(**values)  # type: ignore[arg-type]


def face(at_ms: int, x: float, *, speaker: str = "SPEAKER_00", confidence: float = 0.9) -> FaceBox:
    """One detected face, normalized inside the source frame."""
    return FaceBox(
        at_ms=at_ms,
        x=x,
        y=0.25,
        width=0.2,
        height=0.3,
        confidence=confidence,
        speaker=speaker,
    )


def segment(segment_id: str, speaker: str, start_ms: int, end_ms: int) -> SpeakerSegment:
    """One stretch of the transcript in which one speaker is talking."""
    return SpeakerSegment(
        segment_id=segment_id,
        speaker=speaker,
        start_ms=start_ms,
        end_ms=end_ms,
        word_ids=(),
    )


@pytest.mark.unit
def test_a_suggestion_follows_the_face_of_whoever_the_transcript_says_is_talking() -> None:
    """Two people in frame is the ordinary case, and the wrong one is the wrong clip."""
    suggestion = suggest_crop_keyframes(
        item(),
        faces=(
            face(0, 0.10, speaker="SPEAKER_00"),
            face(0, 0.70, speaker="SPEAKER_01"),
            face(20_000, 0.10, speaker="SPEAKER_00"),
            face(20_000, 0.70, speaker="SPEAKER_01"),
        ),
        segments=(
            segment("s1", "SPEAKER_00", 0, 15_000),
            segment("s2", "SPEAKER_01", 15_000, 30_000),
        ),
        source_aspect=16 / 9,
    )

    assert suggestion.keyframes
    first = suggestion.keyframes[0].transform
    last = suggestion.keyframes[-1].transform
    assert first is not None and last is not None
    assert first.x < 0.5 < last.x


@pytest.mark.unit
def test_a_suggestion_never_proposes_a_window_that_leaves_the_source_frame() -> None:
    """A window outside the picture is a black bar, which is not a framing decision."""
    suggestion = suggest_crop_keyframes(
        item(),
        faces=(face(0, 0.02), face(10_000, 0.98)),
        segments=(segment("s1", "SPEAKER_00", 0, 30_000),),
        source_aspect=16 / 9,
    )

    half_width = suggestion.crop_width / 2
    for keyframe in suggestion.keyframes:
        assert keyframe.transform is not None
        assert half_width <= keyframe.transform.x <= 1 - half_width


@pytest.mark.unit
def test_a_suggestion_never_travels_faster_than_a_viewer_can_follow() -> None:
    """A jump cut across the frame reads as a mistake rather than as a camera move."""
    suggestion = suggest_crop_keyframes(
        item(),
        faces=(face(0, 0.05), face(1_000, 0.95)),
        segments=(segment("s1", "SPEAKER_00", 0, 30_000),),
        source_aspect=16 / 9,
    )

    for earlier, later in zip(suggestion.keyframes, suggestion.keyframes[1:], strict=False):
        assert earlier.transform is not None and later.transform is not None
        seconds = max((later.at_ms - earlier.at_ms) / 1000, 0.001)
        assert abs(later.transform.x - earlier.transform.x) / seconds <= MAX_PAN_PER_SECOND + 1e-9


@pytest.mark.unit
def test_keyframes_are_ordered_and_never_repeat_one_instant() -> None:
    """The validator refuses anything else, so a suggestion that did would be unusable."""
    suggestion = suggest_crop_keyframes(
        item(),
        faces=tuple(face(at_ms, 0.3 if at_ms % 2_000 else 0.7) for at_ms in range(0, 30_000, 500)),
        segments=(segment("s1", "SPEAKER_00", 0, 30_000),),
        source_aspect=16 / 9,
    )

    times = [keyframe.at_ms for keyframe in suggestion.keyframes]
    assert times == sorted(set(times))
    assert times[-1] <= item().duration_ms


@pytest.mark.unit
def test_no_detected_face_falls_back_to_the_centred_framing_rather_than_a_guess() -> None:
    """A suggestion invented from nothing would be worse than the default it replaced."""
    suggestion = suggest_crop_keyframes(
        item(),
        faces=(),
        segments=(segment("s1", "SPEAKER_00", 0, 30_000),),
        source_aspect=16 / 9,
    )

    assert suggestion.keyframes == ()
    assert suggestion.crop == Crop(
        x=(1 - suggestion.crop_width) / 2, y=0.0, width=suggestion.crop_width, height=1.0
    )
    assert suggestion.reason == "no face was detected"


@pytest.mark.unit
def test_a_face_below_the_confidence_floor_is_not_evidence() -> None:
    """A guess with a number attached is still a guess."""
    suggestion = suggest_crop_keyframes(
        item(),
        faces=(face(0, 0.1, confidence=0.2), face(10_000, 0.9, confidence=0.25)),
        segments=(segment("s1", "SPEAKER_00", 0, 30_000),),
        source_aspect=16 / 9,
        policy=SmartCropPolicy(minimum_confidence=0.5),
    )

    assert suggestion.keyframes == ()
    assert suggestion.reason == "no face was detected"


@pytest.mark.unit
def test_an_item_a_member_has_already_animated_is_left_exactly_as_it_is() -> None:
    """Smart crop is a suggestion, and a suggestion never overwrites somebody's work."""
    authored = (
        Keyframe(
            at_ms=0,
            easing="linear",
            transform=Transform(x=0.2, y=0.5, scale=1.0, rotation=0.0),
            opacity=None,
            style=None,
        ),
    )

    suggestion = suggest_crop_keyframes(
        item(keyframes=authored),
        faces=(face(0, 0.8), face(10_000, 0.8)),
        segments=(segment("s1", "SPEAKER_00", 0, 30_000),),
        source_aspect=16 / 9,
    )

    assert suggestion.keyframes == ()
    assert suggestion.reason == "this item already carries keyframes"


@pytest.mark.unit
def test_a_source_already_the_target_shape_needs_no_crop_at_all() -> None:
    """Cropping a vertical source into a vertical frame would only lose picture."""
    suggestion = suggest_crop_keyframes(
        item(),
        faces=(face(0, 0.8), face(10_000, 0.2)),
        segments=(segment("s1", "SPEAKER_00", 0, 30_000),),
        source_aspect=9 / 16,
    )

    assert suggestion.crop is None
    assert suggestion.keyframes == ()
    assert suggestion.reason == "the source already matches the target frame"


@pytest.mark.unit
def test_a_speaker_with_no_detected_face_holds_the_last_framing_that_was_evidence() -> None:
    """Losing a face is a detection gap, not a reason to swing the camera away."""
    suggestion = suggest_crop_keyframes(
        item(),
        faces=(face(0, 0.7, speaker="SPEAKER_01"), face(20_000, 0.7, speaker="SPEAKER_01")),
        segments=(
            segment("s1", "SPEAKER_01", 0, 10_000),
            segment("s2", "SPEAKER_02", 10_000, 30_000),
        ),
        source_aspect=16 / 9,
    )

    assert suggestion.keyframes
    positions = {
        keyframe.transform.x for keyframe in suggestion.keyframes if keyframe.transform is not None
    }
    assert len(positions) == 1


@pytest.mark.unit
def test_faces_that_belong_to_nobody_who_speaks_are_not_evidence_either() -> None:
    """A face in the background is not the speaker, so it does not decide the framing."""
    suggestion = suggest_crop_keyframes(
        item(),
        faces=(face(0, 0.8, speaker="SPEAKER_09"), face(10_000, 0.8, speaker="SPEAKER_09")),
        # The whole clip is spoken by SPEAKER_01, including its last instant, so no
        # sample falls outside the transcript's evidence.
        segments=(segment("s1", "SPEAKER_01", 0, 30_500),),
        source_aspect=16 / 9,
    )

    assert suggestion.keyframes == ()
    assert suggestion.reason == "no face was detected"
