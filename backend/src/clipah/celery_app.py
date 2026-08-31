"""Celery composition root: queues, routing, and the retry policy tasks never decide."""

from __future__ import annotations

from collections.abc import Mapping

from celery import Celery
from kombu import Queue

from clipah.config import Settings
from clipah.models import JobKind

SETTINGS_KEY = "clipah_settings"
DEFAULT_QUEUE = "maintenance"
RETRY_BASE_SECONDS = 5
RETRY_MAX_SECONDS = 600
MAX_ATTEMPTS = 5
# One queue per contended resource, so slow work can never starve fast work.
QUEUE_FOR_JOB_KIND: Mapping[JobKind, str] = {
    JobKind.SOURCE_IMPORT: "source_import",
    JobKind.INGEST: "ingest",
    JobKind.TRANSCRIBE: "ai",
    JobKind.ANALYZE: "ai",
    JobKind.BROLL_PLAN: "ai",
    JobKind.CAMPAIGN_GENERATE: "ai",
    JobKind.BROLL_RETRIEVE: "broll_retrieve",
    JobKind.BROLL_GENERATE: "broll_generate",
    JobKind.RENDER: "render",
    JobKind.SOCIAL_RENDITION: "social_rendition",
    JobKind.SOCIAL_PUBLISH: "social_publish",
    JobKind.SOCIAL_RECONCILE: "social_reconcile",
    JobKind.CLEANUP: DEFAULT_QUEUE,
}
QUEUE_NAMES: tuple[str, ...] = tuple(dict.fromkeys(QUEUE_FOR_JOB_KIND.values()))


def create_celery_app() -> Celery:
    """Create the unconfigured application tasks are registered against."""
    return Celery("clipah")


def configure_celery(app: Celery, settings: Settings) -> Celery:
    """Apply one deployment's broker, queues, and delivery guarantees to a Celery app."""
    app.conf.update(
        broker_url=settings.redis_url,
        result_backend=settings.redis_url,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_queues=tuple(Queue(name) for name in QUEUE_NAMES),
        task_default_queue=DEFAULT_QUEUE,
        # One message at a time with late acknowledgement: a lost worker re-delivers
        # the job instead of silently dropping paid work.
        worker_prefetch_multiplier=1,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_default_retry_delay=RETRY_BASE_SECONDS,
        broker_connection_retry_on_startup=True,
        timezone="UTC",
        enable_utc=True,
    )
    app.conf[SETTINGS_KEY] = settings
    return app


def queue_for(kind: JobKind) -> str:
    """Return the queue one kind of work is allowed to occupy."""
    return QUEUE_FOR_JOB_KIND[kind]


def settings_for(app: Celery) -> Settings:
    """Return the settings this worker process was composed with."""
    settings = app.conf.get(SETTINGS_KEY)
    if not isinstance(settings, Settings):
        raise RuntimeError("celery application was never configured with Clipah settings")
    return settings
