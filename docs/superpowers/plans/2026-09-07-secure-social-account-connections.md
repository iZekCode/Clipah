# Secure Social Account Connections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This repository's owner requires inline execution without subagents.

**Goal:** Add Workspace-scoped YouTube, Instagram, and TikTok Social Account connections whose OAuth Grants are encrypted, redacted, refresh-safe, revocable, and separate from every login and source-import credential.

**Architecture:** A provider-neutral OAuth ceremony validates exact redirect URIs, state, PKCE S256, minimum scopes, authoritative account metadata, and one-time callback consumption. SQLAlchemy repositories persist safe Social Account metadata separately from envelope-encrypted OAuth Grants; use cases expose bounded grant leases and coordinate duplicate connection, capability refresh, token rotation, disconnect, revocation, and audit history. FastAPI routes remain thin and use the existing Workspace authorization, CSRF, recent-authentication, and sanitized-error boundaries.

**Tech Stack:** Python 3.13, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, PostgreSQL RLS, Authlib/HTTPX-compatible provider protocols, cryptography AES-GCM/HKDF, pytest, Ruff, and strict mypy.

**Spec:** `docs/superpowers/specs/2026-09-07-secure-social-account-connections-design.md`

## Global Constraints

- Follow `AGENTS.md`, `plan.md`, and `CONTEXT.md`; Task 36 changes only the rebuild backend and its documentation.
- The agent never commits, pushes, rebases, or writes Git history. The owner commit message is `feat: add secure social account connections`.
- Do not use subagents.
- Every production behavior begins with a focused failing test whose expected failure is observed.
- Every tenant row carries `workspace_id`; tenant child references use composite Workspace foreign keys and RLS.
- Social Account mutations require `WorkspaceAction.SOCIAL_CONNECTION_MANAGE`, CSRF, and recent authentication. Reads require a live Workspace membership.
- Guessed and missing Workspace/Social Account identifiers return identical public `404` envelopes.
- Login Identity, Session, Source Connection, Social Account, and OAuth Grant never auto-link, even when provider names or email-like metadata match.
- Access/refresh tokens, client secrets, authorization codes, decrypted grants, and raw provider diagnostics are forbidden from logs, errors, API responses, jobs, analytics/audit metadata, general caches, and plaintext database columns.
- Initial scopes are exactly YouTube `youtube.readonly` plus `youtube.upload`, Instagram `instagram_business_basic` plus `instagram_business_content_publish`, and TikTok `user.info.basic` plus `video.upload`.
- TikTok Direct Post `video.publish`, provider publishing HTTP adapters, frontend UI, durable Publications, and provider network smoke tests remain in later tasks.
- Backend completion requires `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`, and `uv run pytest -q --cov=clipah --cov-fail-under=90` from `backend/`.

---

### Task 1: Define the provider-neutral OAuth and secret boundaries

**Files:**
- Create: `backend/src/clipah/social_accounts/__init__.py`
- Create: `backend/src/clipah/social_accounts/models.py`
- Create: `backend/src/clipah/social_accounts/oauth.py`
- Create: `backend/src/clipah/social_accounts/secrets.py`
- Create: `backend/tests/security/test_oauth_grants.py`

**Interfaces:**
- Produces: `SocialProvider`, `SocialConnectionStatus`, `SocialAccountIdentity`, `PublishingCapabilities`, `OAuthGrantMaterial`, `OAuthConnectionResult`, `RefreshedGrant`, `ProviderPolicy`, `PendingSocialAuthorization`, `SocialOAuthProvider`, `OAuthGrantLease`, `SocialSecretStore`, and a local envelope-encryption implementation.
- Consumes: injected clocks and randomness, provider client registrations, and configured wrapping-key material.

