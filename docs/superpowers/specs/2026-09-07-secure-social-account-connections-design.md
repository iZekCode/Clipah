# Secure Social Account Connections Design

**Status:** Approved in chat; awaiting repository-owner review

**Date:** 2026-09-07

**Task:** `plan.md` Task 36

## 1. Purpose

Task 36 establishes the Workspace-scoped connection boundary that later publication tasks use.
It connects an explicitly authorized YouTube channel, Instagram professional account, or TikTok
creator account; stores public account metadata separately from an encrypted OAuth Grant; exposes
only redacted status; and lends decrypted credentials to one selected provider adapter for one
bounded operation.

This task does not publish media. Tasks 39-41 add the production provider publishing adapters.
Task 36 defines the provider-neutral OAuth and credential interfaces those adapters consume and
uses deterministic adapters to prove the connection lifecycle without external network calls.

## 2. Constraints and non-goals

- Workspace is the tenant boundary. Every Social Account, OAuth Grant, OAuth ceremony, and audit
  record carries a Workspace identity protected by composite foreign keys and RLS.
- Only owners and admins may connect, refresh, or disconnect Social Accounts, and each mutation
  requires a Session authenticated within the configured ten-minute recent-authentication window.
- Login Identity, Session, Source Connection, Social Account, and OAuth Grant are distinct domains.
  Equal providers, subjects, account names, or email addresses never link them.
- Access tokens, refresh tokens, client secrets, authorization codes, decrypted grant material, and
  provider diagnostics never enter ordinary database columns, JSON metadata, Redis, job arguments,
  logs, analytics, exceptions, or API responses.
- Task 36 does not add publishing state, provider media transfer, frontend screens, webhooks, or live
  provider smoke tests.
- The repository owner, not the agent, creates commits.

## 3. Provider-neutral domain

`social_accounts/models.py` defines closed enums and immutable values:

- `SocialProvider`: `youtube`, `instagram`, `tiktok`.
- `SocialConnectionStatus`: `active`, `reconnect_required`, `revoked`.
- `SocialAccount`: authoritative provider identity and safe display metadata.
- `PublishingCapabilities`: a versioned, provider-neutral snapshot whose provider-specific choices
  remain structured data rather than flattened promises.
- `OAuthGrantMaterial`: access token, optional refresh token, token type, and expiry evidence. Its
  printed representation is always redacted.
- `OAuthGrantLease`: a context manager that materializes `OAuthGrantMaterial` only for one bounded
  adapter call and clears mutable plaintext buffers at exit.
- `OAuthConnectionResult`, `RefreshedGrant`, and normalized provider failure categories.

The service surface promised to later tasks is:

```python
class SocialAccountService:
    def connect(...) -> SocialAccountSummary: ...
    def list(...) -> tuple[SocialAccountSummary, ...]: ...
    def get_capabilities(...) -> PublishingCapabilities: ...
    def refresh(...) -> SocialAccountSummary: ...
    def disconnect(...) -> None: ...
    def lease(...) -> OAuthGrantLease: ...
```

`connect` completes an already validated OAuth ceremony. Starting and validating the browser
ceremony belongs to `social_accounts/oauth.py` and the HTTP routes.

## 4. OAuth ceremony and provider boundary

`social_accounts/oauth.py` defines a `SocialOAuthProvider` protocol. Deterministic adapters used by
tests implement authorization endpoint discovery, code exchange, authoritative account lookup,
capability lookup, credential refresh, and revocation. Production HTTP provider adapters remain in
Tasks 39-41, but must implement the same protocol without changing the service contract.

Each provider has an immutable policy containing its exact configured redirect URI, API version,
authorization lifetime, and minimum scopes:

| Provider | Initial scopes | Reason |
| --- | --- | --- |
| YouTube | `https://www.googleapis.com/auth/youtube.readonly`, `https://www.googleapis.com/auth/youtube.upload` | Identify the destination channel and upload only to its videos. |
| Instagram | `instagram_business_basic`, `instagram_business_content_publish` | Identify an Instagram professional account and publish its media. |
| TikTok | `user.info.basic`, `video.upload` | Identify the creator and use the unaudited draft/inbox fallback. |

TikTok `video.publish` is incremental consent added only after the Direct Post audit gate in Task 41.
YouTube caption-management scopes and any broader provider permissions are also deferred until a
feature requires them.

