"""Let each ranked moment carry a sharp poster: the Asset kind and the job that draws it.

Revision ID: 0025
Revises: 0024
"""

from __future__ import annotations

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the poster Asset kind and the clip-posters Job kind.

    The worker already inserts Assets and Jobs and the API already reads both, so no grant
    changes: a poster is one more derived Asset, and drawing posters is one more Job.
    """
    op.execute("ALTER TYPE asset_kind ADD VALUE IF NOT EXISTS 'poster'")
    op.execute("ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'clip_posters'")


def downgrade() -> None:
    """Leave both values in place.

    PostgreSQL cannot remove a value from an enumeration. Both are additive and unused once
    the code that writes them is gone.
    """
