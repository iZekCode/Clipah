"""Public-only yt-dlp adapter with bounded, shell-free provider execution."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import BinaryIO, Protocol, cast
from urllib.parse import urljoin

import httpx

from clipah.assets.source_validation import (
    MAX_SOURCE_REDIRECTS,
    Resolver,
    revalidate_youtube_url,
    validate_redirect_chain,
)
from clipah.assets.storage import ObjectStore, StoredObject
from clipah.assets.youtube import (
    MAX_SOURCE_DURATION_SECONDS,
    MAX_SOURCE_SIZE_BYTES,
    NormalizedYouTubeUrl,
    SourceMetadata,
    SourcePrivateError,
    SourceTlsError,
    SourceTooLongError,
    SourceUnavailableError,
    SourceUnsupportedError,
)

COMMAND_TIMEOUT_SECONDS = 15 * 60
PREFLIGHT_TIMEOUT_SECONDS = 15.0
MAX_COMMAND_OUTPUT_CHARS = 1_048_576
YT_DLP_COMMAND = "yt-dlp"
_EXTENSION_PATTERN = re.compile(r"^[a-z0-9]{1,8}$", flags=re.ASCII)
_PRIVATE_FAILURE_MARKERS = (
    "private video",
    "sign in to confirm your age",
)
_UNSUPPORTED_FAILURE_MARKERS = ("members-only", "subscriber only")
_TLS_FAILURE_MARKERS = (
    "certificate verify failed",
    "certificate_verify_failed",
    "tlsv1 alert",
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Bounded provider command output with no subprocess type escaping the adapter."""

    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    """Execute one argument array inside a selected job workspace."""

    def run(self, arguments: Sequence[str], *, cwd: Path, timeout: float) -> CommandResult:
        """Run without a shell and return bounded text output."""


class SourcePreflight(Protocol):
    """Expose only the validated source-navigation redirects preceding extraction."""

    def redirect_chain(self, source: NormalizedYouTubeUrl) -> tuple[str, ...]:
        """Follow a bounded HTTPS source request without consuming its response body."""


