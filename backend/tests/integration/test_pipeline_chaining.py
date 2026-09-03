"""Integration contracts for the conveyor belt between durable pipeline stages.

Every stage runner already worked in isolation, and nothing joined them: no code created
an INGEST or a TRANSCRIBE Job, and no Project ever reached the `transcribing` status that
analysis admission requires. These tests describe the belt.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from clipah.assets.storage import FakeObjectStore
from clipah.db import RuntimeRole, session_scope
from clipah.jobs.admission import admission_policy
from clipah.jobs.pipeline import advance_after, pipeline_key, start_stage
from clipah.jobs.use_cases import create_job, start_job, succeed_job
from clipah.models import Job, JobKind, Project, ProjectStatus, SourceKind, WorkspaceMembership
from clipah.workspaces.authorization import DatabaseWorkspaceAuthorizer
from harness import NOW, Browser, Clock, StubGoogleProvider, build_app, sign_in
from support import provision_identity, runtime_settings


@pytest.mark.integration
@pytest.mark.parametrize(
    ("finished", "expected", "status"),
    [
        (JobKind.SOURCE_IMPORT, JobKind.INGEST, ProjectStatus.INGESTING),
        (JobKind.INGEST, JobKind.TRANSCRIBE, ProjectStatus.TRANSCRIBING),
        (JobKind.TRANSCRIBE, JobKind.ANALYZE, ProjectStatus.ANALYZING),
    ],
)
def test_a_finished_stage_starts_the_next_one_and_moves_the_project_into_it(
    engine: Engine, finished: JobKind, expected: JobKind, status: ProjectStatus
) -> None:
    """A Project must arrive at the status the next stage's admission already demands."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="chain")
    completed = _finished_job(workspace_id, user_id, project_id, kind=finished)

    with _worker_session(workspace_id, user_id) as session:
        following = advance_after(
            session,
            policy=admission_policy(runtime_settings()),
            access=_access(session, user_id=user_id, workspace_id=workspace_id),
            project_id=project_id,
            completed_kind=finished,
            completed_job_id=completed,
            now=NOW,
        )
        assert following is not None
        assert following.kind is expected

    assert _project_status(workspace_id, user_id, project_id) is status
    assert _job_kinds(workspace_id, user_id, project_id).count(expected) == 1


@pytest.mark.integration
def test_the_last_stage_of_the_pipeline_starts_nothing(engine: Engine) -> None:
    """Analysis is the end of the belt; inventing work after it would bill for nothing."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="chain-end")
    completed = _finished_job(workspace_id, user_id, project_id, kind=JobKind.ANALYZE)

    with _worker_session(workspace_id, user_id) as session:
        following = advance_after(
            session,
            policy=admission_policy(runtime_settings()),
            access=_access(session, user_id=user_id, workspace_id=workspace_id),
            project_id=project_id,
            completed_kind=JobKind.ANALYZE,
            completed_job_id=completed,
            now=NOW,
        )

    assert following is None
    assert _job_kinds(workspace_id, user_id, project_id) == [JobKind.ANALYZE]


@pytest.mark.integration
def test_advancing_the_same_finished_job_twice_starts_one_successor(engine: Engine) -> None:
    """A replayed completion must not buy a Workspace the same stage twice."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="chain-replay")
    completed = _finished_job(workspace_id, user_id, project_id, kind=JobKind.INGEST)

    first = _advance(workspace_id, user_id, project_id, completed, JobKind.INGEST)
    second = _advance(workspace_id, user_id, project_id, completed, JobKind.INGEST)

    assert first is not None
    assert second is not None
    assert first == second
    assert _job_kinds(workspace_id, user_id, project_id).count(JobKind.TRANSCRIBE) == 1


@pytest.mark.integration
def test_the_pipeline_key_names_the_job_it_follows_and_the_stage_it_starts() -> None:
    """Two different completions must never collide on one idempotency binding."""
    left, right = uuid4(), uuid4()

    assert pipeline_key(after_job_id=left, kind=JobKind.INGEST) != pipeline_key(
        after_job_id=right, kind=JobKind.INGEST
    )
    assert pipeline_key(after_job_id=left, kind=JobKind.INGEST) != pipeline_key(
        after_job_id=left, kind=JobKind.TRANSCRIBE
    )


