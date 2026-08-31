"""Let the API append job events without ever being able to rewrite one.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Grant append-only job event writes to the API role.

    The API now owns two transitions of its own: it creates jobs and it cancels work
    nobody has started. Both must announce themselves in the same append-only history
    the worker writes, and INSERT alone cannot alter a fact already recorded.
    """
    op.execute("GRANT INSERT ON TABLE job_events TO clipah_api")


def downgrade() -> None:
    """Return the API role to reading job history only."""
    op.execute("REVOKE INSERT ON TABLE job_events FROM clipah_api")
