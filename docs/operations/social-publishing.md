# Social publishing operations

How each provider is configured, in what order the gates open, and how to stop publishing
within one deployment when a provider or this application is at fault.

Publishing acts on somebody else's account under a standing permission they gave once, so
every part of it is opt-in, staged, and reversible from this side. A deployment without a
provider's credentials reports that provider as absent rather than broken, and no gate
opens because another one already did.

## Configuration

Every setting takes the `CLIPAH_` prefix.

| Setting | Default | What it does |
| --- | --- | --- |
| `SOCIAL_PUBLISHING_ENABLED` | `false` | The one switch above every provider. With it off, no provider gate can be open and the browser is told publishing is unavailable. |
| `YOUTUBE_PUBLISHING_ENABLED` | `false` | Opens the YouTube gate. Requires the four YouTube credentials and audit approval. |
| `YOUTUBE_OAUTH_CLIENT_ID`, `YOUTUBE_OAUTH_CLIENT_SECRET`, `YOUTUBE_OAUTH_REDIRECT_URI`, `YOUTUBE_API_KEY` | — | All four together, or the gate refuses to open. |
| `YOUTUBE_AUDIT_APPROVED` | `false` | Whether Google has audited this application. Until it is true the browser only offers private visibility. |
| `INSTAGRAM_PUBLISHING_ENABLED` | `false` | Opens the Instagram Reels gate. |
| `INSTAGRAM_OAUTH_CLIENT_ID`, `INSTAGRAM_OAUTH_CLIENT_SECRET`, `INSTAGRAM_OAUTH_REDIRECT_URI` | — | All three together, or the gate refuses to open. |
| `INSTAGRAM_AUDIT_APPROVED` | `false` | Whether Meta approved this application's Reels publishing use case. |
| `TIKTOK_PUBLISHING_ENABLED` | `false` | Opens the TikTok gate. Without Direct Post approval this means drafts only. |
| `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_OAUTH_REDIRECT_URI` | — | All three together, or the gate refuses to open. |
| `TIKTOK_AUDIT_APPROVED` | `false` | Whether TikTok has audited this application for Direct Post. Until it is true every TikTok publication lands in the creator's inbox as a draft. |
| `MULTI_DESTINATION_SCHEDULING_ENABLED` | `false` | The last gate. Until it is true, one publication carries exactly one destination. |
| `*_API_VERSION` | `v3`, `v22.0`, `v2` | The one provider API version this deployment may call. Retired versions are refused at startup. |

Every OAuth redirect URI must be an exact HTTPS callback ending in that provider's own
`/api/v1/social-oauth/{provider}/callback` path. Startup fails on anything else, including
an `http` URI, a wildcard, or another provider's path.

The browser learns which gates are open from `GET /api/v1/me`, whose `capabilities` carry
`socialPublishing`, `youtubePublishing`, `youtubePublicPrivacy`, `instagramPublishing`,
`tiktokPublishing`, `tiktokDirectPost`, and `multiDestinationScheduling`. A capability is
not an authority: every route still proves membership, role, and authentication freshness
for itself.

## The rollout order

Each gate opens on its own, and only after its evidence exists.

1. **YouTube, private and audited-restricted.** Contract tests pass, sandbox evidence is
   recorded, and `YOUTUBE_AUDIT_APPROVED` stays false so nothing can be published beyond
   private visibility.
2. **Instagram Reels.** Meta's app review is complete for the Reels publishing use case,
   and the deauthorization and data-deletion callbacks are reachable and verified.
3. **TikTok draft fallback.** `TIKTOK_AUDIT_APPROVED` stays false, so every publication
   arrives in the creator's TikTok inbox and a human publishes it from the app.
4. **TikTok Direct Post.** Only after TikTok's audit. The composer states the change in
   plain words, because the effect of one click changes from a draft to a live post.
5. **Multi-destination scheduling.** Last, because it multiplies the blast radius of every
   other gate.

