"""Create Clipah's foundational Postgres schema.

Revision ID: 0001
Revises:
Create Date: 2026-08-30

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("user_status", ("active", "disabled", "deleted")),
    ("workspace_kind", ("personal", "team")),
    ("publishing_role_policy", ("owner_admin_editor", "owner_admin")),
    ("workspace_status", ("active", "suspended", "deleted")),
    ("workspace_role", ("owner", "admin", "editor", "reviewer", "viewer")),
    (
        "project_status",
        (
            "created",
            "uploading",
            "ingesting",
            "transcribing",
            "analyzing",
            "ready",
            "failed",
            "archived",
        ),
    ),
    ("source_kind", ("upload", "public_url", "authenticated_source")),
    (
        "asset_kind",
        ("source", "proxy", "thumbnail", "waveform", "transcription_audio", "render"),
    ),
    ("asset_source_type", ("user_upload", "source_import", "derived", "generated")),
    (
        "source_import_status",
        ("queued", "downloading", "completed", "failed", "canceled"),
    ),
    (
        "multipart_upload_status",
        ("pending", "uploading", "completed", "aborted", "expired"),
    ),
    (
        "job_kind",
        (
            "source_import",
            "ingest",
            "transcribe",
            "analyze",
            "broll_plan",
            "broll_retrieve",
            "broll_generate",
            "render",
            "campaign_generate",
            "social_rendition",
            "social_publish",
            "social_reconcile",
            "cleanup",
        ),
    ),
    (
        "job_status",
        ("queued", "running", "retrying", "cancel_requested", "succeeded", "failed", "canceled"),
    ),
)

TENANT_TABLES = (
    "workspace_memberships",
    "workspace_invites",
    "projects",
    "assets",
    "jobs",
    "source_imports",
    "multipart_uploads",
    "job_events",
    "transcripts",
    "clip_candidates",
    "clip_edits",
    "clip_edit_revisions",
    "render_artifacts",
    "audit_events",
    "provider_usage",
    "retention_tombstones",
)
ALL_TABLES = (
    "users",
    "auth_identities",
    "auth_sessions",
    "workspaces",
    *TENANT_TABLES,
)
API_GRANTS = {
    "SELECT": (*ALL_TABLES,),
    "INSERT": (
        "users",
        "auth_identities",
        "auth_sessions",
        "workspaces",
        "workspace_memberships",
        "workspace_invites",
        "projects",
        "assets",
        "jobs",
        "source_imports",
        "multipart_uploads",
        "clip_edits",
        "clip_edit_revisions",
        "audit_events",
        "retention_tombstones",
    ),
    "UPDATE": (
        "users",
        "auth_identities",
        "auth_sessions",
        "workspaces",
        "workspace_memberships",
        "workspace_invites",
        "projects",
        "assets",
        "jobs",
        "source_imports",
        "multipart_uploads",
        "clip_edits",
    ),
}
WORKER_GRANTS = {
    "SELECT": (
        "users",
        "workspaces",
        "workspace_memberships",
        "projects",
        "assets",
        "jobs",
        "source_imports",
        "multipart_uploads",
        "job_events",
        "transcripts",
        "clip_candidates",
        "clip_edits",
        "clip_edit_revisions",
        "render_artifacts",
        "audit_events",
        "provider_usage",
        "retention_tombstones",
    ),
    "INSERT": (
        "assets",
        "job_events",
        "transcripts",
        "clip_candidates",
        "render_artifacts",
        "audit_events",
        "provider_usage",
    ),
    "UPDATE": (
        "projects",
        "assets",
        "jobs",
        "source_imports",
        "multipart_uploads",
        "provider_usage",
        "retention_tombstones",
    ),
}


def _enum(name: str, *values: str) -> postgresql.ENUM:
    return postgresql.ENUM(*values, name=name, create_type=False)


def _uuid_primary_key() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def _tenant_identity_constraints(table: str) -> tuple[sa.UniqueConstraint]:
    return (sa.UniqueConstraint("workspace_id", "id", name=f"uq_{table}_workspace_id_id"),)


def _validate_runtime_roles() -> None:
    for role in ("clipah_api", "clipah_worker"):
        op.execute(
            sa.text(
                f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                        RAISE EXCEPTION
                            'required externally provisioned runtime role {role} is missing';
                    END IF;
                    IF EXISTS (
                        SELECT 1
                        FROM pg_roles
                        WHERE rolname = '{role}'
                          AND (
                              rolcanlogin OR rolinherit OR rolsuper OR rolcreatedb
                              OR rolcreaterole OR rolreplication OR rolbypassrls
                          )
                    ) THEN
                        RAISE EXCEPTION 'unsafe attributes on runtime role {role}';
                    END IF;
                    IF EXISTS (
                        SELECT 1
                        FROM pg_auth_members AS membership
                        JOIN pg_roles AS member_role ON member_role.oid = membership.member
                        WHERE member_role.rolname = '{role}'
                    ) THEN
                        RAISE EXCEPTION 'runtime role {role} must not inherit another role';
                    END IF;
                END
                $$
                """
            )
        )


