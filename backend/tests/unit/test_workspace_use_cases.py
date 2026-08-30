"""Unit contracts for Workspace use cases that never need a database."""

from __future__ import annotations

from uuid import uuid4

import pytest

from clipah.models import PublishingRolePolicy, WorkspaceRole
from clipah.workspaces.models import WorkspaceAccess, WorkspaceNotFoundError
from clipah.workspaces.use_cases import describe_workspace, workspace_slug


class VanishedSession:
    """A transaction in which the authorized Workspace no longer exists."""

    def get(self, entity: object, identifier: object) -> None:
        """Report the row as gone the way a concurrent deletion would."""
        del entity, identifier
        return None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "stem"),
    [
        ("Studio Team", "studio-team"),
        ("  Ruang  Kerja  ", "ruang-kerja"),
        ("///", "workspace"),
        ("Nadia Putri's Workspace", "nadia-putri-s-workspace"),
    ],
)
def test_a_slug_is_url_safe_and_unique_per_workspace(name: str, stem: str) -> None:
    """Two Workspaces sharing a name must still receive distinct slugs."""
    slug = workspace_slug(name)

    assert slug.startswith(f"{stem}-")
    assert slug != workspace_slug(name)


@pytest.mark.unit
def test_a_workspace_that_disappeared_mid_transaction_is_reported_as_missing() -> None:
    """Authorization proves standing, not that the row survived until the read."""
    access = WorkspaceAccess(
        workspace_id=uuid4(),
        user_id=uuid4(),
        role=WorkspaceRole.OWNER,
        publishing_role_policy=PublishingRolePolicy.OWNER_ADMIN_EDITOR,
    )

    with pytest.raises(WorkspaceNotFoundError):
        describe_workspace(VanishedSession(), access=access)  # type: ignore[arg-type]
