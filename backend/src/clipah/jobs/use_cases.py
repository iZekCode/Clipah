"""The durable job state machine, written once for the API and the worker alike."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.jobs.admission import AdmissionPolicy, QuotaLedger, admit_job
from clipah.jobs.models import (
    EVENT_FOR_STATUS,
    InvalidJobTransitionError,
    JobEventRecord,
    JobEventType,
    JobSnapshot,
    assert_transition,
)
from clipah.jobs.repository import JobRepository, snapshot_of
from clipah.models import Job, JobKind, JobStatus, Project, ProjectStatus, QuotaResource
from clipah.workspaces.models import WorkspaceAccess

SUCCESS_PROGRESS = 1.0


def create_job(
    session: Session,
    *,
    policy: AdmissionPolicy,
    access: WorkspaceAccess,
    project_id: UUID,
    kind: JobKind,
    idempotency_key: str,
    now: datetime,
    estimated_units: Decimal = Decimal(1),
) -> JobSnapshot:
    """Admit one unit of paid work, record it durably, and announce that it exists."""
    repository = JobRepository(session)
    existing = repository.find_by_idempotency_key(
        workspace_id=access.workspace_id, idempotency_key=idempotency_key
    )
    if existing is not None:
        return snapshot_of(existing)

    job = admit_job(
        session,
        policy=policy,
        workspace_id=access.workspace_id,
        project_id=project_id,
        user_id=access.user_id,
        kind=kind,
        idempotency_key=idempotency_key,
        now=now,
        estimated_units=estimated_units,
    )
    repository.append_event(job, event_type=JobEventType.CREATED)
    return snapshot_of(job)


def start_job(session: Session, *, workspace_id: UUID, job_id: UUID, now: datetime) -> JobSnapshot:
    """Claim one queued or retrying job for a worker and count the attempt it is spending."""
    repository = JobRepository(session)
    job = repository.lock(workspace_id=workspace_id, job_id=job_id)
    assert_transition(job.status, JobStatus.RUNNING)
    job.status = JobStatus.RUNNING
    job.attempt += 1
    job.error_code = None
    job.error_message = None
    if job.started_at is None:
        job.started_at = now
    return _record(repository, job, JobEventType.STARTED)


def update_job_progress(
    session: Session,
    *,
    workspace_id: UUID,
    job_id: UUID,
    stage: str,
    progress: float,
    now: datetime,
    detail: Mapping[str, Any] | None = None,
) -> JobSnapshot:
    """Record honest progress, with any detail a reader needs to understand the stage."""
    del now
    repository = JobRepository(session)
    job = repository.lock(workspace_id=workspace_id, job_id=job_id)
    if job.status is not JobStatus.RUNNING:
        raise InvalidJobTransitionError(f"{job.status.value} reports no progress")
    job.stage = stage
    job.progress = progress
    repository.append_event(job, event_type=JobEventType.PROGRESS, payload=dict(detail or {}))
    return snapshot_of(job)


def succeed_job(
    session: Session, *, workspace_id: UUID, job_id: UUID, now: datetime
) -> JobSnapshot:
    """Finish one job successfully, exactly once."""
    return _finish(
        session, workspace_id=workspace_id, job_id=job_id, target=JobStatus.SUCCEEDED, now=now
    )


def fail_job(
    session: Session,
    *,
    workspace_id: UUID,
    job_id: UUID,
    error_code: str,
    retryable: bool,
    now: datetime,
) -> JobSnapshot:
    """End one attempt, leaving the job recoverable only when the failure was transient."""
    repository = JobRepository(session)
    job = repository.lock(workspace_id=workspace_id, job_id=job_id)
    target = JobStatus.RETRYING if retryable else JobStatus.FAILED
    assert_transition(job.status, target)
    job.status = target
    job.error_code = error_code
    if target is JobStatus.FAILED:
        job.finished_at = now
        _reconcile_analysis_terminal(session, job=job, target=target, now=now)
    return _record(repository, job, EVENT_FOR_STATUS[target])


def request_job_cancellation(
    session: Session, *, workspace_id: UUID, job_id: UUID, now: datetime
) -> JobSnapshot:
    """Cancel work nobody has started, or record the intent a worker must honour."""
    repository = JobRepository(session)
    job = repository.lock(workspace_id=workspace_id, job_id=job_id)
    target = JobStatus.CANCEL_REQUESTED if job.status is JobStatus.RUNNING else JobStatus.CANCELED
    assert_transition(job.status, target)
    job.status = target
    job.cancel_requested_at = now
    if target is JobStatus.CANCELED:
        job.finished_at = now
        _reconcile_analysis_terminal(session, job=job, target=target, now=now)
    return _record(repository, job, EVENT_FOR_STATUS[target])


def cancel_job(session: Session, *, workspace_id: UUID, job_id: UUID, now: datetime) -> JobSnapshot:
    """End one running job that has already been asked to stop."""
    return _finish(
        session, workspace_id=workspace_id, job_id=job_id, target=JobStatus.CANCELED, now=now
    )


def complete_job_after_runner(
    session: Session, *, workspace_id: UUID, job_id: UUID, now: datetime
) -> JobSnapshot:
    """Resolve success or a cancellation that arrived while the stage runner finished."""
    job = JobRepository(session).lock(workspace_id=workspace_id, job_id=job_id)
    if job.status is JobStatus.CANCEL_REQUESTED:
        return cancel_job(session, workspace_id=workspace_id, job_id=job_id, now=now)
    if job.status is JobStatus.SUCCEEDED:
        return snapshot_of(job)
    return succeed_job(session, workspace_id=workspace_id, job_id=job_id, now=now)


def job_snapshot(session: Session, *, workspace_id: UUID, job_id: UUID) -> JobSnapshot:
    """Read one job of this Workspace, or refuse to admit that it exists."""
    return snapshot_of(JobRepository(session).load(workspace_id=workspace_id, job_id=job_id))


def job_events(
    session: Session, *, workspace_id: UUID, job_id: UUID, after_sequence: int = 0
) -> list[JobEventRecord]:
    """Replay one job's durable history from the sequence a subscriber already holds."""
    return JobRepository(session).events(
        workspace_id=workspace_id, job_id=job_id, after_sequence=after_sequence
    )


