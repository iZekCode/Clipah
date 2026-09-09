"""Add immutable provider renditions and Publication linkage.

Revision ID: 0021
Revises: 0020
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

_TENANT_PREDICATE = """
    workspace_id = NULLIF(current_setting('clipah.workspace_id', true), '')::uuid
    AND NULLIF(current_setting('clipah.user_id', true), '')::uuid IS NOT NULL
"""


def upgrade() -> None:
    """Persist immutable provider-ready bytes and bind Publications to exact results."""
    op.add_column("render_artifacts", sa.Column("watermark_text", sa.Text()))
    provider = postgresql.ENUM(
        "youtube", "instagram", "tiktok", name="social_provider", create_type=False
    )
    op.create_table(
        "social_renditions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("render_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("provider", provider, nullable=False),
        sa.Column("profile_version", sa.String(length=128), nullable=False),
        sa.Column("output_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(), nullable=False),
        sa.Column("validation_report", postgresql.JSONB(), nullable=False),
        sa.Column("reused_master", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_social_renditions_workspace_id_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "source_sha256",
            "provider",
            "profile_version",
            name="uq_social_renditions_workspace_source_provider_profile",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "render_artifact_id"],
            ["render_artifacts.workspace_id", "render_artifacts.id"],
            name="fk_social_renditions_workspace_render_artifact",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("octet_length(source_sha256) = 32", name="source_sha256"),
        sa.CheckConstraint("octet_length(output_sha256) = 32", name="output_sha256"),
        sa.CheckConstraint("size_bytes >= 0", name="nonnegative_size"),
        sa.CheckConstraint("duration_ms > 0", name="positive_duration"),
    )
    op.add_column("publications", sa.Column("social_rendition_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_publications_workspace_social_rendition",
        "publications",
        "social_renditions",
        ["workspace_id", "social_rendition_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )
    op.execute(
        """
        CREATE TRIGGER trg_social_renditions_append_only
        BEFORE UPDATE OR DELETE ON social_renditions
        FOR EACH ROW EXECUTE FUNCTION clipah_prevent_history_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION clipah_protect_publication_rendition()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.social_rendition_id IS NOT NULL AND
               NEW.social_rendition_id IS DISTINCT FROM OLD.social_rendition_id THEN
                RAISE EXCEPTION 'Publication rendition is immutable'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_publications_rendition_immutable
        BEFORE UPDATE ON publications
        FOR EACH ROW EXECUTE FUNCTION clipah_protect_publication_rendition()
        """
    )
    op.execute("ALTER TABLE social_renditions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE social_renditions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY social_renditions_tenant_isolation ON social_renditions "
        f"USING ({_TENANT_PREDICATE}) WITH CHECK ({_TENANT_PREDICATE})"
    )
    op.execute("REVOKE ALL PRIVILEGES ON TABLE social_renditions FROM PUBLIC")
    op.execute("REVOKE ALL PRIVILEGES ON TABLE social_renditions FROM clipah_api, clipah_worker")
    op.execute("GRANT SELECT, INSERT ON TABLE social_renditions TO clipah_api, clipah_worker")


def downgrade() -> None:
    """Remove only Task 38 rendition persistence."""
    op.execute("DROP FUNCTION clipah_protect_publication_rendition() CASCADE")
    op.drop_constraint(
        "fk_publications_workspace_social_rendition", "publications", type_="foreignkey"
    )
    op.drop_column("publications", "social_rendition_id")
    op.drop_table("social_renditions")
    op.execute("ALTER TABLE render_artifacts DROP COLUMN IF EXISTS watermark_text")
