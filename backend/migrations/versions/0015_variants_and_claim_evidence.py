"""Record honest alternative cuts, and what a User said about the sources behind claims.

Revision ID: 0015
Revises: 0014

`plan.md` names this migration `0005`. That numbering predates ten migrations that have
since landed; the file is numbered for the schema it actually follows.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

_TENANT_PREDICATE = """
    workspace_id = NULLIF(current_setting('clipah.workspace_id', true), '')::uuid
    AND NULLIF(current_setting('clipah.user_id', true), '')::uuid IS NOT NULL
"""

_HOOK_STRATEGIES = ("cold_open", "question_first", "statement_first")
_PLATFORMS = ("tiktok", "instagram_reels", "youtube_shorts")
_VERIFICATION_STATUSES = ("unverified", "supported", "disputed", "retracted")


def upgrade() -> None:
    """Create the two tables Task 32 introduces, both tenant-scoped and API-owned.

    Neither table is worker work. Variants are generated inside the request that asks for
    them, and evidence is something a person writes, so the worker receives no privilege
    on either — a pipeline stage that could rewrite a member's citation would be a way to
    launder an assertion nobody made.
    """
    # The types are created once, explicitly, and the columns below reference them with
    # `create_type=False`; letting `create_table` create them again collides inside the
    # same transaction.
    for name, values in (
        ("clip_variant_hook_strategy", _HOOK_STRATEGIES),
        ("clip_variant_platform", _PLATFORMS),
        ("claim_verification_status", _VERIFICATION_STATUSES),
    ):
        postgresql.ENUM(*values, name=name).create(op.get_bind(), checkfirst=True)
    hook_strategy = postgresql.ENUM(
        *_HOOK_STRATEGIES, name="clip_variant_hook_strategy", create_type=False
    )
    platform = postgresql.ENUM(*_PLATFORMS, name="clip_variant_platform", create_type=False)
    verification = postgresql.ENUM(
        *_VERIFICATION_STATUSES, name="claim_verification_status", create_type=False
    )

    op.create_table(
        "clip_variants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hook_strategy", hook_strategy, nullable=False),
        sa.Column("platform", platform, nullable=False),
        sa.Column("target_duration_ms", sa.Integer(), nullable=False),
        sa.Column("start_word_id", sa.String(length=32), nullable=False),
        sa.Column("end_word_id", sa.String(length=32), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("packaging", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_clip_variants_workspace_id_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "candidate_id",
            "hook_strategy",
            "target_duration_ms",
            "platform",
            name="uq_clip_variants_candidate_strategy_target_platform",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_clip_variants_workspace_id_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "candidate_id"],
            ["clip_candidates.workspace_id", "clip_candidates.id"],
            name="fk_clip_variants_workspace_id_candidate_id_clip_candidates",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("start_ms >= 0 AND end_ms > start_ms", name="valid_variant_range"),
        sa.CheckConstraint("target_duration_ms > 0", name="positive_variant_target"),
    )
    op.create_index(
        "ix_clip_variants_workspace_candidate", "clip_variants", ["workspace_id", "candidate_id"]
    )

    op.create_table(
        "claim_evidence",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("start_word_id", sa.String(length=32), nullable=False),
        sa.Column("end_word_id", sa.String(length=32), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_title", sa.Text(), nullable=False),
        sa.Column("publisher", sa.Text(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "verification_status",
            verification,
            nullable=False,
            server_default=sa.text("'unverified'"),
        ),
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
        sa.UniqueConstraint("workspace_id", "id", name="uq_claim_evidence_workspace_id_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_claim_evidence_workspace_id_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "candidate_id"],
            ["clip_candidates.workspace_id", "clip_candidates.id"],
            name="fk_claim_evidence_workspace_id_candidate_id_clip_candidates",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_claim_evidence_created_by_user_id_users",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_claim_evidence_workspace_candidate", "claim_evidence", ["workspace_id", "candidate_id"]
    )

    for table in ("clip_variants", "claim_evidence"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING ({_TENANT_PREDICATE}) WITH CHECK ({_TENANT_PREDICATE})"
        )
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM PUBLIC")
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM clipah_api, clipah_worker")

    # Variants are written once by the request that generated them and never rewritten:
    # a stored Variant is the boundary a member was actually shown.
    op.execute("GRANT SELECT, INSERT ON TABLE clip_variants TO clipah_api")
    # Evidence is a person's record, so the API may correct one — including the only path
    # by which a verification status ever changes.
    op.execute("GRANT SELECT, INSERT, UPDATE ON TABLE claim_evidence TO clipah_api")


def downgrade() -> None:
    """Remove both tables and the vocabulary they introduced."""
    op.drop_index("ix_claim_evidence_workspace_candidate", table_name="claim_evidence")
    op.drop_table("claim_evidence")
    op.drop_index("ix_clip_variants_workspace_candidate", table_name="clip_variants")
    op.drop_table("clip_variants")
    retired = (
        "claim_verification_status",
        "clip_variant_platform",
        "clip_variant_hook_strategy",
    )
    for name in retired:
        op.execute(f"DROP TYPE IF EXISTS {name}")
