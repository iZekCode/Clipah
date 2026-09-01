"""Record the transcript evidence each clip candidate was derived from.

Analysis keys every candidate to authoritative word IDs and explains both its hook and its
payoff, so the durable row must carry that evidence instead of only its timestamps.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the payoff, word-ID range, and context dependencies analysis produces."""
    op.add_column(
        "clip_candidates",
        sa.Column("payoff", sa.Text(), nullable=False, server_default=sa.text("''")),
    )
    op.add_column(
        "clip_candidates",
        sa.Column("start_word_id", sa.String(32), nullable=False, server_default=sa.text("''")),
    )
    op.add_column(
        "clip_candidates",
        sa.Column("end_word_id", sa.String(32), nullable=False, server_default=sa.text("''")),
    )
    op.add_column(
        "clip_candidates",
        sa.Column(
            "context_dependencies",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    """Remove only the candidate evidence columns introduced by this revision."""
    op.drop_column("clip_candidates", "context_dependencies")
    op.drop_column("clip_candidates", "end_word_id")
    op.drop_column("clip_candidates", "start_word_id")
    op.drop_column("clip_candidates", "payoff")
