"""Unit contracts for the Workspace role matrix and its publishing policy."""

from __future__ import annotations

from itertools import pairwise
from uuid import uuid4

import pytest

from clipah.models import PublishingRolePolicy, WorkspaceRole
from clipah.workspaces.authorization import (
    ROLE_ACTIONS,
    permits,
    requires_recent_authentication,
)
from clipah.workspaces.models import WorkspaceAccess, WorkspaceAction

Action = WorkspaceAction
Role = WorkspaceRole
WIDENING_ROLES = [Role.VIEWER, Role.REVIEWER, Role.EDITOR, Role.ADMIN, Role.OWNER]


def access(
    role: WorkspaceRole,
    policy: PublishingRolePolicy = PublishingRolePolicy.OWNER_ADMIN_EDITOR,
) -> WorkspaceAccess:
    """Build one member's standing inside a Workspace."""
    return WorkspaceAccess(
        workspace_id=uuid4(),
        user_id=uuid4(),
        role=role,
        publishing_role_policy=policy,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("lower", "higher"),
    list(pairwise(WIDENING_ROLES)),
)
def test_each_role_carries_every_authority_of_the_role_below_it(
    lower: WorkspaceRole, higher: WorkspaceRole
) -> None:
    """A promotion must never silently take an authority away."""
    assert ROLE_ACTIONS[lower] < ROLE_ACTIONS[higher]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("role", "action", "allowed"),
    [
        (Role.VIEWER, Action.WORKSPACE_READ, True),
        (Role.VIEWER, Action.PROJECT_WRITE, False),
        (Role.REVIEWER, Action.REVIEW_DECIDE, True),
        (Role.REVIEWER, Action.EDIT_WRITE, False),
        (Role.EDITOR, Action.EDIT_WRITE, True),
        (Role.EDITOR, Action.WORKSPACE_UPDATE, False),
        (Role.ADMIN, Action.MEMBER_MANAGE, True),
        (Role.ADMIN, Action.WORKSPACE_DELETE, False),
        (Role.ADMIN, Action.OWNERSHIP_TRANSFER, False),
        (Role.OWNER, Action.WORKSPACE_DELETE, True),
        (Role.OWNER, Action.OWNERSHIP_TRANSFER, True),
    ],
)
def test_the_role_matrix_grants_exactly_the_documented_authorities(
    role: WorkspaceRole, action: WorkspaceAction, allowed: bool
) -> None:
    """Each role's boundary is a contract other tasks build permission checks on."""
    assert permits(access(role), action) is allowed


@pytest.mark.unit
@pytest.mark.parametrize(
    ("role", "policy", "allowed"),
    [
        (Role.EDITOR, PublishingRolePolicy.OWNER_ADMIN_EDITOR, True),
        (Role.EDITOR, PublishingRolePolicy.OWNER_ADMIN, False),
        (Role.ADMIN, PublishingRolePolicy.OWNER_ADMIN, True),
        (Role.OWNER, PublishingRolePolicy.OWNER_ADMIN, True),
        (Role.REVIEWER, PublishingRolePolicy.OWNER_ADMIN_EDITOR, False),
        (Role.VIEWER, PublishingRolePolicy.OWNER_ADMIN_EDITOR, False),
    ],
)
def test_publishing_authority_follows_the_workspace_policy(
    role: WorkspaceRole, policy: PublishingRolePolicy, allowed: bool
) -> None:
    """Publishing is the one authority a Workspace can narrow for itself."""
    assert permits(access(role, policy), Action.PUBLISH) is allowed


@pytest.mark.unit
@pytest.mark.parametrize(
    ("action", "required"),
    [
        (Action.WORKSPACE_DELETE, True),
        (Action.OWNERSHIP_TRANSFER, True),
        (Action.MEMBER_MANAGE, True),
        (Action.SOCIAL_CONNECTION_MANAGE, True),
        (Action.WORKSPACE_UPDATE, False),
        (Action.WORKSPACE_READ, False),
    ],
)
def test_only_irreversible_actions_demand_a_freshly_authenticated_session(
    action: WorkspaceAction, required: bool
) -> None:
    """Re-authentication protects the actions a stolen Session would be worst for."""
    assert requires_recent_authentication(action) is required
