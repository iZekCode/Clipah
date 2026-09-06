# Quota-Aware Generated Media Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in, stock-first generated B-roll fallback with visible estimates, durable async execution, strict moderation, and exact Workspace quota reconciliation.

**Architecture:** Extend the existing B-roll Suggestion aggregate and durable Job machinery without a migration. Provider-neutral values and safety policy live in `broll/generation.py`; fal and Runway stay inside adapters; admission writes one existing suggestion plus one Job and its quota rows atomically; a worker resumes provider state from sanitized suggestion metadata and persists generated media with provenance before returning the suggestion to `proposed`.

**Tech Stack:** Python 3.13, FastAPI, Pydantic 2, SQLAlchemy 2, Celery, Postgres/RLS, HTTPX, cryptography ED25519, Pillow, React 19, Next.js 15, TanStack Query, Vitest, Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-06-quota-aware-generated-media-design.md`

## Global Constraints

- Work only in the rebuild: `backend/` and `frontend/`; do not extend the legacy stack.
- Write and observe every behavior test failing before production code.
- The agent never runs `git commit`, `git push`, or another history-writing command; each task ends with an owner handoff checkpoint instead.
- Provider credentials, raw payloads, ephemeral output URLs, signed URLs, and provider error text never enter Postgres, Redis, logs, Job events, or API responses.
- Generated output remains a proposed B-roll Suggestion until an authorized User accepts it.
- Generated video defaults disabled and every video confirmation requires a second explicit consent.
- Reject every configured model alias or provider model ID containing `sora`, case-insensitively.
- Ordinary tests make no network call and no billable generation request.

---

### Task 1: Provider-Neutral Generation Contract and Configuration

**Files:**
- Create: `backend/src/clipah/broll/generation.py`
- Modify: `backend/src/clipah/config.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Create: `backend/tests/contract/test_generation_providers.py`
- Modify: `backend/tests/unit/test_config.py`

**Interfaces:**
- Consumes: `Settings`, injected UTC and monotonic clocks.
- Produces: `GenerationMediaKind`, `GenerationRequest`, `GenerationEstimate`, `GenerationHandle`, `GenerationStatus`, `GenerationUsage`, `GenerationResult`, `GenerativeMediaProvider`, `PromptModerator`, `CircuitBreaker`, and generation exception classes carrying fixed codes.

- [ ] **Step 1: Write strict value and fake-provider contract tests**

```python
def test_provider_contract_normalizes_estimate_and_actual_usage() -> None:
    request = image_request()
    provider = FakeGenerativeMediaProvider(
        estimate=GenerationEstimate.image(
            output_count=1, width=1080, height=1920,
            latency_class=GenerationLatencyClass.STANDARD,
            provider_credits=Decimal("8"), cost_usd=Decimal("0.08"),
        ),
        result=successful_image_result(cost_usd=Decimal("0.08")),
    )
    assert provider.estimate(request=request).image_units == Decimal(1)
    assert provider.poll(handle=provider.submit(request=request, idempotency_key="job:1")).usage.cost_usd == Decimal("0.08")
```

Add separate tests for closed schemas, invalid dimensions/durations, negative/non-finite cost and usage, unknown models, prompt moderation, cancellation, retry idempotency, and circuit open/half-open/closed behavior.

- [ ] **Step 2: Run the contract/config tests and observe missing imports**

Run: `cd backend && uv run pytest -q tests/contract/test_generation_providers.py tests/unit/test_config.py`

Expected: collection fails because `clipah.broll.generation` and new settings do not exist.

- [ ] **Step 3: Implement strict values, fake adapter, moderation, and circuit breaker**

```python
class GenerativeMediaProvider(Protocol):
    def estimate(self, *, request: GenerationRequest) -> GenerationEstimate: ...
    def submit(self, *, request: GenerationRequest, idempotency_key: str) -> GenerationHandle: ...
    def poll(self, *, handle: GenerationHandle) -> GenerationResult: ...
    def cancel(self, *, handle: GenerationHandle) -> None: ...

class GenerationProviderError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
```

Use fixed error codes for unavailable, rate-limited, timeout, rejected, moderation rejected, invalid response, unknown model, and open circuit. Make prompt construction deterministic from `VisualIntent` and reject the three prohibited safety classes before submission.

- [ ] **Step 4: Add validated settings and locked Pillow dependency**

