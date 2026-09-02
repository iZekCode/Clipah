"""Contract for the one stream a Workspace's global job center subscribes to.

The dashboard keeps a single connection open per Workspace rather than one per Job, so
this stream carries every Job's history in the order it was recorded, resumes from what
the client already holds, and stays open after any one Job finishes.
"""

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
from clipah.jobs.use_cases import create_job, start_job, succeed_job
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

STREAM_PATH = "/api/v1/jobs/events"
STREAM_SETTINGS: dict[str, object] = {
    "job_event_poll_seconds": 0.02,
    "job_event_heartbeat_seconds": 0.05,
}


@pytest.mark.integration
def test_the_workspace_stream_replays_every_job_it_paid_for(
    engine: Engine, clean_database: None
) -> None:
    """A job center that opens after work started must still learn the whole history."""
    del clean_database
    clock = Clock(NOW)
    browser, workspace_id = _signed_in_workspace(clock)
    user_id = _owner_of(engine, workspace_id)
    first = _finished_job(workspace_id, user_id, clock, key="workspace-first")
    second = _queued_job(workspace_id, user_id, clock, key="workspace-second")

    frames = browser.stream(f"{STREAM_PATH}?workspace_id={workspace_id}", limit=4)

    assert [frame.event for frame in frames] == [
        JobEventType.CREATED,
        JobEventType.STARTED,
        JobEventType.SUCCEEDED,
        JobEventType.CREATED,
    ]
    assert [json.loads(frame.data)["jobId"] for frame in frames] == [
        str(first),
        str(first),
        str(first),
        str(second),
    ]


@pytest.mark.integration
def test_the_workspace_stream_stays_open_after_one_job_finishes(
    engine: Engine, clean_database: None
) -> None:
    """One finished Job says nothing about the rest of the Workspace, so the stream lives on."""
    del clean_database
    clock = Clock(NOW)
    browser, workspace_id = _signed_in_workspace(clock)
    user_id = _owner_of(engine, workspace_id)
    _finished_job(workspace_id, user_id, clock, key="stays-open")

    frames = browser.stream(f"{STREAM_PATH}?workspace_id={workspace_id}", limit=5, comments=True)

    assert [frame.event for frame in frames[:3]] == [
        JobEventType.CREATED,
        JobEventType.STARTED,
        JobEventType.SUCCEEDED,
    ]
    assert [frame.comment for frame in frames[3:]] == ["heartbeat", "heartbeat"]


@pytest.mark.integration
def test_a_reconnect_resumes_after_the_last_delivered_workspace_event(
    engine: Engine, clean_database: None
) -> None:
    """`Last-Event-ID` is what keeps a reconnecting job center from replaying or skipping."""
    del clean_database
    clock = Clock(NOW)
    browser, workspace_id = _signed_in_workspace(clock)
    user_id = _owner_of(engine, workspace_id)
    job_id = _finished_job(workspace_id, user_id, clock, key="workspace-resume")

    first_read = browser.stream(f"{STREAM_PATH}?workspace_id={workspace_id}", limit=2)
    resumed = browser.stream(
        f"{STREAM_PATH}?workspace_id={workspace_id}",
        headers={"Last-Event-ID": first_read[-1].id or ""},
        limit=1,
    )

    assert [frame.event for frame in resumed] == [JobEventType.SUCCEEDED]
    assert json.loads(resumed[0].data)["jobId"] == str(job_id)


@pytest.mark.integration
def test_an_unreadable_resume_point_replays_the_whole_workspace_history(
    engine: Engine, clean_database: None
) -> None:
    """A corrupted or forged resume point must degrade to a full replay, never to a crash."""
    del clean_database
    clock = Clock(NOW)
    browser, workspace_id = _signed_in_workspace(clock)
    user_id = _owner_of(engine, workspace_id)
    _queued_job(workspace_id, user_id, clock, key="workspace-garbage")

    frames = browser.stream(
        f"{STREAM_PATH}?workspace_id={workspace_id}",
        headers={"Last-Event-ID": "not-a-cursor"},
        limit=1,
    )

    assert [frame.event for frame in frames] == [JobEventType.CREATED]


@pytest.mark.integration
def test_the_workspace_stream_never_carries_another_workspaces_jobs(
    engine: Engine, clean_database: None
) -> None:
    """One connection per Workspace only stays safe while it is scoped to that Workspace."""
    del clean_database
    clock = Clock(NOW)
    browser, workspace_id = _signed_in_workspace(clock)
    user_id = _owner_of(engine, workspace_id)
    mine = _queued_job(workspace_id, user_id, clock, key="workspace-mine")
    stranger_user, stranger_workspace = provision_identity(
        engine, suffix=f"wsstream-{uuid4().hex[:8]}"
    )
    theirs = _queued_job(stranger_workspace, stranger_user, clock, key="workspace-theirs")

    frames = browser.stream(f"{STREAM_PATH}?workspace_id={workspace_id}", limit=1)

    job_ids = {json.loads(frame.data)["jobId"] for frame in frames}
    assert job_ids == {str(mine)}
    assert str(theirs) not in job_ids


@pytest.mark.integration
def test_a_workspace_stream_without_membership_is_refused_like_a_missing_workspace(
    engine: Engine, clean_database: None
) -> None:
    """Opening a stream must not tell a caller that someone else's Workspace exists."""
    del clean_database
    clock = Clock(NOW)
    browser, _ = _signed_in_workspace(clock)
    _, stranger_workspace = provision_identity(engine, suffix=f"wsstream-{uuid4().hex[:8]}")

    known = browser.get(f"{STREAM_PATH}?workspace_id={stranger_workspace}")
    unknown = browser.get(f"{STREAM_PATH}?workspace_id={uuid4()}")

    assert known.status_code == unknown.status_code == 404
    assert known.json()["error"]["code"] == unknown.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.integration
def test_an_anonymous_visitor_cannot_open_a_workspace_job_stream(
    engine: Engine, clean_database: None
) -> None:
    """Every Job in a Workspace is private data, and so is the feed announcing them."""
    del clean_database
    clock = Clock(NOW)
    browser, workspace_id = _signed_in_workspace(clock)
    anonymous = Browser(browser.app)

    response = anonymous.get(f"{STREAM_PATH}?workspace_id={workspace_id}")

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


def _finished_job(workspace_id: UUID, user_id: UUID, clock: Clock, *, key: str) -> UUID:
    """Run one Job all the way to success so its whole history already exists."""
    job_id = _queued_job(workspace_id, user_id, clock, key=key)
    with _worker_session(workspace_id, user_id) as session:
        start_job(session, workspace_id=workspace_id, job_id=job_id, now=clock())
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
