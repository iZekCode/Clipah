# Reproducible Production Services Design

## Scope

This design implements Task 46 from `plan.md`: reproducible local and Railway runtimes for
the Next.js frontend, FastAPI API, isolated source import, background processing, social
publishing, reconciliation, and scheduled maintenance. It does not add CI, load testing, or
recovery drills from Task 47, and it does not remove the legacy stack or `nixpacks.toml` before
Task 48's cutover.

## Image architecture

`infra/docker/backend.Dockerfile` is a multi-stage build rooted in a digest-pinned Python 3.13
image, a dated Debian snapshot, and the repository's frozen `uv.lock`. Its common runtime target
contains only the application and shared native libraries. A media target adds the exact FFmpeg
and ffprobe build, libmagic, fontconfig, and pinned fonts needed by ingest, render, and social
rendition work. API, non-media AI, B-roll, publishing, reconciliation, scheduler, and maintenance
processes use the smallest compatible target instead of inheriting media or source-import tools.

The existing `infra/docker/source-import.Dockerfile` remains independent. It alone contains
yt-dlp, yt-dlp-ejs, Deno, and any later approved PO Token plugin. Its build and startup readiness
checks continue to pin each executable and package exactly.

`infra/docker/frontend.Dockerfile` uses digest-pinned Node and Corepack/pnpm versions. It installs
from the workspace lockfile, builds the Next.js application in standalone mode, and copies only
the standalone server, static assets, and public assets into its non-root runtime stage.

Every image runs as a fixed unprivileged UID/GID. The runtime filesystem is read-only. `/tmp` and
the Job workspace root are explicit writable mounts or tmpfs paths; application code and installed
dependencies remain immutable. Secrets enter only at runtime.

## Runtime verification

A backend runtime module exposes role-aware readiness commands. Media readiness checks the exact
FFmpeg and ffprobe version plus the encoders and filters used by Clipah. The required capabilities
are H.264 video, AAC audio, `subtitles`, `drawtext`, and `zoompan`; font readiness proves that the
pinned sans-serif family resolves through fontconfig. The Docker build invokes the same verifier
used by worker startup, preventing build/startup drift.

API liveness uses `/health/live`; API readiness uses `/health/ready`. Celery worker health
uses a bounded process-local ping or readiness command that cannot dump configuration. The
scheduler health check proves its PID is alive and its schedule file is writable in the declared
runtime volume. Health responses and failures contain no environment or secret values.

`scripts/verify-runtime.sh` is the operator entry point. It verifies Compose service health,
checks role-specific image contents and filesystem permissions, applies migrations, seeds a fixed
fixture User, personal Workspace, and Project, and drives the existing durable APIs and workers
through the Task 46 smoke scenario. It verifies the final MP4 and dialogue audio, fake
YouTube/Instagram/TikTok results including partial success, and idempotent replay.

## Process topology and isolation

Compose and Railway use the same role boundaries and Celery queue names:

| Process | Queues or command | Native tooling | Credential scope |
| --- | --- | --- | --- |
| frontend | Next.js standalone server | none | API origin only |
| api | ASGI server | none | browser auth, database, Redis, object store, OAuth connection setup |
| source-import | `source_import` | yt-dlp, Deno, FFmpeg, ffprobe, libmagic | source-import and object-store credentials only |
| ingest-ai | `ingest,ai,maintenance` | FFmpeg, ffprobe, libmagic | transcription/LLM, object store, worker and retention database access |
| broll | `broll_retrieve,broll_generate` | none | stock/generation providers and object store |
| render | `render,social_rendition` | FFmpeg, ffprobe, fonts | object store only |
| social-publish | `social_publish` | none | provider-specific publishing credentials, KMS, object store |
| social-reconcile | `social_reconcile` | none | provider-specific reconciliation credentials and KMS |
| scheduler | Celery beat | none | Redis and scheduling database access only |

Each process has an independently configurable concurrency value. Celery prefetch remains one.
Existing Workspace admission and provider/Social-Account rate-limit subjects remain the
authoritative admission controls; deployment concurrency supplements rather than replaces them.

Railway configuration is one file per Task 46 process and selects the appropriate Docker target,
start command, health check, restart policy, and graceful shutdown interval. It names required
environment variables but contains no credential values. Compose supplies explicitly local-only
values and binds infrastructure ports to loopback.

## OAuth Grant key management

Production uses an AWS KMS implementation of the existing `SocialSecretStore` boundary. KMS wraps
one random data-encryption key per OAuth Grant; only the KMS key ARN/version reference and wrapped
bytes are durable. Decryption is allowed only through the existing `OAuthGrantLease`, whose mutable
plaintext buffer is single-use, time-bounded, and overwritten when the operation exits.

The API, social-publish worker, and social-reconcile worker receive KMS access because they create,
refresh, revoke, publish with, or reconcile Social Accounts. Other workers receive neither KMS
configuration nor social-provider client secrets. Local Compose retains the existing locally
derived wrapping-key implementation so development does not require a cloud account. Production
fails closed if KMS is selected but its key reference, region, or permissions are unavailable.

## Compose lifecycle

Postgres, Redis, and MinIO keep their current pinned releases and health checks. A one-shot MinIO
initializer creates the private bucket. A one-shot migration service runs to completion before API
and worker services start. Application services declare dependency health, resource limits,
read-only roots, tmpfs paths, bounded stop-grace periods, and restart behavior.

The smoke workflow uses deterministic fake external providers and checked-in media fixtures. It
never needs internet access or live provider credentials. Re-running the workflow against the same
idempotency keys must reuse durable resources instead of duplicating them.

## Testing strategy

Development follows red-green-refactor. Static contract tests first parse Dockerfiles, Compose,
Railway files, process commands, health checks, mounts, resource limits, image pins, and credential
allowlists. Unit tests cover role-aware media capability validation and AWS KMS wrapping failures
without network calls. Python overwrites its mutable data-key working copy after use and promptly
releases the SDK-owned immutable response bytes; it does not claim memory erasure Python cannot
guarantee. Integration tests build the relevant targets and assert the non-root,
read-only, version, tool-presence, and tool-absence contracts. The final gate is the deterministic
Compose smoke workflow followed by all backend and frontend quality gates.

Configuration errors fail before a process accepts work. Runtime provider, storage, and KMS
failures use existing sanitized domain errors and telemetry; raw commands, environment dumps,
credentials, signed URLs, and local paths never enter health output or crash reports.

## Completion

Task 46 is complete only when every checkbox in `plan.md` passes, both image families build, all
Compose services become healthy, `scripts/verify-runtime.sh` completes twice without duplication,
and the backend and frontend quality gates remain green. `PROGRESS.md` will then record the result
and the owner will commit it as `build: add reproducible production services`.