Add provider credentials/aliases, webhook base URL, maximum duration/output bytes, HTTP timeout, poll timing, retry count, circuit threshold/cooldown, estimate-token TTL, and `generative_video_enabled=false`. Validate positive bounds, `poll_seconds >= 5`, maximum generation duration `<= broll_max_shot_ms`, complete credential/model pairs, and the Sora exclusion.

- [ ] **Step 5: Run focused tests until green**

Run: `cd backend && uv run pytest -q tests/contract/test_generation_providers.py tests/unit/test_config.py`

- [ ] **Step 6: Owner handoff checkpoint**

Record Task 1 files and focused test output; do not commit.

### Task 2: fal Async Adapter and Signed Webhook Verification

**Files:**
- Create: `backend/src/clipah/broll/fal_adapter.py`
- Create: `backend/src/clipah/api/routes/generation_webhooks.py`
- Modify: `backend/src/clipah/api/app.py`
- Modify: `backend/tests/contract/test_generation_providers.py`
- Create: `backend/tests/contract/test_generation_webhooks.py`

**Interfaces:**
- Consumes: `GenerativeMediaProvider`, `httpx.Client`, `cryptography.hazmat.primitives.asymmetric.ed25519.Ed25519PublicKey`, `JobDispatcher`.
- Produces: `FalGenerativeMediaProvider`, `FalWebhookVerifier.verify(headers, body, now)`, and `POST /api/v1/webhooks/generation/fal`.

- [ ] **Step 1: Write failing fal adapter tests**

```python
def test_fal_submit_returns_only_an_opaque_request_identity() -> None:
    client = fal_http_client(submit_response={"request_id": "fal-request", "response_url": "https://secret"})
    handle = FalGenerativeMediaProvider(config=fal_config(), client=client).submit(
        request=image_request(), idempotency_key="job-id"
    )
    assert handle.provider_request_id == "fal-request"
    assert "secret" not in repr(handle)
```

Cover pricing lookup/estimate, queue submit, queued/running/success/error status mapping, cancel, timeouts, throttling, model rejection, moderation evidence, schema rejection, and absence of provider URLs/errors from returned values.

- [ ] **Step 2: Write failing webhook signature tests**

Generate a test ED25519 keypair. Assert raw-body SHA-256, exact newline message order, all four headers, ±300-second replay window, bad hex/signature/key/body rejection, cached JWKS refresh, duplicate request ID acceptance without duplicate dispatch, and immediate `204` response.

- [ ] **Step 3: Run tests and observe missing adapters/routes**

Run: `cd backend && uv run pytest -q tests/contract/test_generation_providers.py tests/contract/test_generation_webhooks.py`

- [ ] **Step 4: Implement fal with raw HTTP and strict codecs**

Use the allowlisted endpoint ID to construct queue URLs. Send `Authorization: Key ...`, `X-Fal-No-Retry: 1`, configured start timeout, and the webhook URL. Parse only fields required by the domain contract. Retrieve current unit price from the fal pricing API for estimates, cache it for a bounded interval, and fail closed when price cannot be established.

- [ ] **Step 5: Implement verification-only webhook ingestion**

```python
@router.post("/api/v1/webhooks/generation/fal", status_code=204)
async def receive_fal_webhook(request: Request) -> Response:
    body = await request.body()
    event = verifier_for(request).verify(headers=request.headers, body=body, now=clock_for(request)())
    record_generation_webhook(event)
    dispatch_generation_if_resumable(event.provider_request_id)
    return Response(status_code=204)
```

Persistence must store only request ID, normalized status, stable payload digest, and received timestamp in the matching suggestion's `generation` metadata. The route performs no media download and no Job transition.

- [ ] **Step 6: Run focused tests until green**

Run: `cd backend && uv run pytest -q tests/contract/test_generation_providers.py tests/contract/test_generation_webhooks.py`

- [ ] **Step 7: Owner handoff checkpoint**

Record fal/webhook test output; do not commit.

### Task 3: Optional Runway Video Adapter

**Files:**
- Create: `backend/src/clipah/broll/runway_adapter.py`
- Modify: `backend/tests/contract/test_generation_providers.py`

**Interfaces:**
- Consumes: provider-neutral generation contract, configured model alias, HTTPX.
- Produces: `RunwayGenerativeMediaProvider` supporting video estimate, submit, poll, and cancel.

- [ ] **Step 1: Write failing Runway contract cases**

Assert video-only behavior, API-version header, task ID retention, `PENDING`/`THROTTLED`/`RUNNING`/`SUCCEEDED` mapping, `FAILED` and `CANCELED`, `SAFETY.INPUT.*` and `SAFETY.OUTPUT.*` moderation normalization, timeout/throttling classification, ephemeral output URL containment, cost normalization from credits at `$0.01`, and cancel idempotency.

