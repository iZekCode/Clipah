# PROGRESS.md

Tracks the Clipah rebuild against Section 11 of `plan.md`. Tasks run in order; each one is
complete only when its own checkboxes pass and all four gates in `AGENTS.md` are green.

**Current position:** Task 10 is complete. **Task 11 is next.**

Legend: `[x]` landed · `[~]` in progress · `[ ]` not started

## Phase A — Safety foundation (Tasks 1-9)

Exit when personal/team Workspaces, isolated projects, authentication, object storage, RLS,
and durable jobs work without invoking AI or rendering.

| # | Task | Status | Commits |
| --- | --- | --- | --- |
| 1 | Create the locked backend workspace and quality gates | `[x]` | `73c8b43`, `55b151c` |
| 2 | Establish the FastAPI application and stable error contract | `[x]` | `8b00b18`, `8ce15b6` |
| 3 | Add Postgres persistence and the initial schema | `[x]` | `259ff93`, `a222c1f`, `3348aa9` |
| 4 | Implement Login Identities, Sessions, Workspaces, and authorization dependencies | `[x]` | `c29df19`, `15aef0c`, `8fbe92d` |
| 5 | Implement project use cases and idempotent create/update/delete routes | `[x]` | `fd55d5e` |
| 6 | Implement the S3-compatible object-store module and multipart uploads | `[x]` | `a60342e` |
| 7 | Add backend rate limits, quotas, and concurrent-job admission | `[x]` | `5c041a4` |
| 8 | Implement durable jobs, events, cancellation, and Celery integration | `[x]` | `46d07d8` |
| 9 | Replace shared working files with secure per-job workspaces | `[x]` | `011f9c0` |

## Phase B — Durable media and AI pipeline (Tasks 10-16)

Exit when a fixture video becomes ranked candidates without rendering final clips and
retries do not duplicate records or artifacts.

| # | Task | Status |
| --- | --- | --- |
| 10 | Implement safe YouTube imports and source validation | `[x]` (pending owner commit) |
| 11 | Implement ffprobe validation, proxy generation, and ingest orchestration | `[ ]` |
| 12 | Implement one-pass transcription with real diarization | `[ ]` |
| 13 | Implement transcript windowing and candidate extraction schemas | `[ ]` |
| 14 | Implement structured LLM extraction, deduplication, and global reranking | `[ ]` |
| 15 | Add the versioned highlight evaluation harness | `[ ]` |
| 16 | Expose analysis and ranked candidate endpoints | `[ ]` |

## Phase C — Product dashboard and basic editor (Tasks 17-23)

Exit when members can navigate the full Workspace/project shell, upload, review candidates,
complete the editor-engine bake-off, trim/crop/style captions, and autosave one edit.

| # | Task | Status |
| --- | --- | --- |
| 17 | Move the product UI into one clean Next.js frontend | `[ ]` |
| 18 | Build authentication and project dashboard UX | `[ ]` |
| 19 | Build resumable upload and safe YouTube import UX | `[ ]` |
| 20 | Build ranked clips review UX | `[ ]` |
| 21 | Run the editor-engine bake-off and record the adoption decision | `[ ]` |
| 22 | Implement composition validation and immutable edit revisions | `[ ]` |
| 23 | Build the basic non-destructive editor and autosave | `[ ]` |

## Phase D — Advanced editor parity (Tasks 24-26)

| # | Task | Status |
| --- | --- | --- |
| 24 | Implement render-plan compilation and safe FFmpeg export | `[ ]` |
| 25 | Add complete timeline, asset, sound, text, and scene editing | `[ ]` |
| 26 | Add styling, karaoke, keyframes, templates, motion, and smart crop | `[ ]` |

## Phase E — Source connections, B-roll, and differentiated workflows (Tasks 27-35)

| # | Task | Status |
| --- | --- | --- |
| 27 | Add feature-flagged authenticated YouTube connections | `[ ]` |
| 28 | Model semantic beats and generate deterministic B-roll plans | `[ ]` |
| 29 | Retrieve, license, and rerank user-owned and stock B-roll | `[ ]` |
| 30 | Integrate editable B-roll suggestions into the clip editor | `[ ]` |
| 31 | Add quota-aware generated-media fallback | `[ ]` |
| 32 | Add context-safe clip variants and platform packaging | `[ ]` |
| 33 | Add brand kits, reusable templates, and moment-to-campaign outputs | `[ ]` |
| 34 | Build the searchable creator content library | `[ ]` |
| 35 | Add Workspace collaboration, project review, and accessibility quality gates | `[ ]` |

