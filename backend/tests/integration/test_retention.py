"""Integration contracts for tombstone-driven retention.

Retention deletes what nothing else can bring back, so every test here pins one of
the properties that keep a sweep from becoming an outage: it waits the full window,
it locks what it claims, it touches only the prefix its own tombstone named, and a
failed attempt is recorded rather than retried against something broader.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from clipah.assets.storage import (
    FakeObjectStore,
    ObjectListing,
    ObjectStoreUnavailableError,
)
from clipah.celery_app import RETENTION_SWEEP_TASK, configure_celery, create_celery_app
from clipah.db import RuntimeRole, retention_session_scope, session_scope
from clipah.jobs.admission import ProjectUnavailableError, admission_policy
from clipah.jobs.use_cases import create_job
from clipah.models import JobKind
from clipah.retention.policy import (
    RETENTION_ACTOR_ID,
    RetentionEntityKind,
    RetentionPolicy,
    project_prefix,
    workspace_prefix,
)
from clipah.retention.tasks import DischargeOutcome, discharge_tombstone, scan_workspace
from clipah.retention.use_cases import (
    claim_due_tombstones,
    purge_project_rows,
    purge_storage_prefix,
    purge_workspace_rows,
    record_purge_failure,
    schedule_tombstone,
)
from clipah.workspaces.authorization import DatabaseWorkspaceAuthorizer
from harness import Browser, Clock, StubGoogleProvider, assert_error, build_app, sign_in
from support import DATABASE_URL, provision_identity, runtime_settings

NOW = datetime.now(tz=UTC).replace(microsecond=0)


def retention_settings(**overrides: object) -> object:
    """Build settings for a process allowed to open owner-privileged retention work."""
    return runtime_settings(migration_database_url=DATABASE_URL, **overrides)


def _policy(**overrides: object) -> RetentionPolicy:
    """Build one deployment's retention policy from settings alone."""
    return RetentionPolicy.from_settings(retention_settings(**overrides))  # type: ignore[arg-type]


def _store(keys: tuple[str, ...]) -> FakeObjectStore:
    """Fill a deterministic store with objects a sweep may or may not be allowed to touch."""
    store = FakeObjectStore(now=lambda: NOW)
    for key in keys:
        store.put_file(key=key, content_type="video/mp4", file=BytesIO(b"media"))
    return store


@pytest.mark.integration
def test_a_tombstone_is_not_claimable_before_its_window_elapses(engine: Engine) -> None:
    """A thirty-day recovery window means nothing if a sweep may run on day one."""
    _, workspace_id = provision_identity(engine, suffix=f"retain-window-{uuid4().hex[:8]}")
    policy = _policy()
    project_id = uuid4()
    with Session(engine) as session, session.begin():
        _tenant(session, workspace_id)
        schedule_tombstone(
            session,
            workspace_id=workspace_id,
            entity_kind=RetentionEntityKind.PROJECT,
            entity_id=project_id,
            storage_prefix=project_prefix(workspace_id=workspace_id, project_id=project_id),
            eligible_at=policy.eligible_at(RetentionEntityKind.PROJECT, now=NOW),
        )

    with Session(engine) as session, session.begin():
        _tenant(session, workspace_id)
        early = claim_due_tombstones(session, now=NOW + timedelta(days=29), limit=10)
        due = claim_due_tombstones(session, now=NOW + timedelta(days=30), limit=10)

    assert early == ()
    assert [tombstone.entity_id for tombstone in due] == [project_id]


@pytest.mark.integration
def test_scheduling_the_same_entity_twice_keeps_one_tombstone(engine: Engine) -> None:
    """A repeated delete request must not queue a second purge of the same data."""
    _, workspace_id = provision_identity(engine, suffix=f"retain-once-{uuid4().hex[:8]}")
    project_id = uuid4()
    prefix = project_prefix(workspace_id=workspace_id, project_id=project_id)
    eligible = NOW + timedelta(days=30)

    for _ in range(2):
        with Session(engine) as session, session.begin():
            _tenant(session, workspace_id)
            schedule_tombstone(
                session,
                workspace_id=workspace_id,
                entity_kind=RetentionEntityKind.PROJECT,
                entity_id=project_id,
                storage_prefix=prefix,
                eligible_at=eligible,
            )

    with engine.connect() as connection:
        count = connection.scalar(
            text(
                "SELECT count(*) FROM retention_tombstones "
                "WHERE workspace_id = :workspace AND entity_id = :entity"
            ),
            {"workspace": workspace_id, "entity": project_id},
        )
    assert count == 1


@pytest.mark.integration
def test_a_claimed_tombstone_is_invisible_to_a_second_sweeper(engine: Engine) -> None:
    """Two sweepers must never delete the same prefix at the same time."""
    _, workspace_id = provision_identity(engine, suffix=f"retain-lock-{uuid4().hex[:8]}")
    project_id = uuid4()
    with Session(engine) as session, session.begin():
        _tenant(session, workspace_id)
        schedule_tombstone(
            session,
            workspace_id=workspace_id,
            entity_kind=RetentionEntityKind.PROJECT,
            entity_id=project_id,
            storage_prefix=project_prefix(workspace_id=workspace_id, project_id=project_id),
            eligible_at=NOW,
        )

    with Session(engine) as first, first.begin():
        _tenant(first, workspace_id)
        claimed = claim_due_tombstones(first, now=NOW, limit=10)
        assert len(claimed) == 1
        with Session(engine) as second, second.begin():
            _tenant(second, workspace_id)
            assert claim_due_tombstones(second, now=NOW, limit=10) == ()


