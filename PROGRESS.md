# PROGRESS.md

Tracks the Clipah rebuild against Section 11 of `plan.md`. Tasks run in order; each one is
complete only when its own checkboxes pass and all four gates in `AGENTS.md` are green.

**Current position:** Tasks 1-43 have landed. **Task 44 is complete and awaiting the
owner's commit.** Task 45 follows.

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
| 10 | Implement safe YouTube imports and source validation | `[x]` (`f3fec26`) |
| 11 | Implement ffprobe validation, proxy generation, and ingest orchestration | `[x]` (`b434f8d`) |
| 12 | Implement one-pass transcription with real diarization | `[x]` (`b272927`) |
| 13 | Implement transcript windowing and candidate extraction schemas | `[x]` (`cedc4e9`) |
| 14 | Implement structured LLM extraction, deduplication, and global reranking | `[x]` (`efcdcfa`) |
| 15 | Add the versioned highlight evaluation harness | `[x]` (`7a51d88`) |
| 16 | Expose analysis and ranked candidate endpoints | `[x]` (`98a8636`) |

## Phase C — Product dashboard and basic editor (Tasks 17-23)

Exit when members can navigate the full Workspace/project shell, upload, review candidates,
complete the editor-engine bake-off, trim/crop/style captions, and autosave one edit.

| # | Task | Status |
| --- | --- | --- |
| 17 | Move the product UI into one clean Next.js frontend | `[x]` (`e45a3fe`) |
| 18 | Build authentication and project dashboard UX | `[x]` (`af04a94`) |
| 19 | Build resumable upload and safe YouTube import UX | `[x]` (`8e7add7`) |
| 20 | Build ranked clips review UX | `[x]` (`c4d73e3`) |
| 21 | Run the editor-engine bake-off and record the adoption decision | `[x]` (`9af7f62`, `8c0e206`; ADR Accepted — Mediabunny selected) |
| 22 | Implement composition validation and immutable edit revisions | `[x]` (`fe76bad`) |
| 23 | Build the basic non-destructive editor and autosave | `[x]` (`4bc5523`) |

## Phase D — Advanced editor parity (Tasks 24-26)

| # | Task | Status |
| --- | --- | --- |
| 24 | Implement render-plan compilation and safe FFmpeg export | `[x]` (`191f4ea`) |
| 25 | Add complete timeline, asset, sound, text, and scene editing | `[x]` (`131339f`) |
| 26 | Add styling, karaoke, keyframes, templates, motion, and smart crop | `[x]` (`ec5176e`) |

## Phase E — Source connections, B-roll, and differentiated workflows (Tasks 27-35)

| # | Task | Status |
| --- | --- | --- |
| 27 | Add feature-flagged authenticated YouTube connections | `[x]` (`a5955aa`) |
| 28 | Model semantic beats and generate deterministic B-roll plans | `[x]` (`dfa312e`) |
| 29 | Retrieve, license, and rerank user-owned and stock B-roll | `[x]` (`e81140a`) |
| 30 | Integrate editable B-roll suggestions into the clip editor | `[x]` (`42ca150`) |
| 31 | Add quota-aware generated-media fallback | `[x]` (`5ec9273`) |
| 32 | Add context-safe clip variants and platform packaging | `[x]` (`5ec9273`) |
| 33 | Add brand kits, reusable templates, and moment-to-campaign outputs | `[x]` (`80cf2f6`) |
| 34 | Build the searchable creator content library | `[x]` (`2b4a49b`) |
| 35 | Add Workspace collaboration, project review, and accessibility quality gates | `[x]` (`3c9867c`) |

## Phase F — Workspace social publishing (Tasks 36-43)

| # | Task | Status |
| --- | --- | --- |
| 36 | Implement Social Account connections and encrypted OAuth Grants | `[x]` (`548cb5d`) |
| 37 | Build the Publication domain, state machine, scheduler, and idempotency foundation | `[x]` (`6c55780`) |
| 38 | Build immutable provider renditions and publication preflight | `[x]` (`b92be20`) |
| 39 | Implement the official YouTube Shorts publishing adapter | `[x]` (`76d4c4f`) |
| 40 | Implement the official Instagram Reels publishing adapter | `[x]` (`1717039`) |
| 41 | Implement TikTok draft fallback and audited Direct Post adapter | `[x]` (`0176929`) |
| 42 | Complete multi-destination scheduling, dispatch, and reconciliation | `[x]` (`147f056`) |
| 43 | Build Connections, publishing dashboard, composer, history, and rollout gates | `[x]` (`421f2dd`) |

## Phase G — Production hardening and cutover (Tasks 44-48)

| # | Task | Status |
| --- | --- | --- |
| 44 | Add structured observability, provider usage, and operational dashboards | `[x]` (uncommitted) |
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

Landed in `011f9c0`. `jobs/workspace.py` replaces shared working filenames with a
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

Landed in `f3fec26`. Strict URL normalization accepts only exact HTTPS single-video YouTube hosts
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

### Task 11 — Durable media ingest pipeline

Landed in `b434f8d`. The registered `INGEST` runner now downloads the private source through a
five-minute signed capability, enforces observed byte and SHA-256 boundaries while streaming,
rejects libmagic MIME mismatches before invoking ffprobe, and converts complete ffprobe output into
the exact duration, stream-count, resolution, and codec limits from the plan. FFmpeg and ffprobe run
only as argument arrays in isolated process groups with fixed timeouts, bounded diagnostics,
cancellation checks, and an exact `7.1.5` startup version gate. Ingest-capable Celery workers also
prove native libmagic readiness before accepting work.

Every accepted source produces an orientation-aware, no-upscale H.264/AAC proxy bounded to 720p, a
JPEG thumbnail, and mono 16 kHz PCM transcription audio. Derivative IDs and tenant-scoped object
keys are deterministic. Uploads submit the locally computed S3 SHA-256, persist it as immutable
object metadata, and require both the provider upload response and the subsequent object metadata
read to match the local byte length and digest before database persistence. Storage transport
failures use a sanitized retryable code; malformed media and integrity failures remain terminal.
Source metadata and all three derivative rows converge in one tenant-scoped transaction, and a
complete retry reuses the existing deterministic Asset set without repeating external media work.

Checked-in landscape, portrait, missing-stream, corrupt, unsupported-codec, oversized-metadata,
and true-VFR fixtures have a deterministic generator and SHA-256 manifest. The rebuilt pinned image
includes `libmagic1t64=1:5.46-5`; its ingest readiness passed, regeneration matched every committed
fixture digest, and the checked real-media integration test passed all three landscape, portrait,
and VFR cases inside that image while inspecting proxy, JPEG, and WAV outputs. Host verification:
552 tests passed, four environment-gated tests skipped, and coverage reached 93.18%; Ruff check,
Ruff format check, strict mypy, and the full pytest/coverage gate all passed.

### Task 12 — One-pass diarized word transcripts

Landed in `b272927`. Added a provider-neutral `Transcriber` port and immutable transcript values
for canonical word IDs, millisecond timestamps, confidence, punctuation, opaque provider speaker
labels, utterances, and maximal contiguous speaker segments. Normalization preserves legitimate
speaker overlap, refuses empty or malformed provider evidence, rejects regressing or out-of-source
timestamps, and never invents punctuation or speaker identity.

The AssemblyAI adapter uses the locked 1.0.0 SDK without global API-key mutation, enables provider
speaker diarization, and issues exactly one pre-recorded transcription request. Requested English,
Spanish, German, French, Portuguese, and Italian use only `universal-3-pro`; Indonesian and other
languages outside that support set use only `universal-2`; an unspecified language uses the ordered
U3 Pro/U2 detection fallback. SDK values, signed audio capabilities, provider diagnostics, and raw
exceptions remain inside the adapter. A deterministic fake supports the normal test suite, while an
explicit environment-gated AssemblyAI contract smoke test is excluded by default.

The registered `TRANSCRIBE` runner resolves Task 11's deterministic transcription-audio Asset under
the worker's live Workspace context, performs provider and object-store work outside transactions,
stores one SHA-256-verified private raw provider JSON document, and persists normalized words,
utterances, and speaker segments atomically. Migration `0007` enforces one Transcript per source
Asset. Repeated delivery validates and reuses the canonical row without another provider call;
provider/storage outages remain sanitized retryable failures while invalid or conflicting evidence
is terminal.

Final verification: 585 tests passed, five environment-gated tests skipped, and coverage reached
92.43%. Ruff check, Ruff format check, strict mypy, the full pytest/coverage gate, migration `0007`
downgrade/upgrade, Alembic drift check, and `git diff --check` all passed. The existing Authlib
deprecation warning remains unrelated to Task 12. The provider smoke test was not opted in during
normal verification.

### Task 13 — Timestamp-safe highlight windows and candidates

Landed in `cedc4e9`. `highlights/models.py` holds the window and candidate shapes as data: a
`WindowingPolicy` (120-180 second target, 20-second overlap, silence-gap threshold, minimum word
count), a `CandidatePolicy` (the inclusive 20-90 second preset), the `ClipCategory` review filters,
the seven-dimension `ScoreBreakdown`, the strict `ClipCandidateProposal` a provider may return, and
the accepted `ClipCandidateDraft`. Both Pydantic models forbid extra fields and are frozen, so a
provider cannot smuggle free-form timestamps or unlisted attributes into durable state.

`highlights/windowing.py` is pure and deterministic. `build_windows` walks the authoritative words,
so every window is a contiguous slice that can never repeat a word ID, consecutive windows overlap
by at most the configured amount, and the windows together cover every word. Inside the target band
the cut is chosen by preference — a silence gap outranks a speaker change, which outranks a sentence
ending — and the longest qualifying window wins a tie. A transcript below the configured word count
produces no windows at all, and a final window that would fall below that count is grown backwards
rather than dropped, so the tail of a source is never lost.

`highlights/extractor.py` validates one proposal against the transcript and resolves the clip
bounds itself: unknown word IDs, reversed ranges, durations outside the preset, and excerpts that do
not match the authoritative words are all refused through the stable `CANDIDATE_UNKNOWN_WORD_ID`,
`CANDIDATE_RANGE_REVERSED`, `CANDIDATE_DURATION_OUT_OF_RANGE`, `CANDIDATE_EXCERPT_MISMATCH`, and
`CANDIDATE_SCHEMA_INVALID` codes, which carry no provider text. Excerpt comparison ignores case,
spacing, and punctuation only; the stored excerpt is always the transcript's own text. Context
warnings and dependencies survive validation verbatim. No module in this task performs provider,
network, or database work.

Final verification: 620 tests passed, five environment-gated tests skipped, and coverage reached
92.73%, with 100% line and branch coverage on the three new modules. Ruff check, Ruff format check,
strict mypy, the full pytest/coverage gate, and `git diff --check` all passed.

### Task 14 — Structured extraction, deduplication, and global reranking

`highlights/provider.py` holds the provider-neutral ports. An extraction provider may only propose
candidates keyed to the word IDs it was shown; it never resolves a timestamp and never decides
whether a proposal is valid. `ProviderCall` carries everything usage recording needs — provider,
operation, model, request ID, latency, input and output units, prompt version, and schema version —
so no SDK object ever leaves an adapter. The module also ships the offline
`DeterministicHighlightProvider`, which proposes sentence-aligned candidates from the words alone
and marks them "selected without a language model", and the `FakeHighlightProvider` used where real
provider work would be inappropriate.

`highlights/groq_adapter.py` is the only module that knows Groq exists. It sends strict JSON Schema
requests in which every property is required and `additionalProperties` is false, uses the
configured extraction model for windows and the configured reranking model for the global order,
pins temperature to zero and an explicit timeout, and spends its retry budget on transient failures
only. Provider failures are classified by status alone onto the stable
`HIGHLIGHT_PROVIDER_RATE_LIMITED`, `HIGHLIGHT_PROVIDER_UNAVAILABLE`, `HIGHLIGHT_PROVIDER_REJECTED`,
`HIGHLIGHT_PROVIDER_INVALID`, and `HIGHLIGHT_WINDOW_TOO_LARGE` codes, which carry no provider text.
`highlights/provider_router.py` proves both configured model aliases are known and unretired before
a worker starts, and falls back to the offline provider only for retryable failures.

`highlights/deduplicate.py` drops a candidate that repeats a stronger one at temporal IoU 0.65 or
excerpt cosine 0.90, walking strongest-first so the survivor is deterministic, and returns
survivors in transcript order. `highlights/rerank.py` scores the seven dimensions with explicit
weights, accepts a provider order only when it is a true permutation, and otherwise falls back to
the local ranking. Stored scores are assigned from the sorted pool of local scores, so a stored
score can never contradict its own rank. The ranking policy keeps 30 candidates and exposes 10.

`highlights/analyzer.py` drives one transcript: build windows, extract per window, validate every
proposal against the authoritative words, deduplicate, rerank globally, and rank. A window the
provider refuses is reported through `on_window_failure` and the remaining windows still complete;
the analysis fails only when fewer than three candidates survive, and that failure is retryable
exactly when a window failed transiently.

`jobs/analyze_task.py` runs the ANALYZE stage. It loads the project's sole canonical Transcript,
returns immediately when candidates already exist, records each failed window as its own durable
progress event carrying the window index and the stable error code, and inserts every ranked
candidate and every provider call in one tenant-scoped worker transaction. Because the worker holds
no DELETE grant on `clip_candidates`, a redelivery converges on the stored set rather than
rewriting it, and a concurrent insert conflict is re-checked before it is reported as
`ANALYSIS_INTEGRITY`. `ANALYZE` is now registered in `jobs/tasks.py`.

Two supporting changes were needed. Migration `0008` adds the candidate evidence the plan's
original column list omitted — `payoff`, `start_word_id`, `end_word_id`, and
`context_dependencies` — so a durable candidate carries the word IDs it was derived from rather
than timestamps alone. `update_job_progress` gained an optional `detail` payload so a failed window
can be explained in its own event instead of being flattened into a stage name.

Final verification: 697 tests passed, five environment-gated tests skipped, and coverage reached
93%, with 100% line and branch coverage on every module this task touched. Ruff check, Ruff format
check, and strict mypy all passed.

### Task 15 — Versioned highlight and transcription evaluation

`highlights/evaluation.py` scores one labeled case against a ranked result set and reports the five
quality gates the plan names: timestamp validity, duration validity, duplicate rate, context-safety
recall, and top-three acceptance. Timestamp validity is judged against the case's own words rather
than against the provider's arithmetic, so a candidate whose bounds do not reproduce the transcript
counts as invalid even when its numbers look plausible. Duplicate rate runs the production
`deduplicate` policy over the produced candidates, so the harness measures the same overlap rule the
pipeline enforces. Context-safety recall counts a labeled risky cut only when a surfaced candidate
both covers it and carries a context warning. Clip-level rates aggregate weighted by candidate
count, so a long case is not outvoted by a short one, while case-level rates aggregate by case.
`HighlightReport` carries the manifest version, adapter, provider, model, prompt version, and schema
version, so a score can always be attributed to what produced it, and it decides `passed` itself
rather than leaving the judgement to a reader.

`transcripts/evaluation.py` scores transcription with word error rate over a minimal edit path,
named-entity accuracy, median and p95 word-timestamp drift over matched words, and a diarization
error rate that maps hypothesis speakers onto reference speakers by majority, so a provider that
renames speakers consistently is charged nothing. It also records p95 completion time, failure rate,
and cost per source hour. A case that failed to transcribe is counted as total error rather than
skipped, because a provider that cannot finish has not earned a favourable average.

Both loaders are tamper-evident: `manifest.json` records a SHA-256 per case file, and a case whose
bytes do not match its digest is refused rather than scored. That makes the checked-in baseline hard
to move without saying so.

`backend/evals/` holds the fixtures and the runners. The 15 highlight cases and 15 transcription
cases are sanitized synthetic transcripts — five English, five Indonesian, five code-switched — and
`evals/generate_fixtures.py` regenerates them deterministically. The runners are wiring only:
`scripts/run-highlight-eval.sh` and `scripts/run-transcription-eval.sh` select an adapter, take one
observation per case, write the report, and exit non-zero on a gate violation. Every live adapter
(Groq for highlights; AssemblyAI, Deepgram, or WhisperX for transcription) needs an explicit
credential and, for transcription, a local directory of evaluation audio, so a live comparison is
always deliberate and never an ordinary CI dependency. The live adapters live under `backend/evals/`
rather than under `src/clipah/`, so comparing Deepgram and WhisperX adds no backend dependency.

The offline run passes: the highlight evaluation scores 93 candidates across 15 cases at 1.0
timestamp validity, 1.0 duration validity, 0.0 duplicate rate, 1.0 context-safety recall, and 1.0
top-three acceptance; the transcription evaluation scores 0.0 word error rate and 0.0 diarization
error rate. Those numbers prove the harness and the plumbing, not provider quality: the offline
provider proposes sentence-aligned candidates and warns on every candidate, and the fixture labels
are sentence-aligned the same way. The harness earns its keep against a live adapter on real audio.

Final verification: 756 tests passed, five environment-gated tests skipped, total coverage 93.99%,
and 100% line and branch coverage on both new evaluation modules. Ruff check, Ruff format check, and
strict mypy all passed.

### Task 16 — Analysis admission and ranked candidate API

The new analysis endpoint admits exactly one durable `ANALYZE` Job for a transcribed Project,
reserves the Workspace analysis quota before dispatch, spends the per-User hourly allowance only
after database concurrency and quota admission succeed, and binds one idempotency key to one
Project. Broker failure cannot erase the committed Job. Analysis completion settles the reservation;
failure or cancellation releases it, marks the Project failed, and a cancellation arriving after
the stage runner still reaches the terminal `canceled` state instead of holding capacity forever.

The analysis runner now reads windowing, candidate, deduplication, and ranking policies from
validated settings. A successful run moves the Project to `ready` after candidates are durable and
before any render artifact exists. Dispatch selects the queue from the Job kind, including analysis,
instead of routing every admitted Job through the source-import queue.

The candidate collection and detail endpoints expose only ranked review evidence through strict
Pydantic response schemas. Reads require a ready, active Project in the selected Workspace; hidden,
foreign, failed, canceled, and cross-Project candidates are indistinguishable from missing data.
Pagination uses an opaque stable cursor over `(rank, id)`, and responses omit storage keys, provider
raw output, and private model metadata.

Contract coverage includes lifecycle preconditions, idempotency binding, quota and concurrency
refusals, hourly allowance accounting, durable dispatch, terminal quota reconciliation, late
cancellation, tenant scoping, stable pagination, invalid cursors, strict OpenAPI schemas, safe fields,
and the ready-before-render invariant. Final verification: 785 tests passed, five environment-gated
tests skipped, and total coverage reached 94.11%. Ruff check, Ruff format check, and strict mypy all
passed. No commit was created; the required owner commit message is
`feat: expose ranked clip candidates`.

### Task 17 — One clean Next.js product frontend

The product UI is now the Next.js application in `frontend/`, and the repository root is a
pnpm workspace whose single package it is. The root `app/`, `components/`, `hooks/`, `lib/`,
and the Next.js configuration moved under `frontend/`; the root `styles/` directory keeps
only the TrueType fonts the legacy Flask renderer still loads. The v0 scaffold's
`ignoreDuringBuilds` and `ignoreBuildErrors` escapes are gone, so ESLint and TypeScript now
fail the build rather than being skipped by it.