- [ ] **Step 2: Run the Runway cases and observe missing adapter failure**

Run: `cd backend && uv run pytest -q tests/contract/test_generation_providers.py -k runway`

- [ ] **Step 3: Implement the minimal video-only adapter**

Use `POST /v1/image_to_video` without `promptImage` for text-to-video, `GET /v1/tasks/{id}` for polling, and `DELETE /v1/tasks/{id}` for cancellation. Set `X-Runway-Version: 2024-11-06`; never call `wait_for_task_output` or block an API request.

- [ ] **Step 4: Run all provider contracts until green**

Run: `cd backend && uv run pytest -q tests/contract/test_generation_providers.py`

- [ ] **Step 5: Owner handoff checkpoint**

Record provider contract output; do not commit.

### Task 4: Generation Eligibility, Estimate, Admission, and Quotas

**Files:**
- Modify: `backend/src/clipah/broll/repository.py`
- Modify: `backend/src/clipah/broll/use_cases.py`
- Modify: `backend/src/clipah/jobs/admission.py`
- Modify: `backend/src/clipah/jobs/use_cases.py`
- Modify: `backend/src/clipah/api/routes/broll.py`
- Modify: `backend/src/clipah/api/errors.py`
- Modify: `backend/src/clipah/api/app.py`
- Create: `backend/tests/integration/test_broll_generation.py`
- Modify: `backend/tests/contract/test_broll_api.py`

**Interfaces:**
- Consumes: existing B-roll Suggestion, `JobAdmission`, `QuotaLedger`, `EDIT_WRITE`, configured provider factory.
- Produces: `estimate_generation`, `start_broll_generation`, idempotent multi-resource reservation/settlement/release, estimate and generate endpoints.

- [ ] **Step 1: Write failing eligibility and estimate API tests**

Assert missing/foreign suggestions return identical 404s; reviewer/viewer cannot estimate or generate; above-threshold stock suppresses generation; an empty or below-threshold proposal offers image first; missing provider and disabled video return `available=false`; estimate exposes output count, duration, resolution, latency, quota, credits, USD cost, and a signed expiring confirmation token without reserving quota.

- [ ] **Step 2: Write failing admission and quota tests**

```python
def test_video_confirmation_atomically_reserves_video_and_seconds(engine: Engine) -> None:
    response = confirm_video_generation(..., video_confirmed=True)
    assert response.status_code == 202
    assert reserved_resources(engine, response.json()["jobId"]) == {
        QuotaResource.GENERATED_VIDEOS: Decimal(1),
        QuotaResource.GENERATED_SECONDS: Decimal(5),
    }
```

Cover absent second confirmation, stale/tampered/cross-User tokens, concurrency, each quota independently, 50 concurrent final-slot callers admitting one, exact idempotent replay, conflicting key reuse, accepting User persistence, and atomic rollback when any reservation fails.

- [ ] **Step 3: Run focused integration tests and observe missing routes/use cases**

Run: `cd backend && uv run pytest -q tests/integration/test_broll_generation.py tests/contract/test_broll_api.py -k generation`

- [ ] **Step 4: Generalize quota reconciliation safely**

Add `QuotaLedger.settle_if_reserved` and `release_if_reserved`, each locking the reservation and returning an already-terminal row unchanged. Add helpers that load all generation resources for one Job, so image settles one row and video settles two without changing analysis behavior.

- [ ] **Step 5: Implement eligibility and signed estimate confirmation**

Derive request values from the locked suggestion. Seal a versioned JSON confirmation with the existing deployment secret, binding Workspace ID, User ID, suggestion ID, complete request, estimate, and expiration. Never accept browser-provided prompt, model, dimensions, cost, or quota units.

- [ ] **Step 6: Implement atomic generation admission**

Lock the suggestion and idempotency subject, validate the confirmation, create one Job/event, reserve all applicable quota rows, record sanitized generation metadata including `requested_by_user_id`, and move status to `generation_requested` before committing and dispatching.

- [ ] **Step 7: Run focused tests until green**

Run: `cd backend && uv run pytest -q tests/integration/test_broll_generation.py tests/contract/test_broll_api.py -k generation`

- [ ] **Step 8: Owner handoff checkpoint**

Record admission/API output; do not commit.

### Task 5: Durable Generation Worker and Generated Asset Provenance

