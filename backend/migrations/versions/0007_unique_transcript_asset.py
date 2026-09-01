"""Enforce one canonical Transcript for each source Asset.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Make concurrent transcription deliveries converge at the durable boundary."""
    op.create_unique_constraint(
        "uq_transcripts_workspace_asset",
        "transcripts",
        ["workspace_id", "asset_id"],
    )


def downgrade() -> None:
    """Remove only the source-Asset uniqueness introduced by this revision."""
    op.drop_constraint(
        "uq_transcripts_workspace_asset",
        "transcripts",
        type_="unique",
    )
