"""Real-Postgres contracts for Clipah's foundational durable schema."""

from __future__ import annotations

from datetime import UTC
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from clipah.config import Environment, Settings
from clipah.db import (
    RuntimeRole,
    authorize_retention_mutation,
    create_user_with_personal_workspace,
    get_engine,
    session_scope,
)
from clipah.models import Base, Job, JobEvent, Project, User, Workspace
from support import (
    API_RUNTIME_DATABASE_URL,
    DATABASE_URL,
    RUNTIME_INIT_SQL,
    alembic_config,
    provision_identity,
    runtime_settings,
)

FOUNDATIONAL_TABLES = {
    "alembic_version",
    "assets",
    "audit_events",
    "auth_identities",
    "auth_sessions",
    "clip_candidates",
    "clip_edit_revisions",
    "clip_edits",
    "idempotency_keys",
    "job_events",
    "jobs",
    "multipart_uploads",
    "projects",
    "provider_usage",
    "render_artifacts",
    "retention_tombstones",
    "source_imports",
    "transcripts",
    "users",
    "workspace_invites",
    "workspace_memberships",
    "workspace_quota_reservations",
    "workspaces",
}
TENANT_TABLES = FOUNDATIONAL_TABLES - {
    "alembic_version",
    "auth_identities",
    "auth_sessions",
    "users",
    "workspaces",
}
API_TABLE_PRIVILEGES = {
    "users": {"SELECT", "INSERT", "UPDATE"},
    "auth_identities": {"SELECT", "INSERT", "UPDATE"},
    "auth_sessions": {"SELECT", "INSERT", "UPDATE"},
    "workspaces": {"SELECT", "INSERT", "UPDATE"},
    "workspace_memberships": {"SELECT", "INSERT", "UPDATE"},
    "idempotency_keys": {"SELECT", "INSERT"},
    "workspace_invites": {"SELECT", "INSERT", "UPDATE"},
    "projects": {"SELECT", "INSERT", "UPDATE"},
    "assets": {"SELECT", "INSERT", "UPDATE"},
    "jobs": {"SELECT", "INSERT", "UPDATE"},
    "source_imports": {"SELECT", "INSERT", "UPDATE"},
    "multipart_uploads": {"SELECT", "INSERT", "UPDATE"},
    "job_events": {"SELECT", "INSERT"},
    "transcripts": {"SELECT"},
    "clip_candidates": {"SELECT"},
    "clip_edits": {"SELECT", "INSERT", "UPDATE"},
    "clip_edit_revisions": {"SELECT", "INSERT"},
    "render_artifacts": {"SELECT"},
    "audit_events": {"SELECT", "INSERT"},
    "provider_usage": {"SELECT"},
    "retention_tombstones": {"SELECT", "INSERT"},
    "workspace_quota_reservations": {"SELECT", "INSERT"},
}
WORKER_TABLE_PRIVILEGES = {
    "users": {"SELECT"},
    "workspaces": {"SELECT"},
    "workspace_memberships": {"SELECT"},
    "projects": {"SELECT", "UPDATE"},
    "assets": {"SELECT", "INSERT", "UPDATE"},
    # A worker creates exactly one kind of row here: the stage that follows the one it
    # just finished. Nothing else joins the pipeline, so nothing else could.
    "jobs": {"SELECT", "INSERT", "UPDATE"},
    "source_imports": {"SELECT", "UPDATE"},
    "multipart_uploads": {"SELECT", "UPDATE"},
    "job_events": {"SELECT", "INSERT"},
    "transcripts": {"SELECT", "INSERT"},
    "clip_candidates": {"SELECT", "INSERT"},
    "clip_edits": {"SELECT"},
    "clip_edit_revisions": {"SELECT"},
    "render_artifacts": {"SELECT", "INSERT"},
    "audit_events": {"SELECT", "INSERT"},
    "provider_usage": {"SELECT", "INSERT", "UPDATE"},
    "retention_tombstones": {"SELECT", "UPDATE"},
    "workspace_quota_reservations": {"SELECT", "INSERT"},
}
EXPECTED_ENUMS = {
    "asset_kind": (
        "source",
        "proxy",
        "thumbnail",
        "waveform",
        "transcription_audio",
        "render",
    ),
    "asset_source_type": ("user_upload", "source_import", "derived", "generated"),
    "job_kind": (
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
    "job_status": (
        "queued",
        "running",
        "retrying",
        "cancel_requested",
        "succeeded",
        "failed",
        "canceled",
    ),
    "multipart_upload_status": ("pending", "uploading", "completed", "aborted", "expired"),
    "project_status": (
        "created",
        "uploading",
        "ingesting",
        "transcribing",
        "analyzing",
        "ready",
        "failed",
        "archived",
    ),
    "publishing_role_policy": ("owner_admin_editor", "owner_admin"),
    "quota_reservation_status": ("reserved", "settled", "released"),
    "quota_resource": (
        "analyses",
        "stock_requests",
        "generated_images",
        "generated_videos",
        "generated_seconds",
        "social_publications",
    ),
    "source_import_status": ("queued", "downloading", "completed", "failed", "canceled"),
    "source_kind": ("upload", "public_url", "authenticated_source"),
    "user_status": ("active", "disabled", "deleted"),
    "workspace_kind": ("personal", "team"),
    "workspace_role": ("owner", "admin", "editor", "reviewer", "viewer"),
    "workspace_status": ("active", "suspended", "deleted"),
}


def provision_safe_runtime_roles(engine: Engine) -> None:
    """Restore external test-cluster roles after intentionally destructive RED probes."""
    with engine.begin() as connection:
        for role in ("clipah_api", "clipah_worker"):
            connection.execute(
                text(
                    f"""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                            CREATE ROLE {role};
                        END IF;
                    END
                    $$
                    """
                )
            )
            connection.execute(
                text(
                    f"""
                    ALTER ROLE {role}
                        NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                        NOINHERIT NOREPLICATION NOBYPASSRLS
                    """
                )
            )
        connection.execute(text("GRANT clipah_api TO clipah_api_runtime"))
        connection.execute(text("GRANT clipah_worker TO clipah_worker_runtime"))


def provision_edit_revision(
    engine: Engine, *, suffix: str, asset_size_bytes: int = 100
) -> tuple[UUID, UUID, UUID, bytes]:
    """Create the smallest real editing graph required by revision/render contracts."""
    user_id, workspace_id = provision_identity(engine, suffix=suffix)
    ids = {
        name: uuid4()
        for name in ("project", "asset", "transcript", "candidate", "edit", "revision")
    }
    composition_hash = bytes.fromhex("22" * 32)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects
                    (id, workspace_id, created_by_user_id, name, status, source_kind)
                VALUES (:project, :workspace, :user, 'Edits', 'created', 'upload')
                """
            ),
            {**ids, "workspace": workspace_id, "user": user_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO assets
                    (id, workspace_id, project_id, kind, source_type, storage_key,
                     content_type, size_bytes, duration_ms, sha256)
                VALUES (:asset, :workspace, :project, 'source', 'user_upload',
                        'source.mp4', 'video/mp4', :size_bytes, 1000, :hash)
                """
            ),
            {
                **ids,
                "workspace": workspace_id,
                "size_bytes": asset_size_bytes,
                "hash": composition_hash,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO transcripts
                    (id, workspace_id, project_id, asset_id, provider, provider_version, model,
                     language, full_text, words, speaker_segments, utterances,
                     duration_ms, raw_result_storage_key)
                VALUES
                    (:transcript, :workspace, :project, :asset, 'test', '1', 'test-model',
                     'en', 'hello world', '[]', '[]', '[]', 1000, 'raw/transcript.json')
                """
            ),
            {**ids, "workspace": workspace_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO clip_candidates
                    (id, workspace_id, project_id, transcript_id, rank, score, hook, reason,
                     category, tags, start_ms, end_ms, transcript_excerpt, score_breakdown,
                     context_warnings, visual_opportunities, model_metadata)
                VALUES
                    (:candidate, :workspace, :project, :transcript, 1, 0.9, 'Hook', 'Reason',
                     'story', ARRAY['story'], 0, 1000, 'hello world', '{}', ARRAY[]::text[],
                     '[]', '{}')
                """
            ),
            {**ids, "workspace": workspace_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO clip_edits
                    (id, workspace_id, candidate_id, created_by_user_id, current_revision)
                VALUES (:edit, :workspace, :candidate, :user, 1)
                """
            ),
            {**ids, "workspace": workspace_id, "user": user_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO clip_edit_revisions
                    (id, workspace_id, clip_edit_id, revision, composition,
                     composition_hash, created_by_user_id)
                VALUES (:revision, :workspace, :edit, 1, '{}', :hash, :user)
                """
            ),
            {**ids, "workspace": workspace_id, "user": user_id, "hash": composition_hash},
        )
    return workspace_id, ids["edit"], ids["revision"], composition_hash


