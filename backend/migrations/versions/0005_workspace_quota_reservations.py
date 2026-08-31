"""Add Workspace-scoped monthly quota reservations for metered resources."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_TENANT_PREDICATE = """
    workspace_id = NULLIF(current_setting('clipah.workspace_id', true), '')::uuid
    AND NULLIF(current_setting('clipah.user_id', true), '')::uuid IS NOT NULL
"""
_RESOURCES = (
    "analyses",
    "stock_requests",
    "generated_images",
    "generated_videos",
    "generated_seconds",
    "social_publications",
)
_STATUSES = ("reserved", "settled", "released")


def upgrade() -> None:
    """Create the reservation ledger both runtime roles reconcile usage through."""
    bind = op.get_bind()
    postgresql.ENUM(*_RESOURCES, name="quota_resource").create(bind, checkfirst=False)
    postgresql.ENUM(*_STATUSES, name="quota_reservation_status").create(bind, checkfirst=False)

    op.create_table(
        "workspace_quota_reservations",
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
            "resource",
            postgresql.ENUM(*_RESOURCES, name="quota_resource", create_type=False),
            nullable=False,
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(*_STATUSES, name="quota_reservation_status", create_type=False),
            nullable=False,
            server_default=sa.text("'reserved'"),
        ),
        sa.Column("estimated_units", sa.Numeric(14, 4), nullable=False),
        sa.Column("actual_units", sa.Numeric(14, 4)),
        sa.Column("reference_kind", sa.String(length=32), nullable=False),
        sa.Column("reference_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_workspace_quota_reservations_workspace_id_id"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "resource",
            "reference_kind",
            "reference_id",
            name="uq_workspace_quota_reservations_workspace_resource_reference",
        ),
        sa.CheckConstraint(
            "estimated_units >= 0",
            name=op.f("ck_workspace_quota_reservations_nonnegative_estimated_units"),
        ),
        sa.CheckConstraint(
            "actual_units IS NULL OR actual_units >= 0",
            name=op.f("ck_workspace_quota_reservations_nonnegative_actual_units"),
        ),
    )
    op.create_index(
        "ix_workspace_quota_reservations_workspace_resource_period",
        "workspace_quota_reservations",
        ["workspace_id", "resource", "period_start"],
    )

    op.execute("ALTER TABLE workspace_quota_reservations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspace_quota_reservations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY workspace_quota_reservations_tenant_isolation "
        "ON workspace_quota_reservations "
        f"USING ({_TENANT_PREDICATE}) WITH CHECK ({_TENANT_PREDICATE})"
    )
    op.execute("REVOKE ALL PRIVILEGES ON TABLE workspace_quota_reservations FROM PUBLIC")
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE workspace_quota_reservations FROM clipah_api, clipah_worker"
    )
    # The API admits work by reserving an estimate; the worker reconciles the real cost.
    op.execute(
        "GRANT SELECT, INSERT ON TABLE workspace_quota_reservations TO clipah_api, clipah_worker"
    )
    op.execute(
        "GRANT UPDATE (status, actual_units, settled_at) ON TABLE workspace_quota_reservations "
        "TO clipah_api, clipah_worker"
    )


def downgrade() -> None:
    """Remove the reservation ledger and the enums introduced with it."""
    op.drop_index(
        "ix_workspace_quota_reservations_workspace_resource_period",
        table_name="workspace_quota_reservations",
    )
    op.drop_table("workspace_quota_reservations")
    bind = op.get_bind()
    postgresql.ENUM(name="quota_reservation_status").drop(bind, checkfirst=False)
    postgresql.ENUM(name="quota_resource").drop(bind, checkfirst=False)
