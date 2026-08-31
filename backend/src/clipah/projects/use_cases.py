"""Workspace-scoped Project operations and business policy."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from uuid import UUID

from clipah.projects.repository import ProjectRepository
from clipah.projects.schemas import (
    CreateProjectCommand,
    ProjectPage,
    ProjectPageBoundary,
    ProjectSummary,
)
from clipah.workspaces.models import WorkspaceAccess

PROJECT_RECOVERY_WINDOW = timedelta(days=30)
MAX_PROJECT_NAME_LENGTH = 200


class ProjectNotFoundError(Exception):
    """Signal that an authorized Workspace cannot see the requested Project."""


class ProjectConflictError(Exception):
    """Signal that a Project operation conflicts with durable state."""


class ProjectValidationError(Exception):
    """Signal that a normalized Project input is invalid."""


def create_project(
    repository: ProjectRepository,
    *,
    access: WorkspaceAccess,
    command: CreateProjectCommand,
    idempotency_scope: str,
    idempotency_key: str,
    now: datetime,
) -> ProjectSummary:
    """Create one Project once, replaying an identical Workspace-wide retry."""
    name = normalize_project_name(command.name)
    request_hash = _request_hash(name=name, source_kind=command.source_kind)
    existing = repository.idempotency_replay(
        workspace_id=access.workspace_id,
        route=idempotency_scope,
        key=idempotency_key,
    )
    if existing is not None:
        return _replay(existing.request_hash, request_hash, existing.project)
    reserved = repository.reserve_idempotency_key(
        workspace_id=access.workspace_id,
        user_id=access.user_id,
        route=idempotency_scope,
        key=idempotency_key,
        request_hash=request_hash,
    )
    if not reserved:
        winner = repository.idempotency_replay(
            workspace_id=access.workspace_id,
            route=idempotency_scope,
            key=idempotency_key,
        )
        if winner is None:
            raise ProjectConflictError("idempotency reservation failed")
        return _replay(winner.request_hash, request_hash, winner.project)
    summary = repository.create(
        workspace_id=access.workspace_id,
        created_by_user_id=access.user_id,
        name=name,
        source_kind=command.source_kind,
        now=now,
    )
    repository.complete_idempotency_key(
        workspace_id=access.workspace_id,
        route=idempotency_scope,
        key=idempotency_key,
        project=summary,
    )
    return summary


def list_projects(
    repository: ProjectRepository,
    *,
    access: WorkspaceAccess,
    limit: int,
    after: ProjectPageBoundary | None,
) -> ProjectPage:
    """List non-archived Projects in immutable creation order inside one Workspace."""
    return repository.page(workspace_id=access.workspace_id, limit=limit, after=after)


def get_project(
    repository: ProjectRepository, *, access: WorkspaceAccess, project_id: UUID
) -> ProjectSummary:
    """Return one visible Project or hide its absence behind the Workspace boundary."""
    project = repository.active_by_id(workspace_id=access.workspace_id, project_id=project_id)
    if project is None:
        raise ProjectNotFoundError("project is unavailable")
    return project


def rename_project(
    repository: ProjectRepository,
    *,
    access: WorkspaceAccess,
    project_id: UUID,
    name: str,
    now: datetime,
) -> ProjectSummary:
    """Rename one visible Project without exposing archived records."""
    project = repository.rename(
        workspace_id=access.workspace_id,
        project_id=project_id,
        name=normalize_project_name(name),
        now=now,
    )
    if project is None:
        raise ProjectNotFoundError("project is unavailable")
    return project


def soft_delete_project(
    repository: ProjectRepository, *, access: WorkspaceAccess, project_id: UUID, now: datetime
) -> None:
    """Archive a visible Project so it remains recoverable for thirty days."""
    if not repository.archive(workspace_id=access.workspace_id, project_id=project_id, now=now):
        raise ProjectNotFoundError("project is unavailable")


def restore_project(
    repository: ProjectRepository, *, access: WorkspaceAccess, project_id: UUID, now: datetime
) -> ProjectSummary:
    """Restore one recently archived Project while its recovery window is open."""
    result = repository.restore(
        workspace_id=access.workspace_id,
        project_id=project_id,
        now=now,
        recovery_window=PROJECT_RECOVERY_WINDOW,
    )
    if result.recovery_window_elapsed:
        raise ProjectConflictError("project recovery window elapsed")
    if result.project is None:
        raise ProjectNotFoundError("project is unavailable")
    return result.project


def normalize_project_name(name: str) -> str:
    """Collapse spacing and enforce the canonical Project name length for every caller."""
    normalized = " ".join(name.split())
    if not normalized or len(normalized) > MAX_PROJECT_NAME_LENGTH:
        raise ProjectValidationError("project name is outside canonical bounds")
    return normalized


def _request_hash(*, name: str, source_kind: str) -> bytes:
    """Hash canonical request semantics instead of storing request payloads unnecessarily."""
    payload = json.dumps(
        {"name": name, "sourceKind": source_kind}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).digest()


def _replay(
    stored_hash: bytes, request_hash: bytes, project: ProjectSummary | None
) -> ProjectSummary:
    """Return a durable domain replay or reject a changed request body for the same key."""
    if stored_hash != request_hash or project is None:
        raise ProjectConflictError("idempotency key cannot satisfy this request")
    return project
