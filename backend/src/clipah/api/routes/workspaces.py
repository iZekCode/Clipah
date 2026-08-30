"""Workspace creation, selection, settings, and membership endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from clipah.api.dependencies import (
    CurrentUserDependency,
    CurrentWorkspace,
    DatabaseSession,
    auth_components_for,
    require_csrf,
    require_workspace,
)
from clipah.api.errors import ApiError
from clipah.models import PublishingRolePolicy, WorkspaceKind
from clipah.workspaces.models import (
    MemberSummary,
    PersonalWorkspaceExistsError,
    WorkspaceAction,
    WorkspaceSummary,
)
from clipah.workspaces.use_cases import (
    create_workspace,
    describe_workspace,
    list_members,
    list_workspaces,
    update_workspace,
)

router = APIRouter(prefix="/api/v1", tags=["workspaces"])

ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.WORKSPACE_READ))
]
ManageableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.WORKSPACE_UPDATE))
]
ListableMembers = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.MEMBER_READ))
]


class WorkspaceCreateRequest(BaseModel):
    """The Workspace a member asks us to create for them."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    kind: WorkspaceKind = WorkspaceKind.TEAM


class WorkspaceUpdateRequest(BaseModel):
    """The Workspace settings an owner or admin may change."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str | None = Field(default=None, min_length=1, max_length=120)
    publishing_role_policy: PublishingRolePolicy | None = Field(
        default=None, alias="publishingRolePolicy"
    )


@router.post("/workspaces", status_code=201, dependencies=[Depends(require_csrf)])
def create(
    request: Request,
    session: DatabaseSession,
    user: CurrentUserDependency,
    payload: WorkspaceCreateRequest,
) -> dict[str, Any]:
    """Create a Workspace owned by the caller."""
    components = auth_components_for(request)
    try:
        summary = create_workspace(
            session,
            owner_user_id=user.user_id,
            name=payload.name.strip(),
            kind=payload.kind,
            now=components.now(),
        )
    except PersonalWorkspaceExistsError as error:
        raise ApiError(status_code=409, code="CONFLICT") from error
    return _workspace_body(summary)


@router.get("/workspaces")
def index(session: DatabaseSession, user: CurrentUserDependency) -> dict[str, Any]:
    """List every Workspace the caller may enter."""
    summaries = list_workspaces(session, user_id=user.user_id)
    return {"workspaces": [_workspace_body(summary) for summary in summaries]}


@router.get("/workspaces/{workspace_id}")
def show(session: DatabaseSession, workspace: ReadableWorkspace) -> dict[str, Any]:
    """Describe one Workspace to one of its members."""
    return _workspace_body(describe_workspace(session, access=workspace.access))


@router.patch("/workspaces/{workspace_id}", dependencies=[Depends(require_csrf)])
def update(
    session: DatabaseSession, workspace: ManageableWorkspace, payload: WorkspaceUpdateRequest
) -> dict[str, Any]:
    """Change the Workspace settings its stewards control."""
    summary = update_workspace(
        session,
        access=workspace.access,
        name=None if payload.name is None else payload.name.strip(),
        publishing_role_policy=payload.publishing_role_policy,
    )
    return _workspace_body(summary)


@router.get("/workspaces/{workspace_id}/members")
def members(session: DatabaseSession, workspace: ListableMembers) -> dict[str, Any]:
    """List the people who can currently act inside this Workspace."""
    return {
        "members": [
            _member_body(member)
            for member in list_members(session, workspace_id=workspace.access.workspace_id)
        ]
    }


def _workspace_body(summary: WorkspaceSummary) -> dict[str, Any]:
    """Render one Workspace for its member."""
    return {
        "id": str(summary.workspace_id),
        "name": summary.name,
        "slug": summary.slug,
        "kind": summary.kind.value,
        "status": summary.status.value,
        "publishingRolePolicy": summary.publishing_role_policy.value,
        "role": summary.role.value,
        "createdAt": summary.created_at.isoformat(),
    }


def _member_body(member: MemberSummary) -> dict[str, Any]:
    """Render one fellow member for the Workspace they belong to."""
    return {
        "userId": str(member.user_id),
        "role": member.role.value,
        "displayName": member.display_name,
        "email": member.email,
        "joinedAt": member.joined_at.isoformat(),
    }
