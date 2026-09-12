"""Opt-in behavioral contracts for the immutable Task 46 runtime images."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[3]
RUN_IMAGE_TESTS = os.environ.get("CLIPAH_RUN_RUNTIME_IMAGE_TESTS") == "1"


def _docker(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run Docker from the repository root and retain bounded diagnostics for assertions."""
    return subprocess.run(
        ["docker", *arguments],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15 * 60,
    )


@pytest.mark.slow
@pytest.mark.timeout(900)
@pytest.mark.skipif(not RUN_IMAGE_TESTS, reason="opt in with CLIPAH_RUN_RUNTIME_IMAGE_TESTS=1")
def test_backend_api_image_builds_as_the_fixed_unprivileged_user() -> None:
    """The production API target must build reproducibly and never run as root."""
    built = _docker(
        "build",
        "--file",
        "infra/docker/backend.Dockerfile",
        "--target",
        "api",
        "--tag",
        "clipah-backend-api:task46-test",
        ".",
    )
    assert built.returncode == 0, built.stderr[-2_000:]

    inspected = _docker(
        "run",
        "--rm",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m",
        "clipah-backend-api:task46-test",
        "id",
        "-u",
    )
    assert inspected.returncode == 0, inspected.stderr[-2_000:]
    assert inspected.stdout.strip() == "10001"


@pytest.mark.slow
@pytest.mark.timeout(900)
@pytest.mark.skipif(not RUN_IMAGE_TESTS, reason="opt in with CLIPAH_RUN_RUNTIME_IMAGE_TESTS=1")
def test_media_worker_proves_capabilities_without_source_import_tools() -> None:
    """Render workers need the reviewed media stack but no source extraction surface."""
    built = _docker(
        "build",
        "--file",
        "infra/docker/backend.Dockerfile",
        "--target",
        "media-worker",
        "--tag",
        "clipah-backend-media:task46-test",
        ".",
    )
    assert built.returncode == 0, built.stderr[-2_000:]

    readiness = _docker(
        "run",
        "--rm",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m",
        "--entrypoint",
        "python",
        "clipah-backend-media:task46-test",
        "-m",
        "clipah.runtime.readiness",
        "media",
    )
    assert readiness.returncode == 0, readiness.stderr[-2_000:]
    assert readiness.stdout.strip() == "ready"

    source_tools = _docker(
        "run",
        "--rm",
        "--entrypoint",
        "sh",
        "clipah-backend-media:task46-test",
        "-c",
        "command -v yt-dlp || command -v deno",
    )
    assert source_tools.returncode != 0


@pytest.mark.slow
@pytest.mark.timeout(900)
@pytest.mark.skipif(not RUN_IMAGE_TESTS, reason="opt in with CLIPAH_RUN_RUNTIME_IMAGE_TESTS=1")
def test_source_import_image_keeps_its_complete_isolated_toolchain() -> None:
    """The source worker alone must carry exact extraction and JavaScript runtimes."""
    built = _docker(
        "build",
        "--file",
        "infra/docker/source-import.Dockerfile",
        "--tag",
        "clipah-source-import:task46-test",
        ".",
    )
    assert built.returncode == 0, built.stderr[-2_000:]

    readiness = _docker(
        "run",
        "--rm",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m",
        "--tmpfs",
        "/var/cache/clipah/deno:rw,noexec,nosuid,size=16m",
        "--tmpfs",
        "/var/lib/clipah/job-workspaces:rw,noexec,nosuid,size=16m,uid=10001,gid=10001,mode=0700",
        "--entrypoint",
        "python",
        "clipah-source-import:task46-test",
        "-m",
        "clipah.source_connectors.readiness",
    )
    assert readiness.returncode == 0, readiness.stderr[-2_000:]
    assert "yt-dlp=2026.08.19" in readiness.stdout
    assert "deno=2.9.5" in readiness.stdout


@pytest.mark.slow
@pytest.mark.timeout(900)
@pytest.mark.skipif(not RUN_IMAGE_TESTS, reason="opt in with CLIPAH_RUN_RUNTIME_IMAGE_TESTS=1")
def test_frontend_image_builds_as_a_standalone_unprivileged_server() -> None:
    """The browser runtime must not require source files or root after its build stage."""
    built = _docker(
        "build",
        "--file",
        "infra/docker/frontend.Dockerfile",
        "--tag",
        "clipah-frontend:task46-test",
        ".",
    )
    assert built.returncode == 0, built.stderr[-2_000:]
    assert "ESLint: Failed to load plugin" not in built.stderr

    inspected = _docker(
        "run",
        "--rm",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m",
        "--entrypoint",
        "id",
        "clipah-frontend:task46-test",
        "-u",
    )
    assert inspected.returncode == 0, inspected.stderr[-2_000:]
    assert inspected.stdout.strip() == "10001"
