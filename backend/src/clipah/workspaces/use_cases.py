"""Workspace creation, selection, and settings as transaction-scoped use cases."""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.db import set_workspace_context
from clipah.models import (
    PublishingRolePolicy,
    User,
    Workspace,
    WorkspaceKind,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from clipah.workspaces.models import (
    MemberSummary,
    PersonalWorkspaceExistsError,
    WorkspaceAccess,
    WorkspaceNotFoundError,
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
