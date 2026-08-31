"""Project use-case values independent from SQLAlchemy and HTTP."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CreateProjectCommand:
    """Describe a normalized Project a Workspace member wants to create."""

    name: str
    source_kind: str


@dataclass(frozen=True, slots=True)
class ProjectSummary:
    """Expose stable Project fields safe for an authorized Workspace member."""

    project_id: UUID
    workspace_id: UUID
    name: str
    status: str
    source_kind: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectPage:
    """Carry one cursor-bounded Project collection response."""

    projects: tuple[ProjectSummary, ...]
    next_boundary: ProjectPageBoundary | None


@dataclass(frozen=True, slots=True)
class ProjectPageBoundary:
    """Describe one stable Project collection boundary without HTTP encoding."""

    created_at: datetime
    project_id: UUID


@dataclass(frozen=True, slots=True)
class IdempotencyReplay:
    """Represent a durable, HTTP-neutral result for one idempotent Project create."""

    request_hash: bytes
    project: ProjectSummary | None


@dataclass(frozen=True, slots=True)
class ProjectRestoreResult:
    """Report whether persistence restored a Project or found an expired recovery window."""

    project: ProjectSummary | None
    recovery_window_elapsed: bool