@pytest.mark.integration
def test_a_purge_deletes_only_keys_inside_its_own_prefix(engine: Engine) -> None:
    """Another Workspace's media is never in scope, whatever a listing returns.

    The store here answers with a key it was never asked about, which is what a
    misconfigured bucket, a shared prefix, or a hostile provider response looks like.
    """
    _, workspace_id = provision_identity(engine, suffix=f"retain-scope-{uuid4().hex[:8]}")
    prefix = workspace_prefix(workspace_id)
    mine = f"{prefix}projects/{uuid4()}/source/{uuid4()}"
    theirs = f"{workspace_prefix(uuid4())}projects/{uuid4()}/source/{uuid4()}"

    class OverreachingStore(FakeObjectStore):
        """Return one key from outside the requested prefix, as a broken provider would."""

        def list_objects(self, *, prefix: str, limit: int, after: str | None = None) -> object:
            """Answer with every stored key regardless of the prefix asked for."""
            return ObjectListing(keys=(mine, theirs), next_token=None)

    store = OverreachingStore(now=lambda: NOW)
    for key in (mine, theirs):
        store.put_file(key=key, content_type="video/mp4", file=BytesIO(b"media"))

    outcome = purge_storage_prefix(store, prefix=prefix, page_size=100)

    assert outcome.deleted_keys == 1
    assert outcome.finished
    assert store.deleted == [mine]
    assert theirs in store.objects


@pytest.mark.integration
def test_a_purge_deletes_at_most_one_bounded_page_per_pass(engine: Engine) -> None:
    """A Workspace with a million objects must not be swept in one unbounded request."""
    prefix = workspace_prefix(uuid4())
    keys = tuple(f"{prefix}projects/p/source/{index:03d}" for index in range(5))
    store = _store(keys)

    first = purge_storage_prefix(store, prefix=prefix, page_size=2)
    second = purge_storage_prefix(store, prefix=prefix, page_size=2)

    assert (first.deleted_keys, first.finished) == (2, False)
    assert (second.deleted_keys, second.finished) == (2, False)
    assert len(store.deleted) == 4


@pytest.mark.integration
def test_a_failed_attempt_is_recorded_without_widening_its_target(engine: Engine) -> None:
    """A storage outage costs a retry, never a broader prefix on the next attempt."""
    _, workspace_id = provision_identity(engine, suffix=f"retain-fail-{uuid4().hex[:8]}")
    project_id = uuid4()
    prefix = project_prefix(workspace_id=workspace_id, project_id=project_id)
    with Session(engine) as session, session.begin():
        _tenant(session, workspace_id)
        schedule_tombstone(
            session,
            workspace_id=workspace_id,
            entity_kind=RetentionEntityKind.PROJECT,
            entity_id=project_id,
            storage_prefix=prefix,
            eligible_at=NOW,
        )

    with Session(engine) as session, session.begin():
        _tenant(session, workspace_id)
        claimed = claim_due_tombstones(session, now=NOW, limit=10)
        record_purge_failure(
            session, tombstone=claimed[0], error_code="RETENTION_STORAGE_UNAVAILABLE", now=NOW
        )

    with Session(engine) as session, session.begin():
        _tenant(session, workspace_id)
        again = claim_due_tombstones(session, now=NOW, limit=10)

    assert again[0].failure_count == 1
    assert again[0].storage_prefix == prefix
    assert again[0].deleted_at is None


@pytest.mark.integration
def test_a_storage_outage_leaves_the_prefix_for_the_next_sweep(engine: Engine) -> None:
    """An unavailable store is a retry, not an empty prefix that ends a tombstone."""
    prefix = workspace_prefix(uuid4())

    class UnavailableStore(FakeObjectStore):
        """Fail every listing the way an unreachable provider would."""

        def list_objects(self, *, prefix: str, limit: int, after: str | None = None) -> object:
            """Refuse to enumerate anything at all."""
            raise ObjectStoreUnavailableError("object store listing unavailable")

    with pytest.raises(ObjectStoreUnavailableError):
        purge_storage_prefix(UnavailableStore(now=lambda: NOW), prefix=prefix, page_size=10)


@pytest.mark.integration
def test_a_retention_transaction_holds_its_workspace_and_no_membership(engine: Engine) -> None:
    """The sweep declares one tenant per transaction and belongs to no Workspace."""
    _, workspace_id = provision_identity(engine, suffix=f"retain-context-{uuid4().hex[:8]}")

    with retention_session_scope(
        settings=retention_settings(),  # type: ignore[arg-type]
        workspace_id=workspace_id,
    ) as session:
        context = {
            name: session.execute(
                text("SELECT current_setting(:name, true)"), {"name": name}
            ).scalar_one()
            for name in ("clipah.workspace_id", "clipah.user_id")
        }
        membership = session.execute(
            text("SELECT count(*) FROM workspace_memberships WHERE user_id = :user"),
            {"user": RETENTION_ACTOR_ID},
        ).scalar_one()

    assert context["clipah.workspace_id"] == str(workspace_id)
    assert context["clipah.user_id"] == str(RETENTION_ACTOR_ID)
    assert membership == 0


def _tenant(session: Session, workspace_id: UUID) -> None:
    """Install the tenant context an owner-privileged retention statement still needs."""
    session.execute(
        text("SELECT set_config('clipah.workspace_id', :workspace, true)"),
        {"workspace": str(workspace_id)},
    )
    session.execute(
        text("SELECT set_config('clipah.user_id', :user, true)"),
        {"user": str(RETENTION_ACTOR_ID)},
    )


