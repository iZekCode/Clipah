"""HTTP adapters for Workspace-scoped Project creation, recovery, and pagination."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from clipah.api.dependencies import (
    CurrentWorkspace,
    DatabaseSession,
    auth_components_for,
    require_csrf,
    require_workspace,
)
from clipah.api.errors import ApiError
from clipah.models import SourceKind
from clipah.projects.repository import ProjectRepository
from clipah.projects.schemas import CreateProjectCommand, ProjectPageBoundary, ProjectSummary
from clipah.projects.use_cases import (
    ProjectConflictError,
    ProjectNotFoundError,
    ProjectValidationError,
    create_project,
    get_project,
    list_projects,
    rename_project,
    restore_project,
    soft_delete_project,
)
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["projects"])

ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_READ))
]
WritableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_WRITE))
]
IdempotencyHeader = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]
PROJECT_CREATE_IDEMPOTENCY_SCOPE = "projects:create"


class ProjectCreateRequest(BaseModel):
    """Accept one strictly shaped Project creation payload."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1)
    source_kind: SourceKind = Field(alias="sourceKind")


class ProjectRenameRequest(BaseModel):
    """Accept one strictly shaped Project rename payload."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)


@router.post("/projects", status_code=201, dependencies=[Depends(require_csrf)])
def create(
    request: Request,
    session: DatabaseSession,
    workspace: WritableWorkspace,
    payload: ProjectCreateRequest,
    idempotency_key: IdempotencyHeader,
) -> dict[str, object]:
    """Create one Project in the Workspace whose membership was already proven."""
    try:
        summary = create_project(
            ProjectRepository(session),
            access=workspace.access,
            command=CreateProjectCommand(name=payload.name, source_kind=payload.source_kind.value),
            idempotency_scope=PROJECT_CREATE_IDEMPOTENCY_SCOPE,
            idempotency_key=idempotency_key,
            now=auth_components_for(request).now(),
        )
    except ProjectValidationError as error:
        raise ApiError(status_code=422, code="VALIDATION_ERROR") from error
    except ProjectConflictError as error:
        raise ApiError(status_code=409, code="CONFLICT") from error
    return _project_body(summary)


@router.get("/projects")
def list_collection(
    session: DatabaseSession,
    workspace: ReadableWorkspace,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> dict[str, Any]:
    """List visible Projects from one Workspace with an opaque creation-order cursor."""
    try:
        page = list_projects(
            ProjectRepository(session),
            access=workspace.access,
            limit=limit,
            after=_decode_cursor(cursor) if cursor is not None else None,
        )
    except ValueError as error:
        raise ApiError(status_code=422, code="VALIDATION_ERROR") from error
    return {
        "projects": [_project_body(project) for project in page.projects],
        "nextCursor": None if page.next_boundary is None else _encode_cursor(page.next_boundary),
    }


@router.get("/projects/{project_id}")
def show(
    project_id: UUID, session: DatabaseSession, workspace: ReadableWorkspace
) -> dict[str, object]:
    """Show one visible Project without leaking archived or cross-Workspace records."""
    try:
        return _project_body(
            get_project(ProjectRepository(session), access=workspace.access, project_id=project_id)
        )
    except ProjectNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error


@router.patch("/projects/{project_id}", dependencies=[Depends(require_csrf)])
def rename(
    request: Request,
    project_id: UUID,
    session: DatabaseSession,
    workspace: WritableWorkspace,
    payload: ProjectRenameRequest,
) -> dict[str, object]:
    """Rename a visible Project when the member may write this Workspace."""
    try:
        return _project_body(
            rename_project(
                ProjectRepository(session),
                access=workspace.access,
                project_id=project_id,
                name=payload.name,
                now=auth_components_for(request).now(),
            )
        )
    except ProjectNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except ProjectValidationError as error:
        raise ApiError(status_code=422, code="VALIDATION_ERROR") from error


@router.delete("/projects/{project_id}", status_code=204, dependencies=[Depends(require_csrf)])
def delete(
    request: Request, project_id: UUID, session: DatabaseSession, workspace: WritableWorkspace
) -> Response:
    """Soft-delete one visible Project so retention policy can later recover or purge it."""
    try:
        soft_delete_project(
            ProjectRepository(session),
            access=workspace.access,
            project_id=project_id,
            now=auth_components_for(request).now(),
        )
    except ProjectNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return Response(status_code=204)


@router.post("/projects/{project_id}/restore", dependencies=[Depends(require_csrf)])
def restore(
    request: Request, project_id: UUID, session: DatabaseSession, workspace: WritableWorkspace
) -> dict[str, object]:
    """Restore a recently soft-deleted Project for an authorized Workspace member."""
    try:
        return _project_body(
            restore_project(
                ProjectRepository(session),
                access=workspace.access,
                project_id=project_id,
                now=auth_components_for(request).now(),
            )
        )
    except ProjectNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except ProjectConflictError as error:
        raise ApiError(status_code=409, code="CONFLICT") from error


def _project_body(project: ProjectSummary) -> dict[str, object]:
    """Render one Project domain value into the public camelCase HTTP representation."""
    return {
        "id": str(project.project_id),
        "workspaceId": str(project.workspace_id),
        "name": project.name,
        "status": project.status,
        "sourceKind": project.source_kind,
        "createdAt": project.created_at.isoformat(),
        "updatedAt": project.updated_at.isoformat(),
    }


def _encode_cursor(boundary: ProjectPageBoundary) -> str:
    """Create the opaque unsigned base64 `(created_at, id)` HTTP cursor."""
    raw = f"{boundary.created_at.isoformat()}|{boundary.project_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> ProjectPageBoundary:
    """Parse one opaque unsigned base64 cursor into a domain pagination boundary."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        created_at, project_id = base64.urlsafe_b64decode(padded).decode().split("|", 1)
        timestamp = datetime.fromisoformat(created_at)
        if timestamp.tzinfo is None:
            raise ValueError("cursor timestamp has no timezone")
        return ProjectPageBoundary(created_at=timestamp, project_id=UUID(project_id))
    except (ValueError, UnicodeDecodeError, binascii.Error) as error:
        raise ValueError("invalid cursor") from error
