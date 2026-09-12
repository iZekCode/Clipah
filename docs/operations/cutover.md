# Cutover runbook

How Clipah moves from the legacy Flask application to the rebuilt backend and frontend, and
how it moves back if it has to. Every step here is reversible until the last one, and the
last one is deliberately separated from the rest by a rollback window.

This runbook is the operator's document. `plan.md` Section 11 Task 48 is the specification
it implements, and `PROGRESS.md` records what has actually been done.

## What is being replaced

| Legacy | Replacement |
| --- | --- |
| One Flask process (`app.py`) holding progress in module-level globals | The FastAPI API, Celery workers, and the Celery scheduler, each a separate service |
| Server-rendered Jinja templates and `static/script.js` | The Next.js application in `frontend/` |
| A single shared working directory on the web host's disk | Per-Job workspaces plus S3-compatible object storage |
| A background `threading.Thread` per submission | Durable Jobs with events, retries, cancellation, and admission control |
| No accounts | Google OIDC Login Identities, Sessions, and Workspaces with row-level security |

The legacy application has no database. It stores nothing that has to be migrated, so there
is no data migration step: the rebuilt stack starts empty and fills up as members use it.
That is the single fact that makes this cutover cheap, and it is worth stating plainly
before anyone plans a backfill that has nothing to back up.

## Environment inventory

The legacy stack read six unprefixed variables. Every rebuilt setting takes the `CLIPAH_`
prefix, and the complete reference lives in `ENVIRONMENT_SETUP.md`.

| Legacy variable | Replacement | Note |
| --- | --- | --- |
| `ASSEMBLYAI_API_KEY` | `CLIPAH_ASSEMBLYAI_API_KEY` | Same provider, same account. The rebuilt adapter selects the transcription model by language rather than hardcoding one. |
| `GROQ_API_KEY` | `CLIPAH_GROQ_API_KEY` | Same provider. The legacy stack reached Groq through the OpenAI SDK's compatibility base URL; the rebuilt adapter speaks to Groq directly with strict JSON Schema requests. |
| `FLASK_DEBUG` | `CLIPAH_DEBUG` | Defaulted to `True` in the legacy launcher. The rebuilt configuration refuses to start a production process with debug enabled. |
| `FLASK_ENV` | `CLIPAH_ENVIRONMENT` | Values are `local`, `staging`, and `production`, and the profile is selected by a real environment variable rather than by a file on disk. |
| `FLASK_HOST` | none | The host is a property of the deployment's start command, not of the application. |
| `FLASK_PORT` | none | The API binds `$PORT`, supplied by the platform. |

Three legacy dependencies have no replacement because nothing in the rebuilt stack uses
them, and they are removed rather than carried forward:

- **`google-generativeai`** was never called. The legacy analysis path ran on Groq. The
  package, and the README and setup text that credited Google Gemini with clip selection,
  described software that did not exist.
- **`Flask-CORS`** granted cross-origin access the rebuilt stack does not need. The browser
  only ever calls its own origin, because `frontend/next.config.mjs` rewrites `/api/*` to
  the API process. That is what keeps the Session cookie first-party and lets the CSRF
  `Origin` check see a same-origin request.
- **`moviepy`** is replaced by FFmpeg invoked as an argument array, with pinned versions in
  the media image. It is removed once render parity is proven, which is the golden-frame
  gate in `.github/workflows/ci.yml`.

The retired Groq model ID `meta-llama/llama-4-scout-17b-16e-instruct` disappears with
`app.py`. It remains in `backend/src/clipah/config.py` as a denied value: configuring it, or
its bare form, fails startup. That denial is not legacy residue and stays.

## Rollout control

`NEW_CLIPAH_ENABLED` is the one flag the rollout turns. It gates the Next.js application
through `frontend/middleware.ts`.

- It fails closed. Only the exact word `true` exposes the UI; unset, empty, or misspelled
  leaves it hidden.
- **It is read per request, not baked into the build.** Changing it and restarting the
  frontend process is enough; no rebuild is needed, which is what makes a rollback fast.
- While it is off, every product route answers a bare `404` that names no flag and no
  deployment, so a stage that has not been reached is indistinguishable from a host that
  was never serving Clipah.
- `/api/*` keeps answering the whole time. The foundations are deployed and proven before
  anyone can see the UI in front of them.

Social publishing is **not** controlled by this flag. It rolls out only through the
per-provider gates Task 43 built: `CLIPAH_SOCIAL_PUBLISHING_ENABLED` plus
`CLIPAH_YOUTUBE_PUBLISHING_ENABLED`, `CLIPAH_INSTAGRAM_PUBLISHING_ENABLED`, and
`CLIPAH_TIKTOK_PUBLISHING_ENABLED`, each requiring its complete OAuth configuration before
startup will accept it. TikTok stays on the official draft fallback until
`CLIPAH_TIKTOK_AUDIT_APPROVED` reflects a real Direct Post audit approval. No flag in this
runbook may be used to bypass a provider's consent, privacy, disclosure, or review
requirements; `docs/operations/social-publishing.md` holds those obligations.

