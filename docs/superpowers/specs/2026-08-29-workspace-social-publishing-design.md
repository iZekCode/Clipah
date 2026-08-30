# Workspace Accounts and Social Publishing Design

**Status:** Draft for user review

**Date:** 2026-08-29

**Research:**

- `research/2026-08-29-social-publishing-apis.md`
- `research/2026-08-29-clipah-platform-options.md`

## 1. Purpose

Clipah will use a workspace-first account model and allow an authorized user to publish an approved immutable clip to YouTube Shorts, Instagram Reels, and TikTok. The product supports both immediate publishing and scheduled publishing. TikTok Upload-to-draft is the launch fallback until Clipah passes the platform review required for unrestricted Direct Post; Direct Post remains the target capability.

The publishing subsystem preserves each platform's live rules instead of pretending the platforms share one identical post model. A multi-destination confirmation creates independent publications, so one provider's failure cannot roll back or duplicate another provider's successful post.

## 2. Goals

1. Make Workspace the durable tenant boundary for projects, assets, quotas, brand data, social connections, and publications.
2. Separate human identity, Clipah sessions, external social accounts, and provider OAuth credentials.
3. Publish one explicitly approved Render Artifact to one or more explicitly selected Social Accounts.
4. Support `publish now` and scheduled publishing with provider-correct scheduling behavior.
5. Make transfer resumable, retryable, idempotent, observable, and safe after ambiguous provider responses.
6. Preserve an audit trail of the artifact, destination, metadata, privacy, disclosures, actor, consent, and provider result.
7. Support partial success without compensating deletion of already-published posts.
8. Provide secure token refresh, revocation, disconnect, and reconnect flows.
9. Keep every provider behind a typed adapter and deterministic fake for tests.

## 3. Non-goals

- Automatic posting without a recorded user approval.
- Scraping or browser automation as a substitute for official publishing APIs.
- Social inbox, comment moderation, or community management.
- Advertising campaign creation, paid boosting, shopping tags, or branded-content partner management.
- Automatic deletion of provider posts when Clipah data is deleted.
- Treating a publishing OAuth grant as authorization to download or modify arbitrary source media.
- Atomic publish-to-all semantics; external platforms cannot support a real distributed transaction.
- Native social-platform music pickers or editing tools. Published audio is already embedded lawfully in the Render Artifact.

## 4. Canonical domain model

The canonical terms are defined in the repository-root `CONTEXT.md`.

### 4.1 Ownership boundary

Every new User receives a personal Workspace. A User may join team Workspaces through Workspace Memberships. Projects and reusable product data belong to a Workspace, never directly to a User.

The migration changes these ownership fields:

| Existing plan field | Approved replacement |
| --- | --- |
| `projects.owner_id` | `projects.workspace_id`, plus `created_by_user_id` |
| `clip_edits.owner_id` | Workspace derived through Project, plus `created_by_user_id` |
| `brand_kits.owner_id` | `brand_kits.workspace_id` |
| `templates.owner_id` | `templates.workspace_id` |
| `project_members` | `workspace_memberships`; project-specific overrides are excluded from the initial release |
| `source_connections.owner_id` | `source_connections.workspace_id`, plus `authorized_by_user_id` |
| `users/{user_id}/projects/...` storage keys | `workspaces/{workspace_id}/projects/...` |

### 4.2 Roles

Workspace roles are:

- `owner`: manages ownership, membership, deletion, social connections, quotas, and all project operations.
- `admin`: manages membership except ownership transfer, manages social connections, and performs all content operations.
- `editor`: creates and edits projects, accepts B-roll, renders, prepares publications, and publishes when the Workspace policy permits.
- `reviewer`: reads projects, comments, requests changes, and approves Edit Revisions; cannot mutate compositions or publish.
- `viewer`: read-only access to permitted Workspace content.

The initial Workspace publishing policy is `owner_admin_editor`. An owner may restrict it to `owner_admin` without changing membership roles.

