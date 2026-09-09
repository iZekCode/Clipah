"""Add durable Publication orchestration and transactional outbox.

Revision ID: 0020
Revises: 0019
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

_TENANT_PREDICATE = """
    workspace_id = NULLIF(current_setting('clipah.workspace_id', true), '')::uuid
    AND NULLIF(current_setting('clipah.user_id', true), '')::uuid IS NOT NULL
"""
_STATUSES = (
    "draft",
    "awaiting_approval",
    "scheduled",
    "preflighting",
    "transferring",
    "processing",
    "published",
    "retryable_failed",
    "reconnect_required",
    "permanent_failed",
    "cancelled",
)
_TABLES = (
    "publication_batches",
    "publications",
    "publication_attempts",
    "provider_events",
    "publication_outbox",
)


def upgrade() -> None:
    """Add tenant-safe snapshots, histories, provider evidence, and dispatch intent."""
    op.add_column("render_artifacts", sa.Column("sha256", sa.LargeBinary()))
    op.create_check_constraint(
        "sha256_is_sha256", "render_artifacts", "sha256 IS NULL OR octet_length(sha256) = 32"
    )
    postgresql.ENUM(*_STATUSES, name="publication_status").create(op.get_bind(), checkfirst=True)
    status = postgresql.ENUM(*_STATUSES, name="publication_status", create_type=False)

    op.create_table(
        "publication_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("edit_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("render_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_fingerprint", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_publication_batches_workspace_id_id"),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_publication_batches_workspace_key"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "edit_revision_id"],
            ["clip_edit_revisions.workspace_id", "clip_edit_revisions.id"],
            name="fk_publication_batches_workspace_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "render_artifact_id"],
            ["render_artifacts.workspace_id", "render_artifacts.id"],
            name="fk_publication_batches_workspace_artifact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_publication_batches_created_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "octet_length(request_fingerprint) = 32", name="request_fingerprint_sha256"
        ),
    )

    op.create_table(
        "publications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("social_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("edit_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("render_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("metadata_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("provider_options", postgresql.JSONB(), nullable=False),
        sa.Column("consent_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("capability_version", sa.String(length=128)),
        sa.Column("provider_policy_version", sa.String(length=128)),
        sa.Column("scheduled_for", sa.DateTime(timezone=True)),
        sa.Column("display_timezone", sa.String(length=255), nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("provider_operation_key", sa.String(length=255), nullable=False),
        sa.Column("provider_publication_id", sa.String(length=512)),
        sa.Column("provider_permalink", sa.Text()),
        sa.Column("checkpoint_metadata", postgresql.JSONB()),
        sa.Column("encrypted_checkpoint_reference", sa.Text()),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("normalized_error_code", sa.String(length=128)),
        sa.Column("sanitized_error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("transferred_at", sa.DateTime(timezone=True)),
        sa.Column("processing_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("workspace_id", "id", name="uq_publications_workspace_id_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "social_account_id",
            "idempotency_key",
            name="uq_publications_workspace_account_key",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "batch_id"],
            ["publication_batches.workspace_id", "publication_batches.id"],
            name="fk_publications_workspace_batch",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "social_account_id"],
            ["social_accounts.workspace_id", "social_accounts.id"],
            name="fk_publications_workspace_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "edit_revision_id"],
            ["clip_edit_revisions.workspace_id", "clip_edit_revisions.id"],
            name="fk_publications_workspace_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "render_artifact_id"],
            ["render_artifacts.workspace_id", "render_artifacts.id"],
            name="fk_publications_workspace_artifact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"],
            ["users.id"],
            name="fk_publications_approved_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("octet_length(artifact_sha256) = 32", name="artifact_sha256"),
        sa.CheckConstraint("attempt_count >= 0", name="nonnegative_attempt_count"),
        sa.CheckConstraint(
            "char_length(display_timezone) BETWEEN 1 AND 255", name="timezone_bounded"
        ),
    )
    op.create_index(
        "ix_publications_workspace_due",
        "publications",
        ["workspace_id", "status", "scheduled_for"],
    )

    op.create_table(
        "publication_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("provider_request_id", sa.String(length=512)),
        sa.Column("byte_checkpoint", sa.BigInteger()),
        sa.Column("request_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("response_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("error_code", sa.String(length=128)),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_publication_attempts_workspace_id_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "publication_id",
            "attempt",
            "stage",
            name="uq_publication_attempts_workspace_publication_attempt_stage",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "publication_id"],
            ["publications.workspace_id", "publications.id"],
            name="fk_publication_attempts_workspace_publication",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("attempt > 0", name="positive_attempt"),
        sa.CheckConstraint(
            "byte_checkpoint IS NULL OR byte_checkpoint >= 0", name="byte_checkpoint"
        ),
    )

    op.create_table(
        "provider_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("social_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True)),
        sa.Column("provider_event_id", sa.String(length=512)),
        sa.Column("payload_hash", sa.LargeBinary(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("signature_valid", sa.Boolean(), nullable=False),
        sa.Column("normalized_status", sa.String(length=128)),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("encrypted_raw_payload_reference", sa.Text()),
        sa.UniqueConstraint("workspace_id", "id", name="uq_provider_events_workspace_id_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "social_account_id"],
            ["social_accounts.workspace_id", "social_accounts.id"],
            name="fk_provider_events_workspace_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "publication_id"],
            ["publications.workspace_id", "publications.id"],
            name="fk_provider_events_workspace_publication",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("octet_length(payload_hash) = 32", name="payload_hash_sha256"),
    )
    op.create_index(
        "uq_provider_events_workspace_account_provider_id",
        "provider_events",
        ["workspace_id", "social_account_id", "provider_event_id"],
        unique=True,
        postgresql_where=sa.text("provider_event_id IS NOT NULL"),
    )
    op.create_index(
        "uq_provider_events_workspace_account_hash_type",
        "provider_events",
        ["workspace_id", "social_account_id", "payload_hash", "event_type"],
        unique=True,
        postgresql_where=sa.text("provider_event_id IS NULL"),
    )

    op.create_table(
        "publication_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("operation_key", sa.String(length=255), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_publication_outbox_workspace_id_id"),
        sa.UniqueConstraint(
            "workspace_id", "operation_key", name="uq_publication_outbox_workspace_operation"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "publication_id"],
            ["publications.workspace_id", "publications.id"],
            name="fk_publication_outbox_workspace_publication",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="nonnegative_attempt_count"),
    )
    op.create_index(
        "ix_publication_outbox_workspace_pending",
        "publication_outbox",
        ["workspace_id", "delivered_at"],
    )

    _protect_history_and_snapshots()
    _apply_security_and_grants()


def _protect_history_and_snapshots() -> None:
    """Reuse the common append-only guard and freeze approved snapshot columns."""
    for table_name in ("publication_batches", "publication_attempts", "provider_events"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_append_only
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION clipah_prevent_history_mutation()
            """
        )
    op.execute(
        """
        CREATE FUNCTION clipah_protect_publication_snapshot()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.approved_at IS NOT NULL AND (
                NEW.batch_id IS DISTINCT FROM OLD.batch_id OR
                NEW.social_account_id IS DISTINCT FROM OLD.social_account_id OR
                NEW.edit_revision_id IS DISTINCT FROM OLD.edit_revision_id OR
                NEW.render_artifact_id IS DISTINCT FROM OLD.render_artifact_id OR
                NEW.artifact_sha256 IS DISTINCT FROM OLD.artifact_sha256 OR
                NEW.approved_by_user_id IS DISTINCT FROM OLD.approved_by_user_id OR
                NEW.metadata_snapshot IS DISTINCT FROM OLD.metadata_snapshot OR
                NEW.provider_options IS DISTINCT FROM OLD.provider_options OR
                NEW.consent_snapshot IS DISTINCT FROM OLD.consent_snapshot OR
                NEW.capability_version IS DISTINCT FROM OLD.capability_version OR
                NEW.provider_policy_version IS DISTINCT FROM OLD.provider_policy_version OR
                NEW.scheduled_for IS DISTINCT FROM OLD.scheduled_for OR
                NEW.display_timezone IS DISTINCT FROM OLD.display_timezone OR
                NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key OR
                NEW.provider_operation_key IS DISTINCT FROM OLD.provider_operation_key OR
                NEW.approved_at IS DISTINCT FROM OLD.approved_at
            ) THEN
                RAISE EXCEPTION 'approved Publication snapshot is immutable'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_publications_snapshot_immutable
        BEFORE UPDATE ON publications
        FOR EACH ROW EXECUTE FUNCTION clipah_protect_publication_snapshot()
        """
    )


