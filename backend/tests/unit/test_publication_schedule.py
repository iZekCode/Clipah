"""Unit contracts for how publishing work is scheduled and registered with Celery."""

from __future__ import annotations

import pytest

from clipah.celery_app import (
    PUBLICATION_DELIVERY_TASK,
    PUBLICATION_RELAY_TASK,
    configure_celery,
    create_celery_app,
)
from clipah.db import RuntimeRole
from clipah.jobs.tasks import celery_app
from support import runtime_settings

pytestmark = pytest.mark.unit


def test_the_publication_relay_runs_on_the_publishing_queue_when_publishing_is_on() -> None:
    """Without it, a confirmed publication would wait in the outbox forever."""
    settings = runtime_settings(
        RuntimeRole.WORKER,
        redis_url="redis://localhost:56380/1",
        social_publishing_enabled=True,
        publication_relay_interval_seconds=20,
    )

    app = configure_celery(create_celery_app(), settings)

    entry = app.conf.beat_schedule["publication-relay"]
    assert entry["task"] == PUBLICATION_RELAY_TASK
    assert entry["schedule"] == 20
    assert entry["options"]["queue"] == "social_publish"


def test_a_deployment_without_publishing_schedules_no_relay() -> None:
    """A switched-off feature must not wake a worker every few seconds for nothing."""
    settings = runtime_settings(RuntimeRole.WORKER, redis_url="redis://localhost:56380/1")

    app = configure_celery(create_celery_app(), settings)

    assert "publication-relay" not in app.conf.beat_schedule


def test_the_relay_and_delivery_tasks_are_registered_on_the_worker_application() -> None:
    """A schedule or a send naming a task nothing registered would silently do nothing."""
    assert PUBLICATION_RELAY_TASK in celery_app.tasks
    assert PUBLICATION_DELIVERY_TASK in celery_app.tasks