At each gate, all five of these are required before the flag is turned on:

- `scripts/run-social-provider-contracts.sh --adapter=fake` passes.
- `scripts/run-social-provider-contracts.sh --adapter=sandbox` passes with that provider's
  own test credentials, and its output is kept as review evidence.
- The provider's policy review or audit is recorded, with its decision date.
- This runbook is current for the provider being opened.
- The rollback switch below has been exercised in staging.

## Credentials

Provider credentials are read from the environment and never written to the database. The
OAuth grants themselves are stored encrypted and are never returned by any route: the API
reports status, scopes, and expiry only. Rotating a client secret does not invalidate
stored grants; revoking an application's access at the provider does, and every affected
Social Account moves to `reconnect_required` on its next use.

To rotate one provider's client secret: set the new value, restart the API and workers,
then confirm one refresh succeeds on a connected account. If refreshes start failing with
`SOCIAL_ACCOUNT_RECONNECT_REQUIRED`, the grants were invalidated and each member has to
reconnect from the Connections page.

## Callback domains and webhook verification

- OAuth callbacks are exact per-provider HTTPS paths, checked at startup.
- Meta sends deauthorization and data-deletion callbacks as a `signed_request`. The
  handler requires `HMAC-SHA256`, rejects anything older than 300 seconds, deduplicates on
  a stable digest, and answers a data-deletion request with a URL and confirmation code.
- TikTok signs deliveries as `TikTok-Signature: t=…,s=…` over the timestamp and the raw
  body. The handler checks the client key, rejects anything outside a 300-second window,
  and never adopts a publish identifier from an `authorization.removed` event.
- No webhook changes publication state inline. A verified, new delivery is recorded as
  evidence and enqueues exactly one reconciliation for a worker.

## Stuck publications

A destination is stuck when it has been `transferring` for more than an hour, or
`processing` for more than six. The reconciler claims a bounded page of them, reads what
the provider actually reports, and only then changes state. An unreadable provider is
never treated as truth; the destination stays where it is.

To unstick one destination by hand, run the operator reconciliation for it. It refuses a
destination that is not stuck, refuses one from another Workspace, and refuses to act on
an observation it could not make. A destination whose attempt was ambiguous — the transfer
may or may not have reached the provider — cannot be retried until it has been reconciled,
because a retry could publish the video twice.

## Quota and rate alerts

Each destination reserves one publication from its Workspace quota when it is dispatched.
A retryable or ambiguous attempt keeps that reservation, because it may already have been
spent at the provider. Only a terminal refusal returns it.

Alert on these, per provider and Workspace:

- publications entering `retryable_failed` faster than they leave it,
- any destination reaching its attempt budget,
- YouTube's daily upload quota and Instagram's `content_publishing_limit` approaching their
  ceilings,
- webhook deliveries failing signature verification, which means a secret is wrong or
  somebody is probing the endpoint.

## Privacy and data deletion

A member ends a connection from the Connections page. Disconnecting cancels that account's
unpublished work, erases the stored grant so it cannot be recovered, and leaves already
published videos where they are, because Clipah cannot and should not remove them. Meta's
data-deletion callback is answered the same way, and the confirmation code it returns is
what a member quotes when asking about the deletion's status.

## Rolling back a provider incident

1. Set that provider's `*_PUBLISHING_ENABLED` to `false` and restart the API and workers.
   The routes for that provider start answering as if they do not exist, and the browser
   stops offering it. Nothing already published is touched.
2. If the incident is broader, set `SOCIAL_PUBLISHING_ENABLED=false`, which closes every
   gate at once.
3. Scheduled destinations stay scheduled and simply do not dispatch while the gate is
   closed. Cancel them explicitly if the incident will outlast their scheduled time.
4. When reopening, reopen one gate at a time, in the rollout order above, and re-run the
   provider contract script before each one.
