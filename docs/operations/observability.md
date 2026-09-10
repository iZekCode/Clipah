# Observability operations

What this system records about itself, what it is forbidden to record, and which numbers
an on-call engineer is woken for.

Telemetry here is treated as a published artifact rather than a debugging convenience. It
is retained longer than any request, read by people who are not looking at the code, and
exported to systems this project does not control. So the rule that governs every module
in `backend/src/clipah/observability/` is that a record says what happened and never the
material that made it happen.

## What is recorded

| Signal | Where it goes | What it carries |
| --- | --- | --- |
| Structured logs | stdout as JSON, one object per line | The declared fields only, listed below |
| Traces | An OTLP collector when one is configured, otherwise in-process only | Span name, duration, declared attributes, and an error type |
| Metrics | The same collector | The declared instruments, cut by their declared labels |
| Error reports | Sentry when a DSN is configured | The scrubbed event, never headers, cookies, or request bodies |
| Provider usage | The `provider_usage` table | Workspace, provider, operation, model or API version, units, and cost |

## The two rules that make this safe

**A log event may only use declared field names.** The allowlist lives in
`LOG_FIELDS`. A field nobody declared is dropped and counted as `droppedFields` rather than
written. This is what makes "no transcript text ever reaches a log" a property of the
system instead of a habit: a caller cannot invent `transcriptText` and have it survive.

**Every value that survives that check is scrubbed.** Authorization headers, absolute
URLs, `name=value` assignments, local filesystem paths, and high-entropy runs are replaced
with `[redacted]`. Stable error codes, UUIDs, and provider request identifiers are left
intact, because a record that says nothing is not worth retaining. The same scrubber runs
over span attributes, metric labels, and Sentry events.

The declared fields are, in three groups: the correlation identifiers (`requestId`,
`traceId`, `jobId`, `workspaceId`, `projectId`, `userId`, `publicationId`, `batchId`,
`socialAccountId`, `sourceConnectionId`, `editId`, `revisionId`, `renderId`, `assetId`,
`candidateId`, `campaignId`, `providerEventId`, `idempotencyKey`); what happened (`event`,
`level`, `logger`, `timestamp`, `code`, `outcome`, `reason`, `retryable`, `attempt`,
`stage`, `jobKind`, `queue`, `status`, `statusCode`, `method`, `route`, `provider`,
`operation`, `model`, `apiVersion`, `quotaResource`, `decision`, `environment`,
`capabilityVersion`, `configVersion`); and how much (`durationMs`, `latencyMs`,
`ageSeconds`, `bytes`, `count`, `inputUnits`, `outputUnits`, `estimatedCostUsd`,
`speedRatio`).

## Configuration

Every setting takes the `CLIPAH_` prefix.

| Setting | Default | What it does |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | Refused at startup unless it names a standard level. |
| `OTEL_EXPORTER_ENDPOINT` | unset | The OTLP HTTP collector. Unset means spans and metrics are still created but exported nowhere, so the instrumented path runs in every environment. |
| `OTEL_SERVICE_NAME` | `clipah` | The service name a collector groups by. |
| `SENTRY_DSN` | unset | Error reporting. Unset disables it entirely. |
| `PROVIDER_SHUTDOWNS` | empty | Announced retirements, each written `model:<identifier>=YYYY-MM-DD` or `api:<identifier>=YYYY-MM-DD`. Malformed entries fail startup. |

## The deprecation and readiness monitor

An external model that is switched off on a Monday morning is an outage nobody scheduled.
`readiness_report` compares what this deployment is configured to call against the
announced shutdowns it knows about, and answers in two parts:

- a **warning**, once a shutdown is within 180 days, naming the date and the replacement;
- a **failure**, once the date has passed, which makes `GET /health/ready` return 503.

