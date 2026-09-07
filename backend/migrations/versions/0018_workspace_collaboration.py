"""Add append-only Workspace collaboration and Edit review evidence.

Revision ID: 0018
Revises: 0017
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

_TENANT_PREDICATE = """
    workspace_id = NULLIF(current_setting('clipah.workspace_id', true), '')::uuid
    AND NULLIF(current_setting('clipah.user_id', true), '')::uuid IS NOT NULL
"""
_EVENT_KINDS = (
    "invite_created",
    "invite_revoked",
    "invite_accepted",
    "role_changed",
    "member_removed",
    "ownership_transferred",
)
_ANCHOR_KINDS = ("timestamp", "item")
_DECISION_KINDS = ("request_changes", "approve")
_TABLES = (
    "workspace_membership_events",
    "edit_review_comments",
    "edit_review_comment_resolutions",
    "edit_review_decisions",
)


def upgrade() -> None:
    """Expand the schema with tenant-safe, append-only collaboration evidence."""
    for name, values in (
        ("workspace_membership_event_kind", _EVENT_KINDS),
        ("review_anchor_kind", _ANCHOR_KINDS),
        ("edit_review_decision_kind", _DECISION_KINDS),
    ):
        postgresql.ENUM(*values, name=name).create(op.get_bind(), checkfirst=True)

    event_kind = postgresql.ENUM(
        *_EVENT_KINDS, name="workspace_membership_event_kind", create_type=False
    )
    anchor_kind = postgresql.ENUM(*_ANCHOR_KINDS, name="review_anchor_kind", create_type=False)
    decision_kind = postgresql.ENUM(
        *_DECISION_KINDS, name="edit_review_decision_kind", create_type=False
    )
    workspace_role = postgresql.ENUM(
        "owner",
        "admin",
        "editor",
        "reviewer",
        "viewer",
        name="workspace_role",
        create_type=False,
    )

    op.create_table(
        "workspace_membership_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("invite_id", postgresql.UUID(as_uuid=True)),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", event_kind, nullable=False),
        sa.Column("old_role", workspace_role),
        sa.Column("new_role", workspace_role),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_workspace_membership_events_workspace_id_id"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "member_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_membership_events_workspace_member",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "invite_id"],
            ["workspace_invites.workspace_id", "workspace_invites.id"],
            name="fk_membership_events_workspace_invite",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_workspace_membership_events_actor_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "member_user_id IS NOT NULL OR invite_id IS NOT NULL", name="has_event_subject"
        ),
    )

    op.create_table(
        "edit_review_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("clip_edit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("clip_edit_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("anchor_kind", anchor_kind, nullable=False),
        sa.Column("anchor_ms", sa.Integer()),
        sa.Column("item_id", sa.String(length=128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_edit_review_comments_workspace_id_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "clip_edit_id"],
            ["clip_edits.workspace_id", "clip_edits.id"],
            name="fk_edit_review_comments_workspace_edit_clip_edits",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "clip_edit_revision_id"],
            ["clip_edit_revisions.workspace_id", "clip_edit_revisions.id"],
            name="fk_edit_review_comments_workspace_revision_clip_edit_revisions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_edit_review_comments_created_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("char_length(text) BETWEEN 1 AND 4000", name="bounded_text"),
        sa.CheckConstraint(
            "(anchor_kind = 'timestamp' AND anchor_ms IS NOT NULL "
            "AND anchor_ms >= 0 AND item_id IS NULL) OR "
            "(anchor_kind = 'item' AND item_id IS NOT NULL "
            "AND char_length(item_id) <= 128 AND anchor_ms IS NULL)",
            name="valid_anchor",
        ),
    )

    op.create_table(
        "edit_review_comment_resolutions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("comment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_edit_review_comment_resolutions_workspace_id_id"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "comment_id"],
            ["edit_review_comments.workspace_id", "edit_review_comments.id"],
            name="fk_review_resolutions_workspace_comment",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "comment_id",
            "sequence",
            name="uq_review_resolutions_workspace_comment_sequence",
        ),
        sa.CheckConstraint("sequence > 0", name="positive_sequence"),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_edit_review_comment_resolutions_actor_user_id_users",
            ondelete="RESTRICT",
        ),
    )

    op.create_table(
        "edit_review_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("clip_edit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("clip_edit_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("decision", decision_kind, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_edit_review_decisions_workspace_id_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "clip_edit_id"],
            ["clip_edits.workspace_id", "clip_edits.id"],
            name="fk_edit_review_decisions_workspace_edit_clip_edits",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "clip_edit_id",
            "sequence",
            name="uq_edit_review_decisions_workspace_edit_sequence",
        ),
        sa.CheckConstraint("sequence > 0", name="positive_sequence"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "clip_edit_revision_id"],
            ["clip_edit_revisions.workspace_id", "clip_edit_revisions.id"],
            name="fk_edit_review_decisions_workspace_revision_clip_edit_revisions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_edit_review_decisions_actor_user_id_users",
            ondelete="RESTRICT",
        ),
    )

    for table_name in _TABLES:
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table_name}_tenant_isolation ON {table_name} "
            f"USING ({_TENANT_PREDICATE}) WITH CHECK ({_TENANT_PREDICATE})"
        )
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table_name} FROM PUBLIC")
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table_name} FROM clipah_api, clipah_worker")
        op.execute(f"GRANT SELECT, INSERT ON TABLE {table_name} TO clipah_api")
        op.execute(f"GRANT SELECT ON TABLE {table_name} TO clipah_worker")


def downgrade() -> None:
    """Remove only the additive Task 35 collaboration schema."""
    for table_name in reversed(_TABLES):
        op.drop_table(table_name)
    for name in (
        "edit_review_decision_kind",
        "review_anchor_kind",
        "workspace_membership_event_kind",
    ):
        op.execute(f"DROP TYPE IF EXISTS {name}")