The final owner cannot leave or delete their User until they transfer ownership or delete the Workspace. Removing a member invalidates their Sessions' Workspace authorization immediately but retains tombstoned actor references in audit records.

## 5. Relational data model

Postgres is the only durable source of truth. Redis is limited to queues, transient event wakeups, distributed locks, and rate-limit counters.

### 5.1 Identity and tenancy tables

**`users`**

- `id uuid primary key`
- `primary_email citext not null`
- `display_name text not null`
- `avatar_url text null`
- `status user_status not null`
- `created_at timestamptz not null`
- `disabled_at timestamptz null`
- `deleted_at timestamptz null`

Email is contact/profile data and is not an authentication key.

**`auth_identities`**

- `id uuid primary key`
- `user_id uuid not null references users(id)`
- `provider text not null`
- `issuer text not null`
- `subject text not null`
- `email_at_provider citext null`
- `email_verified boolean not null`
- `created_at timestamptz not null`
- `last_login_at timestamptz null`
- unique `(issuer, subject)`

Identity linking requires an authenticated User and a fresh authorization ceremony. Equal email addresses never auto-link identities.

**`auth_sessions`**

- `id uuid primary key`
- `user_id uuid not null references users(id)`
- `token_hash bytea not null unique`
- `created_at timestamptz not null`
- `last_seen_at timestamptz not null`
- `idle_expires_at timestamptz not null`
- `absolute_expires_at timestamptz not null`
- `recent_auth_at timestamptz not null`
- `revoked_at timestamptz null`
- `ip_hash bytea null`
- `user_agent_summary text null`

**`workspaces`**

- `id uuid primary key`
- `name text not null`
- `slug citext not null unique`
- `kind workspace_kind not null`
- `publishing_role_policy publishing_role_policy not null default 'owner_admin_editor'`
- `status workspace_status not null`
- `created_at timestamptz not null`
- `deleted_at timestamptz null`

**`workspace_memberships`**

- `workspace_id uuid not null references workspaces(id)`
- `user_id uuid not null references users(id)`
- `role workspace_role not null`
- `invited_by_user_id uuid null references users(id)`
- `joined_at timestamptz not null`
- `removed_at timestamptz null`
- primary key `(workspace_id, user_id)`

Ownership-changing use cases lock the Workspace and its active owner memberships in one transaction and reject a change that would leave zero active owners.

**`workspace_invites`**

- Hashed opaque token, intended email, role, creator, expiry, acceptance, and revocation timestamps.
- Acceptance requires an authenticated User; the token is single-use and expires after seven days.

### 5.2 Tenant integrity

Every Workspace-owned table carries `workspace_id not null`, including Projects, Assets, Jobs, Transcripts, Clip Candidates, Edits, Render Artifacts, Brand Kits, Templates, Social Accounts, Publications, Usage, and Audit Events.

Cross-tenant relationships use composite keys. For example, an Edit Revision cannot reference an Asset unless both rows have the same Workspace ID. Tenant indexes begin with `workspace_id`.

Application authorization is the primary control. Composite foreign keys are the integrity control. Postgres Row-Level Security is defense-in-depth on tenant and credential-metadata tables. API and worker roles do not own tables and do not have `BYPASSRLS`; the migration role is separate.

### 5.3 Social publishing tables

**`social_accounts`**

- `id uuid primary key`
- `workspace_id uuid not null`
- `provider social_provider not null`
- `external_account_id text not null`
- `display_name text not null`
- `avatar_url text null`
- `account_type text null`
- `login_family text not null`
- `api_version text not null`
- `connection_status social_connection_status not null`
- `capability_snapshot jsonb not null`
- `authorized_by_user_id uuid not null`
- `last_validated_at timestamptz null`
- `created_at timestamptz not null`
- `revoked_at timestamptz null`
- unique `(workspace_id, provider, external_account_id)`

**`oauth_grants`**