def _finish(
    session: Session, *, workspace_id: UUID, job_id: UUID, target: JobStatus, now: datetime
) -> JobSnapshot:
    """Move one job into a terminal state and stamp the instant it ended."""
    repository = JobRepository(session)
    job = repository.lock(workspace_id=workspace_id, job_id=job_id)
    assert_transition(job.status, target)
    job.status = target
    job.finished_at = now
    if target is JobStatus.SUCCEEDED:
        job.progress = SUCCESS_PROGRESS
    _reconcile_analysis_terminal(session, job=job, target=target, now=now)
    return _record(repository, job, EVENT_FOR_STATUS[target])


def _reconcile_analysis_terminal(
    session: Session, *, job: Job, target: JobStatus, now: datetime
) -> None:
    """Settle successful analysis quota or release it after failure and cancellation."""
    if job.kind is not JobKind.ANALYZE:
        return
    ledger = QuotaLedger(session, limits={})
    if target is JobStatus.SUCCEEDED:
        ledger.settle(
            workspace_id=job.workspace_id,
            resource=QuotaResource.ANALYSES,
            reference_kind="job",
            reference_id=job.id,
            actual_units=Decimal(1),
            now=now,
        )
        return
    if target not in {JobStatus.FAILED, JobStatus.CANCELED}:
        return
    ledger.release(
        workspace_id=job.workspace_id,
        resource=QuotaResource.ANALYSES,
        reference_kind="job",
        reference_id=job.id,
        now=now,
    )
    project = session.scalar(
        select(Project).where(
            Project.workspace_id == job.workspace_id,
            Project.id == job.project_id,
        )
    )
    if project is not None:
        project.status = ProjectStatus.FAILED


def _record(repository: JobRepository, job: Job, event_type: JobEventType) -> JobSnapshot:
    """Flush one transition and append the single event that announces it."""
    repository.append_event(job, event_type=event_type)
    return snapshot_of(job)
