"""Hold a member's own source credential separately from everything that describes it.

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_TENANT_PREDICATE = """
    workspace_id = NULLIF(current_setting('clipah.workspace_id', true), '')::uuid
    AND NULLIF(current_setting('clipah.user_id', true), '')::uuid IS NOT NULL
"""


def upgrade() -> None:
    """Create the connection record and, separately, the encrypted material it names.

    The split is the point. `source_connections` is metadata a member is meant to see —
    which account, who authorized it, when they consented, when it expires, whether it has
    been revoked — and the API may read and write all of it. The credential itself lives
    in `source_connection_secrets`, which the API may only write and delete: an API process
    can create a connection and destroy it, and cannot read the secret back out. Only the
    worker that performs an import may read it, and only for as long as the row exists.
    """
    provider = postgresql.ENUM("youtube", name="source_connection_provider", create_type=False)
    kind = postgresql.ENUM("cookie", name="source_connection_kind", create_type=False)
    status = postgresql.ENUM(
        "active", "revoked", "expired", name="source_connection_status", create_type=False
    )
    provider.create(op.get_bind(), checkfirst=True)
    kind.create(op.get_bind(), checkfirst=True)
    status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "source_connections",
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
        sa.Column("provider", provider, nullable=False),
        sa.Column("kind", kind, nullable=False),
        sa.Column("status", status, nullable=False, server_default=sa.text("'active'")),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("domain_scope", sa.Text(), nullable=False),
        sa.Column("secret_reference", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "authorized_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_source_connections_workspace_id_id"),
        sa.CheckConstraint("expires_at > consented_at", name="connection_outlives_consent"),
    )
    op.create_index(
        "ix_source_connections_workspace_status",
        "source_connections",
        ["workspace_id", "status"],
    )

    op.create_table(
        "source_connection_secrets",
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
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key_reference", sa.Text(), nullable=False),
        sa.Column("wrapped_key", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_source_connection_secrets_workspace_id_id"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["source_connections.workspace_id", "source_connections.id"],
            name="fk_source_connection_secrets_workspace_connection",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_source_connection_secrets_workspace_connection",
        "source_connection_secrets",
        ["workspace_id", "connection_id"],
    )

    for table in ("source_connections", "source_connection_secrets"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING ({_TENANT_PREDICATE}) WITH CHECK ({_TENANT_PREDICATE})"
        )
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM PUBLIC")
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM clipah_api, clipah_worker")

    # An import records which connection it was admitted with, so a retry can only ever
    # reuse that one: a credential is never substituted for another between attempts.
    op.add_column(
        "source_imports",
        sa.Column("source_connection_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_source_imports_workspace_connection",
        "source_imports",
        "source_connections",
        ["workspace_id", "source_connection_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )

    # A member manages their own connections, so the API reads and writes the record.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE source_connections TO clipah_api")
    op.execute("GRANT SELECT ON TABLE source_connections TO clipah_worker")
    # The credential is write-and-destroy for the API and read-only for the worker: an
    # API process can store a secret and erase it, and can never read one back. Deleting
    # by identity requires reading the identity, so the API is granted SELECT on exactly
    # the identifying columns and on none of the material.
    op.execute("GRANT INSERT, DELETE ON TABLE source_connection_secrets TO clipah_api")
    op.execute(
        "GRANT SELECT (id, workspace_id, connection_id, created_at) "
        "ON TABLE source_connection_secrets TO clipah_api"
    )
    op.execute("GRANT SELECT ON TABLE source_connection_secrets TO clipah_worker")


def downgrade() -> None:
    """Remove both tables, their grants, and the enumerations they introduced."""
    op.drop_constraint(
        "fk_source_imports_workspace_connection",
        "source_imports",
        type_="foreignkey",
    )
    op.drop_column("source_imports", "source_connection_id")
    op.drop_index(
        "ix_source_connection_secrets_workspace_connection",
        table_name="source_connection_secrets",
    )
    op.drop_table("source_connection_secrets")
    op.drop_index("ix_source_connections_workspace_status", table_name="source_connections")
    op.drop_table("source_connections")
    for name in (
        "source_connection_status",
        "source_connection_kind",
        "source_connection_provider",
    ):
        op.execute(f"DROP TYPE IF EXISTS {name}")