## Phase F — Workspace social publishing (Tasks 36-43)

| # | Task | Status |
| --- | --- | --- |
| 36 | Implement Social Account connections and encrypted OAuth Grants | `[ ]` |
| 37 | Build the Publication domain, state machine, scheduler, and idempotency foundation | `[ ]` |
| 38 | Build immutable provider renditions and publication preflight | `[ ]` |
| 39 | Implement the official YouTube Shorts publishing adapter | `[ ]` |
| 40 | Implement the official Instagram Reels publishing adapter | `[ ]` |
| 41 | Implement TikTok draft fallback and audited Direct Post adapter | `[ ]` |
| 42 | Complete multi-destination scheduling, dispatch, and reconciliation | `[ ]` |
| 43 | Build Connections, publishing dashboard, composer, history, and rollout gates | `[ ]` |

## Phase G — Production hardening and cutover (Tasks 44-48)

| # | Task | Status |
| --- | --- | --- |
| 44 | Add structured observability, provider usage, and operational dashboards | `[ ]` |
| 45 | Implement retention, Workspace/project recovery, and account deletion | `[ ]` |
| 46 | Containerize local and production processes with pinned media tooling | `[ ]` |
| 47 | Add CI, security scanning, load tests, and recovery drills | `[ ]` |
| 48 | Migrate, cut over, remove legacy behavior, and update product documentation | `[ ]` |

## What each completed task actually delivered

### Task 1 — Locked backend workspace and quality gates

`73c8b43`, `55b151c`. `backend/` as a `uv`-managed package pinned to Python 3.13 with a
committed lockfile; ruff lint and format, strict mypy over `src/clipah`, and pytest with
`--strict-markers`, declared `unit`/`integration`/`slow` markers, a 15-second per-test
timeout, and branch coverage failing under 90%. Configuration is fail-closed pydantic
settings under the `CLIPAH_` prefix: a deployment missing required key material refuses to
start rather than falling back to a weak default.

### Task 2 — FastAPI application and stable error contract

`8b00b18`, `8ce15b6`. An application factory with dependencies injected as frozen
dataclasses (`ReadinessProbes`, later `AuthComponents`), an `X-Request-ID` middleware, and a
single sanitized error envelope `{"error": {"code", "message", "requestId"}}`. Public
messages come from a fixed table, so exception text, SQL, and stack traces never reach a
client.

### Task 3 — Postgres persistence and the initial schema

`259ff93`, `a222c1f`, `3348aa9`. Migration `0001` creates the durable domain schema: every
tenant-scoped table carries `workspace_id`, and child rows reference their parent through a
composite `(workspace_id, id)` foreign key so no row can be re-parented across Workspaces.
Row-level security is enabled on all tenant tables with the predicate

```
workspace_id = NULLIF(current_setting('clipah.workspace_id', true), '')::uuid
AND NULLIF(current_setting('clipah.user_id', true), '')::uuid IS NOT NULL
```

applied as both `USING` and `WITH CHECK`. Least-privilege database roles follow: `NOLOGIN`
group roles `clipah_api` and `clipah_worker`, `LOGIN` runtime roles `clipah_api_runtime`
and `clipah_worker_runtime`, and the migration owner `clipah_migrator`. Each transaction
issues `SET LOCAL ROLE` and sets its tenant GUCs transaction-locally.

### Task 4 — Login Identities, Sessions, Workspaces, and authorization

Split into three parts.

**4a — Login Identities and Sessions (`c29df19`).** Google OIDC Authorization Code with
PKCE S256, a per-ceremony `state` and `nonce`, and an injectable provider so the flow is
testable offline. Sessions are opaque 256-bit tokens stored SHA-256-only with idle and
absolute expiry plus a ten-minute recent-authentication window.

