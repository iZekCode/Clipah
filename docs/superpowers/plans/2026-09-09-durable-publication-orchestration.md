# Durable Publication Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this
> plan inline. The repository owner, not the agent, creates commits.

**Goal:** Build restart-safe, tenant-isolated Publication preparation, confirmation, scheduling,
state transitions, idempotency, and transactional outbox delivery.

**Architecture:** A pure transition module protects lifecycle rules; a tenant-scoped repository
owns SQL and locks; use cases coordinate immutable approval snapshots; a scheduler claims due rows
and writes an outbox record atomically. Provider adapters remain outside Task 37.

**Tech Stack:** Python 3.13, FastAPI, Pydantic 2, SQLAlchemy 2, PostgreSQL 17, Alembic, Celery,
pytest, Ruff, and strict mypy.

**Spec:** `docs/superpowers/specs/2026-09-09-durable-publication-orchestration-design.md`

## Global Constraints

- Workspace is the tenant boundary; all child foreign keys are composite by Workspace.
- Use Section 4 Publication state names exactly.
- No provider SDK types or secrets may enter publishing domain values, JSON, logs, or errors.
- Every production behavior begins with a focused failing test.
- The agent does not commit or write Git history.

---

### Task 1: Pure Publication state machine

**Files:**
- Create: `backend/src/clipah/publishing/__init__.py`
- Create: `backend/src/clipah/publishing/models.py`
- Create: `backend/src/clipah/publishing/state_machine.py`
- Create: `backend/tests/contract/test_publication_states.py`

**Interfaces:**
- Produces: `PublicationStatus`, `PublicationTransition`, `transition(status, target)`,
  `may_cancel(status, provider_cancellable)`.

- [ ] Write table-driven tests with literal allowed and rejected transition pairs, all terminal
  states, recovery states, and truthful late cancellation.
- [ ] Run `uv run pytest tests/contract/test_publication_states.py -q`; verify missing-module RED.
- [ ] Implement immutable enums/results and the smallest transition table that passes.
- [ ] Re-run the focused contract test; verify GREEN.

### Task 2: Publication persistence and tenant constraints

**Files:**
- Create: `backend/migrations/versions/0020_publications.py`
- Modify: `backend/src/clipah/models.py`
- Modify: `backend/tests/integration/test_schema.py`
- Create: `backend/tests/integration/test_publications.py`

**Interfaces:**
- Produces ORM rows for `PublicationBatch`, `Publication`, `PublicationAttempt`, `ProviderEvent`,
  and `PublicationOutbox`.

- [ ] Add failing schema tests for tables, enums, composite foreign keys, tenant-leading indexes,
  RLS, runtime grants, append-only children, snapshot checks, and outbox uniqueness.
- [ ] Run the focused schema tests and verify absence failures.
- [ ] Add ORM declarations and migration `0020` with downgrade symmetry.
- [ ] Run upgrade, schema tests, downgrade to `0019`, upgrade to head, and drift checks.

### Task 3: Repository, event deduplication, and outbox

**Files:**
- Create: `backend/src/clipah/publishing/repository.py`
- Create: `backend/src/clipah/publishing/outbox.py`
- Extend: `backend/tests/integration/test_publications.py`

**Interfaces:**
- Produces: tenant-scoped reads/locks, `append_attempt`, `record_provider_event`,
  `enqueue_outbox`, `pending_outbox`, and `acknowledge_outbox`.

- [ ] Write failing tests proving cross-Workspace absence, append-only history, event-ID/hash
  deduplication, one pending outbox message per operation, and replay after an unacknowledged read.
- [ ] Run focused tests and verify behavior-specific RED failures.
- [ ] Implement repository SQL and immutable outbox messages without commits inside repositories.
- [ ] Re-run focused tests and verify GREEN.

### Task 4: Draft preparation and immutable confirmation

**Files:**
- Create: `backend/src/clipah/publishing/use_cases.py`
- Extend: `backend/src/clipah/publishing/models.py`
- Extend: `backend/tests/integration/test_publications.py`

