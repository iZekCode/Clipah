"""Contract for the Workspace-scoped job event stream other tasks build clients against."""

from __future__ import annotations

import json
from contextlib import AbstractContextManager
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from clipah.db import RuntimeRole, session_scope
from clipah.jobs.admission import admission_policy
from clipah.jobs.models import JobEventType
from clipah.jobs.use_cases import create_job, start_job, succeed_job, update_job_progress
from clipah.models import (
    JobKind,
    Project,
    PublishingRolePolicy,
    SourceKind,
    WorkspaceMembership,
    WorkspaceRole,
)
from clipah.workspaces.models import WorkspaceAccess
from harness import NOW, Browser, Clock, StubGoogleProvider, build_app, sign_in
from support import provision_identity, runtime_settings

STREAM_SETTINGS: dict[str, object] = {
    "job_event_poll_seconds": 0.02,
    "job_event_heartbeat_seconds": 0.05,
}


@pytest.mark.integration
def test_the_stream_replays_every_recorded_event_in_sequence_order(
    engine: Engine, clean_database: None
) -> None:
    """A client that connects late must still learn everything that already happened."""
    del clean_database
    clock = Clock(NOW)
    browser, workspace_id = _signed_in_workspace(clock)
    user_id = _owner_of(engine, workspace_id)
    job_id = _job_with_history(workspace_id, user_id, clock, key="replay")

    frames = browser.stream(f"/api/v1/jobs/{job_id}/events?workspace_id={workspace_id}")

    assert [frame.event for frame in frames] == [
        JobEventType.CREATED,
        JobEventType.STARTED,
        JobEventType.PROGRESS,
        JobEventType.SUCCEEDED,
    ]
    assert [frame.id for frame in frames] == ["1", "2", "3", "4"]
    assert json.loads(frames[-1].data)["status"] == "succeeded"


@pytest.mark.integration
def test_a_reconnect_resumes_after_the_last_delivered_event(
    engine: Engine, clean_database: None
) -> None:
    """`Last-Event-ID` exists so a dropped connection never replays or skips work."""
    del clean_database
    clock = Clock(NOW)
    browser, workspace_id = _signed_in_workspace(clock)
    user_id = _owner_of(engine, workspace_id)
    job_id = _job_with_history(workspace_id, user_id, clock, key="resume")

    frames = browser.stream(
        f"/api/v1/jobs/{job_id}/events?workspace_id={workspace_id}",
        headers={"Last-Event-ID": "2"},
    )

    assert [frame.event for frame in frames] == [JobEventType.PROGRESS, JobEventType.SUCCEEDED]
    assert [frame.id for frame in frames] == ["3", "4"]


@pytest.mark.integration
def test_the_stream_sends_heartbeats_while_a_job_is_quiet(
    engine: Engine, clean_database: None
) -> None:
    """A proxy will drop an idle connection, so a quiet job must still produce traffic."""
    del clean_database
    clock = Clock(NOW)
    browser, workspace_id = _signed_in_workspace(clock)
    user_id = _owner_of(engine, workspace_id)
    job_id = _queued_job(workspace_id, user_id, clock, key="heartbeat")

    frames = browser.stream(
        f"/api/v1/jobs/{job_id}/events?workspace_id={workspace_id}", limit=3, comments=True
    )

    assert [frame.event for frame in frames[:1]] == [JobEventType.CREATED]
    assert [frame.comment for frame in frames[1:]] == ["heartbeat", "heartbeat"]


@pytest.mark.integration
def test_the_stream_closes_once_the_job_reaches_a_terminal_event(
    engine: Engine, clean_database: None
) -> None:
    """A finished job has nothing more to say, and clients must not hold the connection."""
    del clean_database
    clock = Clock(NOW)
    browser, workspace_id = _signed_in_workspace(clock)
    user_id = _owner_of(engine, workspace_id)
    job_id = _job_with_history(workspace_id, user_id, clock, key="closes")

    frames = browser.stream(f"/api/v1/jobs/{job_id}/events?workspace_id={workspace_id}")

    assert frames[-1].event == JobEventType.SUCCEEDED


@pytest.mark.integration
def test_another_workspaces_job_stream_is_refused_like_a_missing_job(
    engine: Engine, clean_database: None
) -> None:
    """The stream is a read of tenant data and obeys the same indistinguishable 404."""
    del clean_database
    clock = Clock(NOW)
    browser, workspace_id = _signed_in_workspace(clock)
    stranger_user, stranger_workspace = provision_identity(
        engine, suffix=f"stream-stranger-{uuid4().hex[:8]}"
    )
    foreign_job = _queued_job(stranger_workspace, stranger_user, clock, key="foreign")

    response = browser.get(f"/api/v1/jobs/{foreign_job}/events?workspace_id={workspace_id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.integration
def test_an_anonymous_visitor_cannot_open_a_job_stream(
    engine: Engine, clean_database: None
) -> None:
    """Job progress is private Workspace data, not a public feed."""
    del clean_database
    clock = Clock(NOW)
    browser, workspace_id = _signed_in_workspace(clock)
    user_id = _owner_of(engine, workspace_id)
    job_id = _queued_job(workspace_id, user_id, clock, key="anonymous")
    anonymous = Browser(browser.app)

    response = anonymous.get(f"/api/v1/jobs/{job_id}/events?workspace_id={workspace_id}")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def _signed_in_workspace(clock: Clock) -> tuple[Browser, UUID]:
    """Drive one login ceremony against an application tuned for fast stream polling."""
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), **STREAM_SETTINGS)
    browser = Browser(app)
    sign_in(browser, flow)
    return browser, UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])


def _owner_of(engine: Engine, workspace_id: UUID) -> UUID:
    """Return the owner of one Workspace created through the login ceremony."""
    with Session(engine) as session:
        return session.scalars(
            select(WorkspaceMembership.user_id).where(
                WorkspaceMembership.workspace_id == workspace_id
            )
        ).one()


def _queued_job(workspace_id: UUID, user_id: UUID, clock: Clock, *, key: str) -> UUID:
    """Create one admitted, queued Job with a Project of its own."""
    project_id = uuid4()
    with _api_session(workspace_id, user_id) as session:
        session.add(
            Project(
                id=project_id,
                workspace_id=workspace_id,
                created_by_user_id=user_id,
                name="Stream",
                source_kind=SourceKind.UPLOAD,
            )
        )
        session.flush()
        return create_job(
            session,
            policy=admission_policy(runtime_settings()),
            access=WorkspaceAccess(
                workspace_id=workspace_id,
                user_id=user_id,
                role=WorkspaceRole.OWNER,
                publishing_role_policy=PublishingRolePolicy.OWNER_ADMIN_EDITOR,
            ),
            project_id=project_id,
            kind=JobKind.INGEST,
            idempotency_key=key,
            now=clock(),
        ).job_id


def _job_with_history(workspace_id: UUID, user_id: UUID, clock: Clock, *, key: str) -> UUID:
    """Run one job all the way to success so its whole event history exists."""
    job_id = _queued_job(workspace_id, user_id, clock, key=key)
    with _worker_session(workspace_id, user_id) as session:
        start_job(session, workspace_id=workspace_id, job_id=job_id, now=clock())
        update_job_progress(
            session,
            workspace_id=workspace_id,
            job_id=job_id,
            stage="probing",
            progress=0.5,
            now=clock(),
        )
        succeed_job(session, workspace_id=workspace_id, job_id=job_id, now=clock())
    return job_id


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