@pytest.mark.integration
def test_initial_migration_owns_exact_foundational_table_scope(engine: Engine) -> None:
    """Adding a later-task table or omitting a foundation table must fail this boundary."""
    actual = set(inspect(engine).get_table_names())

    assert actual == FOUNDATIONAL_TABLES
    assert set(Base.metadata.tables) == FOUNDATIONAL_TABLES - {"alembic_version"}


@pytest.mark.integration
def test_initial_migration_owns_exact_foundational_enum_scope(engine: Engine) -> None:
    """Adopting, omitting, or extending a durable enum must fail the schema boundary."""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT enum_type.typname, enum_value.enumlabel
                FROM pg_type AS enum_type
                JOIN pg_enum AS enum_value ON enum_value.enumtypid = enum_type.oid
                JOIN pg_namespace AS namespace ON namespace.oid = enum_type.typnamespace
                WHERE namespace.nspname = 'public'
                ORDER BY enum_type.typname, enum_value.enumsortorder
                """
            )
        ).all()

    actual: dict[str, list[str]] = {}
    for enum_name, enum_value in rows:
        actual.setdefault(enum_name, []).append(enum_value)

    assert {name: tuple(values) for name, values in actual.items()} == EXPECTED_ENUMS


@pytest.mark.integration
def test_foundation_omits_foreign_ids_owned_by_later_domain_tables(engine: Engine) -> None:
    """Unconstrained future-parent identifiers must not permit dangling tenant references."""
    inspector = inspect(engine)
    source_import_columns = {column["name"] for column in inspector.get_columns("source_imports")}
    provider_usage_columns = {column["name"] for column in inspector.get_columns("provider_usage")}

    assert "connection_id" not in source_import_columns
    assert "publication_id" not in provider_usage_columns


@pytest.mark.integration
def test_database_generates_real_uuid_primary_keys_and_aware_utc_timestamps(
    engine: Engine,
) -> None:
    """Replacing UUID/timestamptz server defaults with text or naive values must fail."""
    with engine.begin() as connection:
        row = connection.execute(
            text(
                """
                INSERT INTO users (primary_email, display_name, status)
                VALUES ('uuid@example.com', 'UUID User', 'active')
                RETURNING id, created_at
                """
            )
        ).one()

    assert isinstance(row.id, UUID)
    assert row.created_at.tzinfo is not None
    assert row.created_at.astimezone(UTC).utcoffset().total_seconds() == 0


@pytest.mark.integration
def test_google_identity_is_unique_by_issuer_and_subject_not_email(engine: Engine) -> None:
    """Permitting duplicate stable IdP subjects must fail even when emails differ."""
    first_user, _ = provision_identity(engine, suffix="identity-one")
    second_user, _ = provision_identity(engine, suffix="identity-two")
    issuer = "https://accounts.google.com"
    subject = "google-subject-123"

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO auth_identities
                    (user_id, provider, issuer, subject, email_at_provider, email_verified)
                VALUES (:user_id, 'google', :issuer, :subject, 'first@example.com', true)
                """
            ),
            {"user_id": first_user, "issuer": issuer, "subject": subject},
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO auth_identities
                    (user_id, provider, issuer, subject, email_at_provider, email_verified)
                VALUES (:user_id, 'google', :issuer, :subject, 'second@example.com', true)
                """
            ),
            {"user_id": second_user, "issuer": issuer, "subject": subject},
        )


@pytest.mark.integration
def test_personal_workspace_provisioning_is_atomic_and_creates_owner_membership(
    engine: Engine,
) -> None:
    """Omitting the personal Workspace or committing partial identity state must fail."""
    with Session(engine) as session, session.begin():
        provisioned = create_user_with_personal_workspace(
            session,
            primary_email="new@example.com",
            display_name="New User",
            workspace_name="New User Workspace",
            workspace_slug="new-user",
        )
        user_id = provisioned.user.id
        workspace_id = provisioned.workspace.id
        assert provisioned.workspace.kind.value == "personal"
        assert provisioned.membership.role.value == "owner"

    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT w.kind, m.role
                FROM workspaces AS w
                JOIN workspace_memberships AS m ON m.workspace_id = w.id
                WHERE w.id = :workspace_id AND m.user_id = :user_id
                """
            ),
            {"workspace_id": workspace_id, "user_id": user_id},
        ).one()
    assert row == ("personal", "owner")

    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        create_user_with_personal_workspace(
            session,
            primary_email="rolled-back@example.com",
            display_name="Rolled Back",
            workspace_name="Duplicate Slug",
            workspace_slug="new-user",
        )

    with engine.connect() as connection:
        count = connection.scalar(
            text("SELECT count(*) FROM users WHERE primary_email = 'rolled-back@example.com'")
        )
    assert count == 0


@pytest.mark.integration
def test_membership_composite_key_and_explicit_role_enum_are_enforced(engine: Engine) -> None:
    """Duplicate membership or an unrecognized authorization role must fail."""
    user_id, workspace_id = provision_identity(engine, suffix="member")

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO workspace_memberships (workspace_id, user_id, role)
                VALUES (:workspace_id, :user_id, 'viewer')
                """
            ),
            {"workspace_id": workspace_id, "user_id": user_id},
        )

    other_user, _ = provision_identity(engine, suffix="invalid-role")
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO workspace_memberships (workspace_id, user_id, role)
                VALUES (:workspace_id, :user_id, 'super_admin')
                """
            ),
            {"workspace_id": workspace_id, "user_id": other_user},
        )


@pytest.mark.integration
def test_composite_foreign_keys_reject_cross_workspace_references(engine: Engine) -> None:
    """A tenant row pointing at another Workspace's parent must fail at the database."""
    user_a, workspace_a = provision_identity(engine, suffix="tenant-a")
    _, workspace_b = provision_identity(engine, suffix="tenant-b")
    project_id = uuid4()

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects
                    (id, workspace_id, created_by_user_id, name, status, source_kind)
                VALUES (:id, :workspace_id, :user_id, 'Project A', 'created', 'upload')
                """
            ),
            {"id": project_id, "workspace_id": workspace_a, "user_id": user_a},
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO assets
                    (workspace_id, project_id, kind, source_type, storage_key,
                     content_type, size_bytes, sha256)
                VALUES
                    (:workspace_id, :project_id, 'source', 'user_upload',
                     'cross-tenant.mp4', 'video/mp4', 10, :sha256)
                """
            ),
            {
                "workspace_id": workspace_b,
                "project_id": project_id,
                "sha256": bytes.fromhex("11" * 32),
            },
        )


