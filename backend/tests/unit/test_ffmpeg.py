"""Unit contracts for safe, deterministic ffprobe and FFmpeg invocation."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

import clipah.jobs.ingest_task as ingest_task
import clipah.jobs.render_task as render_task
import clipah.jobs.tasks as job_tasks
from clipah.assets.ffmpeg import (
    FFMPEG_FAILED,
    FFPROBE_FAILED,
    MEDIA_PROCESS_TIMEOUT,
    MEDIA_TOOL_VERSION_UNSUPPORTED,
    CommandExecutor,
    FFmpegRunner,
    MediaProcessError,
    SubprocessExecutor,
    parse_progress,
)
from clipah.assets.probe import MediaValidationError


class RecordingExecutor(CommandExecutor):
    """Record complete argument arrays while returning realistic ffprobe JSON."""

    def __init__(self) -> None:
        """Start with no external commands observed."""
        self.calls: list[tuple[tuple[str, ...], float, int | None]] = []

    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
        cancellation_check: Callable[[], None],
        progress_duration_ms: int | None = None,
        progress: Callable[[float], None] | None = None,
    ) -> bytes:
        """Return complete probe output and materialize requested FFmpeg outputs."""
        cancellation_check()
        self.calls.append((tuple(arguments), timeout_seconds, progress_duration_ms))
        if arguments[-1] == "-version":
            return f"{Path(arguments[0]).name} version 7.1.5-0+deb13u1\n".encode()
        if "-show_streams" in arguments:
            return json.dumps(
                {
                    "format": {"duration": "10.5"},
                    "streams": [
                        {
                            "codec_type": "video",
                            "codec_name": "h264",
                            "width": 1920,
                            "height": 1080,
                            "avg_frame_rate": "30/1",
                            "r_frame_rate": "30/1",
                        },
                        {"codec_type": "audio", "codec_name": "aac"},
                    ],
                }
            ).encode()
        if progress is not None:
            progress(1.0)
        Path(arguments[-1]).write_bytes(b"complete")
        return b""


class UnsupportedVersionExecutor(RecordingExecutor):
    """Report a complete but unapproved media tool version."""

    def run(self, arguments: Sequence[str], **kwargs: object) -> bytes:
        """Return an older version while retaining the recording behavior contract."""
        del kwargs
        self.calls.append((tuple(arguments), 1.0, None))
        return f"{Path(arguments[0]).name} version 7.0.0\n".encode()


class FixedOutputExecutor(CommandExecutor):
    """Return one fixed stdout value or sanitized process failure."""

    def __init__(self, *, output: bytes = b"", error: MediaProcessError | None = None) -> None:
        """Select the exact boundary outcome returned by every call."""
        self.output = output
        self.error = error

    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
        cancellation_check: Callable[[], None],
        progress_duration_ms: int | None = None,
        progress: Callable[[float], None] | None = None,
    ) -> bytes:
        """Return the configured result after exercising cancellation."""
        del arguments, timeout_seconds, progress_duration_ms, progress
        cancellation_check()
        if self.error is not None:
            raise self.error
        return self.output


@pytest.mark.unit
def test_parse_progress_emits_monotonic_clamped_ratios_from_fragmented_records() -> None:
    """Malformed or regressing provider progress must not regress durable Job progress."""
    observed = parse_progress(
        [
            "out_time_us=1000000\n",
            "progress=continue\n",
            "out_time_us=not-a-number\n",
            "progress=continue\n",
            "out_time_us=500000\n",
            "progress=continue\n",
            "out_time_us=12000000\n",
            "progress=end\n",
        ],
        duration_ms=10_000,
    )

    assert observed == (0.1, 1.0)


@pytest.mark.unit
def test_runner_requires_the_pinned_ffmpeg_and_ffprobe_version() -> None:
    """A worker must fail readiness before processing media with an unreviewed toolchain."""
    executor = RecordingExecutor()
    runner = FFmpegRunner(
        executor=executor, ffmpeg_path="/tools/ffmpeg", ffprobe_path="/tools/ffprobe"
    )

    runner.validate_versions()

    assert [call[0] for call in executor.calls] == [
        ("/tools/ffmpeg", "-version"),
        ("/tools/ffprobe", "-version"),
    ]


@pytest.mark.unit
def test_runner_rejects_an_unapproved_media_tool_version_without_raw_output() -> None:
    """Version diagnostics must not become a public or durable Job error message."""
    runner = FFmpegRunner(executor=UnsupportedVersionExecutor())

    with pytest.raises(MediaProcessError) as captured:
        runner.validate_versions()

    assert captured.value.code == MEDIA_TOOL_VERSION_UNSUPPORTED
    assert str(captured.value) == MEDIA_TOOL_VERSION_UNSUPPORTED


@pytest.mark.unit
def test_ingest_readiness_validates_media_versions_and_native_libmagic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ingest worker must prove both native boundaries before accepting work."""
    calls: list[object] = []
    monkeypatch.setattr(ingest_task, "validate_media_runtime", lambda: calls.append("ffmpeg"))
    monkeypatch.setattr(
        ingest_task,
        "sniff_mime",
        lambda path: calls.append(path) or "text/plain",
    )

    ingest_task.validate_ingest_readiness()

    assert calls == ["ffmpeg", Path(ingest_task.__file__)]


