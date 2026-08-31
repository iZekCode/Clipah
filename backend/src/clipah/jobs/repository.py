"""Row-locked reads and append-only writes for durable jobs and their events."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from clipah.jobs.models import JobEventRecord, JobEventType, JobNotFoundError, JobSnapshot
from clipah.models import Job, JobEvent


class JobRepository:
    """Read and write one Workspace's jobs inside the caller's open transaction."""

    def __init__(self, session: Session) -> None:
        """Bind the repository to one transaction that already holds tenant context."""
        self._session = session

    def load(self, *, workspace_id: UUID, job_id: UUID) -> Job:
        """Return one job of this Workspace, or refuse to admit that it exists."""
        job = self._session.scalar(
            select(Job).where(Job.workspace_id == workspace_id, Job.id == job_id)
        )
        if job is None:
            raise JobNotFoundError(str(job_id))
        return job

    def lock(self, *, workspace_id: UUID, job_id: UUID) -> Job:
        """Return one job with its row locked so no second writer can transition it."""
        job = self._session.scalar(
            select(Job).where(Job.workspace_id == workspace_id, Job.id == job_id).with_for_update()
        )
        if job is None:
            raise JobNotFoundError(str(job_id))
        return job

    def find_by_idempotency_key(self, *, workspace_id: UUID, idempotency_key: str) -> Job | None:
        """Return the job an earlier identical submission already created, if any."""
        return self._session.scalar(
            select(Job).where(
                Job.workspace_id == workspace_id, Job.idempotency_key == idempotency_key
            )
        )

    def append_event(
        self,
        job: Job,
        *,
        event_type: JobEventType,
        payload: dict[str, Any] | None = None,
    ) -> JobEvent:
        """Append one immutable history entry after the last sequence this job holds.

        Callers hold the job's row lock, so the next sequence cannot be handed out twice.
        """
        last = self._session.scalar(
            select(func.coalesce(func.max(JobEvent.sequence), 0)).where(
                JobEvent.workspace_id == job.workspace_id, JobEvent.job_id == job.id
            )
        )
        event = JobEvent(
            id=uuid4(),
            workspace_id=job.workspace_id,
            job_id=job.id,
            sequence=(last or 0) + 1,
            event_type=event_type.value,
            payload={**_job_payload(job), **(payload or {})},
        )
        self._session.add(event)
        self._session.flush()
        return event

    def events(
        self, *, workspace_id: UUID, job_id: UUID, after_sequence: int = 0
    ) -> list[JobEventRecord]:
        """Replay this job's history in the order it was recorded."""
        events = self._session.scalars(
            select(JobEvent)
            .where(
                JobEvent.workspace_id == workspace_id,
                JobEvent.job_id == job_id,
                JobEvent.sequence > after_sequence,
            )
            .order_by(JobEvent.sequence)
        ).all()
        return [_event_record(event) for event in events]


def snapshot_of(job: Job) -> JobSnapshot:
    """Copy one job row into the immutable value every caller outside the ORM reads."""
    return JobSnapshot(
        job_id=job.id,
        workspace_id=job.workspace_id,
        project_id=job.project_id,
        kind=job.kind,
        status=job.status,
        stage=job.stage,
        progress=float(job.progress),
        attempt=job.attempt,
        error_code=job.error_code,
        cancel_requested_at=job.cancel_requested_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _event_record(event: JobEvent) -> JobEventRecord:
    """Copy one stored event into the value a subscriber replays."""
    return JobEventRecord(
        job_id=event.job_id,
        sequence=event.sequence,
        event_type=JobEventType(event.event_type),
        payload=dict(event.payload),
        created_at=event.created_at,
    )


def _job_payload(job: Job) -> dict[str, Any]:
    """Describe the job state one event announces, so a subscriber needs no second read."""
    return {
        "status": job.status.value,
        "stage": job.stage,
        "progress": float(job.progress),
        "attempt": job.attempt,
        "errorCode": job.error_code,
    }