**4b — Browser auth surface (`15aef0c`).** The login, callback, session, and logout routes;
the `__Host-clipah_session` cookie; the OIDC ceremony sealed into a cookie with Fernet; and
CSRF protection combining a same-origin proof (`Origin`, falling back to `Referer`) with a
JS-readable double-submit cookie echoed in `X-CSRF-Token`, required on POST/PUT/PATCH/DELETE.

**4c — Workspaces and the authorization matrix (`8fbe92d`).** Personal and team Workspace
creation, Workspace selection, and the `owner` ⊃ `admin` ⊃ `editor` ⊃ `reviewer` ⊃ `viewer`
matrix in `workspaces/authorization.py`. `PUBLISH` sits deliberately outside the matrix and
is resolved by each Workspace's configurable `publishing_role_policy`
(`owner_admin_editor` or `owner_admin`). Routes declare their authority through the
`require_workspace(action)` dependency factory, which proves Membership, enforces the
recent-auth window for irreversible actions, and installs the tenant RLS context in the same
call. Migration `0002` relaxes only the *read* predicate on `workspace_memberships` so
`GET /workspaces` can work before a tenant is chosen; `WITH CHECK` stays strict, so nobody
can enrol themselves into a guessed Workspace. Contract tests prove a non-member receives
the same 404 — same code and same public message — for a guessed UUID as for a nonexistent
one, and that every request and worker transaction ends holding the expected
`clipah.workspace_id` and `clipah.user_id`.

### Task 5 — Workspace-scoped Projects and idempotent routes

`fd55d5e`. Added create, list, get, rename, soft-delete, and restore routes
behind the Workspace authorization dependency; Project SQLAlchemy details remain private to the
repository module. Keyset pagination orders by `(created_at, id)` and excludes soft-deleted
Projects. Soft deletion preserves the Project workflow status through recovery for 30 days.
Migration `0003` adds RLS-protected, Workspace-wide idempotency records with nullable actor
attribution, API-only least-privilege grants, deterministic concurrent replay/conflict behavior,
and no worker access. Final verification: 290 tests passed with 97.68% coverage; Ruff check,
Ruff format check, strict mypy, migration downgrade/upgrade, and `git diff --check` all passed.

### Task 6 — Isolated multipart media storage

`a60342e`. Added server-generated tenant keys, a provider-neutral
`ObjectStore` protocol, deterministic fake, boto3-backed S3/MinIO adapter, and CSRF-protected
create/sign/complete/abort routes. Migration `0004` persists display/content metadata and enforces
the 2 GiB boundary. Durable transitions lock upload and active Project rows, canonicalize completion
parts, sanitize provider invalid-part errors, preserve exact-key cleanup, and return five-minute
download URLs without exposing private keys or provider upload IDs. The real MinIO suite covers the
full required lifecycle/error matrix. Final verification: 301 tests passed with 96.55% coverage;
Ruff check, Ruff format check, strict mypy, migration downgrade/upgrade/drift, and
`git diff --check` all passed.

### Task 7 — Processing limits, Workspace quotas, and job admission

`5c041a4`. Added a Redis sliding-window limiter evaluated inside one
atomic Lua script (`auth/limits.py`), so two API processes can never admit the same final
request, and a refusal reports the exact wait a client must honour. Plan limits live in
`config.py` — 60 read and 20 write requests per minute per User, 3 analyses per hour, 5
concurrent jobs per Workspace, and the six monthly Workspace budgets — never in route code.
Per-request limits are spent in `require_authenticated_user` rather than ASGI middleware,
because that is the first point that knows which User is speaking. `jobs/admission.py` counts
unfinished jobs under a Postgres advisory transaction lock before creating one more, so 50
concurrent callers admit exactly the limit. Migration `0005` adds the RLS-protected
`workspace_quota_reservations` ledger: admission holds an estimate, reconciliation settles the
real cost or releases the whole hold, and runtime roles may only update `status`,
`actual_units`, and `settled_at` — a held budget stays evidence. Refusals surface as the
sanitized `RATE_LIMITED`, `QUOTA_EXCEEDED`, and `CONCURRENCY_LIMIT` codes with a `Retry-After`
header and no balance leakage. Final verification: 325 tests passed with 96.51% coverage; Ruff
check, Ruff format check, strict mypy, and migration downgrade/upgrade/drift all passed.

### Task 8 — Durable jobs, events, cancellation, and Celery

