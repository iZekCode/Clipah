# Generative media operations

How generated B-roll is configured, what it costs, and how to turn it off in a hurry.

Generated media is the fallback for a beat stock could not illustrate. It is opt-in at
every level: a deployment without credentials simply reports the capability as absent,
generated video stays disabled until a deployment turns it on, and no Workspace is ever
billed without a member first reading an estimate and confirming it.

## Configuration

Every setting takes the `CLIPAH_` prefix.

| Setting | Default | What it does |
| --- | --- | --- |
| `GENERATED_IMAGE_PROVIDER` | `fal` | Which adapter answers still requests. |
| `GENERATED_VIDEO_PROVIDER` | `fal` | `fal` or `runway`. Selecting `runway` requires its complete configuration. |
| `FAL_API_KEY` | — | Absent means generated media is unavailable, not broken. |
| `FAL_WEBHOOK_BASE_URL` | — | A bare HTTPS origin. Required whenever a fal key is set. |
| `FAL_IMAGE_MODEL_ALIAS` / `FAL_IMAGE_MODEL_ID` | `image-default` / `fal-ai/nano-banana-2` | The one still endpoint this deployment may call. |
| `FAL_VIDEO_MODEL_ALIAS` / `FAL_VIDEO_MODEL_ID` | `video-default` / `fal-ai/kling-video/v2.6/pro/text-to-video` | The one video endpoint this deployment may call. |
| `RUNWAY_API_SECRET`, `RUNWAY_VIDEO_MODEL_ALIAS`, `RUNWAY_VIDEO_MODEL_ID` | — | All three together, or none. |
| `GENERATIVE_VIDEO_ENABLED` | `false` | The feature gate for generated video. Production always defaults it off. |
| `GENERATED_AUDIO_ENABLED` | `false` | Cannot be enabled: generated B-roll is silent by construction. |
| `GENERATION_MAX_DURATION_MS` | `5000` | May not exceed `BROLL_MAX_SHOT_MS`. |
| `GENERATION_MAX_OUTPUT_BYTES` | `100000000` | The download ceiling for one generated file. |
| `GENERATION_HTTP_TIMEOUT_SECONDS` | `30` | Per provider request. |
| `GENERATION_POLL_SECONDS` | `5` | Minimum five, per the providers' guidance. |
| `GENERATION_POLL_ATTEMPT_DEADLINE_SECONDS` | `60` | How long one worker attempt polls before handing the Job back to Celery. |
| `GENERATION_ADAPTER_RETRY_COUNT` | `3` | Bounded local retries. |
| `GENERATION_CIRCUIT_FAILURE_THRESHOLD` / `GENERATION_CIRCUIT_COOLDOWN_SECONDS` | `5` / `60` | The in-process circuit breaker per provider and model. |
| `GENERATION_ESTIMATE_TOKEN_TTL_SECONDS` | `300` | How long a confirmed price stays spendable. |
| `MONTHLY_GENERATED_IMAGES`, `MONTHLY_GENERATED_VIDEOS`, `MONTHLY_GENERATED_SECONDS` | plan limits | The Workspace budgets generation reserves against. |

**No configuration path accepts a model alias or provider model ID containing `sora`, in
any capitalization.** Startup fails rather than accepting one.

## What a request costs, and when

1. **Estimate.** `POST /api/v1/broll-suggestions/{id}/generation-estimates` prices a
   request the server derives itself. It reserves nothing and generates nothing. The
   response carries the estimate and a confirmation sealed with the deployment secret,
   bound to the Workspace, the User, the suggestion, and the exact request.
2. **Admission.** `POST /api/v1/broll-suggestions/{id}/generate` verifies the
   confirmation, re-proves eligibility, and in one transaction creates the Job, reserves
   one generated image — or one generated video plus its seconds — records sanitized
   metadata, and moves the suggestion to `generation_requested`.
3. **Settlement.** On success the worker settles each reservation with the usage the
   provider actually reported. On permanent failure or cancellation the still-reserved
   rows are released; usage already settled is left alone, because a provider that billed
   for a failed generation was still paid.

Generated video additionally requires `videoConfirmed: true`. There is no one-click path
from a suggestion card to a video Job.

## fal webhooks and JWKS

The fal adapter supplies a webhook URL on submit. `POST /api/v1/webhooks/generation/fal`
verifies the raw body against fal's ED25519 scheme — the four `X-Fal-Webhook-*` headers, a
timestamp inside ±300 seconds, the newline-delimited signed message, and keys fetched from
`https://rest.fal.ai/.well-known/jwks.json` and cached for at most 24 hours. The route
downloads no media and changes no Job state; a delivery is a wakeup, never authority.

**The durable webhook sink is not wired in this release.** Until it is, the route answers
`503` and completion is detected by the worker's own bounded polling plus Celery's retry
policy. Deployments do not need a public webhook endpoint for generation to work.

## Runway

Runway is video-only and does not participate in the fal webhook route. It submits a task,
polls `GET /v1/tasks/{id}` no faster than the configured interval, and cancels with
`DELETE /v1/tasks/{id}`. Its API version is pinned to `2024-11-06`. Because a Runway task
payload reports neither geometry nor billed credits, the requested geometry is recorded on
the durable handle and credits are derived from the configured per-second rate at Runway's
fixed one-cent credit price.

## Safety

- The prompt is built by the server from the suggestion's Visual Intent. The browser never
  sends prompt text, a model name, a size, or a cost.
- Requests naming impersonation, sexual content involving minors, or deceptive real-person
  claims are refused before any provider call.
- Provider moderation evidence is mandatory: a result without affirmative approval cannot
  create an Asset. fal `has_nsfw_concepts` and Runway `SAFETY.INPUT.*` / `SAFETY.OUTPUT.*`
  both normalize into the same refusal.
- Generated stills are decoded and verified with Pillow, accepted only as PNG, JPEG, or
  WebP, and bounded by pixel count and byte size. Generated video is probed and normalized
  to a private proxy, and rejected when longer than the request allowed.
- Output URLs are ephemeral capabilities. They are downloaded immediately and never
  logged, returned, or stored.

## Observability boundaries

Provider credentials, raw payloads, output URLs, and provider error text never reach
Postgres, Redis, logs, Job events, or API responses. What is durable is the provider name,
the opaque request ID, the server-built prompt, the model and version, the seed, the
moderation result, a usage snapshot, and the media checksum.

## Turning it off

- **One provider, immediately:** unset `CLIPAH_FAL_API_KEY` (or the Runway trio) and
  restart. The capability reports as unavailable; nothing errors.
- **Video only:** set `CLIPAH_GENERATIVE_VIDEO_ENABLED=false`. Estimates answer
  `video_disabled` and admission refuses.
- **Key rotation:** replace the credential and restart. Handles already stored stay valid,
  because they carry only the provider's own request identity.

In-flight Jobs at the moment of a disable fail as provider-unavailable, which is
retryable; their reservations are released when the Job is finally given up.

## Sandbox smoke test

`backend/tests/slow/test_generation_provider_smoke.py` is the only test that can reach a
live provider. It skips unless opted into explicitly, and never runs during ordinary
verification:

```bash
cd backend
CLIPAH_RUN_GENERATION_SMOKE=1 CLIPAH_FAL_API_KEY=... \
  uv run pytest tests/slow/test_generation_provider_smoke.py -m slow
```

The fal case exercises live pricing only and bills nothing. The Runway case submits one
five-second task and cancels it in a `finally` block.
