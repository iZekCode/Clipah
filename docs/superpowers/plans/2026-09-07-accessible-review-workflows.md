# Accessible Workspace Review Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This repository's owner has required inline execution without subagents.

**Goal:** Add secure Workspace collaboration, immutable Edit Revision review, and deterministic accessibility quality gates with complete backend and frontend authorization coverage.

**Architecture:** Extend the existing live `DatabaseWorkspaceAuthorizer` and tenant schema rather than adding a second permission system. Membership and review mutations are transaction-scoped use cases backed by append-only audit records; accessibility analysis is a pure module over validated composition documents. The frontend consumes regenerated OpenAPI types and exposes only role-allowed controls while the backend remains authoritative.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL RLS, pytest; Next.js 15, React 19, TypeScript, TanStack Query, Vitest/Testing Library, Playwright, axe-core.

**Spec:** `docs/superpowers/specs/2026-09-07-accessible-review-workflows-design.md`

## Global Constraints

- Follow `AGENTS.md`, `plan.md`, and `CONTEXT.md`; new work belongs only in `backend/` and `frontend/`.
- The agent never commits, pushes, rebases, or writes Git history. The owner commit message is `feat: add accessible review workflows`.
- Every tenant row carries `workspace_id`; tenant child relationships use composite Workspace foreign keys and RLS.
- Authorization reads a live Workspace Membership before tenant context is declared; guessed and missing identifiers return identical public `404` envelopes.
- Membership mutations require CSRF and recent authentication; all public errors use the fixed sanitized envelope.
- Every behavior begins with a failing test whose expected failure is observed before production code changes.
- Backend completion requires Ruff check, Ruff format check, strict mypy, and pytest with at least 90% coverage.
- Frontend completion requires lint, typecheck, unit tests, and production build; user/provider text is rendered only as React children.
- Collaboration routes are invisible when `CLIPAH_COLLABORATION_ENABLED=false`; tests explicitly cover both states.
- Fixed accessibility thresholds are 20 characters/second, 1,000 ms minimum duration, two lines, WCAG ratios 4.5:1 normal text and 3:1 large text, plus documented platform safe/control regions.

---

### Task 1: Expand the collaboration schema safely

**Files:**
- Create: `backend/migrations/versions/0018_workspace_collaboration.py`
- Modify: `backend/src/clipah/models.py`
- Modify: `backend/tests/integration/test_schema.py`
- Test: `backend/tests/integration/test_workspace_collaboration_schema.py`

**Interfaces:**
- Produces: `WorkspaceMembershipEvent`, `EditReviewComment`, `EditReviewCommentResolution`, and `EditReviewDecision` ORM models plus closed event/decision enums.
- Consumes: existing `Workspace`, `WorkspaceMembership`, `ClipEdit`, `ClipEditRevision`, `User`, RLS roles, and naming conventions.

- [x] **Step 1: Write schema tests that fail before migration `0018` exists.**

  Add tests which upgrade from `0017` to `0018`, inspect columns/constraints/grants, insert tenant-correct rows, reject cross-Workspace composite references, reject invalid anchors, and prove audit/update/delete privileges are absent. The core assertions use literal names:

  ```python
  EXPECTED = {
      "workspace_membership_events",
      "edit_review_comments",
      "edit_review_comment_resolutions",
      "edit_review_decisions",
  }
  assert EXPECTED <= set(inspect(connection).get_table_names())
  assert _grants(connection, "edit_review_decisions", "clipah_api") == {"SELECT", "INSERT"}
  ```

- [x] **Step 2: Run the focused schema tests and observe RED.**

  Run from `backend/`:

  ```bash
  uv run pytest -q tests/integration/test_workspace_collaboration_schema.py tests/integration/test_schema.py
  ```

  Expected: failure because revision `0018` and its tables/models do not exist.

- [x] **Step 3: Implement the additive migration and ORM models.**

  Define append-only event enums and models with these stable relationships:

  ```python
  class EditReviewDecisionKind(StrEnum):
      REQUEST_CHANGES = "request_changes"
      APPROVE = "approve"

  class ReviewAnchorKind(StrEnum):
      TIMESTAMP = "timestamp"
      ITEM = "item"
  ```

  Use composite foreign keys `(workspace_id, clip_edit_id)` and `(workspace_id, clip_edit_revision_id)`, mutually exclusive timestamp/item anchor checks, non-empty bounded comment text, RLS, API-only writes, and no destructive data migration. `downgrade()` drops only the four new tables and their enum types in dependency order.

