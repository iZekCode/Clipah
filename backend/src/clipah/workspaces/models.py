"""Workspace authorization values shared by use cases, routes, and workers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from clipah.models import PublishingRolePolicy, WorkspaceKind, WorkspaceRole, WorkspaceStatus


class WorkspaceError(Exception):
    """Base class for every Workspace failure the API maps to a stable code."""


class WorkspaceNotFoundError(WorkspaceError):
    """The Workspace does not exist, or the caller may not know that it does.

    Both cases raise this one error on purpose: a guessed identifier must be
    answered exactly like an identifier that was never issued.
    """


class WorkspacePermissionError(WorkspaceError):
    """The caller belongs to the Workspace but the action is above their role."""


class PersonalWorkspaceExistsError(WorkspaceError):
    """Login already created the single personal Workspace a User may own."""


class WorkspaceAction(StrEnum):
    """Every distinct authority a Workspace role can carry."""

    WORKSPACE_READ = "workspace:read"
    WORKSPACE_UPDATE = "workspace:update"
    WORKSPACE_DELETE = "workspace:delete"
    MEMBER_READ = "member:read"
    MEMBER_MANAGE = "member:manage"
    OWNERSHIP_TRANSFER = "ownership:transfer"
    PROJECT_READ = "project:read"
    PROJECT_WRITE = "project:write"
    EDIT_WRITE = "edit:write"
    REVIEW_DECIDE = "review:decide"
    PUBLISH = "publish"
    SOCIAL_CONNECTION_MANAGE = "social_connection:manage"
    SOURCE_CONNECTION_READ = "source_connection:read"
    SOURCE_CONNECTION_MANAGE = "source_connection:manage"


@dataclass(frozen=True, slots=True)
class WorkspaceAccess:
    """One User's proven standing inside one Workspace, for one transaction."""

    workspace_id: UUID
    user_id: UUID
    role: WorkspaceRole
    publishing_role_policy: PublishingRolePolicy


@dataclass(frozen=True, slots=True)
class WorkspaceSummary:
    """A Workspace as it is safe to show one of its own members."""

    workspace_id: UUID
    name: str
    slug: str
    kind: WorkspaceKind
    status: WorkspaceStatus
    publishing_role_policy: PublishingRolePolicy
    role: WorkspaceRole
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MemberSummary:
    """A fellow member as it is safe to show inside a Workspace."""

    user_id: UUID
    role: WorkspaceRole
    display_name: str
    email: str
    joined_at: datetime


class WorkspaceAuthorizer(Protocol):
    """The single question every Workspace-scoped entry point must ask."""

    def require(
        self, *, user_id: UUID, workspace_id: UUID, action: WorkspaceAction
    ) -> WorkspaceAccess:
        """Return the caller's access, or refuse the action."""
        ...
