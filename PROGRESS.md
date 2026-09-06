# PROGRESS.md

Tracks the Clipah rebuild against Section 11 of `plan.md`. Tasks run in order; each one is
complete only when its own checkboxes pass and all four gates in `AGENTS.md` are green.

**Current position:** Tasks 1-30 have landed. **Task 31 is complete and awaiting the owner's
commit.** Task 32 follows.

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
| 31 | Add quota-aware generated-media fallback | `[x]` (uncommitted) |
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


## Deferrals

Work deliberately left for the task that owns it, recorded so it is not mistaken for an
oversight.

| Deferred | Owner |
| --- | --- |
| Serving the API as a process, and the object-store configuration a browser run needs (`CLIPAH_OBJECT_STORE_ENDPOINT`, `_BUCKET`, `_ACCESS_KEY_ID`, `_SECRET_ACCESS_KEY`, and a provisioned bucket). `uvicorn` is in no lockfile, because every test drives the application in process; the browser suite was run with `uv run --with uvicorn` against a scratch entrypoint | Task 46, with the containerized processes |
| Pointing the browser suite at a production build. `playwright.config.ts` starts `pnpm dev`, whose first-hit route compilation races the five-second assertion timeout | Task 47, with CI |
| The clip page's other halves — Revision history and exports — and the navigation into it. Task 30 wired only the B-roll a clip carries, and the page still needs its Project named in the URL because no route resolves a Clip Candidate to its Project | Tasks 34 and 35, with the content library and project review |
| Retrieving *stock* images, so a member can accept a still they did not pay a model for. Task 31 generates stills, and the editor places them as image overlays; both stock adapters still query the video endpoints only | Task 34, with the content library |
| Offering genuinely new alternatives for a replacement. Replacement swaps to another asset the Project owns, because only the selected candidate is ever downloaded | whichever task can pay for a second search inside the providers' terms |
| Measuring a live stock provider. Every test uses fixtures or fakes, so the recorded behaviour proves the adapters rather than either provider's catalogue; `tests/slow/test_stock_provider_smoke.py` is written and opt-in | the repository owner, with Pexels and Pixabay credentials |
| A real vision model behind `FrameRelevanceProvider`. The port, the recording of model and version, and the fake are all in place; no provider is wired, so sampled-frame relevance is reported as unmeasured in production | whichever task adopts a vision provider |
| Richer user-asset search. A Workspace's own footage is matched on the query its provenance recorded, which covers reuse; genuinely user-uploaded B-roll carries no description to match on yet | Task 34, with the searchable content library |
| Charging the monthly stock-request budget per provider request. `BROLL_RETRIEVE` reserves the `stock_requests` quota once per Job through the existing admission path; metering each provider call separately needs the per-request accounting Task 44 introduces | Task 44 |
| Charging a metered Workspace budget for B-roll planning; a plan spends a concurrency slot and no quota, because the plan's limit table names no planning budget. This is the same gap the render deferral records | Tasks 44-46, with operational cost accounting |
| Visual scene detection. `scene_boundaries` derives cuts from silence gaps and speaker changes, which is what a transcript can actually evidence; shot-change detection on the proxy would give placement real cuts to respect | whichever task adds shot detection to the pinned image |
| ~~Generating a suggestion when retrieval finds nothing, and the browser surface for it~~ — landed in Task 31 | done |
| Measuring a live planning provider. Every test uses the fake provider, so the recorded behaviour proves the pipeline rather than any model's judgement about what deserves a picture | the repository owner, with real Groq credentials |
| Workspace invites, role mutation, member removal, ownership transfer | Task 35 (`plan.md:1588-1595`) |
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
| A managed key-management store for wrapping data keys. `LocalSecretStore` wraps with key material this deployment holds; `CLIPAH_SECRET_MANAGER_KEY_NAME` is configured for but not yet implemented against | Task 36, which needs the same envelope for OAuth grants |
| Sweeping expired source connections. A connection past its window is reported as expired and refuses every lease, but the row and its material are removed only when a member revokes it | Task 45, with retention |
| A landmark on the editor's loading and error states. A page that is nothing but an error renders no `main`, so nothing anchors a screen reader; the alert itself is correct and announced | Task 35, with the accessibility quality gates |
| A member-visible list of a Project's own past Jobs; the panel follows the one Job the Project is currently working through, and the Workspace-wide job center holds the rest | Task 35 (`plan.md:1571-1600`), with project review |
| The Playwright member-removal scenario, marked `test.fixme` — only `GET /workspaces/{workspaceId}/members` exists, so there is no removal to drive | Task 35 (`plan.md:1588-1595`) |
| Accepting a B-roll suggestion in a browser, marked `test.fixme`. The seed stages the pipeline's output up to the clip; staging a licensed picture would mean inventing provenance, which Task 29 exists to refuse | the repository owner, with real Groq and stock credentials |
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
| Running the whole pipeline against live providers — the belt exists and every stage is wired, but no run has used real AssemblyAI or Groq credentials | the repository owner, before trusting Phase B's exit gate |
| Measuring a live generative provider. Every test uses the fake provider or a local transport, so the recorded behaviour proves the adapters rather than either provider's output; `tests/slow/test_generation_provider_smoke.py` is written and opt-in | the repository owner, with fal and Runway credentials |
| Generating an alternative for a suggestion that already carries generated media. Task 31 offers generation for an empty or below-threshold beat only; regenerating a picture a member did not like needs a decision about what happens to the first one | whichever task adds regeneration |
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
