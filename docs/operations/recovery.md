# Recovery drills

What happens to work in flight when something underneath it goes away, how to find out for
yourself, and what was observed the last time each drill was run.

Every drill here is a thing that will happen in production: a deploy restarts the API, a
broker is upgraded, a provider stops answering, an extractor breaks because a video site
changed. The point of running them deliberately is that the first time you see the
behaviour should not be during an incident.

A drill either **runs against the local stack** — you stop something and watch — or it is
**discharged by a suite**, because staging it for real would mean breaking a provider that
is not ours to break. Each drill below says which it is, and a drill discharged by a suite
names the file that holds the evidence.

## Before you start

```bash
docker compose -f infra/compose.yaml up --detach --wait
```

The stack this brings up is the one in `AGENTS.md`: Postgres on 55433, Redis on 56380,
MinIO on 59001, the API on 58000, the frontend on 53000, and one worker per queue. Every
command below is run from the repository root.

Two things are true of every drill and are not repeated in each one. No drill may lose a
durable record: a Job, a Publication, and an Edit Revision all survive anything stopping,
because they are rows rather than memory. And no drill may turn into a data leak: a failure
answers with the sanitized envelope, never with an exception, a query, or a path.

## 1. The API restarts while members are working

**Run it.**

```bash
docker compose -f infra/compose.yaml restart api
docker compose -f infra/compose.yaml exec -T postgres \
  psql -U clipah_migrator -d clipah_rebuild_foundation -c "select status, count(*) from jobs group by status"
```

**Expect.** The container becomes healthy again without help. Every Job keeps the status it
had: nothing was being held in the API process, because admission wrote the Job before
dispatching it. A browser with an open job-center stream reconnects on its own and replays
from `Last-Event-ID`, so it loses no event — the resumption itself is covered by
`backend/tests/contract/test_workspace_job_events.py`.

**Observed.** With one queued Job in the table, the API answered readiness again one second
after the restart and the Job table was identical either side of it: `queued:1` before,
`queued:1` after.

## 2. The YouTube extractor breaks

**Discharged by a suite.** Breaking a real extractor means waiting for YouTube to change,
so the failure is staged instead: `backend/tests/unit/test_yt_dlp_adapter.py` drives the
adapter against a command that fails the way a broken extractor fails.

**Expect.** The Job ends `failed` with a stable source code, not with yt-dlp's own text. No
command line, no path, and no cookie value reaches the Job event, the log, or the member.
The pinned image is what makes this recoverable: `infra/docker/source-import.Dockerfile`
pins yt-dlp, yt-dlp-ejs, and Deno by exact version, so recovering is a version bump and a
rebuild rather than an emergency edit.

**Operator action.** Bump the pinned yt-dlp version, rebuild the source-import image, and
redeploy that one service. Nothing else in the stack moves.

## 3. A source connection or a Social Account expires

**Discharged by suites.** `test_a_lease_and_its_refusals_never_print_what_was_borrowed` in
`backend/tests/security/test_source_secret_redaction.py` covers a revoked source connection
and one asked for long after it expired;
`test_provider_rejection_erases_the_grant_and_requires_an_explicit_reconnect` in
`backend/tests/integration/test_social_accounts.py` covers the provider refusing a refresh,
which is what an expired Grant looks like from here.

**Expect.** The lease is refused before any external call is made, the refusal carries no
credential material, and the connection is marked unusable rather than retried in a loop.
The member is asked to reconnect; nothing else in their Workspace is affected.

## 4. A worker is killed mid-encode, mid-generation, or mid-transfer

**Run it.**

```bash
docker compose -f infra/compose.yaml kill --signal SIGKILL worker-render
docker compose -f infra/compose.yaml up --detach worker-render
```

**Expect.** Celery acknowledges late and prefetches one, so the message the dead worker held
is redelivered rather than dropped. The Job is picked up again and its stage runner starts
from the beginning of that stage; nothing half-written survives, because a per-Job workspace
is a fresh directory each time and every artifact is uploaded only after it is complete.
An Asset that already exists is reused rather than produced twice — the deterministic IDs in
`backend/src/clipah/assets/keys.py` are what make a retry idempotent.

**Observed.** `SIGKILL` to the render worker left the Job table untouched — `queued:1`
before and after — and the container rejoined its queues once started. No Job was left
`running` with nobody working on it.

## 5. Redis restarts

**Run it.**

```bash
docker compose -f infra/compose.yaml restart redis
curl --silent --fail http://127.0.0.1:58000/health/ready
```

**Expect.** Readiness recovers. Rate-limit windows are gone, which is the documented
trade-off: the limiter is a fixed window in Redis, so a restart forgives whatever was spent
in the current minute. Nothing durable is lost, because Redis holds no record of its own —
Job wake-ups travel over pub/sub as an optimization, and a missed wake-up only means the
next poll finds the work.

**Observed.** Readiness answered again immediately after the restart, before Redis had
finished its own health check, because readiness does not fail closed on a broker that the
API only uses to accelerate work it can do without.

## 6. A provider stops answering

**Discharged by suites.** Transcription, the language model, both stock providers, the
generative providers, and every social adapter are each driven against a transport that
times out — see `backend/tests/integration/test_transcription.py`,
`backend/tests/integration/test_highlight_analysis.py`,
`backend/tests/contract/test_stock_providers.py`,
`backend/tests/contract/test_generation_providers.py`, and the three publisher contract
suites.

**Expect.** A timeout is retryable and spends the Job's retry budget with exponential
backoff; an exhausted budget ends the Job `failed` rather than holding a concurrency slot.
The provider's own message never reaches the member. A reserved quota is released when the
Job ends without producing anything.

## 7. A webhook is lost, or delivered twice