## Stage 0 — Deploy the foundations with the UI hidden

1. Provision Postgres, Redis, and object storage, and set every variable the service
   requires. Each `infra/railway/*.toml` file declares its own `requiredVariables`, and the
   configuration refuses to start when one is missing rather than falling back to a weak
   default.
2. Run migrations as `clipah_migrator`: `cd backend && uv run alembic upgrade head`.
3. Deploy the API, the scheduler, and the six worker services.
4. Deploy the frontend **without** `NEW_CLIPAH_ENABLED`.
5. Confirm `GET /health/ready` answers `200` and that a product route answers `404`.

## Stage 1 — Compare the two stacks on the same inputs

Run both smoke scripts against the same sanitized fixture inputs and compare what they
produce.

```bash
scripts/legacy-smoke.sh --fixture backend/tests/fixtures/media/landscape.mp4
scripts/new-stack-smoke.sh --fixture backend/tests/fixtures/media/landscape.mp4
```

Each script writes a JSON observation. Compare candidate timing, titles, subtitles, and
final-media validity across the two.

Differences that are intentional and are not defects:

| Difference | Why |
| --- | --- |
| Candidate boundaries differ | The rebuilt pipeline resolves every boundary to authoritative transcript word IDs. The legacy stack let the model return timestamps directly, which is exactly the class of error the rebuild removes. |
| Candidate count and order differ | The rebuilt pipeline deduplicates by temporal overlap and excerpt similarity, then reranks globally across windows. The legacy stack ranked within one prompt. |
| The rebuilt run produces no final clips at this stage | A Project becomes reviewable before any render exists. Rendering is a separate, explicit step. |
| Subtitle text differs in punctuation and casing | Captions come from the same transcription that produced the candidates, rather than from a second pass. |
| The legacy run emits a ZIP; the rebuilt run emits per-clip renditions | Packaging is per destination now. |

Record the comparison and its intentional differences alongside the release. If
`legacy-smoke.sh` cannot run — no credentials, or the legacy deployment is already gone —
it reports the comparison as **unmeasured** and exits non-zero. That is a real result and
must be written down as such rather than reported as a pass.

## Stage 2 — Expose the new UI in stages

Turn `NEW_CLIPAH_ENABLED=true` for progressively more traffic:

| Stage | Audience |
| --- | --- |
| 1 | One internal Workspace |
| 2 | 10% of eligible traffic |
| 3 | 50% of eligible traffic |
| 4 | 100% |

**Each stage holds for 24 hours before the next one begins.** Advance only when all four of
these were true for the whole period:

- no cross-Workspace incident of any kind,
- no data loss,
- no queue-age or scheduler-age alert,
- render failure rate at or below 5%.

`docs/operations/observability.md` names the dashboards and alerts that answer these, and
`docs/operations/recovery.md` holds the drills. If any one of them is false, stop and roll
back rather than advancing.

## Rollback

Rollback changes routing only.

1. Set `NEW_CLIPAH_ENABLED` to anything other than `true` and restart the frontend process.
   The flag is read per request, so the product UI disappears as soon as the process comes
   back, without waiting for a build.
2. Route traffic to the legacy deployment.

Durable records and objects created by the rebuilt stack are untouched by a rollback. The
two stacks share no database and no bucket, so nothing the rebuilt stack wrote can be
corrupted by the legacy one being live, and rolling forward again loses nothing.

**The legacy deployment is preserved for seven days after 100% is reached.** During that
window it stays deployable, with its credentials valid.

## Stage 3 — Remove the legacy stack

Only after the seven-day window closes. Removed in one commit:

- `app.py`
- `templates/`
- `static/script.js` and `static/style.css`, keeping the fonts the renderer loads
- the root `requirements.txt`
- `nixpacks.toml`
- any root-level frontend file the Next.js package replaced

Then prove it is gone. `scripts/check-legacy-removed.sh` fails when any of these appear
outside documentation: `processing_status`, `threading.Thread`, `main_video.mp4`,
`cookies.txt`, `nocheckcertificate`, `innerHTML`, or the legacy `/process`, `/status`,
`/download`, and `/reset` routes. It runs in CI, so the legacy stack cannot return quietly.

Finally, run every command in `plan.md` Section 12 on a clean checkout.

## If something goes wrong

| Symptom | Where to look |
| --- | --- |
| Jobs queue but never start | `docs/operations/observability.md`, queue-age alerts |
| A render fails repeatedly | `docs/operations/recovery.md` |
| A publication is stuck or duplicated | `docs/operations/social-publishing.md` |
| Generation spend climbs unexpectedly | `docs/operations/generative-media.md` |
| A member asks for deletion or recovery | `docs/operations/data-retention.md` |