`46d07d8`. `jobs/models.py` holds the state machine as data — the allowed
transitions, the terminal set, and the event each transition announces — so no route or task can
invent a fifth way for a job to end. `jobs/repository.py` reads jobs under `SELECT ... FOR UPDATE`
and appends events whose sequence is computed under that same lock, so two writers can never hand
out sequence 4 twice; the table is append-only by grant, not by convention. `jobs/use_cases.py`
writes each transition exactly once for the API and the worker alike, and a repeated idempotency
key returns the job the first submission created instead of admitting a second. Every event payload
carries the job's status, stage, progress, attempt, and error code, so a subscriber never needs a
second read.

`celery_app.py` is the only place that knows Celery exists: it maps every `JobKind` onto the
`source_import`, `ingest`, `ai`, `broll_retrieve`, `broll_generate`, `render`, `social_rendition`,
`social_publish`, `social_reconcile`, and `maintenance` queues, sets late acknowledgement and a
prefetch of one so a lost worker's job is redelivered rather than dropped, and stores `Settings` on
the app so tasks need no import-time configuration. `jobs/tasks.py` carries only UUID strings across
the broker — never an ORM object, Session, or token — and re-proves the caller's Workspace standing
from those identifiers before touching anything. Recoverable provider failures retry with
exponential backoff and jitter; an exhausted retry budget ends the job as `failed` rather than
holding a concurrency slot forever. `JobContext.raise_if_cancelled()` reads `cancel_requested_at` in
its own short transaction between stages, so cancelling a running job stops it at the next boundary
instead of orphaning work.