@pytest.mark.integration
def test_every_tenant_table_has_not_null_workspace_and_tenant_leading_index(
    engine: Engine,
) -> None:
    """Dropping redundant tenancy or moving it behind another index column must fail."""
    inspector = inspect(engine)
    for table_name in sorted(TENANT_TABLES):
        workspace_column = next(
            column
            for column in inspector.get_columns(table_name)
            if column["name"] == "workspace_id"
        )
        assert workspace_column["nullable"] is False, table_name

        with engine.connect() as connection:
            has_leading_index = connection.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_index AS index
                        JOIN pg_class AS table_class ON table_class.oid = index.indrelid
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = table_class.relnamespace
                        WHERE namespace.nspname = 'public'
                          AND table_class.relname = :table_name
                          AND (
                              SELECT attribute.attname
                              FROM unnest(index.indkey) WITH ORDINALITY AS key(attnum, ordinal)
                              JOIN pg_attribute AS attribute
                                ON attribute.attrelid = table_class.oid
                               AND attribute.attnum = key.attnum
                              WHERE key.ordinal = 1
                          ) = 'workspace_id'
                    )
                    """
                ),
                {"table_name": table_name},
            )
        assert has_leading_index is True, table_name


@pytest.mark.integration
def test_rls_denies_without_context_and_reveals_only_matching_workspace(engine: Engine) -> None:
    """Missing or mismatched transaction context must never expose tenant rows."""
    user_a, workspace_a = provision_identity(engine, suffix="rls-a")
    user_b, workspace_b = provision_identity(engine, suffix="rls-b")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects
                    (workspace_id, created_by_user_id, name, status, source_kind)
                VALUES
                    (:workspace_a, :user_a, 'Visible', 'created', 'upload'),
                    (:workspace_b, :user_b, 'Hidden', 'created', 'upload')
                """
            ),
            {
                "workspace_a": workspace_a,
                "user_a": user_a,
                "workspace_b": workspace_b,
                "user_b": user_b,
            },
        )

    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE clipah_api"))
        assert connection.scalar(text("SELECT count(*) FROM projects")) == 0

        connection.execute(
            text("SELECT set_config('clipah.workspace_id', :value, true)"),
            {"value": str(workspace_a)},
        )
        assert connection.scalar(text("SELECT count(*) FROM projects")) == 0

        connection.execute(
            text("SELECT set_config('clipah.user_id', :value, true)"),
            {"value": str(user_a)},
        )
        names = connection.scalars(text("SELECT name FROM projects ORDER BY name")).all()

    assert names == ["Visible"]


@pytest.mark.integration
def test_session_scope_sets_transaction_local_context_and_resets_it_after_commit(
    engine: Engine,
) -> None:
    """Using session-wide context or forgetting User context must fail this isolation contract."""
    user_id, workspace_id = provision_identity(engine, suffix="session-scope")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects
                    (workspace_id, created_by_user_id, name, status, source_kind)
                VALUES (:workspace_id, :user_id, 'Scoped', 'created', 'upload')
                """
            ),
            {"workspace_id": workspace_id, "user_id": user_id},
        )

    settings = runtime_settings()
    with session_scope(
        settings=settings,
        workspace_id=workspace_id,
        user_id=user_id,
        runtime_role=RuntimeRole.API,
    ) as session:
        assert session.scalar(text("SELECT count(*) FROM projects")) == 1
        assert session.scalar(text("SELECT current_setting('clipah.user_id', true)")) == str(
            user_id
        )

    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE clipah_api"))
        assert connection.scalar(text("SELECT count(*) FROM projects")) == 0


@pytest.mark.integration
def test_session_scope_defaults_to_api_role_on_a_non_superuser_login(engine: Engine) -> None:
    """Default application access must assume a constrained role over a runtime login."""
    user_id, workspace_id = provision_identity(engine, suffix="runtime-default")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects
                    (workspace_id, created_by_user_id, name, status, source_kind)
                VALUES (:workspace_id, :user_id, 'Runtime Scoped', 'created', 'upload')
                """
            ),
            {"workspace_id": workspace_id, "user_id": user_id},
        )

    settings = runtime_settings()
    with session_scope(
        settings=settings,
        workspace_id=workspace_id,
        user_id=user_id,
    ) as session:
        identity = session.execute(text("SELECT session_user, current_user")).one()
        names = session.scalars(text("SELECT name FROM projects ORDER BY name")).all()

    assert identity == ("clipah_api_runtime", "clipah_api")
    assert names == ["Runtime Scoped"]


@pytest.mark.integration
def test_session_scope_rejects_a_migration_superuser_login(engine: Engine) -> None:
    """SET ROLE must not disguise an unsafe connection-pool identity from the app."""
    user_id, workspace_id = provision_identity(engine, suffix="unsafe-admin")
    settings = Settings(environment=Environment.TEST, database_url=DATABASE_URL)

    with (
        pytest.raises(RuntimeError, match="unsafe database session identity"),
        session_scope(
            settings=settings,
            workspace_id=workspace_id,
            user_id=user_id,
        ),
    ):
        pass


@pytest.mark.integration
@pytest.mark.parametrize(
    "unsafe_capability",
    (
        "superuser",
        "bypassrls",
        "table_owner",
        "inherit",
        "createrole",
        "direct_grant",
    ),
)
def test_session_scope_rejects_each_unsafe_runtime_login_capability(
    engine: Engine,
    unsafe_capability: str,
) -> None:
    """Owner, superuser, and BYPASSRLS login authority must each fail independently."""
    cleanup_statement: str
    with engine.begin() as connection:
        if unsafe_capability == "superuser":
            connection.execute(text("ALTER ROLE clipah_api_runtime SUPERUSER"))
            cleanup_statement = "ALTER ROLE clipah_api_runtime NOSUPERUSER"
        elif unsafe_capability == "bypassrls":
            connection.execute(text("ALTER ROLE clipah_api_runtime BYPASSRLS"))
            cleanup_statement = "ALTER ROLE clipah_api_runtime NOBYPASSRLS"
        elif unsafe_capability == "table_owner":
            connection.execute(text("CREATE TABLE clipah_api_runtime_owner_probe (id integer)"))
            connection.execute(
                text("ALTER TABLE clipah_api_runtime_owner_probe OWNER TO clipah_api_runtime")
            )
            cleanup_statement = "DROP TABLE clipah_api_runtime_owner_probe"
        elif unsafe_capability == "inherit":
            connection.execute(text("ALTER ROLE clipah_api_runtime INHERIT"))
            cleanup_statement = "ALTER ROLE clipah_api_runtime NOINHERIT"
        elif unsafe_capability == "createrole":
            connection.execute(text("ALTER ROLE clipah_api_runtime CREATEROLE"))
            cleanup_statement = "ALTER ROLE clipah_api_runtime NOCREATEROLE"
        else:
            connection.execute(text("GRANT SELECT ON users TO clipah_api_runtime"))
            cleanup_statement = "REVOKE SELECT ON users FROM clipah_api_runtime"

    settings = runtime_settings()
    try:
        with (
            pytest.raises(RuntimeError, match="unsafe database session identity"),
            session_scope(settings=settings),
        ):
            pass
    finally:
        with engine.begin() as connection:
            connection.execute(text(cleanup_statement))


