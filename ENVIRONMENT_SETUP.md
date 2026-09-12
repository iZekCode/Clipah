# Environment Setup Guide

Everything Clipah needs to run, locally and in production, and the complete inventory of
every environment variable it reads.

Clipah is one backend (`backend/`, FastAPI and Celery on Python 3.13) and one frontend
(`frontend/`, Next.js). Every backend setting takes the `CLIPAH_` prefix and is defined in
`backend/src/clipah/config.py`, which is fail-closed: a deployment missing required key
material refuses to start rather than falling back to a weak default.

## What you need first

| Tool | Why |
| --- | --- |
| [`uv`](https://docs.astral.sh/uv/) | Manages the Python 3.13 environment and lockfile. Never call `pip` or a bare `python`. |
| [`pnpm`](https://pnpm.io/) | Manages the frontend workspace. |
| Docker | Runs Postgres, Redis, and MinIO locally, and builds the pinned media images. |
| FFmpeg | Media probing, proxies, and exports. The pinned version lives in the media image; a host copy is only needed for the evaluation harnesses. |

## Step 1: Start the local infrastructure

```bash
docker compose -f infra/compose.yaml up -d
```

Three services, all bound to loopback only:

| Service | Address | Notes |
| --- | --- | --- |
| Postgres 17 | `127.0.0.1:55433` | database `clipah_rebuild_foundation` |
| Redis 7.4 | `127.0.0.1:56380` | |
| MinIO | `127.0.0.1:59001` (console `59002`) | user `clipah_local`, password `clipah_local_secret` |

`infra/postgres/init-runtime.sql` provisions the database roles on first start. The
migration owner is `clipah_migrator`; the application connects as the least-privilege
logins `clipah_api_runtime` and `clipah_worker_runtime`.

**Create the object-store bucket once.** Compose does not create it for you:

```bash
docker exec -it clipah-rebuild-foundation-minio-1 \
  mc alias set local http://127.0.0.1:9000 clipah_local clipah_local_secret
docker exec -it clipah-rebuild-foundation-minio-1 mc mb local/clipah
```

## Step 2: Write `backend/.env`

The file is gitignored. The fields that must be filled in are listed under
[Credentials](#step-3-credentials); the complete list of everything you *may* set is the
[inventory](#the-complete-inventory) at the end of this document.

**One trap worth knowing.** `CLIPAH_ENVIRONMENT=local` *inside* the file does nothing on
its own. `backend/src/clipah/config.py` only consults `.env` after a real environment
variable or an explicit argument has already selected the local profile, so that a
production process can never be reconfigured by a file someone dropped beside it. Export it
in your shell:

```bash
export CLIPAH_ENVIRONMENT=local
```

Without that, the file is ignored entirely and every setting silently falls back to its
default.

## Step 3: Credentials

### Google sign-in (required — there is no other way in)

Clipah authenticates every request through a Session created by Google OIDC. Without these
three values nobody can sign in at all.

1. **Create a project** at <https://console.cloud.google.com/projectcreate>, then select it
   in the top bar.
2. **Configure the consent screen** at <https://console.cloud.google.com/auth/branding>:
   user type **External**, an app name, and your own address for both support and developer
   contact. Leave it in **Testing**, and add your Google account under **Audience → Test
   users** — in Testing mode only listed accounts may sign in.
3. **Scopes need no configuration.** The application requests exactly `openid`, `email`,
   and `profile` (`backend/src/clipah/auth/google_oidc.py`). All three are non-sensitive, so
   Google requires no verification review.
4. **Create the client** at <https://console.cloud.google.com/apis/credentials>:
   **Create credentials → OAuth client ID → Web application**.
   - Leave *Authorized JavaScript origins* **empty**. This is a server-side
     authorization-code flow with PKCE; no browser SDK is involved.
   - Add exactly one *Authorized redirect URI*:

     ```
     http://localhost:3000/api/v1/auth/google/callback
     ```

**That URI points at the Next dev server, not the API, and the distinction matters.** The
browser only ever talks to its own origin; `frontend/next.config.mjs` rewrites `/api/*` to
FastAPI, which is what keeps the Session cookie first-party and lets the CSRF `Origin`
check see a same-origin request. Sending Google to port 8000 instead would set the cookie on
the wrong origin, and sign-in would appear to succeed and then silently fail. Google
compares the string literally, so `127.0.0.1` will not match `localhost`. Plain `http` is
accepted because Google exempts localhost; the configuration enforces HTTPS in production
only.

```env
CLIPAH_GOOGLE_OIDC_CLIENT_ID=<...apps.googleusercontent.com>
CLIPAH_GOOGLE_OIDC_CLIENT_SECRET=<GOCSPX-...>
CLIPAH_GOOGLE_OIDC_REDIRECT_URI=http://localhost:3000/api/v1/auth/google/callback
```

### Session secret (required)

At least 32 characters. It signs Session material and seals generated-media confirmation
tokens, so rotating it invalidates any estimate a member is currently holding.

```bash
openssl rand -hex 32
```

### The AI pipeline (required to process anything)

| Variable | Purpose | Where |
| --- | --- | --- |
| `CLIPAH_ASSEMBLYAI_API_KEY` | Transcription and speaker diarization | <https://www.assemblyai.com/dashboard/> |
| `CLIPAH_GROQ_API_KEY` | Highlight extraction and reranking | <https://console.groq.com/keys> |

Transcription model routing is decided by language, not by configuration: English, Spanish,
German, French, Portuguese, and Italian use `universal-3-pro`; Indonesian and every other
language outside that set use `universal-2`; an unspecified language tries U3 Pro and falls
back to U2. Highlight extraction and reranking use `CLIPAH_GROQ_EXTRACTION_MODEL` and
`CLIPAH_GROQ_RERANKING_MODEL`. Startup refuses a retired model ID.

### Stock B-roll (optional)

| Variable | Where |
| --- | --- |
| `CLIPAH_PEXELS_API_KEY` | <https://www.pexels.com/api/> |
| `CLIPAH_PIXABAY_API_KEY` | <https://pixabay.com/api/docs/> |

An absent credential means that provider is simply unavailable. It is never a startup
failure: a Workspace may run on its own footage alone.

### Generated B-roll (optional)

Leave `CLIPAH_FAL_API_KEY` blank and generation reports itself as unavailable, which is a
normal state. If you do set it, **you must also set `CLIPAH_FAL_WEBHOOK_BASE_URL`** — the
configuration refuses a key without an origin and an origin without a key. It must be a bare
HTTPS origin with no path or query.

```env
CLIPAH_FAL_API_KEY=<https://fal.ai/dashboard/keys>
CLIPAH_FAL_WEBHOOK_BASE_URL=https://clipah.test
```

Generated video stays disabled behind `CLIPAH_GENERATIVE_VIDEO_ENABLED=false` until a
deployment turns it on deliberately. Runway is an optional second video provider and needs
all three of `CLIPAH_RUNWAY_API_SECRET`, `CLIPAH_RUNWAY_VIDEO_MODEL_ALIAS`, and
`CLIPAH_RUNWAY_VIDEO_MODEL_ID`, or none of them. `docs/operations/generative-media.md`
covers costs, quotas, safety, and how to turn generation off in a hurry.

**No configuration path accepts a model alias or provider model ID containing `sora`, in any
capitalization.** Startup fails rather than accepting one.

### Social publishing (optional, gated per provider)

Publishing is off until `CLIPAH_SOCIAL_PUBLISHING_ENABLED` is true, and each provider is off
until its own flag is true *and* its complete OAuth configuration is present. TikTok Direct
Post stays behind `CLIPAH_TIKTOK_AUDIT_APPROVED` and falls back to an official draft upload
until a real audit approves it. `docs/operations/social-publishing.md` holds the consent,
privacy, disclosure, and review obligations that no flag may bypass.

Secrets are encrypted at rest. `CLIPAH_SOCIAL_SECRET_BACKEND` chooses between a local
wrapping key (`CLIPAH_SECRET_ENCRYPTION_KEY`) and AWS KMS (`CLIPAH_AWS_KMS_KEY_ARN` and
`CLIPAH_AWS_REGION`). Production requires `CLIPAH_SECRET_ENCRYPTION_ENABLED`.

### Source imports

Public YouTube import needs no credential. `CLIPAH_YOUTUBE_API_KEY` is only used for
metadata lookups, and authenticated imports stay behind
`CLIPAH_AUTHENTICATED_SOURCE_IMPORT_ENABLED`.

The public import adapter runs shell-free with a sanitized environment and no certificate
bypass, and it needs no credential at all.

**There is no environment variable that holds cookie material, and there never will be.**
When authenticated import is enabled, a member's credential is leased per import and written
`0600` inside that Job's own workspace, then removed however the import ends.
`--cookies-from-browser` is not offered: on a hosted worker the browser profile belongs to
the machine rather than to the member.

## Step 4: Install and migrate

```bash
pnpm install                       # from the repository root; installs the frontend
cd backend && uv sync --all-extras
uv run alembic upgrade head
```

## Step 5: Run it

```bash
# terminal one, from backend/
CLIPAH_ENVIRONMENT=local uv run uvicorn clipah.runtime.api:app --host 127.0.0.1 --port 8000
```

```bash
# terminal two, from backend/ — one worker covering every queue is enough locally
CLIPAH_ENVIRONMENT=local uv run celery --app clipah.runtime.worker:app worker \
  --queues source_import,ingest,ai,broll_retrieve,broll_generate,render,social_rendition,social_publish,social_reconcile,maintenance \
  --loglevel INFO
```

```bash
# terminal three, from the repository root
NEW_CLIPAH_ENABLED=true pnpm dev
```

Open <http://localhost:3000>. Check the API on its own with
`curl http://127.0.0.1:8000/health/ready` — it answers `503` when a dependency it needs is
unreachable, which is the fastest way to find a compose service that did not come up.

**`NEW_CLIPAH_ENABLED` is required for the UI to appear at all.** It is the cutover flag and
it fails closed: without the exact word `true`, every product route answers `404` while
`/api/*` keeps working. `docs/operations/cutover.md` explains why.

## Step 6: Confirm the configuration loads

```bash
cd backend
CLIPAH_ENVIRONMENT=local uv run python -c "
from clipah.config import Settings
s = Settings()
print('redirect  :', s.google_oidc_redirect_uri)
print('google    :', bool(s.google_oidc_client_id))
print('pipeline  :', bool(s.assemblyai_api_key), bool(s.groq_api_key))
"
```

A `ValidationError` here is the configuration refusing to start on something it cannot
honour — a half-configured provider, a bound that cannot be satisfied — rather than failing
later against a real request. The message names the setting.

## Verification gates

All four backend commands run from `backend/`, and all four must pass:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q --cov=clipah --cov-fail-under=90
```

A change touching `frontend/` runs these four from the repository root:

```bash
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Settings all show defaults; `.env` seems ignored | `CLIPAH_ENVIRONMENT=local` is not exported in the shell. |
| Every page is `404` but `/api` works | `NEW_CLIPAH_ENABLED` is not exactly `true`. |
| `redirect_uri_mismatch` from Google | The registered URI differs from `CLIPAH_GOOGLE_OIDC_REDIRECT_URI`. Compare literally, including port and trailing slash. |
| `access_denied`, or "app not verified" | Your account is not in the consent screen's Test users. |
| Sign-in appears to work, then you are signed out | You reached the API's port directly instead of `localhost:3000`. |
| `fal credentials require complete webhook and model configuration` | `CLIPAH_FAL_API_KEY` is set without `CLIPAH_FAL_WEBHOOK_BASE_URL`. |
| Uploads answer `503` | The MinIO bucket was never created. See Step 1. |
| Tests cannot reach Postgres | The compose stack is not up, or a previous stack is holding the ports. |

## Security notes

- Never commit `backend/.env`. `.env*` is already gitignored.
- Secrets are never stored in a recoverable form where a hash will do. Session tokens are
  stored SHA-256-only; provider tokens are encrypted.
- Provider credentials, raw payloads, and ephemeral output URLs never reach Postgres, Redis,
  logs, Job events, or an API response.
- Use different credentials for local, staging, and production.

---

# The complete inventory

Every variable Clipah reads. `backend/tests/unit/test_environment_documentation.py` fails
the build when a setting exists in `config.py` and appears nowhere below, and when a name
below is read by nothing, so this table cannot quietly go stale.

Defaults shown are the local defaults. Production validation is stricter: it refuses debug,
requires secret encryption, requires the `__Host-` session cookie prefix over HTTPS, and
requires HTTPS origins.

## Identity, session, and process

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLIPAH_ENVIRONMENT` | `local` | `local`, `staging`, or `production`. Selects the validation profile. |
| `CLIPAH_PROCESS_ROLE` | `api` | `api` or `worker`. Each role is refused the other's database URL. |
| `CLIPAH_DEBUG` | `True` | Refused outright in production. |
| `CLIPAH_LOG_LEVEL` | `INFO` | |
| `CLIPAH_FRONTEND_ORIGIN` | — | The origin the browser uses. Must be HTTPS in production. |
| `CLIPAH_GOOGLE_OIDC_CLIENT_ID` | — | Required to sign in. |
| `CLIPAH_GOOGLE_OIDC_CLIENT_SECRET` | — | Required to sign in. |
| `CLIPAH_GOOGLE_OIDC_REDIRECT_URI` | — | Required to sign in. Must be HTTPS in production. |
| `CLIPAH_SESSION_SECRET` | — | At least 32 characters. |
| `CLIPAH_SESSION_COOKIE_NAME` | `clipah_session` | Must be `__Host-clipah_session` in production. |
| `CLIPAH_SESSION_COOKIE_SECURE` | `False` | Must be true in production. |
| `CLIPAH_SESSION_COOKIE_SAMESITE` | `lax` | |
| `CLIPAH_SESSION_IDLE_TTL_MINUTES` | `10080` | |
| `CLIPAH_SESSION_ABSOLUTE_TTL_MINUTES` | `43200` | |
| `CLIPAH_SESSION_RECENT_AUTH_TTL_MINUTES` | `10` | The window irreversible actions require. |

## Data stores

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLIPAH_DATABASE_URL` | — | The API's least-privilege login. A worker process is refused this. |
| `CLIPAH_WORKER_DATABASE_URL` | — | The worker's least-privilege login. An API process is refused this. |
| `CLIPAH_MIGRATION_DATABASE_URL` | — | The migration owner, used by Alembic only. |
| `CLIPAH_REDIS_URL` | — | Rate limits, job-event wakeups, and the Celery broker. |
| `CLIPAH_OBJECT_STORE_ENDPOINT` | — | S3-compatible endpoint. |
| `CLIPAH_OBJECT_STORE_BUCKET` | — | |
| `CLIPAH_OBJECT_STORE_ACCESS_KEY_ID` | — | |
| `CLIPAH_OBJECT_STORE_SECRET_ACCESS_KEY` | — | |

## Secrets at rest

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLIPAH_SECRET_ENCRYPTION_ENABLED` | `False` | Required in production. |
| `CLIPAH_SECRET_ENCRYPTION_KEY` | — | Local wrapping key. |
| `CLIPAH_SOCIAL_SECRET_BACKEND` | `local` | `local` or `aws_kms`. |
| `CLIPAH_AWS_KMS_KEY_ARN` | — | Required when the backend is KMS. |
| `CLIPAH_AWS_REGION` | — | Required when the backend is KMS. |

## Limits, quotas, and admission

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLIPAH_READ_REQUESTS_PER_MINUTE` | `60` | Per User. |
| `CLIPAH_WRITE_REQUESTS_PER_MINUTE` | `20` | Per User. |
| `CLIPAH_ANALYSES_PER_HOUR` | `3` | Per User. |
| `CLIPAH_CONCURRENT_JOBS_PER_WORKSPACE` | `5` | Enforced under an advisory lock. |
| `CLIPAH_MONTHLY_ANALYSES` | `30` | Per Workspace. |
| `CLIPAH_MONTHLY_STOCK_REQUESTS` | `200` | Per Workspace. |
| `CLIPAH_MONTHLY_GENERATED_IMAGES` | `50` | Per Workspace. |
| `CLIPAH_MONTHLY_GENERATED_VIDEOS` | `10` | Per Workspace. |
| `CLIPAH_MONTHLY_GENERATED_SECONDS` | `300` | Per Workspace. |
| `CLIPAH_MONTHLY_SOCIAL_PUBLICATIONS` | `100` | Per Workspace. |

## Transcription and highlight analysis

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLIPAH_ASSEMBLYAI_API_KEY` | — | Transcription and diarization. |
| `CLIPAH_GROQ_API_KEY` | — | Extraction and reranking. |
| `CLIPAH_GROQ_EXTRACTION_MODEL` | `openai/gpt-oss-20b` | Refused if retired. |
| `CLIPAH_GROQ_RERANKING_MODEL` | `openai/gpt-oss-120b` | Refused if retired. |
| `CLIPAH_PROVIDER_SHUTDOWNS` | empty | Model retirement dates the configuration honours. |
| `CLIPAH_ANALYSIS_WINDOW_TARGET_MIN_MS` | `120000` | |
| `CLIPAH_ANALYSIS_WINDOW_TARGET_MAX_MS` | `180000` | |
| `CLIPAH_ANALYSIS_WINDOW_OVERLAP_MS` | `20000` | |
| `CLIPAH_ANALYSIS_WINDOW_SILENCE_GAP_MS` | `1200` | Preferred cut point. |
| `CLIPAH_ANALYSIS_WINDOW_MIN_WORDS` | `25` | Below this a transcript yields no windows. |
| `CLIPAH_ANALYSIS_CANDIDATE_MIN_DURATION_MS` | `20000` | |
| `CLIPAH_ANALYSIS_CANDIDATE_MAX_DURATION_MS` | `90000` | |
| `CLIPAH_ANALYSIS_DEDUPLICATION_TEMPORAL_IOU` | `0.65` | |
| `CLIPAH_ANALYSIS_DEDUPLICATION_EXCERPT_COSINE` | `0.9` | |
| `CLIPAH_ANALYSIS_CANDIDATES_KEPT` | `30` | Stored. |
| `CLIPAH_ANALYSIS_CANDIDATES_EXPOSED` | `10` | Shown for review. |

## B-roll planning and retrieval

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLIPAH_PEXELS_API_KEY` | — | Optional stock provider. |
| `CLIPAH_PIXABAY_API_KEY` | — | Optional stock provider. |
| `CLIPAH_BROLL_MIN_SHOT_MS` | `2000` | |
| `CLIPAH_BROLL_MAX_SHOT_MS` | `5000` | |
| `CLIPAH_BROLL_HOOK_GUARD_MS` | `3000` | Opening protected from cutaways. |
| `CLIPAH_BROLL_MIN_CONFIDENCE` | `0.5` | |
| `CLIPAH_BROLL_MIN_RELEVANCE` | `0.5` | |
| `CLIPAH_BROLL_REPETITION_PENALTY` | `0.15` | |
| `CLIPAH_BROLL_MIN_ASSET_WIDTH` | `320` | |
| `CLIPAH_BROLL_MIN_ASSET_HEIGHT` | `320` | |
| `CLIPAH_BROLL_MAX_ASPECT_RATIO` | `2.0` | |
| `CLIPAH_BROLL_MAX_PROVIDER_REQUESTS` | `2` | Per plan. |
| `CLIPAH_BROLL_SUFFICIENT_LOCAL_RESULTS` | `4` | Stops before reaching a provider. |

## Generated media

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLIPAH_GENERATIVE_VIDEO_ENABLED` | `False` | |
| `CLIPAH_GENERATED_AUDIO_ENABLED` | `False` | |
| `CLIPAH_GENERATED_IMAGE_PROVIDER` | `fal` | |
| `CLIPAH_GENERATED_VIDEO_PROVIDER` | `fal` | |
| `CLIPAH_FAL_API_KEY` | — | Requires the webhook origin. |
| `CLIPAH_FAL_WEBHOOK_BASE_URL` | — | Bare HTTPS origin. |
| `CLIPAH_FAL_IMAGE_MODEL_ALIAS` | `image-default` | |
| `CLIPAH_FAL_IMAGE_MODEL_ID` | `fal-ai/nano-banana-2` | |
| `CLIPAH_FAL_VIDEO_MODEL_ALIAS` | `video-default` | |
| `CLIPAH_FAL_VIDEO_MODEL_ID` | Kling v2.6 Pro | |
| `CLIPAH_RUNWAY_API_SECRET` | — | All three Runway values or none. |
| `CLIPAH_RUNWAY_VIDEO_MODEL_ALIAS` | — | |
| `CLIPAH_RUNWAY_VIDEO_MODEL_ID` | — | |
| `CLIPAH_GENERATION_MAX_DURATION_MS` | `5000` | |
| `CLIPAH_GENERATION_MAX_OUTPUT_BYTES` | `100000000` | |
| `CLIPAH_GENERATION_ESTIMATE_TOKEN_TTL_SECONDS` | `300` | Confirmation token lifetime. |
| `CLIPAH_GENERATION_HTTP_TIMEOUT_SECONDS` | `30.0` | |
| `CLIPAH_GENERATION_POLL_SECONDS` | `5.0` | |
| `CLIPAH_GENERATION_POLL_ATTEMPT_DEADLINE_SECONDS` | `60.0` | |
| `CLIPAH_GENERATION_ADAPTER_RETRY_COUNT` | `3` | |
| `CLIPAH_GENERATION_CIRCUIT_FAILURE_THRESHOLD` | `5` | |
| `CLIPAH_GENERATION_CIRCUIT_COOLDOWN_SECONDS` | `60.0` | |

## Source imports

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLIPAH_AUTHENTICATED_SOURCE_IMPORT_ENABLED` | `False` | |
| `CLIPAH_YOUTUBE_API_KEY` | — | Metadata lookups only. |

## Social publishing

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLIPAH_SOCIAL_PUBLISHING_ENABLED` | `False` | The master gate. |
| `CLIPAH_MULTI_DESTINATION_SCHEDULING_ENABLED` | `False` | Requires the master gate. |
| `CLIPAH_YOUTUBE_PUBLISHING_ENABLED` | `False` | |
| `CLIPAH_YOUTUBE_OAUTH_CLIENT_ID` | — | |
| `CLIPAH_YOUTUBE_OAUTH_CLIENT_SECRET` | — | |
| `CLIPAH_YOUTUBE_OAUTH_REDIRECT_URI` | — | |
| `CLIPAH_YOUTUBE_API_VERSION` | `v3` | Retired versions are refused. |
| `CLIPAH_YOUTUBE_AUDIT_APPROVED` | `False` | |
| `CLIPAH_INSTAGRAM_PUBLISHING_ENABLED` | `False` | |
| `CLIPAH_INSTAGRAM_OAUTH_CLIENT_ID` | — | |
| `CLIPAH_INSTAGRAM_OAUTH_CLIENT_SECRET` | — | |
| `CLIPAH_INSTAGRAM_OAUTH_REDIRECT_URI` | — | |
| `CLIPAH_INSTAGRAM_API_VERSION` | `v22.0` | |
| `CLIPAH_INSTAGRAM_AUDIT_APPROVED` | `False` | |
| `CLIPAH_TIKTOK_PUBLISHING_ENABLED` | `False` | |
| `CLIPAH_TIKTOK_CLIENT_KEY` | — | |
| `CLIPAH_TIKTOK_CLIENT_SECRET` | — | |
| `CLIPAH_TIKTOK_OAUTH_REDIRECT_URI` | — | |
| `CLIPAH_TIKTOK_API_VERSION` | `v2` | |
| `CLIPAH_TIKTOK_AUDIT_APPROVED` | `False` | Direct Post stays a draft upload until this is true. |

## Jobs, rendering, and retention

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLIPAH_JOB_EVENT_POLL_SECONDS` | `1.0` | |
| `CLIPAH_JOB_EVENT_HEARTBEAT_SECONDS` | `15.0` | Server-Sent Events keepalive. |
| `CLIPAH_RENDER_WATERMARK_TEXT` | — | Rendered as text, never as filter syntax. |
| `CLIPAH_RETENTION_SWEEP_INTERVAL_SECONDS` | `300.0` | |
| `CLIPAH_RETENTION_BATCH_SIZE` | `50` | |
| `CLIPAH_RETENTION_LISTING_PAGE_SIZE` | `500` | |
| `CLIPAH_RETENTION_MAX_FAILURES` | `10` | |
| `CLIPAH_RETENTION_SOFT_DELETED_PROJECT_DAYS` | `30` | Recovery window. |
| `CLIPAH_RETENTION_SOFT_DELETED_WORKSPACE_DAYS` | `30` | Recovery window. |
| `CLIPAH_RETENTION_DELETED_USER_DAYS` | `30` | |
| `CLIPAH_RETENTION_ABANDONED_UPLOAD_HOURS` | `24` | |
| `CLIPAH_RETENTION_FAILED_JOB_WORKSPACE_DAYS` | `7` | Diagnostics kept this long. |
| `CLIPAH_RETENTION_UNSELECTED_STOCK_PREVIEW_HOURS` | `24` | |
| `CLIPAH_RETENTION_REJECTED_GENERATED_DRAFT_HOURS` | `24` | |

## Collaboration and observability

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLIPAH_COLLABORATION_ENABLED` | `False` | |
| `CLIPAH_SENTRY_DSN` | — | |
| `CLIPAH_OTEL_EXPORTER_ENDPOINT` | — | |
| `CLIPAH_OTEL_SERVICE_NAME` | `clipah` | |

## Variables read by a process, an image, or a test

These are not `Settings` fields. They configure a container, a script, or a test run.

| Variable | Read by | Purpose |
| --- | --- | --- |
| `NEW_CLIPAH_ENABLED` | `frontend/middleware.ts` | The cutover flag. Fails closed. |
| `CLIPAH_API_ORIGIN` | `frontend/next.config.mjs` | Where `/api/*` is rewritten to. |
| `CLIPAH_FORWARDED_ALLOW_IPS` | the API start command | Trusted proxy addresses. |
| `CLIPAH_IMAGE_TARGET` | the deploy configuration | Which image stage a service runs. |
| `CLIPAH_RUNTIME_ROLE` | the container entrypoint | Which readiness checks run at start. |
| `CLIPAH_MEDIA_RUNTIME_REQUIRED` | the container entrypoint | Whether FFmpeg and libmagic must prove their versions. |
| `CLIPAH_WORKER_QUEUES` | the worker entrypoint | Queues this worker consumes. |
| `CLIPAH_WORKER_CONCURRENCY` | the worker start command | |
| `CLIPAH_SOURCE_IMPORT_CONCURRENCY` | compose | Per-queue local concurrency. |
| `CLIPAH_INGEST_AI_CONCURRENCY` | compose | |
| `CLIPAH_BROLL_CONCURRENCY` | compose | |
| `CLIPAH_RENDER_CONCURRENCY` | compose | |
| `CLIPAH_SOCIAL_PUBLISH_CONCURRENCY` | compose | |
| `CLIPAH_SOCIAL_RECONCILE_CONCURRENCY` | compose | |
| `CLIPAH_JOB_WORKSPACE_ROOT` | the job workspace module | Root of per-Job working directories. Must be a `0700` directory owned by the process user. |
| `CLIPAH_SECRET_MANAGER_KEY_NAME` | the secret backend | |
| `CLIPAH_SMOKE_IDENTITY` | `scripts/verify-runtime.sh` | The fixed identity the container smoke replays. |
| `CLIPAH_DOCKER_BIN` | `scripts/verify-runtime.sh` | Overrides the `docker` executable. |
| `CLIPAH_CURL_BIN` | the cutover smoke scripts | Overrides the `curl` executable. |
| `CLIPAH_SMOKE_SESSION_COOKIE` | `scripts/new-stack-smoke.sh` | Cookie header for a signed-in Session. Kept out of the command line so it stays out of shell history. |
| `CLIPAH_SMOKE_CSRF_TOKEN` | `scripts/new-stack-smoke.sh` | Double-submit token echoed on unsafe requests. |
| `CLIPAH_TEST_DATABASE_URL` | the test suite | Migration-owner connection. |
| `CLIPAH_TEST_API_RUNTIME_DATABASE_URL` | the test suite | |
| `CLIPAH_TEST_WORKER_RUNTIME_DATABASE_URL` | the test suite | |
| `CLIPAH_TEST_REDIS_URL` | the test suite | |
| `CLIPAH_E2E_BASE_URL` | the Playwright suite | |

Beyond these, several families of variables exist only to opt in to work that is skipped by
default and never runs in an ordinary test run: the live provider smoke tests, the sandbox
credentials for each social provider, the transcription evaluation adapters, the editor
bake-off gates, and the load-test parameters. Each is read in exactly one place, and the
test or script that reads it skips loudly when it is absent rather than passing quietly.
