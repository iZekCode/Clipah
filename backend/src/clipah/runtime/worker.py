"""Celery composition root and queue validation for deployed general workers."""

from __future__ import annotations

from clipah.celery_app import QUEUE_NAMES, configure_celery
from clipah.config import ProcessRole, Settings
from clipah.jobs.tasks import celery_app
from clipah.runtime.readiness import RuntimeReadinessError

ALLOWED_WORKER_QUEUES = frozenset(QUEUE_NAMES) - {"source_import"}


def parse_worker_queues(value: str) -> tuple[str, ...]:
    """Return one explicit, unique, allowlisted queue selection."""
    names = tuple(part.strip() for part in value.split(",") if part.strip())
    if not names or len(set(names)) != len(names) or not set(names) <= ALLOWED_WORKER_QUEUES:
        raise RuntimeReadinessError("worker queue configuration unavailable")
    return names


app = configure_celery(celery_app, Settings(process_role=ProcessRole.WORKER))
