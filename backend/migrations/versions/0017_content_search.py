"""Give a Workspace one derived, rebuildable index of the work it has already made.

Revision ID: 0017
Revises: 0016

`plan.md` names this migration `0007`. That numbering predates sixteen migrations that
have since landed; the file is numbered for the schema it actually follows.

`search_documents` is the only table in this schema that holds no authority. Every row is
derived from a Project, a Transcript, a Clip Candidate, or a Campaign Output, and can be
recomputed from those rows at any moment — which is why it is the one tenant table both
runtime roles may delete from. Losing it costs a rebuild and nothing else.

The stemmed text lives in `search_vector` rather than in a generated column because the
stemmer depends on the document's own language, and `to_tsvector(regconfig, text)` is only
immutable when its configuration is a literal. The indexer writes the vector, so one code
path decides how a language is stemmed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

_TENANT_PREDICATE = """
    workspace_id = NULLIF(current_setting('clipah.workspace_id', true), '')::uuid
    AND NULLIF(current_setting('clipah.user_id', true), '')::uuid IS NOT NULL
"""

_ENTITY_TYPES = ("project", "transcript", "clip", "campaign_output")
_EXPORT_STATES = ("not_exported", "exported")
_LANGUAGES = ("id", "en", "other")


def upgrade() -> None:
    """Create the derived search index, its tenant policy, and its two text indexes."""
    # Typo tolerance needs trigrams, and a name typed months later needs to match the one
    # that was saved with accents and punctuation in it.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")

    for name, values in (
        ("search_entity_type", _ENTITY_TYPES),
        ("search_export_state", _EXPORT_STATES),
        ("search_language", _LANGUAGES),
    ):
        postgresql.ENUM(*values, name=name).create(op.get_bind(), checkfirst=True)
    entity_type = postgresql.ENUM(*_ENTITY_TYPES, name="search_entity_type", create_type=False)
    export_state = postgresql.ENUM(*_EXPORT_STATES, name="search_export_state", create_type=False)
    language = postgresql.ENUM(*_LANGUAGES, name="search_language", create_type=False)

    op.create_table(
        "search_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", entity_type, nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        # What a member opens when they click the result. It is the indexed row itself
        # except for campaign copy, which is read on the clip it was written for.
        sa.Column("anchor_id", postgresql.UUID(as_uuid=True), nullable=False),
        # A Transcript is indexed as many documents, one per speaker turn, so a result is
        # a moment a person can open rather than a two-hour wall of text.
        sa.Column("segment_ordinal", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("title", sa.Text(), nullable=False),
        # The accent- and punctuation-free form the trigram index is built over, so a name
        # matches however the person who is looking for it happens to type it.
        sa.Column("title_normalized", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("speaker", sa.Text()),
        sa.Column(
            "topics", postgresql.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "tags", postgresql.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("language", language, nullable=False),
        sa.Column("start_ms", sa.Integer()),
        sa.Column("end_ms", sa.Integer()),
        sa.Column("export_state", export_state, nullable=False),
        # When the work itself happened, not when it was indexed: a date filter is about
        # the former, and a rebuild must not move anything into "this week".
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "indexed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_search_documents_workspace_id_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "entity_type",
            "entity_id",
            "segment_ordinal",
            name="uq_search_documents_workspace_entity_segment",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_search_documents_workspace_id_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("segment_ordinal >= 0", name="nonnegative_segment_ordinal"),
        sa.CheckConstraint(
            "(start_ms IS NULL AND end_ms IS NULL) OR (start_ms >= 0 AND end_ms > start_ms)",
            name="valid_document_time_range",
        ),
    )
    op.create_index(
        "ix_search_documents_workspace_project",
        "search_documents",
        ["workspace_id", "project_id"],
    )
    op.create_index(
        "ix_search_documents_workspace_type_created",
        "search_documents",
        ["workspace_id", "entity_type", "source_created_at"],
    )
    op.create_index(
        "ix_search_documents_vector",
        "search_documents",
        ["search_vector"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_search_documents_title_trgm",
        "search_documents",
        ["title_normalized"],
        postgresql_using="gin",
        postgresql_ops={"title_normalized": "gin_trgm_ops"},
    )

    op.execute("ALTER TABLE search_documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE search_documents FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY search_documents_tenant_isolation ON search_documents "
        f"USING ({_TENANT_PREDICATE}) WITH CHECK ({_TENANT_PREDICATE})"
    )
    op.execute("REVOKE ALL PRIVILEGES ON TABLE search_documents FROM PUBLIC")
    op.execute("REVOKE ALL PRIVILEGES ON TABLE search_documents FROM clipah_api, clipah_worker")
    # Both roles index and both roles may drop a document, because the whole table is
    # derived: the API reindexes a Project a member renamed, and the worker reindexes one
    # whose analysis or render it just finished. Deleting here destroys no evidence.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE search_documents "
        "TO clipah_api, clipah_worker"
    )
    # Reindexing a Project reads everything that Project contains, and campaign copy is
    # part of it. The worker reindexes after an analysis, so it needs to read that copy.
    op.execute("GRANT SELECT ON TABLE campaign_outputs TO clipah_worker")


def downgrade() -> None:
    """Drop the derived index and the vocabulary it introduced."""
    op.drop_index("ix_search_documents_title_trgm", table_name="search_documents")
    op.drop_index("ix_search_documents_vector", table_name="search_documents")
    op.drop_index("ix_search_documents_workspace_type_created", table_name="search_documents")
    op.drop_index("ix_search_documents_workspace_project", table_name="search_documents")
    op.drop_table("search_documents")
    for name in ("search_language", "search_export_state", "search_entity_type"):
        op.execute(f"DROP TYPE IF EXISTS {name}")