**Files:**
- Create: `backend/src/clipah/jobs/broll_generate_task.py`
- Modify: `backend/src/clipah/jobs/tasks.py`
- Modify: `backend/src/clipah/assets/keys.py`
- Modify: `backend/src/clipah/jobs/use_cases.py`
- Modify: `backend/tests/integration/test_broll_generation.py`
- Modify: `backend/tests/unit/test_asset_keys.py`

**Interfaces:**
- Consumes: generation request stored on suggestion, provider factory, `SourceDownloader`, Pillow image validation, `MediaProcessor`, `ObjectStore`, `QuotaLedger`, Job cancellation.
- Produces: registered `BROLL_GENERATE` stage, generated B-roll Asset plus atomic provenance, terminal suggestion/quota cleanup.

- [ ] **Step 1: Write failing stage tests**

Cover request lookup, `generation_requested -> generating`, submit once, redelivery polling the stored handle, webhook-completed resume, bounded polling timeout, retryable/terminal mapping, provider rejection, input/output moderation rejection, cancellation before and after submit, best-effort provider cancel, and provider absence as unavailable.

- [ ] **Step 2: Write failing media/provenance tests**

Assert generated PNG/JPEG/WebP signatures and dimensions are validated with decompression limits; invalid or oversized media creates no row; generated video is probed and normalized to a private proxy; SHA-256 is calculated during bounded download; object-store size/digest is read back; Asset and provenance are atomic; provenance includes provider, request ID, prompt, model/version, seed, moderation, cost/usage snapshot, source type, and checksum; no URL or raw provider payload is stored.

- [ ] **Step 3: Write failing terminal quota tests**

Assert success settles actual image/video/seconds once, duplicate completion does not recharge, unbilled failure/cancellation releases reservations, billed provider failure settles reported usage, and suggestion becomes `failed` on permanent failure/cancellation.

- [ ] **Step 4: Run stage tests and observe missing runner failure**

Run: `cd backend && uv run pytest -q tests/integration/test_broll_generation.py -k 'stage or media or provenance or terminal'`

- [ ] **Step 5: Implement server-owned generated keys and media validation**

Use `workspaces/{workspace}/projects/{project}/generated/{asset}/{kind}`. Images accept only PNG, JPEG, and WebP, are decoded/verified with Pillow, and enforce configured pixel and byte bounds. Videos use the existing safe downloader and FFmpeg processor, reject duration above the request/configured maximum, generate the existing H.264 proxy, and discard generated audio from placement semantics.

- [ ] **Step 6: Implement resumable stage runner**

Read/write short transactions around every provider or object-store operation. Persist the opaque handle before polling. Treat webhook state as a wakeup, not authority; retrieve the authoritative provider result. Never submit when a handle already exists. Update Job progress with stable stage names and identifiers only.

- [ ] **Step 7: Implement atomic success and terminal cleanup**

Persist Asset and AssetProvenance together, then attach the suggestion and settle quotas in the same tenant transaction. Extend generic Job terminal reconciliation to release still-reserved generation rows and mark the suggestion failed on permanent failure/cancellation while preserving already-settled billed usage.

- [ ] **Step 8: Register the runner and run integration tests until green**

Run: `cd backend && uv run pytest -q tests/integration/test_broll_generation.py tests/unit/test_asset_keys.py`

- [ ] **Step 9: Owner handoff checkpoint**

Record worker/integration output; do not commit.

### Task 6: Generated API Client and Confirmation UX

**Files:**
- Modify: `contracts/openapi.json`
- Generate: `frontend/lib/api/generated/`
- Create: `frontend/features/broll/GenerationConfirmDialog.tsx`
- Modify: `frontend/features/broll/BrollPanel.tsx`
- Modify: `frontend/features/broll/BrollSuggestionCard.tsx`
- Create: `frontend/tests/broll-generation.test.tsx`
- Modify: `frontend/tests/broll-editor.test.tsx`

**Interfaces:**
- Consumes: generated estimate/generate clients, existing B-roll panel and Job events.
- Produces: image-first estimate dialog, explicit second video confirmation, safe background status and refetch.

- [ ] **Step 1: Write failing frontend tests**

```tsx
test('shows the complete still estimate before admitting work', async () => {
  renderPanelWithGeneration({ imageEstimate: estimateFixture() })
  await user.click(screen.getByRole('button', { name: /generate still/i }))
  expect(await screen.findByText('$0.08 estimated')).toBeVisible()
  expect(screen.getByText('1080 × 1920')).toBeVisible()
  expect(api.generationSubmissions).toHaveLength(0)
})
```

