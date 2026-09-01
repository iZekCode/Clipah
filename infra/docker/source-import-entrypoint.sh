#!/bin/sh
set -eu

uv run --frozen --no-sync python -m clipah.source_connectors.readiness
exec celery \
  --app clipah.source_connectors.worker:app \
  worker \
  --queues source_import \
  --concurrency 1 \
  --loglevel INFO
