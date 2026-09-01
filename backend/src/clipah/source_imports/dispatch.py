"""Broker dispatch ports and adapters for durable jobs."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from clipah.celery_app import queue_for
from clipah.jobs.tasks import run_job
from clipah.models import JobKind


class JobDispatcher(Protocol):
    """Send identifiers for one already-committed job to its isolated queue."""

    def dispatch(self, *, job_id: UUID, workspace_id: UUID, user_id: UUID) -> None:
        """Dispatch UUID identifiers only."""


class CeleryJobDispatcher:
    """Production adapter for the single durable Celery task."""

    def dispatch(self, *, job_id: UUID, workspace_id: UUID, user_id: UUID) -> None:
        """Route source imports without serializing ORM state or credentials."""
        run_job.apply_async(
            args=(str(job_id), str(workspace_id), str(user_id)),
            queue=queue_for(JobKind.SOURCE_IMPORT),
        )


class RecordingJobDispatcher:
    """Deterministic fake used by in-process HTTP tests."""

    def __init__(self, *, fail: bool = False) -> None:
        """Initialize an empty call ledger and optional deterministic failure."""
        self.calls: list[tuple[UUID, UUID, UUID]] = []
        self.fail = fail

    def dispatch(self, *, job_id: UUID, workspace_id: UUID, user_id: UUID) -> None:
        """Record only the UUID values a real broker message would carry."""
        self.calls.append((job_id, workspace_id, user_id))
        if self.fail:
            raise RuntimeError("fake broker unavailable")