- [x] **Step 4: Verify upgrade, constraints, downgrade, and re-upgrade are GREEN.**

  Run the focused test command, then:

  ```bash
  uv run alembic downgrade 0017
  uv run alembic upgrade head
  ```

---

### Task 2: Complete invitations and membership mutation

**Files:**
- Create: `backend/src/clipah/workspaces/memberships.py`
- Create: `backend/src/clipah/api/routes/workspace_memberships.py`
- Modify: `backend/src/clipah/workspaces/models.py`
- Modify: `backend/src/clipah/workspaces/authorization.py`
- Modify: `backend/src/clipah/config.py`
- Modify: `backend/src/clipah/api/errors.py`
- Modify: `backend/src/clipah/api/app.py`
- Modify: `backend/src/clipah/api/routes/auth.py`
- Test: `backend/tests/integration/test_workspace_memberships.py`
- Test: `backend/tests/unit/test_workspace_authorization.py`

**Interfaces:**
- Produces: `create_invite`, `accept_invite`, `revoke_invite`, `change_member_role`, `remove_member`, `transfer_ownership`, and membership/invite/event read models.
- Produces: `Settings.collaboration_enabled: bool = False` and an auth feature response field `collaboration`.
- Consumes: `WorkspaceAccess`, `WorkspaceAction`, `DatabaseWorkspaceAuthorizer`, injected `now`, and injected token generator.

- [x] **Step 1: Write failing integration tests for the complete membership lifecycle.**

  Cover explicit roles, SHA-256-only persistence, one-time raw token response, expiry, revocation, replay, acceptance by an authenticated User whose login email differs, reactivation of removed membership, personal-Workspace refusal, role permissions, last-owner races, transfer, immediate access loss, audit actors, CSRF, recent auth, cross-Workspace identifiers, and disabled-feature invisibility. A representative assertion is:

  ```python
  created = owner.post(path, json={"email": "invite@example.test", "role": "reviewer"})
  token = created.json()["token"]
  assert token not in _database_text(engine)
  accepted = invitee.post(f"/api/v1/workspace-invites/{token}/accept")
  assert accepted.json()["role"] == "reviewer"
  ```

- [x] **Step 2: Run membership and authorization tests and observe RED.**

  ```bash
  uv run pytest -q tests/integration/test_workspace_memberships.py tests/unit/test_workspace_authorization.py
  ```

  Expected: missing membership service/routes and collaboration setting.

- [x] **Step 3: Implement transaction-scoped membership use cases.**

  Use explicit signatures and injected nondeterminism:

  ```python
  def create_invite(session: Session, *, access: WorkspaceAccess, email: str,
                    role: WorkspaceRole, now: datetime,
                    token: str) -> CreatedInvite: ...
  def accept_invite(session: Session, *, user_id: UUID, token: str,
                    now: datetime) -> MemberSummary: ...
  def change_member_role(session: Session, *, access: WorkspaceAccess,
                         member_user_id: UUID, role: WorkspaceRole,
                         now: datetime) -> MemberSummary: ...
  def remove_member(session: Session, *, access: WorkspaceAccess,
                    member_user_id: UUID, now: datetime) -> None: ...
  def transfer_ownership(session: Session, *, access: WorkspaceAccess,
                         member_user_id: UUID, now: datetime) -> tuple[MemberSummary, MemberSummary]: ...
  ```

  Hash tokens with SHA-256 before queries, lock active owner rows for demotion/removal/transfer, retain removed rows, append one audit event per state change, and never compare invite email to authenticated identity.

- [x] **Step 4: Add feature-gated routes and stable error mappings.**

  Disabled collaboration routes raise `ApiError(status_code=404, code="NOT_FOUND")`. Use existing `require_workspace()` dependencies for member reads/manage/transfer and `require_csrf` on writes. Accepting a token authenticates first, then establishes the invite Workspace context only after the hashed token identifies a live invite.

- [x] **Step 5: Run focused tests until GREEN, then run the existing auth/project route regressions.**

  ```bash
  uv run pytest -q tests/integration/test_workspace_memberships.py tests/unit/test_workspace_authorization.py tests/integration/test_auth_routes.py tests/integration/test_projects.py
  ```

---

