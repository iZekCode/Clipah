"""Durable job values, the transitions they may take, and the worker's cancel check."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import select

from clipah.config import Settings
from clipah.db import RuntimeRole, session_scope
from clipah.models import Job, JobKind, JobStatus


class JobEventType(StrEnum):
    """Every durable fact a Job appends to its own append-only history."""

    CREATED = "created"
    STARTED = "started"
    PROGRESS = "progress"
    RETRYING = "retrying"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


TERMINAL_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELED}
)
# The state machine of `plan.md` section 2, written once so no caller can invent an edge.
ALLOWED_TRANSITIONS: Mapping[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.CANCELED}),
    JobStatus.RUNNING: frozenset(
        {
            JobStatus.RETRYING,
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCEL_REQUESTED,
        }
    ),
    # A retry budget can run out, so a retrying job may still end as a plain failure.
    JobStatus.RETRYING: frozenset({JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELED}),
    JobStatus.CANCEL_REQUESTED: frozenset({JobStatus.CANCELED, JobStatus.FAILED}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELED: frozenset(),
}
EVENT_FOR_STATUS: Mapping[JobStatus, JobEventType] = {
    JobStatus.RUNNING: JobEventType.STARTED,
    JobStatus.RETRYING: JobEventType.RETRYING,
    JobStatus.CANCEL_REQUESTED: JobEventType.CANCEL_REQUESTED,
    JobStatus.SUCCEEDED: JobEventType.SUCCEEDED,
    JobStatus.FAILED: JobEventType.FAILED,
    JobStatus.CANCELED: JobEventType.CANCELED,
}


class JobError(Exception):
    """Base class for every job failure the API maps to a stable code."""


class JobNotFoundError(JobError):
    """The Job does not exist, or this Workspace may not know that it does."""


class InvalidJobTransitionError(JobError):
    """The requested transition is not an edge of the durable job state machine."""


class JobCancelledError(JobError):
    """Raised inside a worker when the Workspace asked for this work to stop."""


class RetryableJobError(JobError):
    """Raised by a stage runner when a provider or network failure may succeed later."""


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    """One Job as every reader outside the ORM sees it."""

    job_id: UUID
    workspace_id: UUID
    project_id: UUID
    kind: JobKind
    status: JobStatus
    stage: str
    progress: float
    attempt: int
    error_code: str | None
    cancel_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class JobEventRecord:
    """One append-only entry of a Job's history, as a subscriber replays it."""

    job_id: UUID
    sequence: int
    event_type: JobEventType
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class JobContext:
    """The identifiers and cancellation question one worker stage is allowed to see.

    It carries no ORM object, Session, or credential, so a stage runner can never
    reach outside its own Workspace or hold a transaction open across provider work.
    """

    job_id: UUID
    workspace_id: UUID
    project_id: UUID
    user_id: UUID
    attempt: int
    settings: Settings

    def raise_if_cancelled(self) -> None:
        """Stop between stages when the Workspace has asked for this work to end."""
        if self.cancellation_requested():
            raise JobCancelledError(str(self.job_id))

    def cancellation_requested(self) -> bool:
        """Read the durable cancel flag in its own short transaction, never a stale one."""
        with session_scope(
            settings=self.settings,
            workspace_id=self.workspace_id,
            user_id=self.user_id,
            runtime_role=RuntimeRole.WORKER,
        ) as session:
            requested = session.scalar(
                select(Job.cancel_requested_at).where(
                    Job.workspace_id == self.workspace_id, Job.id == self.job_id
                )
            )
        return requested is not None


def assert_transition(current: JobStatus, target: JobStatus) -> None:
    """Refuse any transition the durable state machine does not describe."""
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidJobTransitionError(f"{current.value} cannot become {target.value}")
