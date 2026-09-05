"""The golden-frame gate: a render has to keep looking like the frame it was signed off on.

Each scenario is compiled, rendered by the real FFmpeg on this machine, sampled at one
instant, and compared against a checked-in frame with structural similarity. The gate is
0.97, and the bands that type is drawn into are masked, because font rasterization is a
property of the machine rather than of the composition.

Two honest limits are enforced rather than hidden. A scenario whose filters this FFmpeg
build does not carry is skipped by name — `subtitles` and `drawtext` need libass and
libfreetype, which the pinned render image has and a developer machine may not. And a
scenario with no golden frame checked in yet is skipped as unmeasured rather than passed,
so an under-equipped run can never certify a picture nobody has looked at.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from clipah.editor.models import parse_composition
from clipah.models import AssetKind
from clipah.renders.compiler import compile_render_plan, input_path
from clipah.renders.ffmpeg_renderer import FFmpegRenderer
from clipah.renders.models import RenderAsset, RenderPreset
from golden_scenarios import GOLDEN_SCENARIOS, GoldenScenario
from perceptual import SSIM_THRESHOLD, extract_frame, read_frame, structural_similarity

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"
MEDIA_ROOT = FIXTURE_ROOT / "media"
GOLDEN_ROOT = FIXTURE_ROOT / "golden"
MANIFEST_PATH = GOLDEN_ROOT / "manifest.json"
LANDSCAPE = MEDIA_ROOT / "landscape.mp4"
SOURCE_ASSET_ID = "11111111-1111-4111-8111-111111111111"
BROLL_IMAGE_ID = "33333333-3333-4333-8333-333333333333"
BROLL_VIDEO_ID = "44444444-4444-4444-8444-444444444444"


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.parametrize(
    "scenario", GOLDEN_SCENARIOS, ids=[scenario.name for scenario in GOLDEN_SCENARIOS]
)
def test_each_scenario_still_renders_the_frame_it_was_signed_off_on(
    tmp_path: Path, scenario: GoldenScenario
) -> None:
    """A change that alters the picture has to be looked at, not merged quietly."""
    _require_media_tools()
    _require_filters(scenario)
    golden = _golden_path(scenario)
    if not golden.exists():
        pytest.skip(f"no golden frame is checked in for {scenario.name}")
    _verify_manifest(scenario, golden)

    rendered = _render(tmp_path, scenario)
    frame = extract_frame(
        rendered, tmp_path / f"{scenario.name}.png", at_seconds=scenario.at_seconds
    )

    width, height = 1080, 1920
    score = structural_similarity(
        read_frame(golden, width=width, height=height),
        read_frame(frame, width=width, height=height),
        masked=scenario.masked,
    )

    assert score >= SSIM_THRESHOLD, (
        f"{scenario.name} scored {score:.4f} against its golden frame, below {SSIM_THRESHOLD}"
    )


@pytest.mark.integration
@pytest.mark.slow
def test_the_gate_notices_when_a_render_stops_matching_its_golden_frame(tmp_path: Path) -> None:
    """A gate nobody has watched fail is a gate nobody should trust."""
    _require_media_tools()
    scenario = next(entry for entry in GOLDEN_SCENARIOS if entry.name == "plain")
    golden = _golden_path(scenario)
    if not golden.exists():
        pytest.skip("no golden frame is checked in for plain")

    altered = next(entry for entry in GOLDEN_SCENARIOS if entry.name == "smart-crop")
    rendered = _render(tmp_path, altered)
    frame = extract_frame(rendered, tmp_path / "altered.png", at_seconds=altered.at_seconds)

    score = structural_similarity(
        read_frame(golden, width=1080, height=1920),
        read_frame(frame, width=1080, height=1920),
    )

    assert score < SSIM_THRESHOLD


def _render(tmp_path: Path, scenario: GoldenScenario) -> Path:
    """Compile and render one scenario into its own Job workspace."""
    workspace = tmp_path / scenario.name
    (workspace / "inputs").mkdir(parents=True)
    assets = _stage_inputs(workspace, scenario, tmp_path)
    plan = compile_render_plan(
        parse_composition(scenario.document),
        assets=assets,
        preset=RenderPreset.PORTRAIT,
        workspace=workspace,
    )
    output = FFmpegRenderer(duration_probe=_probe_duration_ms).render(
        plan,
        workspace=workspace,
        cancellation_check=lambda: None,
        progress=lambda _ratio: None,
    )
    return output.path


def _stage_inputs(
    workspace: Path, scenario: GoldenScenario, tmp_path: Path
) -> dict[str, RenderAsset]:
    """Copy the media one scenario reads into the Job workspace it reads it from."""
    from uuid import UUID

    source_id = UUID(SOURCE_ASSET_ID)
    assets = {
        source_id: RenderAsset(
            asset_id=source_id,
            kind=AssetKind.SOURCE,
            content_type="video/mp4",
            duration_ms=_probe_duration_ms(LANDSCAPE),
            width=640,
            height=360,
        )
    }
    shutil.copy(LANDSCAPE, input_path(workspace, source_id))
    if scenario.needs_image:
        image_id = UUID(BROLL_IMAGE_ID)
        still = tmp_path / f"{scenario.name}-still.png"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=640x360:rate=1:duration=1",
                "-frames:v",
                "1",
                str(still),
            ],
            check=True,
            capture_output=True,
        )
        shutil.copy(still, input_path(workspace, image_id))
        assets[image_id] = RenderAsset(
            asset_id=image_id,
            kind=AssetKind.SOURCE,
            content_type="image/png",
            duration_ms=None,
            width=640,
            height=360,
        )
    if scenario.needs_broll_video:
        video_id = UUID(BROLL_VIDEO_ID)
        clip = tmp_path / f"{scenario.name}-broll.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "smptebars=size=640x360:rate=25:duration=1",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(clip),
            ],
            check=True,
            capture_output=True,
        )
        shutil.copy(clip, input_path(workspace, video_id))
        assets[video_id] = RenderAsset(
            asset_id=video_id,
            kind=AssetKind.BROLL,
            content_type="video/mp4",
            duration_ms=1_000,
            width=640,
            height=360,
        )
    return assets  # type: ignore[return-value]


def _golden_path(scenario: GoldenScenario) -> Path:
    """Where one scenario's signed-off frame lives."""
    return GOLDEN_ROOT / f"{scenario.name}.png"


def _verify_manifest(scenario: GoldenScenario, golden: Path) -> None:
    """Refuse a golden frame whose bytes do not match the manifest that recorded it."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    recorded = manifest["frames"].get(f"{scenario.name}.png")
    assert recorded is not None, f"{scenario.name} has a golden frame but no manifest entry"
    digest = hashlib.sha256(golden.read_bytes()).hexdigest()
    assert digest == recorded["sha256"], f"{scenario.name}.png does not match the manifest"


def _require_media_tools() -> None:
    """Skip when this machine has no FFmpeg to render with."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for the golden-frame gate")


def _require_filters(scenario: GoldenScenario) -> None:
    """Skip a scenario this FFmpeg build cannot draw, naming the filter it lacks."""
    if not scenario.required_filters:
        return
    available = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"], check=True, capture_output=True, text=True
    ).stdout
    for name in scenario.required_filters:
        if f" {name} " not in available:
            pytest.skip(f"this FFmpeg build has no {name} filter")


def _probe_duration_ms(path: Path) -> int:
    """Read one media file's duration, in whole milliseconds."""
    probed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return round(float(probed) * 1000)
