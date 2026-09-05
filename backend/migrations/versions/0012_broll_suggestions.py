"""Hold proposed B-roll placements that change no Edit until a member accepts one.

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

_TENANT_PREDICATE = """
    workspace_id = NULLIF(current_setting('clipah.workspace_id', true), '')::uuid
    AND NULLIF(current_setting('clipah.user_id', true), '')::uuid IS NOT NULL
"""


def upgrade() -> None:
    """Create the suggestion table, its plan identity, and its least-privilege grants.

    A suggestion is a proposal and nothing more, so the table carries no reference to a
    composition. The unique key is the plan — candidate, planner version, coverage — plus
    the beat inside it, which is what makes a redelivered planning Job converge on the rows
    it already wrote rather than proposing the same picture twice.

    The worker plans and therefore inserts; it is granted no UPDATE and no DELETE, so a
    replay cannot quietly rewrite a member's decision. The API reads suggestions and, from
    the task that owns accepting them, updates their status.
    """
    coverage = postgresql.ENUM(
        "minimal", "balanced", "dynamic", name="broll_coverage", create_type=False
    )
    status = postgresql.ENUM(
        "proposed",
        "accepted",
        "placed",
        "replaced",
        "removed",
        "rejected",
        "generation_requested",
        "generating",
        "failed",
        name="broll_suggestion_status",
        create_type=False,
    )
    source_type = postgresql.ENUM(
        "user_asset", "stock", "generated", name="broll_source_type", create_type=False
    )
    coverage.create(op.get_bind(), checkfirst=True)
    status.create(op.get_bind(), checkfirst=True)
    source_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "broll_suggestions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("planner_version", sa.String(64), nullable=False),
        sa.Column("coverage", coverage, nullable=False),
        sa.Column("beat_start_word_id", sa.String(32), nullable=False),
        sa.Column("beat_end_word_id", sa.String(32), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("visual_intent", postgresql.JSONB(), nullable=False),
        sa.Column("search_terms", postgresql.JSONB(), nullable=False),
        sa.Column(
            "exclusions",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("status", status, nullable=False, server_default=sa.text("'proposed'")),
        sa.Column("placement_reason", sa.Text(), nullable=False),
        sa.Column("source_type", source_type),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True)),
        sa.Column("edit_id", postgresql.UUID(as_uuid=True)),
        sa.Column("relevance_score", sa.Numeric(6, 5)),
        sa.Column("provider_metadata", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("workspace_id", "id", name="uq_broll_suggestions_workspace_id_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "candidate_id",
            "planner_version",
            "coverage",
            "beat_start_word_id",
            name="uq_broll_suggestions_plan_beat",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_broll_suggestions_workspace_id_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "candidate_id"],
            ["clip_candidates.workspace_id", "clip_candidates.id"],
            name="fk_broll_suggestions_workspace_id_candidate_id_clip_candidates",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("start_ms >= 0 AND end_ms > start_ms", name="valid_shot_range"),
    )
    op.create_index(
        "ix_broll_suggestions_workspace_candidate",
        "broll_suggestions",
        ["workspace_id", "candidate_id"],
    )

    # A Job carries only identifiers across the broker, so what the member asked for is
    # recorded here by the API and read back by the worker that performs the planning.
    op.create_table(
        "broll_plan_requests",
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
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("coverage", coverage, nullable=False),
        sa.Column(
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_broll_plan_requests_workspace_id_id"),
        sa.UniqueConstraint("workspace_id", "job_id", name="uq_broll_plan_requests_workspace_job"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "candidate_id"],
            ["clip_candidates.workspace_id", "clip_candidates.id"],
            name="fk_broll_plan_requests_workspace_candidate_clip_candidates",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["jobs.workspace_id", "jobs.id"],
            name="fk_broll_plan_requests_workspace_id_job_id_jobs",
            ondelete="RESTRICT",
        ),
    )

    for table in ("broll_plan_requests",):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING ({_TENANT_PREDICATE}) WITH CHECK ({_TENANT_PREDICATE})"
        )
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM PUBLIC")
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM clipah_api, clipah_worker")
    # The API records what a planning Job is for when it admits that Job, and never
    # rewrites it; the worker only reads the target it was handed.
    op.execute("GRANT SELECT, INSERT ON TABLE broll_plan_requests TO clipah_api")
    op.execute("GRANT SELECT ON TABLE broll_plan_requests TO clipah_worker")

    op.execute("ALTER TABLE broll_suggestions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE broll_suggestions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY broll_suggestions_tenant_isolation ON broll_suggestions "
        f"USING ({_TENANT_PREDICATE}) WITH CHECK ({_TENANT_PREDICATE})"
    )
    op.execute("REVOKE ALL PRIVILEGES ON TABLE broll_suggestions FROM PUBLIC")
    op.execute("REVOKE ALL PRIVILEGES ON TABLE broll_suggestions FROM clipah_api, clipah_worker")
    op.execute("GRANT SELECT, UPDATE ON TABLE broll_suggestions TO clipah_api")
    op.execute("GRANT SELECT, INSERT ON TABLE broll_suggestions TO clipah_worker")


def downgrade() -> None:
    """Remove the table, its grants, and the three enumerations it introduced."""
    op.drop_table("broll_plan_requests")
    op.drop_index("ix_broll_suggestions_workspace_candidate", table_name="broll_suggestions")
    op.drop_table("broll_suggestions")
    for name in ("broll_source_type", "broll_suggestion_status", "broll_coverage"):
        op.execute(f"DROP TYPE IF EXISTS {name}")
