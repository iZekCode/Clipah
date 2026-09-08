"""Add Workspace Social Accounts and encrypted OAuth Grants.

Revision ID: 0019
Revises: 0018
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

_TENANT_PREDICATE = """
    workspace_id = NULLIF(current_setting('clipah.workspace_id', true), '')::uuid
    AND NULLIF(current_setting('clipah.user_id', true), '')::uuid IS NOT NULL
"""
_PROVIDERS = ("youtube", "instagram", "tiktok")
_STATUSES = ("active", "reconnect_required", "revoked")
_TABLES = ("social_oauth_ceremonies", "social_accounts", "oauth_grants")


def upgrade() -> None:
    """Add replay-safe ceremonies, redacted accounts, and encrypted grants."""
    postgresql.ENUM(*_PROVIDERS, name="social_provider").create(op.get_bind(), checkfirst=True)
    postgresql.ENUM(*_STATUSES, name="social_connection_status").create(
        op.get_bind(), checkfirst=True
    )
    provider = postgresql.ENUM(*_PROVIDERS, name="social_provider", create_type=False)
    status = postgresql.ENUM(*_STATUSES, name="social_connection_status", create_type=False)

    op.create_table(
        "social_oauth_ceremonies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", provider, nullable=False),
        sa.Column("state_hash", sa.LargeBinary(), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("requested_scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_social_oauth_ceremonies_workspace_id_id"
        ),
        sa.UniqueConstraint("state_hash", name="uq_social_oauth_ceremonies_state_hash"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_social_oauth_ceremonies_workspace_id_workspaces",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_social_oauth_ceremonies_actor_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("octet_length(state_hash) = 32", name="state_hash_is_sha256"),
        sa.CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        sa.CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= created_at", name="consumption_after_creation"
        ),
    )
    op.create_index(
        "ix_social_oauth_ceremonies_workspace_expiry",
        "social_oauth_ceremonies",
        ["workspace_id", "expires_at"],
    )

    op.create_table(
        "social_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", provider, nullable=False),
        sa.Column("external_account_id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("avatar_url", sa.Text()),
        sa.Column("account_type", sa.String(length=64)),
        sa.Column("login_family", sa.String(length=64), nullable=False),
        sa.Column("api_version", sa.String(length=32), nullable=False),
        sa.Column("connection_status", status, nullable=False),
        sa.Column("capability_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("authorized_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_validated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("workspace_id", "id", name="uq_social_accounts_workspace_id_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "external_account_id",
            name="uq_social_accounts_workspace_provider_external",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_social_accounts_workspace_id_workspaces",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["authorized_by_user_id"],
            ["users.id"],
            name="fk_social_accounts_authorized_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "char_length(external_account_id) BETWEEN 1 AND 512",
            name="bounded_external_account_id",
        ),
        sa.CheckConstraint(
            "char_length(display_name) BETWEEN 1 AND 256", name="bounded_display_name"
        ),
        sa.CheckConstraint(
            "(connection_status = 'revoked' AND revoked_at IS NOT NULL) OR "
            "(connection_status <> 'revoked' AND revoked_at IS NULL)",
            name="revocation_matches_status",
        ),
    )
    op.create_index(
        "ix_social_accounts_workspace_status",
        "social_accounts",
        ["workspace_id", "connection_status"],
    )

    op.create_table(
        "oauth_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("social_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key_reference", sa.Text(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("wrapped_key", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("granted_scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True)),
        sa.Column("refresh_token_expires_at", sa.DateTime(timezone=True)),
        sa.Column("token_version", sa.Integer(), nullable=False),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True)),
        sa.Column("reconnect_reason", sa.String(length=128)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_oauth_grants_workspace_id_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "social_account_id",
            name="uq_oauth_grants_workspace_social_account",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "social_account_id"],
            ["social_accounts.workspace_id", "social_accounts.id"],
            name="fk_oauth_grants_workspace_social_account",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("key_version > 0", name="positive_key_version"),
        sa.CheckConstraint("token_version > 0", name="positive_token_version"),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND octet_length(nonce) = 12 "
            "AND octet_length(wrapped_key) > 12 AND octet_length(ciphertext) > 0) OR "
            "(revoked_at IS NOT NULL AND octet_length(wrapped_key) = 0 "
            "AND octet_length(nonce) = 0 AND octet_length(ciphertext) = 0)",
            name="encrypted_material_matches_revocation",
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

    op.execute("GRANT SELECT, INSERT, UPDATE ON TABLE social_oauth_ceremonies TO clipah_api")
    op.execute("GRANT SELECT, INSERT, UPDATE ON TABLE social_accounts TO clipah_api")
    op.execute("GRANT SELECT, INSERT, UPDATE ON TABLE oauth_grants TO clipah_api")
    op.execute("GRANT SELECT, UPDATE ON TABLE social_accounts TO clipah_worker")
    op.execute("GRANT SELECT, UPDATE ON TABLE oauth_grants TO clipah_worker")


def downgrade() -> None:
    """Remove only the additive Task 36 Social Account schema."""
    for table_name in reversed(_TABLES):
        op.drop_table(table_name)
    op.execute("DROP TYPE IF EXISTS social_connection_status")
    op.execute("DROP TYPE IF EXISTS social_provider")