**Interfaces:**
- Produces: `prepare_publication_draft`, `preflight_publication_draft`,
  `confirm_publication_draft`, `get_publication_batch`, `list_publications`, and
  `get_publication`.

- [ ] Write failing tests for exact Edit Revision/Render checksum/account/capability/policy/copy/
  consent/actor/timezone/UTC snapshots and mutation resistance.
- [ ] Add failing tests for one Publication per destination, same-key replay, changed-payload
  conflict, duplicate destination rejection, inactive account rejection, schedule validation, and
  cross-Workspace 404 behavior.
- [ ] Implement strict Pydantic inputs, canonical payload hashing, locked confirmation, and stable
  summaries.
- [ ] Re-run focused tests and verify GREEN.

### Task 5: Scheduler claims and authorization/capability recheck

**Files:**
- Create: `backend/src/clipah/publishing/scheduler.py`
- Create: `backend/src/clipah/publishing/tasks.py`
- Extend: `backend/tests/integration/test_publications.py`

**Interfaces:**
- Produces: `PublicationScheduler.claim_due(now, limit) -> list[UUID]` and scalar-only task entry
  points that consume outbox work.

- [ ] Write concurrent failing tests proving bounded claims, `SKIP LOCKED`, no duplicate claim,
  UTC due boundaries, restart recovery, and atomic state/outbox writes.
- [ ] Add failing tests for removed-member authority, disconnected account, changed capability
  version, and unchanged eligible dispatch.
- [ ] Implement claim SQL and task-side live rechecks; capability drift returns to approval and
  disconnected accounts become reconnect-required.
- [ ] Re-run focused concurrency/crash tests and verify GREEN.

### Task 6: Retry, cancellation, and Task 36 coordination

**Files:**
- Extend: `backend/src/clipah/publishing/use_cases.py`
- Modify: `backend/src/clipah/api/app.py`
- Extend: `backend/tests/integration/test_publications.py`
- Modify: `backend/tests/integration/test_social_accounts.py`

**Interfaces:**
- Produces: `retry_publication`, `cancel_publication`, and
  `PublicationFutureWorkCoordinator.cancel_or_pause_unpublished`.

- [ ] Write failing tests for retryable-only recovery, provider-ambiguous reconciliation before
  retry, cancellable scheduled/preflight work, rejected late cancellation, terminal idempotency,
  and account-disconnect cancellation scoped to one Workspace/account.
- [ ] Implement locked retry/cancel transitions and wire the real coordinator into app state.
- [ ] Re-run publication and Social Account integration tests; verify GREEN.

### Task 7: Publication HTTP contract

**Files:**
- Create: `backend/src/clipah/api/routes/publications.py`
- Modify: `backend/src/clipah/api/app.py`
- Extend: `backend/tests/integration/test_publications.py`

**Interfaces:**
- Produces the Task 37 publication endpoints from Section 5 with sanitized response models.

- [ ] Write failing API tests for draft/preflight/confirm/read/list/retry/cancel, CSRF, permission,
  recent-auth, idempotency header, strict bodies, stable errors, and secret-field absence.
- [ ] Implement route-only validation/mapping and include the router in the app factory.
- [ ] Export OpenAPI and verify generated backend contract remains deterministic.
- [ ] Re-run focused API tests and verify GREEN.

### Task 8: Full verification and handoff

**Files:**
- Modify: `PROGRESS.md`

**Interfaces:**
- Produces a verified Task 37 handoff with no Git commit.

- [ ] Run `uv run ruff check .`.
- [ ] Run `uv run ruff format --check .`.
- [ ] Run `uv run mypy src`.
- [ ] Run `uv run pytest -q --cov=clipah --cov-fail-under=90`.
- [ ] Run migration downgrade/upgrade and Alembic drift checks.
- [ ] Run generated-contract and `git diff --check` checks.
- [ ] Update `PROGRESS.md`: Task 36 is landed as `548cb5d`; Task 37 is complete awaiting owner
  commit; record tests, coverage, migration, deferrals, and `feat: add durable publication orchestration`.
