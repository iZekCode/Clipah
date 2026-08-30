"""Real-Postgres contracts for Clipah's foundational durable schema."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from clipah.config import Environment, Settings
from clipah.db import RuntimeRole, create_user_with_personal_workspace, session_scope
from clipah.models import Job, JobEvent, Project, User, Workspace

DATABASE_URL = os.getenv(
    "CLIPAH_TEST_DATABASE_URL",
    "postgresql+psycopg://clipah_migrator:clipah_migrator_local@localhost:55433/"
    "clipah_rebuild_foundation",
)
BACKEND_ROOT = Path(__file__).resolve().parents[2]

FOUNDATIONAL_TABLES = {
    "alembic_version",
    "assets",
    "audit_events",
    "auth_identities",
    "auth_sessions",
    "clip_candidates",
    "clip_edit_revisions",
    "clip_edits",
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
    "workspaces",
}
LATER_FEATURE_TABLES = {
    "asset_provenance",
    "brand_kits",
    "campaign_outputs",
    "claim_evidence",
    "clip_variants",
    "edit_reviews",
    "oauth_grants",
    "publication_attempts",
    "publication_batches",
    "publications",
    "review_comments",
    "social_accounts",
    "source_connections",
    "templates",
}
TENANT_TABLES = FOUNDATIONAL_TABLES - {
    "alembic_version",
    "auth_identities",
    "auth_sessions",
    "users",
    "workspaces",
}


def alembic_config() -> Config:
    """Build Alembic configuration against only this worktree's database."""
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", DATABASE_URL)
    return config


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Upgrade the dedicated test database and expose a real SQLAlchemy engine."""
    command.upgrade(alembic_config(), "head")
    database_engine = create_engine(DATABASE_URL)
    yield database_engine
    database_engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(engine: Engine) -> Iterator[None]:
    """Keep tests isolated without invoking protected row-delete triggers."""
    command.upgrade(alembic_config(), "head")
    table_names = sorted(inspect(engine).get_table_names())
    application_tables = [name for name in table_names if name != "alembic_version"]
    if application_tables:
        quoted = ", ".join(f'"{name}"' for name in application_tables)
        with engine.begin() as connection:
            connection.execute(text(f"TRUNCATE TABLE {quoted} CASCADE"))
    yield


def provision_identity(engine: Engine, *, suffix: str) -> tuple[UUID, UUID]:
    """Create a User and personal Workspace through the production helper."""
    with Session(engine) as session, session.begin():
        provisioned = create_user_with_personal_workspace(
            session,
            primary_email=f"{suffix}@example.com",
            display_name=f"User {suffix}",
            workspace_name=f"{suffix.title()} Workspace",
            workspace_slug=f"{suffix}-{uuid4().hex[:8]}",
        )
        return provisioned.user.id, provisioned.workspace.id


@pytest.mark.integration
def test_initial_migration_owns_exact_foundational_table_scope(engine: Engine) -> None:
    """Adding a later-task table or omitting a foundation table must fail this boundary."""
    actual = set(inspect(engine).get_table_names())

    assert actual >= FOUNDATIONAL_TABLES
    assert actual.isdisjoint(LATER_FEATURE_TABLES)


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

    settings = Settings(environment=Environment.TEST, database_url=DATABASE_URL)
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
                SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls
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
        ("clipah_api", False, False, False, False),
        ("clipah_worker", False, False, False, False),
    ]
    assert owners.isdisjoint({"clipah_api", "clipah_worker"})


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
