# Quota-Aware Generated Media Design

## Purpose

Task 31 adds an opt-in fallback for a B-roll Suggestion whose stock retrieval produced no
useful media. A User sees an estimate before spending quota, confirms the request, and receives
one durable generated still or video proposal. Generation never edits a clip, never accepts its
own output, and never keeps an API request open while a provider works.

This design implements only Task 31. It does not add regeneration, generated alternatives,
retention sweeps, live provider accounting dashboards, or new composition behavior.

## Existing seams

The implementation extends the B-roll Suggestion aggregate rather than adding a table or
migration:

- `BrollSuggestionStatus` already defines `generation_requested`, `generating`, `proposed`, and
  `failed`.
- `BrollSuggestion.provider_metadata` can hold sanitized generation state and normalized usage.
- `Asset` already supports generated B-roll.
- `AssetProvenance` already has prompt, provider, model/version, seed, moderation, and checksum
  fields.
- `JobKind.BROLL_GENERATE` and its Celery queue already exist.
- `WorkspaceQuotaReservation` permits separate rows for generated images, generated videos, and
  generated seconds against the same Job.

The Job ID, accepting User ID, request, estimate snapshot, provider request ID, and terminal usage
are stored under a versioned `generation` object in `provider_metadata`. Provider response URLs,
credentials, response bodies, and error text are never persisted.

## Domain contract

`backend/src/clipah/broll/generation.py` owns provider-neutral immutable values:

- `GenerationMediaKind`: `image` or `video`.
- `GenerationRequest`: prompt, media kind, output count, duration, width, height, model alias, and
  optional deterministic seed.
- `GenerationEstimate`: output count, duration, width, height, latency class, normalized quota
  units, provider credits, and estimated cost in USD.
- `GenerationHandle`: provider, provider request ID, model, model version, and submitted instant.
- `GenerationStatus`: queued, running, succeeded, rejected, moderation rejected, canceled, or
  failed.
- `GenerationUsage`: generated image count, generated video count, generated seconds, provider
  credits, and cost in USD.
- `GenerationResult`: status, optional output capability, normalized usage, seed, moderation
  result, and output metadata.
- `GenerativeMediaProvider`: `estimate`, `submit`, `poll`, and `cancel` operations.

All Pydantic request/result schemas are strict and forbid extra fields. Money uses `Decimal`,
durations use whole milliseconds internally, and all timestamps are timezone-aware UTC. Adapters
translate provider payloads into these values; SDK or HTTP payload types never escape an adapter.

Unknown model aliases, arbitrary provider model IDs, OpenAI model IDs containing `sora`, non-finite
numbers, negative usage, and output dimensions or durations outside configured limits are rejected
before a provider call.

## Provider adapters

fal is the primary provider for configured image and video aliases. Its adapter uses asynchronous
queue submission and retains only `request_id`; response, status, and cancel URLs are reconstructed
from the allowlisted model and request ID rather than stored. A webhook URL is supplied on submit.

fal webhooks are verified from the raw body using its published ED25519 scheme: the four required
`X-Fal-Webhook-*` headers, a timestamp within 300 seconds, the newline-delimited signed message,
and keys fetched from `https://rest.fal.ai/.well-known/jwks.json`. JWKS values are cached for no
longer than 24 hours. The cryptography dependency already in the backend performs ED25519
verification, so no generation SDK dependency is required.

Runway is an optional video-only adapter. It submits an asynchronous task and polls
`GET /v1/tasks/{id}` at a configured interval of at least five seconds with jitter. Runway does not
participate in the fal webhook route. Output URLs from both providers are ephemeral capabilities:
the worker downloads them immediately and never exposes or persists them.

Provider timeouts and throttling are retryable. Invalid requests, unknown models, provider
rejections, moderation rejections, and invalid returned media are terminal. Cancellation invokes
the provider cancellation operation best-effort and still records the local durable cancellation.
The provider request ID is the idempotency anchor: a redelivered Job polls the existing request and
never submits another billable generation.

An in-process circuit breaker is maintained per provider/model in each worker process. It opens
after the configured consecutive retryable-failure threshold, remains open for the configured
cooldown, and allows one trial after cooldown. It is deliberately not a cross-worker distributed
breaker; global operational provider health belongs to Task 44.

References:

- <https://fal.ai/docs/documentation/model-apis/inference/queue>
- <https://fal.ai/docs/documentation/model-apis/inference/webhooks>
- <https://docs.dev.runwayml.com/api-details/sdks/>
- <https://docs.dev.runwayml.com/assets/outputs/>

## Prompt and output safety

The generation module constructs the prompt from the existing Visual Intent; the browser never
sends arbitrary prompt text. A deterministic prompt policy rejects requests containing disallowed
impersonation, sexual content involving minors, or deceptive real-person claims before submission.
It also includes the suggestion's existing exclusions and factual-risk flags as negative or safety
constraints.

Provider moderation evidence is mandatory on completion. fal results must expose the configured
model's safety result; Runway `SAFETY.INPUT.*` and `SAFETY.OUTPUT.*` failures normalize to prompt
and output moderation rejection. An adapter result without affirmative moderation evidence is
invalid and cannot create an Asset.

The completed output is streamed into the Job workspace with byte and checksum limits, validated
through the existing media processor, normalized into a playable B-roll rendition, uploaded to the
private object store, and read back for size/checksum verification. `Asset` and `AssetProvenance`
are inserted in the same transaction before the suggestion points at the Asset. A generated still
has no duration and is animated by the editor's existing Ken Burns placement; a generated video
must not exceed the configured generation duration.

