"""Workspace creation, selection, settings, and membership endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from clipah.api.dependencies import (
    CurrentUserDependency,
    CurrentWorkspace,
    DatabaseSession,
    auth_components_for,
    require_csrf,
    require_workspace,
)
from clipah.api.errors import ApiError
from clipah.models import (
    PublishingRolePolicy,
    WorkspaceKind,
    WorkspaceRole,
    WorkspaceStatus,
)
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


class WorkspaceResponse(BaseModel):
    """One Workspace as it is safe to show a member of it."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    name: str
    slug: str
    kind: WorkspaceKind
    status: WorkspaceStatus
    publishing_role_policy: PublishingRolePolicy = Field(alias="publishingRolePolicy")
    role: WorkspaceRole
    created_at: datetime = Field(alias="createdAt")

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return value.isoformat()


class WorkspaceCollectionResponse(BaseModel):
    """Every Workspace the caller may enter."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    workspaces: tuple[WorkspaceResponse, ...]


class MemberResponse(BaseModel):
    """One fellow member as it is safe to show inside their Workspace."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    user_id: UUID = Field(alias="userId")
    role: WorkspaceRole
    display_name: str = Field(alias="displayName")
    email: str
    joined_at: datetime = Field(alias="joinedAt")

    @field_serializer("joined_at")
    def serialize_joined_at(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return value.isoformat()


class MemberCollectionResponse(BaseModel):
    """The people who can currently act inside one Workspace."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    members: tuple[MemberResponse, ...]


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


@router.post(
    "/workspaces",
    status_code=201,
    response_model=WorkspaceResponse,
    dependencies=[Depends(require_csrf)],
)
def create(
    request: Request,
    session: DatabaseSession,
    user: CurrentUserDependency,
    payload: WorkspaceCreateRequest,
) -> WorkspaceResponse:
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


@router.get("/workspaces", response_model=WorkspaceCollectionResponse)
def index(session: DatabaseSession, user: CurrentUserDependency) -> WorkspaceCollectionResponse:
    """List every Workspace the caller may enter."""
    summaries = list_workspaces(session, user_id=user.user_id)
    return WorkspaceCollectionResponse(
        workspaces=tuple(_workspace_body(summary) for summary in summaries)
    )


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
def show(session: DatabaseSession, workspace: ReadableWorkspace) -> WorkspaceResponse:
    """Describe one Workspace to one of its members."""
    return _workspace_body(describe_workspace(session, access=workspace.access))


@router.patch(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceResponse,
    dependencies=[Depends(require_csrf)],
)
def update(
    session: DatabaseSession, workspace: ManageableWorkspace, payload: WorkspaceUpdateRequest
) -> WorkspaceResponse:
    """Change the Workspace settings its stewards control."""
    summary = update_workspace(
        session,
        access=workspace.access,
        name=None if payload.name is None else payload.name.strip(),
        publishing_role_policy=payload.publishing_role_policy,
    )
    return _workspace_body(summary)


@router.get("/workspaces/{workspace_id}/members", response_model=MemberCollectionResponse)
def members(session: DatabaseSession, workspace: ListableMembers) -> MemberCollectionResponse:
    """List the people who can currently act inside this Workspace."""
    return MemberCollectionResponse(
        members=tuple(
            _member_body(member)
            for member in list_members(session, workspace_id=workspace.access.workspace_id)
        )
    )


def _workspace_body(summary: WorkspaceSummary) -> WorkspaceResponse:
    """Render one Workspace for its member."""
    return WorkspaceResponse(
        id=summary.workspace_id,
        name=summary.name,
        slug=summary.slug,
        kind=summary.kind,
        status=summary.status,
        publishingRolePolicy=summary.publishing_role_policy,
        role=summary.role,
        createdAt=summary.created_at,
    )


def _member_body(member: MemberSummary) -> MemberResponse:
    """Render one fellow member for the Workspace they belong to."""
    return MemberResponse(
        userId=member.user_id,
        role=member.role,
        displayName=member.display_name,
        email=member.email,
        joinedAt=member.joined_at,
    )
