# Durable Publication Orchestration Design

**Status:** Approved in chat

**Date:** 2026-09-09

**Task:** `plan.md` Task 37

## Purpose

Task 37 establishes the provider-neutral durable boundary between an approved Render Artifact and
the provider adapters added in Tasks 38-42. It stores one independent Publication per selected
Social Account, freezes the exact approval evidence, claims scheduled work safely, and records
every transition before any future provider side effect.

This task does not transcode provider renditions or call YouTube, Instagram, or TikTok. It makes
those later operations restart-safe and independently retryable.

## Domain and state machine

The authoritative states are `draft`, `awaiting_approval`, `scheduled`, `preflighting`,
`transferring`, `processing`, `published`, `retryable_failed`, `reconnect_required`,
`permanent_failed`, and `cancelled`. The spelling and names follow Section 4 of `plan.md` and the
approved social-publishing design.

Allowed transitions are data, not route logic:

```text
draft -> awaiting_approval
awaiting_approval -> scheduled | preflighting | cancelled
scheduled -> preflighting | cancelled | reconnect_required
preflighting -> awaiting_approval | transferring | retryable_failed | reconnect_required |
  permanent_failed | cancelled
transferring -> processing | retryable_failed | reconnect_required | permanent_failed
processing -> published | retryable_failed | reconnect_required | permanent_failed
retryable_failed -> preflighting | cancelled
reconnect_required -> scheduled | awaiting_approval | cancelled
```

`published`, `permanent_failed`, and `cancelled` are terminal. Cancellation is allowed only before
the provider boundary says cancellation can still be guaranteed. A late cancel leaves the durable
state unchanged and reports a conflict; it never claims a provider post was cancelled.

## Persistence

Migration `0020_publications.py` adds:

- `publication_batches`, the user-facing grouping for one prepare/confirm flow;
- `publications`, one destination-specific lifecycle row per Social Account;
- `publication_attempts`, append-only sanitized execution history;
- `provider_events`, append-only deduplicated webhook/poll evidence; and
- `publication_outbox`, messages inserted in the same transaction as claim/transition state.

Every table is Workspace-scoped, has a composite `(workspace_id, id)` identity, uses composite
tenant foreign keys, and receives forced RLS. Publication child rows include `workspace_id` even
when ancestry could infer it. Runtime grants keep attempts, events, and delivered outbox rows from
being rewritten or deleted.

A prepared request creates one batch plus draft Publications. Confirmation locks all rows and
copies immutable values onto each Publication: Edit Revision, Render Artifact and SHA-256,
destination, metadata, provider options, consent, Social Account capability version, provider
policy version, approving User, approval time, requested IANA timezone, and normalized UTC instant.
Later changes to the Edit, Render Artifact, or Social Account cannot alter those columns.

The existing Workspace idempotency ledger binds the prepare request key to a payload hash and batch
response. Each destination also has a stable provider operation key derived from its Publication
UUID and operation generation. Attempts and provider events deduplicate ambiguous responses without
blindly repeating a provider create operation.

## Modules and ownership

- `publishing/models.py` owns immutable request/response values, enums, and snapshot validation.
- `publishing/state_machine.py` owns pure transition and truthful-cancellation rules.
- `publishing/repository.py` owns tenant-scoped SQL, row locks, append-only records, and idempotency.
- `publishing/use_cases.py` owns prepare, preflight-readiness, confirm, read/list, retry, cancel, and
  the Task 36 future-publication coordinator.
- `publishing/scheduler.py` owns bounded due-work claims through `FOR UPDATE SKIP LOCKED`.
- `publishing/outbox.py` owns durable message construction and delivery acknowledgement.
- `publishing/tasks.py` exposes scalar-only Celery entry points and rechecks current Workspace
  Membership plus Social Account capability before emitting dispatch work.
- `api/routes/publications.py` owns request validation, authorization, CSRF, error mapping, and
  response shaping only.

## Request flow

Preparing a draft requires `EDIT_WRITE`, a valid immutable Edit Revision/Render Artifact pair, at
least one explicitly selected live Social Account, and an idempotency key. It creates independent
draft Publications so changing one destination never changes a sibling.

Preflight in Task 37 validates durable prerequisites only: destination still exists, snapshot input
is strictly shaped, timezone is an IANA zone, schedule is not in the past, and the Render Artifact
still matches the Edit Revision. Provider-media preflight belongs to Task 38.

Confirmation requires `PUBLISH`, explicit consent for every destination, and recent authentication
when destination, visibility, or future schedule makes the action irreversible. It freezes the
snapshot and moves each destination to `scheduled` or `preflighting`. The same request key and
payload replays the same batch; a changed payload conflicts.

The scheduler claims due `scheduled` rows in bounded batches. Under the same transaction it moves
each eligible row to `preflighting` and appends one outbox record. It skips locked rows, so multiple
schedulers never claim the same Publication. Dispatch consumers reload scalar IDs, reprove current
Membership/publishing authority, and recheck the Social Account/capability version. Capability
drift returns a Publication to `awaiting_approval`; missing credentials produce
`reconnect_required`.

## Crash recovery and events

Every transition holds the Publication row lock, appends a Publication Attempt or Provider Event
when relevant, and inserts required outbox work before commit. An outbox message remains pending
until explicit acknowledgement. A worker crash after provider ambiguity records a reconciliation
checkpoint; retry cannot enqueue a new provider create until reconciliation resolves the prior
operation.

Provider events deduplicate by `(Workspace, provider, Social Account, provider_event_id)` when an
ID exists, otherwise by a stable payload hash plus event type. Duplicate webhook or poll evidence
returns the first record and cannot apply a transition twice.

## HTTP and errors

Task 37 adds the publication endpoints listed in Section 5. All private routes use the existing
Session, Workspace Membership, CSRF, rate-limit, request-ID, and sanitized error boundaries.
Guessed cross-Workspace UUIDs return the same `NOT_FOUND` response as absent UUIDs.

Stable new errors distinguish illegal state, changed idempotency payload, invalid schedule/timezone,
approval required, capability drift, reconnect required, and cancellation no longer guaranteed.
Responses expose no object-storage key, OAuth material, checkpoint secret, provider upload URL, or
raw provider payload.

## Verification and non-goals

Contract tests prove the complete transition matrix. Integration tests prove immutable snapshots,
independent destinations, replay/conflict idempotency, concurrent scheduler claims, pending-outbox
recovery, duplicate provider events, truthful cancellation, authorization rechecks, tenant/RLS
isolation, and API sanitization.

Task 38 owns provider renditions and media preflight. Tasks 39-41 own official provider adapters.
Task 42 owns complete provider dispatch/reconciliation and provider-specific limits. Task 43 owns
the publishing frontend. Task 45 owns retention. Task 46 owns production scheduler deployment.