- `id uuid primary key`
- `workspace_id uuid not null`
- `social_account_id uuid not null unique`
- `encrypted_secret_ref text not null`
- `granted_scopes text[] not null`
- `access_token_expires_at timestamptz null`
- `refresh_token_expires_at timestamptz null`
- `token_version integer not null`
- `last_refreshed_at timestamptz null`
- `reconnect_reason text null`
- `revoked_at timestamptz null`

The database stores a Secret Manager reference or envelope-encrypted blob, never plaintext tokens. Refresh uses a row/advisory lock and atomically replaces rotating refresh tokens.

**`publication_batches`**

- Groups Publications created by one confirmation action.
- Stores Workspace, approved Edit Revision/Render Artifact, creating actor, and creation timestamp.
- Has no aggregate success that can hide per-destination state; UI derives counts from Publications.

**`publications`**

- `id uuid primary key`
- `workspace_id uuid not null`
- `batch_id uuid not null`
- `social_account_id uuid not null`
- `edit_revision_id uuid not null`
- `render_artifact_id uuid not null`
- `approved_by_user_id uuid not null`
- `metadata_snapshot jsonb not null`
- `provider_options jsonb not null`
- `consent_snapshot jsonb not null`
- `scheduled_for timestamptz null`
- `display_timezone text not null`
- `state publication_state not null`
- `idempotency_key text not null`
- `provider_publication_id text null`
- `provider_permalink text null`
- `checkpoint_metadata jsonb null`
- `encrypted_checkpoint_ref text null`
- `attempt_count integer not null`
- `next_attempt_at timestamptz null`
- `normalized_error_code text null`
- `sanitized_error_message text null`
- timestamps for approval, dispatch, transfer, processing, publication, failure, cancellation
- unique `(workspace_id, social_account_id, idempotency_key)`

**`publication_attempts`**

- Append-only attempt number, stage, request ID, normalized response metadata, byte checkpoint, timing, and sanitized error.
- Raw access tokens, refresh tokens, authorization headers, upload URLs, and signed source URLs are forbidden.

**`provider_events`**

- Append-only webhook/poll reconciliation events.
- Unique provider event ID when supplied; otherwise unique payload hash plus provider account and event type.
- Stores signature validity, receive time, processing time, normalized status, and encrypted/raw-payload retention reference when legally permitted.

**`audit_events`**

- Append-only actor, Workspace, action, target, before/after security-relevant metadata, request ID, and timestamp.
- Records connection, disconnection, scheduling, approval, privacy/disclosure choice, cancellation, retry, publication, membership, ownership, and deletion actions.

## 6. Authentication and session design

Clipah initially uses Google OpenID Connect Authorization Code flow with PKCE S256, state, and nonce. The stable authentication key is `(issuer, subject)`, never email.

The browser receives a random opaque `__Host-clipah_session` cookie with `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, and no Domain attribute. The database stores only SHA-256 of the token. The session rotates after login, identity linking, privilege changes, suspicious activity, and recent-auth actions. State-changing requests require same-origin validation and CSRF protection.

Recent authentication within ten minutes is required for:

- ownership transfer;
- Workspace deletion;
- User deletion;
- membership role elevation to owner/admin;
- connecting or disconnecting a Social Account;
- changing a scheduled Publication's destination or visibility;
- revoking every session.

Social-provider OAuth grants are never Clipah Sessions. Login scopes and publishing scopes are requested in separate authorization ceremonies using least privilege and incremental consent.

## 7. Provider adapter boundary

Each provider implements the following domain interface without leaking SDK types:

```python
class SocialPublisher(Protocol):
    def capabilities(self, *, account: SocialAccount) -> PublishingCapabilities: ...
    def refresh_credentials(self, *, grant: OAuthGrantLease) -> RefreshedGrant: ...
    def preflight(self, *, publication: Publication, artifact: RenderArtifact) -> PreflightResult: ...
    def begin(self, *, publication: Publication, artifact: RenderArtifact) -> PublishCheckpoint: ...
    def transfer(self, *, checkpoint: PublishCheckpoint, artifact: RenderArtifact) -> PublishCheckpoint: ...
    def poll(self, *, checkpoint: PublishCheckpoint) -> ProviderPublicationStatus: ...
    def cancel(self, *, checkpoint: PublishCheckpoint) -> CancelResult: ...
    def disconnect(self, *, account: SocialAccount, grant: OAuthGrantLease) -> None: ...
