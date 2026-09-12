# Reproducible Production Services Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this
> plan task-by-task. Execute inline; the owner explicitly prohibited subagents for this task.

**Goal:** Build reproducible, least-privilege local and Railway runtime images for every Clipah
process and prove the complete fixture workflow in Docker Compose.

**Architecture:** A digest-pinned multi-stage backend image supplies minimal and media-capable
targets, while source import remains a separate tool-isolated image and the frontend ships as a
standalone Next.js image. Role-aware startup verification, explicit Compose/Railway boundaries,
and AWS KMS behind the existing OAuth secret-store interface enforce runtime integrity and
credential isolation.

**Tech Stack:** Docker BuildKit, Docker Compose, Railway, Python 3.13, uv, FastAPI/Uvicorn,
Celery, FFmpeg 7.1.5, Node.js, pnpm, Next.js, AWS KMS/boto3, pytest, shell.

**Spec:** `docs/superpowers/specs/2026-09-11-reproducible-production-services-design.md`

## Global Constraints

- Follow every Task 46 checkbox in `plan.md` and every safety rule in `AGENTS.md`.
- Pin FFmpeg `7:7.1.5-0+deb13u1`, yt-dlp `2026.08.19`, yt-dlp-ejs `0.8.0`, Deno
  `2.9.5`, and uv `0.12.7`.
- Keep yt-dlp, yt-dlp-ejs, and Deno exclusive to the source-import image.
- Run application containers as UID/GID 10001 with read-only root filesystems.
- Keep Celery prefetch at one and preserve Workspace/provider admission limits.
- Never bake or print secrets, signed URLs, OAuth plaintext, or local paths.
- Keep `nixpacks.toml` until Task 48 cutover.
- Do not commit, push, or rewrite history; the owner makes the final commit.

---

### Task 1: Lock the deployment contract in failing tests

**Files:**
- Create: `backend/tests/contract/test_runtime_deployment.py`
- Modify: `PROGRESS.md`

**Interfaces:**
- Produces: executable contracts for images, services, process commands, isolation, health, and
  resource limits.
- Consumes: the approved service matrix and exact version pins.

- [x] **Step 1: Update current progress.** Record Task 45 as landed at `ba19e39`, change Task 46
  to `[~]`, and retain its final owner commit as uncommitted.
- [x] **Step 2: Write configuration tests before configuration exists.** Render Compose through
  `docker compose config --format json` and parse Railway files with `tomllib`. Assert the required
  services (`frontend`, `api`, `source-import`, three processing worker groups, two social worker
  groups, scheduler, migration, MinIO initialization, Postgres, Redis, and MinIO), non-root users,
  read-only roots, writable temp paths, limits, health checks, shutdown settings, exact queue
  ownership, Docker targets, and credential allowlists. Tool versions and absence are verified by
  executing built images in Task 4, not by grepping Dockerfile source.
- [x] **Step 3: Verify RED.** Run `cd backend && uv run pytest -q
  tests/contract/test_runtime_deployment.py`. Expect failures for missing backend/frontend images,
  Railway files, and Compose application services.

---

### Task 2: Add role-aware startup and media verification

**Files:**
- Create: `backend/src/clipah/runtime/__init__.py`
- Create: `backend/src/clipah/runtime/readiness.py`
- Create: `backend/src/clipah/runtime/api.py`
- Create: `backend/src/clipah/runtime/worker.py`
- Create: `backend/tests/unit/test_runtime_readiness.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/src/clipah/jobs/ingest_task.py`
- Modify: `backend/src/clipah/jobs/render_task.py`

**Interfaces:**
- Produces: `MediaCapabilityVerifier.verify()`, ASGI `app`, and role-aware Celery `app`.
- Consumes: `create_app`, `configure_celery`, current task registration, and
  `CLIPAH_WORKER_QUEUES`.

- [x] **Step 1: Write verifier tests.** Use a fake executor for `ffmpeg -version`, `ffprobe
  -version`, `ffmpeg -encoders`, `ffmpeg -filters`, and `fc-match`. Test exact-version acceptance;
  separate sanitized failures for H.264, AAC, `subtitles`, `drawtext`, `zoompan`, and `Noto Sans`;
  ASGI construction; allowed queue sets; and rejection of unknown/source-import queues.
- [x] **Step 2: Verify RED.** Run `cd backend && uv run pytest -q
  tests/unit/test_runtime_readiness.py`. Expect collection failure because `clipah.runtime` is
  absent.
- [x] **Step 3: Implement minimal runtime composition.** Use argument arrays, bounded output, and
  timeouts. Define `REQUIRED_ENCODERS = {"libx264", "aac"}`, `REQUIRED_FILTERS = {"subtitles",
  "drawtext", "zoompan"}`, and `PINNED_FONT_FAMILY = "Noto Sans"`. Add locked Uvicorn. Construct
  API and Celery apps without exposing settings, validate queue names, and run media checks only
  for ingest/render/social-rendition roles.