**Discharged by suites.** `backend/tests/integration/test_publication_recovery.py` records
the same delivery twice; `backend/tests/security/test_webhook_intake.py` proves no webhook
route accepts an unsigned delivery at all.

**Expect.** A duplicate is recorded as evidence and enqueues nothing a second time. A lost
delivery costs nothing either: reconciliation polls the provider on its own schedule, so a
webhook is an accelerator rather than the only path to the truth. This is why the scheduler
can be stopped and started without a Publication getting stuck.

## 8. The scheduler is paused

**Run it.**

```bash
docker compose -f infra/compose.yaml stop scheduler
# ... leave it stopped while scheduled work becomes due ...
docker compose -f infra/compose.yaml up --detach scheduler
```

**Expect.** Scheduled Publications stay `scheduled` and become overdue rather than lost.
When the scheduler comes back it claims them under a database lock, so two schedulers can
never claim the same Publication — the claim is proven in
`backend/tests/integration/test_publications.py`.

**Observed.** The scheduler stopped and rejoined healthy in five seconds. There were no
Publications due in the local database at the time, so what this run exercised is the pause
and the resume; that a claim is taken once and only once is proven by
`test_scheduler_claims_due_work_once_in_bounded_batches_and_writes_outbox` in
`backend/tests/integration/test_publications.py`.

## 9. Two refreshes of the same OAuth Grant race

**Discharged by a suite.** `backend/tests/integration/test_social_accounts.py` runs two
refreshes concurrently.

**Expect.** One refresh wins under a row lock and the other waits for it rather than spending
the same refresh token at the same moment. A provider that rotates refresh tokens is the
reason this matters: two concurrent spends race to store the rotated material, and the loser
would be stored holding a token the provider has already invalidated.

`test_two_refreshes_of_one_grant_never_reach_the_provider_at_once` runs two refreshes from
two threads and asserts that the provider boundary was entered twice without the two calls
ever overlapping, and that the Grant's token version advanced once per refresh rather than
losing one.

## 10. A publish completes ambiguously

**Discharged by a suite.** `backend/tests/integration/test_publication_recovery.py` covers a
provider that accepts the upload and then answers nothing useful.

**Expect.** The Publication stays in a reconcilable state rather than being guessed either
way, and reconciliation resolves it from the provider's own record. The operation key is
what makes the retry safe: the same key reaches the same provider publication instead of
posting a second one.

## 11. Object storage is unreachable

**Run it.**

```bash
docker compose -f infra/compose.yaml stop minio
# Any call that needs storage; this one needs a Session, which `clipah.dev.seed` prints.
curl -s -w '\nstatus=%{http_code}\n' -X POST \
  "http://127.0.0.1:58000/api/v1/projects/$PROJECT_ID/uploads?workspace_id=$WORKSPACE_ID" \
  -H "Origin: http://localhost:53000" -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -b "clipah_session=$SESSION; clipah_csrf=$CSRF" \
  -d '{"filename":"drill.mp4","contentType":"video/mp4","contentLength":1048576}'
docker compose -f infra/compose.yaml up --detach --wait minio
```

**Expect.** Storage failures are retryable and sanitized: a member sees a service-unavailable
envelope, never a provider error. No Asset row is written for bytes that were never stored,
because the row is written after the upload is verified. Work already queued waits rather
than failing permanently.

**Observed — and this drill failed the first time it was run.** With MinIO stopped, creating
an upload answered `500 INTERNAL_ERROR`. The envelope was sanitized, so nothing leaked, but
the code was wrong in a way that matters: it tells a client the request can never succeed,
when the truth is that it succeeds as soon as the store is reachable. Two gaps produced it —
the S3 adapter did not wrap transport failures for `create_multipart_upload`,
`sign_upload_part`, `complete_multipart_upload`, `abort_multipart_upload`, or
`delete_object`, and the application had no handler for a storage outage.

Both are fixed, and the drill was re-run: creating an upload with MinIO stopped now answers
`503 SERVICE_UNAVAILABLE`, `multipart_uploads` holds no row for the attempt, and the same
request answers `201` once MinIO is back. The tests that hold this are
`test_every_s3_operation_reports_an_unreachable_store_as_an_outage` in
`backend/tests/unit/test_object_storage.py` and
`test_an_unreachable_object_store_is_a_service_outage_rather_than_a_defect` in
`backend/tests/integration/test_multipart_uploads.py`.

One thing to know while reading a dashboard during a storage outage: `/health/ready`
answered `200` throughout. Readiness probes the database and the broker, not the object
store, so an outage here shows up as refused work rather than as an unready API.

## 12. A migration has to be rolled back

**Run it.**

```bash
cd backend
uv run alembic downgrade -1
uv run alembic upgrade head
uv run alembic check
```

**Expect.** Every migration downgrades and upgrades again cleanly, and the schema after the
round trip has no drift from the ORM metadata. This is also a CI job, so a migration that
cannot be reversed fails before it is merged rather than during an incident.

**Observed.** The most recent migration downgraded and upgraded again in well under a second
and `alembic check` reported `No new upgrade operations detected`.

## 13. A task is delivered twice

**Run it.**

```bash
scripts/verify-runtime.sh
```

**Expect.** The script replays one fixed identity through the whole stack twice. The second
pass must produce the same records as the first: idempotency keys bind one request to one
Job, deterministic Asset identifiers bind one stage to one artifact, and a job event
sequence is computed under the lock that appends it.

**Observed.** Both passes ran the same 10 smoke checks and converged on the same records.

## When a drill fails

A failed drill is a defect, not a note. Write down which drill, what you did, and what you
saw instead; then fix the behaviour rather than the drill. The drills exist to be believed.