def _provision_project_graph(
    engine: Engine,
    *,
    suffix: str,
    identity: tuple[UUID, UUID] | None = None,
) -> dict[str, UUID]:
    """Create the smallest Project graph that spans jobs, edits, renders, and history."""
    user_id, workspace_id = identity or provision_identity(engine, suffix=suffix)
    ids = {
        name: uuid4()
        for name in ("project", "asset", "job", "transcript", "candidate", "edit", "revision")
    }
    parameters = {
        **ids,
        "workspace": workspace_id,
        "user": user_id,
        "hash": bytes.fromhex("33" * 32),
    }
    statements = (
        """
        INSERT INTO projects (id, workspace_id, created_by_user_id, name, status, source_kind)
        VALUES (:project, :workspace, :user, 'Retention', 'ready', 'upload')
        """,
        """
        INSERT INTO assets
            (id, workspace_id, project_id, kind, source_type, storage_key,
             content_type, size_bytes, duration_ms, sha256)
        VALUES (:asset, :workspace, :project, 'source', 'user_upload', 'source.mp4',
                'video/mp4', 100, 1000, :hash)
        """,
        """
        INSERT INTO jobs (id, workspace_id, project_id, kind, status, stage, attempt,
                          idempotency_key)
        VALUES (:job, :workspace, :project, 'ingest', 'succeeded', 'complete', 1,
                'retention-fixture')
        """,
        """
        INSERT INTO job_events (workspace_id, job_id, sequence, event_type, payload)
        VALUES (:workspace, :job, 1, 'job.succeeded', '{}')
        """,
        """
        INSERT INTO transcripts
            (id, workspace_id, project_id, asset_id, provider, provider_version, model,
             language, full_text, words, speaker_segments, utterances, duration_ms,
             raw_result_storage_key)
        VALUES (:transcript, :workspace, :project, :asset, 'test', '1', 'test-model', 'en',
                'hello world', '[]', '[]', '[]', 1000, 'raw/transcript.json')
        """,
        """
        INSERT INTO clip_candidates
            (id, workspace_id, project_id, transcript_id, rank, score, hook, reason,
             category, tags, start_ms, end_ms, transcript_excerpt, score_breakdown,
             context_warnings, visual_opportunities, model_metadata)
        VALUES (:candidate, :workspace, :project, :transcript, 1, 0.9, 'Hook', 'Reason',
                'story', ARRAY['story'], 0, 1000, 'hello world', '{}', ARRAY[]::text[],
                '[]', '{}')
        """,
        """
        INSERT INTO clip_edits (id, workspace_id, candidate_id, created_by_user_id,
                                current_revision)
        VALUES (:edit, :workspace, :candidate, :user, 1)
        """,
        """
        INSERT INTO clip_edit_revisions
            (id, workspace_id, clip_edit_id, revision, composition, composition_hash,
             created_by_user_id)
        VALUES (:revision, :workspace, :edit, 1, '{}', :hash, :user)
        """,
        """
        INSERT INTO render_artifacts
            (workspace_id, clip_edit_revision_id, preset, composition_hash, storage_key,
             size_bytes, duration_ms)
        VALUES (:workspace, :revision, '1080x1920', :hash, 'renders/export.mp4', 100, 1000)
        """,
    )
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement), parameters)
    return {**ids, "workspace": workspace_id, "user": user_id}


def _row_counts(engine: Engine, workspace_id: UUID) -> dict[str, int]:
    """Count every tenant table's surviving rows inside one Workspace."""
    from clipah.models import Base

    counts: dict[str, int] = {}
    with engine.connect() as connection:
        for table in Base.metadata.sorted_tables:
            if "workspace_id" not in table.columns:
                continue
            counts[table.name] = (
                connection.scalar(
                    text(f"SELECT count(*) FROM {table.name} WHERE workspace_id = :workspace"),
                    {"workspace": workspace_id},
                )
                or 0
            )
    return counts


@pytest.mark.integration
def test_purging_a_project_removes_its_graph_and_leaves_its_workspace_standing(
    engine: Engine,
) -> None:
    """A recovered window that has elapsed removes one Project, not a Workspace."""
    graph = _provision_project_graph(engine, suffix=f"retain-rows-{uuid4().hex[:8]}")
    workspace_id = graph["workspace"]

    with retention_session_scope(
        settings=retention_settings(),  # type: ignore[arg-type]
        workspace_id=workspace_id,
    ) as session:
        deleted = purge_project_rows(
            session, workspace_id=workspace_id, project_id=graph["project"]
        )

    counts = _row_counts(engine, workspace_id)
    assert deleted >= 9
    assert counts["projects"] == 0
    assert counts["assets"] == 0
    assert counts["jobs"] == 0
    assert counts["job_events"] == 0
    assert counts["clip_edit_revisions"] == 0
    assert counts["render_artifacts"] == 0
    assert counts["workspace_memberships"] == 1


@pytest.mark.integration
def test_purging_a_project_leaves_a_sibling_projects_rows_untouched(engine: Engine) -> None:
    """Two Projects in one Workspace share storage and a tenant, but never a purge."""
    kept = _provision_project_graph(engine, suffix=f"retain-keep-{uuid4().hex[:8]}")
    workspace_id = kept["workspace"]
    doomed_project = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects "
                "(id, workspace_id, created_by_user_id, name, status, source_kind) "
                "VALUES (:project, :workspace, :user, 'Doomed', 'ready', 'upload')"
            ),
            {"project": doomed_project, "workspace": workspace_id, "user": kept["user"]},
        )

    with retention_session_scope(
        settings=retention_settings(),  # type: ignore[arg-type]
        workspace_id=workspace_id,
    ) as session:
        purge_project_rows(session, workspace_id=workspace_id, project_id=doomed_project)

    counts = _row_counts(engine, workspace_id)
    assert counts["projects"] == 1
    assert counts["clip_candidates"] == 1
    assert counts["render_artifacts"] == 1