@pytest.mark.integration
def test_a_deleted_project_is_never_carried_further_down_the_pipeline(engine: Engine) -> None:
    """Work already in flight when a Project is deleted must stop, not resume it."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="chain-deleted")
    completed = _finished_job(workspace_id, user_id, project_id, kind=JobKind.INGEST)
    with _api_session(workspace_id, user_id) as session:
        session.execute(update(Project).where(Project.id == project_id).values(archived_at=NOW))

    following = _advance(workspace_id, user_id, project_id, completed, JobKind.INGEST)

    assert following is None
    assert _job_kinds(workspace_id, user_id, project_id) == [JobKind.INGEST]


@pytest.mark.integration
def test_a_failed_project_is_not_revived_by_a_late_completion(engine: Engine) -> None:
    """A Project that already failed stays failed, whatever finishes afterwards."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="chain-failed")
    completed = _finished_job(workspace_id, user_id, project_id, kind=JobKind.INGEST)
    with _api_session(workspace_id, user_id) as session:
        session.execute(
            update(Project).where(Project.id == project_id).values(status=ProjectStatus.FAILED)
        )

    following = _advance(workspace_id, user_id, project_id, completed, JobKind.INGEST)

    assert following is None
    assert _project_status(workspace_id, user_id, project_id) is ProjectStatus.FAILED