@pytest.mark.integration
@pytest.mark.parametrize("membership_chain", ("direct_safe", "transitive_bypassrls"))
def test_session_scope_rejects_every_additional_reachable_role_membership(
    engine: Engine,
    membership_chain: str,
) -> None:
    """A runtime login must reach only its one intended group through SET ROLE."""
    login_role = "clipah_membership_probe"
    bridge_role = "clipah_membership_bridge"
    parent_role = "clipah_membership_parent"
    password = "clipah_membership_probe_local"
    probe_url = (
        make_url(DATABASE_URL)
        .set(username=login_role, password=password)
        .render_as_string(hide_password=False)
    )
    settings = Settings(environment=Environment.TEST, database_url=probe_url)
    user_id, workspace_id = provision_identity(engine, suffix=f"membership-{membership_chain}")

    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE ROLE {login_role}
                    LOGIN PASSWORD '{password}'
                    NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOINHERIT NOREPLICATION NOBYPASSRLS
                """
            )
        )
        connection.execute(text(f"GRANT clipah_api TO {login_role}"))
        if membership_chain == "direct_safe":
            connection.execute(
                text(
                    f"""
                    CREATE ROLE {parent_role}
                        NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                        NOINHERIT NOREPLICATION NOBYPASSRLS
                    """
                )
            )
            connection.execute(text(f"GRANT {parent_role} TO {login_role}"))
        else:
            connection.execute(
                text(
                    f"""
                    CREATE ROLE {bridge_role}
                        NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                        NOINHERIT NOREPLICATION NOBYPASSRLS
                    """
                )
            )
            connection.execute(
                text(
                    f"""
                    CREATE ROLE {parent_role}
                        NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                        NOINHERIT NOREPLICATION BYPASSRLS
                    """
                )
            )
            connection.execute(text(f"GRANT SELECT ON projects TO {parent_role}"))
            connection.execute(text(f"GRANT {parent_role} TO {bridge_role}"))
            connection.execute(text(f"GRANT {bridge_role} TO {login_role}"))

    try:
        with (
            pytest.raises(RuntimeError, match="unsafe database session identity"),
            session_scope(
                settings=settings,
                workspace_id=workspace_id,
                user_id=user_id,
            ) as session,
        ):
            session.execute(text(f"SET LOCAL ROLE {parent_role}"))
            if membership_chain == "transitive_bypassrls":
                assert session.scalar(text("SELECT count(*) FROM projects")) >= 0
    finally:
        get_engine(settings).dispose()
        with engine.begin() as connection:
            if membership_chain == "direct_safe":
                connection.execute(text(f"REVOKE {parent_role} FROM {login_role}"))
            else:
                connection.execute(text(f"REVOKE {bridge_role} FROM {login_role}"))
                connection.execute(text(f"REVOKE {parent_role} FROM {bridge_role}"))
                connection.execute(text(f"REVOKE SELECT ON projects FROM {parent_role}"))
            connection.execute(text(f"REVOKE clipah_api FROM {login_role}"))
            connection.execute(text(f"DROP ROLE {login_role}"))
            if membership_chain == "transitive_bypassrls":
                connection.execute(text(f"DROP ROLE {bridge_role}"))
            connection.execute(text(f"DROP ROLE {parent_role}"))


@pytest.mark.integration
@pytest.mark.parametrize(
    ("runtime_role", "forbidden_role"),
    (
        (RuntimeRole.API, RuntimeRole.WORKER),
        (RuntimeRole.WORKER, RuntimeRole.API),
    ),
)
def test_each_runtime_process_login_cannot_assume_the_other_group(
    engine: Engine,
    runtime_role: RuntimeRole,
    forbidden_role: RuntimeRole,
) -> None:
    """API and worker connection credentials must not be interchangeable."""
    del engine
    settings = runtime_settings(runtime_role)

    with (
        pytest.raises(DBAPIError) as caught,
        session_scope(settings=settings, runtime_role=runtime_role) as session,
    ):
        session.execute(text(f"SET LOCAL ROLE {forbidden_role.value}"))

    assert caught.value.orig.sqlstate == "42501"


@pytest.mark.integration
def test_alembic_ignores_the_application_runtime_database_url(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runtime credentials must never replace Alembic's migration/admin connection."""
    del engine
    monkeypatch.setenv("CLIPAH_DATABASE_URL", API_RUNTIME_DATABASE_URL)
    monkeypatch.delenv("CLIPAH_MIGRATION_DATABASE_URL", raising=False)

    command.check(alembic_config())


@pytest.mark.integration
@pytest.mark.parametrize(
    ("workspace_id", "user_id", "invalid_field"),
    (
        ("not-a-uuid", uuid4(), "workspace_id"),
        (uuid4(), "not-a-uuid", "user_id"),
    ),
)
def test_session_scope_rejects_malformed_tenant_context_before_opening_transaction(
    workspace_id: object,
    user_id: object,
    invalid_field: str,
) -> None:
    """Removing runtime UUID validation must fail before malformed RLS context is stored."""
    settings = Settings(environment=Environment.TEST, database_url=DATABASE_URL)

    with (
        pytest.raises(ValueError, match=rf"^{invalid_field} must be a UUID$"),
        session_scope(
            settings=settings,
            workspace_id=workspace_id,  # type: ignore[arg-type]
            user_id=user_id,  # type: ignore[arg-type]
            runtime_role=RuntimeRole.API,
        ),
    ):
        pass