@pytest.mark.integration
def test_purging_a_workspace_keeps_the_records_that_prove_it_was_deleted(
    engine: Engine,
) -> None:
    """Compliance survives the data: the tombstone, the audit trail, and who could act."""
    graph = _provision_project_graph(engine, suffix=f"retain-ws-{uuid4().hex[:8]}")
    workspace_id = graph["workspace"]
    with Session(engine) as session, session.begin():
        _tenant(session, workspace_id)
        session.execute(
            text(
                "INSERT INTO audit_events "
                "(workspace_id, actor_user_id, action, target_kind, target_id, request_id) "
                "VALUES (:workspace, :user, 'workspace.deleted', 'workspace', :workspace, 'req')"
            ),
            {"workspace": workspace_id, "user": graph["user"]},
        )
        schedule_tombstone(
            session,
            workspace_id=workspace_id,
            entity_kind=RetentionEntityKind.WORKSPACE,
            entity_id=workspace_id,
            storage_prefix=workspace_prefix(workspace_id),
            eligible_at=NOW,
        )

    with retention_session_scope(
        settings=retention_settings(),  # type: ignore[arg-type]
        workspace_id=workspace_id,
    ) as session:
        purge_workspace_rows(session, workspace_id=workspace_id)

    counts = _row_counts(engine, workspace_id)
    preserved = {name for name, count in counts.items() if count > 0}
    assert preserved <= {
        "audit_events",
        "retention_tombstones",
        "workspace_memberships",
        "workspace_membership_events",
        "workspace_quota_reservations",
    }
    assert counts["audit_events"] == 1
    assert counts["retention_tombstones"] == 1
    assert counts["projects"] == 0
    with engine.connect() as connection:
        workspace_rows = connection.scalar(
            text("SELECT count(*) FROM workspaces WHERE id = :workspace"),
            {"workspace": workspace_id},
        )
    assert workspace_rows == 1


def _workspace_id(browser: Browser) -> UUID:
    """Return the signed-in User's personal Workspace identifier."""
    return UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])


def _create_project(browser: Browser, workspace_id: UUID, *, key: str) -> str:
    """Create one upload-backed Project through the public API."""
    response = browser.request(
        "POST",
        f"/api/v1/projects?workspace_id={workspace_id}",
        headers={"Idempotency-Key": key},
        json={"name": "Retention", "sourceKind": "upload"},
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _tombstones(engine: Engine, workspace_id: UUID, entity_id: UUID) -> list[datetime]:
    """Return the eligibility instants recorded for one entity."""
    with engine.connect() as connection:
        return [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT eligible_at FROM retention_tombstones "
                    "WHERE workspace_id = :workspace AND entity_id = :entity"
                ),
                {"workspace": workspace_id, "entity": entity_id},
            ).all()
        ]


@pytest.mark.integration
def test_deleting_a_project_schedules_its_purge_thirty_days_out(engine: Engine) -> None:
    """A member's delete starts the clock rather than the deletion."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    project_id = _create_project(browser, workspace_id, key=f"delete-{uuid4().hex[:8]}")

    assert (
        browser.request("DELETE", f"/api/v1/projects/{project_id}?workspace_id={workspace_id}")
    ).status_code == 204

    assert _tombstones(engine, workspace_id, UUID(project_id)) == [NOW + timedelta(days=30)]


@pytest.mark.integration
def test_restoring_a_project_cancels_the_purge_it_scheduled(engine: Engine) -> None:
    """Recovery is only real if the sweep no longer holds a claim on the data."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    project_id = _create_project(browser, workspace_id, key=f"restore-{uuid4().hex[:8]}")
    browser.request("DELETE", f"/api/v1/projects/{project_id}?workspace_id={workspace_id}")

    restored = browser.request(
        "POST", f"/api/v1/projects/{project_id}/restore?workspace_id={workspace_id}"
    )

    assert restored.status_code == 200
    assert _tombstones(engine, workspace_id, UUID(project_id)) == []


@pytest.mark.integration
def test_a_deleted_project_admits_no_further_work(engine: Engine) -> None:
    """Soft deletion hides a Project and stops new jobs, even inside its recovery window."""
    clock = Clock(NOW)
    app, flow, settings = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    project_id = _create_project(browser, workspace_id, key=f"admit-{uuid4().hex[:8]}")
    user_id = UUID(browser.get("/api/v1/me").json()["id"])
    browser.request("DELETE", f"/api/v1/projects/{project_id}?workspace_id={workspace_id}")

    with (
        session_scope(settings=settings, workspace_id=workspace_id, user_id=user_id) as session,
        pytest.raises(ProjectUnavailableError),
    ):
        create_job(
            session,
            policy=admission_policy(settings),
            access=DatabaseWorkspaceAuthorizer(session).access_for(
                user_id=user_id, workspace_id=workspace_id
            ),
            project_id=UUID(project_id),
            kind=JobKind.ANALYZE,
            idempotency_key=f"admit-{uuid4().hex[:8]}",
            now=NOW,
        )


def _upload(engine: Engine, *, workspace_id: UUID, project_id: UUID, status: str, age: timedelta):
    """Insert one multipart upload of a chosen age and state."""
    upload_id = uuid4()
    with Session(engine) as session, session.begin():
        _tenant(session, workspace_id)
        session.execute(
            text(
                """
                INSERT INTO multipart_uploads
                    (id, workspace_id, project_id, storage_upload_id, storage_key,
                     client_filename, content_type, declared_size_bytes, status,
                     created_at, expires_at)
                VALUES (:id, :workspace, :project, :provider_id, :key, 'display.mp4',
                        'video/mp4', 1024, :status, :created_at, :expires_at)
                """
            ),
            {
                "id": upload_id,
                "workspace": workspace_id,
                "project": project_id,
                "provider_id": f"provider-{upload_id}",
                "key": f"workspaces/{workspace_id}/projects/{project_id}/source/{upload_id}",
                "status": status,
                "created_at": NOW - age,
                "expires_at": NOW - age + timedelta(days=1),
            },
        )
    return upload_id


