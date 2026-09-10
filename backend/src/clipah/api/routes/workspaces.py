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
    settings_for,
)
from clipah.api.errors import ApiError
from clipah.api.request_id import request_id_for
from clipah.models import (
    PublishingRolePolicy,
    WorkspaceKind,
    WorkspaceRole,
    WorkspaceStatus,
)
from clipah.retention.policy import RetentionPolicy
from clipah.workspaces.models import (
    MemberSummary,
    PersonalWorkspaceExistsError,
    WorkspaceAction,
    WorkspaceAlreadyDeletedError,
    WorkspaceNotFoundError,
    WorkspaceRecoveryWindowElapsedError,
    WorkspaceSummary,
)
from clipah.workspaces.use_cases import (
    create_workspace,
    delete_workspace,
    describe_workspace,
    list_members,
    list_workspaces,
    restore_workspace,
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


class WorkspaceDeletionResponse(BaseModel):
    """What a member is told when they delete a Workspace."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    workspace_id: UUID = Field(alias="workspaceId")
    recoverable_until: datetime = Field(alias="recoverableUntil")
    # Stated rather than implied: Clipah stops publishing and forgets its credentials,
    # but a post already live on YouTube, Instagram, or TikTok stays there until the
    # member removes it on that platform themselves.
    published_posts_remain_on_providers: bool = Field(
        default=True, alias="publishedPostsRemainOnProviders"
    )

    @field_serializer("recoverable_until")
    def serialize_recoverable_until(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return value.isoformat()


ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.WORKSPACE_READ))
]
ManageableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.WORKSPACE_UPDATE))
]
ListableMembers = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.MEMBER_READ))
]
DeletableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.WORKSPACE_DELETE))
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


@router.delete(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceDeletionResponse,
    dependencies=[Depends(require_csrf)],
)
def destroy(
    request: Request, session: DatabaseSession, workspace: DeletableWorkspace
) -> WorkspaceDeletionResponse:
    """Close one Workspace, revoke what it can still act with, and schedule its removal."""
    try:
        deletion = delete_workspace(
            session,
            access=workspace.access,
            policy=RetentionPolicy.from_settings(settings_for(request)),
            request_id=request_id_for(request),
            now=auth_components_for(request).now(),
        )
    except WorkspaceAlreadyDeletedError as error:
        raise ApiError(status_code=409, code="CONFLICT") from error
    return WorkspaceDeletionResponse(
        workspaceId=deletion.workspace_id, recoverableUntil=deletion.recoverable_until
    )


@router.post(
    "/workspaces/{workspace_id}/restore",
    response_model=WorkspaceResponse,
    dependencies=[Depends(require_csrf)],
)
def restore(
    request: Request,
    workspace_id: UUID,
    session: DatabaseSession,
    user: CurrentUserDependency,
) -> WorkspaceResponse:
    """Bring back a deleted Workspace for its owner while recovery is still possible.

    Recovery cannot go through the ordinary Workspace dependency, because that
    dependency refuses to admit a deleted Workspace exists at all — which is exactly
    what it should do everywhere else.
    """
    try:
        restored = restore_workspace(
            session,
            user_id=user.user_id,
            workspace_id=workspace_id,
            now=auth_components_for(request).now(),
        )
    except WorkspaceNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except WorkspaceRecoveryWindowElapsedError as error:
        raise ApiError(status_code=409, code="CONFLICT") from error
    return _workspace_body(restored)


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
