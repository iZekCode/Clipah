"""Behavioral contracts for runtime shell entry points with controlled executables."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[3]
SOURCE_ENTRYPOINT = REPOSITORY_ROOT / "infra" / "docker" / "source-import-entrypoint.sh"


def _fake_executable(directory: Path, name: str, body: str) -> None:
    """Create one executable test boundary without adding helpers to production code."""
    executable = directory / name
    executable.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    executable.chmod(0o755)


@pytest.mark.unit
def test_source_import_entrypoint_uses_explicit_deployment_concurrency(tmp_path: Path) -> None:
    """Scaling source imports must not require rebuilding the isolated image."""
    _fake_executable(tmp_path, "python", "exit 0")
    _fake_executable(tmp_path, "celery", "printf '%s\\n' \"$@\"")
    environment = {
        **os.environ,
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "CLIPAH_SOURCE_IMPORT_CONCURRENCY": "3",
    }

    completed = subprocess.run(
        ["/bin/sh", str(SOURCE_ENTRYPOINT)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0
    arguments = completed.stdout.splitlines()
    position = arguments.index("--concurrency")
    assert arguments[position + 1] == "3"
