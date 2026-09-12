"""The legacy Flask stack is gone and cannot come back quietly.

Task 48 removes the legacy application after the rollback window and then proves it. The
proof is a repository sweep rather than a reading: the specific names below are the ones the
rebuild was designed to make impossible — shared module state, a thread as a job runner, a
single shared working file, downloader cookies, a disabled certificate check, and markup
assigned as a string. A test that only checked the files were deleted would pass the day
someone reintroduced the pattern under a new filename.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SWEEP = REPOSITORY_ROOT / "scripts" / "check-legacy-removed.sh"

#: Paths that existed only to serve the legacy stack.
REMOVED_PATHS = (
    "app.py",
    "templates",
    "requirements.txt",
    "nixpacks.toml",
    "static/script.js",
    "static/style.css",
)

#: Patterns the sweep refuses outside documentation. Each names a property of the rebuilt
#: system, not a coding preference.
FORBIDDEN = (
    "processing_status",
    "threading.Thread",
    "main_video.mp4",
    "cookies.txt",
    "nocheckcertificate",
    "innerHTML",
)


@pytest.mark.unit
def test_every_legacy_path_is_gone() -> None:
    """The legacy deployment is removed, not merely unreferenced."""
    surviving = [path for path in REMOVED_PATHS if (REPOSITORY_ROOT / path).exists()]

    assert surviving == [], f"these legacy paths still exist: {surviving}"


@pytest.mark.unit
def test_the_legacy_font_and_asset_directories_are_gone() -> None:
    """Nothing in the rebuilt stack loads these, so keeping them would only mislead."""
    assert not (REPOSITORY_ROOT / "styles").exists()
    assert not (REPOSITORY_ROOT / "public").exists()


@pytest.mark.unit
def test_the_sweep_is_executable_and_passes_on_this_checkout() -> None:
    """The sweep is the gate; if it cannot run, nothing is being checked."""
    assert os.access(SWEEP, os.X_OK)

    completed = subprocess.run(
        [str(SWEEP)], cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=False
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.unit
def test_the_sweep_fails_when_a_forbidden_pattern_returns(tmp_path: Path) -> None:
    """A sweep that cannot fail is a sweep that proves nothing.

    Each pattern is reintroduced in a real tracked file, one at a time, and the sweep has to
    notice. The file is removed again immediately, so the checkout is left as it was found.
    """
    intruder = REPOSITORY_ROOT / "frontend" / "lib" / "legacy-intruder.ts"

    for pattern in FORBIDDEN:
        intruder.write_text(f"export const reintroduced = '{pattern}'\n", encoding="utf-8")
        try:
            completed = subprocess.run(
                [str(SWEEP)],
                cwd=REPOSITORY_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            intruder.unlink()

        assert completed.returncode != 0, f"the sweep ignored {pattern}"
        assert pattern in completed.stdout + completed.stderr


@pytest.mark.unit
def test_the_sweep_fails_when_a_legacy_route_returns() -> None:
    """The legacy HTTP surface is the part a rollback would be tempted to restore."""
    intruder = REPOSITORY_ROOT / "frontend" / "lib" / "legacy-route-intruder.ts"
    intruder.write_text("export const route = '/process'\n", encoding="utf-8")
    try:
        completed = subprocess.run(
            [str(SWEEP)], cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=False
        )
    finally:
        intruder.unlink()

    assert completed.returncode != 0
