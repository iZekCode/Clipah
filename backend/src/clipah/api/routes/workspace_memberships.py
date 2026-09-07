"""Feature-gated Workspace invitation and membership mutation endpoints."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
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
from clipah.api.routes.workspaces import MemberCollectionResponse, MemberResponse, _member_body
from clipah.models import WorkspaceRole
from clipah.workspaces.memberships import (
    CollaborationConflictError,
    CollaborationInvalidError,
    CollaborationNotFoundError,
    PendingInvite,
    accept_invite,
    change_member_role,
    create_invite,
    list_pending_invites,
    remove_member,
    revoke_invite,
    transfer_ownership,
)
from clipah.workspaces.models import WorkspaceAction
from clipah.workspaces.use_cases import list_members

router = APIRouter(prefix="/api/v1", tags=["workspace-memberships"])
ManageableMembers = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.MEMBER_MANAGE))
]
ReadableMembers = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.MEMBER_READ))
]
TransferableOwnership = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.OWNERSHIP_TRANSFER))
]
INVITE_LIFETIME = timedelta(days=7)


class InviteCreateRequest(BaseModel):
    """The delivery address and authority an owner or admin selected."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")
    role: WorkspaceRole


class InviteResponse(BaseModel):
    """One invitation including the bearer token disclosed only on creation."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    email: str
    role: WorkspaceRole
    token: str
    expires_at: datetime = Field(alias="expiresAt")

    @field_serializer("expires_at")
    def serialize_expires_at(self, value: datetime) -> str:
        """Return an explicit-offset invitation deadline."""
        return value.isoformat()


class PendingInviteResponse(BaseModel):
    """Invitation delivery metadata with no reusable bearer credential."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    email: str
    role: WorkspaceRole
    expires_at: datetime = Field(alias="expiresAt")

    @field_serializer("expires_at")
    def serialize_expires_at(self, value: datetime) -> str:
        """Return an explicit-offset invitation deadline."""
        return value.isoformat()


class PendingInviteCollectionResponse(BaseModel):
    """Every invitation that remains available for acceptance."""

    model_config = ConfigDict(extra="forbid")

    invites: tuple[PendingInviteResponse, ...]


class MemberRoleRequest(BaseModel):
    """The explicit non-owner role selected for an active member."""

    model_config = ConfigDict(extra="forbid")

    role: WorkspaceRole