## HTTP and durable lifecycle

Two authenticated, CSRF-protected routes extend the B-roll API:

1. `POST /api/v1/broll-suggestions/{suggestion_id}/generation-estimates`
2. `POST /api/v1/broll-suggestions/{suggestion_id}/generate`

Both require `EDIT_WRITE`, which admits owners, admins, and editors while excluding reviewers and
viewers. Both obscure foreign, missing, and inaccessible suggestion IDs behind the existing 404.

The estimate request names only `mediaKind`. The server derives prompt, dimensions, duration,
model, and output count from the suggestion and configured policy. The response reports
`available`; when false it carries a fixed public reason such as provider unavailable or video
disabled and no monetary or quota values. When true it carries the complete `GenerationEstimate`
and a short-lived signed confirmation token binding Workspace, User, suggestion, request, estimate,
and expiry. Estimation performs no quota reservation and no billable generation.

The generate request carries the confirmation token and `videoConfirmed`. The server verifies the
token and current User, re-proves the suggestion is eligible, requires `videoConfirmed=true` for
video, locks the suggestion, and checks idempotency. In one transaction it:

- creates one `BROLL_GENERATE` Job,
- reserves one generated-image unit for an image, or one generated-video unit plus the estimated
  generated seconds for a video,
- records the requesting User and estimate snapshot in sanitized provider metadata, and
- moves the suggestion to `generation_requested`.

The transaction commits before UUID-only Celery dispatch. A repeated idempotency key and identical
payload returns the existing Job. The same key with a different suggestion or request returns
`CONFLICT`.

The worker locks and loads the request, changes the suggestion to `generating`, checks cancellation,
and either submits once or resumes the stored provider request. A verified webhook records a
sanitized completion signal and returns `204` immediately; it does not download media or change Job
state. The webhook then best-effort dispatches the existing Job. When no webhook arrives, the
worker polls only until the configured attempt deadline and raises a retryable timeout so Celery's
existing retry policy schedules a later attempt.

On success, the worker persists the Asset and provenance, attaches it to the same suggestion,
sets `source_type=generated`, returns the suggestion to `proposed`, and settles every reservation
with actual usage. Settlement is idempotent: an already-settled reservation is returned unchanged
and cannot be charged twice. Failure or cancellation sets the suggestion to `failed` and releases
every still-reserved generation reservation. Provider-reported billed usage on a failed request is
settled rather than released.

## Eligibility and stock-first behavior

Generation is available only when the suggestion is still reviewable and either has no Asset or
its relevance score is below `broll_min_relevance`. A stock or User Asset at or above that threshold
suppresses both the UI offer and backend admission. Accepted, placed, replaced, removed, rejected,
already-generating, or already-generated suggestions cannot start another generation in Task 31.

The first UI action is always “Generate still.” The dialog shows the image estimate and a primary
confirmation button. When generated video is enabled and configured, a secondary “Consider video”
action requests a video estimate; that estimate opens a distinct second confirmation view and the
final request must carry `videoConfirmed=true`. There is no one-click path from a suggestion card
to a video Job.

## Configuration

Settings add:

- fal API key, webhook base URL, image model alias, video model alias, and model allowlist entries;
- optional Runway API secret and video model alias;
- generated image/video provider selection;
- maximum video duration, output byte limit, provider request timeout, poll interval, polling
  attempt deadline, and adapter retry count;
- circuit-breaker failure threshold and cooldown;
- estimate token lifetime;
- existing monthly image, video, and generated-second budgets; and
- existing `generative_video_enabled=false` default.

Missing provider credentials or aliases are a normal unavailable capability in local and production
configuration. If credentials are present, model configuration must validate completely at startup.
No Sora alias or provider model ID is accepted.

## Frontend

`GenerationConfirmDialog` is controlled by `BrollPanel` and uses generated OpenAPI clients. The
suggestion card shows generation only for an eligible empty/below-threshold proposal. The dialog
renders output count, duration, resolution, latency class, image/video quota units, provider
credits, and estimated USD cost before confirmation. It never renders provider or planner text as
HTML.

After confirmation, the panel reports that generation continues in the background, follows the
returned Job through the existing Job event mechanism, and refetches suggestions on terminal state.
Quota, concurrency, disabled-video, moderation, and provider-unavailable outcomes use fixed public
copy; unexpected errors retain the standard request ID.

## Testing

Contract tests hold every adapter to estimate, submit, poll/webhook completion, timeout, provider
rejection, prompt/output moderation rejection, cancellation, retry idempotency, unknown-model
rejection, and normalized usage/cost behavior. Raw error text, URLs, and credentials are asserted
absent from durable and public values.

Integration tests use the real Postgres quota ledger, deterministic provider, deterministic object
store, checked media fixtures, and injected clock. They prove stock-first suppression, image before
video, second video confirmation, authorization, tenant isolation, atomic concurrent reservation,
release after failure/cancellation, actual settlement exactly once, redelivery without resubmission,
webhook deduplication, and generated Asset/provenance atomicity.

Frontend tests prove estimate visibility before confirmation, no direct video submission, disabled
and unavailable states, safe text rendering, background Job feedback, and API error mapping. An
opt-in sandbox smoke test may call configured provider credentials; ordinary tests and CI make no
billable or network generation call.

Completion requires all four backend and all four frontend gates from `AGENTS.md`, contract export
and generated-client cleanliness, migration drift verification, and an update to `PROGRESS.md`.