Types are no longer hand-written on either side of the boundary. `scripts/export-openapi.sh`
builds the application from the same factory the API serves and writes
`contracts/openapi.json`, and `pnpm generate:api` turns that document into the TanStack Query
client under `frontend/lib/api/generated/`. Every generated call goes through `apiFetch` in
`frontend/lib/api/client.ts`, so the whole client inherits one behaviour: same-origin `/api`
paths, `credentials: 'same-origin'`, the double-submit CSRF token echoed in `X-CSRF-Token` on
unsafe methods, and failures raised as an `ApiError` carrying the backend's `code` and
`requestId`. `next.config.mjs` rewrites `/api/:path*` to `CLIPAH_API_ORIGIN`, which keeps the
Session cookie first-party and satisfies the backend's same-origin check without CORS; the
rewrite was verified end to end against a stub origin.

The landing page and the dashboard shell are rebuilt as React text rendering. No component
in the product path uses `dangerouslySetInnerHTML` — the one vendored use, the chart theme
style tag, now passes its rules as element text, and `react/no-danger` is a lint error so a
future one cannot land quietly. The subtitle and watermark controls emit real booleans and
omit the watermark text entirely when no watermark was asked for, replacing the legacy form
fields that serialized every toggle as the string `"true"` or `"false"`. The new UI depends
on neither the Tailwind CDN, the unpkg Lucide bundle, the Flask templates, nor
`static/script.js`; the redundant `lucide` package was dropped in favour of `lucide-react`.

Twelve smoke tests were written and watched fail before any of it existed: the landing page,
the authenticated dashboard shell, request-identifier error rendering, strict boolean
serialization, literal rendering of strings that contain HTML tags, and the client's cookie,
CSRF, and error-envelope behaviour including a failure body that is not the envelope.

Final verification: `pnpm lint`, `pnpm typecheck`, `pnpm test` (12 passed), and `pnpm build`
all passed, and the backend gates were re-run unchanged — Ruff check, Ruff format check,
strict mypy, and 785 tests passed with five environment-gated skips at 94.11% coverage. No
commit was created; the required owner commit message is
`refactor: make Next.js the product frontend`.


### Task 18 — Authentication and the project dashboard

The frontend now shows a Workspace instead of a placeholder. `RequireSession` reads
`/api/v1/me` and renders one of three honest states — still checking, an invitation to sign
in, or the private content — so nothing about a Session is guessed from the browser.
`WorkspaceProvider` holds the memberships the backend returned and the one Workspace being
viewed; a freshly bootstrapped User lands in their personal Workspace, and the only thing the
browser remembers is which membership was last opened, ignored as soon as it names a
Workspace the member no longer belongs to.

The route shell from Section 9 exists: `/signin`, `/demo`, `/dashboard` and its overview,
projects, project detail, clips, clip detail, assets, templates, brand kits, team,
publishing, settings, and settings/connections. `DashboardShell` marks the entry for the
route being viewed with `aria-current="page"` and keeps its navigation reachable on a narrow
screen behind one labelled control. `/demo` renders bundled example candidates and calls no
endpoint at all.

The Project library pages through the backend's cursor rather than holding a Workspace in
memory, names the processing state each Project is actually in, renames optimistically and
reverts on refusal, and offers the recovery window a soft delete leaves open. Writing
controls are hidden from members whose role cannot write, which is a courtesy on top of the
backend's refusal, never a substitute for it. Another Workspace's Project renders the same
generic not-found message as a Project that never existed.

The job center follows one Workspace-wide Server-Sent Events stream. It survives navigation,
keeps finished work in the list instead of dropping it at the moment it succeeds, deep-links
each job to its Project, and closes the connection and empties the list when the active
Workspace changes, so one Workspace's work is never visible while another is open.

Two backend gaps were found and closed inside this task, because the screen the plan asks
for cannot be answered without them:

- `GET /api/v1/dashboard/summary` answers the whole overview in one read — the Workspace and
  the caller's role, the active Project count, recent Projects, unfinished Jobs, consumption
  against each monthly budget, and the top exposed Clip Candidates. Six contract tests cover
  it, including that archived Projects, terminal Jobs, and unexposed candidates are omitted,
  that another Workspace's work never appears, and that a missing membership is refused
  exactly like a missing Workspace.
- `GET /api/v1/jobs/events` streams every Job of one Workspace. Its `id` is an opaque base64
  cursor over `(created_at, job_id, sequence)`, because `sequence` is per-Job and events
  written in one transaction share a timestamp; an unreadable cursor replays the whole
  history rather than failing. Seven contract tests cover replay, resumption, staying open
  past a terminal Job, tenant isolation, and anonymous refusal.

Strict response models were added to `/me`, `/workspaces`, `/workspaces/{id}/members`, and
the Project routes so the generated frontend types stop being `Record<string, unknown>`;
twenty contract tests assert every typed success response is a component reference that
forbids extra fields. `contracts/openapi.json` was re-exported and the client regenerated.
JSON shapes did not change.

`clipah.dev.seed` mints one signed-in Session directly for browser tests, refuses to run when
the environment is production, and prints the cookies as JSON for the Playwright runner.

Thirty-five frontend tests were written and watched fail before any feature module existed,
covering signed-out, loading, empty, populated, error, membership-safe 404, Workspace
switching, responsive navigation, active-route state, job-center persistence across
navigation, and role-gated controls.

Final verification: `pnpm lint`, `pnpm typecheck`, `pnpm test` (35 passed), and `pnpm build`
all passed; Ruff check, Ruff format check, strict mypy, and 822 backend tests passed with
five environment-gated skips at 94.32% coverage. The Playwright suite in `frontend/e2e/` was
written but not run here: it needs Postgres, the backend, the frontend, and browser binaries
running together. No commit was created; the required owner commit message is
`feat: add secure project dashboard`.



### Task 19 — Resumable upload and safe YouTube import

Direct upload is now the way media gets into a Project, and it is the first control on the
Project page. `uploader.ts` holds the rules with no React anywhere near them: a file is
refused here for the same reasons the backend would refuse it, parts are sized inside the
agreed 8-32 MiB band, at most three are in flight at once, and a part that fails is tried
again with a longer wait each time until the budget runs out.

The checkpoint is the part worth being careful about. A resumed upload remembers the upload
identifier and the ETags the object store already acknowledged, and nothing else — the shape
has no room for a signed URL, and a test reads back everything written to prove none leaked
in. Every part is signed inside the attempt that uses it, so a retry after a long backoff
asks for a fresh five-minute capability rather than replaying an expired one, and a
checkpoint restored from IndexedDB grants no access on its own. Checkpoints are forgotten
when the upload completes, when the member cancels, and when the backend refuses the
finished media; a network failure keeps them, because that is the case resuming exists for.

`UploadPanel` names one state at a time and takes each from the backend rather than guessing:
uploading with its own percentage, then importing, transcribing, finding moments, retrying
with its attempt number, stopping, canceled, failed with the code the backend reported, and
ready to review. It follows the Workspace event stream and ignores every Job belonging to
another Project. A dropped connection says it is reconnecting without forgetting the Job it
was watching, because the browser resumes that stream from `Last-Event-ID` by itself. The
member can stop a running Job, and analysis is asked for under one key derived from the
upload, so pressing the button again is one analysis to the backend rather than two.

`YouTubeImportForm` is labelled a convenience connector and says plainly that it takes one
public, non-live video the member has the right to use. It checks the scheme and the host
before spending a round trip, and shows every other refusal — private, age-restricted,
unsupported, too long — exactly as the backend worded it. There is no cookie control on the
page and no code path that would render one: the authenticated connector belongs to Task 27,
behind its server capability and feature flag.

Twenty-eight frontend tests were written and watched fail before any of it existed, covering
size rejection, part sizing and concurrency, retry backoff and exhaustion, resume through
both an injected store and real IndexedDB, cancellation with a provider abort, the backend's
media rejection, every rendered state, job cancellation, stream reconnection, and duplicate
submission of both an analysis and an import. Each behaviour was then re-checked by breaking
the implementation and watching the matching test fail.

Final verification: `pnpm lint`, `pnpm typecheck`, `pnpm test` (63 passed), and `pnpm build`
all passed. No backend file was touched, so the backend gates were not re-run. The Playwright
suite in `frontend/e2e/upload-analysis.spec.ts` was written but not run here: it needs
Postgres, MinIO, the backend, the frontend, and browser binaries running together. No commit
was created; the required owner commit message is `feat: add resilient media submission flow`.



### Task 20 — Ranked clip review

The review surface exists, and it is available the moment analysis finishes rather than
after anything has been rendered. `ClipList` reads the whole exposed ranked set before it
offers a single control, because sorting or filtering half a list would be an order the
browser invented rather than the one the analysis decided; the set is bounded by the
ranking policy, so reading it whole costs one or two requests. Sorting by rank, score, and
length, and filtering by category and by maximum length, all work over that complete set.

`ClipCard` shows a reviewer enough to disagree with the analysis: the rank, the hook, the
payoff, why the moment was chosen, its category, tags, length, and transcript excerpt, all
seven score dimensions behind the number rather than the number alone, the context each
clip depends on, the visual opportunities it named, and its context warnings in a group a
reviewer cannot miss. Every one of those fields came from a language model reading someone
else's words, so all of it is rendered as React children; a hook containing an `img` tag
appears as that text and produces no element.

`ClipPreview` plays the proposed range against the Project's proxy and stops where the
candidate ends, so a reviewer hears the moment being proposed and not whatever follows it.
The capability is asked for when the preview opens and never kept: closing and reopening
asks the backend for a new five-minute URL.

One backend gap was closed inside this task, because preview cannot exist without it.
`GET /projects/{project_id}/proxy` signs the newest proxy of one active Project for five
minutes and returns the media shape a player needs before it loads. No numbered task owned
an asset or playback route, and Task 20's interface names signed proxy URLs. Six contract
tests cover it, including that a foreign Project, a deleted Project, and a Project whose
ingest has produced no proxy all answer exactly like a Project that never existed, and that
an anonymous caller is refused. `contracts/openapi.json` was re-exported and the client
regenerated.

One deliberate judgement is worth recording: a 404 from the candidates endpoint is rendered
as "No clips to review yet" rather than as an error. Absence is the backend's one answer for
a Project still being analysed, a Project that produced nothing, and a Project the caller
has no standing on — and only the first is possible on a Project the member already has
open. Every other failure still raises the alert carrying the request identifier.

Seventeen frontend tests and six backend tests were written and watched fail before any of
it existed, covering ranking order, score and reason visibility, all seven breakdown
dimensions, warning badges, category and duration filters, each sort, literal rendering of
untrusted text, the empty and not-yet-analysed states, range playback stopping at the
candidate end, keyboard operation, a missing proxy, and a fresh capability per preview. Each
behaviour was then re-checked by breaking the implementation and watching the matching test
fail.

Final verification: `pnpm lint`, `pnpm typecheck`, `pnpm test` (80 passed), and `pnpm build`
all passed; Ruff check, Ruff format check, strict mypy, and 828 backend tests passed with
five environment-gated skips at 94.36% coverage. The Playwright suite in
`frontend/e2e/clips-review.spec.ts` was written but not run here: it needs Postgres, the
backend, the frontend, and browser binaries running together. No commit was created; the
required owner commit message is `feat: add ranked clip review`.



### Task 21 — Editor-engine bake-off

The bake-off ran as far as this environment allows, and the ADR says exactly where that
stopped.

`BrowserEditorEngine` is the port every candidate is measured through: the seven members
`plan.md` names, plus the editing operations and the composition read-back that the gate's
byte-for-byte comparison needs. No candidate's types appear in it. One contract test runs
thirty-one assertions against every candidate — load and give the fixture back unchanged,
seek to exact frame boundaries, play and pause, trim, split a media item and a caption,
edit karaoke words without moving their timings, apply a 9:16 crop, undo and redo one step
at a time, hand a history over and take it back, render a preview frame or name the
capability it needs, read a waveform or name the capability it needs, and go inert once
disposed — and then requires every candidate to end the same operation sequence holding a
byte-identical canonical composition. It was watched fail with no adapter, exactly as the
task's second checkbox asks. Both adapters now pass all of it.

The Elah adapter drives `@elah/core`'s real `TimelineEngine` for tracks, clips, trimming,
splitting, and history. The comparison adapter implements OpenReel's approach — own the
timeline, reach for Mediabunny only at the media boundary — as Clipah's own arithmetic.

Three findings cost something, and all three are in the ADR. Elah has no model for caption
word timings, karaoke, or a crop rectangle, so Clipah keeps a parallel model beside it.
Elah's own undo cannot restore what Elah never held, so its history is unusable for
Clipah's composition and the adapter rebuilds the timeline from Clipah's snapshot instead.
And `@elah/core` publishes ESM with extensionless internal imports that Node's resolver
refuses, which `vitest.config.ts` records rather than works around silently.

`scripts/check-editor-licenses.sh` scans all 754 resolved packages and fails on GPL-family
licenses, unstated licenses, and commercial licenses with no recorded owner and renewal
cost. It passes, and it reports the weak-copyleft obligations it found — `mediabunny` and
`axe-core` under MPL-2.0, and an LGPL native binary that never reaches the browser bundle.
It records that OpenVideo Editor and Remotion require separate license review, and it was
negative-controlled: inverted to forbid MIT, it reports 629 blocking dependencies and exits
non-zero.

**No engine was selected, and the ADR is `Proposed`.** Every mandatory gate — initial load
under five seconds, median seek under 150 ms, memory under 1.5 GiB, no leaked workers after
`dispose()`, Safari codec fallback, one-hour proxy playback, and frame and timing parity
against native FFmpeg — needs browser binaries, long-form proxy media, and `ffmpeg`, none of
which exist here. `frontend/e2e/editor-engine-parity.spec.ts` runs all of them against both
candidates and skips loudly until `CLIPAH_EDITOR_PROXY_URL` and
`CLIPAH_EDITOR_REFERENCE_MACHINE` are set, and `verdict()` reports an unobserved gate as
unmeasured rather than passed, so an under-equipped run fails instead of quietly certifying
nothing. Elah leads on the evidence that does exist — 67 KiB gzipped against 397 KiB, a real
timeline engine, Apache-2.0 — but it is three months old, and the unmeasured gates are
precisely the ones that would expose that. The repository owner decided the selection would
not be guessed at; the ADR names the four commands that finish it.

Both adapters therefore stay, and the task's instruction to delete the unused spike
dependency is deferred with the decision it depends on.

Final verification: `pnpm lint`, `pnpm typecheck`, `pnpm test` (111 passed), and `pnpm build`
all passed, and `scripts/check-editor-licenses.sh` exits zero. No backend file was touched,
so the backend gates were not re-run. Two weaknesses the contract test had were found by
breaking the implementation on purpose — a caption split and a disposed engine both passed
for the wrong reason — and both were closed before the gates were declared green. No commit
was created; the required owner commit message is `docs: select browser editor foundation`.



### Task 21 addendum — the bake-off was actually run

The first pass of Task 21 delivered a benchmark that could not measure what it claimed.
Both adapters returned a stubbed `renderPreviewFrame` after a capability check, no adapter
ever fetched media, `CLIPAH_EDITOR_PROXY_URL` was read only to decide whether to skip, and
the measurement ran in the Node test process rather than in the page. It would have reported
a two-millisecond seek and cleared every gate while timing JSON mutation. That is recorded
here rather than quietly fixed, because the ADR had described it as runnable.

The harness was rebuilt. Both adapters now decode for real — Elah through
`createDefaultDemuxerFactory` into `GpuRenderer`, waiting until `VideoLayer`'s provider
actually holds the requested source frame, and the Mediabunny adapter through `UrlSource`
into `CanvasSink`. `PreviewFrame` carries whether media was decoded; a seek that decoded
nothing is recorded as a failure and withholds the seek gate entirely. The load gate now
measures time to first decoded frame, because `load()` alone was 0–1 ms and measured
nothing a person waits for. Worker counts are read through CDP around the run. Every timing
is taken inside `page.evaluate`.

Chromium and WebKit binaries were installed, and 30- and 60-minute H.264/AAC proxies were
generated with Task 11's own encoder settings and served over a range-capable local server.

Two measurement mistakes were caught before they became conclusions. The first headless runs
put Elah's median seek at 432 ms, but headless Chromium falls back to SwiftShader, and Elah
is GPU-composited while the Mediabunny path is decode-to-canvas — so software rendering
penalised one candidate and not the other. On the real GPU Elah's median seek is 108 ms. The
suite now records the graphics renderer string with every measurement. The second was a
reported Elah crash at 60 minutes on Chromium that did not reproduce standalone; it is
recorded in the ADR as unresolved rather than as an engine failure.

**The decision is Mediabunny, and Safari decided it.** On real Apple GPU hardware in WebKit,
Elah's median seek is 409 ms against a 150 ms gate, and a one-hour timeline never completes
— it timed out after fifteen minutes. Mediabunny clears every observable gate in both
browsers at both lengths: 38–110 ms median seek, 29–142 ms to first frame, 109–132 MiB heap,
zero leaked workers, every sampled frame decoded. Safari also turned out to support
WebCodecs, so the codec-fallback gate passes because the capability is present rather than
because a fallback was exercised.

Three findings beyond the timings weighed on it: Elah holds none of Clipah's caption, crop,
or karaoke state; its undo cannot restore what it never held, so `TimelineEngine.undo` goes
unused; and it publishes no waveform API at all. Of the three capabilities worth borrowing,
two are Mediabunny underneath in either candidate.

`docs/adr/0001-browser-editor-engine.md` is now `Accepted` and carries the full matrix. One
gate remains outstanding — FFmpeg frame and timing parity — because its fixture renderer is
Task 24's. Both adapters are retained until that gate is discharged.

Final verification: `pnpm lint`, `pnpm typecheck`, `pnpm test` (111 passed), and `pnpm build`
all pass, and `scripts/check-editor-licenses.sh` exits zero. No backend file was touched.



### Out-of-band — the pipeline had no conveyor belt

Found while answering when the whole pipeline could first be run end to end, and fixed
before Task 22 so the editor is not built on a pipeline nobody has watched work.

Every stage runner was registered and tested, and nothing joined them. No code anywhere
constructed a `JobKind.INGEST` or a `JobKind.TRANSCRIBE`; only the YouTube route created a
SOURCE_IMPORT and the analysis route an ANALYZE. Completing an upload dispatched nothing at
all. Worse, nothing ever set `ingesting` or `transcribing`, so `start_analysis`'s
precondition — a Project in `transcribing` holding exactly one transcript — could not be
satisfied by any path, and the analysis endpoint could only ever answer 409. Phase B's exit
gate, "a fixture video becomes ranked candidates", was therefore not met despite Tasks 10-16
being marked landed.

`jobs/pipeline.py` is now the one place that knows the order of the pipeline and the only
place that moves a Project between stages: SOURCE_IMPORT to INGEST to TRANSCRIBE to ANALYZE,
each arrival setting the status that stage means. The successor's idempotency key names the
Job it follows, so a completion delivered twice buys one stage rather than two, and it is
committed in the same transaction as the completion, so a Project can never be recorded as
finished with one stage and stranded before the next. Broker dispatch happens afterwards and
is only a wakeup. A deleted Project is never revived and a failed one is never carried on.

Completing an upload is what puts a Project on the belt, keyed by the upload it followed.

**One security boundary moved, deliberately.** The worker role had SELECT and UPDATE on
`jobs` but no INSERT, because the API created every Job. The actor that knows a stage
succeeded is the worker that ran it, so migration `0009` grants the worker INSERT on `jobs`
and nothing else — it already held the budget grant a metered stage reserves, and it already
inserts the Assets, Transcripts, and Clip Candidates of the same Project. The least-privilege
guardrail in `test_schema.py` was updated to state the new matrix rather than relaxed.