def _create_enums() -> None:
    bind = op.get_bind()
    for name, values in ENUMS:
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=False)


def _create_tables() -> None:
    op.create_table(
        "users",
        _uuid_primary_key(),
        sa.Column("primary_email", postgresql.CITEXT(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("avatar_url", sa.Text()),
        sa.Column(
            "status",
            _enum("user_status", "active", "disabled", "deleted"),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        _created_at(),
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "workspaces",
        _uuid_primary_key(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", postgresql.CITEXT(), nullable=False),
        sa.Column("kind", _enum("workspace_kind", "personal", "team"), nullable=False),
        sa.Column(
            "publishing_role_policy",
            _enum("publishing_role_policy", "owner_admin_editor", "owner_admin"),
            nullable=False,
            server_default=sa.text("'owner_admin_editor'"),
        ),
        sa.Column(
            "status",
            _enum("workspace_status", "active", "suspended", "deleted"),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        _created_at(),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("slug", name="uq_workspaces_slug"),
    )
    op.create_table(
        "auth_identities",
        _uuid_primary_key(),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", name="fk_auth_identities_user_id_users", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("email_at_provider", postgresql.CITEXT()),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        _created_at(),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("issuer", "subject", name="uq_auth_identities_issuer_subject"),
    )
    op.create_table(
        "auth_sessions",
        _uuid_primary_key(),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", name="fk_auth_sessions_user_id_users", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        _created_at(),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recent_auth_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("ip_hash", sa.LargeBinary()),
        sa.Column("user_agent_summary", sa.Text()),
        sa.CheckConstraint(
            "idle_expires_at > created_at",
            name=op.f("ck_auth_sessions_idle_expiry_after_creation"),
        ),
        sa.CheckConstraint(
            "absolute_expires_at > created_at",
            name=op.f("ck_auth_sessions_absolute_expiry_after_creation"),
        ),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )
    op.create_table(
        "workspace_memberships",
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "workspaces.id",
                name="fk_workspace_memberships_workspace_id_workspaces",
                ondelete="RESTRICT",
            ),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id", name="fk_workspace_memberships_user_id_users", ondelete="RESTRICT"
            ),
            primary_key=True,
        ),
        sa.Column(
            "role",
            _enum("workspace_role", "owner", "admin", "editor", "reviewer", "viewer"),
            nullable=False,
        ),
        sa.Column(
            "invited_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id",
                name="fk_workspace_memberships_invited_by_user_id_users",
                ondelete="SET NULL",
            ),
        ),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_workspace_memberships_workspace_id_role",
        "workspace_memberships",
        ["workspace_id", "role"],
    )
    op.create_table(
        "workspace_invites",
        _uuid_primary_key(),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "workspaces.id",
                name="fk_workspace_invites_workspace_id_workspaces",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column(
            "role",
            _enum("workspace_role", "owner", "admin", "editor", "reviewer", "viewer"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id",
                name="fk_workspace_invites_created_by_user_id_users",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        _created_at(),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "accepted_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id",
                name="fk_workspace_invites_accepted_by_user_id_users",
                ondelete="SET NULL",
            ),
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        *_tenant_identity_constraints("workspace_invites"),
        sa.UniqueConstraint("token_hash", name="uq_workspace_invites_token_hash"),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_workspace_invites_expiry_after_creation"),
        ),
    )
    op.create_table(
        "projects",
        _uuid_primary_key(),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "workspaces.id", name="fk_projects_workspace_id_workspaces", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id", name="fk_projects_created_by_user_id_users", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "status",
            _enum(
                "project_status",
                "created",
                "uploading",
                "ingesting",
                "transcribing",
                "analyzing",
                "ready",
                "failed",
                "archived",
            ),
            nullable=False,
            server_default=sa.text("'created'"),
        ),
        sa.Column(
            "source_kind",
            _enum("source_kind", "upload", "public_url", "authenticated_source"),
            nullable=False,
        ),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        *_tenant_identity_constraints("projects"),
    )
    op.create_table(
        "assets",
        _uuid_primary_key(),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "kind",
            _enum(
                "asset_kind",
                "source",
                "proxy",
                "thumbnail",
                "waveform",
                "transcription_audio",
                "render",
            ),
            nullable=False,
        ),
        sa.Column(
            "source_type",
            _enum("asset_source_type", "user_upload", "source_import", "derived", "generated"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("video_codec", sa.Text()),
        sa.Column("audio_codec", sa.Text()),
        sa.Column("sha256", sa.LargeBinary(), nullable=False),
        _created_at(),
        *_tenant_identity_constraints("assets"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_assets_workspace_id_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_assets_nonnegative_size")),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms > 0",
            name=op.f("ck_assets_positive_duration"),
        ),
        sa.CheckConstraint("width IS NULL OR width > 0", name=op.f("ck_assets_positive_width")),
        sa.CheckConstraint("height IS NULL OR height > 0", name=op.f("ck_assets_positive_height")),
    )
    op.create_table(
        "jobs",
        _uuid_primary_key(),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "kind",
            _enum(
                "job_kind",
                "source_import",
                "ingest",
                "transcribe",
                "analyze",
                "broll_plan",
                "broll_retrieve",
                "broll_generate",
                "render",
                "campaign_generate",
                "social_rendition",
                "social_publish",
                "social_reconcile",
                "cleanup",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum(
                "job_status",
                "queued",
                "running",
                "retrying",
                "cancel_requested",
                "succeeded",
                "failed",
                "canceled",
            ),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("progress", sa.Numeric(5, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        *_tenant_identity_constraints("jobs"),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_jobs_workspace_id_idempotency_key"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_jobs_workspace_id_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("progress >= 0 AND progress <= 1", name=op.f("ck_jobs_progress_range")),
        sa.CheckConstraint("attempt >= 0", name=op.f("ck_jobs_nonnegative_attempt")),
    )
    op.create_table(
        "source_imports",
        _uuid_primary_key(),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("normalized_source_url", sa.Text(), nullable=False),
        sa.Column("source_video_id", sa.Text(), nullable=False),
        sa.Column("authorization_attested_at", sa.DateTime(timezone=True)),
        sa.Column(
            "status",
            _enum(
                "source_import_status", "queued", "downloading", "completed", "failed", "canceled"
            ),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True)),
        _created_at(),
        *_tenant_identity_constraints("source_imports"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_source_imports_workspace_id_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["jobs.workspace_id", "jobs.id"],
            name="fk_source_imports_workspace_id_job_id_jobs",
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "multipart_uploads",
        _uuid_primary_key(),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("storage_upload_id", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column(
            "status",
            _enum(
                "multipart_upload_status", "pending", "uploading", "completed", "aborted", "expired"
            ),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        _created_at(),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_identity_constraints("multipart_uploads"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_multipart_uploads_workspace_id_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_multipart_uploads_expiry_after_creation"),
        ),
    )
    op.create_table(
        "job_events",
        _uuid_primary_key(),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        _created_at(),
        *_tenant_identity_constraints("job_events"),
        sa.UniqueConstraint(
            "workspace_id", "job_id", "sequence", name="uq_job_events_workspace_id_job_sequence"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["jobs.workspace_id", "jobs.id"],
            name="fk_job_events_workspace_id_job_id_jobs",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("sequence > 0", name=op.f("ck_job_events_positive_sequence")),
    )
    op.create_table(
        "transcripts",
        _uuid_primary_key(),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_version", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("language", sa.String(32), nullable=False),
        sa.Column("full_text", sa.Text(), nullable=False),
        sa.Column("words", postgresql.JSONB(), nullable=False),
        sa.Column("speaker_segments", postgresql.JSONB(), nullable=False),
        sa.Column("utterances", postgresql.JSONB(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("raw_result_storage_key", sa.Text(), nullable=False),
        _created_at(),
        *_tenant_identity_constraints("transcripts"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_transcripts_workspace_id_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "asset_id"],
            ["assets.workspace_id", "assets.id"],
            name="fk_transcripts_workspace_id_asset_id_assets",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("duration_ms > 0", name=op.f("ck_transcripts_positive_duration")),
    )
    op.create_table(
        "clip_candidates",
        _uuid_primary_key(),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score", sa.Numeric(6, 5), nullable=False),
        sa.Column("hook", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column(
            "tags", postgresql.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("transcript_excerpt", sa.Text(), nullable=False),
        sa.Column("score_breakdown", postgresql.JSONB(), nullable=False),
        sa.Column(
            "context_warnings",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("visual_opportunities", postgresql.JSONB(), nullable=False),
        sa.Column("model_metadata", postgresql.JSONB(), nullable=False),
        _created_at(),
        *_tenant_identity_constraints("clip_candidates"),
        sa.UniqueConstraint(
            "workspace_id", "project_id", "rank", name="uq_clip_candidates_workspace_project_rank"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_clip_candidates_workspace_id_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "transcript_id"],
            ["transcripts.workspace_id", "transcripts.id"],
            name="fk_clip_candidates_workspace_id_transcript_id_transcripts",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("rank > 0", name=op.f("ck_clip_candidates_positive_rank")),
        sa.CheckConstraint(
            "score >= 0 AND score <= 1", name=op.f("ck_clip_candidates_score_range")
        ),
        sa.CheckConstraint(
            "start_ms >= 0 AND end_ms > start_ms",
            name=op.f("ck_clip_candidates_valid_time_range"),
        ),
    )
    op.create_table(
        "clip_edits",
        _uuid_primary_key(),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id", name="fk_clip_edits_created_by_user_id_users", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column("current_revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        *_tenant_identity_constraints("clip_edits"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "candidate_id"],
            ["clip_candidates.workspace_id", "clip_candidates.id"],
            name="fk_clip_edits_workspace_id_candidate_id_clip_candidates",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "current_revision > 0", name=op.f("ck_clip_edits_positive_current_revision")
        ),
    )
    op.create_table(
        "clip_edit_revisions",
        _uuid_primary_key(),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("clip_edit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("composition", postgresql.JSONB(), nullable=False),
        sa.Column("composition_hash", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id",
                name="fk_clip_edit_revisions_created_by_user_id_users",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        _created_at(),
        *_tenant_identity_constraints("clip_edit_revisions"),
        sa.UniqueConstraint(
            "workspace_id",
            "clip_edit_id",
            "revision",
            name="uq_clip_edit_revisions_workspace_edit_revision",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "clip_edit_id"],
            ["clip_edits.workspace_id", "clip_edits.id"],
            name="fk_clip_edit_revisions_workspace_id_clip_edit_id_clip_edits",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("revision > 0", name=op.f("ck_clip_edit_revisions_positive_revision")),
    )
    op.create_table(
        "render_artifacts",
        _uuid_primary_key(),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("clip_edit_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True)),
        sa.Column("preset", sa.String(64), nullable=False),
        sa.Column("composition_hash", sa.LargeBinary(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        _created_at(),
        *_tenant_identity_constraints("render_artifacts"),
        sa.UniqueConstraint(
            "workspace_id",
            "composition_hash",
            "preset",
            name="uq_render_artifacts_workspace_composition_preset",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "clip_edit_revision_id"],
            ["clip_edit_revisions.workspace_id", "clip_edit_revisions.id"],
            name="fk_render_artifacts_workspace_revision_clip_edit_revisions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["jobs.workspace_id", "jobs.id"],
            name="fk_render_artifacts_workspace_id_job_id_jobs",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_render_artifacts_nonnegative_size")),
        sa.CheckConstraint("duration_ms > 0", name=op.f("ck_render_artifacts_positive_duration")),
    )
    op.create_table(
        "audit_events",
        _uuid_primary_key(),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "workspaces.id", name="fk_audit_events_workspace_id_workspaces", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id", name="fk_audit_events_actor_user_id_users", ondelete="RESTRICT"
            ),
        ),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("target_kind", sa.String(64), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("before_metadata", postgresql.JSONB()),
        sa.Column("after_metadata", postgresql.JSONB()),
        sa.Column("request_id", sa.String(128), nullable=False),
        _created_at(),
        *_tenant_identity_constraints("audit_events"),
    )
    op.create_table(
        "provider_usage",
        _uuid_primary_key(),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "workspaces.id",
                name="fk_provider_usage_workspace_id_workspaces",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("model_or_api_version", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("input_units", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("output_units", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "estimated_cost_usd", sa.Numeric(14, 6), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("actual_cost_usd", sa.Numeric(14, 6)),
        sa.Column("job_id", postgresql.UUID(as_uuid=True)),
        _created_at(),
        *_tenant_identity_constraints("provider_usage"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["jobs.workspace_id", "jobs.id"],
            name="fk_provider_usage_workspace_id_job_id_jobs",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "input_units >= 0", name=op.f("ck_provider_usage_nonnegative_input_units")
        ),
        sa.CheckConstraint(
            "output_units >= 0", name=op.f("ck_provider_usage_nonnegative_output_units")
        ),
        sa.CheckConstraint(
            "estimated_cost_usd >= 0",
            name=op.f("ck_provider_usage_nonnegative_estimated_cost"),
        ),
        sa.CheckConstraint(
            "actual_cost_usd IS NULL OR actual_cost_usd >= 0",
            name=op.f("ck_provider_usage_nonnegative_actual_cost"),
        ),
    )
    op.create_table(
        "retention_tombstones",
        _uuid_primary_key(),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "workspaces.id",
                name="fk_retention_tombstones_workspace_id_workspaces",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column("entity_kind", sa.String(64), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("storage_prefix", sa.Text()),
        sa.Column("eligible_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text()),
        _created_at(),
        *_tenant_identity_constraints("retention_tombstones"),
        sa.UniqueConstraint(
            "workspace_id",
            "entity_kind",
            "entity_id",
            name="uq_retention_tombstones_workspace_entity",
        ),
        sa.CheckConstraint(
            "failure_count >= 0",
            name=op.f("ck_retention_tombstones_nonnegative_failure_count"),
        ),
    )


def _protect_append_only_history() -> None:
    op.execute(
        """
        CREATE FUNCTION clipah_prevent_history_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               AND current_setting('clipah.retention_mutation', true) = 'on'
               AND current_user = pg_get_userbyid(
                    (
                        SELECT relation.relowner
                        FROM pg_class AS relation
                        WHERE relation.oid = TG_RELID
                    )
               ) THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
                USING ERRCODE = '55000';
        END
        $$
        """
    )
    for table_name in (
        "job_events",
        "clip_edit_revisions",
        "render_artifacts",
        "audit_events",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_append_only
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION clipah_prevent_history_mutation()
            """
        )


def _apply_tenant_security() -> None:
    predicate = """
        workspace_id = NULLIF(current_setting('clipah.workspace_id', true), '')::uuid
        AND NULLIF(current_setting('clipah.user_id', true), '')::uuid IS NOT NULL
    """
    for table_name in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table_name}
            USING ({predicate})
            WITH CHECK ({predicate})
            """
        )

    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {', '.join(ALL_TABLES)} FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA public TO clipah_api, clipah_worker")
    for role, grants in (("clipah_api", API_GRANTS), ("clipah_worker", WORKER_GRANTS)):
        for privilege, table_names in grants.items():
            op.execute(f"GRANT {privilege} ON TABLE {', '.join(table_names)} TO {role}")


def upgrade() -> None:
    """Expand an empty database to the complete Task 3 foundation."""
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    _validate_runtime_roles()
    _create_enums()
    _create_tables()
    _protect_append_only_history()
    _apply_tenant_security()


def downgrade() -> None:
    """Remove only objects introduced by this revision in reverse dependency order."""
    for table_name in reversed(TENANT_TABLES):
        if table_name in {"workspace_memberships", "workspace_invites", "projects"}:
            continue
        op.drop_table(table_name)
    op.drop_table("workspace_invites")
    op.drop_table("workspace_memberships")
    op.drop_table("auth_sessions")
    op.drop_table("auth_identities")
    op.drop_table("projects")
    op.drop_table("workspaces")
    op.drop_table("users")
    op.execute("DROP FUNCTION clipah_prevent_history_mutation()")

    bind = op.get_bind()
    for name, values in reversed(ENUMS):
        postgresql.ENUM(*values, name=name).drop(bind, checkfirst=False)

    op.execute("REVOKE USAGE ON SCHEMA public FROM clipah_api, clipah_worker")
    # citext is intentionally retained: it is a shared cluster capability and
    # dropping an extension that may predate this migration would be destructive.
