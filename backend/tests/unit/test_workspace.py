"""Contracts for secure per-Job temporary workspaces."""

from __future__ import annotations

import shutil
import stat
import tempfile
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest

from clipah.jobs.workspace import job_workspace

JOB_ID = UUID("11111111-1111-4111-8111-111111111111")


@pytest.mark.unit
def test_job_workspace_is_uuid_scoped_beneath_the_configured_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A Job must never share its working path with another filesystem consumer."""
    root = tmp_path / "job-workspaces"
    monkeypatch.setenv("CLIPAH_JOB_WORKSPACE_ROOT", str(root))

    with job_workspace(JOB_ID) as workspace:
        assert workspace.parent == root.resolve()
        assert workspace.name.startswith(f"{JOB_ID}-")
        assert workspace.is_dir()
        assert stat.S_IMODE(workspace.stat().st_mode) == 0o700

    assert not workspace.exists()
    assert root.is_dir()


@pytest.mark.unit
def test_job_workspace_removes_only_its_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cleanup must preserve the configured root and unrelated sibling contents."""
    root = tmp_path / "job-workspaces"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    sibling = root / "keep.txt"
    sibling.write_text("unrelated", encoding="utf-8")
    monkeypatch.setenv("CLIPAH_JOB_WORKSPACE_ROOT", str(root))

    with job_workspace(JOB_ID) as workspace:
        (workspace / "working.bin").write_bytes(b"temporary")

    assert root.is_dir()
    assert sibling.read_text(encoding="utf-8") == "unrelated"
    assert not workspace.exists()


@pytest.mark.unit
def test_job_workspace_survives_nested_failure_until_diagnostics_are_collected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Inner failure handlers need the workspace before outer cleanup runs."""
    root = tmp_path / "job-workspaces"
    monkeypatch.setenv("CLIPAH_JOB_WORKSPACE_ROOT", str(root))
    captured: list[str] = []

    with (
        pytest.raises(RuntimeError, match="pipeline failed"),
        job_workspace(JOB_ID) as workspace,
        collect_diagnostics(workspace, captured),
    ):
        raise RuntimeError("pipeline failed")

    assert captured == ["diagnostic for pipeline failed"]
    assert not workspace.exists()


@pytest.mark.unit
def test_job_workspace_rejects_a_configured_root_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A symlink must not redirect temporary media outside the configured root."""
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "job-workspaces"
    root.symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("CLIPAH_JOB_WORKSPACE_ROOT", str(root))

    with pytest.raises(ValueError, match="symlink"), job_workspace(JOB_ID):
        pytest.fail("an unsafe workspace was yielded")

    assert list(outside.iterdir()) == []


@pytest.mark.unit
def test_job_workspace_rejects_a_preexisting_permissive_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Other local users must not be able to rename or replace active Job directories."""
    root = tmp_path / "job-workspaces"
    root.mkdir()
    root.chmod(0o777)
    monkeypatch.setenv("CLIPAH_JOB_WORKSPACE_ROOT", str(root))

    with pytest.raises(ValueError, match="0700"), job_workspace(JOB_ID):
        pytest.fail("a workspace was created beneath an untrusted root")

    assert list(root.iterdir()) == []


@pytest.mark.unit
def test_job_workspace_rejects_a_directory_returned_outside_the_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Containment validation must reject an unexpected temporary-directory location."""
    root = tmp_path / "job-workspaces"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    monkeypatch.setenv("CLIPAH_JOB_WORKSPACE_ROOT", str(root))
    monkeypatch.setattr(tempfile, "mkdtemp", fixed_workspace_path(outside))

    with pytest.raises(ValueError, match="escaped"), job_workspace(JOB_ID):
        pytest.fail("an out-of-root workspace was yielded")

    assert sentinel.read_text(encoding="utf-8") == "preserve"


@pytest.mark.unit
def test_job_workspace_never_cleans_the_configured_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Even an invalid temporary-directory result must never select the parent root."""
    root = tmp_path / "job-workspaces"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    sentinel = root / "keep.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    monkeypatch.setenv("CLIPAH_JOB_WORKSPACE_ROOT", str(root))
    monkeypatch.setattr(tempfile, "mkdtemp", fixed_workspace_path(root))

    with pytest.raises(ValueError, match="escaped"), job_workspace(JOB_ID):
        pytest.fail("the configured root was yielded as a Job workspace")

    assert sentinel.read_text(encoding="utf-8") == "preserve"


@pytest.mark.unit
def test_job_workspace_cleanup_does_not_follow_a_replaced_directory_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A path swap during a Job must not turn cleanup into recursive deletion elsewhere."""
    root = tmp_path / "job-workspaces"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    monkeypatch.setenv("CLIPAH_JOB_WORKSPACE_ROOT", str(root))

    with job_workspace(JOB_ID) as workspace:
        shutil.rmtree(workspace)
        workspace.symlink_to(outside, target_is_directory=True)

    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert not workspace.exists()


@pytest.mark.unit
def test_job_workspace_cleanup_is_best_effort(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A cleanup failure must not hide the pipeline result that caused context exit."""
    root = tmp_path / "job-workspaces"
    monkeypatch.setenv("CLIPAH_JOB_WORKSPACE_ROOT", str(root))
    cleanup_attempts: list[Path] = []

    with job_workspace(JOB_ID) as workspace:
        monkeypatch.setattr(shutil, "rmtree", failing_cleanup(cleanup_attempts))

    assert cleanup_attempts == [workspace]
    assert workspace.is_dir()


@pytest.mark.integration
def test_concurrent_job_workspaces_cannot_collide_on_the_same_filename(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Concurrent attempts for one Job must receive distinct random-suffixed directories."""
    root = tmp_path / "job-workspaces"
    monkeypatch.setenv("CLIPAH_JOB_WORKSPACE_ROOT", str(root))
    barrier = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(write_same_filename, JOB_ID, barrier) for _ in range(2)]
        results = [future.result() for future in futures]

    assert results[0][0] != results[1][0]
    assert [result[1] for result in results] == [f"payload:{JOB_ID}", f"payload:{JOB_ID}"]
    assert not any(root.iterdir())


@contextmanager
def collect_diagnostics(workspace: Path, captured: list[str]) -> Iterator[None]:
    """Record diagnostic evidence before propagating a nested pipeline failure."""
    try:
        yield
    except RuntimeError as exc:
        diagnostic = workspace / "diagnostic.txt"
        diagnostic.write_text(f"diagnostic for {exc}", encoding="utf-8")
        captured.append(diagnostic.read_text(encoding="utf-8"))
        raise


def failing_cleanup(cleanup_attempts: list[Path]) -> Callable[[Path], None]:
    """Return a cleanup operation that records and then refuses one removal."""

    def fail(path: Path) -> None:
        cleanup_attempts.append(path)
        raise OSError(f"cannot remove {path.name}")

    return fail


def fixed_workspace_path(path: Path) -> Callable[..., str]:
    """Return a temporary-directory provider pinned to one security-test path."""

    def return_path(*args: object, **kwargs: object) -> str:
        """Ignore provider inputs and return the requested fixed path."""
        del args, kwargs
        return str(path)

    return return_path


def write_same_filename(job_id: UUID, barrier: Barrier) -> tuple[Path, str]:
    """Keep two workspaces live while each writes the same relative filename."""
    with job_workspace(job_id) as workspace:
        working_file = workspace / "main_video.mp4"
        working_file.write_text(f"payload:{job_id}", encoding="utf-8")
        barrier.wait()
        return workspace, working_file.read_text(encoding="utf-8")