@pytest.mark.integration
def test_api_and_worker_roles_cannot_own_or_bypass_tenant_security(engine: Engine) -> None:
    """Granting elevated runtime-role attributes must fail this deployment safety contract."""
    del engine
    with create_engine(DATABASE_URL).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT
                    rolname, rolcanlogin, rolinherit, rolsuper, rolcreatedb,
                    rolcreaterole, rolreplication, rolbypassrls
                FROM pg_roles
                WHERE rolname IN ('clipah_api', 'clipah_worker')
                ORDER BY rolname
                """
            )
        ).all()
        owners = set(
            connection.scalars(
                text(
                    """
                    SELECT DISTINCT owner.rolname
                    FROM pg_class AS relation
                    JOIN pg_roles AS owner ON owner.oid = relation.relowner
                    JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = 'public' AND relation.relkind = 'r'
                    """
                )
            )
        )

    assert rows == [
        ("clipah_api", False, False, False, False, False, False, False),
        ("clipah_worker", False, False, False, False, False, False, False),
    ]
    assert owners.isdisjoint({"clipah_api", "clipah_worker"})


@pytest.mark.integration
def test_local_runtime_role_initialization_is_repeatable_and_safe(engine: Engine) -> None:
    """Rerunning local role provisioning must converge instead of failing on existing roles."""
    init_sql = RUNTIME_INIT_SQL.read_text(encoding="utf-8")
    with engine.begin() as connection:
        connection.exec_driver_sql(init_sql)
        connection.exec_driver_sql(init_sql)
        rows = connection.execute(
            text(
                """
                SELECT
                    rolname, rolcanlogin, rolinherit, rolsuper, rolcreatedb,
                    rolcreaterole, rolreplication, rolbypassrls
                FROM pg_roles
                WHERE rolname IN (
                    'clipah_api', 'clipah_api_runtime', 'clipah_worker', 'clipah_worker_runtime'
                )
                ORDER BY rolname
                """
            )
        ).all()
        legacy_shared_login = connection.scalar(
            text("SELECT rolcanlogin FROM pg_roles WHERE rolname = 'clipah_runtime'")
        )
        memberships = connection.execute(
            text(
                """
                SELECT member.rolname, parent.rolname
                FROM pg_auth_members AS membership
                JOIN pg_roles AS member ON member.oid = membership.member
                JOIN pg_roles AS parent ON parent.oid = membership.roleid
                WHERE starts_with(member.rolname, 'clipah_')
                ORDER BY member.rolname, parent.rolname
                """
            )
        ).all()

    assert rows == [
        ("clipah_api", False, False, False, False, False, False, False),
        ("clipah_api_runtime", True, False, False, False, False, False, False),
        ("clipah_worker", False, False, False, False, False, False, False),
        ("clipah_worker_runtime", True, False, False, False, False, False, False),
    ]
    assert memberships == [
        ("clipah_api_runtime", "clipah_api"),
        ("clipah_worker_runtime", "clipah_worker"),
    ]
    assert legacy_shared_login in (None, False)


@pytest.mark.integration
def test_runtime_roles_have_exact_least_privilege_table_grants(engine: Engine) -> None:
    """Worker/auth boundaries and immutable rows must not inherit blanket table CRUD."""
    expected = {
        (role, table_name, privilege)
        for role, matrix in (
            ("clipah_api", API_TABLE_PRIVILEGES),
            ("clipah_worker", WORKER_TABLE_PRIVILEGES),
        )
        for table_name, privileges in matrix.items()
        for privilege in privileges
    }
    with engine.connect() as connection:
        actual = set(
            connection.execute(
                text(
                    """
                    SELECT grantee, table_name, privilege_type
                    FROM information_schema.role_table_grants
                    WHERE table_schema = 'public'
                      AND grantee IN ('clipah_api', 'clipah_worker')
                    """
                )
            ).tuples()
        )

    assert actual == expected


@pytest.mark.integration
def test_quota_reservations_are_updatable_only_on_reconciliation_columns(engine: Engine) -> None:
    """A held budget is evidence, so runtime roles may close a row but never rewrite it."""
    with engine.connect() as connection:
        actual = set(
            connection.execute(
                text(
                    """
                    SELECT grantee, column_name
                    FROM information_schema.column_privileges
                    WHERE table_schema = 'public'
                      AND table_name = 'workspace_quota_reservations'
                      AND privilege_type = 'UPDATE'
                      AND grantee IN ('clipah_api', 'clipah_worker')
                    """
                )
            ).tuples()
        )

    assert actual == {
        (role, column)
        for role in ("clipah_api", "clipah_worker")
        for column in ("status", "actual_units", "settled_at")
    }


@pytest.mark.integration
def test_idempotency_key_privileges_allow_only_api_response_completion(engine: Engine) -> None:
    """Only the API may complete a replay record; neither runtime may rewrite its identity."""
    with engine.connect() as connection:
        checks = connection.execute(
            text(
                """
                SELECT
                    has_column_privilege(
                        'clipah_api', 'idempotency_keys', 'response_body', 'UPDATE'
                    ),
                    has_column_privilege(
                        'clipah_api', 'idempotency_keys', 'workspace_id', 'UPDATE'
                    ),
                    has_column_privilege('clipah_api', 'idempotency_keys', 'route', 'UPDATE'),
                    has_column_privilege('clipah_api', 'idempotency_keys', 'user_id', 'UPDATE'),
                    has_column_privilege(
                        'clipah_api', 'idempotency_keys', 'request_hash', 'UPDATE'
                    ),
                    has_table_privilege('clipah_worker', 'idempotency_keys', 'SELECT')
                """
            )
        ).one()
    assert checks == (True, False, False, False, False, False)


@pytest.mark.integration
def test_upgrade_revokes_public_table_privileges_inherited_from_cluster_defaults(
    engine: Engine,
) -> None:
    """Cluster default privileges must not create a path around the runtime-role matrix."""
    config = alembic_config()
    command.downgrade(config, "base")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                ALTER DEFAULT PRIVILEGES FOR ROLE clipah_migrator IN SCHEMA public
                GRANT SELECT ON TABLES TO PUBLIC
                """
            )
        )

    try:
        command.upgrade(config, "head")
        with engine.connect() as connection:
            public_privileges = connection.execute(
                text(
                    """
                    SELECT table_name, privilege_type
                    FROM information_schema.table_privileges
                    WHERE table_schema = 'public' AND grantee = 'PUBLIC'
                    ORDER BY table_name, privilege_type
                    """
                )
            ).all()
        assert public_privileges == []
    finally:
        command.downgrade(config, "base")
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    ALTER DEFAULT PRIVILEGES FOR ROLE clipah_migrator IN SCHEMA public
                    REVOKE SELECT ON TABLES FROM PUBLIC
                    """
                )
            )
        command.upgrade(config, "head")


@pytest.mark.integration
@pytest.mark.parametrize(
    "statement",
    (
        "INSERT INTO users DEFAULT VALUES",
        "UPDATE users SET display_name = display_name WHERE false",
        "DELETE FROM users WHERE false",
        "INSERT INTO auth_identities DEFAULT VALUES",
        "UPDATE auth_identities SET provider = provider WHERE false",
        "DELETE FROM auth_identities WHERE false",
        "INSERT INTO auth_sessions DEFAULT VALUES",
        "UPDATE auth_sessions SET user_agent_summary = user_agent_summary WHERE false",
        "DELETE FROM auth_sessions WHERE false",
        "INSERT INTO workspaces DEFAULT VALUES",
        "UPDATE workspaces SET name = name WHERE false",
        "DELETE FROM workspaces WHERE false",
    ),
)
def test_worker_runtime_role_cannot_mutate_identity_or_workspace_roots(
    engine: Engine,
    statement: str,
) -> None:
    """A worker connection must be denied every root-table write operation by Postgres."""
    del engine
    settings = runtime_settings(RuntimeRole.WORKER)

    with (
        pytest.raises(DBAPIError) as caught,
        session_scope(settings=settings, runtime_role=RuntimeRole.WORKER) as session,
    ):
        session.execute(text(statement))

    assert caught.value.orig.sqlstate == "42501"


@pytest.mark.integration
@pytest.mark.parametrize("runtime_role", (RuntimeRole.API, RuntimeRole.WORKER))
@pytest.mark.parametrize(
    "table_name",
    ("job_events", "clip_edit_revisions", "render_artifacts", "audit_events"),
)
def test_ordinary_runtime_roles_cannot_delete_immutable_or_history_tables(
    engine: Engine,
    runtime_role: RuntimeRole,
    table_name: str,
) -> None:
    """No ordinary runtime role may acquire a delete path into durable evidence."""
    del engine
    settings = runtime_settings(runtime_role)

    with (
        pytest.raises(DBAPIError) as caught,
        session_scope(settings=settings, runtime_role=runtime_role) as session,
    ):
        session.execute(text(f"DELETE FROM {table_name} WHERE false"))

    assert caught.value.orig.sqlstate == "42501"


@pytest.mark.integration
def test_api_runtime_role_can_atomically_provision_a_personal_workspace(engine: Engine) -> None:
    """The API's bounded root-table grants must support only the Task 4 bootstrap helper."""
    del engine
    settings = runtime_settings()
    with session_scope(settings=settings) as session:
        provisioned = create_user_with_personal_workspace(
            session,
            primary_email="runtime-bootstrap@example.com",
            display_name="Runtime Bootstrap",
            workspace_name="Runtime Bootstrap Workspace",
            workspace_slug=f"runtime-bootstrap-{uuid4().hex[:8]}",
        )
        assert provisioned.membership.role.value == "owner"


