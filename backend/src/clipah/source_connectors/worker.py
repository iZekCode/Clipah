"""Celery composition root for the independently deployed source-import worker."""

from clipah.celery_app import configure_celery
from clipah.config import ProcessRole, Settings
from clipah.jobs.tasks import celery_app

app = configure_celery(celery_app, Settings(process_role=ProcessRole.WORKER))