`api/routes/jobs.py` serves the job read, a CSRF-protected cancel, and the Server-Sent Events
stream. The stream subscribes before its first read, replays from `Last-Event-ID` so a reconnecting
browser loses no event, emits heartbeat comment frames through quiet periods, and closes on the
terminal event. Each poll opens its own short tenant-scoped transaction, so an open stream holds no
database connection. Wakeups travel over Redis pub/sub when configured (`jobs/events.py`, the one
file beyond the plan's list) and fall back to polling otherwise; a wakeup is only an optimization,
so an unreachable broker never fails work Postgres already recorded. Migration `0006` grants the API
role `INSERT` on `job_events` — job creation and cancelling a queued job are API-side transitions —
and no `UPDATE` or `DELETE`, so history cannot be rewritten. A cross-Workspace job identifier
returns the same 404 as a missing one on every route, including the stream. Final verification: 357
tests passed with 96.23% coverage; Ruff check, Ruff format check, strict mypy, and migration
`0006` downgrade/upgrade plus the drift check all passed.

Two frozen test clocks (`tests/harness.py` and `tests/integration/test_auth.py`) were anchored to
the present day. Postgres stamps `created_at` from the server clock and checks that expiries lie
after it, so a hardcoded past date turned into nine failing upload tests once real time caught up.

### Task 9 — Secure per-Job workspaces

Pending owner commit. `jobs/workspace.py` replaces shared working filenames with a
`job_workspace(job_id)` context manager. Every invocation creates a random-suffixed,
UUID-prefixed `0700` directory beneath `CLIPAH_JOB_WORKSPACE_ROOT` (defaulting to a dedicated
system-temporary root). The root itself must be a real `0700` directory owned by the effective
process user, so another local user cannot pre-create a permissive root and swap active Job paths.
Canonical direct-child checks reject paths outside the root, configured-root symlinks are refused,
and cleanup unlinks a replaced workspace symlink without following it. Recursive cleanup is
best-effort and can target only the exact Job directory, never its root or a parent.

Tests cover permissions, UUID scoping, normal and exceptional cleanup, nested diagnostic
collection, symlink/path replacement attacks, untrusted roots, parent preservation, cleanup
failure, and two concurrent workspaces for the same Job writing the same relative filename. Final
verification: 367 tests passed with 96.30% coverage; the workspace module has 100% line and branch
coverage; Ruff check, Ruff format check, and strict mypy all passed.

### Task 10 — Safe public YouTube imports

Pending owner commit. Strict URL normalization accepts only exact HTTPS single-video YouTube hosts
and forms, rejects deceptive authorities and playlists, validates every IPv4/IPv6 DNS result as
globally routable, and detects rebinding and unsafe redirects. Provider-neutral source contracts
keep yt-dlp details, raw errors, commands, and paths out of API and durable Job state. The public
adapter runs shell-free with a sanitized environment, process-group timeout, continuously drained
bounded output, no cookies/browser profile/certificate bypass, deterministic Job-local output, a
two-GiB ceiling, final-path containment, single-final-file enforcement, SHA-256 calculation, and
exact-key private object upload.

`POST /api/v1/projects/{project_id}/youtube-imports` now enforces authentication, CSRF, Workspace
write authority, active Project state, admission limits, and payload-bound idempotency. An advisory
lock makes concurrent identical requests converge on one `SOURCE_IMPORT` Job and SourceImport;
commit occurs before UUID-only dispatch, so broker failure leaves durable queued work that an exact
replay can redispatch. The isolated worker uses short tenant-scoped transactions around external
work, a deterministic object key and Asset ID, immutable metadata verification, cancellation
boundaries, and stable terminal/retryable source codes.

The independently buildable non-root source-import image pins its Python base and Debian snapshot,
yt-dlp `2026.08.19`, yt-dlp-ejs `0.8.0`, Deno `2.9.5`, FFmpeg/ffprobe
`7.1.5-0+deb13u1`, and immutable `uv 0.12.7`. Its isolated readiness command reported every exact
version. The environment-gated public metadata/EJS/Deno smoke test is marked `slow` and skipped by
default; it was intentionally not opted in during normal verification. Final verification: 477
tests passed, one opt-in network smoke test skipped, and coverage reached 94.00%; Ruff check, Ruff
format check, strict mypy, and the full pytest/coverage gate all passed. The two-axis standards/spec
review against `011f9c0` passed after all reported findings were resolved.

## Deferrals

Work deliberately left for the task that owns it, recorded so it is not mistaken for an
oversight.

| Deferred | Owner |
| --- | --- |
| Workspace invites, role mutation, member removal, ownership transfer | Task 35 (`plan.md:1588-1595`) |
| Workspace delete and restore endpoints | Task 45 |
| Reconcile provider multipart uploads orphaned by a crash or late database failure before a durable upload row exists | Task 45 — Task 8 supplies the `maintenance` queue this sweep will run on |
| Bind readiness to a real configured object-store probe instead of the current no-op default | Tasks 44 and 46 |
| Remaining metered job-creation routes that map `QuotaExceededError` onto the HTTP envelope; Task 10 now maps source-import concurrency admission | Tasks 11-16 |
| Real stage runners for remaining `JobKind` values; Task 10 now registers `SOURCE_IMPORT`, while unsupported remaining kinds fail with `JOB_KIND_UNSUPPORTED` | Tasks 11-16 and later pipeline tasks |
| Quota reconciliation driven from job completion, so an estimate settles against real cost when a job ends | Tasks 16 and 30-32 |
| Spending the analysis allowance from a real analysis endpoint | Task 16 |
| A foreign key for the quota reservation's `reference_kind`/`reference_id` — `publications` does not exist yet | Task 37 |
| Per-Social-Account provider publish limits, currently exercised through generic `social_account:<uuid>` limiter subjects | Task 36 |
| Settling generated video seconds and image counts against real provider usage | Tasks 30-32 |
| Everything RLS cannot express — RLS checks the declared tenant, never membership; the application proves membership before declaring it | permanent property, see `AGENTS.md` |

## Task 5 decisions and review notes

The repository owner approved a shared `idempotency_keys` table and an opaque unsigned base64
cursor over `(created_at, id)`. Task 5 implements both decisions. Idempotency identity is
Workspace-wide, actor attribution is nullable, and API privileges are limited to response
completion; workers receive no Task 5 idempotency-table access.

Final review left two non-blocking notes for later adjudication: require canonical strict
URL-safe base64 cursor decoding, and type closed Project status/source values as domain enums
instead of unrestricted strings. Neither changes the completed Task 5 contract.

## Notes

- `AGENTS.md` holds the working rules, gate commands, and local infrastructure setup.
- `CONTEXT.md` holds the canonical vocabulary.
- The legacy Flask/Next.js stack at the repository root is untouched until Task 48.
