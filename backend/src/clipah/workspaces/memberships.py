"""Transaction-scoped Workspace invitation and membership mutations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.db import set_workspace_context
from clipah.models import (
    User,
    Workspace,
    WorkspaceInvite,
    WorkspaceKind,
    WorkspaceMembership,
    WorkspaceMembershipEvent,
    WorkspaceMembershipEventKind,
    WorkspaceRole,
)
from clipah.workspaces.models import MemberSummary, WorkspaceAccess

INVITE_TOKEN_BYTES = 32


class CollaborationNotFoundError(Exception):
    """The invitation or member does not exist for this caller."""


class CollaborationConflictError(Exception):
    """The requested collaboration mutation conflicts with durable state."""


class CollaborationInvalidError(Exception):
    """The requested role or Workspace kind cannot support this mutation."""


@dataclass(frozen=True, slots=True)
class CreatedInvite:
    """One invitation and the raw token that is disclosed exactly once."""

    invite_id: UUID
    email: str
    role: WorkspaceRole
    token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PendingInvite:
    """Non-secret delivery metadata for one invitation that can still be accepted."""

    invite_id: UUID
    email: str
    role: WorkspaceRole
    expires_at: datetime


def list_pending_invites(
    session: Session, *, access: WorkspaceAccess, now: datetime
) -> tuple[PendingInvite, ...]:
    """List live invitations without ever returning their bearer-token hashes."""
    rows = session.scalars(
        select(WorkspaceInvite)
        .where(
            WorkspaceInvite.workspace_id == access.workspace_id,
            WorkspaceInvite.accepted_at.is_(None),
            WorkspaceInvite.revoked_at.is_(None),
            WorkspaceInvite.expires_at > now,
        )
        .order_by(WorkspaceInvite.created_at, WorkspaceInvite.id)
    )
    return tuple(
        PendingInvite(
            invite_id=row.id,
            email=row.email,
            role=row.role,
            expires_at=row.expires_at,
        )
        for row in rows
    )


def create_invite(
    session: Session,
    *,
    access: WorkspaceAccess,
    email: str,
    role: WorkspaceRole,
    now: datetime,
    expires_at: datetime,
    secret: str,
) -> CreatedInvite:
    """Create one hashed, expiring invitation for a team Workspace."""
    workspace = session.execute(
        select(Workspace).where(Workspace.id == access.workspace_id).with_for_update()
    ).scalar_one()
    if workspace.kind is not WorkspaceKind.TEAM or role is WorkspaceRole.OWNER:
        raise CollaborationInvalidError("this Workspace cannot issue that role")
    if expires_at <= now:
        raise CollaborationInvalidError("invite expiry must be in the future")

    invite_id = uuid4()
    token = f"{access.workspace_id}.{secret}"
    invite = WorkspaceInvite(
        id=invite_id,
        workspace_id=access.workspace_id,
        email=email.strip(),
        role=role,
        token_hash=_token_hash(token),
        created_by_user_id=access.user_id,
        created_at=now,
        expires_at=expires_at,
    )
    session.add(invite)
    session.flush()
    _event(
        session,
        access=access,
        kind=WorkspaceMembershipEventKind.INVITE_CREATED,
        invite_id=invite_id,
        new_role=role,
        now=now,
    )
    return CreatedInvite(
        invite_id=invite_id, email=invite.email, role=role, token=token, expires_at=expires_at
    )


def accept_invite(session: Session, *, user_id: UUID, token: str, now: datetime) -> MemberSummary:
    """Bind one valid invitation to the independently authenticated current User."""
    workspace_id = _workspace_id_from_token(token)
    set_workspace_context(session, workspace_id=workspace_id)
    invite = session.execute(
        select(WorkspaceInvite)
        .where(
            WorkspaceInvite.workspace_id == workspace_id,
            WorkspaceInvite.token_hash == _token_hash(token),
            WorkspaceInvite.accepted_at.is_(None),
            WorkspaceInvite.revoked_at.is_(None),
            WorkspaceInvite.expires_at > now,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if invite is None:
        raise CollaborationNotFoundError("no active invitation")

    membership = session.get(WorkspaceMembership, (workspace_id, user_id))
    if membership is None:
        membership = WorkspaceMembership(
            workspace_id=workspace_id,
            user_id=user_id,
            role=invite.role,
            invited_by_user_id=invite.created_by_user_id,
            joined_at=now,
        )
        session.add(membership)
    else:
        membership.role = invite.role
        membership.invited_by_user_id = invite.created_by_user_id
        membership.joined_at = now
        membership.removed_at = None
    invite.accepted_by_user_id = user_id
    invite.accepted_at = now
    session.flush()
    access = WorkspaceAccess(
        workspace_id=workspace_id,
        user_id=user_id,
        role=invite.role,
        publishing_role_policy=session.get_one(Workspace, workspace_id).publishing_role_policy,
    )
    _event(
        session,
        access=access,
        kind=WorkspaceMembershipEventKind.INVITE_ACCEPTED,
        member_user_id=user_id,
        invite_id=invite.id,
        new_role=invite.role,
        now=now,
    )
    return _member_summary(session, membership)


def remove_member(
    session: Session,
    *,
    access: WorkspaceAccess,
    member_user_id: UUID,
    now: datetime,
) -> None:
    """Remove one active member while preserving the last owner."""
    target = session.execute(
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == access.workspace_id,
            WorkspaceMembership.user_id == member_user_id,
            WorkspaceMembership.removed_at.is_(None),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if target is None:
        raise CollaborationNotFoundError("no active member")
    if target.role is WorkspaceRole.OWNER:
        owners = _lock_active_owners(session, workspace_id=access.workspace_id)
        if len(owners) == 1:
            raise CollaborationConflictError("the last owner cannot be removed")
    old_role = target.role
    target.removed_at = now
    _event(
        session,
        access=access,
        kind=WorkspaceMembershipEventKind.MEMBER_REMOVED,
        member_user_id=member_user_id,
        old_role=old_role,
        now=now,
    )


def revoke_invite(
    session: Session,
    *,
    access: WorkspaceAccess,
    invite_id: UUID,
    now: datetime,
) -> None:
    """Revoke one unused invitation without revealing foreign identifiers."""
    invite = session.execute(
        select(WorkspaceInvite)
        .where(
            WorkspaceInvite.workspace_id == access.workspace_id,
            WorkspaceInvite.id == invite_id,
            WorkspaceInvite.accepted_at.is_(None),
            WorkspaceInvite.revoked_at.is_(None),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if invite is None:
        raise CollaborationNotFoundError("no active invitation")
    invite.revoked_at = now
    _event(
        session,
        access=access,
        kind=WorkspaceMembershipEventKind.INVITE_REVOKED,
        invite_id=invite.id,
        old_role=invite.role,
        now=now,
    )


def change_member_role(
    session: Session,
    *,
    access: WorkspaceAccess,
    member_user_id: UUID,
    role: WorkspaceRole,
    now: datetime,
) -> MemberSummary:
    """Change one active non-owner role while protecting explicit ownership transfer."""
    if role is WorkspaceRole.OWNER:
        raise CollaborationInvalidError("ownership requires an explicit transfer")
    target = _active_member_for_update(
        session, workspace_id=access.workspace_id, user_id=member_user_id
    )
    if target.role is WorkspaceRole.OWNER:
        _lock_active_owners(session, workspace_id=access.workspace_id)
        raise CollaborationConflictError("an owner may only be demoted by transfer")
    old_role = target.role
    target.role = role
    _event(
        session,
        access=access,
        kind=WorkspaceMembershipEventKind.ROLE_CHANGED,
        member_user_id=member_user_id,
        old_role=old_role,
        new_role=role,
        now=now,
    )
    return _member_summary(session, target)


def transfer_ownership(
    session: Session,
    *,
    access: WorkspaceAccess,
    member_user_id: UUID,
    now: datetime,
) -> tuple[MemberSummary, MemberSummary]:
    """Atomically promote one active member and demote the acting owner."""
    workspace = session.get_one(Workspace, access.workspace_id)
    if workspace.kind is not WorkspaceKind.TEAM:
        raise CollaborationInvalidError("personal Workspace ownership cannot transfer")
    owners = _lock_active_owners(session, workspace_id=access.workspace_id)
    actor = next((member for member in owners if member.user_id == access.user_id), None)
    if actor is None:
        raise CollaborationNotFoundError("the acting owner is no longer active")
    target = _active_member_for_update(
        session, workspace_id=access.workspace_id, user_id=member_user_id
    )
    if target.user_id == actor.user_id:
        raise CollaborationConflictError("ownership already belongs to this member")
    old_role = target.role
    actor.role = WorkspaceRole.ADMIN
    target.role = WorkspaceRole.OWNER
    _event(
        session,
        access=access,
        kind=WorkspaceMembershipEventKind.OWNERSHIP_TRANSFERRED,
        member_user_id=member_user_id,
        old_role=old_role,
        new_role=WorkspaceRole.OWNER,
        now=now,
    )
    return _member_summary(session, actor), _member_summary(session, target)


def _workspace_id_from_token(token: str) -> UUID:
    """Read the non-secret tenant locator embedded in an invitation token."""
    prefix, separator, secret = token.partition(".")
    if separator != "." or not secret:
        raise CollaborationNotFoundError("malformed invitation")
    try:
        return UUID(prefix)
    except ValueError as error:
        raise CollaborationNotFoundError("malformed invitation") from error


def _token_hash(token: str) -> bytes:
    """Reduce an invitation bearer token to its irreversible database identity."""
    return sha256(token.encode()).digest()


def _member_summary(session: Session, membership: WorkspaceMembership) -> MemberSummary:
    """Return the public member fields after a successful mutation."""
    user = session.get_one(User, membership.user_id)
    return MemberSummary(
        user_id=user.id,
        role=membership.role,
        display_name=user.display_name,
        email=user.primary_email,
        joined_at=membership.joined_at,
    )


def _active_member_for_update(
    session: Session, *, workspace_id: UUID, user_id: UUID
) -> WorkspaceMembership:
    """Lock and return one active member without leaking foreign membership."""
    member = session.execute(
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.removed_at.is_(None),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if member is None:
        raise CollaborationNotFoundError("no active member")
    return member


def _lock_active_owners(session: Session, *, workspace_id: UUID) -> list[WorkspaceMembership]:
    """Serialize every mutation that could remove a Workspace's last owner."""
    return list(
        session.execute(
            select(WorkspaceMembership)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.role == WorkspaceRole.OWNER,
                WorkspaceMembership.removed_at.is_(None),
            )
            .order_by(WorkspaceMembership.user_id)
            .with_for_update()
        ).scalars()
    )


def _event(
    session: Session,
    *,
    access: WorkspaceAccess,
    kind: WorkspaceMembershipEventKind,
    now: datetime,
    member_user_id: UUID | None = None,
    invite_id: UUID | None = None,
    old_role: WorkspaceRole | None = None,
    new_role: WorkspaceRole | None = None,
) -> None:
    """Append one actor-attributed collaboration event."""
    session.add(
        WorkspaceMembershipEvent(
            id=uuid4(),
            workspace_id=access.workspace_id,
            member_user_id=member_user_id,
            invite_id=invite_id,
            actor_user_id=access.user_id,
            kind=kind,
            old_role=old_role,
            new_role=new_role,
            created_at=now,
        )
    )
