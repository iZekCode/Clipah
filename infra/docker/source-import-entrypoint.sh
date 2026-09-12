#!/bin/sh
set -eu

python -m clipah.source_connectors.readiness
concurrency="${CLIPAH_SOURCE_IMPORT_CONCURRENCY:-1}"
case "$concurrency" in
  ''|*[!0-9]*|0) echo 'source import concurrency unavailable' >&2; exit 1 ;;
esac
exec celery \
  --app clipah.source_connectors.worker:app \
  worker \
  --queues source_import \
  --concurrency "$concurrency" \
  --loglevel INFO
