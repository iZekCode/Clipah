# AGENTS.md

Instructions for any coding agent (or human) working in this repository. Read this file
before touching code, then read the two documents it points at.

## What this repository is

Clipah is being rebuilt. Two codebases live here at once:

- **The rebuild** — `backend/` (FastAPI + SQLAlchemy + Alembic, Python 3.13) and, later,
  a single frontend package. This is the only code that should receive new features.
- **The legacy stack** — `app.py`, `templates/`, `static/`, root `requirements.txt`, the
  root Next.js files, and `nixpacks.toml`. It stays in place until the cutover in Task 48
  and should not be extended. Do not port new behaviour into it.

## The three documents that govern the work

| File | Role |
| --- | --- |
| `plan.md` | The approved master specification. Sections 1-12 define the architecture; Section 11 lists Tasks 1-48. |
| `CONTEXT.md` | The canonical vocabulary. Use these exact words in code, tests, docstrings, and commit messages. |
| `PROGRESS.md` | Which tasks have landed, in which commits, and what was deliberately deferred. |

`plan.md` is the source of truth for scope. Do not invent requirements it does not state,
and do not skip requirements it does state.

## How a task is executed

1. **Pick the next unstarted task from `PROGRESS.md`.** Tasks run in order. A task may be
   split into sub-parts (4a, 4b, 4c) when it is large, but no later task starts before an
   earlier one is complete.
2. **Read the whole task in `plan.md`** — its `Files`, `Interfaces`, and every checkbox.
   The checkboxes are the acceptance criteria.
3. **Write the tests first.** Every behaviour named by a checkbox gets a failing test
   before the implementation exists. Watch it fail (RED), then implement (GREEN).
4. **Implement the smallest change that satisfies the task.** Anything the task does not
   ask for belongs to a later task; note the deferral in `PROGRESS.md` instead of
   building it early.
5. **Run all four gates** (below) until they are green.
6. **Update `PROGRESS.md`** with the task status and any deferrals.
7. **Hand the work over.** See "Commits" — the agent never commits.

## Quality gates

### Backend

All four commands run from `backend/` and all four must pass before work is handed over:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q --cov=clipah --cov-fail-under=90
```

There is no "fix it in the next task". A gate that fails is work that is not finished.
Coverage may not be lowered; if a line is uncovered, either test it or delete it.

### Frontend

A task that touches `frontend/` runs these four from the repository root, and all four
must pass:

```bash
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

The repository root is a pnpm workspace whose one package is `frontend/`; each root
script delegates to it. `pnpm install` from the root installs everything.

## Local infrastructure

```bash
docker compose -f infra/compose.yaml up -d
```

This brings up the services the integration tests need, all bound to loopback only:

| Service | Address | Notes |
| --- | --- | --- |
| Postgres 17 | `127.0.0.1:55433` | database `clipah_rebuild_foundation` |
| Redis 7.4 | `127.0.0.1:56380` | |
| MinIO | `127.0.0.1:59001` (console `59002`) | |

`infra/postgres/init-runtime.sql` provisions the database roles on first start. The
migration owner is `clipah_migrator`; the application connects as the least-privilege
logins `clipah_api_runtime` and `clipah_worker_runtime`. Tests read their connection
strings from `CLIPAH_TEST_DATABASE_URL`, `CLIPAH_TEST_API_RUNTIME_DATABASE_URL`, and
`CLIPAH_TEST_WORKER_RUNTIME_DATABASE_URL`, each with a local default, so a standard
compose stack needs no environment setup at all.

Migrations:

```bash
cd backend && uv run alembic upgrade head
```

## Backend conventions

**Tooling.** `uv` manages the environment and the lockfile; never call `pip` or a bare
`python`. Ruff lints (`E,F,I,N,UP,B,SIM,RUF`) and formats at line length 100. `mypy` runs
in strict mode over `src/clipah`.

**Layout.** `src/clipah/` holds the package: `config.py` (fail-closed pydantic settings,
`CLIPAH_` prefix), `db.py` (engines, session scope, tenant context), `models.py` (ORM),
`api/` (app factory, dependencies, error envelope, routes), `auth/`, `workspaces/`.
Domain rules live in a `use_cases.py` module; HTTP concerns stay in `api/routes/`.