@pytest.mark.integration
def test_a_scan_expires_an_abandoned_upload_and_spares_a_recent_one(engine: Engine) -> None:
    """An upload nobody finished costs storage until retention notices it, and not before."""
    graph = _provision_project_graph(engine, suffix=f"retain-upload-{uuid4().hex[:8]}")
    workspace_id = graph["workspace"]
    stale = _upload(
        engine,
        workspace_id=workspace_id,
        project_id=graph["project"],
        status="uploading",
        age=timedelta(hours=25),
    )
    fresh = _upload(
        engine,
        workspace_id=workspace_id,
        project_id=graph["project"],
        status="uploading",
        age=timedelta(hours=1),
    )
    completed = _upload(
        engine,
        workspace_id=workspace_id,
        project_id=graph["project"],
        status="completed",
        age=timedelta(days=30),
    )

    with retention_session_scope(
        settings=retention_settings(),  # type: ignore[arg-type]
        workspace_id=workspace_id,
    ) as session:
        scan_workspace(session, policy=_policy(), now=NOW)

    assert _tombstones(engine, workspace_id, stale) == [NOW]
    assert _tombstones(engine, workspace_id, fresh) == []
    assert _tombstones(engine, workspace_id, completed) == []


@pytest.mark.integration
def test_a_scan_expires_a_revoked_source_connection_immediately(engine: Engine) -> None:
    """Cookie material outlives its revocation by nothing at all."""
    user_id, workspace_id = provision_identity(engine, suffix=f"retain-conn-{uuid4().hex[:8]}")
    connection_id = uuid4()
    with Session(engine) as session, session.begin():
        _tenant(session, workspace_id)
        session.execute(
            text(
                """
                INSERT INTO source_connections
                    (id, workspace_id, provider, kind, status, label, domain_scope,
                     secret_reference, authorized_by_user_id, consented_at, expires_at,
                     revoked_at)
                VALUES (:id, :workspace, 'youtube', 'cookie', 'revoked', 'Studio',
                        ARRAY['.youtube.com'], :reference, :user, :now, :expires, :now)
                """
            ),
            {
                "id": connection_id,
                "workspace": workspace_id,
                "reference": uuid4(),
                "user": user_id,
                "now": NOW,
                "expires": NOW + timedelta(days=7),
            },
        )

    with retention_session_scope(
        settings=retention_settings(),  # type: ignore[arg-type]
        workspace_id=workspace_id,
    ) as session:
        scan_workspace(session, policy=_policy(), now=NOW)

    assert _tombstones(engine, workspace_id, connection_id) == [NOW]


@pytest.mark.integration
def test_a_project_purge_waits_while_its_own_work_is_still_running(engine: Engine) -> None:
    """Deleting rows a running Job is writing to would break the Job, not free the space."""
    graph = _provision_project_graph(engine, suffix=f"retain-active-{uuid4().hex[:8]}")
    workspace_id = graph["workspace"]
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE jobs SET status = 'running' WHERE id = :job"),
            {"job": graph["job"]},
        )

    with retention_session_scope(
        settings=retention_settings(),  # type: ignore[arg-type]
        workspace_id=workspace_id,
    ) as session:
        schedule_tombstone(
            session,
            workspace_id=workspace_id,
            entity_kind=RetentionEntityKind.PROJECT,
            entity_id=graph["project"],
            storage_prefix=project_prefix(workspace_id=workspace_id, project_id=graph["project"]),
            eligible_at=NOW,
        )
        claimed = claim_due_tombstones(session, now=NOW, limit=10)
        outcome = discharge_tombstone(
            session,
            _store(()),
            tombstone=claimed[0],
            policy=_policy(),
            now=NOW,
        )

    assert outcome is DischargeOutcome.DEFERRED
    assert _row_counts(engine, workspace_id)["projects"] == 1


@pytest.mark.integration
def test_discharging_a_project_tombstone_removes_its_media_and_its_rows(engine: Engine) -> None:
    """Once the window has passed and nothing is running, the data is actually gone."""
    graph = _provision_project_graph(engine, suffix=f"retain-purge-{uuid4().hex[:8]}")
    workspace_id = graph["workspace"]
    prefix = project_prefix(workspace_id=workspace_id, project_id=graph["project"])
    store = _store((f"{prefix}source/{uuid4()}", f"{prefix}renders/{uuid4()}"))

    with retention_session_scope(
        settings=retention_settings(),  # type: ignore[arg-type]
        workspace_id=workspace_id,
    ) as session:
        schedule_tombstone(
            session,
            workspace_id=workspace_id,
            entity_kind=RetentionEntityKind.PROJECT,
            entity_id=graph["project"],
            storage_prefix=prefix,
            eligible_at=NOW,
        )
        claimed = claim_due_tombstones(session, now=NOW, limit=10)
        outcome = discharge_tombstone(
            session, store, tombstone=claimed[0], policy=_policy(), now=NOW
        )

    counts = _row_counts(engine, workspace_id)
    assert outcome is DischargeOutcome.PURGED
    assert store.objects == {}
    assert counts["projects"] == 0
    assert counts["render_artifacts"] == 0
    with engine.connect() as connection:
        discharged = connection.scalar(
            text(
                "SELECT deleted_at FROM retention_tombstones "
                "WHERE workspace_id = :workspace AND entity_id = :entity"
            ),
            {"workspace": workspace_id, "entity": graph["project"]},
        )
    assert discharged == NOW


