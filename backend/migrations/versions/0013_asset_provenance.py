"""Record where every retrieved asset came from before it can be selected.

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

_TENANT_PREDICATE = """
    workspace_id = NULLIF(current_setting('clipah.workspace_id', true), '')::uuid
    AND NULLIF(current_setting('clipah.user_id', true), '')::uuid IS NOT NULL
"""

_NEW_ASSET_KINDS = ("broll", "broll_proxy")
_NEW_ASSET_SOURCE_TYPES = ("stock",)


def upgrade() -> None:
    """Create the provenance record, and the asset vocabulary retrieved media needs.

    `asset_provenance` is what makes an external picture usable at all: without a complete
    row here, nobody could later answer whether the footage in a published clip was ever
    licensed for that use. The plan forbids secrets in this table, and nothing written to
    it comes from anywhere but a normalized provider record.

    The worker gains a column-level UPDATE on `broll_suggestions` covering only the three
    fields retrieval decides. Status and `decided_at` stay API-only, so a retrieval Job can
    attach a picture to a suggestion and still cannot overwrite a member's decision about
    it.
    """
    for value in _NEW_ASSET_KINDS:
        op.execute(f"ALTER TYPE asset_kind ADD VALUE IF NOT EXISTS '{value}'")
    for value in _NEW_ASSET_SOURCE_TYPES:
        op.execute(f"ALTER TYPE asset_source_type ADD VALUE IF NOT EXISTS '{value}'")

    op.create_table(
        "asset_provenance",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_asset_id", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=False),
        sa.Column("author_url", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("license_name", sa.Text(), nullable=False),
        sa.Column("license_url", sa.Text(), nullable=False),
        sa.Column("terms_snapshot", sa.Text(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("query", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("prompt", sa.Text()),
        sa.Column("model", sa.Text()),
        sa.Column("model_version", sa.Text()),
        sa.Column("seed", sa.Text()),
        sa.Column("moderation_result", sa.String(32), nullable=False),
        sa.Column("attribution_text", sa.Text(), nullable=False),
        sa.Column("checksum", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_asset_provenance_workspace_id_id"),
        sa.UniqueConstraint("workspace_id", "asset_id", name="uq_asset_provenance_workspace_asset"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "asset_id"],
            ["assets.workspace_id", "assets.id"],
            name="fk_asset_provenance_workspace_id_asset_id_assets",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_asset_provenance_workspace_provider_asset",
        "asset_provenance",
        ["workspace_id", "provider", "provider_asset_id"],
    )

    op.execute("ALTER TABLE asset_provenance ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE asset_provenance FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY asset_provenance_tenant_isolation ON asset_provenance "
        f"USING ({_TENANT_PREDICATE}) WITH CHECK ({_TENANT_PREDICATE})"
    )
    op.execute("REVOKE ALL PRIVILEGES ON TABLE asset_provenance FROM PUBLIC")
    op.execute("REVOKE ALL PRIVILEGES ON TABLE asset_provenance FROM clipah_api, clipah_worker")
    # Retrieval writes provenance; a member only ever reads it. Nobody may rewrite it,
    # because a licence snapshot that can be edited is not evidence of anything.
    op.execute("GRANT SELECT ON TABLE asset_provenance TO clipah_api")
    op.execute("GRANT SELECT, INSERT ON TABLE asset_provenance TO clipah_worker")

    # Retrieval attaches a picture to a suggestion. It may say which asset was chosen and
    # how relevant it was, and it may never touch the status or the decision timestamp
    # that belong to the member.
    op.execute(
        "GRANT UPDATE (source_type, asset_id, relevance_score, provider_metadata) "
        "ON TABLE broll_suggestions TO clipah_worker"
    )


def downgrade() -> None:
    """Remove the provenance table and the grant retrieval added.

    Postgres cannot remove a value from an enumeration, so the two asset kinds and the
    stock source type stay. They are additive and unused once this table is gone.
    """
    op.execute("REVOKE UPDATE ON TABLE broll_suggestions FROM clipah_worker")
    op.execute("GRANT SELECT, INSERT ON TABLE broll_suggestions TO clipah_worker")
    op.drop_index("ix_asset_provenance_workspace_provider_asset", table_name="asset_provenance")
    op.drop_table("asset_provenance")