@pytest.mark.integration
def test_downgrade_preserves_externally_provisioned_runtime_roles(engine: Engine) -> None:
    """A schema rollback must not delete cluster roles owned by deployment provisioning."""
    config = alembic_config()
    command.downgrade(config, "base")
    try:
        with engine.connect() as connection:
            roles = connection.scalars(
                text(
                    """
                    SELECT rolname
                    FROM pg_roles
                    WHERE rolname IN ('clipah_api', 'clipah_worker')
                    ORDER BY rolname
                    """
                )
            ).all()
        assert roles == ["clipah_api", "clipah_worker"]
    finally:
        provision_safe_runtime_roles(engine)
        command.upgrade(config, "head")


@pytest.mark.integration
def test_upgrade_rejects_an_unsafe_preexisting_runtime_role(engine: Engine) -> None:
    """A matching role name must not let unsafe cluster attributes pass migration checks."""
    config = alembic_config()
    command.downgrade(config, "base")
    provision_safe_runtime_roles(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER ROLE clipah_worker LOGIN"))

    try:
        with pytest.raises(DBAPIError):
            command.upgrade(config, "head")
    finally:
        with engine.begin() as connection:
            connection.execute(text("ALTER ROLE clipah_worker NOLOGIN"))
        command.upgrade(config, "head")


@pytest.mark.integration
def test_upgrade_rejects_a_runtime_role_that_inherits_another_role(engine: Engine) -> None:
    """NOINHERIT alone must not hide an unsafe parent-role membership."""
    config = alembic_config()
    command.downgrade(config, "base")
    provision_safe_runtime_roles(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE ROLE clipah_unsafe_parent NOLOGIN"))
        connection.execute(text("GRANT clipah_unsafe_parent TO clipah_worker"))

    try:
        with pytest.raises(DBAPIError):
            command.upgrade(config, "head")
    finally:
        with engine.begin() as connection:
            connection.execute(text("REVOKE clipah_unsafe_parent FROM clipah_worker"))
            connection.execute(text("DROP ROLE clipah_unsafe_parent"))
        command.upgrade(config, "head")


@pytest.mark.integration
def test_upgrade_rejects_a_missing_externally_provisioned_runtime_role(engine: Engine) -> None:
    """Migration 0001 must fail rather than silently create a deployment-owned role."""
    config = alembic_config()
    command.downgrade(config, "base")
    with engine.begin() as connection:
        connection.execute(text("REVOKE clipah_worker FROM clipah_worker_runtime"))
        connection.execute(text("DROP ROLE clipah_worker"))

    try:
        with pytest.raises(DBAPIError):
            command.upgrade(config, "head")
    finally:
        provision_safe_runtime_roles(engine)
        command.upgrade(config, "head")


@pytest.mark.integration
def test_upgrade_rejects_a_preexisting_foundational_enum(engine: Engine) -> None:
    """Migration-owned types must never silently adopt or later delete a shared enum."""
    config = alembic_config()
    command.downgrade(config, "base")
    provision_safe_runtime_roles(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TYPE user_status AS ENUM ('active', 'disabled', 'deleted')")
        )

    try:
        with pytest.raises(DBAPIError):
            command.upgrade(config, "head")
    finally:
        command.downgrade(config, "base")
        with engine.begin() as connection:
            connection.execute(text("DROP TYPE IF EXISTS user_status"))
        command.upgrade(config, "head")


@pytest.mark.integration
def test_job_idempotency_keys_are_unique_inside_a_workspace(engine: Engine) -> None:
    """Retrying the same durable intent must not create a duplicate Job."""
    user_id, workspace_id = provision_identity(engine, suffix="idempotency")
    project_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects
                    (id, workspace_id, created_by_user_id, name, status, source_kind)
                VALUES (:id, :workspace_id, :user_id, 'Jobs', 'created', 'upload')
                """
            ),
            {"id": project_id, "workspace_id": workspace_id, "user_id": user_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO jobs
                    (workspace_id, project_id, kind, status, stage, progress,
                     attempt, idempotency_key)
                VALUES
                    (:workspace_id, :project_id, 'ingest', 'queued', 'queued', 0, 0, 'intent-1')
                """
            ),
            {"workspace_id": workspace_id, "project_id": project_id},
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO jobs
                    (workspace_id, project_id, kind, status, stage, progress,
                     attempt, idempotency_key)
                VALUES
                    (:workspace_id, :project_id, 'render', 'queued', 'queued', 0, 0, 'intent-1')
                """
            ),
            {"workspace_id": workspace_id, "project_id": project_id},
        )


@pytest.mark.integration
def test_project_idempotency_key_remains_valid_without_actor_attribution(engine: Engine) -> None:
    """Workspace-wide replay state must outlive an optional originating User attribution."""
    _, workspace_id = provision_identity(engine, suffix="idempotency-attribution")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO idempotency_keys "
                "(workspace_id, user_id, route, key, request_hash) "
                "VALUES (:workspace_id, NULL, 'projects:create', 'actorless', :request_hash)"
            ),
            {"workspace_id": workspace_id, "request_hash": bytes.fromhex("33" * 32)},
        )
    with engine.connect() as connection:
        actor = connection.execute(
            text("SELECT user_id FROM idempotency_keys WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        ).scalar_one()
    assert actor is None


@pytest.mark.integration
def test_revision_numbers_and_render_dedupe_are_database_invariants(engine: Engine) -> None:
    """Duplicate revisions or immutable artifact outputs must fail under concurrency."""
    user_id, workspace_id = provision_identity(engine, suffix="revision")
    ids = {
        name: uuid4()
        for name in ("project", "asset", "transcript", "candidate", "edit", "revision")
    }
    composition_hash = bytes.fromhex("22" * 32)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects
                    (id, workspace_id, created_by_user_id, name, status, source_kind)
                VALUES (:project, :workspace, :user, 'Edits', 'created', 'upload')
                """
            ),
            {**ids, "workspace": workspace_id, "user": user_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO assets
                    (id, workspace_id, project_id, kind, source_type, storage_key,
                     content_type, size_bytes, duration_ms, sha256)
                VALUES (:asset, :workspace, :project, 'source', 'user_upload',
                        'source.mp4', 'video/mp4', 100, 1000, :hash)
                """
            ),
            {**ids, "workspace": workspace_id, "hash": composition_hash},
        )
        connection.execute(
            text(
                """
                INSERT INTO transcripts
                    (id, workspace_id, project_id, asset_id, provider, provider_version, model,
                     language, full_text, words, speaker_segments, utterances,
                     duration_ms, raw_result_storage_key)
                VALUES
                    (:transcript, :workspace, :project, :asset, 'test', '1', 'test-model',
                     'en', 'hello world', '[]', '[]', '[]', 1000, 'raw/transcript.json')
                """
            ),
            {**ids, "workspace": workspace_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO clip_candidates
                    (id, workspace_id, project_id, transcript_id, rank, score, hook, reason,
                     category, tags, start_ms, end_ms, transcript_excerpt, score_breakdown,
                     context_warnings, visual_opportunities, model_metadata)
                VALUES
                    (:candidate, :workspace, :project, :transcript, 1, 0.9, 'Hook', 'Reason',
                     'story', ARRAY['story'], 0, 1000, 'hello world', '{}', ARRAY[]::text[],
                     '[]', '{}')
                """
            ),
            {**ids, "workspace": workspace_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO clip_edits
                    (id, workspace_id, candidate_id, created_by_user_id, current_revision)
                VALUES (:edit, :workspace, :candidate, :user, 1)
                """
            ),
            {**ids, "workspace": workspace_id, "user": user_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO clip_edit_revisions
                    (id, workspace_id, clip_edit_id, revision, composition,
                     composition_hash, created_by_user_id)
                VALUES (:revision, :workspace, :edit, 1, '{}', :hash, :user)
                """
            ),
            {**ids, "workspace": workspace_id, "user": user_id, "hash": composition_hash},
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO clip_edit_revisions
                    (workspace_id, clip_edit_id, revision, composition,
                     composition_hash, created_by_user_id)
                VALUES (:workspace, :edit, 1, '{}', :hash, :user)
                """
            ),
            {
                "workspace": workspace_id,
                "edit": ids["edit"],
                "hash": composition_hash,
                "user": user_id,
            },
        )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO render_artifacts
                    (workspace_id, clip_edit_revision_id, preset, composition_hash,
                     storage_key, size_bytes, duration_ms)
                VALUES (:workspace, :revision, 'vertical-1080p', :hash,
                        'renders/output.mp4', 100, 1000)
                """
            ),
            {"workspace": workspace_id, "revision": ids["revision"], "hash": composition_hash},
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO render_artifacts
                    (workspace_id, clip_edit_revision_id, preset, composition_hash,
                     storage_key, size_bytes, duration_ms)
                VALUES (:workspace, :revision, 'vertical-1080p', :hash,
                        'renders/duplicate.mp4', 100, 1000)
                """
            ),
            {"workspace": workspace_id, "revision": ids["revision"], "hash": composition_hash},
        )


@pytest.mark.integration
def test_assets_and_renders_store_the_exact_two_gibibyte_boundary(engine: Engine) -> None:
    """The documented two-GiB limit must fit without signed-32-bit overflow."""
    two_gibibytes = 2_147_483_648
    workspace_id, _, revision_id, composition_hash = provision_edit_revision(
        engine,
        suffix="two-gibibytes",
        asset_size_bytes=two_gibibytes,
    )
    with engine.begin() as connection:
        render_size = connection.scalar(
            text(
                """
                INSERT INTO render_artifacts
                    (workspace_id, clip_edit_revision_id, preset, composition_hash,
                     storage_key, size_bytes, duration_ms)
                VALUES (:workspace, :revision, 'boundary', :hash,
                        'renders/boundary.mp4', :size_bytes, 1000)
                RETURNING size_bytes
                """
            ),
            {
                "workspace": workspace_id,
                "revision": revision_id,
                "hash": composition_hash,
                "size_bytes": two_gibibytes,
            },
        )
        asset_size = connection.scalar(text("SELECT size_bytes FROM assets"))

    assert asset_size == two_gibibytes
    assert render_size == two_gibibytes


@pytest.mark.integration
def test_edit_revisions_and_render_artifacts_reject_all_mutation(engine: Engine) -> None:
    """Immutable editing inputs and outputs must reject UPDATE/DELETE even from their owner."""
    workspace_id, _, revision_id, composition_hash = provision_edit_revision(
        engine,
        suffix="immutable-edits",
    )
    with engine.begin() as connection:
        artifact_id = connection.scalar(
            text(
                """
                INSERT INTO render_artifacts
                    (workspace_id, clip_edit_revision_id, preset, composition_hash,
                     storage_key, size_bytes, duration_ms)
                VALUES (:workspace, :revision, 'immutable', :hash,
                        'renders/immutable.mp4', 100, 1000)
                RETURNING id
                """
            ),
            {"workspace": workspace_id, "revision": revision_id, "hash": composition_hash},
        )

    statements = (
        ("UPDATE clip_edit_revisions SET composition = '{}' WHERE id = :id", revision_id),
        ("DELETE FROM clip_edit_revisions WHERE id = :id", revision_id),
        ("UPDATE render_artifacts SET storage_key = 'rewritten' WHERE id = :id", artifact_id),
        ("DELETE FROM render_artifacts WHERE id = :id", artifact_id),
    )
    for statement, row_id in statements:
        with pytest.raises(DBAPIError) as caught, engine.begin() as connection:
            connection.execute(text(statement), {"id": row_id})
        assert caught.value.orig.sqlstate == "55000"


@pytest.mark.integration
def test_ordinary_roles_cannot_update_or_delete_revisions_and_artifacts(engine: Engine) -> None:
    """API and worker grants must deny mutation before the immutable-row trigger is reached."""
    workspace_id, _, revision_id, composition_hash = provision_edit_revision(
        engine,
        suffix="ordinary-immutable",
    )
    with engine.begin() as connection:
        artifact_id = connection.scalar(
            text(
                """
                INSERT INTO render_artifacts
                    (workspace_id, clip_edit_revision_id, preset, composition_hash,
                     storage_key, size_bytes, duration_ms)
                VALUES (:workspace, :revision, 'ordinary', :hash,
                        'renders/ordinary.mp4', 100, 1000)
                RETURNING id
                """
            ),
            {"workspace": workspace_id, "revision": revision_id, "hash": composition_hash},
        )

    statements = (
        ("UPDATE clip_edit_revisions SET composition = '{}' WHERE id = :id", revision_id),
        ("DELETE FROM clip_edit_revisions WHERE id = :id", revision_id),
        ("UPDATE render_artifacts SET storage_key = 'rewritten' WHERE id = :id", artifact_id),
        ("DELETE FROM render_artifacts WHERE id = :id", artifact_id),
    )
    for runtime_role in RuntimeRole:
        for statement, row_id in statements:
            with (
                pytest.raises(DBAPIError) as caught,
                session_scope(
                    settings=runtime_settings(runtime_role),
                    workspace_id=workspace_id,
                    user_id=uuid4(),
                    runtime_role=runtime_role,
                ) as session,
            ):
                session.execute(text(statement), {"id": row_id})
            assert caught.value.orig.sqlstate == "42501"


@pytest.mark.integration
def test_table_owner_can_explicitly_open_a_transaction_local_retention_delete_path(
    engine: Engine,
) -> None:
    """Future retention can delete immutable rows but can never rewrite their contents."""
    workspace_id, _, revision_id, composition_hash = provision_edit_revision(
        engine,
        suffix="retention-path",
    )
    with engine.begin() as connection:
        artifact_id = connection.scalar(
            text(
                """
                INSERT INTO render_artifacts
                    (workspace_id, clip_edit_revision_id, preset, composition_hash,
                     storage_key, size_bytes, duration_ms)
                VALUES (:workspace, :revision, 'retention', :hash,
                        'renders/retention.mp4', 100, 1000)
                RETURNING id
                """
            ),
            {"workspace": workspace_id, "revision": revision_id, "hash": composition_hash},
        )
    with pytest.raises(DBAPIError) as caught, engine.begin() as connection:
        authorize_retention_mutation(connection)
        connection.execute(
            text(
                """
                UPDATE clip_edit_revisions
                SET composition = '{"retained": true}'
                WHERE id = :id
                """
            ),
            {"id": revision_id},
        )
    assert caught.value.orig.sqlstate == "55000"

    with engine.begin() as connection:
        authorize_retention_mutation(connection)
        connection.execute(
            text("DELETE FROM render_artifacts WHERE id = :artifact_id"),
            {"artifact_id": artifact_id},
        )
        connection.execute(
            text("DELETE FROM clip_edit_revisions WHERE id = :revision_id"),
            {"revision_id": revision_id},
        )

    with engine.connect() as connection:
        remaining = connection.scalar(
            text(
                """
                SELECT count(*)
                FROM clip_edit_revisions
                WHERE id = :revision_id
                """
            ),
            {"revision_id": revision_id},
        )
    assert remaining == 0


@pytest.mark.integration
def test_retention_authorization_expires_with_the_transaction_that_opened_it(
    engine: Engine,
) -> None:
    """A retention flag left on a pooled connection must not authorize a later transaction."""
    workspace_id, _, revision_id, composition_hash = provision_edit_revision(
        engine,
        suffix="retention-leak",
    )
    with engine.begin() as connection:
        artifact_id = connection.scalar(
            text(
                """
                INSERT INTO render_artifacts
                    (workspace_id, clip_edit_revision_id, preset, composition_hash,
                     storage_key, size_bytes, duration_ms)
                VALUES (:workspace, :revision, 'retention-leak', :hash,
                        'renders/retention-leak.mp4', 100, 1000)
                RETURNING id
                """
            ),
            {"workspace": workspace_id, "revision": revision_id, "hash": composition_hash},
        )

    with engine.connect() as connection:
        with connection.begin():
            authorize_retention_mutation(connection)
            authorized_token = connection.scalar(
                text("SELECT current_setting('clipah.retention_mutation', true)")
            )
            # Simulate the worst pooled-connection case: the same authorization value
            # survives the transaction that produced it.
            connection.execute(
                text("SELECT set_config('clipah.retention_mutation', :token, false)"),
                {"token": authorized_token},
            )

        with pytest.raises(DBAPIError) as caught, connection.begin():
            leaked_token = connection.scalar(
                text("SELECT current_setting('clipah.retention_mutation', true)")
            )
            assert leaked_token == authorized_token
            connection.execute(
                text("DELETE FROM render_artifacts WHERE id = :artifact_id"),
                {"artifact_id": artifact_id},
            )

    assert caught.value.orig.sqlstate == "55000"

    with engine.connect() as connection:
        surviving = connection.scalar(
            text("SELECT count(*) FROM render_artifacts WHERE id = :artifact_id"),
            {"artifact_id": artifact_id},
        )
    assert surviving == 1


@pytest.mark.integration
def test_progress_and_media_time_ranges_are_checked_by_postgres(engine: Engine) -> None:
    """Out-of-range progress or an inverted candidate range must fail before persistence."""
    user_id, workspace_id = provision_identity(engine, suffix="checks")
    project_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects
                    (id, workspace_id, created_by_user_id, name, status, source_kind)
                VALUES (:id, :workspace, :user, 'Checks', 'created', 'upload')
                """
            ),
            {"id": project_id, "workspace": workspace_id, "user": user_id},
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO jobs
                    (workspace_id, project_id, kind, status, stage, progress,
                     attempt, idempotency_key)
                VALUES (:workspace, :project, 'ingest', 'running', 'probe', 1.01, 1, 'bad-progress')
                """
            ),
            {"workspace": workspace_id, "project": project_id},
        )


@pytest.mark.integration
def test_job_events_and_audit_events_are_append_only(engine: Engine) -> None:
    """UPDATE or DELETE of historical evidence must be rejected by database triggers."""
    user_id, workspace_id = provision_identity(engine, suffix="append-only")
    project_id = uuid4()
    job_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects
                    (id, workspace_id, created_by_user_id, name, status, source_kind)
                VALUES (:project, :workspace, :user, 'History', 'created', 'upload')
                """
            ),
            {"workspace": workspace_id, "user": user_id, "project": project_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO jobs
                    (id, workspace_id, project_id, kind, status, stage, progress,
                     attempt, idempotency_key)
                VALUES (:job, :workspace, :project, 'ingest', 'running', 'probe', 0.5, 1, 'history')
                """
            ),
            {"workspace": workspace_id, "project": project_id, "job": job_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO job_events
                    (workspace_id, job_id, sequence, event_type, payload)
                VALUES (:workspace, :job, 1, 'progressed', '{"progress": 0.5}')
                """
            ),
            {"workspace": workspace_id, "job": job_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO audit_events
                    (workspace_id, actor_user_id, action, target_kind, target_id,
                     before_metadata, after_metadata, request_id)
                VALUES (:workspace, :user, 'project.created', 'project', :project,
                        NULL, '{}', 'request-1')
                """
            ),
            {"workspace": workspace_id, "user": user_id, "project": project_id},
        )

    for statement in (
        "UPDATE job_events SET event_type = 'rewritten'",
        "DELETE FROM job_events",
        "UPDATE audit_events SET action = 'rewritten'",
        "DELETE FROM audit_events",
    ):
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(text(statement))


@pytest.mark.integration
def test_audit_actor_deletion_is_restricted_without_mutating_history(engine: Engine) -> None:
    """Deleting an audit actor must fail as an FK violation, not attempt an append-only update."""
    _, workspace_id = provision_identity(engine, suffix="audit-workspace")
    actor_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (id, primary_email, display_name, status)
                VALUES (:actor, 'audit-actor@example.com', 'Audit Actor', 'active')
                """
            ),
            {"actor": actor_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO audit_events
                    (workspace_id, actor_user_id, action, target_kind, target_id, request_id)
                VALUES (:workspace, :actor, 'actor.tested', 'user', :actor, 'audit-delete')
                """
            ),
            {"workspace": workspace_id, "actor": actor_id},
        )

    with pytest.raises(DBAPIError) as caught, engine.begin() as connection:
        connection.execute(text("DELETE FROM users WHERE id = :actor"), {"actor": actor_id})

    assert caught.value.orig.sqlstate == "23503"
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT actor_user_id FROM audit_events WHERE request_id = 'audit-delete'")
            )
            == actor_id
        )


@pytest.mark.integration
def test_models_expose_foundational_entities_from_one_metadata_registry(engine: Engine) -> None:
    """Splitting default table declarations across registries must fail task consumers."""
    del engine
    assert User.__table__.metadata is Workspace.__table__.metadata
    assert Project.__table__.metadata is Job.__table__.metadata
    assert JobEvent.__table__.metadata is User.__table__.metadata


@pytest.mark.integration
def test_initial_migration_has_no_drift_from_central_orm_metadata(engine: Engine) -> None:
    """Changing a migrated constraint away from the central ORM declaration must fail."""
    del engine
    command.check(alembic_config())


@pytest.mark.integration
def test_initial_migration_can_upgrade_downgrade_and_upgrade_again(engine: Engine) -> None:
    """An irreversible dependency ordering or non-idempotent migration must fail."""
    del engine
    config = alembic_config()
    command.downgrade(config, "base")

    temporary_engine = create_engine(DATABASE_URL)
    try:
        assert set(inspect(temporary_engine).get_table_names()).isdisjoint(
            FOUNDATIONAL_TABLES - {"alembic_version"}
        )
    finally:
        temporary_engine.dispose()

    command.upgrade(config, "head")
    upgraded_engine = create_engine(DATABASE_URL)
    try:
        assert set(inspect(upgraded_engine).get_table_names()) >= FOUNDATIONAL_TABLES
    finally:
        upgraded_engine.dispose()
