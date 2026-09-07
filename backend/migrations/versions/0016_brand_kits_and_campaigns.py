"""Record what a Workspace's brand promises, the looks it reuses, and the copy it derives.

Revision ID: 0016
Revises: 0015

`plan.md` names this migration `0006`. That numbering predates fifteen migrations that
have since landed; the file is numbered for the schema it actually follows.

Two shapes here deviate from the column lists in Section 6 of `plan.md`, and both
deviations exist because the task's own acceptance criteria require them.

* A Brand Kit and a template are each split into an identity row and an append-only
  version row, exactly as `clip_edits` and `clip_edit_revisions` already are. `plan.md`
  gives `templates` a single `version` column, but the task requires that a saved
  composition keep resolving the exact version it was built against after the template
  has been edited — which one mutable row cannot provide.
* `campaign_outputs` carries `language`, which Section 6 omits and the task's fourth
  checkbox requires. It also carries `workspace_id` and `project_id`, because every
  tenant-scoped table in this schema does.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

_TENANT_PREDICATE = """
    workspace_id = NULLIF(current_setting('clipah.workspace_id', true), '')::uuid
    AND NULLIF(current_setting('clipah.user_id', true), '')::uuid IS NOT NULL
"""

_TEMPLATE_KINDS = ("clip_look",)
_CAMPAIGN_LANGUAGES = ("id", "en")
_TENANT_TABLES = (
    "brand_kits",
    "brand_kit_versions",
    "brand_templates",
    "brand_template_versions",
    "campaign_outputs",
)


def upgrade() -> None:
    """Create the five tables Task 33 introduces, all tenant-scoped and API-owned.

    None of this is worker work. A Brand Kit is written by a person, a template version is
    published by a person, and campaign copy is derived inside the request that asks for
    it — so the worker receives no privilege on any of them.
    """
    for name, values in (
        ("brand_template_kind", _TEMPLATE_KINDS),
        ("campaign_language", _CAMPAIGN_LANGUAGES),
    ):
        postgresql.ENUM(*values, name=name).create(op.get_bind(), checkfirst=True)
    template_kind = postgresql.ENUM(*_TEMPLATE_KINDS, name="brand_template_kind", create_type=False)
    language = postgresql.ENUM(*_CAMPAIGN_LANGUAGES, name="campaign_language", create_type=False)
    platform = postgresql.ENUM(
        "tiktok",
        "instagram_reels",
        "youtube_shorts",
        name="clip_variant_platform",
        create_type=False,
    )

    op.create_table(
        "brand_kits",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # A kit is archived rather than deleted, so a Revision that names one of its
        # versions keeps resolving to the rules it was actually judged against.
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("workspace_id", "id", name="uq_brand_kits_workspace_id_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_brand_kits_workspace_id_workspaces",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_brand_kits_created_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("current_version > 0", name="positive_brand_kit_version"),
    )
    op.create_index("ix_brand_kits_workspace", "brand_kits", ["workspace_id"])

    op.create_table(
        "brand_kit_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_kit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_brand_kit_versions_workspace_id_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "brand_kit_id",
            "version",
            name="uq_brand_kit_versions_workspace_kit_version",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "brand_kit_id"],
            ["brand_kits.workspace_id", "brand_kits.id"],
            name="fk_brand_kit_versions_workspace_id_brand_kit_id_brand_kits",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_brand_kit_versions_created_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("version > 0", name="positive_brand_kit_version_number"),
    )

    op.create_table(
        "brand_templates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_kit_id", postgresql.UUID(as_uuid=True)),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", template_kind, nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("workspace_id", "id", name="uq_brand_templates_workspace_id_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_brand_templates_workspace_id_workspaces",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "brand_kit_id"],
            ["brand_kits.workspace_id", "brand_kits.id"],
            name="fk_brand_templates_workspace_id_brand_kit_id_brand_kits",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_brand_templates_created_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("current_version > 0", name="positive_template_version"),
    )
    op.create_index("ix_brand_templates_workspace", "brand_templates", ["workspace_id"])

    op.create_table(
        "brand_template_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_brand_template_versions_workspace_id_id"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "template_id",
            "version",
            name="uq_brand_template_versions_workspace_template_version",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "template_id"],
            ["brand_templates.workspace_id", "brand_templates.id"],
            name="fk_brand_template_versions_workspace_id_template_id_templates",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_brand_template_versions_created_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("version > 0", name="positive_template_version_number"),
    )

    op.create_table(
        "campaign_outputs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("clip_edit_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", platform, nullable=False),
        sa.Column("language", language, nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("post_copy", sa.Text(), nullable=False),
        sa.Column("cta", sa.Text(), nullable=False),
        sa.Column("hashtags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("thumbnail_brief", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        # The warnings the clip carried when this copy was derived. A member reads the
        # copy and the caveat together or the caveat may as well not exist.
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_campaign_outputs_workspace_id_id"),
        # One immutable Revision yields one piece of copy per destination and language, so
        # a repeated request converges on the copy a member has already read.
        sa.UniqueConstraint(
            "workspace_id",
            "clip_edit_revision_id",
            "platform",
            "language",
            name="uq_campaign_outputs_revision_platform_language",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_campaign_outputs_workspace_id_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "clip_edit_revision_id"],
            ["clip_edit_revisions.workspace_id", "clip_edit_revisions.id"],
            name="fk_campaign_outputs_workspace_id_revision_id_revisions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_campaign_outputs_created_by_user_id_users",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_campaign_outputs_workspace_revision",
        "campaign_outputs",
        ["workspace_id", "clip_edit_revision_id"],
    )

    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING ({_TENANT_PREDICATE}) WITH CHECK ({_TENANT_PREDICATE})"
        )
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM PUBLIC")
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM clipah_api, clipah_worker")

    # An identity row is renamed, re-pointed at the current version, and archived, so the
    # API may update it. A published version never changes, and neither does derived copy:
    # both are append-only by grant rather than by convention.
    op.execute("GRANT SELECT, INSERT, UPDATE ON TABLE brand_kits TO clipah_api")
    op.execute("GRANT SELECT, INSERT, UPDATE ON TABLE brand_templates TO clipah_api")
    op.execute("GRANT SELECT, INSERT ON TABLE brand_kit_versions TO clipah_api")
    op.execute("GRANT SELECT, INSERT ON TABLE brand_template_versions TO clipah_api")
    op.execute("GRANT SELECT, INSERT ON TABLE campaign_outputs TO clipah_api")
    # The render worker reads the Brand Kit version a composition was built against so an
    # export is judged by the same rules the editor showed. It reads only.
    op.execute("GRANT SELECT ON TABLE brand_kits TO clipah_worker")
    op.execute("GRANT SELECT ON TABLE brand_kit_versions TO clipah_worker")


def downgrade() -> None:
    """Remove the five tables and the vocabulary they introduced."""
    op.drop_index("ix_campaign_outputs_workspace_revision", table_name="campaign_outputs")
    op.drop_table("campaign_outputs")
    op.drop_table("brand_template_versions")
    op.drop_index("ix_brand_templates_workspace", table_name="brand_templates")
    op.drop_table("brand_templates")
    op.drop_table("brand_kit_versions")
    op.drop_index("ix_brand_kits_workspace", table_name="brand_kits")
    op.drop_table("brand_kits")
    for name in ("campaign_language", "brand_template_kind"):
        op.execute(f"DROP TYPE IF EXISTS {name}")