Starting a connection creates a random 256-bit state value and PKCE verifier, derives an S256
challenge, and stores a durable ceremony row containing only the state hash, Workspace, actor,
provider, exact redirect URI, requested scopes, creation/expiry timestamps, and consumption state.
The encrypted, authenticated browser cookie holds the raw state, PKCE verifier, and ceremony ID
under a social-OAuth-specific key derivation context.

The callback:

1. opens the sealed cookie and checks provider, exact redirect URI, state, age, and actor/Workspace;
2. locks the matching ceremony and atomically marks it consumed before code exchange;
3. sends the exact stored redirect URI and PKCE verifier to the selected adapter;
4. rejects declined or missing minimum scopes;
5. reads authoritative provider account metadata and capabilities;
6. encrypts the OAuth Grant and persists it with the Social Account and audit event in one
   transaction; and
7. expires the browser ceremony cookie on every success or failure response.

A consumed or expired ceremony cannot be replayed even if a caller restores an old cookie.
Authorization codes are held only as function arguments during the exchange and are never stored.

## 5. Persistence and tenant isolation

Migration `0019_social_accounts.py` creates:

### `social_oauth_ceremonies`

- UUID identity, Workspace, actor, provider, SHA-256 state hash, redirect URI, requested scopes,
  created/expiry/consumed timestamps.
- Unique state hash and composite Workspace identity.
- API runtime can create, read, and consume its own tenant-scoped ceremonies; workers have no need
  for ceremony access.

### `social_accounts`

- UUID identity, Workspace, provider, external account ID, display name, optional avatar/account
  type, login family, API version, status, JSON capability snapshot, authorizing User,
  last-validated/created/revoked timestamps.
- Unique `(workspace_id, provider, external_account_id)` prevents two active representations of the
  same provider destination in one Workspace.
- Provider metadata is validated into bounded domain values before persistence; raw responses are
  never stored.

### `oauth_grants`

- UUID identity, Workspace, Social Account composite reference, granted scopes, access/refresh
  expiry, `token_version`, key version/reference, wrapped data key, nonce, ciphertext,
  last-refreshed/reconnect/revoked timestamps.
- One grant row per Social Account. Rotation updates the existing encrypted material and increments
  `token_version`; revocation cryptographically erases ciphertext and wrapped-key material.
- API and publishing workers receive only the minimum grants necessary. Ordinary account listing
  never joins or returns encrypted columns.

The existing append-only `audit_events` table records connection, refresh, reconnect-required, and
disconnect actions with safe before/after status, actor, target, request ID, and timestamp. It never
contains scopes encoded with credentials, provider payloads, or diagnostic text.

## 6. Envelope encryption and leases

`social_accounts/secrets.py` uses one random data key per OAuth Grant. AES-GCM encrypts the grant
document with authenticated context containing Workspace, Social Account, OAuth Grant, provider,
and token version. A configured wrapping-key backend wraps the data key and records an opaque key
reference/version.

The local implementation derives a social-OAuth-specific wrapping key from
`CLIPAH_SECRET_ENCRYPTION_KEY`. The interface admits a managed KMS implementation later. Missing,
unknown, disabled, or unavailable wrapping keys fail closed: no connection or lease is created and
no old ciphertext is overwritten.

Rotation decrypts with the stored key version and re-encrypts under the current key version.
Refresh locks the grant row, leases the current material, calls the adapter outside general caches,
then conditionally updates where `token_version` still equals the observed version. A rotated
refresh token and the incremented version land atomically. A stale refresh result is discarded and
the winner is re-read; replay or provider revocation moves the account to `reconnect_required`.

Lease lifetime is checked against an injected clock. Plaintext lives in mutable byte buffers, is
decoded only inside the context manager, and is overwritten at exit. `repr`, `str`, errors, and
public values expose identifiers and expiry only.

## 7. Repository and use cases

`repository.py` owns all SQLAlchemy statements, including tenant filters, row locks, duplicate
resolution, optimistic token-version updates, tombstone reads, and audit inserts. `use_cases.py`
coordinates provider calls and transactions without importing FastAPI or provider SDK types.

Connection behavior:

- A first connection inserts one account and one encrypted grant atomically.
- A callback for an already-active `(Workspace, provider, external account)` replaces the grant
  only after successful validation and records a reconnect audit event instead of duplicating the
  account.
- The same external provider account may be connected independently to different Workspaces.
- Capability reads return the durable safe snapshot; explicit refresh validates live provider data
  and replaces the snapshot/version.

Disconnect behavior:

