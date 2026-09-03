"""Execute one render plan with FFmpeg, and prove the file it produced is the one asked for.

The plan decides what is rendered; this module only runs it. It writes the plan's text
files into the Job workspace, hands FFmpeg the filter graph as a script rather than as an
argument, and then re-reads the output with ffprobe: a file whose duration does not match
the composition is a failed render, not a delivered one.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

from clipah.assets.ffmpeg import (
    FFMPEG_TIMEOUT_SECONDS,
    CancellationCheck,
    CommandExecutor,
    ProgressCallback,
    SubprocessExecutor,
)
from clipah.assets.probe import MediaValidationError
from clipah.renders.models import (
    DURATION_MISMATCH,
    RENDER_AUDIO_SAMPLE_RATE,
    RENDER_DURATION_TOLERANCE_MS,
    RENDER_FAILED,
    RenderOutput,
    RenderPlan,
)

FILTER_SCRIPT_NAME = "filtergraph.txt"
# The pinned FFmpeg this renderer is written for reads a graph from a file through
# `-filter_complex_script`. FFmpeg 8 removed that spelling in favour of `-/filter_complex`,
# which reads the same file; the renderer picks whichever the executable in front of it
# understands, so a newer local build can still run a real export.
PINNED_SCRIPT_OPTION = "-filter_complex_script"
MODERN_SCRIPT_OPTION = "-/filter_complex"
FIRST_VERSION_WITHOUT_SCRIPT_OPTION = 8
OUTPUT_NAME = "render.mp4"
HASH_CHUNK_BYTES = 1024 * 1024

DurationProbe = Callable[[Path], int]


class RenderExecutionError(Exception):
    """A render that ran but did not produce the file the composition promised."""

    def __init__(self, code: str, detail: str) -> None:
        """Carry a stable public code and an internal-only explanation."""
        super().__init__(code)
        self.code = code
        self.detail = detail


def build_render_arguments(
    plan: RenderPlan,
    *,
    output: Path,
    ffmpeg_path: str,
    script_option: str = PINNED_SCRIPT_OPTION,
) -> tuple[str, ...]:
    """Build the exact argument vector one render runs as.

    Every member-supplied value reached a file during compilation, so nothing here can be
    anything but a fixed flag, a number this application computed, or a workspace path.
    """
    inputs: tuple[str, ...] = ()
    for entry in plan.inputs:
        inputs = (*inputs, *entry.arguments())
    return (
        ffmpeg_path,
        "-nostdin",
        "-v",
        "error",
        *inputs,
        script_option,
        str(output.parent / FILTER_SCRIPT_NAME),
        "-map",
        f"[{plan.video_label}]",
        "-map",
        f"[{plan.audio_label}]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-r",
        str(plan.frame_rate),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        str(RENDER_AUDIO_SAMPLE_RATE),
        "-ac",
        "2",
        "-t",
        f"{plan.duration_ms / 1000:.3f}",
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        "-progress",
        "pipe:1",
        "-nostats",
        "-y",
        str(output),
    )


class FFmpegRenderer:
    """Run one compiled plan in its own cancellable process group."""

    def __init__(
        self,
        *,
        executor: CommandExecutor | None = None,
        ffmpeg_path: str = "ffmpeg",
        duration_probe: DurationProbe | None = None,
        timeout_seconds: float = FFMPEG_TIMEOUT_SECONDS,
        script_option: str | None = None,
    ) -> None:
        """Bind the executable, the deadline, and the way an output is measured."""
        self._executor = executor or SubprocessExecutor()
        self._ffmpeg_path = ffmpeg_path
        self._duration_probe = duration_probe
        self._timeout_seconds = timeout_seconds
        self._script_option = script_option

    def render(
        self,
        plan: RenderPlan,
        *,
        workspace: Path,
        cancellation_check: CancellationCheck,
        progress: ProgressCallback,
    ) -> RenderOutput:
        """Write the plan's files, run it, and return the verified artifact it produced."""
        _write_plan_files(plan, workspace)
        output = workspace / OUTPUT_NAME
        (workspace / FILTER_SCRIPT_NAME).write_text(plan.filter_script, encoding="utf-8")
        arguments = build_render_arguments(
            plan,
            output=output,
            ffmpeg_path=self._ffmpeg_path,
            script_option=self._resolved_script_option(cancellation_check),
        )
        self._executor.run(
            arguments,
            timeout_seconds=self._timeout_seconds,
            cancellation_check=cancellation_check,
            progress_duration_ms=plan.duration_ms,
            progress=progress,
        )
        duration_ms = self._measure(output, cancellation_check=cancellation_check)
        if abs(duration_ms - plan.duration_ms) > RENDER_DURATION_TOLERANCE_MS:
            raise RenderExecutionError(
                DURATION_MISMATCH,
                f"rendered {duration_ms} ms against a planned {plan.duration_ms} ms",
            )
        return RenderOutput(
            path=output,
            duration_ms=duration_ms,
            size_bytes=output.stat().st_size,
            sha256=_digest(output),
        )

    def _resolved_script_option(self, cancellation_check: CancellationCheck) -> str:
        """Ask the executable in front of us how it wants a filter script handed over."""
        if self._script_option is None:
            self._script_option = (
                MODERN_SCRIPT_OPTION
                if self._major_version(cancellation_check) >= FIRST_VERSION_WITHOUT_SCRIPT_OPTION
                else PINNED_SCRIPT_OPTION
            )
        return self._script_option

    def _major_version(self, cancellation_check: CancellationCheck) -> int:
        """Read the executable's major version, treating anything unreadable as the pinned one."""
        output = self._executor.run(
            (self._ffmpeg_path, "-hide_banner", "-version"),
            timeout_seconds=30.0,
            cancellation_check=cancellation_check,
        )
        first = output.decode(errors="replace").splitlines()[0] if output else ""
        parts = first.split()
        try:
            return int(parts[2].split(".", 1)[0])
        except (IndexError, ValueError):
            return 0

    def _measure(self, output: Path, *, cancellation_check: CancellationCheck) -> int:
        """Read back the duration of the file that was just written."""
        if self._duration_probe is not None:
            return self._duration_probe(output)
        from clipah.assets.ffmpeg import FFmpegRunner

        try:
            metadata = FFmpegRunner(executor=self._executor, ffmpeg_path=self._ffmpeg_path).probe(
                output, cancellation_check=cancellation_check
            )
        except MediaValidationError as error:
            raise RenderExecutionError(
                DURATION_MISMATCH, "rendered output is unreadable"
            ) from error
        return metadata.duration_ms


def _write_plan_files(plan: RenderPlan, workspace: Path) -> None:
    """Write every text file the graph reads, inside the Job workspace and nowhere else."""
    for entry in plan.files:
        if not entry.path.is_relative_to(workspace):
            raise RenderExecutionError(RENDER_FAILED, "a plan named a path outside its Job")
        entry.path.parent.mkdir(parents=True, exist_ok=True)
        entry.path.write_text(entry.contents, encoding="utf-8")


def _digest(path: Path) -> bytes:
    """Hash one rendered file without holding it in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.digest()
