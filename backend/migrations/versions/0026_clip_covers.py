"""Let each clip carry a designed cover picture: the Asset kind and the job that draws it.

Revision ID: 0026
Revises: 0025
"""

from __future__ import annotations

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the cover Asset kind and the clip-cover Job kind.

    The worker already inserts Assets and Jobs and the API already reads both, so no grant
    changes: a cover is one more derived Asset, and drawing covers is one more Job.
    """
    op.execute("ALTER TYPE asset_kind ADD VALUE IF NOT EXISTS 'cover'")
    op.execute("ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'clip_cover'")


def downgrade() -> None:
    """Leave both values in place.

    PostgreSQL cannot remove a value from an enumeration. Both are additive and unused once
    the code that writes them is gone.
    """
