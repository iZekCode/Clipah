"""Fail-closed readiness checks for the independently released source-import runtime."""

from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from clipah.assets.source_validation import normalize_youtube_syntax
from clipah.assets.youtube import SourceUnsupportedError

EXPECTED_YT_DLP = "2026.08.19"
EXPECTED_YT_DLP_EJS = "0.8.0"
EXPECTED_DENO = "2.9.5"
EXPECTED_FFMPEG = "7.1.5-0+deb13u1"
PROBE_TIMEOUT_SECONDS = 20
MAX_PROBE_OUTPUT_CHARS = 65_536


class RuntimeReadinessError(Exception):
    """The source-import toolchain is missing, drifted, or unable to run safely."""

    def __init__(self) -> None:
        """Retain no command, path, environment, or provider diagnostic detail."""
        super().__init__("source import runtime unavailable")


class ProbeRunner(Protocol):
    """Run one small readiness command and return bounded standard output."""

    def run(self, arguments: Sequence[str]) -> str:
        """Execute one shell-free readiness command."""


class SubprocessProbeRunner:
    """Production readiness runner with a minimal environment and fixed timeout."""

    def run(self, arguments: Sequence[str]) -> str:
        """Run one tool probe without inheriting application credentials."""
        completed = subprocess.run(
            list(arguments),
            env={
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "DENO_DIR": os.environ.get("DENO_DIR", "/tmp/clipah-deno-readiness"),
            },
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            start_new_session=True,
        )
        if completed.returncode != 0:
            raise RuntimeReadinessError
        return completed.stdout[:MAX_PROBE_OUTPUT_CHARS]


@dataclass(frozen=True, slots=True)
class _VersionProbe:
    """One command, parser prefix, and exact expected runtime version."""

    name: str
    arguments: tuple[str, ...]
    prefix: str
    expected: str


_VERSION_PROBES = (
    _VersionProbe("yt-dlp", ("yt-dlp", "--version"), "", EXPECTED_YT_DLP),
    _VersionProbe("deno", ("deno", "--version"), "deno ", EXPECTED_DENO),
    _VersionProbe("ffmpeg", ("ffmpeg", "-version"), "ffmpeg version ", EXPECTED_FFMPEG),
    _VersionProbe("ffprobe", ("ffprobe", "-version"), "ffprobe version ", EXPECTED_FFMPEG),
)


def check_runtime(
    *,
    runner: ProbeRunner | None = None,
    distribution_version: Callable[[str], str] = importlib.metadata.version,
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Require the exact extractor stack and optionally run one explicit metadata probe."""
    probe_runner = runner or SubprocessProbeRunner()
    configured_environment = os.environ if environment is None else environment
    versions: dict[str, str] = {}
    try:
        for probe in _VERSION_PROBES:
            output = probe_runner.run(probe.arguments)
            version = _version_from(output, prefix=probe.prefix)
            if version != probe.expected:
                raise RuntimeReadinessError
            versions[probe.name] = version
        ejs_version = distribution_version("yt-dlp-ejs")
        if ejs_version != EXPECTED_YT_DLP_EJS:
            raise RuntimeReadinessError
        versions["yt-dlp-ejs"] = ejs_version
        if configured_environment.get("CLIPAH_SOURCE_IMPORT_NETWORK_PROBE") == "1":
            _run_network_probe(probe_runner, environment=configured_environment)
    except RuntimeReadinessError:
        raise
    except Exception as error:
        raise RuntimeReadinessError from error
    return {
        "yt-dlp": versions["yt-dlp"],
        "yt-dlp-ejs": versions["yt-dlp-ejs"],
        "deno": versions["deno"],
        "ffmpeg": versions["ffmpeg"],
        "ffprobe": versions["ffprobe"],
    }


def _version_from(output: str, *, prefix: str) -> str:
    """Extract the first whitespace-delimited version after an exact tool prefix."""
    first_line = output.strip().splitlines()[0]
    if prefix and not first_line.startswith(prefix):
        raise RuntimeReadinessError
    remainder = first_line.removeprefix(prefix)
    version, _, _ = remainder.partition(" ")
    if not version:
        raise RuntimeReadinessError
    return version


def _run_network_probe(runner: ProbeRunner, *, environment: Mapping[str, str]) -> None:
    """Run an explicitly configured download-free provider smoke probe."""
    url = environment.get("CLIPAH_SOURCE_IMPORT_NETWORK_PROBE_URL")
    if url is None or not _safe_probe_url(url):
        raise RuntimeReadinessError
    output = runner.run(
        (
            "yt-dlp",
            "--no-config",
            "--no-playlist",
            "--skip-download",
            "--dump-single-json",
            "--no-warnings",
            url,
        )
    )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise RuntimeReadinessError from error
    if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
        raise RuntimeReadinessError


def _safe_probe_url(url: str) -> bool:
    """Allow only an uncredentialed canonical single-video YouTube smoke target."""
    try:
        host, _ = normalize_youtube_syntax(url)
        parsed = urlsplit(url)
    except (SourceUnsupportedError, ValueError):
        return False
    if host == "youtu.be":
        return True
    return host == "www.youtube.com" and parsed.path == "/watch"


def main() -> int:
    """Print only verified versions for container health checks."""
    try:
        versions = check_runtime()
    except RuntimeReadinessError:
        print("source import runtime unavailable", file=sys.stderr)
        return 1
    for name, version in versions.items():
        print(f"{name}={version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