- [x] **Step 1: Write failing model, OAuth-policy, and lease tests.**

  Add security tests proving closed provider/status values, bounded account/capability metadata,
  exact scope sets, exact redirect URI propagation, 256-bit state, PKCE S256, constant-time state
  validation, ceremony expiry, redacted representations, lease expiry, one-entry bounded use, and
  byte-buffer clearing. The wished-for API is:

  ```python
  policy = provider_policy(
      SocialProvider.TIKTOK,
      client_id="tiktok-client",
      redirect_uri="https://clipah.test/api/v1/social-oauth/tiktok/callback",
      api_version="v2",
  )
  pending = begin_authorization(policy=policy, now=NOW, random_bytes=lambda size: b"s" * size)
  assert pending.requested_scopes == frozenset({"user.info.basic", "video.upload"})
  assert pending.code_challenge_method == "S256"
  assert pending.redirect_uri == policy.redirect_uri
  ```

  The test names identify the production behavior that would make each assertion fail; provider
  doubles record calls but assertions target the ceremony/result, not mock call counts alone.

- [x] **Step 2: Run the security file and observe RED.**

  ```bash
  uv run pytest -q tests/security/test_oauth_grants.py
  ```

  Expected: collection fails because `clipah.social_accounts` does not exist.

- [x] **Step 3: Implement immutable domain values and the OAuth protocol.**

  Use frozen dataclasses/Pydantic models with bounded strings and JSON-safe capabilities. Keep
  provider SDK/payload types outside the domain. Define:

  ```python
  class SocialOAuthProvider(Protocol):
      def authorization_endpoint(self) -> str: ...
      def exchange_code(self, *, code: str, code_verifier: str,
                        redirect_uri: str) -> OAuthTokenResult: ...
      def account_identity(self, *, grant: OAuthGrantMaterial) -> SocialAccountIdentity: ...
      def capabilities(self, *, grant: OAuthGrantMaterial) -> PublishingCapabilities: ...
      def refresh(self, *, grant: OAuthGrantMaterial) -> RefreshedGrant: ...
      def revoke(self, *, grant: OAuthGrantMaterial) -> None: ...
  ```

  `begin_authorization` generates state/verifier, derives URL-safe SHA-256 challenge without
  padding, and constructs only fixed provider query keys. `complete_authorization` validates
  provider, state, expiry, redirect URI, and minimum scopes before returning normalized values.

- [x] **Step 4: Implement social-specific envelope encryption and leases.**

  Define authenticated context and ciphertext shapes:

  ```python
  @dataclass(frozen=True, slots=True)
  class OAuthSecretContext:
      workspace_id: UUID
      social_account_id: UUID
      grant_id: UUID
      provider: SocialProvider
      token_version: int

  class SocialSecretStore(Protocol):
      def encrypt(self, plaintext: bytes, *, context: OAuthSecretContext) -> EncryptedOAuthGrant: ...
      def decrypt(self, secret: EncryptedOAuthGrant, *, context: OAuthSecretContext) -> bytearray: ...
  ```

  Use a fresh data key and nonce per encryption, AES-GCM authenticated context, an independently
  derived wrapping-key label, and key-reference/version metadata. `OAuthGrantLease` is a context
  manager that refuses expiry/re-entry/discarded use and overwrites its bytearray on exit. No
  secret-bearing exception interpolates input or ciphertext.

- [x] **Step 5: Run the focused security tests until GREEN.**

  ```bash
  uv run pytest -q tests/security/test_oauth_grants.py
  ```

---

### Task 2: Add fail-closed provider configuration

**Files:**
- Modify: `backend/src/clipah/config.py`
- Modify: `backend/tests/unit/test_config.py`
- Modify: `backend/tests/security/test_oauth_grants.py`

**Interfaces:**
- Produces: exact per-provider OAuth redirect URI settings and validated provider registrations.
- Consumes: existing provider enable flags, client IDs/secrets, API versions, global social flag, and encryption settings.

- [x] **Step 1: Write failing configuration tests.**

  Cover each enabled provider requiring its client identifier, client secret, exact redirect URI,
  supported API version, global social publishing flag, and encryption backend. Reject redirect
  URIs with fragments, userinfo, traversal, mismatched callback provider, non-HTTPS production
  schemes, or query strings. Prove disabled integrations may remain absent and Google Login settings
  never satisfy YouTube publishing settings.

- [x] **Step 2: Run configuration tests and observe RED.**

  ```bash
  uv run pytest -q tests/unit/test_config.py tests/security/test_oauth_grants.py
  ```

  Expected: failures because the three redirect URI fields and callback validation do not exist.

