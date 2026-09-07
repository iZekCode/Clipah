"""Complete literal Workspace role matrix across every current domain action family."""

from __future__ import annotations

from uuid import uuid4

import pytest

from clipah.models import PublishingRolePolicy, WorkspaceRole
from clipah.workspaces.authorization import permits
from clipah.workspaces.models import WorkspaceAccess, WorkspaceAction

DOMAIN_ACTIONS = {
    "workspace.read": WorkspaceAction.WORKSPACE_READ,
    "workspace.update": WorkspaceAction.WORKSPACE_UPDATE,
    "workspace.delete": WorkspaceAction.WORKSPACE_DELETE,
    "membership.read": WorkspaceAction.MEMBER_READ,
    "membership.manage": WorkspaceAction.MEMBER_MANAGE,
    "membership.transfer": WorkspaceAction.OWNERSHIP_TRANSFER,
    "project.read": WorkspaceAction.PROJECT_READ,
    "project.write": WorkspaceAction.PROJECT_WRITE,
    "asset.read": WorkspaceAction.PROJECT_READ,
    "asset.write": WorkspaceAction.PROJECT_WRITE,
    "job.read": WorkspaceAction.PROJECT_READ,
    "job.cancel": WorkspaceAction.PROJECT_WRITE,
    "candidate.read": WorkspaceAction.PROJECT_READ,
    "edit.read": WorkspaceAction.PROJECT_READ,
    "edit.write": WorkspaceAction.EDIT_WRITE,
    "broll.read": WorkspaceAction.PROJECT_READ,
    "broll.write": WorkspaceAction.EDIT_WRITE,
    "render.read": WorkspaceAction.PROJECT_READ,
    "render.write": WorkspaceAction.EDIT_WRITE,
    "brand.read": WorkspaceAction.PROJECT_READ,
    "brand.write": WorkspaceAction.EDIT_WRITE,
    "review.read": WorkspaceAction.PROJECT_READ,
    "review.decide": WorkspaceAction.REVIEW_DECIDE,
    "source_account.read": WorkspaceAction.SOURCE_CONNECTION_READ,
    "source_account.manage": WorkspaceAction.SOURCE_CONNECTION_MANAGE,
    "social_account.manage": WorkspaceAction.SOCIAL_CONNECTION_MANAGE,
    "publication.prepare": WorkspaceAction.EDIT_WRITE,
    "publication.publish": WorkspaceAction.PUBLISH,
}

EXPECTED_ALLOWED = {
    WorkspaceRole.VIEWER: {
        "workspace.read",
        "membership.read",
        "project.read",
        "asset.read",
        "job.read",
        "candidate.read",
        "edit.read",
        "broll.read",
        "render.read",
        "brand.read",
        "review.read",
    },
    WorkspaceRole.REVIEWER: {
        "workspace.read",
        "membership.read",
        "project.read",
        "asset.read",
        "job.read",
        "candidate.read",
        "edit.read",
        "broll.read",
        "render.read",
        "brand.read",
        "review.read",
        "review.decide",
    },
    WorkspaceRole.EDITOR: {
        "workspace.read",
        "membership.read",
        "project.read",
        "project.write",
        "asset.read",
        "asset.write",
        "job.read",
        "job.cancel",
        "candidate.read",
        "edit.read",
        "edit.write",
        "broll.read",
        "broll.write",
        "render.read",
        "render.write",
        "brand.read",
        "brand.write",
        "review.read",
        "review.decide",
        "publication.prepare",
        "publication.publish",
    },
    WorkspaceRole.ADMIN: set(DOMAIN_ACTIONS) - {"workspace.delete", "membership.transfer"},
    WorkspaceRole.OWNER: set(DOMAIN_ACTIONS),
}


def _access(role: WorkspaceRole) -> WorkspaceAccess:
    """Build one member standing under the default publishing policy."""
    return WorkspaceAccess(
        workspace_id=uuid4(),
        user_id=uuid4(),
        role=role,
        publishing_role_policy=PublishingRolePolicy.OWNER_ADMIN_EDITOR,
    )


@pytest.mark.unit
@pytest.mark.parametrize("role", list(WorkspaceRole))
def test_every_domain_action_follows_the_literal_workspace_role_matrix(
    role: WorkspaceRole,
) -> None:
    """A route added to any domain must inherit the documented role boundary."""
    allowed = {name for name, action in DOMAIN_ACTIONS.items() if permits(_access(role), action)}
    assert allowed == EXPECTED_ALLOWED[role]


@pytest.mark.unit
def test_narrow_publishing_policy_removes_only_editor_publication_delivery() -> None:
    """A Workspace may narrow publishing without changing an editor's preparation rights."""
    access = WorkspaceAccess(
        workspace_id=uuid4(),
        user_id=uuid4(),
        role=WorkspaceRole.EDITOR,
        publishing_role_policy=PublishingRolePolicy.OWNER_ADMIN,
    )

    assert permits(access, DOMAIN_ACTIONS["publication.prepare"]) is True
    assert permits(access, DOMAIN_ACTIONS["publication.publish"]) is False