Failing early would take a working deployment down for a migration it could still
schedule; failing late would let it learn about the retirement from its own users. Only one
date lives in code, the Sora shutdown that `plan.md` records. Everything else reaches the
monitor through `CLIPAH_PROVIDER_SHUTDOWNS`, because inventing dates would produce
confident warnings about retirements nobody announced. When a provider publishes a sunset,
add it there in the same change that records it in the provider's own runbook.

Run `scripts/check-observability.sh` in staging before a release. It performs a local trace
smoke test, runs the readiness monitor, and proves the redaction pipeline still removes a
credential from a log event.

## Alerts

Every threshold below is a starting point to be replaced by a seven-day baseline once this
deployment has one. Until that baseline exists, alert on the direction of change rather
than the absolute number, and treat a first firing as a question rather than an incident.

| Alert | Metric | Threshold |
| --- | --- | --- |
| API error rate | `clipah.http.duration` by `statusCode` | 5xx above 1% of requests over 5 minutes, or any 5xx on `/health/ready` |
| API latency | `clipah.http.duration` | p95 above 1s over 10 minutes |
| Worker queue age | `clipah.queue.latency` | p95 above 5 minutes for `ai`, 15 minutes for `render` |
| Scheduler lateness | `clipah.scheduler.latency` | p95 above 2 minutes, which is when a member notices |
| Repeated provider failure | `clipah.stage.outcome` by `code` | The same code more than 10 times in 15 minutes |
| Retry storm | `clipah.stage.outcome` `outcome=retried` | Retries entering faster than they leave for 15 minutes |
| Source or Social Account expiry | `clipah.connection.expiry` | Any connection under 72 hours from expiry |
| Token refresh failure | `clipah.oauth.refresh` `outcome=rejected` | More than 3 in 15 minutes for one provider |
| Invalid webhook spike | `clipah.webhook.rejected` | More than 10 in 5 minutes, which means a wrong secret or somebody probing |
| Stuck publications | `clipah.publication.stuck_age` | Any destination over 1 hour transferring or 6 hours processing |
| Partial batch success | `clipah.publication.partial_success` | Any occurrence, because a member has to be told |
| Time to publish | `clipah.publication.time_to_publish` | p95 above 30 minutes |
| Stock or social quota exhaustion | `clipah.provider.quota` `outcome=refused` | Any refusal, and page when it is the last quarter of the month |
| Generation circuit breaker | `clipah.provider.request` `outcome=failed` for a generation provider | More than 5 failures in 10 minutes |
| Generation cost | `clipah.generation.cost_per_exported_minute` | Above twice the trailing seven-day median |
| Render failure rate | `clipah.stage.outcome` `jobKind=render` | Failures above 5% of attempts over 30 minutes |
| Render speed | `clipah.render.speed_ratio` | p95 above 2.0, meaning the fleet renders at half real time |
| Storage failure | `clipah.storage.duration` `outcome=failed` | More than 5 in 5 minutes |
| Retention backlog | `clipah.stage.outcome` `jobKind=cleanup` | No successful cleanup in 24 hours |
| Candidate collapse | `clipah.candidates.count` `stage=ranked` | Median below half the trailing seven-day median, which is how a prompt or model change first shows |

`clipah.build.info` is emitted once per process with the environment, version, configured
models, and capability version. Join it to any of the above before concluding that a change
in a number was caused by traffic rather than by a deployment.

## Following one incident

Every HTTP request carries an `X-Request-Id`, which is echoed in the error body a member
sees and bound to every log line that request produces. A job binds `jobId`, `workspaceId`,
`projectId`, and `jobKind` for the whole of its attempt, and one publication binds
`publicationId` and `batchId`. So an incident is followed in this order:

1. Take the request identifier from the member's error message.
2. Find its log line, and read the `workspaceId` and the route.
3. Find the jobs for that Workspace and project in the same window.
4. For a publishing incident, find the `publicationId` and read its attempts, which record
   provider evidence without recording any provider credential.

If a step in that chain is missing, that is a defect in the instrumentation and not a
reason to reach for the database.
