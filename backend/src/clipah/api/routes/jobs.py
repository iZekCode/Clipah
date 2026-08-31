"""HTTP adapters for durable job state, cancellation, and the live event stream."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from clipah.api.dependencies import (
    AuthComponents,
    CurrentWorkspace,
    DatabaseSession,
    auth_components_for,
    require_csrf,
    require_workspace,
    settings_for,
)
from clipah.api.errors import ApiError
from clipah.config import Settings
from clipah.db import set_actor_context, set_workspace_context
from clipah.jobs.events import JobEventNotifier
from clipah.jobs.models import (
    InvalidJobTransitionError,
    JobEventRecord,
    JobEventType,
    JobNotFoundError,
    JobSnapshot,
)
from clipah.jobs.use_cases import job_events, job_snapshot, request_job_cancellation
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["jobs"])

ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_READ))
]
WritableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_WRITE))
]
LastEventId = Annotated[str | None, Header(alias="Last-Event-ID")]
SSE_MEDIA_TYPE = "text/event-stream"
TERMINAL_EVENTS = frozenset({JobEventType.SUCCEEDED, JobEventType.FAILED, JobEventType.CANCELED})


@router.get("/jobs/{job_id}")
def show(job_id: UUID, session: DatabaseSession, workspace: ReadableWorkspace) -> dict[str, object]:
    """Show one job to a member of the Workspace that paid for it."""
    return _job_body(_load(session, workspace, job_id))


@router.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_csrf)])
def cancel(
    request: Request, job_id: UUID, session: DatabaseSession, workspace: WritableWorkspace
) -> dict[str, object]:
    """Cancel queued work outright, or record the intent a running worker must honour."""
    try:
        snapshot = request_job_cancellation(
            session,
            workspace_id=workspace.access.workspace_id,
            job_id=job_id,
            now=auth_components_for(request).now(),
        )
    except JobNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except InvalidJobTransitionError as error:
        raise ApiError(status_code=409, code="CONFLICT") from error
    return _job_body(snapshot)


@router.get("/jobs/{job_id}/events")
def stream(
    request: Request,
    job_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
    last_event_id: LastEventId = None,
) -> StreamingResponse:
    """Stream one job's durable history, resuming from whatever the client already holds."""
    _load(session, workspace, job_id)
    frames = _event_frames(
        components=auth_components_for(request),
        settings=settings_for(request),
        notifier=job_event_notifier_for(request),
        workspace_id=workspace.access.workspace_id,
        user_id=workspace.user.user_id,
        job_id=job_id,
        after_sequence=_resume_point(last_event_id),
    )
    return StreamingResponse(frames, media_type=SSE_MEDIA_TYPE)


def job_event_notifier_for(request: Request) -> JobEventNotifier:
    """Return the wakeup transport the application was composed with."""
    notifier: JobEventNotifier = request.app.state.job_event_notifier
    return notifier


def _event_frames(
    *,
    components: AuthComponents,
    settings: Settings,
    notifier: JobEventNotifier,
    workspace_id: UUID,
    user_id: UUID,
    job_id: UUID,
    after_sequence: int,
) -> Iterator[str]:
    """Replay recorded history, then follow the job until it reaches a terminal event.

    The subscription is opened before the first read so no event recorded between the
    replay and the first wait can be missed.
    """
    subscription = notifier.subscribe(workspace_id=workspace_id, job_id=job_id)
    heartbeat = settings.job_event_heartbeat_seconds
    quiet_since = time.monotonic()
    try:
        while True:
            events = _read(
                components,
                workspace_id=workspace_id,
                user_id=user_id,
                job_id=job_id,
                after_sequence=after_sequence,
            )
            for event in events:
                after_sequence = event.sequence
                quiet_since = time.monotonic()
                yield _event_frame(event)
                if event.event_type in TERMINAL_EVENTS:
                    return
            if events:
                continue
            subscription.wait(settings.job_event_poll_seconds)
            if time.monotonic() - quiet_since >= heartbeat:
                quiet_since = time.monotonic()
                yield ": heartbeat\n\n"
    finally:
        subscription.close()


def _read(
    components: AuthComponents,
    *,
    workspace_id: UUID,
    user_id: UUID,
    job_id: UUID,
    after_sequence: int,
) -> list[JobEventRecord]:
    """Read new history in its own short transaction, holding none open between polls."""
    with _tenant_session(components, workspace_id=workspace_id, user_id=user_id) as session:
        return job_events(
            session, workspace_id=workspace_id, job_id=job_id, after_sequence=after_sequence
        )


@contextmanager
def _tenant_session(
    components: AuthComponents, *, workspace_id: UUID, user_id: UUID
) -> Iterator[Session]:
    """Open one transaction already bound to the tenant this stream was authorized for."""
    with components.open_session() as session:
        set_actor_context(session, user_id=user_id)
        set_workspace_context(session, workspace_id=workspace_id)
        yield session


def _load(session: Session, workspace: CurrentWorkspace, job_id: UUID) -> JobSnapshot:
    """Read one job, answering a guessed identifier exactly like a missing one."""
    try:
        return job_snapshot(session, workspace_id=workspace.access.workspace_id, job_id=job_id)
    except JobNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error


def _resume_point(last_event_id: str | None) -> int:
    """Read the sequence a reconnecting client already holds, ignoring anything else."""
    if last_event_id is None or not last_event_id.isdigit():
        return 0
    return int(last_event_id)


def _event_frame(event: JobEventRecord) -> str:
    """Render one recorded event as a single Server-Sent Events frame."""
    data = json.dumps({**event.payload, "sequence": event.sequence})
    return f"id: {event.sequence}\nevent: {event.event_type.value}\ndata: {data}\n\n"


def _job_body(job: JobSnapshot) -> dict[str, object]:
    """Render one job into the public camelCase HTTP representation."""
    return {
        "id": str(job.job_id),
        "workspaceId": str(job.workspace_id),
        "projectId": str(job.project_id),
        "kind": job.kind.value,
        "status": job.status.value,
        "stage": job.stage,
        "progress": job.progress,
        "attempt": job.attempt,
        "errorCode": job.error_code,
        "createdAt": job.created_at.isoformat(),
        "updatedAt": job.updated_at.isoformat(),
    }
