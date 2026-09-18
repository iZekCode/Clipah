# Signal Studio Redesign — Plan Index

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (the repository owner has required inline execution without subagents) to implement each plan below task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Clipah's generic light UI with the dark, media-first "Signal" studio described in `redesign-plan-v2.md`, including the small read-only backend that makes media visible.

**Architecture:** Six plans, executed in order. Each one ends with its gates green and leaves the product working: the foundation re-skins every existing screen through tokens and shared primitives; the backend plan adds derived storyboard, waveform, and transcript reads; the four surface plans rebuild screens on top of both.

**Tech Stack:** Next.js 15, React 19, TypeScript strict, Tailwind CSS 3, Radix UI, cmdk, sonner, TanStack Query, Vitest + Testing Library + vitest-axe, Playwright; Python 3.13, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL, Celery, FFmpeg 7.1.5, pytest.

**Spec:** `redesign-plan-v2.md` (repository root). Read it before any plan; every plan argues from it.

## Plans, in order

| # | Plan | Delivers | Gates |
| --- | --- | --- | --- |
| 1 | `2026-09-17-signal-01-foundation.md` | Baseline screenshots, tokens, fonts, primitives, lint bans, shared components, toasts, shell, command palette, render queue, global drop | Frontend |
| 2 | `2026-09-17-signal-02-preview-media-backend.md` | Migration `0023`, `PREVIEW_MEDIA` job, storyboard and waveform derivatives, storyboard/waveform/transcript reads, `order=recent`, backfill, retention proof, read limit, contracts | Backend + frontend |
| 3 | `2026-09-17-signal-03-media-surfaces.md` | Storyboard/waveform client, `Poster`, `Filmstrip`, `Waveform`, `StageBar`, Home, Projects, Clips, clip page | Frontend |
| 4 | `2026-09-17-signal-04-project-and-review.md` | Project page restructure, moment cards, "Why this moment" sheet, review mode | Frontend |
| 5 | `2026-09-17-signal-05-editor.md` | Timecode controls, studio layout, transport, timeline, inspector, caption editor, style panel, font parity | Frontend + backend image |
| 6 | `2026-09-17-signal-06-surfaces-and-verification.md` | Export, Publishing, Library, Settings, public pages, copy pass, banned-pattern and axe checks, responsive and browser verification, records | Frontend |

Plan 2 may run before Plan 1 finishes only if the owner asks; Plans 3–6 depend on both.

## Global constraints (apply to every plan)

- Follow `AGENTS.md`, `CONTEXT.md`, and `redesign-plan-v2.md`. New work lives in `frontend/` and `backend/` only (plus `infra/docker/` for the font task and `docs/design/signal/` for screenshots).
- The agent never runs `git commit`, `git push`, `git rebase`, or anything that writes history. Each plan ends by handing over a summary, gate output, and the owner commit message it names.
- Every behaviour change starts with a failing test that is run and observed failing before production code changes.
- Frontend gates, from the repository root: `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`.
- Backend gates, from `backend/`: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`, `uv run pytest -q --cov=clipah --cov-fail-under=90`.
- **Backend tests run only against a disposable PostgreSQL cluster and Redis database 15, never the application's local database** (`clean_database` truncates whatever `CLIPAH_TEST_DATABASE_URL` points at; see "Candidate contract experiment — 2026-09-12" in `PROGRESS.md`). Set `CLIPAH_TEST_DATABASE_URL`, `CLIPAH_TEST_API_RUNTIME_DATABASE_URL`, and `CLIPAH_TEST_WORKER_RUNTIME_DATABASE_URL` to the disposable cluster before running any integration test.
- Nothing is rendered as markup; `react/no-danger` stays an error. Provider and user text is React children.
- Types come from the backend: after any API change run `scripts/export-openapi.sh`, then `pnpm generate:api`, then `scripts/check-contracts-clean.sh`.
- Browser storage (`localStorage`) is read and written only inside `try`/`catch`, and the UI renders correctly without it.
- Existing routes, deep links (`?t=`, `?tab=`, legacy publishing query form), CSRF, tenant, and 404-indistinguishability rules are preserved.
- Colour, type, radius, and motion values are exactly the ones in `redesign-plan-v2.md` → Visual System. The banned patterns listed there may not be introduced by any task.
