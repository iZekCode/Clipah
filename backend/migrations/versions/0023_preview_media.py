"""Let ingest's previews be recorded: storyboard sheets and the job that draws them.

Revision ID: 0023
Revises: 0022
"""

from __future__ import annotations

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the storyboard Asset kind and the preview-media Job kind.

    The worker already inserts Assets and Jobs and the API already reads both, so no grant
    changes: a storyboard sheet is one more derived Asset, and preview work is one more Job.
    """
    op.execute("ALTER TYPE asset_kind ADD VALUE IF NOT EXISTS 'storyboard'")
    op.execute("ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'preview_media'")


def downgrade() -> None:
    """Leave both values in place.

    PostgreSQL cannot remove a value from an enumeration. Both are additive and unused once
    the code that writes them is gone.
    """
