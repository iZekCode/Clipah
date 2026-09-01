"""Unit contracts for the isolated, public-only yt-dlp source adapter."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from clipah.assets.source_validation import validate_youtube_url
from clipah.assets.storage import FakeObjectStore
from clipah.assets.youtube import (
    MAX_SOURCE_SIZE_BYTES,
    SourcePrivateError,
    SourceTlsError,
    SourceTooLongError,
    SourceUnavailableError,
    SourceUnsupportedError,
)
from clipah.source_connectors.yt_dlp_adapter import (
    MAX_COMMAND_OUTPUT_CHARS,
    CommandResult,
    SubprocessCommandRunner,
    YtDlpSourceImporter,
)

VIDEO_ID = "dQw4w9WgXcQ"
PUBLIC_IP = "8.8.8.8"
OTHER_PUBLIC_IP = "1.1.1.1"
OBJECT_KEY = "workspaces/w/projects/p/source-imports/s/original"


@pytest.fixture
def clock() -> Callable[[], datetime]:
    """Return a stable timestamp supplier for the deterministic object-store fake."""
    return lambda: datetime(2026, 9, 1, tzinfo=UTC)


class RecordingRunner:
    """Return queued command results while retaining every provider argument list."""

    def __init__(self, *results: CommandResult) -> None:
        """Queue deterministic results and initialize an empty call ledger."""
        self.results = list(results)
        self.calls: list[tuple[tuple[str, ...], Path, float]] = []

    def run(self, arguments: Sequence[str], *, cwd: Path, timeout: float) -> CommandResult:
        """Record one shell-free command and return its next deterministic result."""
        self.calls.append((tuple(arguments), cwd, timeout))
        return self.results.pop(0)


class FakePreflight:
    """Expose a deterministic redirect chain without performing HTTP."""

    def __init__(self, redirects: tuple[str, ...] = ()) -> None:
        """Bind a deterministic safe or unsafe redirect chain."""
        self.redirects = redirects
        self.sources: list[str] = []

    def redirect_chain(self, source) -> tuple[str, ...]:
        """Record the normalized source and return configured hops."""
        self.sources.append(source.canonical_url)
        return self.redirects


def resolver_with(*addresses: str) -> Callable[[str], Iterable[str]]:
    """Return a resolver containing exactly the supplied address set."""
    return lambda _: addresses


@pytest.mark.unit
def test_production_runner_drains_but_never_retains_unbounded_output(tmp_path: Path) -> None:
    """A noisy provider must not make the worker retain unlimited stdout or stderr."""
    result = SubprocessCommandRunner().run(
        (
            sys.executable,
            "-c",
            "import sys; print('o' * 2000000); print('e' * 2000000, file=sys.stderr)",
        ),
        cwd=tmp_path,
        timeout=5,
    )

    assert result.returncode == 0
    assert len(result.stdout.encode()) <= MAX_COMMAND_OUTPUT_CHARS
    assert len(result.stderr.encode()) <= MAX_COMMAND_OUTPUT_CHARS


def metadata(**overrides: object) -> str:
    """Build one minimal public, non-live YouTube metadata document."""
    return json.dumps(
        {
            "id": VIDEO_ID,
            "extractor_key": "Youtube",
            "duration": 120,
            "availability": "public",
            "age_limit": 0,
            "is_live": False,
            "live_status": "not_live",
            "ext": "mp4",
            **overrides,
        }
    )


def result(stdout: str = "", stderr: str = "", returncode: int = 0) -> CommandResult:
    """Create one deterministic provider command result."""
    return CommandResult(returncode=returncode, stdout=stdout, stderr=stderr)


def importer(
    store: FakeObjectStore,
    runner: RecordingRunner,
    *,
    resolver: Callable[[str], Iterable[str]] | None = None,
    preflight: FakePreflight | None = None,
) -> YtDlpSourceImporter:
    """Compose the adapter entirely from deterministic local fakes."""
    return YtDlpSourceImporter(
        store=store,
        runner=runner,
        preflight=preflight or FakePreflight(),
        resolver=resolver or resolver_with(PUBLIC_IP),
    )


def source(*, resolver: Callable[[str], Iterable[str]] | None = None):
    """Create one normalized source with no public DNS dependency."""
    return validate_youtube_url(
        f"https://youtu.be/{VIDEO_ID}", resolver=resolver or resolver_with(PUBLIC_IP)
    )


@pytest.mark.unit
def test_import_uses_safe_metadata_and_download_arguments(tmp_path: Path, clock) -> None:
    """Provider invocation must explicitly disable ambient config and unsafe conveniences."""
    output = tmp_path / "source.mp4"
    output.write_bytes(b"video-bytes")
    runner = RecordingRunner(result(metadata()), result(str(output)))
    store = FakeObjectStore(now=clock)

    stored = importer(store, runner).import_source(
        source(), workspace=tmp_path, object_key=OBJECT_KEY, cancellation_check=lambda: None
    )

    metadata_args, download_args = (call[0] for call in runner.calls)
    for required in ("--no-config", "--no-playlist", "--skip-download", "--dump-single-json"):
        assert required in metadata_args
    for required in ("--no-config", "--no-playlist", "--max-filesize", str(MAX_SOURCE_SIZE_BYTES)):
        assert required in download_args
    forbidden = {
        "--no-check-certificates",
        "--cookies",
        "--cookies-from-browser",
        "--config-locations",
        "--update-to",
        "--exec",
    }
    assert forbidden.isdisjoint(metadata_args)
    assert forbidden.isdisjoint(download_args)
    assert metadata_args[-1] == source().canonical_url
    assert download_args[-1] == source().canonical_url
    assert stored.key == OBJECT_KEY
    assert stored.content_length == len(b"video-bytes")
    assert stored.sha256 is not None
    assert stored.duration_ms == 120_000
    assert store.object_bodies[OBJECT_KEY] == b"video-bytes"


@pytest.mark.unit
def test_import_checks_cancellation_between_every_external_stage(tmp_path: Path, clock) -> None:
    """A cancellation boundary must exist before preflight, metadata, download, and upload."""
    output = tmp_path / "source.mp4"
    output.write_bytes(b"video")
    runner = RecordingRunner(result(metadata()), result(str(output)))
    store = FakeObjectStore(now=clock)
    checks = 0

    def cancellation_check() -> None:
        """Count each adapter boundary that honors cancellation."""
        nonlocal checks
        checks += 1

    importer(store, runner).import_source(
        source(),
        workspace=tmp_path,
        object_key=OBJECT_KEY,
        cancellation_check=cancellation_check,
    )

    assert checks == 4


@pytest.mark.unit
@pytest.mark.parametrize(("stop_at", "expected_commands"), [(1, 0), (2, 0), (3, 1), (4, 2)])
def test_cancellation_prevents_every_later_external_stage(
    tmp_path: Path, clock, stop_at: int, expected_commands: int
) -> None:
    """Each cancellation boundary must prevent all provider and upload work after it."""
    output = tmp_path / "source.mp4"
    output.write_bytes(b"video")
    runner = RecordingRunner(result(metadata()), result(str(output)))
    store = FakeObjectStore(now=clock)
    checks = 0

    def cancellation_check() -> None:
        """Interrupt exactly the selected external-work boundary."""
        nonlocal checks
        checks += 1
        if checks == stop_at:
            raise RuntimeError("cancel now")

    with pytest.raises(RuntimeError, match="cancel now"):
        importer(store, runner).import_source(
            source(),
            workspace=tmp_path,
            object_key=OBJECT_KEY,
            cancellation_check=cancellation_check,
        )

    assert len(runner.calls) == expected_commands
    assert store.objects == {}


@pytest.mark.unit
def test_redirects_are_validated_before_commands_run(tmp_path: Path, clock) -> None:
    """A provider command must never receive authority widened by a source redirect."""
    runner = RecordingRunner()
    preflight = FakePreflight((f"https://evil.example/watch?v={VIDEO_ID}",))

    with pytest.raises(SourceUnsupportedError):
        importer(FakeObjectStore(now=clock), runner, preflight=preflight).import_source(
            source(), workspace=tmp_path, object_key=OBJECT_KEY, cancellation_check=lambda: None
        )

    assert runner.calls == []


@pytest.mark.unit
def test_dns_rebinding_before_metadata_is_rejected(tmp_path: Path, clock) -> None:
    """A changed DNS set must stop execution before yt-dlp sees the source."""
    answers = iter(((PUBLIC_IP,), (OTHER_PUBLIC_IP,)))

    def changing_resolver(_: str) -> tuple[str, ...]:
        """Return a different public address on the second observation."""
        return next(answers)

    initial = source(resolver=changing_resolver)
    runner = RecordingRunner()

    with pytest.raises(SourceUnsupportedError):
        importer(FakeObjectStore(now=clock), runner, resolver=changing_resolver).import_source(
            initial, workspace=tmp_path, object_key=OBJECT_KEY, cancellation_check=lambda: None
        )

    assert runner.calls == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "error_type"),
    [
        ({"extractor_key": "Generic"}, SourceUnsupportedError),
        ({"_type": "playlist", "entries": [{"id": VIDEO_ID}]}, SourceUnsupportedError),
        ({"entries": [{"id": VIDEO_ID}, {"id": VIDEO_ID}]}, SourceUnsupportedError),
        ({"availability": "private"}, SourcePrivateError),
        ({"availability": "subscriber_only"}, SourceUnsupportedError),
        ({"age_limit": 18}, SourcePrivateError),
        ({"is_live": True, "live_status": "is_live"}, SourceUnsupportedError),
        ({"live_status": "is_upcoming"}, SourceUnsupportedError),
        ({"live_status": "post_live"}, SourceUnsupportedError),
        ({"duration": 14_401}, SourceTooLongError),
        ({"duration": None}, SourceUnsupportedError),
        ({"id": "wrong-video"}, SourceUnsupportedError),
    ],
)
def test_metadata_policy_maps_to_stable_errors(
    tmp_path: Path, clock, overrides: dict[str, object], error_type: type[Exception]
) -> None:
    """Only one public, bounded, non-live YouTube video may pass preflight."""
    runner = RecordingRunner(result(metadata(**overrides)))

    with pytest.raises(error_type):
        importer(FakeObjectStore(now=clock), runner).import_source(
            source(), workspace=tmp_path, object_key=OBJECT_KEY, cancellation_check=lambda: None
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("stderr", "error_type"),
    [
        ("certificate verify failed at secret-host", SourceTlsError),
        ("Private video: owner secret", SourcePrivateError),
        ("Sign in to confirm your age", SourcePrivateError),
        ("This video is members-only", SourceUnsupportedError),
        ("upstream temporarily unavailable", SourceUnavailableError),
    ],
)
def test_command_failures_are_sanitized(
    tmp_path: Path, clock, stderr: str, error_type: type[Exception]
) -> None:
    """Classification may inspect provider text but domain errors must retain none of it."""
    runner = RecordingRunner(result(stderr=stderr, returncode=1))

    with pytest.raises(error_type) as captured:
        importer(FakeObjectStore(now=clock), runner).import_source(
            source(), workspace=tmp_path, object_key=OBJECT_KEY, cancellation_check=lambda: None
        )

    assert stderr not in str(captured.value)


@pytest.mark.unit
@pytest.mark.parametrize("reported", ["../escape.mp4", "nested/source.mp4"])
def test_output_must_be_a_direct_workspace_child(tmp_path: Path, clock, reported: str) -> None:
    """Provider output cannot escape or introduce nested authority beneath the job workspace."""
    candidate = tmp_path / reported
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(b"video")
    runner = RecordingRunner(result(metadata()), result(str(candidate)))

    with pytest.raises(SourceUnsupportedError):
        importer(FakeObjectStore(now=clock), runner).import_source(
            source(), workspace=tmp_path, object_key=OBJECT_KEY, cancellation_check=lambda: None
        )


@pytest.mark.unit
def test_output_symlink_is_rejected(tmp_path: Path, clock) -> None:
    """A replaced output symlink must never be followed into another filesystem location."""
    target = tmp_path.parent / "outside.mp4"
    target.write_bytes(b"outside")
    output = tmp_path / "source.mp4"
    output.symlink_to(target)
    runner = RecordingRunner(result(metadata()), result(str(output)))

    with pytest.raises(SourceUnsupportedError):
        importer(FakeObjectStore(now=clock), runner).import_source(
            source(), workspace=tmp_path, object_key=OBJECT_KEY, cancellation_check=lambda: None
        )


@pytest.mark.unit
def test_more_than_one_final_download_file_is_rejected(tmp_path: Path, clock) -> None:
    """An extractor may not smuggle an unreported second final file into later stages."""
    output = tmp_path / "source.mp4"
    output.write_bytes(b"video")
    (tmp_path / "extra.webm").write_bytes(b"extra")
    runner = RecordingRunner(result(metadata()), result(str(output)))

    with pytest.raises(SourceUnsupportedError):
        importer(FakeObjectStore(now=clock), runner).import_source(
            source(), workspace=tmp_path, object_key=OBJECT_KEY, cancellation_check=lambda: None
        )


@pytest.mark.unit
def test_size_is_enforced_again_after_download(tmp_path: Path, clock) -> None:
    """The local file stat remains authoritative even if yt-dlp's ceiling was bypassed."""
    output = tmp_path / "source.mp4"
    with output.open("wb") as oversized:
        oversized.truncate(MAX_SOURCE_SIZE_BYTES + 1)
    runner = RecordingRunner(result(metadata()), result(str(output)))

    with pytest.raises(SourceUnsupportedError):
        importer(FakeObjectStore(now=clock), runner).import_source(
            source(), workspace=tmp_path, object_key=OBJECT_KEY, cancellation_check=lambda: None
        )
