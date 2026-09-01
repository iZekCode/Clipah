"""Run ffprobe and FFmpeg with bounded output, cancellation, and sanitized failures."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import BinaryIO, Protocol, cast

from clipah.assets.probe import MediaValidationError, SourceMetadata, parse_probe

FFPROBE_FAILED = "FFPROBE_FAILED"
FFMPEG_FAILED = "FFMPEG_FAILED"
MEDIA_PROCESS_TIMEOUT = "MEDIA_PROCESS_TIMEOUT"
MEDIA_TOOL_VERSION_UNSUPPORTED = "MEDIA_TOOL_VERSION_UNSUPPORTED"
REQUIRED_MEDIA_TOOL_VERSION = "7.1.5"
MAX_STDERR_BYTES = 65_536
MAX_STDOUT_BYTES = 8 * 1024 * 1024
PROBE_TIMEOUT_SECONDS = 30.0
FFMPEG_TIMEOUT_SECONDS = 6 * 60 * 60.0
PROCESS_STOP_GRACE_SECONDS = 2.0

CancellationCheck = Callable[[], None]
ProgressCallback = Callable[[float], None]


class MediaProcessError(Exception):
    """A sanitized process failure with bounded internal-only diagnostics."""

    def __init__(self, code: str, *, diagnostics: bytes = b"") -> None:
        """Expose a stable code while retaining only a bounded diagnostic suffix."""
        super().__init__(code)
        self.code = code
        self.diagnostics = diagnostics[-MAX_STDERR_BYTES:]


class CommandExecutor(Protocol):
    """Execute one exact media command without exposing process mechanics to callers."""

    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
        cancellation_check: CancellationCheck,
        progress_duration_ms: int | None = None,
        progress: ProgressCallback | None = None,
    ) -> bytes:
        """Return bounded stdout or raise a sanitized process or cancellation error."""
        ...


class SubprocessExecutor:
    """Run an argument vector in its own cancellable process group."""

    def __init__(
        self,
        *,
        poll_seconds: float = 0.05,
        stop_grace_seconds: float = PROCESS_STOP_GRACE_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Configure bounded polling and shutdown timing with an injectable clock."""
        if poll_seconds <= 0 or stop_grace_seconds <= 0:
            raise ValueError("process timing values must be positive")
        self._poll_seconds = poll_seconds
        self._stop_grace_seconds = stop_grace_seconds
        self._monotonic = monotonic

    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
        cancellation_check: CancellationCheck,
        progress_duration_ms: int | None = None,
        progress: ProgressCallback | None = None,
    ) -> bytes:
        """Drain both pipes continuously and stop the entire group on timeout or cancellation."""
        if not arguments or timeout_seconds <= 0:
            raise ValueError("a command and positive timeout are required")
        process = subprocess.Popen(
            list(arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        if process.stdout is None or process.stderr is None:
            _stop_process_group(process, grace_seconds=self._stop_grace_seconds)
            raise MediaProcessError(FFMPEG_FAILED)

        stdout = bytearray()
        stderr = bytearray()
        stdout_overflow = threading.Event()
        reader_errors: list[BaseException] = []
        tracker = _ProgressTracker(duration_ms=progress_duration_ms, callback=progress)
        stdout_thread = threading.Thread(
            target=_read_stdout,
            args=(process.stdout, stdout, stdout_overflow, tracker, reader_errors),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_read_stderr,
            args=(process.stderr, stderr, reader_errors),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        started_at = self._monotonic()
        try:
            while process.poll() is None:
                cancellation_check()
                if self._monotonic() - started_at >= timeout_seconds:
                    raise MediaProcessError(MEDIA_PROCESS_TIMEOUT, diagnostics=bytes(stderr))
                time.sleep(self._poll_seconds)
        except BaseException:
            _stop_process_group(process, grace_seconds=self._stop_grace_seconds)
            raise
        finally:
            stdout_thread.join(timeout=self._stop_grace_seconds)
            stderr_thread.join(timeout=self._stop_grace_seconds)

        if reader_errors or stdout_overflow.is_set():
            raise MediaProcessError(FFMPEG_FAILED, diagnostics=bytes(stderr))
        if process.returncode != 0:
            raise MediaProcessError(FFMPEG_FAILED, diagnostics=bytes(stderr))
        return bytes(stdout)


class FFmpegRunner:
    """Build deterministic media commands on top of the safe executor boundary."""

    def __init__(
        self,
        *,
        executor: CommandExecutor | None = None,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        probe_timeout_seconds: float = PROBE_TIMEOUT_SECONDS,
        ffmpeg_timeout_seconds: float = FFMPEG_TIMEOUT_SECONDS,
    ) -> None:
        """Bind executable identities and explicit deadlines for one worker."""
        self._executor = executor or SubprocessExecutor()
        self._ffmpeg_path = ffmpeg_path
        self._ffprobe_path = ffprobe_path
        self._probe_timeout_seconds = probe_timeout_seconds
        self._ffmpeg_timeout_seconds = ffmpeg_timeout_seconds

    def validate_versions(self) -> None:
        """Fail readiness unless both executables report the reviewed pinned version."""
        for executable, tool in (
            (self._ffmpeg_path, "ffmpeg"),
            (self._ffprobe_path, "ffprobe"),
        ):
            output = self._executor.run(
                (executable, "-version"),
                timeout_seconds=self._probe_timeout_seconds,
                cancellation_check=lambda: None,
            )
            expected = f"{tool} version {REQUIRED_MEDIA_TOOL_VERSION}"
            first_line = output.splitlines()[0] if output.splitlines() else b""
            if not first_line.decode(errors="replace").startswith(expected):
                raise MediaProcessError(
                    MEDIA_TOOL_VERSION_UNSUPPORTED,
                    diagnostics=first_line,
                )

    def probe(self, source: Path, *, cancellation_check: CancellationCheck) -> SourceMetadata:
        """Inspect one local source and return validated-shape metadata without policy checks."""
        arguments = (
            self._ffprobe_path,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(source),
        )
        try:
            output = self._executor.run(
                arguments,
                timeout_seconds=self._probe_timeout_seconds,
                cancellation_check=cancellation_check,
            )
        except MediaProcessError as error:
            if error.code == MEDIA_PROCESS_TIMEOUT:
                raise
            raise MediaProcessError(FFPROBE_FAILED, diagnostics=error.diagnostics) from error
        try:
            payload = json.loads(output)
            if not isinstance(payload, Mapping):
                raise TypeError
            return parse_probe(cast(Mapping[str, object], payload))
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as error:
            raise MediaValidationError("ASSET_INVALID_MEDIA") from error

    def generate_proxy(
        self,
        source: Path,
        output: Path,
        *,
        duration_ms: int,
        cancellation_check: CancellationCheck,
        progress: ProgressCallback,
    ) -> None:
        """Create an H.264/AAC fast-start proxy bounded to 720p without upscaling."""
        scale = (
            "scale="
            "w='if(gte(iw,ih),min(iw,1280),min(iw,720))':"
            "h='if(gte(iw,ih),min(ih,720),min(ih,1280))':"
            "force_original_aspect_ratio=decrease:force_divisible_by=2"
        )
        arguments = (
            self._ffmpeg_path,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-vf",
            scale,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
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
        self._executor.run(
            arguments,
            timeout_seconds=self._ffmpeg_timeout_seconds,
            cancellation_check=cancellation_check,
            progress_duration_ms=duration_ms,
            progress=progress,
        )

    def generate_thumbnail(
        self, source: Path, output: Path, *, cancellation_check: CancellationCheck
    ) -> None:
        """Create one bounded JPEG preview frame from the source's first decodable frame."""
        scale = (
            "scale="
            "w='if(gte(iw,ih),min(iw,1280),min(iw,720))':"
            "h='if(gte(iw,ih),min(ih,720),min(ih,1280))':"
            "force_original_aspect_ratio=decrease:force_divisible_by=2"
        )
        arguments = (
            self._ffmpeg_path,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-vf",
            scale,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-map_metadata",
            "-1",
            "-progress",
            "pipe:1",
            "-nostats",
            "-y",
            str(output),
        )
        self._executor.run(
            arguments,
            timeout_seconds=self._ffmpeg_timeout_seconds,
            cancellation_check=cancellation_check,
        )

    def generate_transcription_audio(
        self,
        source: Path,
        output: Path,
        *,
        duration_ms: int,
        cancellation_check: CancellationCheck,
        progress: ProgressCallback,
    ) -> None:
        """Create one lossless mono 16 kHz WAV stream for transcription."""
        arguments = (
            self._ffmpeg_path,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(source),
            "-vn",
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-map_metadata",
            "-1",
            "-progress",
            "pipe:1",
            "-nostats",
            "-y",
            str(output),
        )
        self._executor.run(
            arguments,
            timeout_seconds=self._ffmpeg_timeout_seconds,
            cancellation_check=cancellation_check,
            progress_duration_ms=duration_ms,
            progress=progress,
        )


def parse_progress(lines: Iterable[str], *, duration_ms: int) -> tuple[float, ...]:
    """Normalize FFmpeg progress records into strictly increasing bounded ratios."""
    observed: list[float] = []
    tracker = _ProgressTracker(duration_ms=duration_ms, callback=observed.append)
    for line in lines:
        tracker.feed(line)
    return tuple(observed)


class _ProgressTracker:
    """Accumulate FFmpeg key-value records until each progress delimiter arrives."""

    def __init__(self, *, duration_ms: int | None, callback: ProgressCallback | None) -> None:
        """Track one duration and emit only meaningful monotonic observations."""
        self._duration_us = duration_ms * 1000 if duration_ms is not None else None
        self._callback = callback
        self._out_time_us: int | None = None
        self._last = 0.0

    def feed(self, raw_line: str) -> None:
        """Consume one decoded progress line while ignoring malformed provider values."""
        key, separator, value = raw_line.strip().partition("=")
        if not separator:
            return
        if key == "out_time_us":
            try:
                parsed = int(value)
            except ValueError:
                self._out_time_us = None
            else:
                self._out_time_us = max(parsed, 0)
            return
        if key != "progress" or self._callback is None or self._duration_us is None:
            return
        if value == "end":
            candidate = 1.0
        elif self._out_time_us is None or self._duration_us <= 0:
            return
        else:
            candidate = min(self._out_time_us / self._duration_us, 1.0)
        if candidate > self._last:
            self._last = candidate
            self._callback(candidate)


def _read_stdout(
    pipe: BinaryIO,
    output: bytearray,
    overflow: threading.Event,
    tracker: _ProgressTracker,
    errors: list[BaseException],
) -> None:
    """Drain process stdout while bounding retained bytes and parsing progress lines."""
    try:
        while True:
            line = pipe.readline()
            if not line:
                break
            if len(output) + len(line) > MAX_STDOUT_BYTES:
                overflow.set()
            elif not overflow.is_set():
                output.extend(line)
            tracker.feed(line.decode(errors="replace"))
    except BaseException as error:
        errors.append(error)


def _read_stderr(pipe: BinaryIO, output: bytearray, errors: list[BaseException]) -> None:
    """Drain process stderr continuously while retaining only its bounded suffix."""
    try:
        while True:
            chunk = pipe.read(8192)
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > MAX_STDERR_BYTES:
                del output[:-MAX_STDERR_BYTES]
    except BaseException as error:
        errors.append(error)


def _stop_process_group(process: subprocess.Popen[bytes], *, grace_seconds: float) -> None:
    """Terminate one isolated process group and escalate to kill after a bounded grace."""
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        return
