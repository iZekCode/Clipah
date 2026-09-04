"""The perceptual gate itself, before it is trusted to judge a render.

A comparison that scores everything highly proves nothing, so these tests ask the gate to
fail: a different picture, a shifted picture, and a masked region that hides a difference
all have to answer the way the golden-frame suite depends on.
"""

from __future__ import annotations

import numpy as np
import pytest

from perceptual import SSIM_THRESHOLD, MaskedRegion, structural_similarity


def gradient(width: int = 128, height: int = 128, *, shift: int = 0) -> np.ndarray:
    """One deterministic frame: a diagonal gradient, optionally moved sideways."""
    columns = (np.arange(width) + shift) % width
    rows = np.arange(height).reshape(height, 1)
    return ((columns.reshape(1, width) + rows) % 256).astype(np.float64)


@pytest.mark.unit
def test_one_frame_is_identical_to_itself() -> None:
    """A gate that cannot recognise an exact match cannot recognise anything."""
    frame = gradient()

    assert structural_similarity(frame, frame) == pytest.approx(1.0)


@pytest.mark.unit
def test_a_frame_a_shade_darker_still_passes_the_gate() -> None:
    """Encoders round, and a render is not wrong because it is one level darker."""
    frame = gradient()

    score = structural_similarity(frame, np.clip(frame - 1, 0, 255))

    assert score >= SSIM_THRESHOLD


@pytest.mark.unit
def test_a_different_picture_fails_the_gate() -> None:
    """The gate exists to catch exactly this, so it may not be generous about it."""
    score = structural_similarity(gradient(), gradient(shift=40))

    assert score < SSIM_THRESHOLD


@pytest.mark.unit
def test_noise_over_the_whole_frame_fails_the_gate() -> None:
    """A render that lost its structure scores low even with the same average brightness."""
    generator = np.random.default_rng(seed=7)
    noisy = generator.integers(0, 256, size=(128, 128)).astype(np.float64)

    assert structural_similarity(gradient(), noisy) < SSIM_THRESHOLD


@pytest.mark.unit
def test_a_masked_region_is_excluded_from_the_score() -> None:
    """Font rasterization differs by machine, so the band type is drawn in is not scored."""
    frame = gradient()
    altered = frame.copy()
    altered[96:128, :] = 0

    unmasked = structural_similarity(frame, altered)
    masked = structural_similarity(
        frame,
        altered,
        masked=(MaskedRegion(top=0.75, bottom=1.0, reason="drawn text"),),
    )

    assert unmasked < SSIM_THRESHOLD
    assert masked >= SSIM_THRESHOLD


@pytest.mark.unit
def test_masking_everything_is_refused_rather_than_scored_as_a_pass() -> None:
    """A gate that compared nothing would certify anything."""
    frame = gradient()

    with pytest.raises(ValueError):
        structural_similarity(
            frame, frame, masked=(MaskedRegion(top=0.0, bottom=1.0, reason="everything"),)
        )


@pytest.mark.unit
def test_two_frames_of_different_sizes_are_refused() -> None:
    """Comparing a resized render against a golden frame would score the resize."""
    with pytest.raises(ValueError):
        structural_similarity(gradient(), gradient(width=64, height=64))