Add tests for stock suppression, unavailable provider, quota copy, image confirmation, video disabled, the distinct “Consider video” step, no video submission without second confirmation, background Job status/refetch, generated image placement as `mediaKind: image`, request-ID errors, and markup rendered as text.

- [ ] **Step 2: Run tests and observe missing component/client failures**

Run: `pnpm test -- broll-generation.test.tsx broll-editor.test.tsx`

- [ ] **Step 3: Export OpenAPI and regenerate the client**

Run: `scripts/export-openapi.sh && pnpm generate:api`

Do not hand-edit `frontend/lib/api/generated/`.

- [ ] **Step 4: Implement `GenerationConfirmDialog`**

Render provider-neutral estimate fields, one primary image confirmation, and a secondary video-estimate flow. Keep prompt/model selection server-owned. Disable all actions while estimating/submitting and return focus to the invoking control on close.

- [ ] **Step 5: Wire eligibility, submission, and media kind into B-roll UI**

Suppress generation for above-threshold media or non-proposed statuses. Use one idempotency key per suggestion/media kind/confirmation. After admission show background status, follow the existing Job event stream, refetch suggestions at terminal status, and infer placement media kind from the returned Asset `contentType` rather than assuming video.

- [ ] **Step 6: Run focused frontend tests until green**

Run: `pnpm test -- broll-generation.test.tsx broll-editor.test.tsx`

- [ ] **Step 7: Verify contract cleanliness**

Run: `scripts/check-contracts-clean.sh`

- [ ] **Step 8: Owner handoff checkpoint**

Record frontend/contract output; do not commit.

### Task 7: Operational Documentation, Sandbox Smoke, and Progress

**Files:**
- Create: `docs/operations/generative-media.md`
- Create: `backend/tests/slow/test_generation_provider_smoke.py`
- Modify: `PROGRESS.md`

**Interfaces:**
- Consumes: final settings and adapter behavior.
- Produces: deployment/runbook guidance, explicit opt-in smoke test, accurate Task 31 record and deferrals.

- [ ] **Step 1: Write operations documentation**

Document provider credentials, model allowlists, video feature gate, fal public webhook/JWKS requirements, Runway polling, cost/quota semantics, moderation behavior, circuit states, retry/cancellation, ephemeral output handling, key rotation, observability boundaries, incident disable procedure, and sandbox smoke invocation.

- [ ] **Step 2: Add an opt-in sandbox smoke test**

Require an explicit `CLIPAH_RUN_GENERATION_SMOKE=1` plus provider credentials. Estimate first, cap output to one image or five seconds, require a model allowlist entry, and skip otherwise. Never run it during ordinary verification.

- [ ] **Step 3: Run the smoke-test selector without opting in**

Run: `cd backend && uv run pytest -q tests/slow/test_generation_provider_smoke.py`

Expected: clean skip and no network call.

- [ ] **Step 4: Update `PROGRESS.md` honestly**

Mark Task 31 landed only after every gate passes. Record exact test counts, coverage, contract status, smoke-test skip, no migration, provider choices, and concrete deferrals. Record required owner commit message `feat: add guarded generative broll fallback`.

- [ ] **Step 5: Owner handoff checkpoint**

Record documentation/progress diff; do not commit.

### Task 8: Full Verification and Handoff

**Files:**
- Verify all Task 31 changes.

**Interfaces:**
- Consumes: completed backend/frontend implementation.
- Produces: evidence that Task 31 meets its acceptance criteria without expanding scope.

- [ ] **Step 1: Run backend gates**

Run from `backend/`:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q --cov=clipah --cov-fail-under=90
```

- [ ] **Step 2: Verify schema and migrations**

Run the repository's Alembic downgrade/upgrade and drift commands used by the existing integration suite. Confirm Task 31 introduced no migration and existing runtime grants cover every new read/write.

- [ ] **Step 3: Run frontend gates**

Run from the repository root:

```bash
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

- [ ] **Step 4: Run contract and whitespace checks**

```bash
scripts/check-contracts-clean.sh
git diff --check
```

- [ ] **Step 5: Inspect final diff against Task 31**

Confirm every Task 31 checkbox has evidence, no provider secret or URL is present in fixtures/durable metadata, no ordinary test can call a live provider, no Sora identifier is accepted, and unrelated deferred work is absent.

- [ ] **Step 6: Hand off without committing**

Provide the summary, exact gate outputs, material deferrals, and owner commit message:

```text
feat: add guarded generative broll fallback
```

