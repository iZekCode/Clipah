"""Let the API withdraw a scheduled purge when a member recovers what they deleted.

Retention tombstones are written when a member deletes something and are the record
that the deletion happened. A recovery inside the window means the deletion did not
happen, so the API needs to remove that one pending row — and nothing more: a
discharged tombstone is still protected by the row policy and by the application,
which withdraws only tombstones that have not yet been acted on.

Revision ID: 0022
Revises: 0021
"""

from __future__ import annotations

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Grant the API the one privilege Project and Workspace recovery needs."""
    op.execute("GRANT DELETE ON TABLE retention_tombstones TO clipah_api")


def downgrade() -> None:
    """Withdraw the recovery privilege again."""
    op.execute("REVOKE DELETE ON TABLE retention_tombstones FROM clipah_api")
