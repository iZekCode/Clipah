"""Unit contracts for isolated source-import runtime readiness."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from clipah.source_connectors.readiness import RuntimeReadinessError, check_runtime


class RecordingProbeRunner:
    """Return deterministic tool versions while retaining every readiness command."""

    def __init__(self, outputs: Mapping[tuple[str, ...], str]) -> None:
        """Bind exact fake command outputs to their argument arrays."""
        self.outputs = dict(outputs)
        self.calls: list[tuple[str, ...]] = []

    def run(self, arguments: Sequence[str]) -> str:
        """Record one argument array and return its configured output."""
        key = tuple(arguments)
        self.calls.append(key)
        return self.outputs[key]


def valid_outputs() -> dict[tuple[str, ...], str]:
    """Return the exact tool outputs accepted by the source-import image."""
    return {
        ("yt-dlp", "--version"): "2026.08.19\n",
        ("deno", "--version"): "deno 2.9.5 (stable, release, aarch64-unknown-linux-gnu)\n",
        ("ffmpeg", "-version"): "ffmpeg version 7.1.5-0+deb13u1 Copyright\n",
        ("ffprobe", "-version"): "ffprobe version 7.1.5-0+deb13u1 Copyright\n",
    }


@pytest.mark.unit
def test_readiness_requires_every_exact_runtime_version() -> None:
    """The independently released worker must fail closed on any silent runtime drift."""
    runner = RecordingProbeRunner(valid_outputs())

    versions = check_runtime(
        runner=runner,
        distribution_version=lambda name: "0.8.0" if name == "yt-dlp-ejs" else "missing",
        environment={},
    )

    assert versions == {
        "yt-dlp": "2026.08.19",
        "yt-dlp-ejs": "0.8.0",
        "deno": "2.9.5",
        "ffmpeg": "7.1.5-0+deb13u1",
        "ffprobe": "7.1.5-0+deb13u1",
    }
    assert runner.calls == list(valid_outputs())


@pytest.mark.unit
@pytest.mark.parametrize(
    ("command", "drifted"),
    [
        (("yt-dlp", "--version"), "2026.08.20\n"),
        (("deno", "--version"), "deno 2.9.6 (stable, release)\n"),
        (("ffmpeg", "-version"), "ffmpeg version 8.0.1\n"),
        (("ffprobe", "-version"), "ffprobe version 8.0.1\n"),
    ],
)
def test_readiness_rejects_tool_version_drift(command: tuple[str, ...], drifted: str) -> None:
    """A partially upgraded extractor stack is unavailable, not approximately healthy."""
    outputs = valid_outputs()
    outputs[command] = drifted

    with pytest.raises(RuntimeReadinessError, match=r"^source import runtime unavailable$"):
        check_runtime(
            runner=RecordingProbeRunner(outputs),
            distribution_version=lambda _: "0.8.0",
            environment={},
        )


@pytest.mark.unit
def test_readiness_rejects_ejs_version_drift() -> None:
    """The EJS plugin participates in the same fail-closed compatibility unit."""
    with pytest.raises(RuntimeReadinessError, match=r"^source import runtime unavailable$"):
        check_runtime(
            runner=RecordingProbeRunner(valid_outputs()),
            distribution_version=lambda _: "0.8.1",
            environment={},
        )


@pytest.mark.unit
def test_network_probe_is_disabled_by_default() -> None:
    """Ordinary readiness and CI must not depend on a changing public video."""
    runner = RecordingProbeRunner(valid_outputs())

    check_runtime(
        runner=runner,
        distribution_version=lambda _: "0.8.0",
        environment={"CLIPAH_SOURCE_IMPORT_NETWORK_PROBE_URL": "https://youtu.be/dQw4w9WgXcQ"},
    )

    assert all("https://youtu.be" not in argument for call in runner.calls for argument in call)


@pytest.mark.unit
def test_enabled_network_probe_uses_metadata_only_safe_flags() -> None:
    """An explicit live probe may extract metadata but must never download media."""
    probe = (
        "yt-dlp",
        "--no-config",
        "--no-playlist",
        "--skip-download",
        "--dump-single-json",
        "--no-warnings",
        "https://youtu.be/dQw4w9WgXcQ",
    )
    outputs = {**valid_outputs(), probe: '{"id":"dQw4w9WgXcQ"}\n'}
    runner = RecordingProbeRunner(outputs)

    check_runtime(
        runner=runner,
        distribution_version=lambda _: "0.8.0",
        environment={
            "CLIPAH_SOURCE_IMPORT_NETWORK_PROBE": "1",
            "CLIPAH_SOURCE_IMPORT_NETWORK_PROBE_URL": "https://youtu.be/dQw4w9WgXcQ",
        },
    )

    assert runner.calls[-1] == probe


@pytest.mark.unit
def test_enabled_network_probe_requires_an_explicit_url() -> None:
    """Opting in without a fixture identity must fail instead of choosing hidden network work."""
    with pytest.raises(RuntimeReadinessError, match=r"^source import runtime unavailable$"):
        check_runtime(
            runner=RecordingProbeRunner(valid_outputs()),
            distribution_version=lambda _: "0.8.0",
            environment={"CLIPAH_SOURCE_IMPORT_NETWORK_PROBE": "1"},
        )
