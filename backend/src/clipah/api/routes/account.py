"""Account deletion: the one request that ends a person's presence in Clipah."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.api.dependencies import (
    CurrentUserDependency,
    DatabaseSession,
    auth_components_for,
    clear_session_cookies,
    require_csrf,
    settings_for,
)
from clipah.api.errors import ApiError
from clipah.auth.sessions import revoke_all_sessions
from clipah.db import set_workspace_context
from clipah.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceKind,
    WorkspaceMembership,
    WorkspaceRole,
)
from clipah.publishing.use_cases import cancel_publications_approved_by
from clipah.retention.policy import RetentionEntityKind, RetentionPolicy
from clipah.retention.use_cases import schedule_tombstone
from clipah.workspaces.models import LastOwnerError

router = APIRouter(prefix="/api/v1", tags=["account"])


@router.delete("/account", status_code=204, dependencies=[Depends(require_csrf)])
def destroy(request: Request, session: DatabaseSession, user: CurrentUserDependency) -> Response:
    """Delete the caller's account, once it can be deleted without stranding anyone.

    Deletion is refused while the caller is the last owner of a live Workspace: a
    Workspace nobody owns cannot be recovered, renamed, or deleted by anyone, and other
    members would be left inside it. The caller transfers ownership or deletes the
    Workspace first, and both are things only they can decide.
    """
    components = auth_components_for(request)
    now = components.now()
    if not user.session.has_recent_authentication(policy=components.policy, now=now):
        raise ApiError(status_code=403, code="RECENT_AUTHENTICATION_REQUIRED")

    try:
        delete_account(
            session,
            user_id=user.user_id,
            policy=RetentionPolicy.from_settings(settings_for(request)),
            now=now,
        )
    except LastOwnerError as error:
        raise ApiError(status_code=409, code="LAST_OWNER") from error

    response = Response(status_code=204)
    clear_session_cookies(response, settings=settings_for(request))
    return response


def delete_account(
    session: Session, *, user_id: UUID, policy: RetentionPolicy, now: datetime
) -> None:
    """End one person's access everywhere and schedule the erasure of who they were.

    What stops immediately is everything that could still act: Sessions, Memberships,
    and the future work this person approved. What waits is the identity data itself,
    so an account deleted by mistake, or under duress, is not irrecoverable the same
    minute — and so the audit trail keeps naming an actor while anyone still needs it.
    """
    _require_no_stranded_workspace(session, user_id=user_id, now=now)

    account = session.get(User, user_id)
    if account is None:
        raise ApiError(status_code=404, code="NOT_FOUND")
    account.status = UserStatus.DELETED
    account.deleted_at = now

    memberships = session.scalars(
        select(WorkspaceMembership).where(
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.removed_at.is_(None),
        )
    ).all()
    for membership in memberships:
        set_workspace_context(session, workspace_id=membership.workspace_id)
        cancel_publications_approved_by(
            session, workspace_id=membership.workspace_id, user_id=user_id, now=now
        )
        membership.removed_at = now
        session.flush()

    revoke_all_sessions(session, user_id=user_id, now=now)
    home = _personal_workspace(session, user_id=user_id)
    set_workspace_context(session, workspace_id=home)
    schedule_tombstone(
        session,
        workspace_id=home,
        entity_kind=RetentionEntityKind.USER,
        entity_id=user_id,
        storage_prefix=None,
        eligible_at=policy.eligible_at(RetentionEntityKind.USER, now=now),
    )
    session.flush()


def _require_no_stranded_workspace(session: Session, *, user_id: UUID, now: datetime) -> None:
    """Refuse while any live Workspace would be left without an owner."""
    del now
    owned = session.scalars(
        select(WorkspaceMembership.workspace_id)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .where(
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.removed_at.is_(None),
            WorkspaceMembership.role == WorkspaceRole.OWNER,
            Workspace.deleted_at.is_(None),
        )
    ).all()
    for workspace_id in owned:
        set_workspace_context(session, workspace_id=workspace_id)
        other_owner = session.scalars(
            select(WorkspaceMembership.user_id)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id != user_id,
                WorkspaceMembership.removed_at.is_(None),
                WorkspaceMembership.role == WorkspaceRole.OWNER,
            )
            .limit(1)
        ).first()
        if other_owner is None:
            raise LastOwnerError("transfer ownership or delete this workspace first")


def _personal_workspace(session: Session, *, user_id: UUID) -> UUID:
    """Return the Workspace one person's own tombstone belongs in.

    A tombstone is Workspace-scoped, and the one Workspace that exists for this person
    alone is their personal one, so their erasure is recorded there rather than inside
    somebody else's team.
    """
    workspace_id = session.scalars(
        select(WorkspaceMembership.workspace_id)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .where(
            WorkspaceMembership.user_id == user_id,
            Workspace.kind == WorkspaceKind.PERSONAL,
        )
        .limit(1)
    ).first()
    if workspace_id is None:
        raise ApiError(status_code=409, code="CONFLICT")
    return workspace_id