def _apply_security_and_grants() -> None:
    """Apply forced tenant isolation and the exact API/worker privilege split."""
    for table_name in _TABLES:
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table_name}_tenant_isolation ON {table_name} "
            f"USING ({_TENANT_PREDICATE}) WITH CHECK ({_TENANT_PREDICATE})"
        )
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table_name} FROM PUBLIC")
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table_name} FROM clipah_api, clipah_worker")

    op.execute("GRANT SELECT, INSERT ON TABLE publication_batches TO clipah_api")
    op.execute("GRANT SELECT, INSERT, UPDATE ON TABLE publications TO clipah_api")
    op.execute("GRANT SELECT, INSERT ON TABLE publication_attempts TO clipah_api")
    op.execute("GRANT SELECT, INSERT ON TABLE provider_events TO clipah_api")
    op.execute("GRANT SELECT, INSERT ON TABLE publication_outbox TO clipah_api")
    op.execute("GRANT SELECT ON TABLE publication_batches TO clipah_worker")
    op.execute("GRANT SELECT, UPDATE ON TABLE publications TO clipah_worker")
    op.execute("GRANT SELECT, INSERT ON TABLE publication_attempts TO clipah_worker")
    op.execute("GRANT SELECT, INSERT ON TABLE provider_events TO clipah_worker")
    op.execute("GRANT SELECT, INSERT, UPDATE ON TABLE publication_outbox TO clipah_worker")


def downgrade() -> None:
    """Remove only the additive Task 37 Publication foundation."""
    op.execute("DROP FUNCTION clipah_protect_publication_snapshot() CASCADE")
    for table_name in reversed(_TABLES):
        op.drop_table(table_name)
    op.execute("DROP TYPE IF EXISTS publication_status")
    op.execute(
        "ALTER TABLE render_artifacts "
        "DROP CONSTRAINT IF EXISTS ck_render_artifacts_sha256_is_sha256"
    )
    op.execute("ALTER TABLE render_artifacts DROP COLUMN IF EXISTS sha256")