@pytest.mark.integration
def test_a_storage_failure_during_a_purge_keeps_the_rows_it_was_going_to_delete(
    engine: Engine,
) -> None:
    """Rows outlive an outage: losing them while the media survives helps nobody."""
    graph = _provision_project_graph(engine, suffix=f"retain-outage-{uuid4().hex[:8]}")
    workspace_id = graph["workspace"]
    prefix = project_prefix(workspace_id=workspace_id, project_id=graph["project"])

    class UnavailableStore(FakeObjectStore):
        """Fail every listing the way an unreachable provider would."""

        def list_objects(self, *, prefix: str, limit: int, after: str | None = None) -> object:
            """Refuse to enumerate anything at all."""
            raise ObjectStoreUnavailableError("object store listing unavailable")

    with retention_session_scope(
        settings=retention_settings(),  # type: ignore[arg-type]
        workspace_id=workspace_id,
    ) as session:
        schedule_tombstone(
            session,
            workspace_id=workspace_id,
            entity_kind=RetentionEntityKind.PROJECT,
            entity_id=graph["project"],
            storage_prefix=prefix,
            eligible_at=NOW,
        )
        claimed = claim_due_tombstones(session, now=NOW, limit=10)
        outcome = discharge_tombstone(
            session,
            UnavailableStore(now=lambda: NOW),
            tombstone=claimed[0],
            policy=_policy(),
            now=NOW,
        )

    assert outcome is DischargeOutcome.FAILED
    assert _row_counts(engine, workspace_id)["projects"] == 1
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT failure_count, last_error, deleted_at, storage_prefix "
                "FROM retention_tombstones WHERE workspace_id = :workspace AND entity_id = :entity"
            ),
            {"workspace": workspace_id, "entity": graph["project"]},
        ).one()
    assert row.failure_count == 1
    assert row.deleted_at is None
    assert row.storage_prefix == prefix


@pytest.mark.unit
def test_the_sweep_is_scheduled_by_the_deployment_rather_than_requested() -> None:
    """Nobody asks for retention, so its cadence belongs to configuration."""
    settings = runtime_settings(
        RuntimeRole.WORKER,
        redis_url="redis://localhost:56380/1",
        retention_sweep_interval_seconds=900.0,
    )

    app = configure_celery(create_celery_app(), settings)

    entry = app.conf.beat_schedule["retention-sweep"]
    assert entry["task"] == RETENTION_SWEEP_TASK
    assert entry["schedule"] == 900.0
    assert entry["options"]["queue"] == "maintenance"


@pytest.mark.unit
def test_the_sweep_task_is_registered_on_the_worker_application() -> None:
    """A schedule that names a task nothing registered would silently do nothing."""
    from clipah.jobs import tasks as task_module

    assert RETENTION_SWEEP_TASK in task_module.celery_app.tasks