### Task 3: Add immutable Edit Revision reviews

**Files:**
- Create: `backend/src/clipah/editor/reviews.py`
- Create: `backend/src/clipah/api/routes/edit_reviews.py`
- Modify: `backend/src/clipah/editor/repository.py`
- Modify: `backend/src/clipah/campaigns/use_cases.py`
- Modify: `backend/src/clipah/api/routes/campaigns.py`
- Modify: `backend/src/clipah/api/app.py`
- Modify: `backend/src/clipah/api/errors.py`
- Test: `backend/tests/integration/test_edit_reviews.py`
- Modify: `backend/tests/integration/test_campaigns.py`

**Interfaces:**
- Produces: `add_comment`, `resolve_comment`, `record_decision`, `review_summary`, and `has_current_approval`.
- Consumes: immutable `ClipEditRevision`, current `ClipEdit.current_revision`, validated composition item IDs/duration, and `WorkspaceAccess`.

- [x] **Step 1: Write failing review tests from the Task 35 matrix.**

  Cover exact Revision IDs, timestamp and item anchors, bounds, plain text, comment resolution/reopening, current approval, request-changes precedence, stale approval after autosave, campaign refusal without current approval, removed member access, viewer refusal, audit ordering, and indistinguishable identifiers. Assert immutable history directly:

  ```python
  approved = reviewer.post(review_path, json={"revisionId": revision_id, "decision": "approve"})
  assert approved.json()["current"] is True
  _save_next_revision(editor, edit_id)
  history = reviewer.get(review_path).json()
  assert history["decisions"][0]["current"] is False
  assert history["decisions"][0]["revisionId"] == revision_id
  ```

- [x] **Step 2: Run the focused review suite and observe RED.**

  ```bash
  uv run pytest -q tests/integration/test_edit_reviews.py
  ```

- [x] **Step 3: Implement review queries and mutations.**

  Keep the public service API independent of FastAPI:

  ```python
  def add_comment(session: Session, *, access: WorkspaceAccess, edit_id: UUID,
                  revision_id: UUID, text: str, anchor: ReviewAnchor,
                  now: datetime) -> ReviewComment: ...
  def record_decision(session: Session, *, access: WorkspaceAccess, edit_id: UUID,
                      revision_id: UUID, decision: EditReviewDecisionKind,
                      now: datetime) -> ReviewDecision: ...
  def has_current_approval(session: Session, *, workspace_id: UUID,
                           edit_id: UUID) -> bool: ...
  ```

  Resolve item anchors against the stored composition and timestamps against its duration. Derive state from append-only rows; never update historical decisions.

  Gate campaign generation through `has_current_approval`; the caller must name the exact
  currently approved Revision. A stale or request-changes Revision returns a stable
  `REVIEW_APPROVAL_REQUIRED` error without creating Campaign Output rows.

- [x] **Step 4: Implement review routes and error translation.**

  Reads require `PROJECT_READ`; writes require `REVIEW_DECIDE`, CSRF, and the collaboration feature. Responses include actor display name, immutable Revision identity/number, anchor, current/stale state, and ordered history but no composition internals unrelated to the review.

- [x] **Step 5: Run review and Edit regression tests until GREEN.**

  ```bash
  uv run pytest -q tests/integration/test_edit_reviews.py tests/integration/test_edit_revisions.py tests/integration/test_campaigns.py
  ```

---

### Task 4: Lock the full permission matrix as a contract

**Files:**
- Create: `backend/tests/contract/test_workspace_permissions.py`
- Modify: `backend/src/clipah/workspaces/models.py`
- Modify: `backend/src/clipah/workspaces/authorization.py`
- Modify: routes that still enforce owner/role checks outside `WorkspaceAuthorizer`

**Interfaces:**
- Produces: a closed action vocabulary covering every domain present after Task 34 and the `PUBLISH` policy future publication routes consume.
- Consumes: every route's declared `require_workspace(Action.*)` dependency.