- [x] **Step 4: Reuse readiness.** Replace ingest/render version-only checks with the shared
  capability verifier while retaining libmagic validation for ingest.
- [x] **Step 5: Verify GREEN.** Run `cd backend && uv run pytest -q
  tests/unit/test_runtime_readiness.py tests/unit/test_ffmpeg.py tests/unit/test_ingest.py
  tests/unit/test_render_compiler.py`. Expect all selected tests to pass.

---

### Task 3: Add AWS KMS OAuth Grant wrapping

**Files:**
- Modify: `backend/src/clipah/config.py`
- Modify: `backend/src/clipah/social_accounts/secrets.py`
- Modify: `backend/src/clipah/api/app.py`
- Create: `backend/tests/unit/test_kms_social_secrets.py`
- Modify: `backend/tests/unit/test_config.py`
- Modify: `backend/tests/unit/test_api_dependencies.py`

**Interfaces:**
- Produces: `AwsKmsSocialSecretStore` and `social_secret_store_for(settings)` selected by
  `CLIPAH_SOCIAL_SECRET_BACKEND=aws_kms`.
- Consumes: boto3 KMS data-key operations and the existing `OAuthGrantLease` boundary.

- [x] **Step 1: Write KMS tests.** With a fake KMS client, prove `generate_data_key` uses the exact
  key ARN, AES-256, and all durable identity fields as encryption context; only wrapped bytes are
  stored; the mutable plaintext working copy is overwritten; decrypt repeats the exact context; provider
  errors are sanitized; production KMS selection requires ARN/region; and representations contain
  no plaintext.
- [x] **Step 2: Verify RED.** Run `cd backend && uv run pytest -q
  tests/unit/test_kms_social_secrets.py tests/unit/test_config.py tests/unit/test_api_dependencies.py
  -k 'kms or social_secret_backend'`. Expect missing settings/store failures.
- [x] **Step 3: Implement the KMS backend.** Add strict `local`/`aws_kms` selection, key ARN, and
  region settings. Store the ARN as `key_reference`, KMS ciphertext as `wrapped_key`, and retain
  AES-GCM OAuth payload encryption. Convert context fields to KMS `EncryptionContext`, overwrite
  the mutable working copy, and promptly release the AWS SDK's immutable response bytes without
  claiming memory erasure Python cannot guarantee.
- [x] **Step 4: Wire fail-closed selection.** Local/test may use local derivation. Production KMS
  requires a configured and reachable backend. API/social workers continue using one-use bounded
  leases; plaintext never enters configuration or telemetry.
- [x] **Step 5: Verify GREEN.** Run `cd backend && uv run pytest -q
  tests/unit/test_kms_social_secrets.py tests/unit/test_source_secrets.py
  tests/unit/test_api_dependencies.py tests/unit/test_config.py`. Expect all selected tests pass.

---

### Task 4: Build backend and frontend images

**Files:**
- Create: `.dockerignore`
- Create: `infra/docker/backend.Dockerfile`
- Create: `infra/docker/backend-entrypoint.sh`
- Create: `infra/docker/frontend.Dockerfile`
- Modify: `infra/docker/source-import.Dockerfile`
- Modify: `infra/docker/source-import-entrypoint.sh`
- Modify: `frontend/next.config.mjs`
- Modify: `backend/tests/contract/test_runtime_deployment.py`

**Interfaces:**
- Produces: minimal API/worker/scheduler, media-worker, standalone frontend, and isolated
  source-import targets.
- Consumes: frozen locks and Task 2 readiness commands.

- [x] **Step 1: Extend failing image tests.** Build the declared targets and execute controlled
  inspection commands against them. Assert UID/GID 10001, read-only-root startup, exact tool
  versions/capabilities, tool absence from unrelated targets, standalone Next.js startup, bounded
  role health commands, and SIGTERM reaching the foreground process.
- [x] **Step 2: Verify RED.** Run `cd backend && uv run pytest -q
  tests/contract/test_runtime_deployment.py -k 'dockerfile or image or entrypoint'`.
- [x] **Step 3: Implement images.** Install exact native packages in media targets, invoke shared
  readiness during build, keep source-only tools isolated, validate positive deployment
  concurrency, and copy only Next.js standalone/static/public output to the frontend runtime.
- [x] **Step 4: Verify image-contract GREEN.** Re-run the selected contract tests.
- [x] **Step 5: Build and inspect.** Build every target and run each with read-only root plus
  declared tmpfs/volumes. Assert UID 10001, media capabilities where required, and source-tool
  absence elsewhere.

---

### Task 5: Complete Compose and deterministic smoke execution