Ten integration tests cover it: each stage starting its successor and moving the Project
into that status, the last stage starting nothing, a replayed completion starting one
successor, a deleted Project stopping, a failed Project staying failed, one key admitting
one stage, and an upload completion putting a Project on the belt. Each was watched fail
first, and four deliberate breaks — dropping the status write, dropping the settled-status
guard, ignoring the completed Job in the key, and restoring the frontend's premature
analysis call — each failed the matching test.

**A Task 19 defect fell out of the same reading.** `UploadPanel` asked for an analysis the
moment an upload completed, which the backend refused every time because the Project had not
been transcribed. It no longer does: completing the upload starts ingest, and the panel
follows the pipeline it already watches. The manual retry is unchanged and still carries one
idempotency key.

Verification: Ruff check, Ruff format check, strict mypy, and 838 backend tests passed with
five environment-gated skips at 94.35% coverage; `pnpm lint`, `pnpm typecheck`,
`pnpm test` (111 passed), and `pnpm build` all passed. `contracts/openapi.json` was
re-exported and the client regenerated; no JSON shape changed.

Still not run end to end against live providers: transcription and analysis need
`CLIPAH_ASSEMBLYAI_API_KEY` and `CLIPAH_GROQ_API_KEY`, and every run so far has used the
offline adapters.


### Task 22 — Versioned compositions and immutable Edit Revisions

`editor/models.py` holds composition version 1 as data. Every part of the document is a
frozen model that forbids unknown fields, so a client cannot smuggle an attribute past the
schema and a member cannot lose one to a silent drop. The document expresses what Section 9
requires the editor to produce: video, audio, music, and extracted-audio tracks; karaoke
caption words with their own type; video, image, text, and citation overlays; transform,
crop, opacity, blend, and motion with keyframes; template and Brand Kit references carrying
their versions; placement origin with its suggestion and provenance references; bookmarks;
and the two audio gains.

It refuses more than it accepts, and each refusal is a rule the renderer would otherwise
discover too late: another `schemaVersion`, an unknown track type, a canvas outside the four
export presets, an unsupported font, blend mode, motion preset, or font weight, a colour that
is not a hex triplet, a non-finite number anywhere, a reversed source range, an item running
past the composition, two items overlapping on one track, overlapping caption words, a
keyframe out of order or past the element it animates, a keyframe that animates nothing, a
repeated identifier, a B-roll origin with no suggestion, a source origin claiming one, a
citation with no evidence record, a media overlay with no asset, and a text overlay carrying
one. `collect_asset_ids` reports every asset a composition depends on, because whether media
may be used is a question about the Project, not about the document, and the two are decided
separately.

`canonical_json` serializes a composition to sorted, compact, non-escaped UTF-8 bytes, and
`composition_hash` is the SHA-256 of exactly those bytes. Two documents that differ only in
key order hash identically; one that differs in a value does not. Those bytes are what is
stored, so a stored Revision and its hash can never disagree, and Task 24's render artifacts
can be keyed by composition hash safely.

`editor/repository.py` and `editor/use_cases.py` add the Edit itself. Creating an Edit from a
reviewed candidate takes an advisory lock on the candidate, so two tabs opening the editor
converge on one Edit rather than forking one clip into two histories, and the first Revision
is derived from the candidate alone: its own span of the source, the transcript words wholly
inside that span shifted to clip time, and nothing invented on the member's behalf. Saving
locks the Edit row, validates the composition, authorizes every asset it names against the
Project that owns them, and only then appends the next Revision. The Edit row is locked on
its own and its Project and current Revision are read afterwards — reading them in the same
statement would answer from the snapshot the lock was taken under, where a competing save's
Revision does not exist yet, and the loser would look like a missing Edit rather than a
conflict. A save whose composition hashes to the Revision already stored appends nothing,
because autosave repeats itself constantly and an unchanged document is not a new version.

`api/routes/edits.py` serves the four endpoints: creating an Edit from a candidate (201 the
first time, 200 for every replay), reading one, saving the next Revision, and listing the
history. `EDIT_REVISION_CONFLICT`, `COMPOSITION_INVALID`, and `COMPOSITION_ASSET_FORBIDDEN`
join the fixed public-message table. **One deliberate protocol decision:** the error envelope
is fixed at `{code, message, requestId}`, so a conflict reports the Revision to reconcile
against in an `X-Clipah-Current-Revision` response header rather than by widening the body.
`ApiError` gained the header carrier this needs; only values the application computed itself
are ever put there.

The composition contract is generated, never hand-written, and there is one source for it.
`backend/tools/export_composition_schema.py` writes `CompositionV1`'s own JSON Schema to
`contracts/composition.schema.json` — the version is published as a constant, so a browser
generating types from it cannot even express a document the backend would refuse — and
`pnpm generate:composition` turns that schema into
`frontend/features/editor/composition.generated.ts`. `scripts/check-contracts-clean.sh`
snapshots the four generated outputs, regenerates all of them, and fails if any changed; it
compares files on disk rather than git, so it answers the same way before a commit and inside
CI, and it was negative-controlled by appending one line to a generated file. Wiring it into
a CI workflow belongs to Task 47, which owns CI.

Sixty-one backend schema tests and twenty-one integration tests were written and watched fail
before the implementation existed, covering every field and every refusal above, the canonical
bytes, replayed and concurrent Edit creation, appended history, stale expectations, two clients
racing on Revision 2, unchanged saves, invalid and unauthorized compositions, CSRF, the
reviewer/editor role split, tenant scoping, an unreviewable candidate, an unknown Edit, a
candidate whose source Asset belongs to another Project, and a damaged transcript that must
still yield an editable clip. Five frontend tests cover the published schema and type the
generated `CompositionV1` without a single assertion. Four deliberate breaks were then made
and each failed the matching test: dropping the Edit row lock, dropping the candidate advisory
lock, dropping the unchanged-composition check, and removing the schema's version constant and
one object's closedness.

Final verification: Ruff check, Ruff format check, strict mypy, and 928 backend tests passed
with five environment-gated skips at 94.83% coverage, with complete line and branch coverage on
`editor/models.py`, `editor/repository.py`, `editor/use_cases.py`, and `api/routes/edits.py`;
`pnpm lint`, `pnpm typecheck`, `pnpm test` (116 passed), and `pnpm build` all passed, and
`scripts/check-contracts-clean.sh` exits zero. `contracts/openapi.json` was re-exported and the
client regenerated. No commit was created; the required owner commit message is
`feat: add versioned clip compositions`.


### Task 23 — The basic non-destructive editor

`features/editor/store.ts` is the editor's state, and the composition document is all of
it. Trim, crop, aspect, caption text, caption style, split, and delete each produce a new
document from the old one through Immer, and the patches Immer reports are the history:
undo restores exactly the fields a change altered rather than a snapshot that may have
drifted from them. The invariants the backend enforces are maintained here too — items lie
end to end from zero, `durationMs` follows them, caption words are re-timed with the
timeline, and a trim is clamped into the source the clip was cut from — because a document
that would be refused on save is not worth editing. Nothing in the module touches source or
proxy media; a trim moves two numbers.

Three rules are worth naming. A trim is a removal from the timeline, so the caption words
covering what was removed go with it and the rest move earlier by the same amount, which
keeps karaoke aligned through head trims, tail trims, and deletions alike. A caption word's
text is editable and its timing is not, because transcription produced those timestamps and
highlighting is only honest while they still describe when the word was said. And the last
remaining item cannot be deleted, because a composition with no items is not a clip.

`features/editor/autosave.ts` saves against the Revision the browser believes is current,
750 ms after the last change, one request at a time. An unsent composition is kept in a
draft store — local storage in the product, injectable in tests — so a dropped connection or
a closed tab does not take a member's work with it, and the draft is forgotten only once the
backend has accepted it. A stale Revision is the one failure that stops autosaving: retrying
it would fail identically forever, so the editor reports the conflict and lets the member
choose. `ApiError` now carries `currentRevision`, read from the `X-Clipah-Current-Revision`
header Task 22 answers a conflict with, so "keep my version" can save against the Revision
the backend actually holds instead of guessing at it.

`features/editor/engine.ts` is the playback boundary the ADR's decision needs: the screen
talks to a `PreviewEngine`, and the implementation shipped here is the browser's own media
element playing the five-minute signed proxy. Frame-accurate compositing through Mediabunny
is a second implementation of that port rather than a change to the screen, and it belongs
with the advanced editor. `Player.tsx` maps clip time onto source time through the items
themselves, so a trimmed or split clip plays what it says it plays; `Timeline.tsx` draws the
ruler, the playhead, zoom, and one selectable button per item, all read from the document;
`CaptionsPanel.tsx` edits word text and the caption type; `Inspector.tsx` carries the trim
numbers, the crop, the four aspect presets, split, delete, undo, redo, save, and the save
state a member reads. `app/editor/[editId]/page.tsx` is the distraction-free route from
Section 9, deliberately outside the dashboard shell.

Keyboard shortcuts cover play/pause, undo, redo, split, delete, zoom, and save, and every
one of them is inert while a member is typing: an editor that treats `s` inside a caption as
a split is an editor that eats words.

**One addition beyond the task's file list, and one beyond its checkboxes.** `engine.ts` is
the adapter boundary the task's own interface section requires, and `EditorScreen.tsx` holds
the screen so the route file stays a route and the screen stays testable. Beyond the
checkboxes, `ClipCard` gained an "Edit this clip" control: without it the editor route is
unreachable from the product, and the Task 22 endpoint it calls converges a repeated click
on the Edit that already exists.

Thirty-seven tests were written and watched fail before any of it existed — twenty over the
store, seven over autosave, and ten over the screen — covering immutable updates, patch-based
undo and redo, clamped trims, caption re-timing, split and delete, aspect presets and centred
crops, dirty-state transitions, the 750 ms debounce, one request in flight, offline queueing
and retry, revision conflict and resume, draft restoration, the rendered save labels, proxy
playback, conflict recovery in both directions, and an Edit that is not there. Four
deliberate breaks were then made: dropping caption re-timing, removing the debounce, removing
the text-field guard from the shortcuts, and the guard test's own first version — which
passed against the broken code and was strengthened until it failed — each failed the
matching test.

Final verification: `pnpm lint`, `pnpm typecheck`, `pnpm test` (153 passed), and `pnpm build`
all passed, and `scripts/check-contracts-clean.sh` exits zero. No backend file was touched,
so the backend gates were not re-run. The Playwright suite in `frontend/e2e/editor-basic.spec.ts`
was written but not run here: it needs Postgres, the backend, the frontend, and browser
binaries running together. No commit was created; the required owner commit message is
`feat: add non-destructive clip editor`.


### Task 24 — Render-plan compilation and safe FFmpeg export

`renders/compiler.py` turns one composition into one complete FFmpeg plan: the files that
are opened, the filter graph that is applied, the text files that graph reads, and nothing
else. Two rules decide its shape.

**Text is content, never syntax.** FFmpeg's filter language would happily read a caption as
an instruction, so caption words become an ASS subtitle file, overlay and citation text
become UTF-8 files read by `drawtext=textfile=`, and the watermark is treated as exactly the
same kind of text. A member's words never reach an argument, an option, or a shell — there is
no shell at all — and the ASS writer additionally neutralizes the override braces and
backslashes that would otherwise restyle the whole export. The tests drive a deliberately
hostile string (`:drop; {\an8} 'quoted' %s` with a newline) through captions, a text overlay,
and a citation, and assert it appears in files and nowhere else.

**An effect this renderer cannot reproduce is refused.** A render that silently dropped a
keyframe would hand a member a file that does not match the preview they approved, so the
compiler refuses scale and rotation keyframes, blend modes other than normal, motion presets
outside the supported set, motion or keyframes on a base timeline item, animated text
overlays, a crop of media whose dimensions are unknown, and any asset the caller did not
authorize. What it does reproduce is the rest of the basic editor: the four export presets,
trims resolved as `trim`/`atrim`, normalized crops resolved into pixels, per-item concat,
karaoke and block captions, image and video B-roll overlays, Ken Burns and pan motion for
stills through `zoompan`, fades and per-overlay opacity through alpha, piecewise-linear
position and opacity keyframes as expressions over time, dialogue gain, music beds delayed to
their own moment, and `preserveDialogueAudio` — true keeps the speaker and mutes the B-roll;
false ducks the dialogue for exactly the overlay's window and mixes the overlay's own audio in.

`renders/ffmpeg_renderer.py` executes a plan and then checks its own work: the output is
re-read and a file that drifts more than 250 ms from the composition is a failure rather than
a delivery. The graph is handed over as a file, never as an argument. **One deviation from the
task's wording is recorded here:** the checkbox names `-filter_complex_script`, which is what
the pinned FFmpeg 7.1.5 image reads; FFmpeg 8 removed that spelling in favour of
`-/filter_complex`, which reads the same file. The renderer asks the executable in front of it
which one it understands and uses that, so the pinned production image runs exactly the
mandated flag and a newer developer machine can still run a real export. Both branches are
tested.

`renders/use_cases.py` and `api/routes/renders.py` add the export endpoints. An export is
deduplicated by `(composition_hash, preset)`: the same composition at the same preset is the
same file, so a healthy artifact is handed straight back with 200 while a new one admits a
durable RENDER Job with 202. `jobs/render_task.py` runs that Job — it recognizes an identical
export before downloading a byte, downloads every authorized asset into the Job's own
workspace, compiles, encodes, uploads, verifies the stored length and digest against what it
wrote, and records the artifact. A redelivery converges on one row rather than a second file,
and every failure leaves as its own stable code: `RENDER_FEATURE_UNSUPPORTED`,
`RENDER_DURATION_MISMATCH`, `RENDER_ASSET_MISSING`, `RENDER_INTEGRITY`, `RENDER_TARGET_MISSING`,
`RENDER_FAILED`, with storage outages and encoder timeouts retryable and everything else
terminal.

**One migration was needed.** A Job carries identifiers and no payload, by design, so nothing
in the existing schema could say which Revision and preset a render Job was admitted for.
Migration `0010` adds `render_requests` — RLS-protected, written by the API in the same
transaction that creates the Job, and read back by the worker from the Job's own identifier.
The API may insert and read it; the worker may only read it. The least-privilege guardrail in
`test_schema.py` states the new matrix rather than relaxing to accommodate it.

Fifty-three compiler tests and twenty-four pipeline tests were written and watched fail before
the implementation existed. The pipeline tests include four **real FFmpeg renders** of fixture
compositions — no B-roll, a stock still with Ken Burns, a stock video with the dialogue
preserved, and a generated video with the dialogue ducked — each validated with ffprobe for
frame size, codec, a mapped audio stream, and duration, plus a cancellation test that proves
the encoder's process group actually stops. Three deliberate breaks were made and each failed
the matching test: writing overlay text into a filter argument, accepting a scale keyframe, and
dropping the deduplication check.

**Not exercised here:** burned-in captions and drawn text were not rendered by real FFmpeg on
this machine, because the local build ships without libass and libfreetype; those paths are
covered by the compiler's own tests and by the pinned image, which carries both. The rendered
scenarios therefore set captions to `off`.

Final verification: Ruff check, Ruff format check, strict mypy, and 1005 backend tests passed
with five environment-gated skips at 94.91% coverage; migration `0010` downgrade and upgrade
both ran, and the Alembic drift check passes. `pnpm lint`, `pnpm typecheck`, `pnpm test`
(153 passed), and `pnpm build` all passed, and `scripts/check-contracts-clean.sh` exits zero
after `contracts/openapi.json` was re-exported and the client regenerated. No commit was
created; the required owner commit message is `feat: render versioned clip exports safely`.


### Task 25 — Complete multi-track timeline, asset, sound, text, and scene editing

The editor now carries the whole timeline Section 9 asks for, and it is still one
composition document: a drag, a ripple delete, a music bed, a text overlay, and a scene
label are all changes to that document, all reversible through the same Immer patches,
and all expressed in integer milliseconds.

`features/editor/store.ts` gained the operations and the arithmetic behind them.
Duplicate, split-away-left, split-away-right, resize by either edge, move, ripple delete,
add a lane, add a marked span of the source, place an asset on a sound lane, extract
audio, set the two sound levels, write and move text, and leave, rename, and remove
markers. Two rules run through all of it. **The base video lane is played by
concatenation**, so items there stay end to end from zero: dragging on that lane reorders
rather than repositions, and every caption word travels with the item it belongs to.
**A sound lane is timed against the picture**, so a bed dropped at eight seconds stays at
eight seconds; the earlier `relayout` packed every lane back to zero, which would have
silently moved a member's music the first time they touched anything else. Nothing on a
lane may overlap anything else on it — a drag or a resize is clamped to the space between
its neighbours — because an overlap is refused on save, and a document that would be
refused is not worth editing.

Three things a member works with are deliberately *not* in the document: which lane is
selected, which lanes are locked, and where the source monitor's marks sit. None of them
change the clip. Locking is enforced in the reducer rather than only in the markup, so an
operation aimed at a locked lane is refused wherever it came from; the timeline also marks
those controls `aria-disabled` so the state is visible before a member tries.

Scenes are derived rather than stored. The transcript already knows who was speaking and
when, so `scenes()` reads speaker runs out of the caption words, and naming one leaves a
marker on the timeline — a second, private idea of where a scene begins could only ever
disagree with the transcript. Naming the same scene twice renames its marker instead of
leaving two.

Snapping is a fixed distance on screen — eight pixels — converted into time by the zoom
level, so it feels the same at every zoom. The targets are the edges a member is aiming at:
the start and end of the clip, every other item's boundaries, and every marker. The item
being dragged never snaps to its own edges.

Six panels were added — `AssetsPanel`, `SourceMonitor`, `TimelineToolbar`, `SceneList`,
`TextPanel`, and `AudioPanel` — and `Timeline` was rebuilt with one row per lane, markers,
pointer drag on an item's body, and a trim handle at each edge. Every gesture has a
keyboard equivalent: the toolbar carries split, split-away-left and -right, duplicate,
delete, snapping, ripple, markers and marker navigation, and adding a lane, while an item
button moves with `Alt`+arrow and stretches with `Shift`+arrow.

**One backend gap was closed inside this task, because the assets panel cannot exist
without it.** `GET /api/v1/projects/{project_id}/assets` lists exactly the media a save
would authorize — the Project's own source assets, never a proxy, thumbnail, waveform,
transcription track, or render, and never another Project's media. A Project that was
deleted, belongs to another Workspace, or never existed answers the same 404 with the same
public message. Seven contract tests cover it, `contracts/openapi.json` was re-exported,
and the client was regenerated.

**The validator and the compiler were extended before the UI that produces those documents
was enabled.** `MIN_ITEM_DURATION_MS` refuses an item shorter than the 500 ms the editor
clamps to, so a sliver left by a resize or a split is refused at save rather than exported
as a frame or two. The compiler now mixes an `extractedAudio` lane at the *dialogue* gain
rather than dropping it silently — extracted speech is dialogue, and a music gain would
mis-level it — and refuses two things it cannot reproduce faithfully: a second video track,
which would have to be composited rather than concatenated, and a gap on the base timeline,
which concatenation would silently close and hand back a clip that does not match the
preview a member approved.

Sixty-three frontend tests were written for this task — forty-five over the reducer and its
pure arithmetic, eighteen driving the screen — covering every operation, undo and redo
restoring byte-equivalent canonical JSON for each one, minimum item duration, collision
clamping, ripple shifts, track locking, snapping and its threshold, bookmark navigation,
keyboard and pointer drags, and the scene list. Four deliberate breaks were made and each
failed the matching test: packing every lane back to zero, leaving a duplicate's captions
where they were, dropping the collision clamp, and — the one that did *not* fail — removing
the timeline's own lock guard, which is a courtesy on top of the reducer's refusal rather
than the enforcement itself. That is recorded rather than papered over: the reducer is what
protects a locked lane, and the test now also asserts the visible disabled state.

