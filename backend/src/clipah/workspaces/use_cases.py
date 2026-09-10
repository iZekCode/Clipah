"""Workspace creation, selection, and settings as transaction-scoped use cases."""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.db import set_workspace_context
from clipah.models import (
    AuditEvent,
    OAuthGrant,
    PublishingRolePolicy,
    RetentionTombstone,
    SocialAccount,
    SourceConnection,
    SourceConnectionStatus,
    User,
    Workspace,
    WorkspaceKind,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from clipah.publishing.use_cases import PublicationFutureWorkCoordinator
from clipah.retention.policy import RetentionEntityKind, RetentionPolicy, workspace_prefix
from clipah.retention.use_cases import cancel_tombstone, schedule_tombstone
from clipah.social_accounts.models import SocialConnectionStatus
from clipah.workspaces.models import (
    MemberSummary,
    PersonalWorkspaceExistsError,
    WorkspaceAccess,
    WorkspaceAlreadyDeletedError,
    WorkspaceDeletion,
    WorkspaceNotFoundError,
    WorkspaceRecoveryWindowElapsedError,
    WorkspaceSummary,
)

UNSAFE_SLUG_CHARACTERS = re.compile(r"[^a-z0-9]+")
MAX_SLUG_STEM_LENGTH = 32
MAX_WORKSPACE_NAME_LENGTH = 120


def workspace_slug(name: str) -> str:
    """Derive a collision-resistant slug that never exposes provider identifiers."""
    stem = UNSAFE_SLUG_CHARACTERS.sub("-", name.lower()).strip("-")
    stem = stem[:MAX_SLUG_STEM_LENGTH].strip("-") or "workspace"
    return f"{stem}-{uuid4().hex[:8]}"


def create_workspace(
    session: Session,
    *,
    owner_user_id: UUID,
    name: str,
    kind: WorkspaceKind,
    now: datetime,
) -> WorkspaceSummary:
    """Create a Workspace and enroll its creator as the owner in one transaction."""
    if kind is WorkspaceKind.PERSONAL and _owns_personal_workspace(session, owner_user_id):
        raise PersonalWorkspaceExistsError("this user already has a personal workspace")

    workspace = Workspace(
        id=uuid4(),
        name=name,
        slug=workspace_slug(name),
        kind=kind,
        publishing_role_policy=PublishingRolePolicy.OWNER_ADMIN_EDITOR,
        status=WorkspaceStatus.ACTIVE,
        created_at=now,
    )
    session.add(workspace)
    session.flush()

    # The Membership is a tenant row, so the new Workspace must be the tenant in context.
    set_workspace_context(session, workspace_id=workspace.id)
    session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=owner_user_id,
            role=WorkspaceRole.OWNER,
            joined_at=now,
        )
    )
    session.flush()
    return _summarize(workspace, WorkspaceRole.OWNER)


def list_workspaces(session: Session, *, user_id: UUID) -> list[WorkspaceSummary]:
    """List every Workspace this User may still enter, oldest membership first."""
    rows = session.execute(
        select(Workspace, WorkspaceMembership.role)
        .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
        .where(
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.removed_at.is_(None),
            Workspace.status == WorkspaceStatus.ACTIVE,
            Workspace.deleted_at.is_(None),
        )
        .order_by(Workspace.created_at, Workspace.id)
    ).all()
    return [_summarize(workspace, role) for workspace, role in rows]


def describe_workspace(session: Session, *, access: WorkspaceAccess) -> WorkspaceSummary:
    """Describe the Workspace the caller has already been authorized against."""
    return _summarize(_load(session, access.workspace_id), access.role)


def update_workspace(
    session: Session,
    *,
    access: WorkspaceAccess,
    name: str | None = None,
    publishing_role_policy: PublishingRolePolicy | None = None,
) -> WorkspaceSummary:
    """Apply the Workspace settings an owner or admin is allowed to change."""
    workspace = _load(session, access.workspace_id)
    if name is not None:
        workspace.name = name
    if publishing_role_policy is not None:
        workspace.publishing_role_policy = publishing_role_policy
    session.flush()
    return _summarize(workspace, access.role)


def list_members(session: Session, *, workspace_id: UUID) -> list[MemberSummary]:
    """List the people who can currently act inside one Workspace."""
    rows = session.execute(
        select(WorkspaceMembership, User)
        .join(User, User.id == WorkspaceMembership.user_id)
        .where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.removed_at.is_(None),
        )
        .order_by(WorkspaceMembership.joined_at, WorkspaceMembership.user_id)
    ).all()
    return [
        MemberSummary(
            user_id=membership.user_id,
            role=membership.role,
            display_name=user.display_name,
            email=user.primary_email,
            joined_at=membership.joined_at,
        )
        for membership, user in rows
    ]