- [x] **Step 1: Write a literal role/action table and route-boundary tests.**

  The test must not derive expected permissions from `ROLE_ACTIONS`:

  ```python
  EXPECTED = {
      WorkspaceRole.VIEWER: {WorkspaceAction.WORKSPACE_READ, WorkspaceAction.MEMBER_READ,
                             WorkspaceAction.PROJECT_READ},
      WorkspaceRole.REVIEWER: {WorkspaceAction.WORKSPACE_READ, WorkspaceAction.MEMBER_READ,
                               WorkspaceAction.PROJECT_READ, WorkspaceAction.REVIEW_DECIDE},
  }
  assert {action for action in WorkspaceAction if permits(access(role), action)} == expected
  ```

  Complete the editor/admin/owner sets literally and exercise Workspace, membership,
  Project, Asset, Job, Candidate, Edit, B-roll, Render, Brand, Template/Campaign, Review,
  Source Connection, Social Account policy, and Publication policy boundaries.

- [x] **Step 2: Run the contract and observe RED for missing or misplaced actions.**

  ```bash
  uv run pytest -q tests/contract/test_workspace_permissions.py
  ```

- [x] **Step 3: Expand named actions and replace route-local role checks.**

  Add distinct read/write actions only where two roles legitimately differ; keep publishing outside the monotonic role sets. Every route declares authority through `require_workspace` or calls `DatabaseWorkspaceAuthorizer.require` inside a worker transaction.

- [x] **Step 4: Run the contract plus all backend contract tests until GREEN.**

  ```bash
  uv run pytest -q tests/contract
  ```

---

### Task 5: Implement deterministic accessibility analysis

**Files:**
- Create: `backend/src/clipah/editor/accessibility.py`
- Extend: `backend/src/clipah/api/routes/edit_reviews.py`
- Create: `backend/tests/unit/test_accessibility_quality.py`
- Extend: `backend/tests/integration/test_edit_reviews.py`

**Interfaces:**
- Produces: `analyze_accessibility(document: dict[str, Any], platform: Platform | None) -> tuple[AccessibilityWarning, ...]`.
- Consumes: composition version 1 validation and existing platform packaging/safe-zone definitions.

- [x] **Step 1: Write failing table-driven tests for every fixed threshold.**

  Hand-derive expected codes for 20/20.01 characters per second, 999/1,000 ms duration,
  touching/overlapping intervals, two/three lines, 4.49/4.5 and 2.99/3 contrast, safe-zone
  edges, platform control intersections, unavailable geometry, stable ordering, and no
  document mutation:

  ```python
  warnings = analyze_accessibility(document_with_caption(text="x" * 21, duration_ms=1_000))
  assert [warning.code for warning in warnings] == ["caption_reading_speed"]
  assert document == original
  ```

- [x] **Step 2: Run the unit suite and observe RED.**

  ```bash
  uv run pytest -q tests/unit/test_accessibility_quality.py
  ```

- [x] **Step 3: Implement the pure analyzer with documented constants.**

  Define frozen result values with stable code, severity, action, item ID/time range, measured value, and threshold. Reuse platform packaging geometry; compute WCAG relative luminance from validated `#RRGGBB`; sort by severity, time, item, and code. Return an explicit `geometry_unavailable` warning rather than fabricating bounds.

- [x] **Step 4: Add the authorized accessibility read endpoint.**

  Load one immutable Revision (current by default, explicit ID when supplied), validate it belongs to the Edit/Workspace, run analysis, and return deterministic JSON. The endpoint stays available when collaboration mutations are disabled.

- [x] **Step 5: Run unit/integration tests until GREEN.**

  ```bash
  uv run pytest -q tests/unit/test_accessibility_quality.py tests/integration/test_edit_reviews.py
  ```

---

### Task 6: Build team, review, and accessibility UI

**Files:**
- Create: `frontend/features/team/TeamSettings.tsx`
- Create: `frontend/features/reviews/ReviewPanel.tsx`
- Create: `frontend/features/editor/AccessibilityPanel.tsx`
- Modify: `frontend/app/dashboard/team/page.tsx`
- Modify: `frontend/features/editor/EditorScreen.tsx`
- Modify: `frontend/app/dashboard/projects/[projectId]/page.tsx`
- Modify: `frontend/app/dashboard/clips/[clipId]/page.tsx`
- Create: `frontend/tests/accessibility-quality.test.tsx`
- Create: `frontend/tests/team-settings.test.tsx`
- Create: `frontend/tests/review-panel.test.tsx`
- Modify: `frontend/package.json`
- Modify: `pnpm-lock.yaml`

**Interfaces:**
- Produces: keyboard-accessible team management, Project job/review history, revision review, and warning navigation.
- Consumes: generated membership/review/accessibility hooks, `useWorkspaceScope`, editor selection/seek dispatch, and deployment feature flags.