Two existing assertions in `editor-basic.test.tsx` were narrowed from `/select/i` to
`/^select scene/i`, because an item now carries two trim handles and a lane carries its own
selection button. Nothing about the behaviour they describe changed.

`frontend/e2e/editor-advanced.spec.ts` covers what a component test cannot see: the media
endpoint refusing a Workspace the caller has no standing on, an anonymous caller refused,
and an Edit of another Workspace refusing every save. Driving the timeline itself against a
real clip stays a `test.fixme`, for the same reason Task 23's did — no API can stage a Clip
Candidate.

Final verification: Ruff check, Ruff format check, strict mypy, and 1021 backend tests
passed with five environment-gated skips at 94.96% coverage. `pnpm lint`, `pnpm typecheck`,
`pnpm test` (216 passed), and `pnpm build` all passed, and `scripts/check-contracts-clean.sh`
exits zero. The Playwright suite was written but not run here: it needs Postgres, the
backend, the frontend, and browser binaries running together. No commit was created; the
required owner commit message is `feat: complete multi-track timeline editing`.


### Task 26 — Styling, karaoke, keyframes, templates, motion, and smart crop

**Templates and motion presets are published data, not code on two sides.**
`renders/templates.py` holds three built-in looks and the seven motion presets, each with
an explicit version and, for a movement, the window it is legible within. Applying a
template writes its type and colour into the composition and records the exact version it
came from, so the reference is provenance rather than a lookup the renderer depends on: a
Revision renders identically forever even if the template is later replaced. The document
is exported by `scripts/export-templates.sh` to `contracts/templates.json` and to the copy
the frontend bundles, and `scripts/check-contracts-clean.sh` now fails when either drifts —
negative-controlled by renaming a template and watching the check refuse the tree.

**The renderer enforces what the editor offers.** The compiler refuses a built-in template
version nobody published (`RENDER_TEMPLATE_UNKNOWN`), and refuses a movement given less or
more time than it reads in — a Ken Burns drift under 600 ms is a lurch, and a pan stretched
past thirty seconds is a still that never arrives. It also gained the one thing a keyframe
on a *base* timeline item may say: the framing travels. That is compiled as an FFmpeg
`crop` whose window keeps its size while its centre moves along a piecewise expression, and
it is what a smart-crop suggestion produces. A keyframe on a base item that fades or
restyles it is refused, because the base picture is the whole clip.

**Smart crop is a suggestion made from evidence.** `assets/smart_crop.py` takes detected
face boxes and the transcript's own speaker segments and proposes where a vertical clip
should look. Three rules hold: an item that already carries keyframes is returned untouched
with the reason; no confident face means the centred framing rather than an invention; and
the proposed window never leaves the source frame and never travels faster than a third of
the frame per second, because a swing across the picture reads as a mistake. Nothing here
detects a face — detection is a provider's job — so the module is decided entirely by its
own inputs and has 100% line and branch coverage.

**The editor gained four panels.** `TemplatesPanel` applies a published look by name and
marks the one in use. `KaraokePanel` names the word being said at the playhead and retimes
one word inside the gap its neighbours leave it, because two words claiming one instant
would give karaoke two active words and would be refused on save. `KeyframeEditor` adds,
moves, and removes keyframes on the selected item and reports the framing at the playhead,
interpolated with the easing each keyframe names. `MotionPanel` offers every published
movement and refuses one an element is too short to show, saying which bounds it missed
rather than accepting it now and failing at export. `CaptionsPanel` grew the style fields
Section 9 names that it was missing — weight, italic, decoration, letter spacing, and line
height.

**The golden-frame gate exists and has been watched fail.** `tests/perceptual.py`
implements structural similarity with a documented mask for the bands type is drawn into,
because font rasterization is a property of the machine rather than of the composition. The
threshold is 0.97, and seven unit tests hold the gate to it: an identical frame scores one,
a frame one level darker passes, a shifted picture and full-frame noise fail, a masked
region hides a difference, masking everything is refused rather than scored as a pass, and
two frames of different sizes are refused. `tools/generate_golden_frames.py` writes the
frames and a SHA-256 manifest; the suite verifies the digest before it compares, so a
golden frame edited by hand is refused rather than trusted.

Four scenarios have checked-in golden frames and pass on this machine — the plain base
timeline, a travelling smart-crop window, a Ken Burns still, and a half-opacity overlay —
and the suite proves the gate discriminates by scoring a deliberately wrong render against
the plain golden and requiring it to fail. One measurement mistake was caught and recorded
rather than papered over: the first smart-crop golden was sampled at the midpoint of an
eased move from 0.3 to 0.7, which sits exactly where a centred crop would, and scored
0.9969 against the plain frame. It is now sampled at 0.15 s, where the window is still left
of centre.

**Three scenarios are unmeasured here, and are skipped by name rather than passed.** The
caption, karaoke, and drawn-text goldens need `subtitles` and `drawtext`, which need libass
and libfreetype; the FFmpeg on this machine (9.0.1) carries neither, and the pinned render
image carries both. The generator refuses to write a golden frame for a scenario this build
cannot draw, so no machine can certify a picture nobody could have looked at.

**One deviation from the task's wording is recorded here.** The checkbox asks for
*preview*/render golden-frame fixtures. The render side is built and running; the preview
side compares against a browser compositor that does not exist yet — the basic editor draws
captions and crop over the proxy rather than compositing frames through Mediabunny, which
was already deferred from Tasks 25 and 26. The gate is written so that the preview frames
drop into the same comparison and the same threshold once that compositor exists.

Final verification: Ruff check, Ruff format check, strict mypy, and 1075 backend tests
passed with eight environment-gated skips at 95.08% coverage. `pnpm lint`, `pnpm typecheck`,
`pnpm test` (242 passed), and `pnpm build` all passed, and `scripts/check-contracts-clean.sh`
exits zero. Four deliberate breaks were made against the new editor behaviour and each
failed the matching test — a retime that ignores its neighbours, a template that records no
version, a movement that ignores its window, and an easing that is not applied; the easing
test was sharpened first, because its original midpoint assertion could not tell an eased
move from a linear one. No commit was created; the required owner commit message is
`feat: add advanced editor styling and smart crop`.


### Task 27 — Feature-flagged authenticated YouTube connections

This task hands Clipah a live credential for somebody's Google account, so almost all of
it is about refusals, boundaries, and what happens when something goes wrong.

**The feature does not exist until a deployment says it does.**
`CLIPAH_AUTHENTICATED_SOURCE_IMPORT_ENABLED` is `false` by default and pinned `false` in
the production profile. While it is off, every connection route answers exactly like a
route that does not exist — a member cannot discover the feature by probing for it —
`GET /api/v1/me` reports the capability as `false`, and the browser renders nothing at all.
The capability also stays off where no key material is configured, because a deployment
that cannot encrypt a jar must not invite anybody to upload one.

**`source_connectors/cookies.py` parses, and never trusts.** An upload is size-checked at
256 KiB before it is decoded, read as Netscape format with either line ending, and reduced
to the documented minimum of YouTube authentication cookies on `.youtube.com` and
`.google.com`. A browser export of somebody's whole browsing life becomes seven rows for
one site. `#HttpOnly_` is read as the flag it is rather than as a comment; a zero expiry is
a session cookie rather than an expired one; a repeated name keeps the last row; an expired
extra is dropped while the jar survives; and a file this parser cannot understand — bad
field count, a non-boolean flag, a non-numeric or negative expiry, a control character, an
escape sequence, bytes that are not UTF-8 — is refused whole. No refusal, message, or
exception carries a cookie value.

**`source_connectors/secrets.py` is envelope encryption with the Workspace bound in.** Each
jar is sealed under a fresh AES-GCM data key, and that key is wrapped by a key derived from
the deployment's secret material through HKDF with a label naming this one use. The
Workspace and Connection are authenticated data, so a row copied into another Workspace
cannot be opened there — the decryption fails rather than returning something plausible. A
`SecretLease` is a loan rather than a copy: it names the Job that took it, stops answering
when its window closes or when it is discarded, overwrites its buffer on the way out, and
never prints what it holds.

**The credential is stored apart from everything that describes it.** Migration `0011`
creates `source_connections` — the metadata a member sees — and `source_connection_secrets`,
which holds the material. The API role has `INSERT`, `DELETE`, and column-level `SELECT` on
the identifying columns only: an API process can store a credential and destroy it, and can
never read one back. Only the worker may read it. Deleting by identity requires reading the
identity, which is exactly why the SELECT grant is per column rather than per table, and
the insert is written as a plain statement so no `RETURNING` clause asks for the material.
`source_imports` gained the connection it was admitted with, so a retry can only ever reuse
that one; the composite foreign key refuses to let a referenced connection be deleted, so
an import can never be orphaned from its credential.

**Consent is recorded rather than assumed.** A connection exists only when the member has
confirmed both that they understand the risk and that the account is theirs, and the
instant of that confirmation is stored. It expires with its own shortest-lived cookie or
after seven days, whichever comes first, and reports itself expired the moment its window
closes rather than waiting for a sweep. Revoking marks the connection **and deletes the
secret row**: there is nothing left to lease, and a lease against a connection whose
material is gone is refused as revoked rather than as missing.

**`source_connectors/authenticated_youtube.py` gives the credential the shortest life it
can have.** The jar is created inside the Job's own `0700` workspace with `0600`
permissions from the moment it exists rather than narrowed afterwards, refuses to write
over a file a previous attempt left behind, is passed to yt-dlp as one argument-array value
— never a shell word or an environment variable — and is removed on success, on provider
failure, and on a worker killed mid-import. The lease is discarded with it.
`--cookies-from-browser` is not used and not offered: on a hosted worker that profile
belongs to the machine rather than to the member. It is documented as a local, self-hosted
command in `docs/security/youtube-import.md` and appears nowhere in the product.

**A new authority was added.** `SOURCE_CONNECTION_MANAGE` sits with the other irreversible
actions and demands a freshly authenticated Session; `SOURCE_CONNECTION_READ` does not,
because listing connections carries no credential and forcing re-authentication to look at
a list would teach members to re-authenticate for no reason. Both belong to admins and
owners.

**The browser side is mostly words.** `YouTubeConnectionDialog` renders nothing where the
capability is off. Where it is on, it says what a cookie jar is, that Google may restrict
or ban an account whose session is used by automated tools, that the connection is kept for
at most seven days, that signing out ends it immediately, that it can be revoked here, and
that only the member's own account may be connected. The Connect button stays disabled
until both confirmations are given, the request carries the jar and those two booleans and
nothing else, and a stored connection is shown by its label, scope, and expiry — never by
its contents. The file is read with `FileReader` rather than `Blob.text()`, which this
product's test environment does not implement and some browsers do not either.

**The security suite is deliberately paranoid.** `tests/security/test_source_secret_redaction.py`
puts one canary value through the whole feature and then hunts for it in every API
response and header, every column of every table in the database, every log record written
while the feature ran, and the text of every refusal it can raise. Four deliberate breaks
were made and each failed the matching tests: ignoring the domain and name allowlist,
skipping the consent check, leaving the jar on disk, and ignoring the feature flag. A fifth
mutation — storing the jar unencrypted — failed the redaction suite, which is what that
suite exists for.

**Three deviations from the task's wording are recorded here.** The migration is `0011`
rather than the `0002` the plan names, because `0002` was taken in Task 4 and revisions are
sequential. The flag is the `CLIPAH_AUTHENTICATED_SOURCE_IMPORT_ENABLED` that Task 1
already defined, rather than a second near-identical `AUTHENTICATED_YOUTUBE_IMPORT_ENABLED`.
And the jar is uploaded as base64 in a JSON body rather than as a multipart file, because
every other route on this API is JSON and multipart would have added a dependency for one
endpoint; the size cap is enforced on the encoded field before anything is decoded.

Final verification: Ruff check, Ruff format check, strict mypy, and 1155 backend tests
passed with eight environment-gated skips at 95% coverage; migration `0011` upgrade,
downgrade, and upgrade again all ran, and the Alembic drift check passes. `pnpm lint`,
`pnpm typecheck`, `pnpm test` (250 passed), and `pnpm build` all passed, and
`scripts/check-contracts-clean.sh` exits zero after `contracts/openapi.json` was re-exported
and the client regenerated. The environment-gated public import smoke test was not opted in,
as in Task 10. No commit was created; the required owner commit message is
`feat: add guarded youtube source connections`.


### Task 28 — Semantic beats and deterministic B-roll plans

The whole task is one boundary: a language model may say what a viewer should see and
which words it belongs to, and nothing else. It never says *when*.

**`broll/models.py` holds the vocabulary as data.** `VisualIntent` is frozen, forbids
extra fields, and demands every field a retriever will later search or refuse on — subject,
action, setting, mood, Indonesian *and* English search terms, portrait suitability,
exclusions, factual-risk flags, and confidence. `VisualBeatProposal` is what a provider may
state: two word IDs, a placement reason, an optional protection, and that intent. There is
no millisecond field anywhere in either schema, so a model cannot supply a timestamp even
by accident. Strictness is per-field rather than per-model: `portrait_suitable` and
`confidence` are strict, so `"true"` is refused, while the list fields stay lax so provider
JSON arrays become tuples instead of being rejected for their type.

**`broll/planner.py` resolves every beat locally.** `validate_beat` reads the bounds from
the transcript the Project actually holds and refuses unknown word IDs, reversed ranges, and
beats outside the clip through the stable `BROLL_BEAT_UNKNOWN_WORD_ID`,
`BROLL_BEAT_RANGE_REVERSED`, `BROLL_BEAT_OUTSIDE_CANDIDATE`, and
`BROLL_BEAT_SCHEMA_INVALID` codes, none of which carry provider text. One refused beat costs
a member no other beat: the planner reports its code and keeps the rest. The Groq adapter
sends a strict JSON Schema in which every property is required and `additionalProperties` is
false, pins temperature to zero with an explicit timeout, shows the model only the clip's own
words, and asks it to translate search *intent* rather than transcript text so an Indonesian
concept keeps its local meaning. Failures are classified by status alone onto
`BROLL_PROVIDER_RATE_LIMITED`, `BROLL_PROVIDER_UNAVAILABLE`, `BROLL_PROVIDER_REJECTED`, and
`BROLL_PROVIDER_INVALID`.

**`broll/placement.py` owns every millisecond.** It drops protected beats, beats below the
confidence floor, beats inside the opening hook guard, and a beat repeating a picture
already offered; sizes each shot into the 2-5 second band; ends it at the next scene change
rather than straddling one; keeps it inside the clip; and enforces the coverage floor —
15 s for `minimal`, 8 s for `balanced`, 5 s for `dynamic`. It never fails: a clip that earns
no shot produces none, because "this clip does not want B-roll" is an answer, not an error.
Scene boundaries are derived from silence gaps and speaker changes.

**Planning proposes and never edits.** `jobs/broll_plan_task.py` writes only `proposed`
suggestions; it creates no asset, touches no composition, and moves no Project between
states. A plan is identified by `(candidate_id, planner_version, coverage)` and a beat
inside it by its start word, which the unique key enforces, so a redelivered Job converges
on the rows already stored and the provider is paid once. A worker that loses the race to
another writer adopts the winner's plan rather than reporting a failure; a conflict the
plan's own key cannot explain is reported as `BROLL_PLAN_INTEGRITY`.

**Six decisions are worth recording.**

- **There is deliberately no offline fallback planner**, unlike highlights. Highlights fall
  back because a Project without candidates is a Project without a product. B-roll is
  optional, and telling a member their clip has no visual opportunities when in truth the
  provider was unreachable is a worse answer than a retryable failure they can run again.
- **Migration `0012`, not the `0003` the plan names**, because `0003` was taken in Task 5
  and revisions are sequential — the same deviation Task 27 recorded.
- **`broll_suggestions` carries `start_ms`, `end_ms`, `coverage`, and `planner_version`,
  which the plan's Section 4 column list omits.** Placement owns the milliseconds, so
  recomputing them when a member accepts a suggestion would let the stored plan and the
  timeline drift apart; and without coverage and planner version the plan has no identity
  to be idempotent on. This is the same kind of gap Task 14's migration `0008` closed for
  candidate evidence.
- **A second table, `broll_plan_requests`,** records which clip and coverage one admitted
  Job was created for, because a Job carries only identifiers across the broker. This
  mirrors `render_requests` from Task 24 exactly.
- **`broll/use_cases.py` is one file beyond the plan's list**, because `AGENTS.md` requires
  domain rules to live in a `use_cases.py` and HTTP concerns to stay in `api/routes/`.
- **Three guards were deleted rather than tested**, because the schema already makes them
  unreachable. The composite `(workspace_id, candidate_id)` and `(workspace_id,
  transcript_id)` foreign keys mean a stored plan request always names a candidate of the
  same Workspace, and that candidate always names a Transcript of the same Workspace, so
  the request, its clip, and its words are now read in one join with exactly one way to
  have no target. The route's quota and hourly-allowance handlers went the same way: the
  plan's limit table names no B-roll planning budget, so neither refusal can arise.

Least privilege follows the direction of the work: the worker plans, so it holds `INSERT`
and no `UPDATE` or `DELETE` — a replay cannot rewrite a member's decision. The API reads and
updates but never inserts, because it does not plan.

Every rule was re-checked by breaking the implementation on purpose. Ignoring beat
protections failed six tests; ignoring the coverage floor failed three; trusting the model's
own bounds instead of the transcript failed one; and admitting a low-confidence beat failed
one.

Final verification: Ruff check, Ruff format check, strict mypy, and 1260 backend tests
passed with eight environment-gated skips at 95.40% coverage, with 100% line and branch
coverage on every module this task added. Migration `0012` upgrade, downgrade, and upgrade
again all ran, and the Alembic drift check passes. `pnpm lint`, `pnpm typecheck`, `pnpm test`
(250 passed), and `pnpm build` all passed, and `scripts/check-contracts-clean.sh` exits zero
after `contracts/openapi.json` was re-exported and the client regenerated. No commit was
created; the required owner commit message is `feat: plan explainable broll suggestions`.


### Task 29 — Provenance-aware stock B-roll retrieval

Everything here answers one question a lawyer would ask a year after a clip is published:
*was this footage ever licensed for this use?* If the answer cannot be produced from the
database alone, the picture should never have been stored.

**Provenance is a gate, not a record.** `provenance_of` in `broll/retriever.py` refuses a
candidate that is missing its provider, asset identity, source URL, author, licence name,
licence URL, terms snapshot, retrieval date, query, or attribution text, and refuses one
that failed safe search whatever else it carries. It runs *before* anything is downloaded,
and the asset row and its provenance row are written in the same transaction, so an
untraceable asset cannot exist even for an instant. Migration `0013` gives the worker
`INSERT` and nobody `UPDATE`: a licence snapshot that can be edited is not evidence.

**The Workspace's own footage is searched first, and "sufficient" is decided by the
reranker.** Counting local results is a poor test of enough — one clip that actually
illustrates the beat is enough, and four that merely mention the right words are not — so
`retrieve_candidates` takes an injectable sufficiency test and the retrieval Job passes one
that reranks. A Workspace that already holds a good picture never pays a provider for a
second one.

**Only the selected candidate is downloaded.** Systematically fetching search results
breaches both providers' terms; the code fetches inside the branch that stores, so it is
not able to. Nothing is hotlinked either: the provider's `download_url` is used once and
never persisted, and members are served Clipah's own copy behind signed URLs.

