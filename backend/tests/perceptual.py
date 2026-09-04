"""The perceptual comparison the golden-frame gate is decided by.

A render is allowed to differ from its golden frame — encoders round, and a font is
rasterized differently by different builds of the same library — but it is not allowed to
differ *structurally*. Structural similarity answers that question directly: it compares
local means, variances, and covariance rather than pixel values, so a frame that is a
shade darker still scores near one while a frame with the wrong picture in it does not.

Two decisions are worth stating.

* **The threshold is 0.97.** Below that, the two frames disagree about something a viewer
  would see. It is a property of the gate rather than of one scenario, so it lives here.
* **Text is masked, never scored.** Font rasterization is a property of the machine, so
  the region a caption or a title is drawn into is excluded from the comparison and the
  mask is recorded with the scenario that used it.

This module is test tooling: it is imported by the golden-frame suite and by nothing in
`src/`, so `numpy` stays a development dependency rather than a production one.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# The gate every golden frame is decided by.
SSIM_THRESHOLD = 0.97
# The window structural similarity is computed over, in pixels.
WINDOW = 8
_C1 = (0.01 * 255) ** 2
_C2 = (0.03 * 255) ** 2


@dataclass(frozen=True, slots=True)
class MaskedRegion:
    """One rectangle excluded from the comparison, and why it is excluded."""

    top: float
    bottom: float
    reason: str


def read_frame(path: Path, *, width: int, height: int) -> np.ndarray:
    """Decode one image to a single-channel frame of exactly the size expected."""
    raw = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            f"scale={width}:{height}",
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    if len(raw) != width * height:
        raise ValueError(f"{path} decoded to {len(raw)} bytes rather than {width * height}")
    return np.frombuffer(raw, dtype=np.uint8).reshape(height, width).astype(np.float64)


def extract_frame(video: Path, destination: Path, *, at_seconds: float) -> Path:
    """Take one frame out of a rendered file, at exactly one instant."""
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{at_seconds:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(destination),
        ],
        check=True,
        capture_output=True,
    )
    return destination


def structural_similarity(
    left: np.ndarray, right: np.ndarray, *, masked: tuple[MaskedRegion, ...] = ()
) -> float:
    """Score how structurally alike two frames are, ignoring every masked region."""
    if left.shape != right.shape:
        raise ValueError(f"frames differ in size: {left.shape} against {right.shape}")
    scores = _similarity_map(left, right)
    keep = np.ones(scores.shape, dtype=bool)
    height = scores.shape[0]
    for region in masked:
        top = int(region.top * height)
        bottom = int(region.bottom * height)
        keep[top:bottom, :] = False
    if not keep.any():
        raise ValueError("every region was masked, so nothing was compared")
    return float(scores[keep].mean())


def _similarity_map(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """Compute the per-window similarity of two frames, as a map over the frame."""
    height, width = left.shape
    rows = height // WINDOW
    columns = width // WINDOW
    trimmed = (rows * WINDOW, columns * WINDOW)
    blocks_left = _blocks(left[: trimmed[0], : trimmed[1]], rows, columns)
    blocks_right = _blocks(right[: trimmed[0], : trimmed[1]], rows, columns)
    mean_left = blocks_left.mean(axis=(2, 3))
    mean_right = blocks_right.mean(axis=(2, 3))
    variance_left = blocks_left.var(axis=(2, 3))
    variance_right = blocks_right.var(axis=(2, 3))
    covariance = (
        (blocks_left - mean_left[:, :, None, None]) * (blocks_right - mean_right[:, :, None, None])
    ).mean(axis=(2, 3))
    numerator = (2 * mean_left * mean_right + _C1) * (2 * covariance + _C2)
    denominator = (mean_left**2 + mean_right**2 + _C1) * (variance_left + variance_right + _C2)
    return numerator / denominator


def _blocks(frame: np.ndarray, rows: int, columns: int) -> np.ndarray:
    """Split one frame into the fixed windows similarity is computed over."""
    return frame.reshape(rows, WINDOW, columns, WINDOW).swapaxes(1, 2)