- [x] **Step 1: Export OpenAPI and regenerate the client before writing UI tests.**

  ```bash
  scripts/export-openapi.sh
  pnpm generate:api
  ```

  Generated files are never edited by hand.

- [x] **Step 2: Add the axe test dependency using pnpm.**

  ```bash
  pnpm --filter clipah-frontend add -D vitest-axe
  ```

- [x] **Step 3: Write failing component tests.**

  Use real components with MSW/fetch boundaries consistent with existing frontend tests.
  Assert role-hidden controls, invite validation, confirmation/focus return, live status,
  Project past-Job display, Revision/export navigation, stale approval, anchor selection,
  warning navigation, reduced motion, error/loading `main` landmarks, plain-text rendering,
  and axe results:

  ```typescript
  const { container } = render(<AccessibilityPanel warnings={warnings} onSelect={onSelect} />)
  expect(await axe(container)).toHaveNoViolations()
  await user.click(screen.getByRole('button', { name: /caption is too fast/i }))
  expect(onSelect).toHaveBeenCalledWith({ itemId: 'caption-1', timeMs: 1200 })
  ```

- [x] **Step 4: Run the new frontend tests and observe RED.**

  ```bash
  pnpm --filter clipah-frontend test -- tests/accessibility-quality.test.tsx tests/team-settings.test.tsx tests/review-panel.test.tsx
  ```

- [x] **Step 5: Implement the three components and page/editor integration.**

  Use generated hooks through the common `apiFetch` mutator, invalidate membership/review queries after successful mutations, gate collaboration UI on the auth feature response, keep the backend authoritative, and add `main` landmarks around editor loading/error states. Extend the Project/clip surfaces with their existing Job, Revision, review, and export reads instead of duplicating persistence. Use native buttons/fields and existing dialog primitives for keyboard/focus behavior.

- [x] **Step 6: Run focused frontend tests until GREEN, then lint/typecheck.**

  ```bash
  pnpm --filter clipah-frontend test -- tests/accessibility-quality.test.tsx tests/team-settings.test.tsx tests/review-panel.test.tsx
  pnpm lint
  pnpm typecheck
  ```

---

### Task 7: Prove the browser workflows and finish Task 35

**Files:**
- Create: `frontend/e2e/team-review.spec.ts`
- Modify: `frontend/e2e/support/seed.ts`
- Modify: `PROGRESS.md`

**Interfaces:**
- Produces: owner/member/reviewer browser proof and the Task 35 handoff record.
- Consumes: completed collaboration/review/accessibility APIs and UI.

- [x] **Step 1: Write failing Playwright scenarios.**

  Cover owner invitation, authenticated acceptance, reviewer comment/approval, editor revision making approval stale, request changes, member removal causing immediate loss, keyboard-only review, reduced motion, and representative axe checks. Re-enable the existing member-removal `test.fixme` rather than duplicating it.

- [x] **Step 2: Run the focused browser suite and observe the expected initial failures.**

  ```bash
  pnpm test:e2e -- team-review.spec.ts
  ```

  If required local services or browser binaries are unavailable, record the exact environmental failure and continue with every non-browser gate; do not mark the scenario passed.

- [x] **Step 3: Make only test-support changes required for real browser state.**

  Seed real Users, Memberships, Edit Revisions, and Sessions through the existing API/runtime-role pattern; do not insert fictional provider or Publication data.

- [x] **Step 4: Run migration verification and all backend gates.**

  From `backend/`:

  ```bash
  uv run alembic downgrade 0017
  uv run alembic upgrade head
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy src
  uv run pytest -q --cov=clipah --cov-fail-under=90
  ```

- [x] **Step 5: Run every frontend gate.**

  From the repository root:

  ```bash
  pnpm lint
  pnpm typecheck
  pnpm test
  pnpm build
  ```

- [x] **Step 6: Check generated contracts and whitespace.**

  ```bash
  scripts/check-contracts-clean.sh
  git diff --check
  ```

- [x] **Step 7: Update `PROGRESS.md` truthfully.**

  Mark Task 34 with landed commit `2b4a49b`; mark Task 35 complete only if every required
  non-environmental gate is green. Record exact test counts, coverage, migration result,
  browser status, deliberate Publication deferral, and the owner commit message
  `feat: add accessible review workflows`. Do not run `git commit`.
