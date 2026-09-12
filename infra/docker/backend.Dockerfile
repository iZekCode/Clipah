# syntax=docker/dockerfile:1.12

ARG PYTHON_IMAGE=python:3.13.15-slim-trixie@sha256:7ce4b6dfe35e55397b7cda544f8a13f191b7ae28dc5aad71fe664dbc9bc2623f
ARG DEBIAN_SNAPSHOT=20260831T000000Z
ARG CLIPAH_IMAGE_TARGET=api

FROM ghcr.io/astral-sh/uv:0.12.7@sha256:95f2aa1fe59274951cfe9b0cbc7972e879ff1004bc8945d130a32eb0dbd85945 AS uv

FROM ${PYTHON_IMAGE} AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/app/backend/.venv \
    PATH=/app/backend/.venv/bin:/usr/local/bin:/usr/bin:/bin
COPY --from=uv /uv /uvx /usr/local/bin/
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
COPY backend/alembic.ini ./alembic.ini
COPY backend/migrations ./migrations
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM ${PYTHON_IMAGE} AS runtime-root
ARG DEBIAN_SNAPSHOT
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/app/backend/.venv \
    PATH=/app/backend/.venv/bin:/usr/local/bin:/usr/bin:/bin \
    CLIPAH_JOB_WORKSPACE_ROOT=/var/lib/clipah/job-workspaces
RUN rm -f /etc/apt/sources.list.d/debian.sources \
    && printf '%s\n' \
      "deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}/ trixie main" \
      > /etc/apt/sources.list \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install --yes --no-install-recommends ca-certificates passwd \
    && rm -rf /var/lib/apt/lists/* \
    && /usr/sbin/groupadd --gid 10001 clipah \
    && /usr/sbin/useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin clipah \
    && install -d -m 0700 -o clipah -g clipah \
      /var/lib/clipah/job-workspaces /var/run/clipah
WORKDIR /app/backend
COPY --from=build --chown=clipah:clipah /app/backend /app/backend
COPY --chmod=0755 infra/docker/backend-entrypoint.sh /usr/local/bin/backend-entrypoint

FROM runtime-root AS api
ENV CLIPAH_RUNTIME_ROLE=api
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3).read()"]
ENTRYPOINT ["backend-entrypoint"]

FROM runtime-root AS worker
ENV CLIPAH_RUNTIME_ROLE=worker
USER 10001:10001
HEALTHCHECK --interval=30s --timeout=8s --start-period=20s --retries=3 \
  CMD ["celery", "--app", "clipah.runtime.worker:app", "inspect", "ping", "--timeout", "5"]
ENTRYPOINT ["backend-entrypoint"]

FROM runtime-root AS scheduler
ENV CLIPAH_RUNTIME_ROLE=scheduler
USER 10001:10001
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "from pathlib import Path; import os; os.kill(int(Path('/var/run/clipah/celerybeat.pid').read_text()), 0)"]
ENTRYPOINT ["backend-entrypoint"]

FROM runtime-root AS media-root
ARG DEBIAN_SNAPSHOT
RUN apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install --yes --no-install-recommends \
      ffmpeg=7:7.1.5-0+deb13u1 \
      fontconfig \
      fonts-noto-core \
      libmagic1t64=1:5.46-5 \
    && rm -rf /var/lib/apt/lists/*
RUN python -m clipah.runtime.readiness media

FROM media-root AS media-worker
ENV CLIPAH_RUNTIME_ROLE=worker \
    CLIPAH_MEDIA_RUNTIME_REQUIRED=1
USER 10001:10001
HEALTHCHECK --interval=30s --timeout=8s --start-period=20s --retries=3 \
  CMD ["celery", "--app", "clipah.runtime.worker:app", "inspect", "ping", "--timeout", "5"]
ENTRYPOINT ["backend-entrypoint"]

FROM build AS smoke-build
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
COPY backend/tests ./tests

FROM media-root AS smoke
COPY --from=smoke-build --chown=clipah:clipah /app/backend /app/backend
USER 10001:10001

# Railway currently accepts a configured Docker target; this build-argument fallback keeps
# service selection explicit on builders that only consume Dockerfile build arguments.
FROM ${CLIPAH_IMAGE_TARGET} AS deployment
