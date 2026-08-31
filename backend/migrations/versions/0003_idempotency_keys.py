"""Add reusable Workspace-scoped idempotency response storage."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_TENANT_PREDICATE = """
    workspace_id = NULLIF(current_setting('clipah.workspace_id', true), '')::uuid
    AND NULLIF(current_setting('clipah.user_id', true), '')::uuid IS NOT NULL
"""


def upgrade() -> None:
    """Create durable replay records guarded by the same tenant policy as Projects."""
    op.create_table(
        "idempotency_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("route", sa.String(length=128), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.LargeBinary(), nullable=False),
        sa.Column("response_status", sa.Integer()),
        sa.Column("response_body", postgresql.JSONB()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "route",
            "key",
            name="uq_idempotency_keys_workspace_route_key",
        ),
    )
    op.execute("ALTER TABLE idempotency_keys ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE idempotency_keys FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY idempotency_keys_tenant_isolation ON idempotency_keys "
        f"USING ({_TENANT_PREDICATE}) WITH CHECK ({_TENANT_PREDICATE})"
    )
    op.execute("REVOKE ALL PRIVILEGES ON TABLE idempotency_keys FROM PUBLIC")
    op.execute("REVOKE ALL PRIVILEGES ON TABLE idempotency_keys FROM clipah_api, clipah_worker")
    op.execute("GRANT SELECT, INSERT ON TABLE idempotency_keys TO clipah_api")
    op.execute("GRANT UPDATE (response_body) ON TABLE idempotency_keys TO clipah_api")


def downgrade() -> None:
    """Remove the reusable idempotency storage introduced by this revision."""
    op.drop_table("idempotency_keys")