- [x] **Step 3: Add and validate exact redirect URI settings.**

  Add:

  ```python
  youtube_oauth_redirect_uri: str | None = None
  instagram_oauth_redirect_uri: str | None = None
  tiktok_oauth_redirect_uri: str | None = None
  ```

  Extend `_validate_enabled_social_providers` to require complete provider registrations only when
  both the global and provider flags allow the integration. Validate an absolute callback URI whose
  path is exactly `/api/v1/social-oauth/{provider}/callback`; permit HTTP only in local/test.
  Pydantic error rendering must continue hiding SecretStr input.

- [x] **Step 4: Run focused configuration/security tests until GREEN.**

  ```bash
  uv run pytest -q tests/unit/test_config.py tests/security/test_oauth_grants.py
  ```

---

### Task 3: Create the tenant-safe social schema and ORM mapping

**Files:**
- Create: `backend/migrations/versions/0019_social_accounts.py`
- Modify: `backend/src/clipah/models.py`
- Create: `backend/tests/integration/test_social_accounts.py`
- Modify: `backend/tests/integration/test_schema.py`

**Interfaces:**
- Produces: `SocialOAuthCeremony`, `SocialAccount`, and `OAuthGrant` ORM rows plus matching PostgreSQL enum types, constraints, indexes, grants, and RLS policies.
- Consumes: existing `Workspace`, `User`, `audit_events`, runtime roles, tenant GUC policy, and ORM naming conventions.

- [x] **Step 1: Write schema tests before migration/model code.**

  Tests upgrade from `0018` to `0019`, inspect literal column/constraint/index/grant names, insert
  tenant-correct rows, and reject copied grants/accounts across Workspaces, duplicate provider
  identities within a Workspace, two grants for one account, non-positive token versions, invalid
  ceremony lifetimes, and reused state hashes. Prove RLS default-deny and that account-list reads do
  not require secret-column access.

  ```python
  EXPECTED_TABLES = {"social_oauth_ceremonies", "social_accounts", "oauth_grants"}
  assert EXPECTED_TABLES <= set(inspect(connection).get_table_names())
  assert _rls_forced(connection, "oauth_grants")
  ```

- [x] **Step 2: Run focused schema tests and observe RED.**

  ```bash
  uv run pytest -q tests/integration/test_social_accounts.py tests/integration/test_schema.py
  ```

  Expected: missing revision/table/model failures.

- [x] **Step 3: Implement the additive migration and ORM models.**

  Persist only hashed state in ceremonies. Store account metadata/capability JSON independently
  from grant ciphertext columns. Use `(workspace_id, id)` unique keys and composite Workspace
  foreign keys. Grant API runtime only the operations needed for ceremony/account management;
  publishing workers may select/update grants and accounts but never ceremonies. Revoke PUBLIC,
  enable and force RLS on all three tables, and make `downgrade()` remove only Task 36 objects in
  dependency order.

- [x] **Step 4: Verify upgrade, constraints, downgrade, and re-upgrade are GREEN.**

  ```bash
  uv run pytest -q tests/integration/test_social_accounts.py tests/integration/test_schema.py
  uv run alembic downgrade 0018
  uv run alembic upgrade head
  ```

---

### Task 4: Implement repository-backed ceremonies and connection completion

**Files:**
- Create: `backend/src/clipah/social_accounts/repository.py`
- Create: `backend/src/clipah/social_accounts/use_cases.py`
- Modify: `backend/tests/integration/test_social_accounts.py`
- Modify: `backend/tests/security/test_oauth_grants.py`

**Interfaces:**
- Produces: `SocialAccountRepository`, `SocialAccountService.connect`, ceremony creation/consumption, duplicate convergence, listing, capability reads, grant encryption, and safe audit events.
- Consumes: `WorkspaceAccess`, `ProviderPolicy`, `SocialOAuthProvider`, `SocialSecretStore`, ORM rows, and injected time/randomness.