@pytest.mark.unit
def test_render_readiness_validates_complete_media_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A render worker must prove filters, encoders, and fonts before accepting work."""
    calls: list[str] = []
    monkeypatch.setattr(render_task, "validate_media_runtime", lambda: calls.append("media"))

    render_task.validate_render_readiness()

    assert calls == ["media"]


@pytest.mark.unit
def test_worker_startup_runs_only_readiness_owned_by_selected_media_queues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source-import and non-media workers must not inherit unrelated native dependencies."""
    calls: list[str] = []
    monkeypatch.setattr(job_tasks, "validate_ingest_readiness", lambda: calls.append("ingest"))
    monkeypatch.setattr(job_tasks, "validate_render_readiness", lambda: calls.append("render"))

    class Queue:
        """Represent the named queue objects Celery may pass to its startup signal."""

        name = "ingest"

    job_tasks._validate_ingest_worker_startup(options={"queues": "source_import"})
    job_tasks._validate_ingest_worker_startup(options={"queues": "source_import,ingest"})
    job_tasks._validate_ingest_worker_startup(options={"queues": "render"})
    job_tasks._validate_ingest_worker_startup(options={"queues": "social_rendition"})
    job_tasks._validate_ingest_worker_startup(options={"queues": "ai"})
    job_tasks._validate_ingest_worker_startup(options={"queues": [Queue()]})
    job_tasks._validate_ingest_worker_startup(options={})

    assert calls == ["ingest", "render", "render", "ingest", "ingest"]


@pytest.mark.unit
def test_probe_maps_process_and_json_failures_to_sanitized_media_codes(tmp_path: Path) -> None:
    """Neither ffprobe diagnostics nor malformed stdout may escape the adapter boundary."""
    source = tmp_path / "source"
    source.write_bytes(b"media")
    failed = FFmpegRunner(
        executor=FixedOutputExecutor(
            error=MediaProcessError(FFMPEG_FAILED, diagnostics=b"private path")
        )
    )

    with pytest.raises(MediaProcessError) as process_error:
        failed.probe(source, cancellation_check=lambda: None)

    assert process_error.value.code == FFPROBE_FAILED
    assert str(process_error.value) == FFPROBE_FAILED
    assert process_error.value.diagnostics == b"private path"

    malformed = FFmpegRunner(executor=FixedOutputExecutor(output=b"not-json"))
    with pytest.raises(MediaValidationError, match=r"^ASSET_INVALID_MEDIA$"):
        malformed.probe(source, cancellation_check=lambda: None)