**Files:**
- Modify: `infra/compose.yaml`
- Create: `infra/compose.env.example`
- Create: `backend/tests/runtime/seed_smoke.py`
- Create: `backend/tests/runtime/run_smoke.py`
- Create: `scripts/verify-runtime.sh`
- Modify: `backend/tests/contract/test_runtime_deployment.py`

**Interfaces:**
- Produces: local infrastructure/application topology and repeatable end-to-end verifier.
- Consumes: image targets, current migrations, fake providers, checked-in media, and durable use
  cases.

- [x] **Step 1: Add failing Compose/script cases.** Render Compose and assert dependencies,
  read-only/non-root execution, limits, writable job/temp paths, health, stop grace, independently
  configured concurrency, and explicit environment allowlists. Execute the script against a fake
  `docker` executable returning controlled health/failure results; assert bounded waiting, bounded
  failure logs, preserved named volumes, and two smoke passes with one fixture identity. Do not
  test source text.
- [x] **Step 2: Verify RED.** Run `cd backend && uv run pytest -q
  tests/contract/test_runtime_deployment.py -k 'compose or credential or queue or verify_runtime'`.
- [x] **Step 3: Complete Compose.** Keep infrastructure loopback-bound. Add private bucket setup,
  one-shot migration, frontend/API, source import, ingest/AI/maintenance, B-roll, render/rendition,
  social publish, social reconcile, scheduler, and smoke profile. List service-specific environment
  keys explicitly; do not use a broad secret-bearing anchor.
- [x] **Step 4: Implement deterministic seed/smoke.** Seed fixed UUIDs, use fake external
  providers, process the fixture through candidates, B-roll plan/retrieval/acceptance, Edit Revision,
  Render Artifact, and independent fake YouTube/Instagram/TikTok Publications. Assert MP4,
  preserved dialogue, intentional partial success, and same-key replay without duplicate rows or
  artifacts.
- [x] **Step 5: Implement `verify-runtime.sh`.** Discover the repository root safely, use the
  explicit Compose file, cap waits, never dump the environment, show bounded logs on failure, and
  invoke the same smoke identity twice. Preserve named volumes and leave healthy infrastructure
  available after success.
- [x] **Step 6: Verify GREEN.** Run `docker compose -f infra/compose.yaml config --quiet`, then
  `cd backend && uv run pytest -q tests/contract/test_runtime_deployment.py tests/runtime`.

---

### Task 6: Add per-process Railway configuration

**Files:**
- Create: `infra/railway/api.toml`
- Create: `infra/railway/worker-ingest-ai.toml`
- Create: `infra/railway/worker-source-import.toml`
- Create: `infra/railway/worker-broll.toml`
- Create: `infra/railway/worker-render.toml`
- Create: `infra/railway/worker-social-publish.toml`
- Create: `infra/railway/worker-social-reconcile.toml`
- Create: `infra/railway/scheduler.toml`
- Modify: `backend/tests/contract/test_runtime_deployment.py`

**Interfaces:**
- Produces: independently deployable Railway services with exact target, command, health, restart,
  concurrency, and shutdown behavior.
- Consumes: Tasks 4–5 process boundaries.

- [x] **Step 1: Add failing TOML tests.** Assert all eight files, Docker paths/targets, exact queue
  ownership, health/restart/shutdown settings, and absence of literal credentials or unrelated
  provider environment names.
- [x] **Step 2: Verify RED.** Run `cd backend && uv run pytest -q
  tests/contract/test_runtime_deployment.py -k railway`.
- [x] **Step 3: Create configurations.** Reference environment names only; select the exact image
  target and runtime command; use API readiness and bounded worker/scheduler health; configure
  finite warm-shutdown and on-failure restart.
- [x] **Step 4: Verify GREEN.** Re-run the Railway contract tests.

---

### Task 7: Full runtime proof and handoff

**Files:**
- Modify: `PROGRESS.md`

**Interfaces:**
- Produces: honest completion record and owner handoff.
- Consumes: all Task 46 acceptance evidence.

- [x] **Step 1: Run backend gates.** From `backend/`, run `uv run ruff check .`, `uv run ruff
  format --check .`, `uv run mypy src`, and `uv run pytest -q --cov=clipah
  --cov-fail-under=90`. All must pass.
- [x] **Step 2: Run frontend gates.** From the root, run `pnpm lint`, `pnpm typecheck`, `pnpm
  test`, and `pnpm build`. All must pass.
- [x] **Step 3: Run deployment gates.** Run `docker compose -f infra/compose.yaml up --build -d`,
  `scripts/verify-runtime.sh`, and `git diff --check`. All must pass without secret leakage.
- [x] **Step 4: Update progress only after success.** Mark Task 46 complete awaiting owner commit,
  record exact gate results and deliberate deferrals, and leave the commit entry uncommitted.
- [x] **Step 5: Hand off without committing.** Report changes, gate outputs, and exact owner commit
  message `build: add reproducible production services`.
