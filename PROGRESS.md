# PROGRESS.md

Tracks the Clipah rebuild against Section 11 of `plan.md`. Tasks run in order; each one is
complete only when its own checkboxes pass and all four gates in `AGENTS.md` are green.

**Current position:** Task 19 is ready to commit. **Task 20 follows.**

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
| 19 | Build resumable upload and safe YouTube import UX | `[~]` (ready to commit) |
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


## Deferrals

Work deliberately left for the task that owns it, recorded so it is not mistaken for an
oversight.

| Deferred | Owner |
| --- | --- |
| Workspace invites, role mutation, member removal, ownership transfer | Task 35 (`plan.md:1588-1595`) |
| Running the Playwright suite — `frontend/e2e/auth-projects.spec.ts` and `frontend/e2e/upload-analysis.spec.ts` exist and `pnpm test:e2e` runs them, but no run happened in the Task 18 or Task 19 sessions because a full stack and browser binaries were not available | the repository owner, before Task 20 |
| Cookie-based authenticated YouTube import, including its consent and ownership-attestation UI; the public form deliberately offers no cookie control while the server capability and feature flag are off | Task 27 (`plan.md:1333-1360`) |
| A member-visible list of a Project's own past Jobs; the panel follows the one Job the Project is currently working through, and the Workspace-wide job center holds the rest | Task 20 |
| The Playwright member-removal scenario, marked `test.fixme` — only `GET /workspaces/{workspaceId}/members` exists, so there is no removal to drive | Task 35 (`plan.md:1588-1595`) |
| A coverage floor for the frontend suite, and feature-level UI tests; Task 17 has smoke coverage only | Tasks 18-20 |
| Removing the legacy Flask UI, its Tailwind CDN and unpkg Lucide script tags, and `static/script.js`; the new UI depends on none of them but the files still serve the legacy deployment | Task 48 |
| Replacing `nixpacks.toml` with per-process Railway deployment configuration for the frontend and backend | Task 46 |
| Workspace delete and restore endpoints | Task 45 |
| Reconcile provider multipart uploads orphaned by a crash or late database failure before a durable upload row exists | Task 45 — Task 8 supplies the `maintenance` queue this sweep will run on |
| Bind readiness to a real configured object-store probe instead of the current no-op default | Tasks 44 and 46 |
| Remaining metered job-creation routes that map `QuotaExceededError` onto the HTTP envelope; Task 10 now maps source-import concurrency admission | Tasks 11-16 |
| Real stage runners for remaining `JobKind` values; Task 10 now registers `SOURCE_IMPORT`, while unsupported remaining kinds fail with `JOB_KIND_UNSUPPORTED` | Tasks 11-16 and later pipeline tasks |
| A foreign key for the quota reservation's `reference_kind`/`reference_id` — `publications` does not exist yet | Task 37 |
| Per-Social-Account provider publish limits, currently exercised through generic `social_account:<uuid>` limiter subjects | Task 36 |
| Settling generated video seconds and image counts against real provider usage | Tasks 30-32 |
| Estimating and settling the real provider cost of an analysis; `provider_usage` currently records units without a price | Tasks 16 and 30-32 |
| The evaluation audio corpus itself — Task 15 checks in sanitized synthetic transcripts, not the two hours of source audio the plan asks for before a provider is frozen, nor the five hours asked for before public launch; the manifests record the shortfall in their audio-coverage fields | the repository owner, before the provider decision in Task 16 and before public launch |
| Measuring a live provider — the checked-in run uses the offline adapters, so the recorded scores prove the harness rather than any provider's quality | the repository owner, after supplying the real-audio corpus and explicit live credentials |
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