class SubprocessCommandRunner:
    """Production runner that excludes ambient secrets and shell interpretation."""

    def run(self, arguments: Sequence[str], *, cwd: Path, timeout: float) -> CommandResult:
        """Execute one provider command in a new process group with sanitized output."""
        environment = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "DENO_DIR": str(cwd / ".deno-cache"),
        }
        process = subprocess.Popen(
            list(arguments),
            cwd=cwd,
            env=environment,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        stdout = bytearray()
        stderr = bytearray()
        drainers = (
            threading.Thread(target=_drain_bounded, args=(process.stdout, stdout), daemon=True),
            threading.Thread(target=_drain_bounded, args=(process.stderr, stderr), daemon=True),
        )
        for drainer in drainers:
            drainer.start()
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            for drainer in drainers:
                drainer.join()
            raise
        for drainer in drainers:
            drainer.join()
        return CommandResult(
            returncode=returncode,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
        )


def _drain_bounded(stream: BinaryIO, retained: bytearray) -> None:
    """Drain a process pipe continuously while retaining only the configured prefix."""
    while chunk := stream.read(65_536):
        remaining = MAX_COMMAND_OUTPUT_CHARS - len(retained)
        if remaining > 0:
            retained.extend(chunk[:remaining])


class HttpxSourcePreflight:
    """Verified-TLS source navigator that validates each redirect before following it."""

    def __init__(self, *, resolver: Resolver, timeout: float = PREFLIGHT_TIMEOUT_SECONDS) -> None:
        """Bind redirect DNS policy and the small source-navigation timeout."""
        self._resolver = resolver
        self._timeout = timeout

    def redirect_chain(self, source: NormalizedYouTubeUrl) -> tuple[str, ...]:
        """Return safe redirects, closing streamed responses without reading their bodies."""
        redirects: list[str] = []
        current = source.canonical_url
        try:
            with httpx.Client(follow_redirects=False, verify=True, timeout=self._timeout) as client:
                for _ in range(MAX_SOURCE_REDIRECTS + 1):
                    with client.stream("GET", current) as response:
                        location = response.headers.get("location")
                        if response.status_code not in {301, 302, 303, 307, 308} or not location:
                            return tuple(redirects)
                    if len(redirects) == MAX_SOURCE_REDIRECTS:
                        raise SourceUnsupportedError
                    current = urljoin(current, location)
                    redirects.append(current)
                    validate_redirect_chain(source, redirects, resolver=self._resolver)
        except SourceUnsupportedError:
            raise
        except httpx.TransportError as error:
            raise _transport_error(error) from None
        raise SourceUnsupportedError


class YtDlpSourceImporter:
    """Import one normalized public YouTube video into an exact private object key."""

    def __init__(
        self,
        *,
        store: ObjectStore,
        runner: CommandRunner,
        preflight: SourcePreflight,
        resolver: Resolver,
        command: str = YT_DLP_COMMAND,
        timeout: float = COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        """Bind provider execution to injected process, network, DNS, and storage boundaries."""
        self._store = store
        self._runner = runner
        self._preflight = preflight
        self._resolver = resolver
        self._command = command
        self._timeout = timeout

    def import_source(
        self,
        source: NormalizedYouTubeUrl,
        *,
        workspace: Path,
        object_key: str,
        cancellation_check: Callable[[], None],
    ) -> StoredObject:
        """Preflight, download, constrain, hash, and store one public source."""
        cancellation_check()
        redirects = self._preflight.redirect_chain(source)
        validate_redirect_chain(source, redirects, resolver=self._resolver)
        revalidate_youtube_url(source, resolver=self._resolver)

        cancellation_check()
        metadata = self._metadata(source, workspace=workspace)
        revalidate_youtube_url(source, resolver=self._resolver)

        cancellation_check()
        downloaded = self._download(source, workspace=workspace)
        if downloaded.stat().st_size <= 0 or downloaded.stat().st_size > MAX_SOURCE_SIZE_BYTES:
            raise SourceUnsupportedError

        cancellation_check()
        with downloaded.open("rb") as source_file:
            hashing_file = _HashingReader(source_file)
            stored = self._store.put_file(
                key=object_key,
                content_type=metadata.content_type,
                file=cast(BinaryIO, hashing_file),
            )
        return replace(
            stored,
            sha256=hashing_file.digest(),
            duration_ms=metadata.duration_seconds * 1000,
        )

    def _metadata(self, source: NormalizedYouTubeUrl, *, workspace: Path) -> SourceMetadata:
        """Run one download-free extraction and normalize its strict public-media subset."""
        result = self._run(
            (
                self._command,
                "--no-config",
                "--no-playlist",
                "--skip-download",
                "--dump-single-json",
                "--no-warnings",
                source.canonical_url,
            ),
            workspace=workspace,
        )
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as error:
            raise SourceUnsupportedError from error
        if not isinstance(payload, dict):
            raise SourceUnsupportedError
        return _normalize_metadata(payload, source=source)

    def _download(self, source: NormalizedYouTubeUrl, *, workspace: Path) -> Path:
        """Download into one fixed template and accept only a direct regular child path."""
        output_template = workspace / "source.%(ext)s"
        result = self._run(
            (
                self._command,
                "--no-config",
                "--no-playlist",
                "--max-filesize",
                str(MAX_SOURCE_SIZE_BYTES),
                "--no-warnings",
                "--print",
                "after_move:filepath",
                "--output",
                str(output_template),
                source.canonical_url,
            ),
            workspace=workspace,
        )
        lines = result.stdout.strip().splitlines()
        if len(lines) != 1:
            raise SourceUnsupportedError
        candidate = Path(lines[0])
        root = workspace.resolve(strict=True)
        if not candidate.is_absolute():
            candidate = workspace / candidate
        if candidate.is_symlink():
            raise SourceUnsupportedError
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            raise SourceUnsupportedError from None
        if resolved.parent != root or not resolved.is_file():
            raise SourceUnsupportedError
        final_files = tuple(
            item.resolve(strict=True)
            for item in workspace.iterdir()
            if item.is_file() and not item.is_symlink()
        )
        if final_files != (resolved,):
            raise SourceUnsupportedError
        return resolved

    def _run(self, arguments: Sequence[str], *, workspace: Path) -> CommandResult:
        """Translate process timeouts and nonzero exits into sanitized domain failures."""
        try:
            result = self._runner.run(arguments, cwd=workspace, timeout=self._timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise SourceUnavailableError from None
        if result.returncode != 0:
            raise _command_error(result.stderr)
        return result


class _HashingReader:
    """File-like reader that updates one SHA-256 digest as storage consumes it."""

    def __init__(self, source: BinaryIO) -> None:
        """Bind hashing to the exact stream consumed by object storage."""
        self._source = source
        self._hash = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        """Read source bytes once and add exactly those bytes to the digest."""
        chunk = self._source.read(size)
        self._hash.update(chunk)
        return chunk

    def digest(self) -> bytes:
        """Return the digest accumulated by the completed storage read."""
        return self._hash.digest()


def _normalize_metadata(
    payload: dict[str, object], *, source: NormalizedYouTubeUrl
) -> SourceMetadata:
    """Reduce provider JSON to one supported public, non-live, bounded video."""
    extractor = payload.get("extractor_key")
    if not isinstance(extractor, str) or extractor.lower() != "youtube":
        raise SourceUnsupportedError
    if payload.get("id") != source.video_id:
        raise SourceUnsupportedError
    if payload.get("_type") == "playlist" or payload.get("entries") is not None:
        raise SourceUnsupportedError

    availability = payload.get("availability")
    if availability in {"private", "premium_only", "needs_auth"}:
        raise SourcePrivateError
    if availability == "subscriber_only":
        raise SourceUnsupportedError
    if availability not in {None, "public"}:
        raise SourceUnsupportedError
    age_limit = payload.get("age_limit")
    if isinstance(age_limit, bool) or not isinstance(age_limit, (int, float)):
        raise SourceUnsupportedError
    if age_limit > 0:
        raise SourcePrivateError
    if payload.get("is_live") is not False or payload.get("live_status") != "not_live":
        raise SourceUnsupportedError

    duration = payload.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0:
        raise SourceUnsupportedError
    if duration > MAX_SOURCE_DURATION_SECONDS:
        raise SourceTooLongError
    extension = payload.get("ext")
    if not isinstance(extension, str) or _EXTENSION_PATTERN.fullmatch(extension) is None:
        raise SourceUnsupportedError
    return SourceMetadata(
        video_id=source.video_id,
        duration_seconds=int(duration),
        content_type=_content_type(extension),
        extension=extension,
    )


def _content_type(extension: str) -> str:
    """Map the small common video extension set without trusting provider MIME strings."""
    return {
        "mp4": "video/mp4",
        "webm": "video/webm",
        "mkv": "video/x-matroska",
        "mov": "video/quicktime",
    }.get(extension, "application/octet-stream")


def _command_error(stderr: str) -> Exception:
    """Classify only fixed provider markers and retain none of the inspected text."""
    normalized = stderr.lower()
    if any(marker in normalized for marker in _TLS_FAILURE_MARKERS):
        return SourceTlsError()
    if any(marker in normalized for marker in _PRIVATE_FAILURE_MARKERS):
        return SourcePrivateError()
    if any(marker in normalized for marker in _UNSUPPORTED_FAILURE_MARKERS):
        return SourceUnsupportedError()
    return SourceUnavailableError()


def _transport_error(error: httpx.TransportError) -> Exception:
    """Separate verified-TLS failures from other retryable source transport failures."""
    normalized = str(error).lower()
    if any(marker in normalized for marker in _TLS_FAILURE_MARKERS):
        return SourceTlsError()
    return SourceUnavailableError()