```

Production adapters are `YouTubePublisher`, `InstagramPublisher`, and `TikTokPublisher`. Each has a deterministic fake and recorded contract fixtures. Unsupported fields remain explicit provider validation errors; adapters never invent cross-platform mappings.

## 8. Publication state machine

```text
draft -> awaiting_approval -> scheduled -> preflighting
draft -> awaiting_approval -> preflighting
preflighting -> transferring -> processing -> published
       |             |             |
       |             |             -> retryable_failed
       |             -> retryable_failed
       -> reconnect_required
       -> permanent_failed

scheduled -> cancelled
retryable_failed -> preflighting
reconnect_required -> scheduled | awaiting_approval
```

Terminal states are `published`, `permanent_failed`, and `cancelled`. A published Publication is never rolled back automatically because another Publication in the same batch failed.

Every transition uses a row lock, validates the current state, appends an event, and commits before the next external operation. Celery tasks receive only the Publication UUID and reload durable state.

## 9. User flow

### 9.1 Connect an account

1. Owner/admin selects YouTube, Instagram, or TikTok from Workspace Settings.
2. Clipah starts the provider authorization with PKCE/state/nonce where supported and the minimum publishing scopes.
3. Callback validates issuer/state/nonce/code binding, fetches authoritative account metadata, encrypts credentials, and stores capability snapshot.
4. The UI shows account, scopes, connection health, authorized-by actor, expiry/reconnect state, and disconnect action.

### 9.2 Prepare a publication

1. User opens an approved Edit Revision or Render Artifact.
2. User selects/deselects individual destination accounts.
3. Clipah validates or creates a provider rendition from the immutable master.
4. A common metadata draft is copied into provider-specific forms. The user reviews every destination independently.
5. The user chooses `Publish now` or a future local date/time; Clipah stores UTC plus the display timezone.
6. The confirmation screen shows artifact thumbnail, exact destination, caption/title, visibility, disclosures, interaction controls, and any provider warnings.
7. One confirmation creates one Publication Batch and one independent Publication per selected account.

### 9.3 Status and recovery

The dashboard shows scheduled, transferring, processing, published, reconnect-required, retryable-failed, permanent-failed, and cancelled states. Each destination has its own retry/cancel/open-permalink action. Ambiguous provider timeouts trigger reconciliation before any create/publish retry.

## 10. Provider-specific behavior

### 10.1 YouTube Shorts

- OAuth scope starts with `youtube.upload`; caption-track publishing is a separately consented scope expansion.
- Collect title, description, tags, category, privacy, made-for-kids, synthetic-media disclosure, optional notification behavior, and schedule.
- Upload through the resumable protocol and persist the session URI encrypted plus confirmed byte range after each response.
- Poll `videos.list` processing details until terminal.
- Upload scheduled content early as private and set native `publishAt`; never use a past time accidentally.
- Classify the rendition as Shorts-eligible when square/vertical and no longer than three minutes, but label this as eligibility because YouTube performs final classification and exposes no Shorts flag.
- Treat unverified-project private-only behavior as a launch gate. Public/unlisted controls remain disabled until the YouTube API compliance audit succeeds.
- Custom thumbnail and timed-caption upload are capability-probed follow-on steps after the video ID exists; failure does not duplicate the video upload.

### 10.2 Instagram Reels

- Use Instagram API with Instagram Login for Professional accounts unless a future requirement explicitly needs Facebook Login for Business.
- Request `instagram_business_basic` and `instagram_business_content_publish` through Advanced Access for customer accounts.
- Create with `media_type=REELS` and provider-specific caption, `share_to_feed`, cover URL/frame offset, and required media fields.
- Prefer provider pull from a short-lived, narrowly scoped URL that remains valid until terminal container processing.
- Poll container status. No publishing webhook is assumed.
- Create/publish near the scheduled time; never create a container more than 23 hours before intended publication because containers expire at 24 hours.
- Query `content_publishing_limit` immediately before reservation/dispatch and treat its response plus provider errors as authoritative.
- Do not expose fake per-post privacy choices or native music-picker behavior.

### 10.3 TikTok

- TikTok Direct Post is the target. TikTok Upload-to-draft is available as a launch fallback until Direct Post audit approval.
- Every Direct Post confirmation refreshes creator info and displays current creator nickname, duration limit, privacy options, Comment/Duet/Stitch availability, commercial-content controls, AI-generated disclosure, and Music Usage Confirmation.
- Privacy has no default. Interaction controls default off and can only be enabled when current creator info permits them.
- Use `PULL_FROM_URL` for server-held Render Artifacts under a verified non-redirecting domain/prefix; keep the URL valid for the provider's documented fetch window.
- Persist `publish_id`; process signed, duplicate-safe webhooks and poll as fallback.
- Clipah scheduling delays initiation until the scheduled time, then refreshes the token and creator info. If required options changed, move to `awaiting_approval` rather than silently substituting a setting.
- The render preflight blocks Clipah promotional branding, URLs, or watermarks prohibited by TikTok's sharing rules.
- Unaudited/private-only constraints are represented as live capabilities, not hidden errors.

## 11. Scheduling and execution

A Postgres-backed scheduler claims due Publications using `FOR UPDATE SKIP LOCKED`, writes an outbox event in the same transaction, and Celery executes it. Each Publication has a lease and a per-Social-Account concurrency lock.

- YouTube: upload early as private, verify processing, and rely on native `publishAt`.
- Instagram: start container creation close to `scheduled_for` and publish after processing.
- TikTok: begin at `scheduled_for`; refresh creator info and move back to approval if capabilities invalidate the approved snapshot.

Clock comparisons use UTC. The original IANA timezone is stored for display and daylight-saving explanations. Scheduling in the past is rejected. Changes to artifact, destination, visibility, commercial disclosure, or caption after approval create a new metadata/consent snapshot and require approval again.

## 12. Media renditions

The approved Edit Revision renders one immutable high-quality master. Provider renditions are immutable derived artifacts keyed by `(master_hash, provider, profile_version)`.

The shared baseline is portrait MP4/H.264/AAC, but validation remains provider-specific. A rendition records resolution, duration, frame rate, video/audio codec, bitrate, size, checksum, and profile version. Publishing never reuses a rendition that fails the destination's current preflight.

Rendition creation is separate from Publication transfer. Retrying a provider upload never rerenders a healthy artifact.

## 13. API surface

All routes are under `/api/v1` and require Workspace permission checks.

### Social accounts

- `GET /workspaces/{workspace_id}/social-accounts`
- `POST /workspaces/{workspace_id}/social-accounts/{provider}/connect`
- `GET /social-oauth/{provider}/callback`
- `POST /social-accounts/{social_account_id}/refresh`
- `DELETE /social-accounts/{social_account_id}`
- `GET /social-accounts/{social_account_id}/capabilities`

### Publications

- `POST /edits/{edit_id}/revisions/{revision}/publication-drafts`
- `GET /publication-drafts/{draft_id}`
- `POST /publication-drafts/{draft_id}/preflight`
- `POST /publication-drafts/{draft_id}/confirm`
- `GET /publication-batches/{batch_id}`
- `GET /publications`
- `GET /publications/{publication_id}`
- `PATCH /publications/{publication_id}` only before transfer and requiring reapproval when consented fields change.
- `POST /publications/{publication_id}/cancel`
- `POST /publications/{publication_id}/retry`

### Provider events

- `POST /webhooks/tiktok`
- `POST /webhooks/instagram/deauthorization`
- `POST /webhooks/instagram/data-deletion`

Webhook handlers verify signatures, persist a deduplicated event, return promptly, and enqueue reconciliation. They do not perform publication state transitions inside the request.

## 14. Dashboard and editor integration

- `/dashboard/settings/connections`: connect, inspect, refresh, and disconnect Social Accounts.
- `/dashboard/publishing`: calendar/list of drafts, scheduled jobs, active transfers, failures, and published links.
- `/dashboard/publishing/[batchId]`: one batch with independent destination state and actions.
- `/dashboard/clips/[clipId]`: `Publish` action available only for an approved revision and permitted role.

The publish composer contains a common section and explicit platform tabs. A destination is never selected implicitly. Provider-specific fields remain visible rather than flattened into a misleading common form.

The final confirmation button identifies the action: `Publish now to 3 accounts` or `Schedule 3 publications for <local time>`. The screen states that successful destinations will not be rolled back if another destination fails.

## 15. Security and privacy

- Secrets use a Secret Manager reference or envelope encryption with key rotation; plaintext is absent from Postgres, Redis, object storage, logs, traces, Sentry, and job events.
- OAuth uses exact redirect URIs, PKCE S256 where supported, state, nonce for OIDC, least privilege, and incremental scope expansion.
- Refresh-token rotation is serialized and atomically persisted.
- Signed provider-pull URLs are scoped to one artifact/provider, non-listable, revocable, and expire after terminal fetch or the documented maximum window.
- Publication and membership mutations require CSRF protection and audit events.
- Webhooks validate signature and replay window before persistence.
- Provider raw errors are sanitized for users; raw payload retention is encrypted, time-bounded, and excludes credentials.
- Disconnect revokes provider permission when supported, cryptographically erases local credentials, marks the account revoked, and cancels all unpublished Publications.
- User deletion removes identities and sessions. Workspace data survives if other owners remain. Personal Workspace deletion follows the 30-day recovery policy.
- Published external posts remain on the platform unless the user separately requests and confirms a supported provider deletion action; provider deletion is outside the initial scope.

## 16. Error and retry semantics

Normalized errors include:

- `SOCIAL_ACCOUNT_RECONNECT_REQUIRED`
- `SOCIAL_SCOPE_MISSING`
- `SOCIAL_ACCOUNT_INELIGIBLE`
- `PUBLICATION_APPROVAL_REQUIRED`
- `PUBLICATION_CAPABILITY_CHANGED`
- `PUBLICATION_MEDIA_INVALID`
- `PUBLICATION_QUOTA_EXCEEDED`
- `PUBLICATION_RATE_LIMITED`
- `PUBLICATION_TRANSFER_EXPIRED`
- `PUBLICATION_PROCESSING_FAILED`
- `PUBLICATION_REJECTED`
- `PUBLICATION_AMBIGUOUS_RESULT`
- `PUBLICATION_PERMANENT_FAILURE`

Retryable network/5xx/rate-limit failures use jittered exponential backoff and provider `Retry-After`. Before retrying any ambiguous create or publish operation, the adapter reconciles saved provider IDs, upload sessions, containers, or recent account publications. The system never blindly repeats an operation that may have succeeded.

## 17. Observability and quotas

Metrics include connection health, refresh failures, due-job lag, preflight failures by field/provider, transfer bytes and resume count, processing time, publication success/failure, reconnect rate, webhook duplicates/signature failures, provider quota headroom, and partial-success batch rate.

Tokens, signed URLs, captions, and private metadata are excluded from metric labels. Provider request IDs are allowed in structured logs.

Quota admission is per Workspace and Social Account. Clipah reserves a publication slot when scheduling, rechecks live provider capability immediately before dispatch, and reconciles on terminal status. Provider limits remain runtime data rather than constants when the platform exposes an authoritative limit endpoint.

## 18. Testing strategy

### Unit tests

- Workspace role/last-owner rules.
- Composite tenant-key validation.
- Session rotation, expiry, and recent-auth rules.
- Provider capability and metadata schemas.
- Publication state transitions.
- Scheduling across timezone and daylight-saving boundaries.
- Rendition profile and disclosure preflight.
- Retry classification and ambiguous-result reconciliation.

### Integration tests

- Postgres migrations upgrade/downgrade and RLS default-deny behavior.
- Cross-Workspace access for every social/publication route and worker.
- Secret encryption/lease/redaction and refresh-token rotation races.
- Transactional Publication/outbox creation.
- Scheduler lease recovery after worker termination.
- Independent batch partial success.
- Disconnect/revocation cancelling unpublished jobs.
- Signed URL expiry and provider-pull lifecycle.
- Duplicate webhook idempotency.

### Provider contract tests

- Recorded, sanitized fixtures for current YouTube, Instagram, and TikTok request/response schemas.
- Fake adapters for ordinary CI.
- Explicit opt-in sandbox/live smoke suites with owned test accounts.
- Capability snapshots and API versions captured in every report.

### End-to-end tests

1. Connect owned test accounts and inspect granted scopes/capabilities.
2. Publish a private/unlisted YouTube Shorts-eligible rendition through a resumable interruption.
3. Publish an Instagram Reel by provider pull and container polling.
4. Upload a TikTok draft before audit and exercise Direct Post in sandbox/audited test mode with mandatory consent controls.
5. Schedule all three, change one TikTok capability before dispatch, and prove only TikTok returns to approval.
6. Publish a batch where Instagram fails permanently and YouTube/TikTok succeed without rollback or duplication.
7. Revoke a Social Account during an upload and prove secrets/jobs terminate safely.
8. Remove a Workspace member and prove new access is denied while actor audit remains intact.

## 19. Rollout gates

1. Workspace-first migration, authorization, RLS, and cross-tenant tests.
2. Connection framework and encrypted credential lifecycle with fake adapters.
3. YouTube private/unlisted immediate upload, resumability, and processing reconciliation.
4. YouTube native scheduling and compliance-audit completion before public visibility.
5. Instagram owned professional test account, pull upload, polling, then Advanced Access/App Review.
6. TikTok Upload-to-draft beta.
7. TikTok Direct Post mandatory UX, webhook handling, sandbox tests, and platform audit.
8. Multi-destination immediate publishing.
9. Multi-destination scheduling after each adapter independently passes revocation, retry, quota, ambiguous-timeout, and partial-failure gates.

## 20. Acceptance criteria

- Every durable product row is attributable to one Workspace or is explicitly global reference data.
- User email changes cannot create a duplicate identity or transfer access.
- Removing a member prevents access immediately without deleting Workspace data.
- No plaintext provider credential or upload secret appears in any durable/telemetry surface.
- Every Publication identifies one Social Account, immutable Render Artifact, metadata snapshot, consent snapshot, and approving User.
- No destination is selected or published without explicit approval.
- One provider failure never restarts, deletes, or duplicates another destination.
- Scheduled Publications survive API/worker/Redis restarts and execute at the correct UTC instant.
- YouTube uploads resume from persisted byte state and Shorts eligibility is not represented as a guaranteed classification.
- Instagram containers are created within their valid scheduling window and actual publishing headroom is checked.
- TikTok Direct Post uses fresh creator info, manual privacy selection, mandatory disclosure/consent UI, compliant transfer mode, and webhook/poll reconciliation.
- Disconnect/revocation cancels unpublished jobs and makes future operations return reconnect-required without leaking credentials.
- All provider, tenant-isolation, security, migration, and end-to-end gates pass before production enablement.
