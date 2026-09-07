# Context-Safe Clip Variants Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a ranked Clip Candidate into evidenced context warnings, honest duration and
hook variants with platform packaging, and User-provided claim evidence — without rendering,
publishing, or editing anything.

**Spec:** `docs/superpowers/specs/2026-09-06-context-safe-variants-design.md`

**Tech Stack:** Python 3.13, FastAPI, Pydantic 2, SQLAlchemy 2, Postgres/RLS, Groq,
React 19, Next.js 15, TanStack Query, Vitest, Testing Library, Playwright.

## Global Constraints

- Work only in the rebuild: `backend/` and `frontend/`.
- Write and observe every behavior test failing before production code.
- The agent never commits; each sub-task ends with an owner handoff.
- A provider may only name word IDs. Every timestamp, boundary, and excerpt is resolved
  from the authoritative transcript, and an unresolvable claim is discarded, never repaired.
- No warning is silently dropped by ranking, and no boundary is silently moved.
- Clipah never labels a claim verified; only a User sets a verification status.
- Ordinary tests make no network call and no billable provider request.

---

### Sub-task 1: Context-safety values, rules, and labeled fixtures

**Files:**
- Create: `backend/src/clipah/variants/__init__.py`, `models.py`, `context_safety.py`
- Create: `backend/tests/unit/test_context_safety.py`

- [ ] **Step 1: Write labeled fixture tests in three languages**

Cover cut-off question, cut-off payoff, missing negation, missing attribution, unsupported
reference, omitted caveat, incomplete list, and a safe complete thought — each in
Indonesian, English, and code-switched speech.

- [ ] **Step 2: Run them and observe the missing module**

Run: `cd backend && uv run pytest -q tests/unit/test_context_safety.py`

- [ ] **Step 3: Implement the closed values and the deterministic rules**

`ContextWarningType`, `ContextWarningSeverity`, `ContextWarning` (evidence word IDs, a
suggested boundary), and `assess_context(transcript, start_word_id, end_word_id)`.

- [ ] **Step 4: Run until green, then hand over**

### Sub-task 2: The provider assessment and its validation

**Files:**
- Create: `backend/src/clipah/variants/assessor.py`
- Modify: `backend/src/clipah/variants/context_safety.py`
- Modify: `backend/tests/unit/test_context_safety.py`

- [ ] **Step 1: Write failing assessor contract tests**

A `ContextSafetyAssessor` port with stable retryable/terminal codes and a `ProviderCall`,
mirroring `highlights/provider.py`. Assert that a proposal naming an unknown word ID, a
range outside the candidate, an unknown type, or an impossible suggested boundary is
discarded rather than repaired; that a deployment without a credential still returns the
deterministic warnings; and that no provider text survives a failure.

- [ ] **Step 2: Run and observe the missing assessor**

- [ ] **Step 3: Implement the port, the Groq adapter, and the offline assessor**

Reuse the configured extraction model and the existing retry and error-mapping style. Merge
provider and deterministic warnings, deduplicating by type and evidence range.

- [ ] **Step 4: Run until green, then hand over**

### Sub-task 3: Variant generation and platform packaging

**Files:**
- Create: `backend/src/clipah/variants/generator.py`, `packaging.py`
- Create: `backend/tests/unit/test_clip_variant_generator.py`

- [ ] **Step 1: Write failing generator tests**

At most three hook strategies; only 20/30/45/60/90-second targets, with any other refused
rather than rounded; every boundary a real word ID; a complete thought preserved; a target
that cannot be satisfied safely yields nothing; a blocking warning refuses the variant.

- [ ] **Step 2: Run and observe the missing generator**

- [ ] **Step 3: Implement the strategies, the duration search, and the packaging table**

TikTok, Reels, and Shorts contribute aspect, safe zones, title guidance, caption style, and
an export preset the render compiler already understands.

- [ ] **Step 4: Run until green, then hand over**

### Sub-task 4: Persistence, migration, and RLS

**Files:**
- Modify: `backend/src/clipah/models.py`
- Create: `backend/migrations/versions/0015_variants_and_claim_evidence.py`
- Create: `backend/src/clipah/variants/repository.py`
- Modify: `backend/tests/integration/test_schema.py`

- [ ] **Step 1: Write failing schema tests**

Composite `(workspace_id, id)` foreign keys, RLS policies matching every other tenant table,
API-only grants, and a worker that holds none.

- [ ] **Step 2: Run and observe the missing tables**

- [ ] **Step 3: Add the ORM rows and migration `0015`**

Note in the migration that the plan's `0005` numbering predates ten landed migrations.

- [ ] **Step 4: Run schema tests and an `alembic downgrade`/`upgrade` round trip**

### Sub-task 5: Variant and claim-evidence use cases and routes

**Files:**
- Create: `backend/src/clipah/variants/use_cases.py`, `claim_evidence.py`
- Create: `backend/src/clipah/api/routes/variants.py`, `claim_evidence.py`
- Modify: `backend/src/clipah/api/app.py`, `errors.py`
- Create: `backend/tests/integration/test_clip_variants.py`

- [ ] **Step 1: Write failing API tests**

Identical `404` for missing and foreign candidates; reviewer and viewer refused; CSRF
required; idempotent variant creation; rejected URLs (private network, executable scheme,
embedded credentials, oversized metadata, raw HTML); cross-candidate word IDs refused;
`verification_status` unchanged unless a User sets it, with the actor recorded.

- [ ] **Step 2: Run and observe the missing routes**

- [ ] **Step 3: Implement the use cases and routes**

- [ ] **Step 4: Run until green, then hand over**

### Sub-task 6: Review surfaces in the browser

**Files:**
- Modify: `contracts/openapi.json`, `frontend/lib/api/generated/`
- Create: `frontend/features/clips/ScoreBreakdown.tsx`, `ContextWarnings.tsx`,
  `VariantLab.tsx`, `EvidencePanel.tsx`
- Create: `frontend/tests/clip-variants.test.tsx`
- Create: `frontend/e2e/clip-variants.spec.ts`

- [ ] **Step 1: Write failing component tests**

Warnings and score breakdown visible before edit creation; variants compared against one
proxy with no duplicated source, transcript, or asset; evidence links isolated and rendered
as text; markup in provider or user text rendered as text.

- [ ] **Step 2: Export OpenAPI and regenerate the client**

- [ ] **Step 3: Implement the four components and wire them into clip review**

- [ ] **Step 4: Run frontend tests and the contract check, then hand over**

### Sub-task 7: Evaluation, vocabulary, and progress

**Files:**
- Modify: `backend/src/clipah/highlights/evaluation.py`
- Modify: evaluation fixtures
- Modify: `CONTEXT.md`, `PROGRESS.md`

- [ ] **Step 1: Extend the harness with warning recall/precision and boundary validity**

Record semantic preservation and user acceptance as unmeasured, with the reason.

- [ ] **Step 2: Add Clip Variant, Claim Evidence, and Context Warning to `CONTEXT.md`**

- [ ] **Step 3: Update `PROGRESS.md` honestly, including the `0015` renumbering**

### Sub-task 8: Full verification and handoff

- [ ] **Step 1: Backend gates** — ruff, format, mypy, pytest with coverage
- [ ] **Step 2: Migration round trip and grant verification**
- [ ] **Step 3: Frontend gates** — lint, typecheck, test, build
- [ ] **Step 4: Contract cleanliness and `git diff --check`**
- [ ] **Step 5: Inspect the final diff against Task 32's checkboxes**
- [ ] **Step 6: Hand off with the owner commit message**

```text
feat: add context-safe clip variants
```