**Tests.** `tests/unit/` (no infrastructure), `tests/integration/` (real Postgres),
`tests/contract/` (behaviour promised to other tasks). Markers `unit`, `integration`, and
`slow` are declared in `pyproject.toml` under `--strict-markers`, so an undeclared marker
is an error rather than a silent skip. Shared database fixtures live in `tests/conftest.py`
and are opt-in — a test that never requests `engine` or `clean_database` never touches
Postgres. The in-process HTTP harness lives in `tests/harness.py`. Both are importable
because `pythonpath = ["src", "tests"]`. Every test has a name that states the behaviour
and a docstring that states why it matters. Per-test timeout is 15 seconds.

**Docstrings.** Every module, class, and function carries one. It explains intent, not
mechanics.

**Determinism.** No wall-clock reads, no randomness, and no network calls inside domain
code. Clocks and providers are injected so tests can pin them.

## Frontend conventions

**Tooling.** `pnpm` manages the workspace and the lockfile. TypeScript runs in strict mode
with `noUncheckedIndexedAccess`; ESLint runs `next/core-web-vitals` and `next/typescript`
over `app`, `components`, `hooks`, `lib`, and `tests`. Vitest with Testing Library runs in
jsdom.

**Types come from the backend.** Request and response types are never hand-written. Run
`scripts/export-openapi.sh` and then `pnpm generate:api`; `frontend/lib/api/generated/` is
generated output and is not edited by hand.

**One way to call the API.** Every request goes through `apiFetch` in
`frontend/lib/api/client.ts`, which is also the generated client's mutator: same-origin
`/api` paths, `credentials: 'same-origin'`, the double-submit CSRF token echoed on unsafe
methods, and failures raised as an `ApiError` carrying `code` and `requestId`.

**Nothing is rendered as markup.** Provider and user text is rendered as React children.
`dangerouslySetInnerHTML` is a lint error.

## Non-negotiable safety rules

These are properties of the whole system, not of any one task. Breaking one is a defect
even when the task's checkboxes pass.

- **Every tenant-scoped row carries `workspace_id`,** and child rows reference their parent
  through a composite `(workspace_id, id)` foreign key so a row can never be re-parented
  into another Workspace.
- **Row-level security is defence in depth, not authorization.** The RLS predicate only
  checks that `clipah.workspace_id` matches the row and that `clipah.user_id` is set; it
  does not verify membership. The application decides which Workspace a caller may declare,
  and it does so by reading a live Membership row before calling `set_workspace_context`.
- **A guessed identifier is indistinguishable from a missing one.** Any Workspace, Project,
  Job, Edit, or Render UUID that the caller has no standing on returns exactly the same
  404 body — same code, same public message — as a UUID that does not exist.
- **Errors are sanitized.** Every failure leaves the API as
  `{"error": {"code", "message", "requestId"}}` with a public message chosen from a fixed
  table. Exception text, SQL, and stack traces never reach a client.
- **Secrets are never stored in a recoverable form** when a hash will do. Session tokens
  are stored SHA-256-only; provider tokens are encrypted.
- **State-changing HTTP methods require CSRF proof** — a same-origin `Origin` (falling back
  to `Referer`) plus a double-submit token echoed in `X-CSRF-Token`.
- **Irreversible actions require a recent authentication.** Workspace deletion, ownership
  transfer, membership management, and social-connection management all check the
  ten-minute recent-auth window.

## Commits

**The agent never commits.** This is a standing instruction from the repository owner and
it has no exceptions, including the `Commit with ...` checkbox that ends every task in
`plan.md`.

When a task is done, hand over:

1. a summary of what changed and why,
2. the output of the four gates,
3. the exact commit message from the task's final checkbox.

The repository owner runs `git commit` themselves. Do not run `git commit`, `git push`,
`git rebase`, or anything else that writes history.

## Style of the work

Deliver the task that was asked for — not a narrower version, not a wider one. If part of
a task turns out to be blocked, finish everything else in full and say plainly what was
left out and why. Report honestly: if a gate fails, show the failure rather than
describing the work as complete.
