# PROGRESS.md

Tracks the Clipah rebuild against Section 11 of `plan.md`. Tasks run in order; each one is
complete only when its own checkboxes pass and all four gates in `AGENTS.md` are green.

**Current position:** Task 6 is complete. **Task 7 is the next task to start.**

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
| 6 | Implement the S3-compatible object-store module and multipart uploads | `[x]` | pending repository-owner commit |
| 7 | Add backend rate limits, quotas, and concurrent-job admission | `[ ]` | — |
| 8 | Implement durable jobs, events, cancellation, and Celery integration | `[ ]` | — |
| 9 | Replace shared working files with secure per-job workspaces | `[ ]` | — |

## Phase B — Durable media and AI pipeline (Tasks 10-16)

Exit when a fixture video becomes ranked candidates without rendering final clips and
retries do not duplicate records or artifacts.

| # | Task | Status |
| --- | --- | --- |
| 10 | Implement safe YouTube imports and source validation | `[ ]` |
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

Pending repository-owner commit. Added server-generated tenant keys, a provider-neutral
`ObjectStore` protocol, deterministic fake, boto3-backed S3/MinIO adapter, and CSRF-protected
create/sign/complete/abort routes. Migration `0004` persists display/content metadata and enforces
the 2 GiB boundary. Durable transitions lock upload and active Project rows, canonicalize completion
parts, sanitize provider invalid-part errors, preserve exact-key cleanup, and return five-minute
download URLs without exposing private keys or provider upload IDs. The real MinIO suite covers the
full required lifecycle/error matrix. Final verification: 301 tests passed with 96.55% coverage;
Ruff check, Ruff format check, strict mypy, migration downgrade/upgrade/drift, and
`git diff --check` all passed.

## Deferrals

Work deliberately left for the task that owns it, recorded so it is not mistaken for an
oversight.

| Deferred | Owner |
| --- | --- |
| Workspace invites, role mutation, member removal, ownership transfer | Task 35 (`plan.md:1588-1595`) |
| Workspace delete and restore endpoints | Task 45 |
| Reconcile provider multipart uploads orphaned by a crash or late database failure before a durable upload row exists | Tasks 8 and 45 |
| Bind readiness to a real configured object-store probe instead of the current no-op default | Tasks 44 and 46 |
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
