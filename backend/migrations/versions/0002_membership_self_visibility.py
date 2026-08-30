"""Let a User read their own Workspace Memberships without a tenant context.

Workspace selection has to answer "which Workspaces do I belong to?" before any
single Workspace has been chosen, so the read side of membership isolation also
accepts rows owned by the authenticated actor. The write side is untouched: a
Membership can still only be created or changed inside the Workspace whose
identifier the transaction already carries, so a User cannot enroll themselves
into a Workspace they merely guessed.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-30

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTOR = "NULLIF(current_setting('clipah.user_id', true), '')::uuid"
TENANT_PREDICATE = f"""
    workspace_id = NULLIF(current_setting('clipah.workspace_id', true), '')::uuid
    AND {ACTOR} IS NOT NULL
"""
SELF_PREDICATE = f"user_id = {ACTOR}"


def _replace_membership_policy(*, using: str) -> None:
    op.execute("DROP POLICY tenant_isolation ON workspace_memberships")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON workspace_memberships
        USING ({using})
        WITH CHECK ({TENANT_PREDICATE})
        """
    )


def upgrade() -> None:
    """Widen only the read predicate to the actor's own Memberships."""
    _replace_membership_policy(using=f"({TENANT_PREDICATE}) OR ({SELF_PREDICATE})")


def downgrade() -> None:
    """Restore the strictly Workspace-scoped read predicate."""
    _replace_membership_policy(using=TENANT_PREDICATE)
