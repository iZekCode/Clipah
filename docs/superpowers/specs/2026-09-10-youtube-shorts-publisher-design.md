# YouTube Shorts Publishing Adapter Design

**Status:** Approved design, written for implementation review

**Date:** 2026-09-10

**Task:** 39 — Implement the official YouTube Shorts publishing adapter

**Primary references:**

- [Videos: insert](https://developers.google.com/youtube/v3/docs/videos/insert)
- [Video resource](https://developers.google.com/youtube/v3/docs/videos)
- [Resumable uploads](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol)
- [Channels: list](https://developers.google.com/youtube/v3/docs/channels/list)
- [Captions: insert](https://developers.google.com/youtube/v3/docs/captions/insert)
- [Thumbnails: set](https://developers.google.com/youtube/v3/docs/thumbnails/set)
- [YouTube API revision history](https://developers.google.com/youtube/v3/revision_history)

## Purpose

Task 39 adds Clipah's first production social-publishing provider. It publishes one frozen,
approved Social Rendition to the exact connected YouTube channel through the official YouTube
Data API, survives interrupted or ambiguous uploads without duplicating a video, and reports
YouTube's processing result truthfully.

The adapter does not claim to upload a distinct “Short.” YouTube classifies uploaded videos from
their properties. Clipah may describe a square or vertical video of at most three minutes as
Shorts-eligible, but it never promises that YouTube will classify it as a Short.

## Scope

The implementation includes:

- Publishing authorization and channel-identity validation.
- Strict YouTube metadata and status request models.
- Compliance-audit privacy enforcement.
- Native scheduled publication with `status.publishAt`.
- Resumable video upload with encrypted session storage and safe byte checkpoints.
- Reconciliation before retry after an ambiguous response.
- Processing-status polling and normalized errors.
- Explicit, independently retryable custom-thumbnail and timed-caption operations.
- Deterministic fake-server contract tests and an opt-in sandbox smoke test.

Task 42 remains responsible for multi-provider dispatch, queue ownership, provider/account locks,
backoff scheduling, quota reservations, and batch-wide reconciliation. Task 43 remains responsible
for the complete composer and publishing-history UI.

## OAuth and channel binding

The initial publishing grant requires the existing `youtube.upload` scope. The existing YouTube
read scope remains necessary to call `channels.list(mine=true)` and prove that the live credential
still names the Social Account's frozen external channel ID immediately before provider work.

Timed caption upload requires `youtube.force-ssl`; it is an optional, separately consented scope
expansion rather than part of the minimum upload grant. A Publication that requests captions
without that scope returns a capability error before video upload. Custom-thumbnail support is
also represented as a capability and is attempted only when the account snapshot and explicit
Publication request enable it.

Provider authentication failures never expose tokens or provider response bodies. Revoked or
invalid grants normalize to reconnect-required. The existing Social Account credential service
continues to own refresh, rotation, encryption, and bounded plaintext leases.

## Adapter boundary

`YouTubePublisher` implements a narrow `SocialPublisher` protocol with provider-specific typed
values. Its operations are:

- `confirm_channel` — call `channels.list(part=id,snippet&mine=true)` and require exactly the
  expected channel ID.
- `begin` — validate the frozen request and initiate one resumable upload session.
- `query_upload` — reconcile the server's acknowledged byte position or completed response.
- `transfer` — upload only the next contiguous byte range and return a new checkpoint.
- `poll` — retrieve `status,processingDetails` for the authoritative video ID.
- `set_thumbnail` — perform one explicit thumbnail operation after video creation.
- `insert_caption` — perform one explicit timed-caption operation after video creation.

The adapter receives an injected `httpx.Client`, credential lease, immutable media reader, clock,
and encrypted checkpoint vault. It never reads mutable Edit state and never opens internal object
storage by key. Task 38 supplies the exact bound Social Rendition bytes and checksum.

## Request model and privacy policy

The request model contains:

- Title: 1–100 characters.
- Description: at most 5,000 UTF-8 bytes.
- Tags: a tuple of individual strings, never one comma-separated value.
- Category ID: a non-empty numeric string chosen from a previously validated capability snapshot.
- Explicit made-for-kids selection.
- Explicit realistic altered/synthetic-media disclosure.
- Requested privacy: `private`, `unlisted`, or `public`.
- Optional scheduled UTC instant.
- Optional timed-caption and custom-thumbnail descriptors.

The existing deployment setting `youtube_audit_approved` defaults to `false`. While false, the
effective upload privacy is always `private`. The approved preflight/confirmation evidence records
both requested and effective privacy plus the fixed reason `youtube_compliance_audit_required`.
The adapter rejects any request whose frozen confirmation evidence omitted that restriction.

When the flag is true, the adapter may use the explicitly approved requested privacy. Native
scheduling is allowed only for an effective private upload with a future `publishAt`; the requested
post-schedule privacy must be public. A past or naive timestamp is invalid. No privacy value is
silently upgraded or inferred.

## Resumable upload and secret handling

Session initiation sends `videos.insert` metadata with `X-Upload-Content-Length` and
`X-Upload-Content-Type`. A successful response supplies a secret `Location` URI. The URI is written
immediately to an injected encrypted checkpoint vault bound to Workspace, Publication, provider,
and attempt. Only the resulting opaque secret reference is stored in
`Publication.encrypted_checkpoint_reference`.

Safe checkpoint metadata contains total bytes, acknowledged bytes, source checksum, session
generation, and whether the final request outcome is ambiguous. It never contains the session URI,
access token, authorization header, signed source URL, object key, or response body.

Chunks are contiguous and aligned to 256 KiB except the final chunk. A `308` response advances the
checkpoint from the inclusive `Range` header. Before retrying after a timeout or retryable 5xx, the
adapter sends an empty status `PUT` with `Content-Range: bytes */TOTAL`:

- `308` resumes from the exact acknowledged next byte.
- `200` or `201` with a valid video resource records the authoritative video ID.
- `404` before any ambiguous final transfer may create a new session.
- `404` after an ambiguous final transfer blocks automatic replay because completion cannot be
  disproved safely.
- Other permanent 4xx responses fail without creating another session.

Every returned checkpoint must be durably persisted before the caller sends the next chunk. A
completed upload never sends video bytes again.

## Processing and normalized outcomes

After `videos.insert` returns a video ID, the Publication is processing even if optional attachment
operations remain. `videos.list(part=status,processingDetails&id=VIDEO_ID)` is authoritative:

- Processing states remain nonterminal and retain the video ID.
- Successful processing becomes published when immediate visibility is effective, or remains
  scheduled when a future `publishAt` is active.
- Upload or processing failure becomes a sanitized permanent provider failure unless the HTTP
  boundary itself is retryable.
- A missing known video is an ambiguous reconciliation failure, never permission to upload again.

HTTP 401 and provider credential revocation normalize to reconnect-required. HTTP 429 and quota
errors normalize to rate-limited or quota-exhausted with `Retry-After` retained when safe. Timeouts
and 500/502/503/504 normalize to retryable unavailable. Metadata, policy, channel, and media
rejections normalize to permanent errors. Provider diagnostics remain bounded and secret-free.

## Optional thumbnail and caption operations

Thumbnail and timed-caption operations begin only after the base video ID exists. Each operation
has its own checkpoint state: `not_requested`, `pending`, `succeeded`, `retryable_failed`,
`permanent_failed`, or `unsupported`.

A failure in either operation never clears the video ID, restarts video transfer, or changes a
successfully uploaded base video to “not published.” Retrying an attachment retries only that
attachment. Caption requests require language, track name, a timed caption file, and the optional
scope. The deprecated automatic synchronization behavior is not used.

## Persistence integration

Task 39 uses the Publication fields established in Task 37:

- `provider_publication_id` stores the YouTube video ID.
- `provider_permalink` is derived only from the validated video ID.
- `encrypted_checkpoint_reference` stores the vault reference to the resumable session.
- `checkpoint_metadata` stores only safe upload and attachment state.
- append-only Publication Attempts store safe request IDs, byte checkpoints, normalized stage
  outcomes, and sanitized errors.

The integration layer locks one Publication, verifies its bound YouTube Social Account and Social
Rendition, persists a returned checkpoint, and commits before further external work is eligible.
It does not implement Task 42's cross-provider worker dispatcher.

## Testing

Contract tests use `httpx.MockTransport` and assert exact URLs, methods, query values, headers,
payloads, byte ranges, and redaction. They cover:

- Minimum and optional OAuth scopes.
- Exact live channel identity.
- Private-only enforcement before audit and audited privacy behavior.
- Title, description, tags, category, made-for-kids, and synthetic-media fields.
- Future native scheduling and invalid scheduling.
- Session initiation, chunk advancement, interruption, status query, expired sessions, and
  ambiguous completion.
- Processing success/failure, quota/rate limits, token expiry, and revocation.
- Independent thumbnail and caption success/retry/failure.
- Shorts eligibility wording without a classification guarantee.

Real-Postgres integration tests prove encrypted-reference persistence, safe checkpoint contents,
append-only attempt evidence, exact video-ID retention, and no video replay during attachment
retry. A slow test is skipped unless `CLIPAH_YOUTUBE_PUBLISH_SMOKE=1` and all required sandbox
credentials/channel/media inputs are explicitly supplied; it creates only a private test upload.

## Stop condition

Task 39 is complete when the fake-server contracts, Postgres integration tests, opt-in smoke-test
skip behavior, migration checks if persistence changes prove necessary, and all four backend gates
pass. No Instagram, TikTok, generic multi-provider dispatcher, webhook receiver, composer UI, or
production schedule worker is added.