def delete_workspace(
    session: Session,
    *,
    access: WorkspaceAccess,
    policy: RetentionPolicy,
    request_id: str,
    now: datetime,
) -> WorkspaceDeletion:
    """Close one Workspace to further work and schedule the removal of what it holds.

    Everything that could still act on the outside world is stopped here rather than in
    thirty days: credentials are revoked and cryptographically erased, and unpublished
    work is cancelled. Posts already published stay on their platforms, because Clipah
    cannot and should not remove someone's published content on their behalf.
    """
    workspace = _load(session, access.workspace_id)
    if workspace.deleted_at is not None:
        raise WorkspaceAlreadyDeletedError("workspace is already deleted")
    workspace.status = WorkspaceStatus.DELETED
    workspace.deleted_at = now
    session.flush()

    revoke_source_connections(session, workspace_id=access.workspace_id, now=now)
    revoke_social_accounts(session, workspace_id=access.workspace_id, now=now)
    session.add(
        AuditEvent(
            workspace_id=access.workspace_id,
            actor_user_id=access.user_id,
            action="workspace.deleted",
            target_kind="workspace",
            target_id=access.workspace_id,
            before_metadata={"status": WorkspaceStatus.ACTIVE.value},
            after_metadata={"status": WorkspaceStatus.DELETED.value},
            request_id=request_id,
            created_at=now,
        )
    )
    eligible_at = policy.eligible_at(RetentionEntityKind.WORKSPACE, now=now)
    schedule_tombstone(
        session,
        workspace_id=access.workspace_id,
        entity_kind=RetentionEntityKind.WORKSPACE,
        entity_id=access.workspace_id,
        storage_prefix=workspace_prefix(access.workspace_id),
        eligible_at=eligible_at,
    )
    session.flush()
    return WorkspaceDeletion(workspace_id=access.workspace_id, recoverable_until=eligible_at)


def restore_workspace(
    session: Session, *, user_id: UUID, workspace_id: UUID, now: datetime
) -> WorkspaceSummary:
    """Bring back a Workspace its owner deleted, while its recovery window is open.

    Only an owner may recover one, and only before the purge becomes eligible: after
    that the data may already be gone, so a restore that appeared to succeed would be
    a lie. Connections stay revoked — recovery returns the Workspace, not credentials
    the providers were told to forget.
    """
    row = session.execute(
        select(Workspace, WorkspaceMembership.role)
        .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
        .where(
            Workspace.id == workspace_id,
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.removed_at.is_(None),
            WorkspaceMembership.role == WorkspaceRole.OWNER,
            Workspace.deleted_at.is_not(None),
        )
    ).one_or_none()
    if row is None:
        raise WorkspaceNotFoundError("no such deleted workspace for this user")
    workspace, role = row

    set_workspace_context(session, workspace_id=workspace_id)
    pending = session.scalar(
        select(RetentionTombstone).where(
            RetentionTombstone.workspace_id == workspace_id,
            RetentionTombstone.entity_kind == RetentionEntityKind.WORKSPACE.value,
            RetentionTombstone.entity_id == workspace_id,
            RetentionTombstone.deleted_at.is_(None),
        )
    )
    if pending is None or pending.eligible_at <= now:
        raise WorkspaceRecoveryWindowElapsedError("workspace recovery window elapsed")

    workspace.status = WorkspaceStatus.ACTIVE
    workspace.deleted_at = None
    cancel_tombstone(
        session,
        workspace_id=workspace_id,
        entity_kind=RetentionEntityKind.WORKSPACE,
        entity_id=workspace_id,
    )
    session.flush()
    return _summarize(workspace, role)


def revoke_source_connections(session: Session, *, workspace_id: UUID, now: datetime) -> int:
    """Revoke every source credential this Workspace still holds."""
    connections = session.scalars(
        select(SourceConnection).where(
            SourceConnection.workspace_id == workspace_id,
            SourceConnection.status == SourceConnectionStatus.ACTIVE,
        )
    )
    revoked = 0
    for connection in connections:
        connection.status = SourceConnectionStatus.REVOKED
        connection.revoked_at = now
        revoked += 1
    session.flush()
    return revoked


def revoke_social_accounts(session: Session, *, workspace_id: UUID, now: datetime) -> int:
    """Revoke every publishing destination and erase the grants that reached them.

    Erasure is local and unconditional: a provider that cannot be reached must not be
    the reason a usable token survives inside Clipah.
    """
    accounts = session.scalars(
        select(SocialAccount).where(
            SocialAccount.workspace_id == workspace_id,
            SocialAccount.connection_status != SocialConnectionStatus.REVOKED,
        )
    )
    coordinator = PublicationFutureWorkCoordinator(session)
    revoked = 0
    for account in accounts:
        account.connection_status = SocialConnectionStatus.REVOKED
        account.revoked_at = now
        session.flush()
        coordinator.cancel_or_pause_unpublished(
            workspace_id=workspace_id, social_account_id=account.id, now=now
        )
        grant = session.scalar(
            select(OAuthGrant).where(
                OAuthGrant.workspace_id == workspace_id,
                OAuthGrant.social_account_id == account.id,
                OAuthGrant.revoked_at.is_(None),
            )
        )
        if grant is not None:
            grant.wrapped_key = b""
            grant.nonce = b""
            grant.ciphertext = b""
            grant.revoked_at = now
        revoked += 1
    session.flush()
    return revoked


def _owns_personal_workspace(session: Session, user_id: UUID) -> bool:
    """Report whether login already bootstrapped this User's personal Workspace."""
    return (
        session.execute(
            select(WorkspaceMembership.workspace_id)
            .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
            .where(
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.removed_at.is_(None),
                Workspace.kind == WorkspaceKind.PERSONAL,
                Workspace.deleted_at.is_(None),
            )
            .limit(1)
        ).first()
        is not None
    )


def _load(session: Session, workspace_id: UUID) -> Workspace:
    """Load a Workspace the caller was authorized against moments ago."""
    workspace = session.get(Workspace, workspace_id)
    if workspace is None:
        raise WorkspaceNotFoundError("no such workspace")
    return workspace


def _summarize(workspace: Workspace, role: WorkspaceRole) -> WorkspaceSummary:
    """Render one Workspace as its member sees it."""
    return WorkspaceSummary(
        workspace_id=workspace.id,
        name=workspace.name,
        slug=workspace.slug,
        kind=workspace.kind,
        status=workspace.status,
        publishing_role_policy=workspace.publishing_role_policy,
        role=role,
        created_at=workspace.created_at,
    )
