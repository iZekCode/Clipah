"""Let a member upload a picture, such as a logo, to mark their clips with.

Revision ID: 0027
Revises: 0026
"""

from __future__ import annotations

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the picture Asset kind.

    The API already inserts and reads Assets, so no grant changes: an uploaded picture is
    one more Asset the member's own upload creates.
    """
    op.execute("ALTER TYPE asset_kind ADD VALUE IF NOT EXISTS 'picture'")


def downgrade() -> None:
    """Leave the value in place.

    PostgreSQL cannot remove a value from an enumeration. It is additive and unused once
    the code that writes it is gone.
    """