class OwnershipTransferRequest(BaseModel):
    """The active member who will become the Workspace owner."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    user_id: UUID = Field(alias="userId")


def _enabled(request: Request) -> None:
    """Hide collaboration mutation routes unless explicitly enabled."""
    if not settings_for(request).collaboration_enabled:
        raise ApiError(status_code=404, code="NOT_FOUND")


@router.get(
    "/workspaces/{workspace_id}/invites",
    response_model=PendingInviteCollectionResponse,
)
def list_workspace_invites(
    request: Request,
    session: DatabaseSession,
    workspace: ReadableMembers,
) -> PendingInviteCollectionResponse:
    """List active invitation metadata without disclosing bearer tokens."""
    _enabled(request)
    return PendingInviteCollectionResponse(
        invites=tuple(
            _pending_invite_body(invite)
            for invite in list_pending_invites(
                session,
                access=workspace.access,
                now=auth_components_for(request).now(),
            )
        )
    )


@router.post(
    "/workspaces/{workspace_id}/invites",
    status_code=201,
    response_model=InviteResponse,
    dependencies=[Depends(require_csrf)],
)
def create_workspace_invite(
    request: Request,
    session: DatabaseSession,
    workspace: ManageableMembers,
    payload: InviteCreateRequest,
) -> InviteResponse:
    """Create one role-bound team Workspace invitation."""
    _enabled(request)
    now = auth_components_for(request).now()
    try:
        created = create_invite(
            session,
            access=workspace.access,
            email=str(payload.email),
            role=payload.role,
            now=now,
            expires_at=now + INVITE_LIFETIME,
            secret=secrets.token_urlsafe(32),
        )
    except CollaborationInvalidError as error:
        raise ApiError(status_code=422, code="VALIDATION_ERROR") from error
    return InviteResponse(
        id=created.invite_id,
        email=created.email,
        role=created.role,
        token=created.token,
        expiresAt=created.expires_at,
    )


@router.post(
    "/workspace-invites/{token}/accept",
    response_model=MemberResponse,
    dependencies=[Depends(require_csrf)],
)
def accept_workspace_invite(
    request: Request,
    token: str,
    session: DatabaseSession,
    user: CurrentUserDependency,
) -> MemberResponse:
    """Accept one invitation as the independently authenticated current User."""
    _enabled(request)
    try:
        member = accept_invite(
            session, user_id=user.user_id, token=token, now=auth_components_for(request).now()
        )
    except CollaborationNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return _member_body(member)


@router.delete(
    "/workspaces/{workspace_id}/invites/{invite_id}",
    status_code=204,
    dependencies=[Depends(require_csrf)],
)
def revoke_workspace_invite(
    request: Request,
    invite_id: UUID,
    session: DatabaseSession,
    workspace: ManageableMembers,
) -> Response:
    """Revoke one invitation before it is accepted."""
    _enabled(request)
    try:
        revoke_invite(
            session,
            access=workspace.access,
            invite_id=invite_id,
            now=auth_components_for(request).now(),
        )
    except CollaborationNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return Response(status_code=204)


@router.patch(
    "/workspaces/{workspace_id}/members/{member_user_id}",
    response_model=MemberResponse,
    dependencies=[Depends(require_csrf)],
)
def update_workspace_member(
    request: Request,
    member_user_id: UUID,
    payload: MemberRoleRequest,
    session: DatabaseSession,
    workspace: ManageableMembers,
) -> MemberResponse:
    """Assign one explicit non-owner role to an active member."""
    _enabled(request)
    try:
        member = change_member_role(
            session,
            access=workspace.access,
            member_user_id=member_user_id,
            role=payload.role,
            now=auth_components_for(request).now(),
        )
    except CollaborationNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except CollaborationConflictError as error:
        raise ApiError(status_code=409, code="CONFLICT") from error
    except CollaborationInvalidError as error:
        raise ApiError(status_code=422, code="VALIDATION_ERROR") from error
    return _member_body(member)


@router.post(
    "/workspaces/{workspace_id}/ownership-transfers",
    response_model=MemberCollectionResponse,
    dependencies=[Depends(require_csrf)],
)
def transfer_workspace_ownership(
    request: Request,
    payload: OwnershipTransferRequest,
    session: DatabaseSession,
    workspace: TransferableOwnership,
) -> MemberCollectionResponse:
    """Transfer team Workspace ownership to one active member atomically."""
    _enabled(request)
    try:
        transfer_ownership(
            session,
            access=workspace.access,
            member_user_id=payload.user_id,
            now=auth_components_for(request).now(),
        )
    except CollaborationNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except CollaborationConflictError as error:
        raise ApiError(status_code=409, code="CONFLICT") from error
    except CollaborationInvalidError as error:
        raise ApiError(status_code=422, code="VALIDATION_ERROR") from error
    return MemberCollectionResponse(
        members=tuple(
            _member_body(member)
            for member in list_members(session, workspace_id=workspace.access.workspace_id)
        )
    )


@router.delete(
    "/workspaces/{workspace_id}/members/{member_user_id}",
    status_code=204,
    dependencies=[Depends(require_csrf)],
)
def remove_workspace_member(
    request: Request,
    member_user_id: UUID,
    session: DatabaseSession,
    workspace: ManageableMembers,
) -> Response:
    """Remove one member and make every existing Session lose tenant access."""
    _enabled(request)
    try:
        remove_member(
            session,
            access=workspace.access,
            member_user_id=member_user_id,
            now=auth_components_for(request).now(),
        )
    except CollaborationNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except CollaborationConflictError as error:
        raise ApiError(status_code=409, code="CONFLICT") from error
    return Response(status_code=204)


def _pending_invite_body(invite: PendingInvite) -> PendingInviteResponse:
    """Render reusable invitation metadata while excluding credential material."""
    return PendingInviteResponse(
        id=invite.invite_id,
        email=invite.email,
        role=invite.role,
        expiresAt=invite.expires_at,
    )