**Two adapters, one candidate shape.** `pexels_adapter.py` and `pixabay_adapter.py` are the
only modules that know their providers exist — different endpoints, different result keys,
renditions as a list versus a dictionary, tags as a list versus one comma-separated string,
a credential in a header versus a query parameter. An entry whose author, source page, or
media file is missing is skipped rather than half-stored, and failures are classified by
status alone onto stable codes that carry no provider text and no credential. 30 contract
fixtures cover all of it with no network in CI.

**Reranking is adapter-neutral and explains itself.** `reranker.py` scores semantic
relevance, sampled-frame relevance, technical quality, 9:16 crop viability, local fit, and
repetition, and refuses outright anything naming a brand exclusion, too small for a vertical
export, or too wide to crop at all. A vision model, where one is configured, is reached
through the `FrameRelevanceProvider` port and recorded by name and version beside every
score; where none is, `frame_relevance` is `None` rather than zero, because a dimension
nobody measured must not be reported as one that scored badly.

**Caching serves both the terms and the budget.** `search_cache.py` stores normalized
candidates — never raw payloads, so nothing credential-shaped reaches Redis — for the 24
hours both providers ask for. An unreachable or unreadable cache is a missed saving and
never a failed search.

**Four decisions are worth recording.**

- **Repetition became a ranking penalty rather than a relevance one.** Charging it against
  the relevance floor dropped a good clip by a new author below the threshold simply
  because two others had been seen first. Candidates are now scored on their own merits,
  sorted, and only then charged for repetition — so the best clip by a photographer
  survives and the fourth sinks.
- **A total provider outage is retryable, not "nothing found".** Source failures are
  absorbed per source so one outage does not empty a search, but they are counted; when
  every source failed and nothing was found, the Job retries rather than telling a member
  their beat has no visual opportunities.
- **Local matching ignores provider chrome and two-letter tokens.** Matching attribution
  lines and source URLs made the stopword "on" look like evidence that a signup-form clip
  illustrated an activation chart.
- **Migration `0013`, not the `0004` the plan names**, for the same sequential-revision
  reason as Tasks 27 and 28. It also adds the `broll`/`broll_proxy` asset kinds and the
  `stock` source type the plan's enumerations omitted, and a column-level `UPDATE` grant
  letting the worker attach an asset while leaving `status` and `decided_at` API-only — a
  retrieval Job can give a suggestion a picture and can never decide for the member.

`backend/evals/broll/` holds 30 labeled Indonesian and English intents, each offered three
relevant clips by different authors beside an irrelevant one, a culturally mismatched one,
an unsafe one, a repeat by an author already used, and one too wide to crop to vertical. The
manifest records a SHA-256 per case, so the baseline is hard to move without saying so, and
`evals/broll/generate.py` reproduces every byte. `scripts/run-broll-eval.sh` scores it and
exits non-zero on a violation. Both offline adapters pass all three gates: provenance
completeness 1.0, zero unsafe selections, and top-three relevance 1.0 across 30 cases.

`docs/legal/asset-provenance.md` records what is stored and why, each provider's licence and
the obligations honoured, and five open items — provider credentials, a terms re-read at the
configured version, attribution display, takedown handling, and what adding a third provider
would involve.

Every rule was re-checked by breaking the implementation on purpose. Storing an asset with
incomplete provenance failed 13 tests; accepting an unsafe candidate failed 3; paying a
provider when the Workspace already had a picture failed 3; ignoring brand exclusions failed
2; and re-downloading footage already held failed 1.

Final verification: Ruff check, Ruff format check, strict mypy, and 1397 backend tests
passed with ten environment-gated skips at 95.77% coverage, with 100% line and branch
coverage on every module this task added. Migration `0013` upgrade, downgrade, and upgrade
again all ran, and the Alembic drift check passes. `scripts/check-contracts-clean.sh` exits
zero, and `scripts/run-broll-eval.sh` passes on both offline adapters. The live provider
smoke test was written and left opt-in, and was not opted into. No commit was created; the
required owner commit message is `feat: retrieve provenance-aware stock broll`.


### Task 30 — Editable B-roll in the clip editor

A proposal is not an edit. Everything here follows from that: the planner's suggestions
reach the preview, the timeline, and the export at exactly one moment, which is the
moment a member says yes, and never before it.

**Two gaps had to be closed before the editor could be built, and both are recorded
here rather than buried.** Task 29 shipped a `BROLL_RETRIEVE` runner that nothing could
start: no route admitted the Job, so every suggestion sat with an empty `asset_id` and
nothing was acceptable. `POST /projects/{id}/candidates/{cid}/broll-retrievals` now
admits it through the existing path, so the `stock_requests` quota is reserved in the
API where every other metered Job reserves it rather than inside a worker. Planning and
retrieval stay two Jobs because they fail differently — a plan that cannot reach its
model is worth retrying on its own, and a search that finds nothing must not throw away
beats a member can still read — and the panel chains them, so a member still presses one
button. And `PLACEABLE_KINDS` gained `AssetKind.BROLL`, because replacing an accepted
picture means naming another asset the Project already holds, and until now the assets
the save-time check would accept and the assets the editor offered had drifted apart.

**One decision, one Revision, one transaction.** `POST /edits/{edit_id}/broll-decisions`
takes the decision and the composition it produced together, because they are the same
event seen twice: the member's answer, and the document that answer made. The decision
is refused unless the document agrees with it — an accept whose composition draws no
overlay for that suggestion, an accept whose overlay names media the suggestion does not
hold, and a remove whose composition still draws the picture are all
`BROLL_DECISION_INVALID`. Without that check the database would end up describing a clip
nobody would ever see. The same optimistic concurrency that guards every save guards
this one, which is what stops two tabs from placing one picture twice: the loser gets
`EDIT_REVISION_CONFLICT` with the Revision to reconcile against, and its suggestion is
left exactly as it was.

**Three decisions are worth recording.**

- **Replacement swaps to another asset the Project owns, and never re-searches.** Task 29
  downloads only the candidate it selected, because systematically fetching search
  results breaches both providers' terms — so there is no stored alternative to offer.
  Re-running retrieval per click would spend a provider budget nobody agreed to. The
  repository owner chose the swap; the earlier decision stays legible in the Revision
  chain, which is where an Edit's history has always lived.
- **Rejecting writes no Revision.** Saying no is an answer about a proposal rather than
  an edit to the clip, so it records `rejected` and leaves the document byte-identical.
  A suggestion that found no picture can still be rejected: refusing to let a member
  dismiss a beat because retrieval failed would punish them for the world's problem.
- **A 404 from the suggestions read renders as "no suggestions yet", not as an error.**
  Absence is the backend's one answer for a clip nobody has planned and for a clip the
  caller has no standing on, and only the first is possible on a clip they already have
  open. This is the judgement Task 20 recorded for candidates, applied again.

**In the browser, an accepted suggestion stops being a suggestion.** `acceptSuggestion`
converts the planner's source-relative beat into timeline time — the backend plans in
the source's own frame, the composition starts at `sourceRange.inMs`, and a member
should never have to know the difference — and writes one overlay with
`preserveDialogueAudio` true. After that it is an ordinary overlay: it trims, moves, and
deletes through the operations Task 25 already built, it appears on the timeline's
overlay lane, and undo restores the exact prior document because the decision is one
Immer patch like every other change. Accepting twice writes nothing the second time.
A still is given `kenBurnsIn`, because a frozen frame reads as a stall.

**Provenance is shown where a member decides and where they check.** Each card names
what the shot should show, why the beat wants a picture, how sure the planner was, and,
behind one control, the provider, author, licence, and attribution. Media a model drew
carries an AI-generated badge. The clip page answers the different question — *what am I
publishing?* — by listing only the pictures actually in the clip, since a proposal
somebody refused licenses nothing. Every word on those cards was written by a language
model reading someone else's transcript, so all of it renders as React children: a
subject containing an `img` tag appears as that text and produces no element.

Two golden frames were added and looked at before they were signed off:
`broll-stock-video`, an accepted stock clip drawn over the speaker with the dialogue
kept, and `broll-stock-still`, an accepted still with the pan and zoom. The third case
the task names — an edit with no B-roll — is the existing `plain` scenario. The origin
is a record and never a rendering instruction, which is why the still renders
identically to any other moving still.

Every rule was re-checked by breaking the implementation on purpose. Removing the
decision-consistency check failed four backend tests; widening the suggestion lookup past
the Edit's own clip failed one; turning off dialogue preservation failed two frontend
tests; dropping the idempotent accept failed one; and enabling the coverage control
before a member had asked failed one.

Final verification: Ruff check, Ruff format check, strict mypy, and 1429 backend tests
passed with ten environment-gated skips at 95.82% coverage, with 100% line and branch
coverage on every module this task touched. The golden-frame gate passed seven scenarios
including both new ones, skipping the three that need libass and libfreetype. Alembic
reports no drift and this task needed no migration: the API has held `UPDATE` on
`broll_suggestions` since `0012`. `pnpm lint`, `pnpm typecheck`, `pnpm test` (283
passed), and `pnpm build` all passed, and `scripts/check-contracts-clean.sh` exits zero
after `contracts/openapi.json` was re-exported and the client regenerated. No commit was
created; the required owner commit message is `feat: add editable broll copilot`.


### Task 31 — Quota-aware generated-media fallback

Generated media is the answer to one narrow question: what does a member do with a beat
stock could not illustrate? Everything here follows from keeping that question narrow. A
suggestion with a good enough picture is never offered a generation, a still is always
offered before a clip, and no Workspace is billed until a member has read a real price and
agreed to it.

**The browser names a media kind and nothing else.** The prompt, model, geometry, duration,
and output count are all derived on the server from the suggestion's stored Visual Intent
and this deployment's configuration. `POST /broll-suggestions/{id}/generation-estimates`
prices that derived request, reserves nothing, and returns a confirmation sealed with the
deployment secret and bound to the Workspace, the User, the suggestion, the complete
request, and an expiry. `POST /broll-suggestions/{id}/generate` verifies that seal,
re-derives the request, and refuses if the two disagree — so a client that edits a price,
a size, or a model is refused rather than obeyed.

**Two budgets are one decision.** A still reserves one generated image; a clip reserves one
generated video *and* its seconds, in the same transaction as the Job. `admit_job` now
accepts an explicit resource map rather than the one-resource default, and a refusal on
either budget rolls back both and the Job with it. `QuotaLedger` gained
`settle_if_reserved` and `release_if_reserved`, which lock the row and return an
already-terminal one unchanged, so a redelivered completion cannot charge twice.

**The provider request ID is the idempotency anchor.** The worker persists the opaque
handle before its first poll, so a redelivered Job resumes the request it already started
instead of buying a second one. A webhook is treated as a wakeup and never as authority:
the stage acts on the provider's own answer. Polling is bounded by the configured attempt
deadline and then raises a retryable timeout, so a slow generation frees the worker rather
than holding it.

**Nothing a provider says becomes evidence on its own.** Output URLs are ephemeral
capabilities carried as `SecretStr`, downloaded immediately and never stored. Stills are
decoded and verified with Pillow, accepted only as PNG, JPEG, or WebP, and bounded by pixel
count and bytes. Video is probed, refused when longer than the request allowed, and
normalized to a private proxy. The Asset and its provenance — provider, request ID,
server-built prompt, model, version, seed, moderation result, usage snapshot, checksum —
are written in one transaction before the suggestion points at anything.

**Runway needed one thing the provider contract did not have.** Its task payload reports
neither geometry nor billed credits, so a worker resuming in a later process had nothing to
normalize the result with. `GenerationHandle` gained three optional geometry fields that
the requesting adapter fills in; fal leaves them empty because it reports its own.

**Sub-task 2 was reviewed before the rest was built, and it left one defect.** The webhook
route emitted `GENERATION_WEBHOOK_INVALID`, a code absent from the public-message table, so
the envelope silently answered with the generic `HTTP_ERROR` message. The code is now
registered with its own message and a test asserts it.

**One defect was found by the tests, in the worker.** `_store_output` minted an asset ID for
the storage key and `_persist` minted a different one for the row, so every generated file
would have been stored under a key naming an Asset that did not exist. The ID is now minted
once and carried through.

**Task 31 needed one migration, which the plan did not anticipate.** Retrieval never changed
a suggestion's status, so the worker's column-level `UPDATE` grant on `broll_suggestions`
deliberately excluded it. Generation must: `generating`, `failed`, and the return to
`proposed` are facts only the worker can know. Migration `0014` widens that grant by exactly
one column. `decided_at` stays outside it, so no Job can still record a member's decision.

Verification: **1613 passed, 10 skipped, 94% coverage** on the backend; **297 passed** on the
frontend, including 12 new generation tests; `pnpm lint`, `pnpm typecheck`, `pnpm build`,
and `scripts/check-contracts-clean.sh` all clean; `alembic downgrade 0013 && upgrade head`
round-trips. The live-provider smoke test skips cleanly with no network call.

Owner commit message:

```text
feat: add guarded generative broll fallback
```

### Task 32 — Context-safe clip variants and platform packaging

A ranked candidate is a proposal about where a moment starts and ends. Task 32 asks two
further questions about that proposal — is this cut honest, and how does it read at
twenty, thirty, forty-five, sixty, or ninety seconds — and lets a member record what a
claim in the clip rests on.

**Context safety runs two engines and trusts neither alone.** Deterministic rules read the
transcript and establish what rules can: a span ending inside a question, a payoff severed
mid-sentence, a negation stranded outside the cut, a reported claim whose attribution was
dropped, an opening pronoun with no antecedent, a caveat left behind, an enumeration shown
as if it were whole. A Groq assessment proposes what rules cannot see, under a closed
schema, and **every proposal is discarded unless it resolves** — a warning naming an
unknown word, a range outside the span, a type outside the closed set, or an impossible
suggested boundary does not reach a member's screen. A deployment with no credential keeps
the deterministic floor, exactly as B-roll falls back to a Workspace's own footage.

All eight warning types are labeled in Indonesian, English, and code-switched speech,
because a rule that only works in English would have passed a third of the fixtures.

**A Variant is refused rather than approximated.** Cuts are made only on sentence
boundaries the transcript already contains. A target that cannot be hit inside tolerance
without severing a thought yields nothing at all, which is the truthful answer to "show me
this at twenty seconds". A request for 25 seconds is refused rather than rounded to 30:
silently serving a different length tells a member something untrue about their own clip.
At most three hook strategies are offered, each a deterministic opening policy rather than
model output.

**Platform packaging is a table, not an integration.** TikTok, Reels, and Shorts each
contribute an aspect ratio, safe-zone insets, title guidance, a caption style, and the
export preset the render compiler already understands. Nothing authenticates or uploads;
Tasks 36-43 own publishing.

**Claim Evidence records what a member said, never what Clipah checked.** A URL is
validated syntactically and never resolved — a citation is displayed, not fetched, and
resolving it would turn opening a review page into an outbound request to a member-supplied
host. HTTPS only, no embedded credentials, no private or loopback address. Markup is
refused rather than sanitized, because a member who typed a tag meant something by it. A
word range outside the candidate is refused. `verification_status` defaults to
`unverified` and only a User ever changes it, with the actor kept beside the assertion.

**Variants are generated synchronously, and that is a decision rather than an oversight.**
One candidate is one bounded provider call over an already-small window, and a member is
choosing between readings interactively; a queue round trip would buy nothing. The
consequence is stated rather than hidden: the request spends the existing write rate limit
and no metered quota, because the plan's limit table names no variant budget.

**Three deviations from the plan's sketch, all deliberate.** The migration is `0015`, not
the `0005` the plan names — that numbering predates ten landed migrations. `workspace_id`
and composite foreign keys were added to both tables, because `AGENTS.md` requires them on
every tenant-scoped row. And the sketch's `kind` column is absent: hook strategy, target
duration, and platform are each their own column, so a fourth discriminator could only ever
disagree with them.

**The evaluation extension lives beside the highlight harness rather than inside it.**
Warning recall and precision and variant boundary validity are scored over labeled
boundaries in `variants/evaluation.py`; the highlight fixtures carry no per-warning labels,
and adding some would either invent labels nobody wrote or make every checked-in highlight
run unreadable. Semantic preservation and user acceptance are reported as **unmeasured**:
both need human review and the real-audio corpus this repository still lacks, and a number
invented for either would misrepresent what was measured.

Verification: **1715 passed, 12 skipped, 94% coverage** on the backend; **311 passed** on
the frontend, including 14 new review tests; `pnpm lint`, `pnpm typecheck`, `pnpm build`,
and `scripts/check-contracts-clean.sh` all clean; `alembic downgrade 0014 && upgrade head`
round-trips. The Playwright spec `e2e/clip-variants.spec.ts` is written but was not run:
the browser suite still needs the served API and provisioned object store recorded below.

Owner commit message:

```text
feat: add context-safe clip variants
```

### Defect fixed during Task 30 — an open stream pinned a database connection

Found by running the Playwright suite against a real stack for the first time. The
backend log carried repeated `sqlalchemy.exc.TimeoutError: QueuePool limit of size 5
overflow 10 reached`, raised from the Workspace job stream's own poll.

**Root cause.** FastAPI tears a yield-dependency down only once the response is
complete, and a `StreamingResponse` completes only when its stream ends. Both job
streams were authorized through `require_workspace`, which depends on the request-scoped
`DatabaseSession`, and `session_scope` opens a transaction as soon as it is entered. So
every open stream held one pooled connection — inside an open Postgres transaction —
for as long as a browser tab stayed open. The pool is 5 plus 10 overflow, so the
sixteenth concurrent stream, or any ordinary request made while fifteen job centers were
open, waited the full thirty seconds and failed. `stream_workspace` even did `del
session`, which shows the author did not want it; deleting the name does not release the
dependency.

Task 8 had recorded the right intent — "each poll opens its own short tenant-scoped
transaction, so an open stream holds no database connection" — and the per-poll
`_tenant_session` honours it exactly. The route-level dependency silently defeated it.

**The fix.** `authorize_workspace` is now a plain function, and `require_workspace_for_stream`
is a second dependency that opens its own transaction, proves the same Membership in it,
and closes it before the first frame is written. Both streaming routes use it and neither
takes a request-scoped session any more; the per-job stream proves its Job exists inside
one short `_tenant_session` instead. Authorization is unchanged — the same Membership,
the same refusals, the same tenant context — only its transaction is now short.

**Measured, not assumed.** Before: while one stream was open the API engine reported
exactly one connection checked out. After: zero. Against the real stack the same run
that had produced repeated pool timeouts produced none across fifteen stream opens, and
every API backend in `pg_stat_activity` sat `idle` rather than `idle in transaction`.
Three browser tests that had been failing on starvation — the Workspace switch, the
empty clip review, and the unsupported-source refusal — went green without being
touched, which is what confirms they were symptoms rather than test bugs.

Two tests hold the rule. `test_an_open_stream_holds_no_request_scoped_database_connection`
watches the pool while a stream is open, and
`test_no_streaming_route_depends_on_the_request_scoped_session` asserts the invariant over
both streaming routes' dependency graphs, so a future stream cannot reintroduce it. Both
were watched failing first, and both fail again when the fix is reverted.


### Defect fixed after Task 30 — a Project could not be created in a browser

The second finding from the first browser run. `POST /api/v1/projects` requires an
`Idempotency-Key`, and the create form sent none, so the request was refused with a
`VALIDATION_ERROR` every time: **creating a Project through the UI had never worked.**
Only the API path was ever exercised, because the browser tests had not been run and the
component tests stub `fetch` and never assert the header.

