"""ASGI composition root for the deployed Clipah API process."""

from clipah.api.app import create_app
from clipah.celery_app import configure_celery
from clipah.config import ProcessRole, Settings
from clipah.jobs.tasks import celery_app

settings = Settings(process_role=ProcessRole.API)

# The API dispatches every committed Job through this same Celery application, and it does
# so best-effort: a broker failure is suppressed so an outage cannot undo work Postgres has
# already recorded. That silence is only safe if the broker is actually configured here.
# Left unconfigured, Celery falls back to its own default broker, every dispatch is refused
# by a host nobody is running, and every Job stays queued while each request still answers
# 202. The worker entrypoints configure the same application for the same reason.
configure_celery(celery_app, settings)

app = create_app(settings)