- [x] **Step 1: Write failing service integration tests.**

  Cover single-use state hash persistence, callback replay under concurrent/repeated use, exact
  redirect URI and verifier passed to exchange, missing/declined scopes, authoritative account
  metadata, one atomic account/grant/audit transaction, duplicate convergence, same external account
  in two Workspaces, stable list order, capability snapshot retrieval, and cross-Workspace IDs.

  Prove credential-family separation by creating equal Google login email, YouTube display name,
  Source Connection label, Instagram/TikTok metadata, and external IDs where types allow; assert no
  identity/session/source row is updated and each Social Account has only its explicit OAuth Grant.

- [x] **Step 2: Run the service tests and observe RED.**

  ```bash
  uv run pytest -q tests/integration/test_social_accounts.py tests/security/test_oauth_grants.py
  ```

  Expected: repository/service symbols or behaviors are missing.

- [x] **Step 3: Implement repository statements and stable errors.**

  Repository methods always accept `workspace_id`, use exact tenant predicates, and expose row-lock
  variants explicitly:

  ```python
  def consume_ceremony(self, *, workspace_id: UUID, ceremony_id: UUID,
                       state_hash: bytes, now: datetime) -> SocialOAuthCeremony: ...
  def account_for_update(self, *, workspace_id: UUID,
                         social_account_id: UUID) -> SocialAccount | None: ...
  def active_grant_for_update(self, *, workspace_id: UUID,
                              social_account_id: UUID) -> OAuthGrant | None: ...
  ```

  Errors carry fixed codes only: not found, unusable ceremony, missing scope, duplicate conflict,
  reconnect required, provider unavailable, or secret backend unavailable.

- [x] **Step 4: Implement `SocialAccountService.connect/list/get_capabilities`.**

  `connect` consumes the ceremony before exchange, validates normalized metadata/scopes, encrypts a
  canonical bounded JSON grant document, and then inserts or reconnects the unique account. A
  reconnect replaces encrypted material only after every provider and encryption step succeeds.
  Listing queries Social Accounts only and returns `SocialAccountSummary`; capability reads validate
  standing/account status and deserialize only the bounded safe snapshot.

- [x] **Step 5: Run focused service/security tests until GREEN.**

  ```bash
  uv run pytest -q tests/integration/test_social_accounts.py tests/security/test_oauth_grants.py
  ```

---

### Task 5: Add bounded leasing, serialized refresh, and key rotation

**Files:**
- Modify: `backend/src/clipah/social_accounts/repository.py`
- Modify: `backend/src/clipah/social_accounts/secrets.py`
- Modify: `backend/src/clipah/social_accounts/use_cases.py`
- Modify: `backend/tests/integration/test_social_accounts.py`
- Modify: `backend/tests/security/test_oauth_grants.py`

**Interfaces:**
- Produces: `SocialAccountService.lease`, `SocialAccountService.refresh`, optimistic `token_version` replacement, and wrapping-key rotation.
- Consumes: locked active grant rows, `SocialOAuthProvider.refresh`, injected clock, and `SocialSecretStore` key-reference/version support.

- [x] **Step 1: Write failing lease and refresh race tests.**

  Cover active/expired/revoked/missing grants, Workspace mismatch, token expiry, one bounded adapter
  operation, lease clearing on success/exception, two refresh callers observing one serialized
  rotation, stale `token_version` refusal, refresh-token rotation, preserved old refresh token when
  omitted by the provider, access/refresh expiry updates, provider replay/revocation mapping, and
  rewrap under a new key version without changing account identity.

- [x] **Step 2: Run focused tests and observe RED.**

  ```bash
  uv run pytest -q tests/integration/test_social_accounts.py tests/security/test_oauth_grants.py -k 'lease or refresh or rotation or version'
  ```

- [x] **Step 3: Implement bounded lease and optimistic encrypted replacement.**

  Lock the grant for refresh, decrypt into a lease, call the selected adapter only inside the lease
  context, construct a new canonical grant document, encrypt it using `token_version + 1`, and issue
  an update guarded by the old version. Never place grant material on the service object or in a
  cache. If the guarded update loses, discard the stale result and return the durable winner's safe
  summary.

