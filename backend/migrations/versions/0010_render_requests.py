"""Record which Edit Revision and preset one render Job was admitted for.

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_TENANT_PREDICATE = """
    workspace_id = NULLIF(current_setting('clipah.workspace_id', true), '')::uuid
    AND NULLIF(current_setting('clipah.user_id', true), '')::uuid IS NOT NULL
"""


def upgrade() -> None:
    """Create the durable link between one render Job and what it must render.

    A Job carries its kind, its Project, and its progress, and deliberately carries no
    payload: nothing that crosses the broker may be anything but identifiers. A render
    still has to say which Edit Revision and which preset it was admitted for, so that
    fact is written here, in the same transaction that creates the Job, and read back by
    the worker from the Job's own identifier.
    """
    op.create_table(
        "render_requests",
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
        sa.Column("clip_edit_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("preset", sa.String(length=64), nullable=False),
        sa.Column("composition_hash", sa.LargeBinary(), nullable=False),
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
        sa.UniqueConstraint("workspace_id", "id", name="uq_render_requests_workspace_id_id"),
        sa.UniqueConstraint("workspace_id", "job_id", name="uq_render_requests_workspace_job"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "clip_edit_revision_id"],
            ["clip_edit_revisions.workspace_id", "clip_edit_revisions.id"],
            name="fk_render_requests_workspace_revision_clip_edit_revisions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["jobs.workspace_id", "jobs.id"],
            name="fk_render_requests_workspace_id_job_id_jobs",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_render_requests_workspace_job",
        "render_requests",
        ["workspace_id", "job_id"],
    )

    op.execute("ALTER TABLE render_requests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE render_requests FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY render_requests_tenant_isolation ON render_requests "
        f"USING ({_TENANT_PREDICATE}) WITH CHECK ({_TENANT_PREDICATE})"
    )
    op.execute("REVOKE ALL PRIVILEGES ON TABLE render_requests FROM PUBLIC")
    op.execute("REVOKE ALL PRIVILEGES ON TABLE render_requests FROM clipah_api, clipah_worker")
    # The API writes the request when it admits the Job; the worker only reads it back.
    op.execute("GRANT SELECT, INSERT ON TABLE render_requests TO clipah_api")
    op.execute("GRANT SELECT ON TABLE render_requests TO clipah_worker")


def downgrade() -> None:
    """Remove the render request table and everything granted on it."""
    op.drop_index("ix_render_requests_workspace_job", table_name="render_requests")
    op.drop_table("render_requests")
