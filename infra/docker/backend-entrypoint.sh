#!/bin/sh
set -eu

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

case "${CLIPAH_RUNTIME_ROLE:-}" in
  api)
    exec uvicorn clipah.runtime.api:app \
      --host 0.0.0.0 \
      --port "${PORT:-8000}" \
      --proxy-headers \
      --forwarded-allow-ips "${CLIPAH_FORWARDED_ALLOW_IPS:-127.0.0.1}"
    ;;
  worker)
    queues="${CLIPAH_WORKER_QUEUES:-}"
    concurrency="${CLIPAH_WORKER_CONCURRENCY:-}"
    python -c 'from clipah.runtime.worker import parse_worker_queues; import os; parse_worker_queues(os.environ.get("CLIPAH_WORKER_QUEUES", ""))'
    case "$concurrency" in
      ''|*[!0-9]*|0) echo 'worker concurrency unavailable' >&2; exit 1 ;;
    esac
    if [ "${CLIPAH_MEDIA_RUNTIME_REQUIRED:-0}" = "1" ]; then
      python -m clipah.runtime.readiness media
    fi
    exec celery --app clipah.runtime.worker:app worker \
      --queues "$queues" \
      --concurrency "$concurrency" \
      --hostname "clipah@%h" \
      --loglevel "${CLIPAH_LOG_LEVEL:-INFO}"
    ;;
  scheduler)
    exec celery --app clipah.runtime.worker:app beat \
      --schedule /var/run/clipah/celerybeat-schedule \
      --pidfile /var/run/clipah/celerybeat.pid \
      --loglevel "${CLIPAH_LOG_LEVEL:-INFO}"
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  *)
    echo 'runtime role unavailable' >&2
    exit 1
    ;;
esac