- [x] **Step 4: Implement reconnect-required and key-rotation behavior.**

  Normalized invalid-grant/revocation errors set account status to `reconnect_required`, preserve a
  fixed reconnect code, cryptographically erase unusable ciphertext, and append a safe audit event.
  A wrapping-key rotation decrypts with the stored key reference/version and encrypts under the
  current key; an unavailable old/new key leaves the durable grant unchanged and denies the lease.

- [x] **Step 5: Run focused and complete social-account tests until GREEN.**

  ```bash
  uv run pytest -q tests/integration/test_social_accounts.py tests/security/test_oauth_grants.py
  ```

---

### Task 6: Implement idempotent disconnect, revocation, and audit tombstones

**Files:**
- Modify: `backend/src/clipah/social_accounts/models.py`
- Modify: `backend/src/clipah/social_accounts/repository.py`
- Modify: `backend/src/clipah/social_accounts/use_cases.py`
- Modify: `backend/tests/integration/test_social_accounts.py`
- Modify: `backend/tests/security/test_oauth_grants.py`

**Interfaces:**
- Produces: `FuturePublicationCoordinator`, empty Task 36 implementation, `SocialAccountService.disconnect`, provider revocation attempt, local grant erasure, and redacted audit tombstone.
- Consumes: a locked Social Account/grant, provider adapter, injected coordinator, actor/request ID, and current time.

- [x] **Step 1: Write failing disconnect workflow tests.**

  Assert the account becomes unavailable before external revocation, the coordinator receives only
  Workspace/Social Account UUIDs, provider revocation receives grant material only inside a lease,
  local encrypted material is erased on provider success/failure/already-revoked responses, the
  Social Account tombstone remains listable as revoked, repeat disconnect succeeds without another
  side effect, and audit history records actor/status transitions without secret fields.

- [x] **Step 2: Run disconnect/security tests and observe RED.**

  ```bash
  uv run pytest -q tests/integration/test_social_accounts.py tests/security/test_oauth_grants.py -k 'disconnect or revoke or audit'
  ```

- [x] **Step 3: Implement the disconnect workflow.**

  Define:

  ```python
  class FuturePublicationCoordinator(Protocol):
      def cancel_or_pause_unpublished(self, *, workspace_id: UUID,
                                      social_account_id: UUID, now: datetime) -> None: ...
  ```

  Lock and mark the account revoked first, flush, invoke the coordinator, attempt provider
  revocation using a bounded lease, then overwrite/delete reusable encrypted material and append the
  audit event. Normalize provider failure without restoring account availability. A row already
  revoked returns success after verifying the Workspace identity.

- [x] **Step 4: Run all social-account integration/security tests until GREEN.**

  ```bash
  uv run pytest -q tests/integration/test_social_accounts.py tests/security/test_oauth_grants.py
  ```

---

### Task 7: Expose the secured HTTP API and composition-root wiring

**Files:**
- Create: `backend/src/clipah/api/routes/social_accounts.py`
- Modify: `backend/src/clipah/api/app.py`
- Modify: `backend/src/clipah/api/errors.py`
- Modify: `backend/tests/harness.py`
- Modify: `backend/tests/integration/test_social_accounts.py`
- Modify: `backend/tests/security/test_oauth_grants.py`

**Interfaces:**
- Produces: Task 36's six public endpoints, sealed social ceremony cookie, redacted response models, injected provider registry/secret store/coordinator, and stable error mappings.
- Consumes: existing `require_workspace`, `require_csrf`, auth clock/session, request IDs, Settings, and service methods.

- [x] **Step 1: Write failing route tests for the complete public contract.**

  Exercise start redirect/cookie flags, callback state and replay, CSRF, recent authentication,
  literal role matrix, disabled global/provider flags, list/get/capability/refresh/disconnect responses,
  provider mismatch, guessed identifiers, callback cookie deletion on success/refusal, exact public
  error envelopes, and absence of tokens/codes/secret metadata in response bodies and headers.

  Routes are exactly:

  ```text
  GET    /api/v1/workspaces/{workspace_id}/social-accounts
  POST   /api/v1/workspaces/{workspace_id}/social-accounts/{provider}/connect
  GET    /api/v1/social-oauth/{provider}/callback
  POST   /api/v1/social-accounts/{social_account_id}/refresh?workspace_id={workspace_id}
  DELETE /api/v1/social-accounts/{social_account_id}?workspace_id={workspace_id}
  GET    /api/v1/social-accounts/{social_account_id}/capabilities?workspace_id={workspace_id}
  ```

