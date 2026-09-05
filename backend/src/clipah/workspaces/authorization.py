"""The Workspace role matrix and the database-backed authorizer built on it."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.models import (
    PublishingRolePolicy,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from clipah.workspaces.models import (
    WorkspaceAccess,
    WorkspaceAction,
    WorkspaceNotFoundError,
    WorkspacePermissionError,
)

Action = WorkspaceAction
Role = WorkspaceRole

_VIEWER_ACTIONS = frozenset({Action.WORKSPACE_READ, Action.MEMBER_READ, Action.PROJECT_READ})
_REVIEWER_ACTIONS = _VIEWER_ACTIONS | {Action.REVIEW_DECIDE}
_EDITOR_ACTIONS = _REVIEWER_ACTIONS | {Action.PROJECT_WRITE, Action.EDIT_WRITE}
_ADMIN_ACTIONS = _EDITOR_ACTIONS | {
    Action.WORKSPACE_UPDATE,
    Action.MEMBER_MANAGE,
    Action.SOCIAL_CONNECTION_MANAGE,
    Action.SOURCE_CONNECTION_READ,
    Action.SOURCE_CONNECTION_MANAGE,
}
_OWNER_ACTIONS = _ADMIN_ACTIONS | {Action.WORKSPACE_DELETE, Action.OWNERSHIP_TRANSFER}

ROLE_ACTIONS = MappingProxyType(
    {
        Role.VIEWER: _VIEWER_ACTIONS,
        Role.REVIEWER: _REVIEWER_ACTIONS,
        Role.EDITOR: _EDITOR_ACTIONS,
        Role.ADMIN: _ADMIN_ACTIONS,
        Role.OWNER: _OWNER_ACTIONS,
    }
)
# Publishing authority is deliberately absent above: it follows Workspace policy.
PUBLISHING_ROLES = MappingProxyType(
    {
        PublishingRolePolicy.OWNER_ADMIN_EDITOR: frozenset({Role.OWNER, Role.ADMIN, Role.EDITOR}),
        PublishingRolePolicy.OWNER_ADMIN: frozenset({Role.OWNER, Role.ADMIN}),
    }
)
# Operations whose damage is irreversible enough to demand a fresh authentication.
RECENT_AUTHENTICATION_ACTIONS = frozenset(
    {
        Action.WORKSPACE_DELETE,
        Action.OWNERSHIP_TRANSFER,
        Action.MEMBER_MANAGE,
        Action.SOCIAL_CONNECTION_MANAGE,
        Action.SOURCE_CONNECTION_MANAGE,
    }
)


def permits(access: WorkspaceAccess, action: WorkspaceAction) -> bool:
    """Report whether one member's role carries one authority in their Workspace."""
    if action is Action.PUBLISH:
        return access.role in PUBLISHING_ROLES[access.publishing_role_policy]
    return action in ROLE_ACTIONS[access.role]


def requires_recent_authentication(action: WorkspaceAction) -> bool:
    """Report whether the action may only proceed on a freshly authenticated Session."""
    return action in RECENT_AUTHENTICATION_ACTIONS


@dataclass(frozen=True, slots=True)
class DatabaseWorkspaceAuthorizer:
    """Answer authorization from live Membership rows inside the caller's transaction."""

    session: Session

    def access_for(self, *, user_id: UUID, workspace_id: UUID) -> WorkspaceAccess:
        """Return the caller's standing, or refuse to admit the Workspace exists."""
        row = self.session.execute(
            select(WorkspaceMembership.role, Workspace.publishing_role_policy)
            .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.removed_at.is_(None),
                Workspace.status == WorkspaceStatus.ACTIVE,
                Workspace.deleted_at.is_(None),
            )
        ).one_or_none()
        if row is None:
            raise WorkspaceNotFoundError("no such workspace for this user")
        return WorkspaceAccess(
            workspace_id=workspace_id,
            user_id=user_id,
            role=row.role,
            publishing_role_policy=row.publishing_role_policy,
        )

    def require(
        self, *, user_id: UUID, workspace_id: UUID, action: WorkspaceAction
    ) -> WorkspaceAccess:
        """Return the caller's access, or refuse the action."""
        access = self.access_for(user_id=user_id, workspace_id=workspace_id)
        if not permits(access, action):
            raise WorkspacePermissionError(f"{access.role.value} may not {action.value}")
        return access