def _attach_publication_graph(engine: Engine, graph: dict[str, UUID]) -> dict[str, UUID]:
    """Attach one connected Social Account and one unpublished Publication to a Project."""
    workspace_id = graph["workspace"]
    ids = {name: uuid4() for name in ("account", "grant", "batch", "publication")}
    with engine.begin() as connection:
        artifact_id = connection.scalar(
            text("SELECT id FROM render_artifacts WHERE workspace_id = :workspace"),
            {"workspace": workspace_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO social_accounts
                    (id, workspace_id, provider, external_account_id, display_name,
                     login_family, api_version, connection_status, capability_snapshot,
                     authorized_by_user_id, created_at)
                VALUES (:account, :workspace, 'youtube', 'external-1', 'Channel',
                        'google', 'v3', 'active', '{}', :user, now())
                """
            ),
            {**ids, "workspace": workspace_id, "user": graph["user"]},
        )
        connection.execute(
            text(
                """
                INSERT INTO oauth_grants
                    (id, workspace_id, social_account_id, key_reference, key_version,
                     wrapped_key, nonce, ciphertext, granted_scopes, token_version,
                     created_at)
                VALUES (:grant, :workspace, :account, 'local', 1,
                        decode(repeat('01', 32), 'hex'), decode(repeat('02', 12), 'hex'),
                        decode(repeat('03', 16), 'hex'),
                        ARRAY['upload'], 1, now())
                """
            ),
            {**ids, "workspace": workspace_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO publication_batches
                    (id, workspace_id, edit_revision_id, render_artifact_id,
                     created_by_user_id, idempotency_key, request_fingerprint, created_at)
                VALUES (:batch, :workspace, :revision, :artifact, :user, 'batch-key',
                        decode(repeat('04', 32), 'hex'), now())
                """
            ),
            {
                **ids,
                "workspace": workspace_id,
                "revision": graph["revision"],
                "artifact": artifact_id,
                "user": graph["user"],
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO publications
                    (id, workspace_id, batch_id, social_account_id, edit_revision_id,
                     render_artifact_id, artifact_sha256, metadata_snapshot,
                     provider_options, consent_snapshot, display_timezone, status,
                     idempotency_key, provider_operation_key, attempt_count, created_at)
                VALUES (:publication, :workspace, :batch, :account, :revision, :artifact,
                        decode(repeat('05', 32), 'hex'), '{}', '{}', '{}', 'UTC',
                        'scheduled', 'publication-key', 'operation-key', 0, now())
                """
            ),
            {
                **ids,
                "workspace": workspace_id,
                "revision": graph["revision"],
                "artifact": artifact_id,
            },
        )
    return ids


@dataclass(frozen=True)
class SignedInOwner:
    """One signed-in owner, with everything a test needs to keep acting as them."""

    browser: Browser
    clock: Clock
    flow: object
    workspace_id: UUID
    user_id: UUID

    def sign_in_again(self) -> None:
        """Authenticate again, as a member returning after their Session lapsed."""
        sign_in(self.browser, self.flow)  # type: ignore[arg-type]


def _signed_in_owner(engine: Engine) -> SignedInOwner:
    """Sign one owner in and return their browser, clock, Workspace, and User."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    user_id = UUID(browser.get("/api/v1/me").json()["id"])
    return SignedInOwner(
        browser=browser, clock=clock, flow=flow, workspace_id=workspace_id, user_id=user_id
    )


@pytest.mark.integration
def test_deleting_a_workspace_hides_it_and_schedules_its_purge(engine: Engine) -> None:
    """Deletion stops the Workspace being usable immediately and its data thirty days later."""
    owner = _signed_in_owner(engine)
    browser, workspace_id = owner.browser, owner.workspace_id

    response = browser.request("DELETE", f"/api/v1/workspaces/{workspace_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["workspaceId"] == str(workspace_id)
    assert body["recoverableUntil"] == (NOW + timedelta(days=30)).isoformat()
    assert body["publishedPostsRemainOnProviders"] is True
    assert browser.get("/api/v1/workspaces").json()["workspaces"] == []
    assert browser.get(f"/api/v1/projects?workspace_id={workspace_id}").status_code == 404
    assert _tombstones(engine, workspace_id, workspace_id) == [NOW + timedelta(days=30)]


@pytest.mark.integration
def test_deleting_a_workspace_revokes_its_connections_and_cancels_unpublished_work(
    engine: Engine,
) -> None:
    """A Workspace nobody can enter must not keep publishing or hold live credentials."""
    owner = _signed_in_owner(engine)
    browser, workspace_id, user_id = owner.browser, owner.workspace_id, owner.user_id
    graph = _provision_project_graph(
        engine,
        suffix=f"ws-{uuid4().hex[:8]}",
        identity=(user_id, workspace_id),
    )
    connection_id = uuid4()
    with Session(engine) as session, session.begin():
        _tenant(session, workspace_id)
        session.execute(
            text(
                """
                INSERT INTO source_connections
                    (id, workspace_id, provider, kind, status, label, domain_scope,
                     secret_reference, authorized_by_user_id, consented_at, expires_at)
                VALUES (:id, :workspace, 'youtube', 'cookie', 'active', 'Studio',
                        ARRAY['.youtube.com'], :reference, :user, :now, :expires)
                """
            ),
            {
                "id": connection_id,
                "workspace": workspace_id,
                "reference": uuid4(),
                "user": user_id,
                "now": NOW,
                "expires": NOW + timedelta(days=7),
            },
        )
    _attach_publication_graph(engine, graph)

    assert browser.request("DELETE", f"/api/v1/workspaces/{workspace_id}").status_code == 200

    with engine.connect() as connection:
        source_status = connection.scalar(
            text("SELECT status FROM source_connections WHERE id = :id"), {"id": connection_id}
        )
        account_status = connection.scalar(
            text("SELECT connection_status FROM social_accounts WHERE workspace_id = :workspace"),
            {"workspace": workspace_id},
        )
        grant = connection.execute(
            text("SELECT ciphertext, revoked_at FROM oauth_grants WHERE workspace_id = :workspace"),
            {"workspace": workspace_id},
        ).one()
        publication_status = connection.scalar(
            text("SELECT status FROM publications WHERE workspace_id = :workspace"),
            {"workspace": workspace_id},
        )
    assert source_status == "revoked"
    assert account_status == "revoked"
    assert bytes(grant.ciphertext) == b""
    assert grant.revoked_at is not None
    assert publication_status == "cancelled"


@pytest.mark.integration
def test_only_an_owner_may_delete_a_workspace(engine: Engine) -> None:
    """Deleting a Workspace is an owner's decision, not an administrator's."""
    owner = _signed_in_owner(engine)
    browser, workspace_id, user_id = owner.browser, owner.workspace_id, owner.user_id
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workspace_memberships SET role = 'admin' "
                "WHERE workspace_id = :workspace AND user_id = :user"
            ),
            {"workspace": workspace_id, "user": user_id},
        )

    assert_error(
        browser.request("DELETE", f"/api/v1/workspaces/{workspace_id}"),
        status_code=403,
        code="FORBIDDEN",
    )


@pytest.mark.integration
def test_deleting_a_workspace_requires_a_recent_authentication(engine: Engine) -> None:
    """An irreversible action needs proof the person at the keyboard is still there."""
    owner = _signed_in_owner(engine)
    browser, clock, workspace_id = owner.browser, owner.clock, owner.workspace_id
    clock.advance(timedelta(minutes=11))

    assert_error(
        browser.request("DELETE", f"/api/v1/workspaces/{workspace_id}"),
        status_code=403,
        code="RECENT_AUTHENTICATION_REQUIRED",
    )


@pytest.mark.integration
def test_a_workspace_can_be_recovered_inside_its_window(engine: Engine) -> None:
    """Thirty days of recovery means an owner can undo the deletion, and the purge with it."""
    owner = _signed_in_owner(engine)
    browser, clock, workspace_id = owner.browser, owner.clock, owner.workspace_id
    browser.request("DELETE", f"/api/v1/workspaces/{workspace_id}")
    clock.advance(timedelta(days=29))
    owner.sign_in_again()

    restored = browser.request("POST", f"/api/v1/workspaces/{workspace_id}/restore")

    assert restored.status_code == 200
    assert restored.json()["status"] == "active"
    assert _tombstones(engine, workspace_id, workspace_id) == []
    assert [entry["id"] for entry in browser.get("/api/v1/workspaces").json()["workspaces"]] == [
        str(workspace_id)
    ]


@pytest.mark.integration
def test_a_workspace_cannot_be_recovered_once_its_window_has_closed(engine: Engine) -> None:
    """After the window the data may already be gone, so recovery is refused rather than faked."""
    owner = _signed_in_owner(engine)
    browser, clock, workspace_id = owner.browser, owner.clock, owner.workspace_id
    browser.request("DELETE", f"/api/v1/workspaces/{workspace_id}")
    clock.advance(timedelta(days=31))
    owner.sign_in_again()

    assert_error(
        browser.request("POST", f"/api/v1/workspaces/{workspace_id}/restore"),
        status_code=409,
        code="CONFLICT",
    )


@pytest.mark.integration
def test_an_account_cannot_be_deleted_while_it_is_a_workspaces_last_owner(
    engine: Engine,
) -> None:
    """Nobody may leave a Workspace with no owner, including by deleting themselves."""
    owner = _signed_in_owner(engine)

    assert_error(
        owner.browser.request("DELETE", "/api/v1/account"),
        status_code=409,
        code="LAST_OWNER",
    )
    assert owner.browser.get("/api/v1/me").status_code == 200


@pytest.mark.integration
def test_deleting_an_account_revokes_its_sessions_and_memberships(engine: Engine) -> None:
    """A deleted account stops working at once, everywhere, rather than at the next sweep."""
    owner = _signed_in_owner(engine)
    owner.browser.request("DELETE", f"/api/v1/workspaces/{owner.workspace_id}")

    deleted = owner.browser.request("DELETE", "/api/v1/account")

    assert deleted.status_code == 204
    assert owner.browser.get("/api/v1/me").status_code == 401
    with engine.connect() as connection:
        user = connection.execute(
            text("SELECT status, deleted_at FROM users WHERE id = :user"),
            {"user": owner.user_id},
        ).one()
        live_sessions = connection.scalar(
            text("SELECT count(*) FROM auth_sessions WHERE user_id = :user AND revoked_at IS NULL"),
            {"user": owner.user_id},
        )
        live_memberships = connection.scalar(
            text(
                "SELECT count(*) FROM workspace_memberships "
                "WHERE user_id = :user AND removed_at IS NULL"
            ),
            {"user": owner.user_id},
        )
    assert user.status == "deleted"
    assert user.deleted_at is not None
    assert live_sessions == 0
    assert live_memberships == 0
    assert _tombstones(engine, owner.workspace_id, owner.user_id) == [NOW + timedelta(days=30)]


@pytest.mark.integration
def test_deleting_an_account_requires_a_recent_authentication(engine: Engine) -> None:
    """Deleting an account is irreversible, so a stale Session may not do it."""
    owner = _signed_in_owner(engine)
    owner.browser.request("DELETE", f"/api/v1/workspaces/{owner.workspace_id}")
    owner.clock.advance(timedelta(minutes=11))

    assert_error(
        owner.browser.request("DELETE", "/api/v1/account"),
        status_code=403,
        code="RECENT_AUTHENTICATION_REQUIRED",
    )


@pytest.mark.integration
def test_a_deleted_account_cannot_sign_in_again(engine: Engine) -> None:
    """Deletion is not a logout: the same Google identity gets no new Session."""
    owner = _signed_in_owner(engine)
    owner.browser.request("DELETE", f"/api/v1/workspaces/{owner.workspace_id}")
    owner.browser.request("DELETE", "/api/v1/account")

    owner.sign_in_again()

    assert owner.browser.get("/api/v1/me").status_code == 401


@pytest.mark.integration
def test_purging_a_user_tombstone_erases_the_identity_behind_it(engine: Engine) -> None:
    """Thirty days later, nothing that identified the person is left to identify them."""
    owner = _signed_in_owner(engine)
    owner.browser.request("DELETE", f"/api/v1/workspaces/{owner.workspace_id}")
    owner.browser.request("DELETE", "/api/v1/account")

    with retention_session_scope(
        settings=retention_settings(),  # type: ignore[arg-type]
        workspace_id=owner.workspace_id,
    ) as session:
        claimed = [
            tombstone
            for tombstone in claim_due_tombstones(session, now=NOW + timedelta(days=30), limit=10)
            if tombstone.entity_kind is RetentionEntityKind.USER
        ]
        outcome = discharge_tombstone(
            session,
            _store(()),
            tombstone=claimed[0],
            policy=_policy(),
            now=NOW + timedelta(days=30),
        )

    with engine.connect() as connection:
        user = connection.execute(
            text("SELECT primary_email, display_name FROM users WHERE id = :user"),
            {"user": owner.user_id},
        ).one()
        identities = connection.scalar(
            text("SELECT count(*) FROM auth_identities WHERE user_id = :user"),
            {"user": owner.user_id},
        )
    assert outcome is DischargeOutcome.PURGED
    assert identities == 0
    assert "@" not in user.primary_email or user.primary_email.endswith("invalid")
    assert user.display_name == "Deleted account"


@pytest.mark.integration
def test_expiring_an_upload_aborts_it_at_the_provider_as_well(engine: Engine) -> None:
    """Deleting the row alone would leave the provider holding parts nobody pays attention to."""
    graph = _provision_project_graph(engine, suffix=f"retain-abort-{uuid4().hex[:8]}")
    workspace_id = graph["workspace"]
    upload_id = _upload(
        engine,
        workspace_id=workspace_id,
        project_id=graph["project"],
        status="uploading",
        age=timedelta(hours=25),
    )
    key = f"workspaces/{workspace_id}/projects/{graph['project']}/source/{upload_id}"
    store = FakeObjectStore(now=lambda: NOW)
    store.upload_keys[f"provider-{upload_id}"] = key
    store.put_file(key=key, content_type="video/mp4", file=BytesIO(b"partial"))

    with retention_session_scope(
        settings=retention_settings(),  # type: ignore[arg-type]
        workspace_id=workspace_id,
    ) as session:
        scan_workspace(session, policy=_policy(), now=NOW)
        claimed = [
            tombstone
            for tombstone in claim_due_tombstones(session, now=NOW, limit=10)
            if tombstone.entity_kind is RetentionEntityKind.MULTIPART_UPLOAD
        ]
        outcome = discharge_tombstone(
            session, store, tombstone=claimed[0], policy=_policy(), now=NOW
        )

    assert outcome is DischargeOutcome.PURGED
    assert store.aborted == [(f"provider-{upload_id}", key)]
    assert store.objects == {}
    assert _row_counts(engine, workspace_id)["multipart_uploads"] == 0
