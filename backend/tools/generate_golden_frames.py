"""Write the golden frames the perceptual gate compares renders against.

Run this only when a picture is *meant* to change, and look at the result before
committing it: a golden frame is the record of what somebody signed off on, so
regenerating one without looking at it turns the gate into a rubber stamp.

    uv run python tools/generate_golden_frames.py [scenario ...]

A scenario whose filters this FFmpeg build does not carry is skipped by name and left
out of the manifest, so a machine without libass never writes a caption frame nobody
could have checked.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from golden_scenarios import GOLDEN_SCENARIOS, GoldenScenario
from perceptual import extract_frame

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = Path(__file__).resolve().parents[1] / "tests"
GOLDEN_ROOT = TESTS_ROOT / "fixtures" / "golden"
MANIFEST_PATH = GOLDEN_ROOT / "manifest.json"


def render(scenario: GoldenScenario, workspace: Path) -> Path:
    """Render one scenario with the real FFmpeg and take the frame the gate samples."""
    sys.path.insert(0, str(TESTS_ROOT / "integration"))
    from test_golden_frames import _render

    output = _render(workspace, scenario)
    return extract_frame(output, workspace / f"{scenario.name}.png", at_seconds=scenario.at_seconds)


def available_filters() -> str:
    """Every filter this FFmpeg build carries, as one searchable block of text."""
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"], check=True, capture_output=True, text=True
    ).stdout


def main() -> None:
    """Write every requested golden frame, and record its digest in the manifest."""
    wanted = set(sys.argv[1:])
    filters = available_filters()
    manifest: dict[str, dict[str, dict[str, str]]] = {"frames": {}}
    if MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    GOLDEN_ROOT.mkdir(parents=True, exist_ok=True)

    for scenario in GOLDEN_SCENARIOS:
        if wanted and scenario.name not in wanted:
            continue
        missing = [name for name in scenario.required_filters if f" {name} " not in filters]
        if missing:
            print(f"skipped {scenario.name}: this FFmpeg has no {', '.join(missing)} filter")
            continue
        with tempfile.TemporaryDirectory() as directory:
            frame = render(scenario, Path(directory))
            destination = GOLDEN_ROOT / f"{scenario.name}.png"
            shutil.copy(frame, destination)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        manifest["frames"][destination.name] = {
            "sha256": digest,
            "intent": scenario.intent,
            "atSeconds": f"{scenario.at_seconds:.3f}",
        }
        print(f"wrote {destination.relative_to(REPOSITORY_ROOT)}")

    document = json.dumps(manifest, indent=2, sort_keys=True)
    MANIFEST_PATH.write_text(f"{document}\n", encoding="utf-8")
    print(f"wrote {MANIFEST_PATH.relative_to(REPOSITORY_ROOT)}")


if __name__ == "__main__":
    main()