- [x] **Step 2: Run route tests and observe RED.**

  ```bash
  uv run pytest -q tests/integration/test_social_accounts.py tests/security/test_oauth_grants.py
  ```

  Expected: missing router/composition-root wiring and endpoint failures.

- [x] **Step 3: Add route models and authorization/error translation.**

  Responses expose safe account identity/display/status, granted scope names, capability snapshot,
  authorizing actor, and lifecycle timestamps only. Mutation routes use
  `WorkspaceAction.SOCIAL_CONNECTION_MANAGE` plus CSRF/recent auth. Callback authority comes from the
  consumed ceremony's actor and Workspace, not a caller-supplied query parameter. Map all domain
  refusals to fixed `ApiError` codes/messages.

- [x] **Step 4: Wire injectable social dependencies into the application and harness.**

  Add a typed registry mapping enabled `SocialProvider` values to deterministic/production-shaped
  `SocialOAuthProvider` implementations, a `SocialSecretStore`, and a
  `FuturePublicationCoordinator`. Store those dependencies on application state through typed
  accessors rather than module globals. Register the router and derive a distinct encrypted cookie
  name/key context from the Session secret.

- [x] **Step 5: Run route, auth, Workspace, and source-connection regressions until GREEN.**

  ```bash
  uv run pytest -q tests/integration/test_social_accounts.py tests/security/test_oauth_grants.py tests/integration/test_auth.py tests/integration/test_workspace_memberships.py tests/integration/test_source_connections.py
  ```

---

### Task 8: Complete canary scans, migration verification, and handoff

**Files:**
- Modify: `backend/tests/security/test_oauth_grants.py`
- Modify: `backend/tests/integration/test_social_accounts.py`
- Modify: `backend/tests/integration/test_schema.py`
- Modify: `backend/src/clipah/api/errors.py`
- Modify: `PROGRESS.md`

**Interfaces:**
- Produces: complete Task 36 acceptance evidence and handoff metadata.
- Consumes: all Task 36 behavior and the four repository quality gates.

- [x] **Step 1: Finish whole-system credential-family and secret-canary tests.**

  Plant distinct canaries for access token, refresh token, authorization code, each provider client
  secret, and decrypted material. Drive connect/list/capability/refresh/disconnect and failure paths,
  then scan API bodies/headers, every database plaintext/JSON value, captured logs, raised
  exceptions, audit/provider-usage rows, job arguments/events, and configured general cache doubles.
  Assert canaries occur only in ephemeral test inputs and authenticated ciphertext decryptions.

- [x] **Step 2: Run focused Task 36 suites and resolve every failure.**

  ```bash
  uv run pytest -q tests/integration/test_social_accounts.py tests/security/test_oauth_grants.py tests/unit/test_config.py tests/integration/test_schema.py
  ```

- [x] **Step 3: Verify migration downgrade, upgrade, and drift.**

  ```bash
  uv run alembic downgrade 0018
  uv run alembic upgrade head
  uv run alembic check
  ```

- [x] **Step 4: Run all four backend gates.**

  ```bash
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy src
  uv run pytest -q --cov=clipah --cov-fail-under=90
  ```

- [x] **Step 5: Run final repository checks and update progress.**

  Run `git diff --check`, inspect `git status --short`, and update `PROGRESS.md` to mark Task 36
  complete awaiting the owner's commit. Record exact gate results and the deliberate deferral that
  Task 37 supplies durable Publication cancellation behind `FuturePublicationCoordinator`.

- [x] **Step 6: Hand off without committing.**

  Report the implementation summary, exact focused/full gate outputs, any explicitly skipped
  environment-gated tests, migration verification, changed-file status, and owner commit message:

  ```text
  feat: add secure social account connections
  ```