The form now mints one key per submission and holds it until the submission succeeds, so
a retry of a failed attempt converges on the Project the first attempt created while a
later submission is new work. Two tests hold it: one asserts the header is sent at all,
one that two separate submissions carry two different keys.

The other routes that require the header — analysis, renders, B-roll, and YouTube
imports — were checked. Every caller that exists sends one; nothing in the browser calls
the render route yet.

### Task 33 — Brand kits, reusable templates, and moment-to-campaign outputs

A Brand Kit is a promise a Workspace makes about how its clips look and what they may
assert. Task 33 makes that promise data, publishes it at a version, holds every clip to
the version it names, and derives supporting copy from one approved cut without inventing
anything or translating anybody.

**Constraints are data, evaluated in both places a clip is judged.** `brands/models.py`
publishes the vocabulary — colours, fonts, a caption size band, allowed alignments, areas
kept clear of type, visual exclusions, required attribution, and forbidden claims — and one
pure function evaluates a composition against it. The editor calls it on every read and
save and reports the violations in `brandViolations`; the render compiler calls the same
function and refuses an export that breaks the kit the composition declares, with the
stable code `RENDER_BRAND_VIOLATION`. Nothing is ever corrected on the member's way past:
a silently fixed clip is an export that no longer matches the preview somebody approved,
and a test proves the composition it judged comes back byte-identical.

Two evaluation decisions are worth recording. **Forbidden claims are exact phrases, not
patterns** — a Workspace-authored regular expression evaluated on every save would be a
denial-of-service surface, and a phrase is what a legal review actually produces. And a
**visual exclusion is judged against media descriptions and drawn text, never against the
caption layer**: the captions are the speaker's own words, and a brand exclusion is not a
licence to rewrite what somebody said. The render worker supplies those descriptions from
`asset_provenance` — the search that found a clip and the prompt that produced it are the
only evidence anybody has about what a stock clip shows.

**A version is published, never rewritten.** Migration `0016` splits a Brand Kit and a
template each into an identity row and an append-only version row, exactly as `clip_edits`
and `clip_edit_revisions` already are. `plan.md` gives `templates` one mutable `version`
column; that shape cannot satisfy the task's own requirement that a saved composition keep
resolving the version it was built against, so the file is shaped for the requirement and
says so in its docstring. Renaming a kit publishes nothing, because a name is not a rule.
Deleting archives: a kit or a look that clips already carry keeps every published version,
and an archived template still resolves for the compositions that name it while no longer
being offered for new ones. The API holds `UPDATE` on the identity rows only, so an
append-only history is a grant rather than a convention.

**Applying a look is by value, and the reference is provenance.** Opening an Edit with a
selected template writes its type into the composition and records `(id, version)`;
opening one with a selected Brand Kit records the kit version the clip is answerable to and
restyles nothing, because a kit says what a look may be rather than what it is. A selection
applies only to an Edit the call actually creates — reaching an Edit somebody already
opened must not restyle their work. Editing the kit afterwards leaves that Revision alone,
and a test proves it.

**Campaign copy is derived, never invented.** `campaigns/generator.py` writes a title, post
copy, a call to action, hashtags, and a thumbnail brief from the clip's own transcript
excerpt and the analysis that chose the moment. The only Clipah-authored words are the
localized scaffolding in `campaigns/localization.py`, which is what makes "nothing was
invented" a property a test can check word by word rather than a promise somebody made.
**A quote is never translated**: English copy about an Indonesian clip still quotes the
speaker in Indonesian, because a translated quote is no longer evidence of what was said.
Indonesian and English each publish their own labels, calls to action, thumbnail direction,
and caveats. Copy is derived from one exact immutable Revision the caller names — never
from "whatever is current" — and a repeated request converges on the copy a member already
read. The clip's declared Brand Kit version supplies the forbidden phrases and the visual
exclusions, so a claim the brand may not make is flagged beside the copy and the exclusions
reach the thumbnail brief. Nothing here publishes: the panel offers a link to the Task 43
composer with the Revision preselected, and a test asserts every request the panel makes is
a read.

**Version 1 uses no language model, and says so.** Every output carries model metadata
recording the deterministic generator and its version, so a member can tell at a glance how
much to trust the copy. A provider adapter would sit behind the same field; no checkbox in
this task asks for one, and inventing an LLM dependency here would have been scope nobody
asked for.

The frontend adds `/dashboard/brand-kits` and `/dashboard/templates` — publish, edit into a
new version, archive, and read archived history — plus the clip-detail campaign panel, and
a look-and-brand selection on the control that opens an Edit, so the versioning the backend
enforces is reachable from the product. Every brand name, look name, and piece of copy is
rendered as React children. Colours are checked against `#RRGGBB` in the browser before a
round trip, and a test proves the invalid one never reaches the backend.

Two supporting changes were needed. `RenderAsset` gained a `description`, because the
compiler cannot watch a clip and had no way to judge an exclusion. And `CONTEXT.md` gained
**Brand Kit** and **Template** entries, since both are now first-class nouns the code,
tests, and commit messages use.

Final verification: Ruff check, Ruff format check, strict mypy, and 1830 backend tests
passed with twelve environment-gated skips at 94.37% coverage; migration `0016`
downgrade/upgrade passed. `pnpm lint`, `pnpm typecheck`, `pnpm test` (329 passed), and
`pnpm build` all passed. Three tests were re-checked by breaking the implementation on
purpose — the render gate, the untranslated quote, and the required-attribution rule — and
each failed as it should. The Playwright suite in `frontend/e2e/brand-campaign.spec.ts` was
written but not run here: it needs Postgres, the backend, the frontend, and browser
binaries running together. No commit was created; the required owner commit message is
`feat: add brand and campaign workflows`.

### Task 34 — The searchable creator content library

`search_documents` is the first table in this schema that holds no authority. Every row is
derived from a Project, a Transcript, an exposed Clip Candidate, or a Campaign Output, and
`clipah/search/indexer.py` can recompute all of them at any moment. That is why it is also
the one tenant table both runtime roles may delete from: losing it costs a rebuild and
nothing else. A document's identifier is a UUID version 5 of what it describes, so two
rebuilds write the same rows rather than a churn of new ones, and `index_project` makes the
stored set *equal* to what the durable rows say — writing what changed and deleting what
lost its source.

Text is indexed twice under one vector. The stemmed half uses the document's own language,
which is what makes "daftar" find "pendaftaran"; the unstemmed `simple` half beside it is
what lets a word typed in the other language still find the document, and what gives a
language this product does not stem an honest literal index. Weights are the document's
name first, its labels next, its text last. `title_normalized` carries the accent- and
punctuation-free form the trigram index is built over, so "cafe kopi" finds `Café Kopi`
and "aktivsi" finds `Aktivasi Pengguna`.

A search asks two questions rather than one predicate with an `OR` in it. The word branch
is answered by the GIN text index; the resemblance branch is answered by the trigram index
through the `<%` operator, and it excludes any document the words already found, so each
document reaches the page from exactly one branch carrying exactly one score. Each branch
carries the cursor and the page bound itself, so the union merges two short lists. Only the
documents that actually reached the page have a highlighted fragment extracted for them.

Three measurements shaped that design, and all three were made against the hundred-thousand
document corpus rather than guessed at. Writing the trigram threshold as a function call
instead of the `<%` operator hid the index and scanned the table. Joining `projects` inside
a ranking branch made the planner walk the tenant index instead of the text index — the join
is redundant there anyway, because archiving a Project deletes its documents in the same
request, and the join still happens when the page is read. And `ts_rank_cd` cost nine times
`ts_rank` while saying almost nothing the weights had not already said, because every
document here is one short segment, one clip, or one name. p95 fell from 1.80 s to 0.28 s.

Nothing leaves the API as markup. `ts_headline` marks a match with two control characters
the indexer strips from every document it writes, and the API splits the result into
`{text, highlighted}` fragments — so a hook containing an `img` tag arrives as that text and
the client decides what a highlight looks like. A result carries its Project, speaker,
timecode, topics, export state, and a deep link, and never a storage key, a provider name,
or a raw payload.

The index is maintained where the truth changes rather than on a schedule: creating,
renaming, soft-deleting, and restoring a Project reindex it in the same request; the
analysis worker reindexes when candidates become durable; and campaign copy is indexed
inside the use case that derives it. `python -m clipah.search.indexer --workspace <id>
--actor <member>` rebuilds one Workspace, and it proves the actor's Membership before it
declares a tenant context, because row-level security needs an actor and a maintenance task
inventing one would be the hole the tenancy rules exist to close.

`LibrarySearch` asks the backend nothing until a member has typed something worth asking,
and offers filters for Project, content type, speaker or guest, topic, language, export
state, and a date window. It is mounted whole at `/dashboard/search`, scoped to clips on the
clips page, and scoped to transcripts on the assets page. A transcript result opens its
Project at its own timecode: `ProjectDetail` signs a fresh five-minute proxy capability when
`?t=` is present, and asks for none when it is not.

Two deliberate judgements are worth recording. A quoted phrase withdraws the resemblance
branch rather than merely outranking it — trigram similarity ignores word order, so it would
have answered `"pricing experiment"` with the same words in the wrong order. And assets
themselves are not indexed: nothing about a stored file is text a person searches for, so
the assets page searches the transcripts a file might illustrate instead. That is recorded
as a deferral rather than hidden behind a screen that looks like it searches assets.

Final verification: Ruff check, Ruff format check, strict mypy, and 1887 backend tests
passed with thirteen environment-gated skips at 94.54% coverage; migration `0017`
downgrade/upgrade and the Alembic drift check passed. `pnpm lint`, `pnpm typecheck`,
`pnpm test` (344 passed), and `pnpm build` all passed. The opt-in load test
(`CLIPAH_SEARCH_LOAD_TEST=1 uv run pytest tests/slow/test_content_search_load.py`) built the
hundred-thousand document corpus and measured p95 0.28 s and median 0.19 s against a 0.5 s
budget. The Playwright suite in `frontend/e2e/content-search.spec.ts` was written but not run
here: it needs Postgres, the backend, the frontend, and browser binaries running together.
No commit was created; the required owner commit message is `feat: add searchable creator
library`.

### Task 35 — Workspace collaboration, project review, and accessibility quality gates

Migration `0018` adds expiring, hashed Workspace invitations and append-only membership
events, review comments, comment resolutions, and review decisions. Composite tenant foreign
keys, row-level security, and least-privilege grants keep every record inside its Workspace.
The collaboration feature flag remains disabled by default. When enabled, owners and admins
can invite explicit roles, revoke pending invitations, change roles, and remove members;
last-owner protection and explicit ownership transfer prevent an ownerless team. Invite
acceptance binds the bearer token to the independently authenticated User and consumes it once.

Every comment and decision names one immutable Edit Revision. Comments support timestamp and
composition-item anchors, resolution and reopening append evidence rather than rewriting the
comment, and a later Revision makes an earlier approval stale. The editor shows plain-text
comments and the current approval state, while reviewers can comment, request changes, approve,
resolve, and reopen with native keyboard controls. Campaign generation now requires approval of
the exact current Revision when collaboration is enabled and keeps the prior behavior when the
flag is disabled.

The accessibility analyzer is pure and deterministic. It reports caption reading speed above
20 characters per second, durations below one second, overlaps, more than two lines, WCAG 2.2
contrast thresholds, declared platform safe-zone placement, platform-control collision, and an
explicit geometry-unavailable notice where composition version 1 has no caption rectangle to
measure. Warnings remain advice: they identify the affected item/time and never alter content.
The editor panel can focus the affected timeline item, uses live regions and labelled controls,
and its representative component state passes `vitest-axe`; the browser workflow is also run
with reduced motion.

Final verification: Ruff check, Ruff format check, strict mypy, and 1919 backend tests passed
with thirteen environment-gated skips at 94.28% coverage. Migration `0018` downgraded to `0017`
and upgraded back to head, and generated-contract plus whitespace checks passed. `pnpm lint`,
`pnpm typecheck`, `pnpm test` (351 passed), and `pnpm build` passed. The Task 35 review workflow
passed in Chromium and WebKit, including authenticated invite acceptance, an immutable-revision
comment, approval, stale approval after a new Revision, keyboard request-changes, reduced motion,
and the accessibility panel. The existing member-removal browser scenario also passed in
Chromium and proves immediate access loss.

Pending Publication cancellation/re-evaluation is deliberately deferred: no Publication table
or state machine exists before Task 37. Task 35 records every membership mutation and exposes
the authorizer needed for that future re-evaluation; Tasks 37 and 42 must apply it when pending
Publications become durable. No commit was created; the required owner commit message is
`feat: add accessible review workflows`.

### Task 36 — Social Account connections and encrypted OAuth Grants

Migration `0019` adds Workspace-isolated, replay-safe OAuth ceremonies, redacted Social Account
metadata, and separately stored encrypted OAuth Grants for YouTube, Instagram, and TikTok. Each
provider uses an exact callback URI, PKCE S256, 256-bit state, and its minimum initial scopes.
Callbacks are consumed once even when consent is incomplete or the secret backend is unavailable;
duplicate explicit connections converge on one account and rotate its grant.

OAuth material is envelope-encrypted with per-grant data keys and authenticated Workspace,
account, provider, grant, and token-version context. The local backend has explicit key references,
current/historical key versions for rotation, sanitized fail-closed errors, and one-use bounded
plaintext leases. Refreshes lock the grant and use SQLAlchemy's optimistic `token_version` guard;
provider rejection cryptographically erases the grant and persists `reconnect_required` without
retaining provider diagnostics. Login Identity, yt-dlp Source Connection, and all three publishing
credential families remain independent even when their external identifiers or display metadata
match.

Owners and admins with recent authentication may connect, refresh, and disconnect; reads require
a live Workspace membership. Every mutation uses the existing CSRF and request-ID boundaries.
Disconnect marks the account unavailable first, calls the narrow future-Publication coordinator,
attempts provider revocation, erases local material on either provider success or failure, preserves
an audit tombstone, and is idempotent. Task 37 supplies the durable Publication implementation
behind that coordinator; production provider HTTP/publishing adapters remain owned by Tasks 39-41.

Final verification: Ruff check, Ruff format check, strict mypy, and 1962 backend tests passed with
thirteen environment-gated skips at 94.22% coverage. Migration `0019` downgraded to `0018`, upgraded
back to head, and the Alembic drift check passed. Landed as `548cb5d` with
`feat: add secure social account connections`.

### Task 37 — Durable Publication orchestration

Migration `0020` adds immutable Publication Batches, one independently recoverable Publication per
Social Account, append-only Publication Attempts and Provider Events, and a transactional outbox.
Every table has composite Workspace foreign keys, forced RLS, and an exact API/worker privilege
split. Render Artifacts now persist the SHA-256 of the actual stored master so confirmation can
freeze the file bytes rather than only the editable composition hash.

The closed provider-neutral state machine covers approval, scheduling, preflight, transfer,
processing, retry, reconnect, truthful cancellation, and terminal outcomes. Prepare requests bind
their idempotency key to a canonical payload fingerprint; confirmation rechecks the latest review
decision and freezes the Edit Revision, master checksum, metadata, destination, consent, approving
actor, IANA display timezone and UTC instant, capability version, and provider policy version.
Retries retain stable provider operation keys and refuse ambiguous creates until reconciliation;
provider events deduplicate by authoritative event ID or stable payload hash.

The scheduler claims bounded due pages with `FOR UPDATE SKIP LOCKED` and commits each transition
with its outbox intent. A real two-scheduler integration race proves one claim. Dispatch reloads
scalar IDs and rechecks live publish authority, Social Account availability, and capability
version immediately before future provider I/O. Authority loss cancels untouched work, capability
drift returns it to approval, and disconnect now invokes the real coordinator to cancel or pause
only that account's unpublished work. The API exposes the explicit prepare, preflight, confirm,
batch/read/list, retry, and cancel ceremonies behind Workspace authorization, recent authentication,
CSRF, strict request shapes, sanitized errors, and tenant-safe absence.

Final verification: Ruff check, Ruff format check, strict mypy, and 2114 backend tests passed with
thirteen environment-gated skips at 93.78% coverage. Migration `0020` downgraded to `0019`, upgraded
back to head, and the Alembic drift and whitespace checks passed. No commit was created; the
required owner commit message is `feat: add durable publication orchestration`.

### Task 38 — Immutable provider renditions and Publication preflight

Migration `0021` adds append-only, Workspace-isolated Social Renditions keyed by the frozen
master checksum, provider, and checked-in profile version. Publications may bind one exact
rendition and can never replace it after binding. Rendition rows retain source/output checksums,
safe FFmpeg arguments and config version, validation evidence, and Render Artifact retention
linkage while the public result deliberately omits private object keys. Both runtime roles have
only `SELECT` and `INSERT`; forced RLS and database triggers enforce tenant isolation and
immutability independently of application code.

Checked-in YouTube, Instagram, and TikTok profiles drive deterministic validation for file size,
duration, container, video/audio codecs, required audio, 9:16 dimensions and aspect ratio, frame
rate, captions, thumbnails, disclosures, overlay safe zones, metadata limits, and promotional
watermarks. TikTok watermark failures return non-destructive remediation and never rewrite the
approved master. Draft preflight records the exact profile and capability versions before consent;
confirmation refuses stale or failed evidence, and dispatch repeats validation against frozen bytes
and choices. Capability, profile, approval, or checksum drift returns untouched work to approval
with a stable diff instead of silently changing provider choices.

Compatible masters are reused byte-for-byte. Otherwise a cancellable shell-free FFmpeg boundary
downloads only the immutable Render Artifact, verifies its checksum, creates the fixed provider
shape, measures and validates the actual output, verifies the uploaded object, and records the
winner. Retries converge on the existing checksum without re-reading mutable Edit state. Tests
cover cache reuse, checksum failures, cancellation before side effects, RLS/privilege boundaries,
append-only persistence, exact Publication binding, and a real checked-in landscape fixture
transcoded to measured 1080x1920 H.264/AAC MP4 at 30 fps.

Final verification: Ruff check, Ruff format check, strict mypy, and 2155 backend tests passed with
thirteen environment-gated skips at 93.69% coverage. Migration `0021` downgraded to `0020`, upgraded
back to head, and the Alembic drift check passed. No commit was created; the required owner commit
message is `feat: add social publication renditions`.

### Task 39 — Official YouTube Shorts publishing adapter

An official YouTube Data API adapter now binds every publish attempt to the exact connected
channel, requires `youtube.upload`, expands to `youtube.force-ssl` only for timed captions, and
maps validated title, UTF-8-bounded description, individual tags, category, made-for-kids choice,
synthetic-media disclosure, privacy, and native `publishAt` scheduling into strict provider
requests. Shorts remain a property-based eligibility result for square or vertical videos up to
three minutes; Clipah never promises YouTube classification and uses no unofficial upload path.

The fail-closed compliance policy forces unaudited uploads to private visibility and freezes the
requested/effective privacy plus restriction in preflight evidence. Confirmation and dispatch
revalidation refuse audit-policy drift and return the Publication to approval with a stable diff.
Audited future schedules use YouTube's private-to-public native transition, remain truthfully
scheduled after processing, and become published only when authoritative provider visibility
changes.

Resumable upload sessions are stored only through a tenant-and-attempt-bound encrypted vault
protocol. Postgres retains the opaque reference, checksum, total and acknowledged bytes,
generation, and ambiguity flag. Transfers send one contiguous 256 KiB-aligned chunk, reconcile
provider progress before retrying ambiguous bytes, preserve the authoritative video ID, and block
automatic replay when an ambiguous final request can no longer be disproved. Status, OAuth,
quota, rate-limit, transient, permanent, and malformed responses normalize to bounded secret-free
errors.