@pytest.mark.integration
def test_starting_one_stage_twice_under_one_key_admits_it_once(engine: Engine) -> None:
    """The belt is built on the same idempotency binding every other Job creation uses."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="chain-once")

    identifiers = []
    for _ in range(2):
        with _worker_session(workspace_id, user_id) as session:
            snapshot = start_stage(
                session,
                policy=admission_policy(runtime_settings()),
                access=_access(session, user_id=user_id, workspace_id=workspace_id),
                project_id=project_id,
                kind=JobKind.INGEST,
                idempotency_key="pipeline:test:ingest",
                now=NOW,
            )
            identifiers.append(snapshot.job_id)

    assert identifiers[0] == identifiers[1]
    assert _job_kinds(workspace_id, user_id, project_id) == [JobKind.INGEST]


@pytest.mark.integration
def test_completing_an_upload_puts_the_project_on_the_belt(engine: Engine) -> None:
    """Finishing an upload is what starts ingest; nothing else ever will."""
    clock = Clock(NOW)
    store = FakeObjectStore(now=clock)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), object_store=store)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    project_id = _project_through_api(browser, workspace_id)

    upload_id = _upload_through_api(browser, store, workspace_id, project_id)

    assert upload_id is not None
    user_id = _owner_of(engine, workspace_id)
    assert _job_kinds(workspace_id, user_id, project_id) == [JobKind.INGEST]
    assert _project_status(workspace_id, user_id, project_id) is ProjectStatus.INGESTING


def _advance(
    workspace_id: UUID, user_id: UUID, project_id: UUID, completed: UUID, kind: JobKind
) -> UUID | None:
    """Run one advance in its own worker transaction and report the Job it started."""
    with _worker_session(workspace_id, user_id) as session:
        following = advance_after(
            session,
            policy=admission_policy(runtime_settings()),
            access=_access(session, user_id=user_id, workspace_id=workspace_id),
            project_id=project_id,
            completed_kind=kind,
            completed_job_id=completed,
            now=NOW,
        )
        return None if following is None else following.job_id


def _finished_job(workspace_id: UUID, user_id: UUID, project_id: UUID, *, kind: JobKind) -> UUID:
    """Create one Job and drive it to success, as a worker would have."""
    with _worker_session(workspace_id, user_id) as session:
        snapshot = create_job(
            session,
            policy=admission_policy(runtime_settings()),
            access=_access(session, user_id=user_id, workspace_id=workspace_id),
            project_id=project_id,
            kind=kind,
            idempotency_key=f"finished-{kind.value}-{uuid4().hex[:8]}",
            now=NOW,
        )
        start_job(session, workspace_id=workspace_id, job_id=snapshot.job_id, now=NOW)
        succeed_job(session, workspace_id=workspace_id, job_id=snapshot.job_id, now=NOW)
        return snapshot.job_id


def _access(session: Session, *, user_id: UUID, workspace_id: UUID) -> object:
    """Read the standing a worker proved before it started this job."""
    return DatabaseWorkspaceAuthorizer(session).access_for(
        user_id=user_id, workspace_id=workspace_id
    )


def _project_through_api(browser: Browser, workspace_id: UUID) -> UUID:
    """Create one Project the way the product does."""
    created = browser.request(
        "POST",
        f"/api/v1/projects?workspace_id={workspace_id}",
        headers={"Idempotency-Key": f"pipeline-{uuid4().hex[:8]}"},
        json={"name": "Belt", "sourceKind": SourceKind.UPLOAD.value},
    )
    assert created.status_code == 201
    return UUID(created.json()["id"])


def _upload_through_api(
    browser: Browser, store: FakeObjectStore, workspace_id: UUID, project_id: UUID
) -> UUID:
    """Run one whole multipart upload to completion through the public routes."""
    created = browser.request(
        "POST",
        f"/api/v1/projects/{project_id}/uploads?workspace_id={workspace_id}",
        json={"filename": "episode.mp4", "contentType": "video/mp4", "contentLength": 8},
    )
    assert created.status_code == 201
    upload_id = UUID(created.json()["id"])

    signed = browser.request(
        "POST",
        f"/api/v1/projects/{project_id}/uploads/{upload_id}/parts/1?workspace_id={workspace_id}",
    )
    assert signed.status_code == 200
    storage_upload_id = next(iter(store.upload_keys))
    store.put_multipart_part(upload_id=storage_upload_id, part_number=1, size_bytes=8)

    completed = browser.request(
        "POST",
        f"/api/v1/projects/{project_id}/uploads/{upload_id}/complete?workspace_id={workspace_id}",
        json={"parts": [{"partNumber": 1, "etag": "fake-etag-1"}]},
    )
    assert completed.status_code == 200
    return upload_id


def _project_in(workspace_id: UUID, user_id: UUID) -> UUID:
    """Create one active Project the pipeline can carry."""
    project_id = uuid4()
    with _api_session(workspace_id, user_id) as session:
        session.add(
            Project(
                id=project_id,
                workspace_id=workspace_id,
                created_by_user_id=user_id,
                name="Belt",
                source_kind=SourceKind.UPLOAD,
            )
        )
    return project_id


def _workspace_with_project(engine: Engine, *, suffix: str) -> tuple[UUID, UUID, UUID]:
    """Provision one Workspace owner and an active Project stages can belong to."""
    user_id, workspace_id = provision_identity(engine, suffix=f"{suffix}-{uuid4().hex[:8]}")
    return user_id, workspace_id, _project_in(workspace_id, user_id)


def _owner_of(engine: Engine, workspace_id: UUID) -> UUID:
    """Return the owner of one Workspace created through the login ceremony."""
    with Session(engine) as session:
        return session.scalars(
            select(WorkspaceMembership.user_id).where(
                WorkspaceMembership.workspace_id == workspace_id
            )
        ).one()


def _project_status(workspace_id: UUID, user_id: UUID, project_id: UUID) -> ProjectStatus:
    """Read one Project's current status inside its own tenant context."""
    with _api_session(workspace_id, user_id) as session:
        status = session.scalar(select(Project.status).where(Project.id == project_id))
        assert status is not None
        return status


def _job_kinds(workspace_id: UUID, user_id: UUID, project_id: UUID) -> list[JobKind]:
    """List every Job recorded for one Project, oldest first."""
    with _api_session(workspace_id, user_id) as session:
        return list(
            session.scalars(
                select(Job.kind).where(Job.project_id == project_id).order_by(Job.created_at)
            )
        )


def _api_session(workspace_id: UUID, user_id: UUID) -> AbstractContextManager[Session]:
    """Open one API-role transaction already holding this tenant's row context."""
    return session_scope(settings=runtime_settings(), workspace_id=workspace_id, user_id=user_id)


def _worker_session(workspace_id: UUID, user_id: UUID) -> AbstractContextManager[Session]:
    """Open one worker-role transaction already holding this tenant's row context."""
    return session_scope(
        settings=runtime_settings(RuntimeRole.WORKER),
        workspace_id=workspace_id,
        user_id=user_id,
        runtime_role=RuntimeRole.WORKER,
    )


assert isinstance(NOW, datetime)