@pytest.mark.unit
def test_runner_builds_explicit_probe_proxy_thumbnail_and_audio_commands(tmp_path: Path) -> None:
    """A command regression must never change stream mapping or safe output properties silently."""
    executor = RecordingExecutor()
    runner = FFmpegRunner(
        executor=executor, ffmpeg_path="/tools/ffmpeg", ffprobe_path="/tools/ffprobe"
    )
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")

    metadata = runner.probe(source, cancellation_check=lambda: None)
    runner.generate_proxy(
        source,
        tmp_path / "proxy.mp4",
        duration_ms=metadata.duration_ms,
        cancellation_check=lambda: None,
        progress=lambda _: None,
    )
    runner.generate_thumbnail(
        source,
        tmp_path / "thumbnail.jpg",
        cancellation_check=lambda: None,
    )
    runner.generate_transcription_audio(
        source,
        tmp_path / "transcription.wav",
        duration_ms=metadata.duration_ms,
        cancellation_check=lambda: None,
        progress=lambda _: None,
    )

    probe, proxy, thumbnail, audio = (call[0] for call in executor.calls)
    assert probe == (
        "/tools/ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(source),
    )
    assert proxy[0:6] == ("/tools/ffmpeg", "-nostdin", "-v", "error", "-i", str(source))
    assert proxy[6:10] == ("-map", "0:v:0", "-map", "0:a:0")
    assert "force_original_aspect_ratio=decrease" in proxy[proxy.index("-vf") + 1]
    assert proxy[proxy.index("-c:v") : proxy.index("-c:v") + 2] == ("-c:v", "libx264")
    assert proxy[proxy.index("-pix_fmt") : proxy.index("-pix_fmt") + 2] == ("-pix_fmt", "yuv420p")
    assert proxy[proxy.index("-movflags") : proxy.index("-movflags") + 2] == (
        "-movflags",
        "+faststart",
    )
    assert proxy[proxy.index("-progress") : proxy.index("-progress") + 2] == ("-progress", "pipe:1")
    assert thumbnail[thumbnail.index("-frames:v") + 1] == "1"
    assert thumbnail[-1].endswith("thumbnail.jpg")
    assert audio[6:13] == ("-vn", "-map", "0:a:0", "-ac", "1", "-ar", "16000")
    assert audio[audio.index("-c:a") : audio.index("-c:a") + 2] == ("-c:a", "pcm_s16le")
    assert all(call[1] > 0 for call in executor.calls)
    assert executor.calls[1][2] == metadata.duration_ms
    assert executor.calls[3][2] == metadata.duration_ms


@pytest.mark.unit
def test_subprocess_executor_returns_stdout_without_a_shell() -> None:
    """The process boundary must execute one exact argument vector and return bounded output."""
    output = SubprocessExecutor().run(
        [sys.executable, "-c", "print('safe-output')"],
        timeout_seconds=2,
        cancellation_check=lambda: None,
    )

    assert output == b"safe-output\n"


@pytest.mark.unit
def test_subprocess_executor_sanitizes_nonzero_exit_and_bounds_diagnostics() -> None:
    """Provider stderr may aid internal diagnosis but must never enter the exception message."""
    command = [
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('x' * 70000); raise SystemExit(7)",
    ]

    with pytest.raises(MediaProcessError) as captured:
        SubprocessExecutor().run(
            command,
            timeout_seconds=2,
            cancellation_check=lambda: None,
        )

    assert captured.value.code == FFMPEG_FAILED
    assert str(captured.value) == FFMPEG_FAILED
    assert len(captured.value.diagnostics) == 65_536


@pytest.mark.unit
def test_subprocess_executor_terminates_a_timed_out_process_group() -> None:
    """A hung media process must not survive its explicit execution deadline."""
    with pytest.raises(MediaProcessError) as captured:
        SubprocessExecutor(poll_seconds=0.01).run(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout_seconds=0.05,
            cancellation_check=lambda: None,
        )

    assert captured.value.code == MEDIA_PROCESS_TIMEOUT
    assert str(captured.value) == MEDIA_PROCESS_TIMEOUT


@pytest.mark.unit
def test_subprocess_executor_terminates_then_reraises_cancellation() -> None:
    """Cancellation must keep its domain type after the child process group is reaped."""

    class PlannedCancellationError(Exception):
        """Signal the deterministic cancellation point used by this test."""

    checks = 0

    def cancel() -> None:
        """Cancel after the process has entered its polling loop."""
        nonlocal checks
        checks += 1
        if checks == 2:
            raise PlannedCancellationError

    with pytest.raises(PlannedCancellationError):
        SubprocessExecutor(poll_seconds=0.01).run(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout_seconds=2,
            cancellation_check=cancel,
        )

    assert checks == 2
