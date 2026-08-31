"""Persist multipart display and content metadata required for durable validation."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024


def upgrade() -> None:
    """Add non-lossy multipart metadata and compatibility-safe size constraints."""
    op.add_column(
        "multipart_uploads",
        sa.Column("client_filename", sa.Text(), nullable=False, server_default=sa.text("''")),
    )
    op.add_column(
        "multipart_uploads",
        sa.Column(
            "content_type",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'application/octet-stream'"),
        ),
    )
    op.add_column(
        "multipart_uploads",
        sa.Column(
            "declared_size_bytes", sa.BigInteger(), nullable=False, server_default=sa.text("1")
        ),
    )
    op.add_column(
        "multipart_uploads", sa.Column("completed_size_bytes", sa.BigInteger(), nullable=True)
    )
    op.create_check_constraint(
        "declared_size_within_initial_media_limit",
        "multipart_uploads",
        f"declared_size_bytes > 0 AND declared_size_bytes <= {_MAX_UPLOAD_BYTES}",
    )
    op.create_check_constraint(
        "completed_size_within_initial_media_limit",
        "multipart_uploads",
        f"completed_size_bytes IS NULL OR "
        f"(completed_size_bytes > 0 AND completed_size_bytes <= {_MAX_UPLOAD_BYTES})",
    )
    op.alter_column("multipart_uploads", "client_filename", server_default=None)
    op.alter_column("multipart_uploads", "content_type", server_default=None)
    op.alter_column("multipart_uploads", "declared_size_bytes", server_default=None)


def downgrade() -> None:
    """Remove only metadata introduced by this compatibility migration."""
    op.drop_constraint(
        "completed_size_within_initial_media_limit", "multipart_uploads", type_="check"
    )
    op.drop_constraint(
        "declared_size_within_initial_media_limit", "multipart_uploads", type_="check"
    )
    op.drop_column("multipart_uploads", "completed_size_bytes")
    op.drop_column("multipart_uploads", "declared_size_bytes")
    op.drop_column("multipart_uploads", "content_type")
    op.drop_column("multipart_uploads", "client_filename")
