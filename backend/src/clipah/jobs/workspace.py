"""Create isolated temporary working directories for Jobs."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

WORKSPACE_ROOT_ENV = "CLIPAH_JOB_WORKSPACE_ROOT"
DEFAULT_WORKSPACE_ROOT = Path(tempfile.gettempdir()) / "clipah-job-workspaces"


@contextmanager
def job_workspace(job_id: UUID) -> Iterator[Path]:
    """Provide the temporary filesystem workspace owned by one Job."""
    root = _configured_root()
    workspace = Path(tempfile.mkdtemp(prefix=f"{job_id}-", dir=root))
    workspace.chmod(0o700)

    if not _is_direct_child(workspace, root):
        _cleanup_workspace(workspace, root)
        raise ValueError("Job workspace escaped its configured root")

    try:
        yield workspace
    finally:
        _cleanup_workspace(workspace, root)


def remove_job_workspaces(job_id: UUID) -> int:
    """Remove any working directory one Job left behind, and nothing else.

    A workspace normally disappears with the context manager that made it; one survives
    only when the worker holding it died. Retention removes those, but the target is
    still exactly the directories this Job's identifier prefixes: the root itself and
    every other Job's work are out of reach by construction.
    """
    root = _configured_root()
    removed = 0
    for candidate in root.glob(f"{job_id}-*"):
        _cleanup_workspace(candidate, root)
        removed += 1
    return removed


def _configured_root() -> Path:
    """Create and validate the one root allowed to contain Job workspaces."""
    configured = Path(os.environ.get(WORKSPACE_ROOT_ENV, DEFAULT_WORKSPACE_ROOT)).absolute()
    configured.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = configured.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError("Job workspace root must not be a symlink")
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ValueError("Job workspace root must be owned by this process with mode 0700")
    return configured.resolve(strict=True)


def _is_direct_child(workspace: Path, root: Path) -> bool:
    """Confirm a real directory is exactly one level beneath the validated root."""
    if workspace == root or workspace.parent != root or workspace.is_symlink():
        return False
    return workspace.resolve(strict=True).parent == root


def _cleanup_workspace(workspace: Path, root: Path) -> None:
    """Best-effort remove one workspace without following a replaced symlink."""
    try:
        if workspace.is_symlink():
            workspace.unlink(missing_ok=True)
        elif workspace.exists() and _is_direct_child(workspace, root):
            shutil.rmtree(workspace)
    except OSError:
        pass
