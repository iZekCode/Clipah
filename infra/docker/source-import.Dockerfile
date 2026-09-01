# syntax=docker/dockerfile:1.12

ARG PYTHON_IMAGE=python:3.13.15-slim-trixie@sha256:7ce4b6dfe35e55397b7cda544f8a13f191b7ae28dc5aad71fe664dbc9bc2623f
ARG DEBIAN_SNAPSHOT=20260831T000000Z

FROM ghcr.io/astral-sh/uv:0.12.7@sha256:95f2aa1fe59274951cfe9b0cbc7972e879ff1004bc8945d130a32eb0dbd85945 AS uv

FROM ${PYTHON_IMAGE} AS deno
ARG DEBIAN_SNAPSHOT
ARG TARGETARCH
RUN rm -f /etc/apt/sources.list.d/debian.sources \
    && printf '%s\n' \
      "deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}/ trixie main" \
      > /etc/apt/sources.list \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install --yes --no-install-recommends ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) deno_arch="x86_64" ;; \
      arm64) deno_arch="aarch64" ;; \
      *) echo "unsupported source-import architecture" >&2; exit 1 ;; \
    esac; \
    asset="deno-${deno_arch}-unknown-linux-gnu.zip"; \
    base_url="https://github.com/denoland/deno/releases/download/v2.9.5"; \
    curl --fail --show-error --silent --location --remote-name "${base_url}/${asset}"; \
    curl --fail --show-error --silent --location --remote-name "${base_url}/${asset}.sha256sum"; \
    sha256sum --check "${asset}.sha256sum"; \
    unzip "${asset}" -d /usr/local/bin; \
    /usr/local/bin/deno --version | head -n 1 | grep --fixed-strings 'deno 2.9.5'

FROM ${PYTHON_IMAGE} AS runtime
ARG DEBIAN_SNAPSHOT
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/app/backend/.venv \
    PATH=/app/backend/.venv/bin:/usr/local/bin:/usr/bin:/bin \
    CLIPAH_JOB_WORKSPACE_ROOT=/var/lib/clipah/job-workspaces \
    DENO_DIR=/var/cache/clipah/deno

RUN rm -f /etc/apt/sources.list.d/debian.sources \
    && printf '%s\n' \
      "deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}/ trixie main" \
      > /etc/apt/sources.list \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install --yes --no-install-recommends \
      ca-certificates \
      ffmpeg=7:7.1.5-0+deb13u1 \
      passwd \
    && rm -rf /var/lib/apt/lists/*
COPY --from=deno /usr/local/bin/deno /usr/local/bin/deno
COPY --from=uv /uv /uvx /usr/local/bin/

RUN /usr/sbin/groupadd --gid 10001 clipah \
    && /usr/sbin/useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin clipah \
    && install -d -m 0700 -o clipah -g clipah \
      /var/lib/clipah/job-workspaces \
      /var/cache/clipah/deno

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --extra source-import --no-install-project
COPY backend/src ./src
RUN uv sync --frozen --no-dev --extra source-import

COPY --chmod=0755 infra/docker/source-import-entrypoint.sh /usr/local/bin/source-import-entrypoint
RUN chown -R clipah:clipah /app/backend
USER clipah

HEALTHCHECK --interval=30s --timeout=25s --start-period=10s --retries=3 \
  CMD ["uv", "run", "--frozen", "--no-sync", "python", "-m", "clipah.source_connectors.readiness"]
ENTRYPOINT ["source-import-entrypoint"]
