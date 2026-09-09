# Social Publication Renditions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate frozen publication media against versioned provider rules and create or reuse immutable provider renditions without consulting mutable Edit state.

**Architecture:** Pure profile/preflight modules normalize provider policy and return deterministic public reports. A tenant-scoped immutable cache stores rendition bytes and provenance, while the Publication flow runs the same validation before confirmation and immediately before dispatch.

**Tech Stack:** Python 3.13, FastAPI domain use cases, SQLAlchemy 2, PostgreSQL 17/RLS, Alembic, FFmpeg/ffprobe 7.1.5, pytest.

**Spec:** `docs/superpowers/specs/2026-09-09-social-publication-renditions-design.md`

## Global Constraints

- New behavior belongs only in `backend/`; the legacy stack is untouched.
- The canonical master is the existing `1080x1920` H.264/AAC Render Artifact.
- Cache identity is `(workspace_id, source_sha256, provider, profile_version)`.
- Provider rules are checked-in versioned values; no runtime network lookup is permitted.
- TikTok promotional watermarks are rejected with remediation and are never removed.
- Every tenant row carries `workspace_id`, composite foreign keys, forced RLS, and least privilege.
- Domain code reads no wall clock and uses no randomness except injected/server-generated record IDs.
- Tests precede production code and must be observed failing for the intended missing behavior.
- The agent never commits; the owner receives the exact Task 38 commit message at handoff.

---

### Task 1: Versioned profiles and deterministic preflight

**Files:**
- Create: `backend/src/clipah/publishing/profiles.py`
- Create: `backend/src/clipah/publishing/preflight.py`
- Create: `backend/tests/unit/test_publication_preflight.py`

**Interfaces:**
- Produces: `ProviderProfile`, `MediaFacts`, `PublicationEvidence`, `PreflightViolation`, `PreflightReport`, `profile_for(provider, version=None)`, and `preflight(profile, media, evidence)`.
- Consumes: `SocialProvider` and frozen Publication metadata/options/consent.

- [ ] **Step 1: Write failing profile and media-validation tests**

  Add literal table cases proving rejection for byte size, duration, container, video/audio codec, aspect ratio, resolution, frame rate, missing audio, captions, thumbnails, disclosure, safe-zone overlays, promotional watermarks, and metadata bounds. Each assertion names the expected stable violation code, such as `file_size`, `duration`, `video_codec`, or `promotional_watermark`.

- [ ] **Step 2: Run the focused tests and verify RED**

  Run: `uv run pytest -q tests/unit/test_publication_preflight.py`

  Expected: collection fails because `clipah.publishing.profiles` and `clipah.publishing.preflight` do not exist.

- [ ] **Step 3: Implement immutable profiles and result values**

  Define frozen dataclasses with exact typed bounds. `profile_for` accepts only checked-in profile versions and raises `UnknownProviderProfileError` for an unknown pair. Add conservative initial versions for all three `SocialProvider` values.

- [ ] **Step 4: Implement pure deterministic validation**

  `preflight` returns violations in a fixed field order, with `code`, `field`, `message`, and `remediation`. It never includes internal paths or provider payloads. TikTok mark evidence always blocks and recommends producing a clean master.

- [ ] **Step 5: Run the unit tests and verify GREEN**

  Run: `uv run pytest -q tests/unit/test_publication_preflight.py`

  Expected: all tests pass.

### Task 2: Immutable tenant-scoped rendition persistence

**Files:**
- Create: `backend/migrations/versions/0021_social_renditions.py`
- Modify: `backend/src/clipah/models.py`
- Create: `backend/src/clipah/publishing/renditions.py`
- Create: `backend/tests/integration/test_social_renditions.py`

**Interfaces:**
- Produces: ORM `SocialRendition`; `RenditionProvenance`; `RenditionResult`; `find_cached_rendition(session, workspace_id, source_sha256, provider, profile_version)`; and `record_rendition(...)`.
- Consumes: `RenderArtifact`, `SocialProvider`, `PreflightReport`, and the object-store metadata contract.

- [ ] **Step 1: Write failing schema, RLS, immutability, and cache tests**

  Prove cross-Workspace reads/inserts fail, API and worker roles cannot update/delete provenance, duplicate cache identities converge, source/render linkage is composite, and public result values omit `storage_key`.

- [ ] **Step 2: Run the focused integration tests and verify RED**

  Run: `uv run pytest -q tests/integration/test_social_renditions.py -k 'schema or rls or immutable or cache'`

  Expected: failures identify missing table/model/repository behavior.

- [ ] **Step 3: Add migration and ORM model**

  Migration `0021` creates `social_renditions` with source/output SHA-256 checks, nonnegative size, positive duration, provider/profile cache uniqueness, source Render Artifact retention FK, JSONB provenance/report, forced RLS, and SELECT/INSERT-only API/worker grants. Add nullable composite `publications.social_rendition_id` linkage and preserve Task 37 snapshot immutability.

- [ ] **Step 4: Add cache read and immutable insert behavior**

  `find_cached_rendition` filters by all cache fields and returns a safe result. `record_rendition` persists normalized provenance and handles the uniqueness race by re-reading the winner without changing it.