1. lock the Social Account and mark it unavailable immediately;
2. invoke a narrow `FuturePublicationCoordinator` port to pause or cancel eligible unpublished
   Publications;
3. lease the grant only long enough to attempt provider revocation;
4. erase local encrypted material whether provider revocation succeeds or is already complete;
5. retain the redacted Social Account tombstone and append an audit event; and
6. return success on repeat disconnect calls.

Task 37 supplies the durable Publication implementation of the coordinator. Until Publication rows
exist, Task 36 wires the empty implementation and contract-tests the call boundary.

## 8. HTTP API and errors

`api/routes/social_accounts.py` adds:

- `GET /api/v1/workspaces/{workspace_id}/social-accounts`
- `POST /api/v1/workspaces/{workspace_id}/social-accounts/{provider}/connect`
- `GET /api/v1/social-oauth/{provider}/callback`
- `POST /api/v1/social-accounts/{social_account_id}/refresh`
- `DELETE /api/v1/social-accounts/{social_account_id}`
- `GET /api/v1/social-accounts/{social_account_id}/capabilities`

Mutation routes require same-origin/double-submit CSRF, owner/admin authority, and recent
authentication. The external OAuth callback validates its own one-time state binding and does not
require the double-submit header. Guessed cross-Workspace UUIDs return the same `NOT_FOUND` envelope
as missing UUIDs.

The API exposes provider, display metadata, connection status, granted scope names, capability
version/snapshot, authorizing actor, expiry, validation, creation, and revocation timestamps. It
never exposes grant IDs, secret references, key versions, ciphertext, token versions, codes, tokens,
or provider errors.

Stable sanitized errors distinguish unavailable configuration, invalid/expired/replayed OAuth,
missing scopes, duplicate conflicts that cannot converge, reconnect-required grants, and temporary
provider failures. Exception chaining may preserve an internal cause, but public messages and log
records contain only fixed codes and safe identifiers.

## 9. Configuration

Add one exact redirect URI per provider:

- `CLIPAH_YOUTUBE_OAUTH_REDIRECT_URI`
- `CLIPAH_INSTAGRAM_OAUTH_REDIRECT_URI`
- `CLIPAH_TIKTOK_OAUTH_REDIRECT_URI`

An enabled provider requires its client identifier, client secret, redirect URI, supported API
version, global social-publishing flag, and encryption backend. Redirect URIs must be absolute HTTPS
outside local/test profiles, contain no fragment or userinfo, and match the provider callback path.
Disabled providers behave like absent routes. Existing Google OIDC settings remain unrelated.

## 10. Test strategy

Tests are written and observed failing before production behavior is added.

`tests/integration/test_social_accounts.py` covers:

- owner/admin mutation permission, viewer/reviewer/editor refusal, and recent-auth enforcement;
- Workspace-scoped listing/get/capability/disconnect and guessed-ID indistinguishability;
- state binding, PKCE S256, exact redirect URI, expiry, durable callback replay refusal;
- exact minimum-scope requests and declined/missing-scope refusal for all providers;
- same provider account in two Workspaces, duplicate convergence within one Workspace, and no
  linking from equal email/provider names across credential families;
- authoritative metadata, redacted listing, capability refresh, serialized token refresh,
  optimistic version conflict, reconnect-required state, key rotation, and fail-closed KMS;
- immediate/idempotent disconnect, publication-coordinator invocation, provider revocation attempt,
  local cryptographic erasure, and append-only audit history; and
- migration upgrade/downgrade, grants, RLS, composite foreign keys, and drift.

`tests/security/test_oauth_grants.py` plants unique canaries in access tokens, refresh tokens, client
secrets, authorization codes, and decrypted grant material, then scans API bodies/headers, database
plaintext and JSON columns, captured logs, exceptions, analytics/audit rows, general caches, and job
arguments. Ciphertext must not contain recognizable canaries, and leases must clear their buffers.

The final verification runs Ruff lint, Ruff formatting, strict mypy, the focused integration and
security suites, the complete pytest suite with at least 90% coverage, migration downgrade/upgrade
and drift checks, and `git diff --check`.

## 11. Acceptance boundary

Task 36 is complete when every checkbox in its `plan.md` entry is covered by a passing test, all
backend gates pass, the provider-neutral interfaces are usable by Tasks 37 and 39-41, and
`PROGRESS.md` records the result and deliberate Publication-coordinator deferral. Production network
publishing, frontend connection management, and durable Publication cancellation remain owned by
their later numbered tasks.