Timed captions and custom thumbnails are explicit post-upload operations with independent durable
states and retry evidence. Their failure never clears the base video ID or falsifies a successfully
processed or published video. The locked persistence coordinator verifies the bound YouTube Social
Account and Social Rendition, appends safe Publication Attempts, and derives only a validated
YouTube permalink.

Final verification: Ruff check and Ruff format check passed; strict mypy passed over 197 source
files; 2,211 backend tests passed with fourteen environment-gated skips at 93.06% coverage. The
focused Task 39 suite passed 53 tests with the private live-upload smoke test skipped by default.
Alembic was already at head and reported no new upgrade operations. Landed in `76d4c4f`
as `feat: publish shorts through youtube api`.

### Task 40 — Official Instagram Reels publishing adapter

An official Instagram API with Instagram Login adapter now publishes Reels through the documented
two-step container flow. `publishing/providers/instagram/oauth.py` holds the least-privilege scope
policy — `instagram_business_basic` plus `instagram_business_content_publish`, both required — the
professional-account eligibility rule, and long-lived token maintenance. A refresh is attempted only
inside Instagram's own window: never before the credential is a day old, never after it has expired,
and only within a week of expiry. The rotated credential travels in an `Authorization` header rather
than a query string, so no provider log retains a reusable token.

`containers.py` holds the container lifecycle. `build_pull_url` is the checkbox about the media URL
made executable: a capability must be HTTPS, must carry no embedded credentials, must end in exactly
the frozen rendition key, must carry none of the nine token-shaped query parameters, and must live
between five minutes and an hour. `MediaPullUrl` has no serializer, and `ContainerCheckpoint.safe_dict`
has no field that could hold one, so the capability cannot reach Postgres by accident. Before any
container is created the adapter proves the rendition is actually fetchable — a HEAD that must return
success, a `video/*` content type, and exactly the frozen byte length — so Instagram is never asked to
pull bytes Clipah has not just seen.

Scheduling is Clipah's, because Instagram has none. `scheduling_decision` returns one of four moves:
wait, create, publish, or recreate an expired container. Creation happens no earlier than thirty
minutes before the requested time, so a container cannot expire unused, and publishing happens no
earlier than the requested instant. A container within fifteen minutes of its twenty-four-hour
lifetime is reported expired rather than published on a guess.

The two ambiguity cases are treated differently on purpose. A lost *creation* response is recorded as
`creationAmbiguous` with no container ID; an unpublished container has no user-visible effect and
expires on its own, so recreating one is safe once the ambiguity is durable. A lost *publish* response
is never repeated. `reconcile_publish` polls the container instead: a container Instagram does not
report as published leaves the Publication retryable, and a published one is resolved against the
account's recent Reels, adopting exactly one match. Zero or several candidates raise the ambiguous
error rather than attaching a wrong permalink.

`InstagramPublishRequest` carries only fields Instagram documents — caption, share-to-feed, cover URL,
thumbnail offset, audio name, location, and up to three distinct collaborators. It forbids extra
fields, so a privacy, draft, visibility, or native-schedule control cannot be smuggled in; six such
names are asserted rejected. An unset optional field is omitted from the container body rather than
sent as an invented default.

`api/routes/instagram_webhooks.py` serves the deauthorization and data-deletion callbacks. Each
verifies Meta's `signed_request` HMAC against the app secret, requires the documented `HMAC-SHA256`
algorithm, and enforces a five-minute replay window before anything is persisted. The route performs
no state transition: it derives a stable digest over the callback kind and the signed payload, hands
that to an awaited sink, and acknowledges. Duplicate deliveries produce the same digest and dispatch
once. Data deletion returns the status URL and confirmation code Meta requires. The form field is read
from the raw body rather than through `python-multipart`, so no dependency was added.

`InstagramPublicationCoordinator` applies container truth to one locked Publication. Reconciliation is
monotonic: a poll sequence at or below the last one applied is ignored, so a late, duplicated, or
reordered event can never regress a published destination. A container error is terminal, an expired
container is retryable, and a permalink from any host but Instagram's is discarded rather than stored.

`Settings` already carried the Instagram publishing, OAuth, API-version, and audit fields from Task 1,
and Postgres already carried the checkpoint JSONB the container evidence uses, so no migration and no
configuration change were needed.

Four invariants were re-checked by breaking the implementation on purpose and watching the matching
test fail: the pull-URL credential check, the pre-container reachability proof, the poll-sequence
monotonicity guard, and the permalink host check.

Final verification: Ruff check and Ruff format check passed; strict mypy passed over 202 source files;
2,314 backend tests passed with fifteen environment-gated skips at 92.86% coverage, and all four new
modules reached complete line and branch coverage. The opt-in sandbox smoke test in
`tests/slow/test_instagram_publisher_smoke.py` was written and was not opted into during verification.
Landed in `1717039` as `feat: publish reels through instagram api`.

### Task 41 — TikTok draft fallback and audited Direct Post

TikTok is the destination where the honest answer is "not yet", and the adapter says so rather than
pretending otherwise. `TikTokPolicy` is the audit gate: with no approval it returns the draft-inbox
mode and freezes evidence naming the requested mode, the effective mode, the restriction, and the
exact action still waiting for the member in the TikTok app. With approval it returns Direct Post and
a null restriction. The Publication contract does not change across that flip, which is the point.

The flip cannot happen silently. `tiktok_policy_evidence` is frozen into the Publication at preflight
and compared again at confirmation and again at dispatch. A destination approved as a draft that has
since become a Direct Post destination returns to `awaiting_approval` with a `tiktokPolicy` diff,
exactly as a YouTube audit change already did.

`oauth.py` holds the Login Kit scope policy. A draft needs `user.info.basic` and `video.upload`;
Direct Post additionally needs `video.publish`, and a draft grant never implies it. Refresh honours
TikTok's rotation: a response that returns the same refresh token is refused as unrotated rather than
persisted as progress, and `invalid_grant` reaches the reconnect lifecycle instead of a retry loop.
Neither token appears in the value's repr, and the client secret never enters a URL.

`transfers.py` holds delivery policy, publish-status truth, sanitized errors, and the durable
coordinator. TikTok reports failure inside a 200 response, so `normalize_tiktok_response` reads the
error envelope rather than the status line, mapping twelve documented conditions onto reconnect,
rate-limited, unavailable, permanent, and URL-ownership codes that carry no `log_id` or provider
prose. `verified_pull_url` enforces what TikTok actually requires: HTTPS, no embedded credentials,
the exact frozen rendition key, no token-shaped query parameter, a five-minute-to-one-hour lifetime,
and membership in a prefix whose ownership this deployment has verified. A deployment with no
verified prefix fails closed rather than earning a provider refusal later.

`adapter.py` refuses to preselect anything. `TikTokPostRequest` gives privacy, all three interaction
toggles, both commercial-content toggles, the AI-generated declaration, and consent no defaults at
all, so omitting any one of them is a validation error rather than a silent choice. Consent is its
own model with individually required music and consumer-terms confirmations.
`require_current_declarations` then re-checks everything against fresh evidence immediately before
submission: creator info and consent must each be under five minutes old, the chosen visibility must
be one the provider currently offers, branded content cannot be posted privately and requires
accepted terms, an interaction the creator has disabled cannot be re-enabled, and the clip must fit
this creator's own `max_video_post_duration_sec`. A draft carries no privacy or interaction fields,
so only the declarations TikTok requires of every creator apply there.

`api/routes/tiktok_webhooks.py` verifies the timestamped HMAC over the exact raw bytes, enforces a
five-minute replay window, refuses a delivery naming another client key, and derives a stable digest
so duplicates dispatch once. It performs no state transition; a worker reconciles. An
`authorization.removed` event never adopts a publish identifier even when one is present in the body.

The coordinator keeps the draft fallback truthful. A delivery to the creator inbox reaches
`processing`, never `published`, and carries the message naming the remaining in-app step. Only a
Direct Post that TikTok reports as complete reaches `published`, with a permalink built from the
frozen creator handle and the one publicly available post identifier. Reconciliation is monotonic, so
a late, duplicated, or reordered event cannot regress a published destination.

Duration, file size, aspect ratio, and the promotional-watermark prohibition were already enforced by
Task 38's preflight against the checked-in TikTok profile; this task adds the per-creator duration
limit that only live `creator_info` can supply.

**One deliberate scope decision.** Delivery uses `PULL_FROM_URL` only; chunked `FILE_UPLOAD` is not
implemented. No checkbox asks for it, the verified-prefix requirement applies only to the pull flow,
and Clipah already stores every rendition behind a signed URL. A side effect worth keeping is that no
TikTok upload URL ever enters the process, so the plan's rule against storing one has nothing to
guard here. Whichever task needs a source that cannot be served over HTTPS owns the chunked path.

Four invariants were re-checked by breaking the implementation on purpose and watching the matching
test fail: the audit gate defaulting to drafts, the refresh-rotation requirement, the verified-prefix
rule, and the return to approval when a destination flips from draft to direct.

Final verification: Ruff check and Ruff format check passed; strict mypy passed over 207 source files;
2,424 backend tests passed with sixteen environment-gated skips at 92.68% coverage, and all four new
modules reached complete line and branch coverage. The opt-in sandbox smoke test in
`tests/slow/test_tiktok_publisher_smoke.py` was written and was not opted into during verification.
Landed in `0176929` as `feat: add audited tiktok publishing`.

### Task 42 — Multi-destination scheduling, dispatch, and reconciliation

Three destinations of one batch are now genuinely independent. `publishing/dispatcher.py` drives one
Publication at a time and owns everything that is identical across providers: the account lock,
revalidation, quota, retry policy, and the truthful terminal state. Provider work reaches it through
an injected `DestinationDriver`, so YouTube, Instagram, and TikTok get the same orchestration rather
than three copies of it. The three adapters meet the dispatcher at exactly one point,
`failure_from_provider_error`, which is covered against the real sanitized error classes of all
three.

Dispatch acquires a bounded, non-blocking advisory lock on the Workspace and Social Account before
any provider call. A second worker holding that account does not queue behind it; it returns
`account_busy` with a short backoff and touches nothing. That keeps one destination's outage from
consuming a worker for the length of a provider timeout.

Revalidation happens immediately before external side effects and refuses in five distinct ways.
A revoked membership cancels, a disconnected account reaches reconnection, a changed capability
version returns to approval, a rendition whose bytes no longer match the approved checksum fails
permanently, and a Publication whose consent evidence is missing fails permanently. None of those
reach a driver at all, which the tests assert by checking the driver recorded nothing.

Retry policy is one function. `backoff_delay` doubles from thirty seconds, applies full jitter so
recovering destinations do not retry in lockstep, never waits less than a provider's own
`Retry-After` hint, and is capped at one hour. A retryable failure records `next_attempt_at` and
enqueues exactly one durable outbox message under a retry-scoped operation key. A reconnect or
permanent refusal enqueues nothing, because retrying an authorization or policy refusal only repeats
it. An exhausted attempt budget becomes `permanent_failed` with the `retry_budget_exhausted` code
rather than looping forever.

Quota is held once per destination, never per attempt. The dispatcher reserves one
`social_publications` unit on the first attempt, settles it when the destination is delivered,
releases it on a terminal refusal, and deliberately keeps it held across a retryable failure and an
ambiguous one. A retryable attempt will run again and still costs one publication; an ambiguous
attempt may already have spent it, and a held budget is evidence. A destination retried after an
outage therefore still has exactly one reservation row.

`publishing/reconciler.py` holds two things the plan asks for separately. `aggregate_batch` derives
batch state from its children — completed, partially failed, failed, cancelled, or in progress — and
counts published, failed, and cancelled destinations, so partial success can never be rounded into a
single verdict. A batch identifier from another Workspace aggregates to `unknown` with a total of
zero rather than revealing anything. The rest of the module recovers stuck work: a bounded
skip-locked page of destinations that have been transferring for an hour or processing for six,
observed through a `ProviderTruthObserver` before any state changes. An unreadable provider produces
no change at all, because an outage during reconciliation must never be mistaken for provider truth,
and a terminal destination is never touched.

`operator_reconcile` is the operator command, and it is deliberately narrow. It refuses a destination
that is not stuck, refuses one outside the caller's Workspace, and refuses to change anything when
the observation comes back `unknown`. Its most useful case is the ambiguous one: a destination whose
delivery could not be proved is resolved from what the provider actually reports, adopting the post
when it exists and becoming an ordinary safe retry when it does not.

`publishing/webhooks.py` is intake only. It resolves the Social Account from the provider identity,
deduplicates by provider event ID or stable payload digest, records the evidence, and enqueues one
reconciliation message for a worker. It performs no state transition, so a replayed delivery records
once and wakes one worker, and a delivery whose signature did not verify is kept as a security
observation while enqueuing nothing.

The end-to-end suite in `tests/e2e/` covers what the checkboxes describe as one story: three
destinations publishing together, one failing and being retried alone while its siblings are never
re-delivered, a permanent refusal leaving the batch partially failed, an ambiguous destination going
through reconciliation before it may be retried, a future schedule surviving a scheduler restart
without publishing early, a cancelled destination dropping out of its own batch, and a live webhook
queueing exactly one reconciliation.

**One deliberate scope decision.** The per-provider drivers that wire each adapter into
`DestinationDriver` are not implemented here. No file in Task 42 names them, the dispatcher's own
contract is provider-neutral by design, and the tests exercise the three-provider matrix through
drivers that raise the adapters' real error types. Whichever task owns the Celery publishing worker
owns that wiring.

Five invariants were re-checked by breaking the implementation on purpose and watching the matching
test fail: the account lock, the schedule guard, the frozen-bytes revalidation, the refusal to treat
an unreadable provider as truth, and the refusal to act on an unsigned webhook.

One process note worth recording. The first full verification run reported four failures in
`tests/contract/test_broll_api.py`, and they were mine rather than the code's: the mutation checks
were running a second pytest process against the same Postgres database, and every test's fixture
truncates all tables. Re-run serially, the suite is clean.

Final verification: Ruff check and Ruff format check passed; strict mypy passed over 210 source
files; 2,499 backend tests passed with sixteen environment-gated skips at 92.81% coverage, and all
three new modules reached complete line and branch coverage. Landed in `147f056` as
`feat: orchestrate scheduled social publishing`.

### Task 43 — Connections, publishing dashboard, composer, history, and rollout gates

Publishing now has a face, and its whole design is refusal. The composer preselects no destination,
no privacy value, no disclosure, and no schedule; each destination is complete only when the member
has answered every question that provider actually asks, and the publish control stays disabled
until then. `features/publishing/destination-draft.ts` holds those answers per account, so nothing a
member wrote for YouTube leaks into a TikTok post, and removing a destination that carries typed
metadata asks before discarding it.

Per-platform controls come from what each provider currently permits rather than from a shared
lowest common denominator. YouTube asks for title, description, visibility, a made-for-kids answer,
and a synthetic-media declaration, and offers only private visibility until this deployment reports
`youtubePublicPrivacy`. Instagram asks for the Reels fields it supports and nothing else. TikTok
reads the creator's own snapshot: the privacy levels this creator has, the interactions TikTok has
switched off for them, the commercial-content disclosure with its branded-content terms and its
refusal of a private branded post, the music and terms confirmations, and the AI declaration. Where
that snapshot cannot be read, the control is disabled rather than guessed.

The confirmation names every effect before anything leaves the Workspace: each account and provider,
whether delivery is a direct post or a draft in the creator's inbox, the revision and rendered
artifact, the requested time in the zone the member chose, the visibility, the disclosures, and that
it cannot be undone from Clipah. Nothing is sent while it is open. One approval sends exactly one
prepare, one preflight, and one confirm, under an idempotency key that is reused when the same
submission is approved again after a failure, so a retried approval cannot become a second post.
The consent instant is frozen when the confirmation opens, which is what makes that key stable.

History and batch detail refuse to collapse a batch into one verdict. Every destination carries its
own status, its own timeline of recorded moments, its provider identifier and permalink when there
is one, an actionable reason when it failed, and its own retry or cancel control. Retry addresses
one destination and never its siblings; a destination the provider may already have published
answers that it has to be reconciled first, in the member's words rather than as a status code.
Cancelling says what it can still stop and what it cannot, and a published destination offers no
cancel control at all. A destination waiting on approval shows which approved value drifted, and one
whose authorization is gone offers reconnection instead of a retry.

Rollout gates are read from the deployment, not from the code. `GET /api/v1/me` now reports
`socialPublishing`, `youtubePublishing`, `youtubePublicPrivacy`, `instagramPublishing`,
`tiktokPublishing`, `tiktokDirectPost`, and `multiDestinationScheduling`, in the plan's own order,
and the new `CLIPAH_MULTI_DESTINATION_SCHEDULING_ENABLED` setting refuses to open without social
publishing behind it. A closed gate hides its provider entirely; a second destination is refused
until multi-destination scheduling is open.

**Two deliberate scope decisions, both recorded rather than quiet.**

The first is that the destination projection had to grow. Task 43 asks for per-destination
timelines, provider identifiers and links, actionable retry reasons, and `awaiting_approval` diffs,
and `PublicationResponse` carried none of them. It now carries the batch identifier, the provider
publication identifier and permalink, the normalized error code and sanitized message, the attempt
count and next attempt, every lifecycle instant, and a `preflightDiff` read only from the approval
drift inside a checkpoint that also holds transfer state. The checkpoint itself is still never
exposed, which a test asserts by planting a secret beside the diff.

The second is the end-to-end suite. Driving a real publication in a browser needs a provider's own
OAuth credentials, which a local or CI deployment does not have, so `e2e/social-publishing.spec.ts`
proves what a real stack can prove without them: the dashboard's honest empty state, connections
offering exactly the providers whose gates are open, an unknown batch refused exactly like a
forbidden one, a refusal shown instead of an empty timeline, and the composer refusing to publish
until a destination is chosen. It was run twice against the real backend, once with every gate
closed and once with the YouTube gate open, so both branches were exercised. A deployment with
provider credentials should extend it with the connected-account path.

Three invariants were re-checked by breaking the implementation and watching the matching test fail:
the publishing role policy that decides whether an editor may publish, the idempotency key that
keeps a repeated approval from publishing twice, and TikTok's refusal of a private branded-content
post. The backend's sanitized approval diff was mutation-checked the same way.

Final verification: backend Ruff check, Ruff format check, and strict mypy over 210 source files all
passed, with 2,503 tests at 92.84% coverage; `scripts/check-contracts-clean.sh` reports the
generated contract and client up to date; frontend lint, `tsc --noEmit`, and 411 unit tests passed;
the full Playwright suite passed 77 tests across Chromium and WebKit with the fourteen pre-existing
environment skips; and `scripts/run-social-provider-contracts.sh --adapter=fake` passed its 230
provider contracts while the sandbox adapter failed closed for missing credentials, as designed. No
commit was created; the required owner commit message is `feat: add social publishing workspace`.


## Browser suite: first run, and what it found

`pnpm test:e2e` had never been run since the specs were written in Task 18. Running it
needed two things that did not exist: `uvicorn` is in no lockfile, because every test
until now drove the application in process, so the API was served with
`uv run --with uvicorn` against a scratch entrypoint rather than by changing the
lockfile; and `playwright.config.ts` starts `pnpm dev`, whose first-hit route compilation
races the five-second assertion timeout, so the suite was run against a production build.

The first run was 9 passed and 12 failed. The final state is **21 passed, 0 failed, and
12 skipped on both Chromium and WebKit** — the skips being the seven `test.fixme`
scenarios and the engine-parity gates that need a reference machine.

What the twelve failures actually were, since the mix is the useful part:

- **Six were symptoms of the connection-pool defect above.** They went green when the
  stream fix landed, without being touched.
- **One was a product defect**: the create form's missing idempotency key.
- **Four were test bugs.** Three specs matched Next's own injected
  `<div role="alert" id="__next-route-announcer__">` with a bare `getByRole('alert')`,
  which Playwright refuses in strict mode; `e2e/support/locators.ts` now excludes it by
  id, because scoping to `main` is wrong for a page that is nothing but an error and
  renders no landmark. And `broll-review.spec.ts` used the bare `request` fixture, which
  carries no cookies, so its setup failed CSRF before reaching a route — a pattern copied
  from `clips-review.spec.ts`, now `page.request` in both.
- **One was environmental**: the object store was not configured for the run, so uploads
  answered `503`. The browser suite needs `CLIPAH_OBJECT_STORE_*` and a provisioned
  bucket, which is recorded below rather than fixed, because Task 46 owns process
  configuration.


## Seeding an analysed Project, and the scenarios it unblocked

Five browser scenarios had been `test.fixme` since Task 18 for one reason: no API can
create a Clip Candidate, because it is the analysis worker's output, so nothing could
stage a *real* clip to edit. `clipah.dev.seed --with-clip NAME` now writes what that
pipeline would have written — a ready Project, its source and proxy Assets, a Transcript
whose words are spaced across the clip so a trim has something to move, and one exposed
Clip Candidate.

**It writes them as the two roles that really own them.** The API may insert Projects and
Assets; the worker may insert Transcripts and Clip Candidates; neither may do both, and
an API process may not even open a worker engine. The seed therefore runs two
transactions under two roles and refuses outright when it holds only one of the two
logins. A seed that needed privileges no runtime role has would be staging a state the
pipeline itself could never reach, which would make every scenario built on it a fiction.

Four scenarios were converted from skipped to running, and one more was added:

- a reviewer turns a candidate into an edit exactly once
- a member trims a real clip and the Revision survives a reload
- a member splits a real clip and the timeline shows both halves
- a member writes a text overlay and it survives a reload
- a clip with no plan yet offers to find B-roll and nothing else

The suite is now **27 passed, 0 failed, 8 skipped on both Chromium and WebKit**, up from
21 and 12.

One of those tests caught a mistake worth recording, because it is the kind a browser
test exists to catch. The trim scenario first waited for the editor to read "Saved" —
which it already does before anything has been edited, so the wait returned instantly and
the reload raced the autosave. The fix was to wait on the save's own response rather than
on the words beside it. The product was correct throughout; the test was asserting a
state that was never false.

**Two scenarios stay `test.fixme`, for reasons that are not about seeding.** Removing a
member needs the endpoint Task 35 builds. Accepting a B-roll suggestion needs one the
planner actually produced, which means a language model and a stock provider: seeding a
licensed picture would mean inventing provenance, and refusing to do that is the whole
point of Task 29. The engine-parity gate still needs a reference machine.


### Task 44 — Structured observability, provider usage, and operational dashboards

Complete and awaiting the owner's commit. Backend gates: Ruff check, Ruff format check,
strict mypy over 215 files, and 2557 passed with 16 skipped at 92.84% coverage. The four
new modules are at complete coverage. The frontend was untouched apart from one
regenerated docstring in `contracts/openapi.json`, so lint, typecheck, 411 Vitest tests,
and `scripts/check-contracts-clean.sh` were run and pass.

**The design decision that carries this task is that a log event may only use field names
somebody declared.** `LOG_FIELDS` is an allowlist; anything else is dropped and counted as
`droppedFields` rather than written. A denylist fails the first time somebody is creative,
and "no transcript, cookie, or token ever reaches a log" has to be a property of the
system rather than a habit of its authors. Values that survive the allowlist are then
scrubbed for authorization headers, absolute URLs, `name=value` assignments, local
filesystem paths, and high-entropy runs. The scrubber deliberately leaves stable error
codes, UUIDs, and provider request identifiers intact, because a record that says nothing
is not worth retaining; `RENDER_ENCODER_UNAVAILABLE` survives and `ya29.…` does not, and
both facts are tested.

The same scrubber runs over span attributes, metric labels, and Sentry events. Sentry's
`before_send` removes headers, cookies, and request bodies outright rather than scrubbing
them, because no header this application sends is worth the risk of retaining one that
carries a session. A failing span records the exception type and never the traceback.

**Instrumentation went in at chokepoints rather than at call sites.** One HTTP middleware,
one Celery entry point, `session_scope`, an `ObservedObjectStore` decorator over the
storage protocol, and one shared usage recorder cover HTTP, jobs, the database, object
storage, and every paid provider request without scattering telemetry through domain code.
The two hand-written `_usage_row` helpers in the analysis and B-roll planners were replaced
by `record_provider_usage`, which writes the ledger row and emits the units and cost
metrics together: a row cannot be alerted on and a metric cannot be billed, so a deployment
needs both. Transcription and generated media now record usage too, and generation cost is
published per exported minute rather than per job, because cost per job cannot be compared
across projects.

Every instrument is declared with the labels it accepts, and `count`/`observe` refuse an
undeclared metric or label at the call site. A typo that silently invents a metric is a
dashboard that is quietly always empty, and one unbounded label ends a metrics backend.

**The deprecation monitor deliberately knows almost nothing in code.** Only the Sora
shutdown date is baked in, because it is the one date `plan.md` states. Every other
announced retirement reaches the monitor through `CLIPAH_PROVIDER_SHUTDOWNS`, written
`model:<identifier>=YYYY-MM-DD` or `api:<identifier>=YYYY-MM-DD` and refused at startup if
malformed. Inventing sunset dates for provider API versions would have produced confident
warnings about retirements nobody announced. The monitor warns 180 days ahead and fails
`GET /health/ready` only once a configured version's date has passed: failing early takes
down a deployment that could still schedule the migration, and failing late lets it learn
about the retirement from its own users.

`scripts/check-observability.sh` runs the local trace smoke test, the readiness monitor,
and a redaction check against whatever configuration the shell has. It passes.

Four invariants were mutation-checked, and each one failed exactly its own test: the log
allowlist, the metric label guard, the retired-version failure, and the readiness gate in
the health route.

**Three earlier deferrals named Task 44 as their owner, and none of them is a Task 44
checkbox.** They are recorded here rather than quietly built:

- *Per-provider-request stock metering.* Task 44 supplies the per-request accounting that
  was missing, so this is now unblocked, but changing what `BROLL_RETRIEVE` charges is a
  quota-policy change no checkbox here asks for. It belongs to whichever task revisits
  quota policy.
- *Database enforcement for the quota ledger's polymorphic reference.* Unchanged and still
  open; it needs the ledger-wide redesign Task 37 described.
- *Reindexing a Project after a render lands.* Still open. A finished render now emits its
  bytes and speed ratio, but nothing here changes when the search index is rebuilt, so a
  clip's `exported` state can still lag until the next reindex.

## Deferrals

Work deliberately left for the task that owns it, recorded so it is not mistaken for an
oversight.

| Deferred | Owner |
| --- | --- |
| Serving the API as a process, and the object-store configuration a browser run needs (`CLIPAH_OBJECT_STORE_ENDPOINT`, `_BUCKET`, `_ACCESS_KEY_ID`, `_SECRET_ACCESS_KEY`, and a provisioned bucket). `uvicorn` is in no lockfile, because every test drives the application in process; the browser suite was run with `uv run --with uvicorn` against a scratch entrypoint | Task 46, with the containerized processes |
| Pointing the browser suite at a production build. `playwright.config.ts` starts `pnpm dev`, whose first-hit route compilation races the five-second assertion timeout | Task 47, with CI |
| The clip page's other halves — Revision history and exports — and the navigation into it. Task 30 wired only the B-roll a clip carries, and the page still needs its Project named in the URL because no route resolves a Clip Candidate to its Project | Tasks 34 and 35, with the content library and project review |
| Retrieving *stock* images, so a member can accept a still they did not pay a model for. Task 31 generates stills, and the editor places them as image overlays; both stock adapters still query the video endpoints only | whichever task revisits stock retrieval — Task 34 built the content library and no checkbox in it touches the stock adapters |
| Offering genuinely new alternatives for a replacement. Replacement swaps to another asset the Project owns, because only the selected candidate is ever downloaded | whichever task can pay for a second search inside the providers' terms |
| Measuring a live stock provider. Every test uses fixtures or fakes, so the recorded behaviour proves the adapters rather than either provider's catalogue; `tests/slow/test_stock_provider_smoke.py` is written and opt-in | the repository owner, with Pexels and Pixabay credentials |
| A real vision model behind `FrameRelevanceProvider`. The port, the recording of model and version, and the fake are all in place; no provider is wired, so sampled-frame relevance is reported as unmeasured in production | whichever task adopts a vision provider |
| Richer user-asset search. A Workspace's own footage is matched on the query its provenance recorded, which covers reuse; genuinely user-uploaded B-roll carries no description to match on yet | whichever task gives an uploaded Asset a description — Task 34 indexes Projects, transcripts, clips, and campaign copy, none of which describes raw footage |
| Charging the monthly stock-request budget per provider request. `BROLL_RETRIEVE` reserves the `stock_requests` quota once per Job through the existing admission path; metering each provider call separately needs the per-request accounting Task 44 introduces | Task 44 |
| Charging a metered Workspace budget for B-roll planning; a plan spends a concurrency slot and no quota, because the plan's limit table names no planning budget. This is the same gap the render deferral records | Tasks 44-46, with operational cost accounting |
| Visual scene detection. `scene_boundaries` derives cuts from silence gaps and speaker changes, which is what a transcript can actually evidence; shot-change detection on the proxy would give placement real cuts to respect | whichever task adds shot detection to the pinned image |
| ~~Generating a suggestion when retrieval finds nothing, and the browser surface for it~~ — landed in Task 31 | done |
| Measuring a live planning provider. Every test uses the fake provider, so the recorded behaviour proves the pipeline rather than any model's judgement about what deserves a picture | the repository owner, with real Groq credentials |
| ~~Workspace invites, role mutation, member removal, ownership transfer~~ | done in Task 35 |
| Removing `@elah/core` and `elah-adapter.ts`, and the FFmpeg frame/timing parity gate the ADR still owes. Task 24 built the renderer the gate compares against, but running it needs browser binaries, long-form proxy media, and a reference machine together, which no session so far has had | the repository owner, then Task 26 |
| A brand-mark policy: the watermark is compiled from one deployment-wide `CLIPAH_RENDER_WATERMARK_TEXT` setting, because composition version 1 carries no watermark field and Brand Kits do not exist yet | Task 33, with brand kits |
| Rendering burned-in captions and drawn text through real FFmpeg; the local build has no libass or libfreetype, so those filters were exercised by the compiler's tests rather than by an encode | the repository owner, inside the pinned image |
| A worker readiness gate for the filters the renderer depends on (`subtitles`, `drawtext`, `zoompan`); `validate_render_readiness` currently checks the pinned FFmpeg version only | Task 46, with the containerized processes |
| Charging a metered Workspace budget for an export; a render spends a concurrency slot and no quota, because no render budget exists in the plan's limit table | Tasks 44-46, with operational cost accounting |
| Frame and timing parity against the native FFmpeg renderer — the fixture render belongs to Task 24, so the scenario is a `test.fixme` rather than a test that would pass by doing nothing | Task 24 (`plan.md:1263-1290`) |
| Frame-accurate preview compositing through Mediabunny — captions, crop, and overlays decoded into one canvas. The basic editor plays the proxy through the `PreviewEngine` port and draws captions and crop over it, which is honest for trim and caption work but is not what the export will look like pixel for pixel | Tasks 25 and 26, as a second implementation of the same port |
| Detecting the faces smart crop reasons about, and the endpoint that would carry its suggestions into the editor. `assets/smart_crop.py` is the policy and is fully tested; nothing in the pipeline produces a face box yet, and no image in the plan pins a detector | the task that adds a face detector to the pinned image |
| Preview-side golden frames. The gate compares an FFmpeg render against a signed-off frame; comparing a *browser preview* frame against the same golden needs the Mediabunny compositor that Tasks 25 and 26 both deferred | whichever task builds the frame-accurate preview compositor |
| Golden frames for burned-in captions, karaoke, and drawn text; they need `subtitles` and `drawtext`, so the generator and the suite both skip them on a build without libass and libfreetype | the repository owner, inside the pinned image |
| Failing CI below the perceptual threshold; the gate fails the suite, but no CI configuration exists to run it | Task 47 |
| Blend modes other than `normal`, and scale or rotation keyframes. The schema can express them and the renderer cannot reproduce them faithfully, so the compiler refuses them and the editor does not offer them | whichever task can prove a faithful FFmpeg reproduction |
| Waveform display under a timeline item; nothing in the pipeline produces a `waveform` Asset yet, so there is no data to draw. Snapping, bookmarks, ripple editing, scene organization, and pointer drag/resize all landed in Task 25 | Task 26, once a waveform rendition exists |
| Detaching a base video item's own audio. Composition version 1 carries no per-item mute, so a detached copy would play twice; Task 25's extract-audio places one asset's audio on its own extracted-audio lane instead | Task 26, with the per-item controls that would need the field |
| Free positioning and multiple lanes of picture. The base video lane is played by concatenation, so dragging there reorders and the compiler refuses both a gap and a second video track rather than exporting a clip that does not match the preview | whichever task gives the renderer a compositing base timeline |
| Re-deriving caption timings when a trim extends an item back out again, and captions for a span added from the source monitor or duplicated on the timeline; those words are not in the composition, and recovering them means reading the Transcript | Task 26, with karaoke and caption retiming |
| Assets a composition may use are the owning Project's own Assets. A Workspace-wide library — a Brand Kit logo, or B-roll reused across Projects — will need the authorization set widened beyond one Project | Tasks 29 and 33 |
| Revision history is returned newest-first with a fixed ceiling of 100 entries and no cursor; a clip edited past that will need pagination | Task 26, with the styling and template history |
| Wiring `scripts/check-contracts-clean.sh` into a CI workflow; the check exists and is negative-controlled, but no CI configuration exists yet | Task 47 |
| Reconciling the bake-off fixture `contracts/fixtures/editor/parity-composition.json`, which is frame-based, with composition version 1, which is millisecond-based; the fixture drives the engine contract test rather than the product | Task 24, with the FFmpeg parity gate |
| Server-side candidate filtering and sorting — the ranking policy exposes a bounded set (ten by default) and the review page reads all of it before offering any control, so no ordering is invented over a partial list. A larger exposed set would need `category` and duration query parameters on `GET /projects/{project_id}/candidates` | whichever task raises the exposure limit |
| Enabling authenticated YouTube import in production. The feature is built and tested, and `docs/security/youtube-import.md` records four open items — legal approval, data retention, incident response, and whether production wraps data keys with a managed key — each of which blocks enablement | the repository owner |
| A managed key-management service client for wrapping data keys. Task 36 adds a social-specific envelope port, key references/versions, historical-key rotation, and fail-closed behavior; local deployments still derive wrapping keys from configured deployment material | Task 46, with production infrastructure wiring |
| Sweeping expired source connections. A connection past its window is reported as expired and refuses every lease, but the row and its material are removed only when a member revokes it | Task 45, with retention |
| A landmark on the editor's loading and error states. A page that is nothing but an error renders no `main`, so nothing anchors a screen reader; the alert itself is correct and announced | Task 35, with the accessibility quality gates |
| A member-visible list of a Project's own past Jobs; the panel follows the one Job the Project is currently working through, and the Workspace-wide job center holds the rest | Task 35 (`plan.md:1571-1600`), with project review |
| ~~The Playwright member-removal scenario~~ | done in Task 35 |
| Accepting a B-roll suggestion in a browser, marked `test.fixme`. The seed stages the pipeline's output up to the clip; staging a licensed picture would mean inventing provenance, which Task 29 exists to refuse | the repository owner, with real Groq and stock credentials |
| A coverage floor for the frontend suite, and feature-level UI tests; Task 17 has smoke coverage only | Tasks 18-20 |
| Removing the legacy Flask UI, its Tailwind CDN and unpkg Lucide script tags, and `static/script.js`; the new UI depends on none of them but the files still serve the legacy deployment | Task 48 |
| Replacing `nixpacks.toml` with per-process Railway deployment configuration for the frontend and backend | Task 46 |
| Workspace delete and restore endpoints | Task 45 |
| Reconcile provider multipart uploads orphaned by a crash or late database failure before a durable upload row exists | Task 45 — Task 8 supplies the `maintenance` queue this sweep will run on |
| Bind readiness to a real configured object-store probe instead of the current no-op default | Tasks 44 and 46 |
| Remaining metered job-creation routes that map `QuotaExceededError` onto the HTTP envelope; Task 10 now maps source-import concurrency admission | Tasks 11-16 |
| Real stage runners for remaining `JobKind` values; Task 10 now registers `SOURCE_IMPORT`, while unsupported remaining kinds fail with `JOB_KIND_UNSUPPORTED` | Tasks 11-16 and later pipeline tasks |
| Database enforcement for the quota ledger's polymorphic `reference_kind`/`reference_id`. Task 37 proved a Publication-only constraint breaks the existing reserve-before-resource contract, so any enforcement needs a ledger-wide reference redesign | Task 44 |
| Per-Social-Account provider publish limits, currently exercised through generic `social_account:<uuid>` limiter subjects | Tasks 42 and 44, when real provider dispatch and operational limits exist |
| Settling generated video seconds and image counts against real provider usage | Tasks 30-32 |
| Estimating and settling the real provider cost of an analysis; `provider_usage` currently records units without a price | Tasks 16 and 30-32 |
| The evaluation audio corpus itself — Task 15 checks in sanitized synthetic transcripts, not the two hours of source audio the plan asks for before a provider is frozen, nor the five hours asked for before public launch; the manifests record the shortfall in their audio-coverage fields | the repository owner, before the provider decision in Task 16 and before public launch |
| Measuring a live provider — the checked-in run uses the offline adapters, so the recorded scores prove the harness rather than any provider's quality | the repository owner, after supplying the real-audio corpus and explicit live credentials |
| Running the whole pipeline against live providers — the belt exists and every stage is wired, but no run has used real AssemblyAI or Groq credentials | the repository owner, before trusting Phase B's exit gate |
| Measuring a live generative provider. Every test uses the fake provider or a local transport, so the recorded behaviour proves the adapters rather than either provider's output; `tests/slow/test_generation_provider_smoke.py` is written and opt-in | the repository owner, with fal and Runway credentials |
| Generating an alternative for a suggestion that already carries generated media. Task 31 offers generation for an empty or below-threshold beat only; regenerating a picture a member did not like needs a decision about what happens to the first one | whichever task adds regeneration |
| A language-model adapter for campaign copy. Version 1 derives copy deterministically and records that in every output's model metadata; no checkbox in Task 33 asks for a provider, and the port to add one is the `model_metadata` field itself | whichever task decides copy quality needs one |
| ~~Gating campaign generation on a recorded review approval~~ | done in Task 35 |
| ~~Re-evaluating publish authority immediately before dispatch~~ — landed in Task 37. Proactively sweeping pending Publications on every membership mutation remains deferred | Task 42, using Task 35 membership audit events |
| Indexing Assets themselves. Nothing about a stored file is text a person searches for, and no checkbox in Task 34 asks for it; the assets screen searches the transcripts a file might illustrate instead | whichever task gives an Asset searchable text of its own |
| Reindexing after a render lands. Export state is recomputed whenever a Project is reindexed, but a finished render does not trigger one, so a clip's `exported` state can lag until the next reindex of its Project | Task 44, with the observability pass over worker completions |
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