- [ ] **Step 5: Run persistence tests and verify GREEN**

  Run: `uv run pytest -q tests/integration/test_social_renditions.py -k 'schema or rls or immutable or cache'`

  Expected: all selected tests pass.

### Task 3: Cancellable FFmpeg rendition worker

**Files:**
- Create: `backend/src/clipah/publishing/render_tasks.py`
- Modify: `backend/src/clipah/publishing/renditions.py`
- Modify: `backend/tests/integration/test_social_renditions.py`

**Interfaces:**
- Produces: `build_rendition_arguments(profile, source, output, ffmpeg_path='ffmpeg')`; `SocialRenditionRenderer.render(...)`; and `ensure_social_rendition(...)`.
- Consumes: frozen Render Artifact bytes, `ObjectStore`, `job_workspace`, cancellation/progress callbacks, FFmpeg runner/probe, and Task 2 cache operations.

- [ ] **Step 1: Write failing reuse, render, checksum, and cancellation tests**

  Prove a compliant master causes no upload, an incompatible master produces one deterministic output, repeat calls reuse its checksum, only the frozen source bytes are read, source/output/storage checksum mismatches fail safely, and cancellation stops before each side effect.

- [ ] **Step 2: Run focused tests and verify RED**

  Run: `uv run pytest -q tests/integration/test_social_renditions.py -k 'master or render or checksum or cancel'`

  Expected: failures identify missing rendition execution behavior.

- [ ] **Step 3: Implement fixed FFmpeg execution**

  Build an argument tuple containing only fixed flags, profile-derived numeric values, and Job-local paths. Emit MP4/H.264/AAC/yuv420p/faststart with the version string `social-rendition-v1`; never accept user text or shell syntax.

- [ ] **Step 4: Implement cache-first orchestration**

  Verify source object length/SHA-256, reuse the master when it passes, otherwise render/probe/preflight/upload/verify/persist. Use deterministic keys derived from Workspace, source checksum, provider, and profile version. Check cancellation before download, FFmpeg, upload, and persistence.

- [ ] **Step 5: Run golden-media and orchestration tests and verify GREEN**

  Run: `uv run pytest -q tests/integration/test_social_renditions.py`

  Expected: all tests pass, with only the established environment-gated native-media skip where FFmpeg is unavailable.

### Task 4: Pre-confirmation and pre-dispatch integration

**Files:**
- Modify: `backend/src/clipah/publishing/use_cases.py`
- Modify: `backend/src/clipah/publishing/tasks.py`
- Modify: `backend/src/clipah/publishing/models.py`
- Modify: `backend/tests/integration/test_publications.py`
- Modify: `backend/tests/integration/test_social_renditions.py`

**Interfaces:**
- Produces: stored public preflight reports/diffs, profile version approval evidence, and immediate dispatch revalidation.
- Consumes: Task 1 preflight, Task 2 rendition identity, Task 37 state transitions/outbox, live Social Account capabilities, and `has_current_approval`.

- [ ] **Step 1: Write failing Publication-flow tests**

  Prove preflight blocks confirmation on violations, passing preflight records its capability/profile evidence, confirmation remains explicit, live capability/profile drift produces an ordered diff and returns to `awaiting_approval`, a superseded review approval does the same, and successful dispatch binds the exact rendition.

- [ ] **Step 2: Run focused tests and verify RED**

  Run: `uv run pytest -q tests/integration/test_publications.py tests/integration/test_social_renditions.py -k 'preflight or capability or approval or dispatch'`

  Expected: failures identify the old transition-only preflight and incomplete dispatch guard.

- [ ] **Step 3: Integrate preflight before confirmation**

  Load the frozen artifact and account, select the checked-in provider profile, run pure validation, persist only its public JSON document, and retain `awaiting_approval`. Confirmation rejects any blocking report or stale evidence and snapshots the profile version.

- [ ] **Step 4: Integrate preflight before dispatch**

  Recheck live publish authority, account state, exact review approval, artifact checksum, capability/profile version, and selected rendition. On drift, store a field-level public diff and transition to `awaiting_approval`; on success remain `preflighting` for Task 39-41 adapters.

- [ ] **Step 5: Run Publication integration tests and verify GREEN**

  Run: `uv run pytest -q tests/integration/test_publications.py tests/integration/test_social_renditions.py`

  Expected: all tests pass.

### Task 5: Full verification and handoff

**Files:**
- Modify: `PROGRESS.md`

**Interfaces:**
- Produces: truthful Task 38 completion record and owner handoff.

- [ ] **Step 1: Run migration lifecycle checks**

  Run from `backend/`: `uv run alembic downgrade 0020`, `uv run alembic upgrade head`, then the repository's Alembic drift check.

- [ ] **Step 2: Run all four backend gates**

  Run from `backend/`:

  ```bash
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy src
  uv run pytest -q --cov=clipah --cov-fail-under=90
  ```

- [ ] **Step 3: Run whitespace and scope checks**

  Run: `git diff --check` and inspect `git status --short` to ensure only Task 38 files changed.

- [ ] **Step 4: Update progress and repeat affected gates**

  Record implementation, test totals, coverage, migration result, genuine deferrals, and owner commit message `feat: add social publication renditions`. Re-run `git diff --check` after the documentation edit.
