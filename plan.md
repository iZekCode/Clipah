# Clipah Production Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the single-user Clipah prototype into a secure, durable, workspace-based, Indonesia-first clipping product that discovers context-safe ranked clips, supports non-destructive timeline editing, suggests provenance-aware B-roll, creates platform variants and campaign assets, renders exports reliably, and publishes approved artifacts directly or on schedule to YouTube Shorts, Instagram Reels, and TikTok.

**Architecture:** Build a modular monolith with a Next.js frontend, a typed FastAPI HTTP process, and separate Celery workers for ingestion, AI, B-roll, rendering, scheduling, and social publishing. Postgres is the source of truth, Redis is the broker/transient event transport, and S3-compatible object storage holds source media, proxies, retrieved/generated assets, immutable render masters, and provider renditions. Workspace is the tenant boundary; Users authenticate through Login Identities and gain access through Workspace Memberships. Each explicitly approved social destination becomes an independent durable Publication backed by provider adapters, encrypted OAuth grants, resumable checkpoints, and webhook/poll reconciliation.

**Tech Stack:** Python 3.13, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, Celery, Redis, Postgres with tenant constraints/RLS, a Secret Manager or envelope encryption, boto3-compatible object storage, Google OpenID Connect, official YouTube Data API, Instagram API with Instagram Login, TikTok Content Posting API, AssemblyAI Universal-2/Universal-3 Pro, Groq GPT-OSS strict structured output, yt-dlp with EJS and Deno, FFmpeg/ffprobe, Next.js 15+, React 19+, TypeScript, Zod, TanStack Query, Zustand with Immer, an editor engine selected between Elah and an OpenReel/Mediabunny approach, Pexels/Pixabay, fal with optional Runway, Vitest, Playwright, pytest, Ruff, mypy, Docker Compose, OpenTelemetry, and Sentry.

**Spec:** This file is the approved master specification and implementation plan. It implements `docs/superpowers/specs/2026-08-29-workspace-social-publishing-design.md`, uses the canonical language in `CONTEXT.md`, and incorporates `research/2026-08-29-clipah-platform-options.md` plus `research/2026-08-29-social-publishing-apis.md`. Sections 1-12 define the target behavior and Tasks 1-48 implement it.

## Global Constraints

- Lock the social-delivery scope to **Publish now + scheduling**; TikTok uses the official draft fallback until Direct Post audit approval.
- Keep Python for media, transcription, AI orchestration, and server-side rendering.
- Pin production to Python 3.13 and test Python 3.14 compatibility in CI without deploying it automatically.
- Keep the system a modular monolith. Deploy the web, worker, and scheduler processes separately from the same backend package.
- Use Postgres as the only durable state source. Redis task state and browser state are caches, never authoritative records.
- Give every User, Login Identity, Session, Workspace, Membership, Project, Asset, Job, candidate, Edit Revision, Render Artifact, Social Account, OAuth Grant, and Publication a UUID.
- Make Workspace the tenant and ownership boundary. Every tenant-owned row carries `workspace_id`; object keys begin with `workspaces/{workspace_id}/`; guessed UUIDs never expose data across Workspaces.
- Create a personal Workspace for every new User and authorize access only through Workspace Memberships. Email is profile/contact data, never an authentication key.
- Identify a Google Login Identity by `(issuer, subject)`, not email. Keep Clipah Sessions separate from source/social OAuth grants.
- Use application authorization as the primary control, composite Workspace foreign keys as integrity control, and Postgres RLS as defense-in-depth with non-owner, non-`BYPASSRLS` API/worker roles.
- Pass only scalar identifiers to Celery tasks. Workers reload database records inside their own transaction.
- Make every task idempotent. A retry must either reuse a completed artifact or safely replace an artifact owned by the same record.
- Transcribe each source asset once. Derive candidate text and clip captions from persisted word timestamps.
- Separate analysis from rendering. Candidate discovery must not encode final clips.
- Store edits as immutable composition revisions. Never destructively modify the uploaded source.
- Build FFmpeg commands as argument arrays and filter scripts. Never use `shell=True` or interpolate user text into shell/filter syntax.
- Keep external providers behind real seams with a production adapter and a deterministic fake adapter for tests.
- Keep Social Accounts separate from encrypted OAuth Grants. Persist only a Secret Manager reference or envelope-encrypted secret; serialize refreshes and never expose credentials or provider upload URLs in telemetry.
- Publish only an immutable approved Render Artifact to explicitly selected Social Accounts. One multi-destination confirmation creates independent Publications; partial success never triggers rollback or duplicate upload elsewhere.
- Support `Publish now` and scheduling. YouTube may use native `publishAt`; Instagram and TikTok scheduling is executed by Clipah using live capability checks.
- Keep TikTok Upload-to-draft as the launch fallback while Direct Post review/audit is incomplete; Direct Post remains the required end state and must implement TikTok's mandatory creator-info, privacy, interaction, disclosure, consent, and watermark rules.
- Use only official social publishing APIs. Do not implement browser automation, credential sharing, or scraping as a publishing fallback.
- Default ingestion to direct upload. Treat public YouTube import as a convenience connector and authenticated YouTube import as an explicitly consented, feature-flagged, legally reviewed capability.
- Never persist or share a plaintext global cookie jar. Any hosted cookie credential is per-user, encrypted, domain-filtered, revocable, short-lived, auditable, and materialized only inside its job workspace.
- Keep yt-dlp, its EJS support, Deno runtime, and optional PO Token provider in an independently deployable ingestion image with a fast update path.
- Route transcription by detected/requested language: AssemblyAI Universal-2 for Indonesian and other unsupported Universal-3 Pro languages; Universal-3 Pro only for its supported languages. Do not infer speaker identity with an LLM.
- Use Groq `openai/gpt-oss-20b` for candidate extraction and evaluate `openai/gpt-oss-120b` for global reranking. Model IDs remain configuration, never inline constants.
- Require strict JSON Schema output where the provider supports it and always enforce local Pydantic plus domain validation.
- Own Clipah's composition schema, editing UX, and FFmpeg compiler. Do not wholesale-fork an editor or build browser decoding, seeking, frame scheduling, waveform generation, and muxing from zero before the editor-engine bake-off fails.
- Make B-roll suggestion-first, stock-first, editable, provenance-aware, and disabled by default for a new project. Generative video is an opt-in fallback with explicit quota and cost visibility.
- Preserve the original dialogue audio for B-roll unless the user explicitly edits it, and never silently publish or export generated visual claims without a review state.
- Treat Bahasa Indonesia, English, and code-switched speech as first-class evaluation cohorts.
- Validate all HTTP input and all model output with Pydantic or Zod schemas.
- Return stable machine-readable error codes. Keep provider errors and stack traces out of user responses.
- Use test-driven development for every behavior change and preserve a green main branch after each task.
- Do not add microservices, Kubernetes, GraphQL, collaborative multi-cursor editing, mobile applications, or custom model training during this plan.

---

## 1. Current-System Problems This Plan Must Eliminate

The implementation is incomplete until every item below is covered by a passing test or an explicit removal commit.

| Current behavior | Required replacement |
|---|---|
| One global `processing_status` dictionary | Durable per-job records and Workspace-scoped job endpoints |
| Shared `main_video.mp4`, `main_audio.mp3`, and output folders | UUID-scoped object keys and temporary workspaces |
| `threading.Thread` launched by `/process` | Celery queues with retries, cancellation, and worker isolation |
| Cleanup at the start of every request | Retention-based cleanup for records and object prefixes owned by one project |
| One global `/status`, `/download`, and `/reset` | `/v1/projects/{id}`, `/v1/jobs/{id}`, and signed artifact download URLs |
| Unauthenticated, unlimited processing | Database-backed sessions, ownership checks, quotas, and Redis rate limits |
| Data modeled as directly owned by `user_id`/project members | Personal/team Workspaces, Login Identities, Sessions, Workspace Memberships, Workspace-owned data, tenant constraints, and RLS defense-in-depth |
| User login conflated with provider access | Separate Google Login Identity, Clipah Session, Source Connection, Social Account, and encrypted OAuth Grant lifecycles |
| Browser uploads 500 MB through Flask | Direct multipart upload to S3-compatible storage |
| Extension-only upload validation | Size, MIME, ffprobe, duration, stream, and codec validation |
| Frontend-only YouTube validation | Backend HTTPS/host allowlist, DNS/IP safety check, and provider policy validation |
| `nocheckcertificate=True` | Normal TLS verification with actionable import errors |
| `cookies.txt` erased and then referenced | Public import without cookies by default; optional per-user encrypted ephemeral cookie connections with consent, expiry, revocation, domain filtering, and audit |
| yt-dlp bundled into the main web process | Independently deployable importer with pinned yt-dlp/EJS/Deno, optional PO Token adapter, resource limits, and a fast update cadence |
| AssemblyAI `SpeechModel.universal` and pinned legacy SDK | Controlled SDK migration, explicit language-to-model routing, provider contract tests, and a measured transcription bake-off |
| Retired Groq Llama 4 Scout model hard-coded in two calls | Configurable Groq adapter using current GPT-OSS strict structured output, staged extraction/reranking, fallback, and deprecation monitoring |
| LLM guesses speaker labels from transcript text | Transcription-provider diarization and persisted speaker segments |
| Full transcript sent in one prompt | Overlapping windows, candidate extraction, deduplication, and global reranking |
| Unvalidated `json.loads` model response | Strict structured output plus semantic timestamp validation |
| Transcript repeated for every candidate subtitle | One word-timestamp transcript sliced by candidate bounds |
| Every candidate rendered immediately | Metadata-first candidate review and on-demand exports |
| MoviePy plus repeated FFmpeg encoding | A render-plan compiler and one controlled FFmpeg export pass |
| No durable asset provenance | Provider/source/license/author/query/prompt/model/seed/moderation/checksum metadata for every external or generated asset |
| One generic landing-style product screen | Project-centric dashboard, project detail, clip library, editor, assets, templates, brand kits, team, and settings routes |
| No automatic visual enrichment | Editable B-roll suggestions from user assets and licensed stock, with opt-in generated fallback and placement explanations |
| Generic virality score | Context-safety, narrative-completeness, platform-fit, and explainable score breakdowns with hook/duration variants |
| Download-only exports and generated campaign copy | Explicit immediate/scheduled Publications to YouTube Shorts, Instagram Reels, and TikTok with independent status, retry, cancellation, and permalink history |
| No recoverable social delivery state | Provider-specific renditions, encrypted resumable checkpoints, idempotent attempts, webhook/poll reconciliation, quota checks, and partial-success semantics |
| Center-only portrait crop | User-editable crop plus optional face/active-speaker suggestions |
| User watermark injected into a filter string | UTF-8 text files referenced by an FFmpeg filter script |
| Form-data values such as `"false"` become truthy Python strings | Typed JSON/multipart fields parsed into strict booleans and covered by contract tests |
| LLM text inserted with `innerHTML` | React rendering and explicit text nodes without raw HTML, eliminating the current XSS path |
| Flask template and unused Next.js app coexist | Next.js is the only product UI; FastAPI serves `/api/v1` only |
| No runtime lock, migrations, or tests | Locked backend/frontend dependencies, Alembic, unit/integration/E2E suites |
| Debug defaults conflict | Explicit environment profiles with production-safe defaults |
| README says Gemini while code uses Groq | Provider-neutral documentation generated from actual configuration |

---

## 2. Target Product Flow

1. A visitor may inspect a read-only demo without receiving access to private uploads.
2. A new User receives a personal Workspace; an authenticated User selects a permitted personal/team Workspace and enters its project-centric dashboard.
3. The user uploads a video directly to object storage, imports one authorized public YouTube video, or explicitly activates the feature-flagged authenticated connector after consent.
4. The API records only durable intent and queues an isolated ingest/import job; no web request stays open for media work.
5. Ingest validates the media with ffprobe, generates a low-resolution proxy, a thumbnail, waveform data, scene boundaries, and transcription audio.
6. Transcription runs exactly once with word timestamps, speaker diarization, language/model routing, and raw provider metadata retained for reproducibility.
7. Analysis divides the transcript into overlapping narrative windows, extracts candidates with a low-cost model, validates word IDs, deduplicates them, and globally reranks them with a higher-quality model.
8. Each candidate receives explainable hook, payoff, narrative-completeness, context-safety, platform-fit, transcript-confidence, and visual-opportunity scores.
9. The dashboard displays ranked candidates before any final clip is rendered and allows project, job, clip, asset, template, and brand-kit navigation.
10. The user can create hook, duration, and platform variants from one candidate without duplicating the source transcript.
11. Opening a candidate creates an initial immutable composition revision from the selected variant.
12. An optional B-roll planning job detects visualizable semantic beats and creates editable suggestions from user assets and licensed stock.
13. When retrieval confidence is below the configured threshold, the user may explicitly request an image or video generation job with a visible cost/quota estimate.
14. The editor modifies a working composition locally, supports undo/redo, shows B-roll confidence/provenance/reasoning, and autosaves new revisions with optimistic concurrency.
15. Preview uses proxy assets and browser-rendered overlays through the selected editor engine; the original source remains untouched.
16. The user can accept, replace, regenerate, move, or remove every suggested B-roll item and can choose `minimal`, `balanced`, or `dynamic` coverage.
17. Export creates an idempotent render job keyed by immutable composition hash and render preset.
18. The render worker compiles the composition to a validated FFmpeg render plan, preserves dialogue audio by default, reports progress, and stores the final artifact.
19. The user downloads through a short-lived signed URL and can generate campaign copy/thumbnail briefs from the same approved Edit Revision.
20. An owner/admin connects Workspace Social Accounts through separate least-privilege OAuth ceremonies; Clipah stores encrypted grants independently from login Sessions.
21. A permitted User selects one immutable Render Artifact, selects/deselects destination accounts, reviews platform-specific metadata/privacy/disclosures, and chooses immediate or scheduled publication.
22. One confirmation creates a Publication Batch and one independent Publication per destination; each snapshots artifact, metadata, provider options, consent, approving User, time, and timezone.
23. The scheduler refreshes credentials/live capabilities, creates or reuses a provider-specific rendition, then dispatches resumable provider work without holding an HTTP request open.
24. YouTube uses resumable upload and native scheduling where applicable; Instagram creates/publishes a Reel container near execution; TikTok uses draft fallback until Direct Post audit and then enforces fresh creator-info consent.
25. The publishing dashboard shows partial success, reconnect-required, retries, cancellations, processing, and provider permalinks without rolling back another destination.
26. Retention jobs remove expired cookie material, OAuth grants after revocation, temporary workspaces, encrypted upload checkpoints, generated drafts, and deleted-Workspace artifacts without affecting active or unrelated Workspaces or deleting published provider posts.

### Durable state machines

`ProjectStatus`:

```text
created -> uploading -> ingesting -> transcribing -> analyzing -> ready
                                                   \-> failed
ready -> archived
```

`JobStatus`:

```text
queued -> running -> succeeded
   |         |\-> retrying -> running
   |         \-> failed
   \-> canceled
running -> cancel_requested -> canceled
```

Allowed job kinds are `source_import`, `ingest`, `transcribe`, `analyze`, `broll_plan`, `broll_retrieve`, `broll_generate`, `render`, `campaign_generate`, `social_rendition`, `social_publish`, `social_reconcile`, and `cleanup`.

`BrollSuggestionStatus`:

```text
proposed -> accepted -> placed
    |          |\-> replaced
    |          \-> removed
    \-> rejected
proposed -> generation_requested -> generating -> proposed
                                      \-> failed
```

Only user action may move a generated suggestion into `accepted`; automation may create `proposed` records but may not silently approve them.

`SocialConnectionStatus`:

```text
connected -> refresh_required -> connected
    |              \-> revoked
    |\-> disabled
    \-> revoked
```

`PublicationStatus`:

```text
draft -> awaiting_approval -> scheduled -> preflighting
draft -> awaiting_approval -> preflighting
preflighting -> transferring -> processing -> published
       |             |             |\-> retryable_failed -> preflighting
       |             |             \-> permanent_failed
       |             \-> retryable_failed
       |\-> reconnect_required -> scheduled | awaiting_approval
       \-> permanent_failed
scheduled -> cancelled
```

`published`, `permanent_failed`, and `cancelled` are terminal. Provider capability changes that invalidate approved TikTok/Instagram settings return the Publication to `awaiting_approval`; adapters never silently substitute visibility or disclosure choices.

---

## 3. Target Repository Layout

```text
Clipah/
├── backend/
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── alembic.ini
│   ├── migrations/
│   ├── src/clipah/
│   │   ├── api/                 # FastAPI app, routes, dependencies, errors
│   │   ├── auth/                # Google OIDC identities, sessions, CSRF/recent auth
│   │   ├── workspaces/          # Tenant, membership, invites, roles, RLS context
│   │   ├── projects/            # Project use cases and persistence
│   │   ├── assets/              # Upload, import, probe, proxy, storage keys
│   │   ├── source_connectors/   # Public/authenticated source imports and secret leases
│   │   ├── jobs/                # Durable jobs, events, cancellation, Celery glue
│   │   ├── transcripts/         # Provider port, word/speaker normalization
│   │   ├── highlights/          # Windowing, extraction, dedupe, reranking
│   │   ├── variants/            # Hook, duration, and platform-specific variants
│   │   ├── broll/               # Visual intent, retrieval, reranking, generation, provenance
│   │   ├── editor/              # Composition schema and revision use cases
│   │   ├── renders/             # Render plans, FFmpeg adapter, artifacts
│   │   ├── brands/              # Brand kits, reusable templates, constraint validation
│   │   ├── campaigns/           # Post copy, CTA, title, hashtag, thumbnail briefs
│   │   ├── social_accounts/      # Provider account metadata and encrypted OAuth grants
│   │   ├── publishing/           # Publications, scheduler, provider adapters, reconciliation
│   │   ├── search/              # Workspace-scoped transcript and content-library search
│   │   ├── retention/           # Expiration and deletion policies
│   │   ├── observability/       # Logging, metrics, traces, provider usage
│   │   ├── db.py
│   │   ├── config.py
│   │   └── celery_app.py
│   └── tests/
│       ├── unit/
│       ├── integration/
│       ├── contract/
│       └── fixtures/media/
├── frontend/
│   ├── app/
│   ├── features/
│   │   ├── auth/
│   │   ├── projects/
│   │   ├── uploads/
│   │   ├── clips/
│   │   ├── jobs/
│   │   ├── assets/
│   │   ├── templates/
│   │   ├── brand-kits/
│   │   ├── broll/
│   │   ├── settings/
│   │   ├── publishing/
│   │   └── editor/
│   ├── lib/api/
│   ├── tests/
│   └── e2e/
├── contracts/
│   ├── openapi.json
│   ├── composition.schema.json
│   ├── transcript.schema.json
│   └── provider-output-schemas/
├── infra/
│   ├── docker/
│   ├── compose.yaml
│   └── railway/
├── scripts/
├── research/
│   ├── 2026-08-29-clipah-platform-options.md
│   └── 2026-08-29-social-publishing-apis.md
├── docs/superpowers/specs/
│   └── 2026-08-29-workspace-social-publishing-design.md
├── CONTEXT.md
├── plan.md
└── README.md
```

Each domain package exposes one small interface to its callers. Provider SDKs, SQLAlchemy queries, and FFmpeg details stay inside their module implementations.

---

## 4. Core Domain and Data Model

### Required tables

Every tenant-owned table below carries `workspace_id NOT NULL`, even when Project ancestry could infer it. Cross-table tenant relationships use composite `(workspace_id, id)` foreign keys and tenant indexes begin with `workspace_id`.

- `users`: `id`, `primary_email`, `display_name`, `avatar_url`, `status`, `created_at`, `disabled_at`, `deleted_at`. Email is mutable contact/profile data, not an authentication key.
- `auth_identities`: `id`, `user_id`, `provider`, `issuer`, `subject`, `email_at_provider`, `email_verified`, `created_at`, `last_login_at` with unique `(issuer, subject)`.
- `auth_sessions`: `id`, `user_id`, `token_hash`, `created_at`, `last_seen_at`, `idle_expires_at`, `absolute_expires_at`, `recent_auth_at`, `revoked_at`, `ip_hash`, `user_agent_summary`.
- `workspaces`: `id`, `name`, `slug`, `kind`, `publishing_role_policy`, `status`, `created_at`, `deleted_at`.
- `workspace_memberships`: `workspace_id`, `user_id`, `role`, `invited_by_user_id`, `joined_at`, `removed_at` with primary key `(workspace_id, user_id)`.
- `workspace_invites`: `id`, `workspace_id`, `email`, `role`, `token_hash`, `created_by_user_id`, `expires_at`, `accepted_by_user_id`, `accepted_at`, `revoked_at`.
- `projects`: `id`, `workspace_id`, `created_by_user_id`, `name`, `status`, `source_kind`, `created_at`, `updated_at`, `archived_at`.
- `assets`: `id`, `project_id`, `kind`, `source_type`, `storage_key`, `content_type`, `size_bytes`, `duration_ms`, `width`, `height`, `video_codec`, `audio_codec`, `sha256`, `created_at`.
- `asset_provenance`: `id`, `asset_id`, `provider`, `provider_asset_id`, `source_url`, `author`, `license_name`, `license_url`, `terms_snapshot`, `retrieved_at`, `query`, `prompt`, `model`, `model_version`, `seed`, `moderation_result`, `checksum`. Secrets and raw cookie values are forbidden in this table.
- `source_connections`: `id`, `workspace_id`, `authorized_by_user_id`, `provider`, `status`, `encrypted_secret_ref`, `domain_scope`, `consented_at`, `expires_at`, `last_used_at`, `revoked_at`. Only an external secret-store reference or envelope-encrypted ciphertext is persisted; plaintext credentials never enter Postgres.
- `source_imports`: `id`, `project_id`, nullable `connection_id` for public imports, `normalized_source_url`, `source_video_id`, nullable `authorization_attested_at`, `status`, `job_id`, `created_at`.
- `multipart_uploads`: `id`, `project_id`, `storage_upload_id`, `storage_key`, `status`, `expires_at`.
- `jobs`: `id`, `project_id`, `kind`, `status`, `stage`, `progress`, `attempt`, `idempotency_key`, `error_code`, `error_message`, `cancel_requested_at`, timestamps.
- `job_events`: `id`, `job_id`, `sequence`, `event_type`, `payload`, `created_at`.
- `transcripts`: `id`, `project_id`, `asset_id`, `provider`, `provider_version`, `model`, `language`, `full_text`, `words`, `speaker_segments`, `utterances`, `duration_ms`, `raw_result_storage_key`, `created_at`.
- `clip_candidates`: `id`, `project_id`, `transcript_id`, `rank`, `score`, `hook`, `reason`, `category`, `tags`, `start_ms`, `end_ms`, `transcript_excerpt`, `score_breakdown`, `context_warnings`, `visual_opportunities`, `model_metadata`, `created_at`.
- `clip_variants`: `id`, `candidate_id`, `kind`, `platform`, `hook_strategy`, `target_duration_ms`, `start_word_id`, `end_word_id`, `title`, `rationale`, `created_at`.
- `claim_evidence`: `id`, `candidate_id`, `start_word_id`, `end_word_id`, `claim_text`, `source_url`, `source_title`, `publisher`, `retrieved_at`, `verification_status`, `created_by`, `created_at`. Version 1 records user-provided evidence and warnings; it does not claim automatic truth verification.
- `clip_edits`: `id`, `workspace_id`, `candidate_id`, `created_by_user_id`, `current_revision`, `created_at`, `updated_at`.
- `clip_edit_revisions`: `id`, `clip_edit_id`, `revision`, `composition`, `composition_hash`, `created_by`, `created_at` with a unique `(clip_edit_id, revision)` constraint.
- `render_artifacts`: `id`, `clip_edit_revision_id`, `job_id`, `preset`, `composition_hash`, `storage_key`, `size_bytes`, `duration_ms`, `created_at` with a unique `(composition_hash, preset)` constraint.
- `broll_suggestions`: `id`, `project_id`, `candidate_id`, `edit_id`, `beat_start_word_id`, `beat_end_word_id`, `visual_intent`, `search_terms`, `exclusions`, `source_type`, `asset_id`, `status`, `relevance_score`, `placement_reason`, `provider_metadata`, `created_at`, `decided_at`.
- `brand_kits`: `id`, `workspace_id`, `name`, `logo_asset_id`, `fonts`, `colors`, `caption_rules`, `visual_exclusions`, `claim_rules`, `created_at`, `updated_at`.
- `templates`: `id`, `workspace_id`, `brand_kit_id`, `name`, `kind`, `version`, `definition`, `created_at`, `archived_at`.
- `campaign_outputs`: `id`, `clip_edit_revision_id`, `platform`, `title`, `post_copy`, `cta`, `hashtags`, `thumbnail_brief`, `model_metadata`, `created_at`.
- `edit_reviews`: `id`, `clip_edit_revision_id`, `actor_id`, `status`, `created_at`; a new revision makes prior approvals stale but never deletes their audit history.
- `review_comments`: `id`, `clip_edit_revision_id`, `actor_id`, `timeline_ms`, `composition_item_id`, `body`, `resolved_by`, `resolved_at`, `created_at`.
- `social_accounts`: `id`, `workspace_id`, `provider`, `external_account_id`, `display_name`, `avatar_url`, `account_type`, `login_family`, `api_version`, `connection_status`, `capability_snapshot`, `authorized_by_user_id`, `last_validated_at`, `created_at`, `revoked_at` with unique `(workspace_id, provider, external_account_id)`.
- `oauth_grants`: `id`, `workspace_id`, `social_account_id`, `encrypted_secret_ref`, `granted_scopes`, `access_token_expires_at`, `refresh_token_expires_at`, `token_version`, `last_refreshed_at`, `reconnect_reason`, `revoked_at`; one active grant per Social Account.
- `publication_batches`: `id`, `workspace_id`, `edit_revision_id`, `render_artifact_id`, `created_by_user_id`, `created_at`.
- `publications`: `id`, `workspace_id`, `batch_id`, `social_account_id`, `edit_revision_id`, `render_artifact_id`, `approved_by_user_id`, `metadata_snapshot`, `provider_options`, `consent_snapshot`, `scheduled_for`, `display_timezone`, `status`, `idempotency_key`, provider IDs/permalink, safe checkpoint metadata, encrypted checkpoint reference, retry/error fields, and lifecycle timestamps with unique `(workspace_id, social_account_id, idempotency_key)`.
- `publication_attempts`: `id`, `publication_id`, `attempt`, `stage`, `provider_request_id`, `byte_checkpoint`, sanitized request/response metadata, timing, error, `created_at`; append-only and secret-free.
- `provider_events`: `id`, `workspace_id`, `social_account_id`, `publication_id`, `provider_event_id`, `payload_hash`, `event_type`, `signature_valid`, normalized status, receive/process timestamps, encrypted raw-payload reference; deduplicated by provider event ID or stable payload hash.
- `audit_events`: `id`, `workspace_id`, nullable `actor_user_id`, `action`, `target_kind`, `target_id`, security-relevant before/after metadata, `request_id`, `created_at`; append-only.
- `provider_usage`: `workspace_id`, provider, operation, model/API version, request ID, input/output units, estimated/actual cost, job/publication ID, timestamps.
- `retention_tombstones`: entity kind/id, storage prefix, eligible_at, deleted_at, failure count.

### Composition document version 1

```json
{
  "schemaVersion": 1,
  "sourceAssetId": "uuid",
  "durationMs": 58000,
  "canvas": {"width": 1080, "height": 1920, "background": "#000000"},
  "sourceRange": {"inMs": 12000, "outMs": 70000},
  "tracks": [
    {
      "id": "main-video",
      "type": "video",
      "items": [
        {
          "id": "scene-1",
          "sourceAssetId": "uuid",
          "timelineStartMs": 0,
          "sourceInMs": 12000,
          "sourceOutMs": 18000,
          "transform": {"x": 0.5, "y": 0.5, "scale": 1.0, "rotation": 0},
          "opacity": 1.0,
          "blendMode": "normal",
          "origin": {"type": "source", "suggestionId": null}
        }
      ]
    }
  ],
  "captions": {
    "mode": "karaoke",
    "words": [{"id": "word-1", "startMs": 0, "endMs": 420, "text": "Example", "speaker": "SPEAKER_00"}],
    "style": {"fontFamily": "Montserrat", "fontSize": 64, "color": "#FFFFFF", "align": "center", "weight": 700, "backgroundEnabled": false}
  },
  "overlays": [
    {
      "id": "broll-1",
      "type": "video",
      "assetId": "uuid",
      "timelineStartMs": 8000,
      "timelineEndMs": 12000,
      "sourceInMs": 0,
      "sourceOutMs": 4000,
      "placement": "cover",
      "preserveDialogueAudio": true,
      "origin": {"type": "brollSuggestion", "suggestionId": "uuid"}
    }
  ],
  "audio": {"gainDb": 0, "musicGainDb": -18},
  "bookmarks": []
}
```

The schema must also support text/image/video B-roll overlays, citation overlays referencing `claim_evidence`, keyframes for transform/opacity/text style, music and extracted-audio tracks, template identifiers, brand-kit references, motion presets, placement origin, and source provenance references. Version 1 rejects unknown track types, unauthorized assets, invalid time ranges, overlapping caption words, and unsupported engine features rather than silently dropping data. Composition JSON stores references to provenance/evidence records, never copied third-party secrets or raw provider payloads.

---

## 5. HTTP Interface

All endpoints are under `/api/v1`. Every private endpoint requires the secure Clipah Session and performs a Workspace Membership permission check. Tenant records are queried by `(workspace_id, id)`; API clients cannot override Workspace context with an unverified body field.

### Authentication

- `GET /auth/google/start`
- `GET /auth/google/callback`
- `POST /auth/logout`
- `GET /me`
- `GET /me/sessions`
- `DELETE /me/sessions/{session_id}`

### Workspaces and membership

- `POST /workspaces`
- `GET /workspaces`
- `GET /workspaces/{workspace_id}`
- `PATCH /workspaces/{workspace_id}`
- `DELETE /workspaces/{workspace_id}` requires recent authentication and follows the 30-day recovery policy.
- `POST /workspaces/{workspace_id}/restore`
- `POST /workspaces/{workspace_id}/invites`
- `POST /workspace-invites/{token}/accept`
- `GET /workspaces/{workspace_id}/members`
- `PATCH /workspaces/{workspace_id}/members/{user_id}`
- `DELETE /workspaces/{workspace_id}/members/{user_id}`
- `POST /workspaces/{workspace_id}/transfer-ownership`

### Projects and uploads

- `GET /dashboard/summary`
- `POST /projects`
- `GET /projects`
- `GET /projects/{project_id}`
- `PATCH /projects/{project_id}`
- `DELETE /projects/{project_id}` performs a recoverable soft delete and schedules retention.
- `POST /projects/{project_id}/restore`
- `POST /projects/{project_id}/uploads`
- `POST /projects/{project_id}/uploads/{upload_id}/parts/{part_number}`
- `POST /projects/{project_id}/uploads/{upload_id}/complete`
- `DELETE /projects/{project_id}/uploads/{upload_id}`
- `POST /projects/{project_id}/youtube-imports`
- `POST /source-connections/youtube-cookie` creates an encrypted, expiring connection only when the feature flag and legal gate are enabled.
- `GET /source-connections`
- `DELETE /source-connections/{connection_id}` revokes and schedules immediate secret deletion.
- `GET /assets`
- `POST /projects/{project_id}/assets` for user-owned B-roll, logos, images, audio, and overlays.
- `GET /projects/{project_id}/assets`
- `DELETE /assets/{asset_id}` only when no live composition revision references it.

### Processing and candidates

- `POST /projects/{project_id}/analysis`
- `GET /projects/{project_id}/candidates`
- `GET /projects/{project_id}/candidates/{candidate_id}`
- `POST /projects/{project_id}/candidates/{candidate_id}/variants`
- `GET /projects/{project_id}/candidates/{candidate_id}/variants`
- `POST /projects/{project_id}/candidates/{candidate_id}/edits`
- `POST /projects/{project_id}/candidates/{candidate_id}/broll-plans`
- `GET /projects/{project_id}/candidates/{candidate_id}/broll-suggestions`
- `POST /broll-suggestions/{suggestion_id}/accept`
- `POST /broll-suggestions/{suggestion_id}/reject`
- `POST /broll-suggestions/{suggestion_id}/replace`
- `POST /broll-suggestions/{suggestion_id}/generate`
- `POST /projects/{project_id}/candidates/{candidate_id}/claim-evidence`
- `GET /projects/{project_id}/candidates/{candidate_id}/claim-evidence`
- `DELETE /claim-evidence/{evidence_id}`

### Editing and rendering

- `GET /edits/{edit_id}`
- `PUT /edits/{edit_id}` with `expectedRevision` for optimistic concurrency.
- `GET /edits/{edit_id}/revisions`
- `POST /edits/{edit_id}/renders`
- `GET /renders/{render_id}`
- `GET /renders/{render_id}/download-url`
- `POST /edits/{edit_id}/campaign-outputs`
- `GET /edits/{edit_id}/campaign-outputs`

### Product library and configuration

- `GET /clips` provides permission-scoped cross-project clip-library pagination.
- `GET /search?q=` searches permission-scoped transcripts, guests, topics, claims, and clips.
- `POST /brand-kits`
- `GET /brand-kits`
- `GET /brand-kits/{brand_kit_id}`
- `PATCH /brand-kits/{brand_kit_id}`
- `DELETE /brand-kits/{brand_kit_id}`
- `POST /templates`
- `GET /templates`
- `PATCH /templates/{template_id}` creates a new immutable template version.
- `DELETE /templates/{template_id}` archives the template without breaking saved compositions.

### Social accounts and publishing

- `GET /workspaces/{workspace_id}/social-accounts`
- `POST /workspaces/{workspace_id}/social-accounts/{provider}/connect`
- `GET /social-oauth/{provider}/callback`
- `POST /social-accounts/{social_account_id}/refresh`
- `DELETE /social-accounts/{social_account_id}` revokes credentials and cancels unpublished Publications.
- `GET /social-accounts/{social_account_id}/capabilities`
- `POST /edits/{edit_id}/revisions/{revision}/publication-drafts`
- `GET /publication-drafts/{draft_id}`
- `POST /publication-drafts/{draft_id}/preflight`
- `POST /publication-drafts/{draft_id}/confirm`
- `GET /publication-batches/{batch_id}`
- `GET /publications`
- `GET /publications/{publication_id}`
- `PATCH /publications/{publication_id}` only before transfer; artifact, destination, visibility, caption, schedule, or disclosure changes require a new approval snapshot.
- `POST /publications/{publication_id}/cancel`
- `POST /publications/{publication_id}/retry`
- `POST /webhooks/tiktok`
- `POST /webhooks/instagram/deauthorization`
- `POST /webhooks/instagram/data-deletion`

### Review

- `GET /edits/{edit_id}/revisions/{revision}/reviews`
- `POST /edits/{edit_id}/revisions/{revision}/comments`
- `PATCH /review-comments/{comment_id}` resolves or reopens a comment.
- `POST /edits/{edit_id}/revisions/{revision}/request-changes`
- `POST /edits/{edit_id}/revisions/{revision}/approve`

### Jobs

- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/events` using Server-Sent Events.
- `POST /jobs/{job_id}/cancel`

### Account lifecycle

- `DELETE /account` requires recent authentication, rejects a last-owner orphan, removes Memberships, revokes Sessions, deletes personal identity data per policy, and deletes a personal Workspace only through its explicit recovery flow.

All create endpoints accept an `Idempotency-Key` header and return the existing resource when the same user repeats the same key and payload.

---

## 6. Deep Module Interfaces

These are the stable interfaces. Tests and callers use them instead of provider SDKs or implementation helpers.

```python
class WorkspaceAuthorizer(Protocol):
    def require(self, *, user_id: UUID, workspace_id: UUID, action: WorkspaceAction) -> WorkspaceAccess: ...

class AssetIngestor(Protocol):
    def ingest(self, *, project_id: UUID, source_asset_id: UUID, job_id: UUID) -> IngestResult: ...

class SourceImporter(Protocol):
    def import_source(self, *, request: SourceImportRequest, workspace: Path, credential: SecretLease | None) -> StoredObject: ...

class Transcriber(Protocol):
    def transcribe(self, *, audio: StoredObject, language: str | None) -> TranscriptResult: ...

class HighlightAnalyzer(Protocol):
    def analyze(self, *, transcript: TranscriptDocument, target_count: int) -> list[ClipCandidateDraft]: ...

class ClipVariantGenerator(Protocol):
    def generate(self, *, candidate: ClipCandidate, platforms: list[Platform], durations_ms: list[int]) -> list[ClipVariantDraft]: ...

class BrollPlanner(Protocol):
    def plan(self, *, transcript: TranscriptDocument, candidate: ClipCandidate, coverage: BrollCoverage) -> list[VisualBeat]: ...

class BrollRetriever(Protocol):
    def search(self, *, intent: VisualIntent, limit: int) -> list[ExternalAssetCandidate]: ...

class GenerativeMediaProvider(Protocol):
    def estimate(self, *, request: GenerationRequest) -> GenerationEstimate: ...
    def submit(self, *, request: GenerationRequest, idempotency_key: str) -> GenerationHandle: ...
    def poll(self, *, handle: GenerationHandle) -> GenerationResult: ...

class SocialPublisher(Protocol):
    def capabilities(self, *, account: SocialAccount) -> PublishingCapabilities: ...
    def refresh_credentials(self, *, grant: OAuthGrantLease) -> RefreshedGrant: ...
    def preflight(self, *, publication: Publication, artifact: RenderArtifact) -> PreflightResult: ...
    def begin(self, *, publication: Publication, artifact: RenderArtifact) -> PublishCheckpoint: ...
    def transfer(self, *, checkpoint: PublishCheckpoint, artifact: RenderArtifact) -> PublishCheckpoint: ...
    def poll(self, *, checkpoint: PublishCheckpoint) -> ProviderPublicationStatus: ...
    def cancel(self, *, checkpoint: PublishCheckpoint) -> CancelResult: ...
    def disconnect(self, *, account: SocialAccount, grant: OAuthGrantLease) -> None: ...

class PublicationScheduler(Protocol):
    def claim_due(self, *, now: datetime, limit: int) -> list[UUID]: ...

class EditRepository(Protocol):
    def save_revision(self, *, edit_id: UUID, expected_revision: int, composition: CompositionV1, actor_id: UUID) -> SavedRevision: ...

class Renderer(Protocol):
    def render(self, *, composition: CompositionV1, preset: RenderPreset, workspace: Path) -> RenderResult: ...

class ObjectStore(Protocol):
    def create_multipart_upload(self, *, key: str, content_type: str) -> MultipartUpload: ...
    def sign_upload_part(self, *, upload_id: str, key: str, part_number: int) -> SignedUrl: ...
    def complete_multipart_upload(self, *, upload_id: str, key: str, parts: list[CompletedPart]) -> StoredObject: ...
    def sign_download(self, *, key: str, expires_in: timedelta) -> SignedUrl: ...
```

Production adapters are AssemblyAI, Groq, yt-dlp, Pexels, Pixabay, the configured generative-media provider, YouTube Data API, Instagram API with Instagram Login, TikTok Content Posting API, S3/R2, Postgres, Redis/Celery, and FFmpeg. Deterministic fake adapters are mandatory for unit tests. Provider-specific SDK/payload types stay inside adapters; provider-neutral domain models must not import Groq, AssemblyAI, Pexels, Pixabay, YouTube, Meta, TikTok, or generation-SDK types.

`SecretLease` exposes only a job-scoped materialization context manager. It must create a `0600` file inside the validated job workspace, return its path to the importer, redact it from process output, and delete it on context exit. Callers cannot read the credential as a Python string.

`OAuthGrantLease` exposes decrypted access/refresh tokens only to the selected social adapter for one bounded operation. Token refresh takes a row/advisory lock, persists any rotated refresh token atomically as `token_version + 1`, and returns reconnect-required on replay/revocation. Provider upload session URLs and signed pull URLs use separate encrypted checkpoint references and never enter ordinary JSON metadata.

---

## 7. Error, Security, and Privacy Rules

- Error response shape: `{"error":{"code":"ASSET_INVALID_CODEC","message":"...","requestId":"..."}}`.
- Map provider timeouts, throttling, invalid media, quota exhaustion, ownership failures, concurrency conflicts, cancellation, and render failures to distinct codes.
- Store detailed provider errors in structured logs and sanitized job metadata; never return credentials, signed URLs, filesystem paths, commands, or stack traces.
- Google Login Identities use OpenID Connect Authorization Code flow with PKCE S256, state, and nonce. The unique key is `(issuer, subject)`; equal email addresses never auto-link Users.
- Session cookies use the `__Host-clipah_session` name, `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, no Domain, explicit idle/absolute expiry, token rotation, and server-side revocation; Postgres stores only the SHA-256 hash of a random 256-bit token.
- Rotate Sessions after login, identity linking, role/ownership changes, suspicious activity, and recent-auth operations. Require authentication within ten minutes for ownership transfer, Workspace/User deletion, owner/admin promotion, Social Account connect/disconnect, and destination/visibility changes on scheduled Publications.
- Login OAuth, source-import authorization, and social-publishing authorization are separate ceremonies and credentials. Request least-privilege publishing scopes incrementally.
- State-changing requests require same-origin checks and CSRF tokens where cookies authenticate the request.
- Production CORS permits only the configured product origin. Local development permits only the configured local frontend origin.
- Upload keys are generated server-side. Client filenames are display metadata only.
- YouTube import permits HTTPS URLs whose normalized host is `youtube.com`, `www.youtube.com`, `m.youtube.com`, or `youtu.be`; DNS results resolving to private, loopback, link-local, multicast, or reserved addresses are rejected.
- The initial public release accepts direct upload and one public, non-live YouTube video only. Private, age-restricted, members-only, playlist, and authenticated imports remain behind `AUTHENTICATED_YOUTUBE_IMPORT_ENABLED=false` until product-specific legal review and the cookie security suite pass.
- Authenticated import requires an explicit ownership/authorization attestation, a consent screen describing session and account-ban risk, validation against the documented minimum YouTube-authentication cookie domain/name allowlist, encryption before persistence, a maximum seven-day connection lifetime, one-click revocation, and immediate deletion on account/project teardown.
- Reject cookie uploads containing non-YouTube authentication domains, malformed Netscape rows, more than 256 KiB, control characters, symlinks, or unexpected MIME types. Never support `--cookies-from-browser` in hosted workers; reserve it for documented local/self-hosted mode.
- Redact cookie paths, cookie content, PO Tokens, provider request authorization, and imported signed URLs from API responses, logs, traces, Sentry events, and job metadata.
- Require an independently reviewed feature flag before enabling a PO Token provider. Treat plugin output as untrusted, pin plugin revisions, and run it only in the import worker's restricted container.
- Rate limits: 60 read requests/minute/user, 20 write requests/minute/user, 3 new analyses/hour/free user, and 5 concurrent jobs/user. Store plan limits in configuration rather than route code.
- Media limits for the first production release: 2 GiB, 4 hours, one video stream, at least one audio stream, maximum 4K input, and supported codecs H.264/H.265/VP8/VP9/AV1 plus AAC/Opus/MP3 audio.
- Validate FFmpeg/ffprobe executable versions at worker startup.
- Store uploaded media under private object prefixes. All downloads use five-minute signed URLs.
- Apply safe-search and provider-license filters before persisting stock assets. Every external/generated asset must have complete provenance, moderation, source-type, and checksum metadata before it can enter a composition.
- Generated visuals remain `proposed` until an authorized owner/editor accepts them. Display an AI-generated indicator in the editor, preserve provider metadata internally, and block generation prompts that request disallowed impersonation, sexual content involving minors, or deceptive real-person claims.
- Enforce separate monthly stock-request, generated-image, generated-video, and generated-duration quotas. Return a price/usage estimate before accepting a generative-video job.
- Do not start a new OpenAI Sora API integration because its documented shutdown date is 2026-09-24; the generative adapter must be provider-neutral and initially target a separately approved provider.
- Social Accounts belong to a Workspace and record the authorizing User; encrypted OAuth Grants are separate records. Access/refresh tokens, authorization headers, resumable session URLs, Instagram pull URLs, TikTok upload URLs, webhook secrets, and raw signed URLs are forbidden in Postgres JSON, Redis payloads, logs, traces, Sentry, metrics, and user-facing errors.
- Social OAuth callbacks require exact redirect URIs, provider state validation, PKCE where supported, and authoritative account metadata. Disconnect revokes provider access where supported, cryptographically erases the local grant, marks the account revoked, and cancels every unpublished Publication.
- A webhook must verify signature and replay window before persistence, deduplicate by provider event ID or stable hash, return promptly, and let a worker reconcile status. Webhook requests never execute publication state transitions inline.
- Every Publication snapshots immutable artifact ID/hash, destination, metadata, privacy, disclosures, scheduled UTC time plus display timezone, approving User, and consent. No destination or privacy value may be default-selected silently.
- YouTube public/unlisted publishing remains disabled until the API project passes the required compliance audit. Instagram checks live `content_publishing_limit`; TikTok Direct Post remains disabled until scope approval/audit and the mandatory UX/security suite pass.
- TikTok publication preflight blocks Clipah promotional branding, links, or watermarks prohibited by the provider; provider capability changes return to `awaiting_approval` instead of silently changing privacy, interactions, commercial disclosure, or AI labeling.
- Default retention: abandoned multipart uploads 24 hours, failed-job workspace objects 7 days, source/proxy/export media until project deletion, soft-deleted projects recoverable for 30 days.
- Delete rejected generated drafts after 24 hours, unselected stock previews after 24 hours, and expired cookie connections immediately; retained accepted assets follow project retention.
- User deletion revokes Sessions immediately and removes Memberships but cannot orphan a Workspace. Workspace deletion disables new work, revokes Source/Social Connections, cancels unpublished work, and creates Workspace-scoped retention tombstones; externally published posts are not deleted automatically.

---

## 8. Analysis and Ranking Specification

1. Normalize the persisted transcript into punctuation-aware windows of 120-180 seconds with 20-second overlap.
2. Exclude silence-only windows and windows below the configured word count.
3. Route transcription explicitly: requested/detected Indonesian uses AssemblyAI Universal-2; only languages supported by Universal-3 Pro may select it. Preserve the selected provider/model/version in the transcript.
4. Ask Groq `openai/gpt-oss-20b` for strict-schema candidates containing hook, payoff, reason, category, tags, start/end word IDs, transcript excerpt, context dependencies, context warnings, visual opportunities, and score breakdown.
5. Resolve word IDs to authoritative timestamps from the transcript. Reject model-supplied free-form timestamps.
6. Require candidate duration between 20 and 90 seconds for the initial preset.
7. Deduplicate candidates when temporal intersection-over-union is at least 0.65 or normalized excerpts have cosine similarity at least 0.90.
8. Globally rerank the surviving candidates with the configured quality model, initially Groq `openai/gpt-oss-120b`, using narrative completeness, context safety, hook strength, payoff, clarity without external context, emotional/informational value, transcript confidence, platform fit, and visual opportunity.
9. Context safety must identify a cut-off question/payoff, missing negation, missing attribution, unsupported pronoun/reference, omitted caveat, material meaning change, or high-confidence claim that needs a source overlay. Warnings are visible and never silently discarded by reranking.
10. Return the top 10 by default and keep up to 30 internal candidates for later reranking.
11. Generate optional variants only from authoritative word boundaries: at most three hook strategies, the requested 20/30/45/60/90-second targets that preserve a complete thought, and TikTok/Reels/Shorts metadata. A variant that fails context safety is rejected.
12. Store prompt version, provider, model, provider request ID, latency, score breakdown, schema version, and token/usage/cost information.
13. Keep model IDs in settings and support a fallback adapter. A model deprecation check must fail staging readiness when a configured model is past its provider shutdown date.
14. Create versioned evaluation fixture sets in Indonesian, English, and code-switched speech. Measure word error rate, named-entity accuracy, timestamp drift, diarization error, timestamp validity, duration validity, duplicate rate, context-safety recall, complete-thought human rating, top-three acceptance rate, p95 provider latency, failure rate, and cost per source hour.
15. Retain AssemblyAI unless a 2-5 hour labeled benchmark proves a material alternative advantage. Benchmark Deepgram Nova-3 and optionally WhisperX; OpenAI diarized transcription is not a drop-in karaoke source unless it provides required word timestamps.

---

## 9. Editor and Rendering Specification

The editor must support the following capabilities before the plan is considered complete:

- Assets panel and import flow.
- B-roll suggestions panel with source, author/license, relevance, placement reason, confidence, alternatives, and accept/replace/remove/generate controls.
- Video, image, sound, extracted-audio, text, and caption items.
- Timeline ruler, playhead, zoom, snapping, bookmarks, track selection, item selection, drag, resize, split-left, split-right, duplicate, delete, and ripple editing.
- Scene organization with speaker labels.
- Source mark-in/mark-out and add-to-timeline.
- Undo/redo implemented with reversible patches.
- Autosave with a visible `Saving`, `Saved`, `Offline`, or `Conflict` state.
- Preview scaling, fit controls, play/pause, timecode entry, and fullscreen.
- Text family, size, color, alignment, weight, style, decoration, letter spacing, line height, and background.
- Caption word editing and karaoke highlighting without changing authoritative source timestamps unless timing is explicitly adjusted.
- Transform, opacity, blending, template, and motion controls with keyframes.
- Aspect presets 9:16, 16:9, 1:1, and 4:5.
- Optional face/active-speaker crop suggestions that users can override.
- Coverage presets `minimal`, `balanced`, and `dynamic`; deterministic placement rules prevent suggestions from covering the first hook reveal, a punchline, an on-screen demonstration, or an emotionally important pause.
- Accessibility warnings for caption reading speed, contrast, safe-zone overflow, missing audio description opportunity, and text hidden by platform UI.
- Export presets for 1080x1920, 1920x1080, 1080x1080, and 1080x1350 H.264/AAC MP4.

Preview and final render must consume the same normalized composition schema. Golden-frame tests compare representative preview frames with FFmpeg output using a documented perceptual-difference threshold.

### Editor-engine adoption gate

The product must not commit to a browser engine before a time-boxed bake-off compares:

1. Elah under Apache-2.0 as the preferred embeddable engine.
2. OpenReel's MIT architecture and its Mediabunny-based approach as the fallback/reference.
3. OpenVideo Editor only as a benchmark when its commercial license is acceptable; Remotion may be evaluated for template rendering but is not treated as a complete editor.

The same fixture composition must pass trim, split, caption edit/karaoke, 9:16 crop, undo/redo serialization, one-hour proxy playback, signed URLs, waveform, Safari degradation, memory, local preview, native FFmpeg export, and frame/timing-parity tests. Record bundle size, long-timeline memory, seek latency, browser support, maintenance activity, license obligations, API surface, and escape cost in an ADR. If no candidate passes, implement the narrow missing browser primitives over Clipah's composition schema; do not fork an entire editor application.

### B-roll planning and placement rules

1. Segment a candidate into semantic beats using authoritative word IDs and speaker turns.
2. Mark only beats whose meaning benefits from a visual; dialogue does not require continuous B-roll.
3. Produce a strict `VisualIntent` containing concrete subject, action, setting, mood, portrait suitability, search terms in Indonesian and English, exclusions, and factual-risk flags.
4. Search accepted user assets first, then Pexels/Pixabay stock adapters with safe-search, orientation, duration, and license filters.
5. Cache provider search results according to provider terms, download only selected assets into private storage, and never rely on permanent hotlinks.
6. Rerank candidates using semantic relevance, sampled visual relevance, technical quality, portrait framing, brand constraints, repetition penalty, and factual/cultural fit.
7. Default placement length is 2-5 seconds. Cap `minimal` at one suggestion per 15 seconds, `balanced` at one per 8-12 seconds, and `dynamic` at one per 5-8 seconds while respecting scene/sentence boundaries.
8. Suggestions preserve dialogue audio, avoid critical face reveals/punchlines/demonstrations, and remain editable timeline items.
9. If retrieval confidence is below the configured threshold, offer generated still plus pan/zoom before generated video. Generated video requires a separate explicit confirmation after cost estimation.
10. Measure suggestion acceptance, rejection, replacement, removal-after-export, coverage, relevance, generation cost per exported minute, p95 latency, and render failure rate by source type.

### Product information architecture

- `/`: public landing page.
- `/signin`: authentication entry and callback recovery.
- `/demo`: sanitized read-only example project.
- `/dashboard`: overview, active jobs, usage, recent projects, and top candidates.
- `/dashboard/projects`: project library.
- `/dashboard/projects/[projectId]`: source, transcript, processing timeline, candidates, assets, and exports.
- `/dashboard/clips`: cross-project clip and variant library.
- `/dashboard/clips/[clipId]`: clip detail, edit versions, B-roll decisions, campaign outputs, and exports.
- `/editor/[editId]`: distraction-free editor.
- `/dashboard/assets`: user uploads, stock, generated assets, provenance, and usage.
- `/dashboard/templates`: reusable caption, layout, and motion templates.
- `/dashboard/brand-kits`: fonts, colors, logos, caption rules, exclusions, and claim rules.
- `/dashboard/team`: members, roles, review/approval activity; launch may keep this route feature-flagged for single-user accounts.
- `/dashboard/publishing`: calendar/list of publication drafts, schedules, active transfers, partial failures, and published permalinks.
- `/dashboard/publishing/[batchId]`: one confirmation batch with independent destination states and actions.
- `/dashboard/settings/connections`: Workspace Source/Social Accounts, scopes, health, expiry, authorized-by actor, refresh, and disconnect actions.
- `/dashboard/settings`: Workspace selector/profile, personal profile, language, retention, export defaults, publishing role policy, and quota visibility.

Navigation reflects domain state rather than process screens. Every long-running job appears in a global job center and remains observable after navigation or refresh.

---

## 10. Delivery Phases and Exit Gates

### Phase A: Safety foundation

Tasks 1-9. Exit when personal/team Workspaces, isolated projects, authentication, object storage, RLS, and durable jobs work without invoking AI or rendering.

### Phase B: Durable media and AI pipeline

Tasks 10-16. Exit when a fixture video becomes ranked candidates without rendering final clips and retries do not duplicate records or artifacts.

### Phase C: Product dashboard and basic editor

Tasks 17-23. Exit when members can navigate the full Workspace/project shell, upload, review candidates, complete the editor-engine bake-off, trim/crop/style captions, and autosave one edit.

### Phase D: Advanced editor parity

Tasks 24-26. Exit when all core editor capabilities in Section 9 are represented in the composition schema, preview, and render compiler.

### Phase E: Source connections, B-roll, and differentiated product workflows

Tasks 27-35. Exit when authenticated source connections are safe and feature-flagged, stock-first B-roll suggestions are editable and provenance-aware, generative fallback requires confirmation, and context-safe variants, brand kits, campaign outputs, search, accessibility, and review workflows pass their acceptance gates.

### Phase F: Workspace social publishing

Tasks 36-43. Exit when Workspace-scoped Social Accounts have secure grant lifecycles, provider renditions and preflight are deterministic, YouTube/Instagram/TikTok adapters pass contract gates, immediate/scheduled independent Publications survive failure/retry/revocation, and the publishing composer/history satisfy every provider's required UX.

### Phase G: Production hardening and cutover

Tasks 44-48. Exit when security, observability, retention, deployment, migration, load, recovery, and acceptance gates pass across ingestion, editing, generation, and social publishing.

---

## 11. Implementation Tasks

### Task 1: Create the locked backend workspace and quality gates

**Files:**
- Create: `.python-version`
- Create: `backend/pyproject.toml`
- Create: `backend/uv.lock`
- Create: `backend/src/clipah/__init__.py`
- Create: `backend/src/clipah/config.py`
- Create: `backend/tests/unit/test_config.py`
- Create: `backend/tests/conftest.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `Settings`, loaded only from environment variables and `.env` in local development.

- [ ] Write `test_config.py` asserting production mode rejects missing database, Redis, object-store, Google OIDC, transcription, model-provider, session-secret, encryption/Secret Manager, and enabled social-provider settings while permitting unapproved social integrations to remain explicitly disabled.
- [ ] Run `cd backend && uv run pytest tests/unit/test_config.py -v`; expect failure because `Settings` does not exist.
- [ ] Add Python 3.13, FastAPI, Pydantic Settings, SQLAlchemy, psycopg, Alembic, Celery, Redis, boto3, Authlib, HTTPX, cryptography/KMS client, Google API client, the current AssemblyAI 1.x SDK, native Groq SDK, python-magic, structlog, OpenTelemetry, Sentry, pytest, pytest-cov, pytest-timeout, Ruff, and mypy dependencies with exact lockfile resolutions.
- [ ] Implement `Settings` with `local`, `test`, `staging`, and `production` profiles; production defaults disable debug, require `__Host-` HTTPS cookies and secret encryption, default authenticated source import/generative video/social publishing to disabled, configure separate YouTube/Instagram/TikTok OAuth/API settings and audit flags, configure GPT-OSS aliases, and reject retired model/API configurations.
- [ ] Configure Ruff formatting/linting, mypy strict mode for `src/clipah`, pytest markers, and 90% coverage for new backend modules.
- [ ] Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`, and `uv run pytest`; expect all commands to pass.
- [ ] Commit with `chore: initialize locked backend workspace`.

### Task 2: Establish the FastAPI application and stable error contract

**Files:**
- Create: `backend/src/clipah/api/app.py`
- Create: `backend/src/clipah/api/errors.py`
- Create: `backend/src/clipah/api/request_id.py`
- Create: `backend/tests/contract/test_health_and_errors.py`

**Interfaces:**
- Produces: `create_app(settings: Settings) -> FastAPI`, `ApiError`, and the error shape from Section 7.

- [ ] Add contract tests for `/health/live`, `/health/ready`, request ID propagation, unknown-route errors, validation errors, and sanitized internal errors.
- [ ] Run `uv run pytest tests/contract/test_health_and_errors.py -v`; expect route/import failures.
- [ ] Implement the application factory, request-ID middleware, exception mapping, version metadata, and JSON response schemas.
- [ ] Make readiness call injected database, Redis, and object-store probes with a 2-second timeout per dependency; liveness must not call external dependencies.
- [ ] Run the contract tests and the full backend quality commands; expect success.
- [ ] Commit with `feat: establish typed backend application`.

### Task 3: Add Postgres persistence and the initial schema

**Files:**
- Create: `backend/src/clipah/db.py`
- Create: `backend/src/clipah/models.py`
- Create: `backend/alembic.ini`
- Create: `backend/migrations/env.py`
- Create: `backend/migrations/versions/0001_initial_schema.py`
- Create: `backend/tests/integration/test_schema.py`
- Create: `infra/compose.yaml`

**Interfaces:**
- Produces: `session_scope()`, transaction-scoped Workspace/User RLS context, foundational SQLAlchemy models for Users, Login Identities, Sessions, Workspaces, Memberships/Invites, Projects, Assets, uploads, Jobs/events, transcripts, candidates, edits/revisions, render artifacts, audit/provider usage, retention tombstones, and an Alembic upgrade/downgrade path. Feature-specific B-roll, campaign, and social-publishing tables are added by their numbered tasks.

- [ ] Start Postgres, Redis, and MinIO through `docker compose -f infra/compose.yaml up -d postgres redis minio`.
- [ ] Write integration tests for UUID keys, Google `(issuer, subject)` uniqueness, automatic personal Workspace creation, Membership keys/roles, composite Workspace foreign keys, tenant-leading indexes, RLS default-deny, non-bypass API/worker roles, unique idempotency keys, edit revision uniqueness, render artifact deduplication, and migration downgrade/upgrade.
- [ ] Run `uv run pytest tests/integration/test_schema.py -v`; expect missing models/migrations.
- [ ] Implement the foundational SQLAlchemy models using UTC-aware timestamps, explicit enums, JSONB only for genuinely dynamic metadata, composite `(workspace_id, id)` constraints, append-only audit rules, check constraints for progress/time ranges, and cascades that preserve audit records until retention runs.
- [ ] Generate and hand-review migration `0001_initial_schema.py`; ensure downgrade drops objects in reverse dependency order.
- [ ] Run `uv run alembic upgrade head`, integration tests, `uv run alembic downgrade base`, and `uv run alembic upgrade head`; expect all steps to succeed.
- [ ] Commit with `feat: add durable domain schema`.

### Task 4: Implement Login Identities, Sessions, Workspaces, and authorization dependencies

**Files:**
- Create: `backend/src/clipah/auth/models.py`
- Create: `backend/src/clipah/auth/identities.py`
- Create: `backend/src/clipah/auth/google_oidc.py`
- Create: `backend/src/clipah/auth/sessions.py`
- Create: `backend/src/clipah/workspaces/models.py`
- Create: `backend/src/clipah/workspaces/use_cases.py`
- Create: `backend/src/clipah/workspaces/authorization.py`
- Create: `backend/src/clipah/api/dependencies.py`
- Create: `backend/src/clipah/api/routes/auth.py`
- Create: `backend/src/clipah/api/routes/workspaces.py`
- Create: `backend/tests/integration/test_auth.py`
- Create: `backend/tests/contract/test_ownership.py`

**Interfaces:**
- Produces: `CurrentUser`, `CurrentWorkspace`, `require_user()`, `WorkspaceAuthorizer.require(...)`, personal Workspace bootstrap, Session/recent-auth use cases, and auth/Workspace endpoints in Section 5.

- [ ] Write tests using a fake OIDC profile for issuer/audience/nonce/state/PKCE S256 validation, `(issuer, subject)` User resolution, refusal to auto-link equal emails, personal Workspace bootstrap, Session rotation, `__Host-clipah_session` flags, idle/absolute expiry, logout/revoke-all, recent-auth, CSRF rejection, and disabled-user rejection.
- [ ] Write authorization tests proving a non-member receives the same 404 for guessed Workspace/Project/Job/Edit/Render UUIDs as for nonexistent UUIDs and every request/worker transaction sets the expected Workspace RLS context.
- [ ] Run the auth and ownership suites; expect missing-route failures.
- [ ] Implement Google OIDC Authorization Code flow with Authlib and an injected fake provider; persist Login Identity separately from the User profile and never use email as the authentication key.
- [ ] Generate 32-byte opaque Session tokens, store only SHA-256 hashes, rotate after callback/identity/privilege changes, track idle and absolute expiry, and require ten-minute recent authentication for sensitive operations.
- [ ] Implement personal/team Workspace creation, Workspace selection, and the `owner`, `admin`, `editor`, `reviewer`, `viewer` authorization matrix with a configurable publishing-role policy.
- [ ] Add same-origin and double-submit CSRF validation to state-changing cookie-authenticated requests.
- [ ] Run the focused tests and full backend suite; expect success.
- [ ] Commit with `feat: secure workspace identities and sessions`.

### Task 5: Implement project use cases and idempotent create/update/delete routes

**Files:**
- Create: `backend/src/clipah/projects/schemas.py`
- Create: `backend/src/clipah/projects/repository.py`
- Create: `backend/src/clipah/projects/use_cases.py`
- Create: `backend/src/clipah/api/routes/projects.py`
- Create: `backend/tests/integration/test_projects.py`

**Interfaces:**
- Produces: `create_project`, `list_projects`, `get_project`, `rename_project`, `soft_delete_project`, and `restore_project`.

- [ ] Write tests for Workspace-scoped project creation, role permission, name normalization, tenant pagination, idempotency-key replay within a Workspace, payload mismatch conflict, rename, soft deletion, restore within 30 days, and rejection of processing requests for archived/deleted Workspaces/Projects.
- [ ] Run `uv run pytest tests/integration/test_projects.py -v`; expect failure.
- [ ] Implement the repository and use cases; keep SQLAlchemy details private to the module.
- [ ] Return cursor pagination ordered by `(created_at, id)` inside the authorized Workspace and exclude soft-deleted Projects by default.
- [ ] Run focused and full tests; expect success.
- [ ] Commit with `feat: add workspace-scoped projects`.

### Task 6: Implement the S3-compatible object-store module and multipart uploads

**Files:**
- Create: `backend/src/clipah/assets/storage.py`
- Create: `backend/src/clipah/assets/keys.py`
- Create: `backend/src/clipah/assets/uploads.py`
- Create: `backend/src/clipah/api/routes/uploads.py`
- Create: `backend/tests/unit/test_storage_keys.py`
- Create: `backend/tests/integration/test_multipart_uploads.py`

**Interfaces:**
- Produces: the `ObjectStore` protocol in Section 6, `S3ObjectStore`, `FakeObjectStore`, and multipart endpoints in Section 5.

- [ ] Write key tests proving client filenames never enter storage keys and every tenant object begins with `workspaces/{workspace_id}/projects/{project_id}/`.
- [ ] Write MinIO integration tests for create, part signing, complete, abort, expiry, wrong Workspace, invalid part number, duplicate completion, and five-minute download URLs.
- [ ] Run focused tests; expect missing-module failures.
- [ ] Implement storage keys, boto3 adapter, fake adapter, database upload record transitions, and cleanup on aborted or expired uploads.
- [ ] Enforce content length at upload creation and again from object metadata after completion.
- [ ] Run focused and full backend suites; expect success.
- [ ] Commit with `feat: add isolated multipart media storage`.

### Task 7: Add backend rate limits, quotas, and concurrent-job admission

**Files:**
- Create: `backend/src/clipah/auth/limits.py`
- Create: `backend/src/clipah/jobs/admission.py`
- Create: `backend/tests/integration/test_limits.py`

**Interfaces:**
- Produces: `RateLimiter.check(subject, bucket, limit, window)`, `JobAdmission.reserve(workspace_id, kind)`, Workspace-level quota reservation/reconciliation for analyses, stock requests, generated images/videos, generated seconds, and social publications, plus stable `RATE_LIMITED`, `QUOTA_EXCEEDED`, and `CONCURRENCY_LIMIT` errors.

- [ ] Write Redis/Postgres-backed tests for each limit in Section 7, retry-after headers, atomic concurrent reservations, Workspace stock/generation/publishing budgets, per-Social-Account provider limits, estimated-versus-actual cost reconciliation, release after terminal job/publication state, and independent Workspaces.
- [ ] Run the focused suite; expect failure.
- [ ] Implement a Redis Lua sliding-window limiter and a Postgres transaction that counts active jobs before creating a new one.
- [ ] Apply read/write middleware limits and analysis/concurrency limits to the relevant use cases.
- [ ] Run focused tests and a 50-request concurrency test; expect exact configured acceptance/rejection counts.
- [ ] Commit with `feat: enforce processing limits`.

### Task 8: Implement durable jobs, events, cancellation, and Celery integration

**Files:**
- Create: `backend/src/clipah/celery_app.py`
- Create: `backend/src/clipah/jobs/models.py`
- Create: `backend/src/clipah/jobs/repository.py`
- Create: `backend/src/clipah/jobs/use_cases.py`
- Create: `backend/src/clipah/jobs/tasks.py`
- Create: `backend/src/clipah/api/routes/jobs.py`
- Create: `backend/tests/integration/test_jobs.py`
- Create: `backend/tests/contract/test_job_events.py`

**Interfaces:**
- Produces: `create_job`, `start_job`, `update_job_progress`, `succeed_job`, `fail_job`, `request_job_cancellation`, `JobContext.raise_if_cancelled()`, and job/SSE endpoints.

- [ ] Write state-machine tests rejecting invalid transitions and ensuring terminal events are emitted once.
- [ ] Write task tests proving Celery arguments contain only UUID strings, include no ORM/session/token objects, reload and authorize the Workspace context, reuse the same job on retry, and expose cancel requests between pipeline stages.
- [ ] Write SSE tests for ordered sequence replay using `Last-Event-ID`, heartbeat frames, Workspace membership checks, cross-Workspace denial, and terminal stream closure.
- [ ] Run focused suites; expect failure.
- [ ] Implement Postgres-backed state transitions with row locking, append-only job events, Redis pub/sub wakeups, polling fallback, and Celery automatic retry with exponential backoff and jitter for recoverable provider/network errors.
- [ ] Route tasks to `source_import`, `ingest`, `ai`, `broll_retrieve`, `broll_generate`, `render`, `social_rendition`, `social_publish`, `social_reconcile`, and `maintenance` queues; keep provider-specific concurrency and admission settings outside task code.
- [ ] Run focused/full tests and a Celery eager-mode integration test; expect success.
- [ ] Commit with `feat: add durable background jobs`.

### Task 9: Replace shared working files with secure per-job workspaces

**Files:**
- Create: `backend/src/clipah/jobs/workspace.py`
- Create: `backend/tests/unit/test_workspace.py`

**Interfaces:**
- Produces: `job_workspace(job_id) -> ContextManager[Path]`.

- [ ] Write tests proving workspace paths are under one configured root, are UUID-scoped, reject symlinks escaping the root, survive nested exceptions long enough to collect diagnostics, and are removed after context exit.
- [ ] Run the focused test; expect failure.
- [ ] Implement workspaces with `tempfile.mkdtemp`, restrictive permissions, path containment checks, and best-effort cleanup that never targets a parent directory.
- [ ] Add an integration test running two workspaces concurrently and asserting filenames cannot collide.
- [ ] Run focused/full tests; expect success.
- [ ] Commit with `fix: isolate media workspaces by job`.

### Task 10: Implement safe YouTube imports and source validation

**Files:**
- Create: `backend/src/clipah/assets/youtube.py`
- Create: `backend/src/clipah/assets/source_validation.py`
- Create: `backend/src/clipah/source_connectors/yt_dlp_adapter.py`
- Create: `backend/src/clipah/api/routes/youtube_imports.py`
- Create: `backend/tests/unit/test_source_validation.py`
- Create: `backend/tests/integration/test_youtube_imports.py`

**Interfaces:**
- Produces: `validate_youtube_url(url) -> NormalizedYouTubeUrl` and a public-only `YtDlpSourceImporter.import_source(...) -> StoredObject`.

- [ ] Write table tests for allowed hosts, deceptive suffixes, userinfo, non-HTTPS URLs, redirects to disallowed hosts, IPv4/IPv6 private ranges, rebinding between validation and download, playlists, live streams, private videos, and age-restricted videos.
- [ ] Run focused tests; expect failure.
- [ ] Implement URL normalization, DNS/IP classification, redirect revalidation, duration preflight, and yt-dlp invocation without certificate bypass, browser-profile access, account cookies, or shared cookie files.
- [ ] Restrict the first release to one public non-live video and return explicit `SOURCE_UNSUPPORTED`, `SOURCE_PRIVATE`, `SOURCE_TOO_LONG`, or `SOURCE_TLS_FAILED` codes.
- [ ] Pin yt-dlp, yt-dlp-ejs, Deno, and FFmpeg in the import image; run `yt-dlp --version`, EJS extraction, Deno execution, and public-video metadata probes at worker readiness. Keep this image independently releasable so YouTube breakage does not redeploy the API or render worker.
- [ ] Define an optional `PoTokenProvider` port but ship no enabled production adapter until Task 27's feature flag, plugin pin, isolation, tests, and legal gate are satisfied.
- [ ] Add a fake yt-dlp adapter for deterministic tests and one opt-in network smoke test excluded from CI.
- [ ] Run focused/full tests; expect success.
- [ ] Commit with `fix: secure remote video imports`.

### Task 11: Implement ffprobe validation, proxy generation, and ingest orchestration

**Files:**
- Create: `backend/src/clipah/assets/probe.py`
- Create: `backend/src/clipah/assets/ffmpeg.py`
- Create: `backend/src/clipah/assets/ingest.py`
- Create: `backend/src/clipah/jobs/ingest_task.py`
- Create: `backend/tests/unit/test_probe_validation.py`
- Create: `backend/tests/integration/test_ingest_pipeline.py`
- Add: `backend/tests/fixtures/media/` small generated audio/video fixtures and their generation script.

**Interfaces:**
- Produces: `AssetIngestor.ingest(...) -> IngestResult` containing source metadata, proxy asset, thumbnail asset, and transcription-audio asset.

- [ ] Generate deterministic fixtures for valid landscape/portrait MP4, no-audio video, audio-only input, corrupt bytes, oversized metadata, unsupported codec, and variable-frame-rate video.
- [ ] Write tests for all media limits in Section 7 and exact error codes.
- [ ] Run focused tests; expect failure.
- [ ] Implement libmagic MIME sniffing, declared/sniffed MIME comparison, `ffprobe` JSON parsing, stream validation, SHA-256 calculation while downloading, proxy generation at max 720p, JPEG thumbnail generation, and mono 16 kHz transcription audio.
- [ ] Invoke FFmpeg with argument arrays, explicit timeouts, process-group cancellation, bounded stderr capture, and `-progress pipe:1` progress parsing.
- [ ] Upload completed artifacts to deterministic asset keys, persist their metadata transactionally, and reuse artifacts on idempotent retry.
- [ ] Run focused/full tests and inspect generated artifacts with ffprobe; expect success.
- [ ] Commit with `feat: add durable media ingest pipeline`.

### Task 12: Implement one-pass transcription with real diarization

**Files:**
- Create: `backend/src/clipah/transcripts/models.py`
- Create: `backend/src/clipah/transcripts/provider.py`
- Create: `backend/src/clipah/transcripts/assemblyai_adapter.py`
- Create: `backend/src/clipah/transcripts/use_cases.py`
- Create: `backend/src/clipah/jobs/transcribe_task.py`
- Create: `backend/tests/unit/test_transcript_normalization.py`
- Create: `backend/tests/integration/test_transcription.py`

**Interfaces:**
- Produces: `Transcriber.transcribe(...) -> TranscriptResult` with word IDs, millisecond timestamps, confidence, punctuation, and provider speaker labels.

- [ ] Write normalization tests for English, Indonesian, speaker changes, missing punctuation, overlapping words, invalid timestamp order, provider retry, and empty audio.
- [ ] Run focused tests; expect failure.
- [ ] Migrate from the legacy AssemblyAI SDK interface to the locked 1.x SDK, enable word timestamps and speaker labels, route Indonesian to Universal-2, route only supported languages to Universal-3 Pro, and remove the LLM-based diarization behavior entirely.
- [ ] Persist utterances and per-word speaker/confidence data; reject provider results whose words fall outside the source duration or regress in time.
- [ ] Store one transcript JSON document per source asset with provider/model/language metadata and a unique asset constraint.
- [ ] Add idempotency tests proving repeated tasks do not call the provider after a valid transcript exists.
- [ ] Add a fake transcriber fixture and an opt-in provider contract smoke test excluded from CI.
- [ ] Run focused/full tests; expect success.
- [ ] Commit with `feat: persist diarized word transcripts`.

### Task 13: Implement transcript windowing and candidate extraction schemas

**Files:**
- Create: `backend/src/clipah/highlights/models.py`
- Create: `backend/src/clipah/highlights/windowing.py`
- Create: `backend/src/clipah/highlights/extractor.py`
- Create: `backend/tests/unit/test_windowing.py`
- Create: `backend/tests/unit/test_candidate_validation.py`

**Interfaces:**
- Produces: `build_windows(transcript) -> list[TranscriptWindow]` and validated `ClipCandidateDraft` objects keyed by transcript word IDs.

- [ ] Write window tests for 120-180-second targets, 20-second overlap, sentence/speaker-aware boundaries, short transcripts, silence gaps, and full word coverage without duplicate word IDs inside one window.
- [ ] Write candidate tests rejecting unknown word IDs, reversed ranges, 19-second/91-second durations, excerpts inconsistent with authoritative words, invalid scores, and unknown categories.
- [ ] Run focused tests; expect failure.
- [ ] Implement pure windowing and Pydantic candidate schemas; keep provider calls out of this module.
- [ ] Run focused/full tests; expect success.
- [ ] Commit with `feat: define timestamp-safe highlight candidates`.

### Task 14: Implement structured LLM extraction, deduplication, and global reranking

**Files:**
- Create: `backend/src/clipah/highlights/provider.py`
- Create: `backend/src/clipah/highlights/groq_adapter.py`
- Create: `backend/src/clipah/highlights/provider_router.py`
- Create: `backend/src/clipah/highlights/deduplicate.py`
- Create: `backend/src/clipah/highlights/rerank.py`
- Create: `backend/src/clipah/highlights/analyzer.py`
- Create: `backend/src/clipah/jobs/analyze_task.py`
- Create: `backend/tests/unit/test_deduplicate.py`
- Create: `backend/tests/unit/test_rerank.py`
- Create: `backend/tests/integration/test_highlight_analysis.py`

**Interfaces:**
- Produces: `HighlightAnalyzer.analyze(...) -> list[ClipCandidateDraft]` ordered by stable rank.

- [ ] Write deterministic tests for temporal IoU 0.65, excerpt similarity 0.90, score tie-breaking, top-10 selection, provider malformed output, context-window overflow, rate limiting, and retry.
- [ ] Run focused tests; expect failure.
- [ ] Implement a typed Groq adapter using strict JSON Schema with every field required and `additionalProperties: false`; use configured `openai/gpt-oss-20b` for window extraction and `openai/gpt-oss-120b` for global reranking.
- [ ] Add a provider router and deterministic fallback adapter. Reject retired/unknown model aliases at startup, and keep strict-schema, retry, timeout, and usage normalization inside the adapter.
- [ ] Resolve model word IDs to timestamps locally, deduplicate candidates, globally rerank, and persist up to 30 candidates while exposing 10 by default.
- [ ] Record prompt version, provider/model, provider request ID, latency, usage, and score breakdown.
- [ ] Ensure one failed window records an event and allows remaining windows to complete; fail the analysis only when fewer than three valid candidates remain.
- [ ] Run focused/full tests; expect success.
- [ ] Commit with `feat: rank complete transcript moments`.

### Task 15: Add the versioned highlight evaluation harness

**Files:**
- Create: `backend/evals/highlights/manifest.json`
- Create: `backend/evals/highlights/cases/` sanitized English and Indonesian transcript fixtures.
- Create: `backend/evals/transcription/manifest.json`
- Create: `backend/evals/transcription/cases/` sanitized Indonesian, English, and code-switched labeled fixtures.
- Create: `backend/evals/transcription/adapters/assemblyai.py`
- Create: `backend/evals/transcription/adapters/deepgram.py`
- Create: `backend/evals/transcription/adapters/whisperx.py`
- Create: `backend/src/clipah/highlights/evaluation.py`
- Create: `backend/src/clipah/transcripts/evaluation.py`
- Create: `backend/tests/unit/test_evaluation_metrics.py`
- Create: `scripts/run-highlight-eval.sh`
- Create: `scripts/run-transcription-eval.sh`

**Interfaces:**
- Produces: versioned JSON evaluation reports for transcription quality, speaker/timestamp accuracy, highlight validity, context safety, cost, and top-three acceptance.

- [ ] Add at least five English, five Indonesian, and five code-switched fixtures with labeled words/speakers, named entities, human-approved clip spans, context warnings, and explicit acceptable-overlap ranges; the accumulated source audio must cover at least two hours before provider selection is frozen and grow toward five hours before public launch.
- [ ] Write metric tests using synthetic perfect, duplicate-heavy, invalid-timestamp, and missed-candidate result sets.
- [ ] Run metric tests; expect failure before implementation.
- [ ] Implement offline metrics and a provider mode that writes model/provider/prompt versions into the report.
- [ ] Add AssemblyAI, Deepgram Nova-3, optional WhisperX, and fake transcription adapters to the offline/live evaluation runner; collect WER, named-entity accuracy, median/p95 word-timestamp drift, diarization error, p95 completion time, failure rate, and cost per source hour.
- [ ] Set initial highlight gates: 100% timestamp validity, 100% duration validity, duplicate rate below 10%, context-safety recall at or above 90% on labeled risky cuts, and top-three acceptance at or above 70% on the checked-in set. Keep AssemblyAI unless a challenger materially improves the weighted score without violating timestamp/diarization gates.
- [ ] Run `scripts/run-highlight-eval.sh --adapter=fake`; expect deterministic pass. Record live-provider reports as CI artifacts without making live calls mandatory.
- [ ] Run `scripts/run-transcription-eval.sh --adapter=fake`; expect deterministic pass. Live AssemblyAI/Deepgram/WhisperX runs require explicit credentials/runtime flags and write comparable versioned reports without becoming ordinary CI dependencies.
- [ ] Commit with `test: add highlight quality evaluation`.

### Task 16: Expose analysis and ranked candidate endpoints

**Files:**
- Create: `backend/src/clipah/api/routes/analysis.py`
- Create: `backend/src/clipah/api/routes/candidates.py`
- Create: `backend/tests/contract/test_analysis_api.py`
- Create: `backend/tests/contract/test_candidates_api.py`

**Interfaces:**
- Produces: analysis and candidate endpoints in Section 5.

- [ ] Write contract tests for precondition states, idempotency, quota checks, job creation, candidate pagination, owner scoping, score/reason/category fields, and absence of storage keys/provider raw output.
- [ ] Run focused tests; expect failure.
- [ ] Implement route schemas and use-case calls without provider or SQL logic in route functions.
- [ ] Verify a fixture project reaches `ready` and candidates appear before any render artifact exists.
- [ ] Run focused/full tests; expect success.
- [ ] Commit with `feat: expose ranked clip candidates`.

### Task 17: Move the product UI into one clean Next.js frontend

**Files:**
- Move/rebuild: current `app/`, `components/`, `hooks/`, `styles/`, and frontend dependencies under `frontend/`.
- Create: `frontend/app/layout.tsx`
- Create: `frontend/app/page.tsx`
- Create: `frontend/app/dashboard/page.tsx`
- Create: `frontend/lib/api/client.ts`
- Create: `frontend/lib/api/generated/` from `contracts/openapi.json`.
- Create: `frontend/tests/smoke.test.tsx`
- Modify: root `package.json`, `README.md`, and deployment configuration.

**Interfaces:**
- Consumes: generated OpenAPI client.
- Produces: one Next.js product UI; the Flask template/static UI is no longer a production entry point.

- [ ] Export backend OpenAPI to `contracts/openapi.json` and generate a typed TypeScript client with Orval.
- [ ] Write smoke tests for landing page, authenticated dashboard shell, request-ID error rendering, strict boolean serialization for subtitle/watermark controls, and safe rendering of strings containing HTML tags.
- [ ] Run frontend unit tests; expect failure before the new app exists.
- [ ] Configure Next.js, TypeScript strict mode, ESLint, Vitest, Testing Library, TanStack Query, and a same-origin `/api` proxy to FastAPI.
- [ ] Rebuild the landing/dashboard shell using React text rendering; do not use `dangerouslySetInnerHTML` for provider/user content.
- [ ] Remove runtime dependencies on Tailwind CDN, unpkg Lucide, Flask templates, and `static/script.js`.
- [ ] Run `pnpm lint`, `pnpm typecheck`, `pnpm test`, and `pnpm build`; expect success.
- [ ] Commit with `refactor: make Next.js the product frontend`.

### Task 18: Build authentication and project dashboard UX

**Files:**
- Create: `frontend/features/auth/`
- Create: `frontend/features/workspaces/`
- Create: `frontend/features/projects/`
- Create: `frontend/features/jobs/JobCenter.tsx`
- Create: `frontend/app/signin/page.tsx`
- Create: `frontend/app/dashboard/layout.tsx`
- Create: `frontend/app/dashboard/page.tsx`
- Create: `frontend/app/dashboard/projects/page.tsx`
- Create: `frontend/app/dashboard/projects/[projectId]/page.tsx`
- Create: `frontend/app/dashboard/clips/page.tsx`
- Create: `frontend/app/dashboard/clips/[clipId]/page.tsx`
- Create: `frontend/app/dashboard/assets/page.tsx`
- Create: `frontend/app/dashboard/templates/page.tsx`
- Create: `frontend/app/dashboard/brand-kits/page.tsx`
- Create: `frontend/app/dashboard/team/page.tsx`
- Create: `frontend/app/dashboard/settings/page.tsx`
- Create: `frontend/app/dashboard/settings/connections/page.tsx`
- Create: `frontend/tests/projects.test.tsx`
- Create: `frontend/e2e/auth-projects.spec.ts`

**Interfaces:**
- Consumes: `/me`, Workspace/membership, project, session, and job endpoints.
- Produces: sign-in redirect handling, personal-Workspace bootstrap, Workspace switcher, the complete route shell from Section 9, project list/create/rename/delete, a persistent Workspace-scoped job center, processing states, the settings/connections route shell completed in Task 43, and a read-only demo route.

- [ ] Write route/component tests for signed-out, loading, empty, populated, archived, error, membership-safe 404, Workspace switching, responsive navigation, active-route state, job-center persistence after navigation, and role-gated controls that never expose inaccessible actions.
- [ ] Write Playwright tests using seeded sessions for personal-Workspace bootstrap, Workspace switch, project create/rename/soft-delete/recovery, member removal, and inability to access another Workspace's project URL.
- [ ] Implement the Workspace/project dashboard information architecture from Section 9 with accessible navigation, cursor pagination, optimistic rename, explicit processing status labels, overview cards backed by `/dashboard/summary`, and no duplicated authoritative state in the browser.
- [ ] Implement a global job center subscribed to Workspace-scoped SSE streams; reconnect using `Last-Event-ID`, discard data when the active Workspace changes, preserve terminal history for that Workspace/session, and deep-link each job to its project or edit.
- [ ] Implement `/demo` with bundled sanitized project data and no private endpoint access.
- [ ] Run frontend unit/E2E tests; expect success.
- [ ] Commit with `feat: add secure project dashboard`.

### Task 19: Build resumable upload and safe YouTube import UX

**Files:**
- Create: `frontend/features/uploads/uploader.ts`
- Create: `frontend/features/uploads/UploadPanel.tsx`
- Create: `frontend/features/uploads/YouTubeImportForm.tsx`
- Create: `frontend/tests/uploads.test.tsx`
- Create: `frontend/e2e/upload-analysis.spec.ts`

**Interfaces:**
- Consumes: multipart upload, YouTube import, analysis, job, and SSE endpoints.
- Produces: resumable uploads, import validation, progress, cancel/retry, and analysis start.

- [ ] Write tests for part retry, browser refresh resume, cancel/abort, size rejection, backend media rejection, invalid YouTube host, public/private/age-restricted import errors, authorization-policy copy, SSE reconnect, job cancellation, and duplicate submit idempotency.
- [ ] Implement multipart uploads in 8-32 MiB parts with maximum three concurrent parts and exponential retry; persist only upload IDs/part ETags in IndexedDB, never signed URLs.
- [ ] Render distinct upload, ingest, transcription, analysis, retry, canceled, failed, and ready states.
- [ ] Make direct upload the primary call to action. Label public YouTube import as a convenience connector and do not render cookie upload controls unless Task 27's server capability and feature flag are both active.
- [ ] Run unit and Playwright tests against MinIO/Redis/Postgres test services; expect success.
- [ ] Commit with `feat: add resilient media submission flow`.

### Task 20: Build ranked clips review UX

**Files:**
- Create: `frontend/features/clips/ClipCard.tsx`
- Create: `frontend/features/clips/ClipList.tsx`
- Create: `frontend/features/clips/ClipPreview.tsx`
- Create: `frontend/tests/clips.test.tsx`
- Create: `frontend/e2e/clips-review.spec.ts`

**Interfaces:**
- Consumes: ranked candidate endpoints and signed proxy URLs.
- Produces: rank/score/hook/reason/category/tags/duration/excerpt display, explainable score breakdown, visible context warnings, preview, sorting/filtering, and edit creation.

- [ ] Write tests for ranking order, score and reason visibility, narrative-completeness/context-safety/platform-fit/visual-opportunity breakdowns, warning badges, category filters, duration filters, safe untrusted text rendering, empty/partial-analysis states, keyboard playback, and edit creation idempotency.
- [ ] Implement a review-first interface that makes candidates available without waiting for final renders.
- [ ] Add preview range playback against the source proxy using candidate `startMs`/`endMs` and stop playback at the candidate end.
- [ ] Run unit/E2E tests; expect success.
- [ ] Commit with `feat: add ranked clip review`.

### Task 21: Run the editor-engine bake-off and record the adoption decision

**Files:**
- Create: `docs/adr/0001-browser-editor-engine.md`
- Create: `contracts/fixtures/editor/parity-composition.json`
- Create: `frontend/spikes/editor-engines/adapter.ts`
- Create: `frontend/spikes/editor-engines/elah-adapter.ts`
- Create: `frontend/spikes/editor-engines/openreel-mediabunny-adapter.ts`
- Create: `frontend/spikes/editor-engines/benchmark.ts`
- Create: `frontend/tests/editor-engine-contract.test.ts`
- Create: `frontend/e2e/editor-engine-parity.spec.ts`
- Create: `scripts/check-editor-licenses.sh`

**Interfaces:**
- Produces: `BrowserEditorEngine` with `load(composition)`, `seek(frame)`, `play()`, `pause()`, `renderPreviewFrame(frame)`, `getWaveform(assetId)`, and `dispose()`; an ADR selecting the production adapter and recording license obligations.

- [ ] Write a framework-neutral contract test that loads `parity-composition.json`, seeks to exact frame boundaries, trims/splits one item, edits karaoke words, applies a 9:16 crop, serializes undo/redo state, and compares the resulting canonical composition JSON byte-for-byte.
- [ ] Run `cd frontend && pnpm vitest run tests/editor-engine-contract.test.ts`; expect failure because no adapter implements `BrowserEditorEngine`.
- [ ] Implement the minimum Elah adapter needed by the contract without allowing Elah types into Clipah's composition or feature modules.
- [ ] Implement the OpenReel/Mediabunny comparison adapter or an isolated proof using its decoding/muxing approach; do not import the entire OpenReel application shell.
- [ ] Add Playwright measurements for a 30-minute and 60-minute proxy: initial load below 5 seconds on the reference development machine, median seek response below 150 ms after warm-up, bounded memory below 1.5 GiB, no leaked media workers after `dispose()`, and graceful Safari fallback when a codec is unsupported.
- [ ] Render the same fixture through each browser adapter and the native FFmpeg fixture renderer; require timestamps within one frame and static-frame SSIM at or above 0.97 after the documented font mask.
- [ ] Run `scripts/check-editor-licenses.sh`; fail on GPL-only browser dependencies, unknown licenses, or commercial terms without a recorded owner/renewal cost. Record that OpenVideo and Remotion require separate license review before production use.
- [ ] Select Elah when it passes every mandatory gate; otherwise select the OpenReel/Mediabunny approach when it passes; otherwise document the failed primitives and approve only a narrow custom adapter over Clipah's schema. Record bundle size, browser support, maintenance risk, upgrade path, and escape cost in the ADR.
- [ ] Delete unused spike dependencies and retain only the selected adapter, contract fixtures, benchmark results, and ADR.
- [ ] Run frontend unit/E2E tests and the FFmpeg parity fixture; expect success.
- [ ] Commit with `docs: select browser editor foundation`.

### Task 22: Implement composition validation and immutable edit revisions

**Files:**
- Create: `contracts/composition.schema.json`
- Create: `backend/src/clipah/editor/models.py`
- Create: `backend/src/clipah/editor/repository.py`
- Create: `backend/src/clipah/editor/use_cases.py`
- Create: `backend/src/clipah/api/routes/edits.py`
- Create: `backend/tests/unit/test_composition.py`
- Create: `backend/tests/integration/test_edit_revisions.py`
- Generate: `frontend/features/editor/composition.generated.ts`

**Interfaces:**
- Produces: `CompositionV1`, `create_edit_from_candidate`, `save_revision`, revision history, canonical JSON hashing, and edit endpoints.

- [ ] Write schema tests covering all fields in Section 4 and editor capabilities in Section 9, including time bounds, track/item IDs, source/B-roll origin, provenance references, `preserveDialogueAudio`, asset permissions, keyframe ordering, finite numeric values, supported fonts/blends/motions, brand/template versions, and schema-version rejection.
- [ ] Write concurrency tests where two clients save revision 2; one succeeds and one receives `EDIT_REVISION_CONFLICT` with the current revision.
- [ ] Implement canonical JSON serialization and SHA-256 composition hashes that are stable across key order.
- [ ] Generate TypeScript types from the same JSON Schema and add a CI check that regenerated files are clean.
- [ ] Run backend/frontend schema tests; expect success.
- [ ] Commit with `feat: add versioned clip compositions`.

### Task 23: Build the basic non-destructive editor and autosave

**Files:**
- Create: `frontend/app/editor/[editId]/page.tsx`
- Create: `frontend/features/editor/store.ts`
- Create: `frontend/features/editor/Player.tsx`
- Create: `frontend/features/editor/Timeline.tsx`
- Create: `frontend/features/editor/CaptionsPanel.tsx`
- Create: `frontend/features/editor/Inspector.tsx`
- Create: `frontend/features/editor/autosave.ts`
- Create: `frontend/tests/editor-basic.test.tsx`
- Create: `frontend/e2e/editor-basic.spec.ts`

**Interfaces:**
- Consumes: composition/revision endpoints, proxy assets, and the `BrowserEditorEngine` selected in Task 21.
- Produces: trim, crop, caption text/style, aspect presets, playback, undo/redo, autosave, and conflict recovery.

- [ ] Write store tests for immutable updates, Immer patch-based undo/redo, source bounds, trim, crop, caption text/style, and dirty-state transitions.
- [ ] Write autosave tests for 750 ms debounce, one request in flight, offline queueing, revision conflict, reload restoration, and visible state labels.
- [ ] Implement player/timeline/inspector controls using the composition document as the only edit state and the selected browser engine only through the adapter boundary.
- [ ] Keep source/proxy media immutable and create a new backend revision for each successful autosave.
- [ ] Add keyboard shortcuts for play/pause, undo, redo, split, delete, zoom, and save without intercepting text-field input.
- [ ] Run unit/E2E tests; expect success.
- [ ] Commit with `feat: add non-destructive clip editor`.

### Task 24: Implement render-plan compilation and safe FFmpeg export

**Files:**
- Create: `backend/src/clipah/renders/models.py`
- Create: `backend/src/clipah/renders/compiler.py`
- Create: `backend/src/clipah/renders/ffmpeg_renderer.py`
- Create: `backend/src/clipah/renders/use_cases.py`
- Create: `backend/src/clipah/jobs/render_task.py`
- Create: `backend/src/clipah/api/routes/renders.py`
- Create: `backend/tests/unit/test_render_compiler.py`
- Create: `backend/tests/integration/test_render_pipeline.py`

**Interfaces:**
- Produces: `compile_render_plan(composition, assets, preset) -> RenderPlan`, `Renderer.render(...)`, idempotent render creation, status, and signed download endpoints.

- [ ] Write compiler tests for every export preset, trim, scale/crop, text, caption, watermark, audio gain, image/video B-roll overlay, stock/generated provenance references, dialogue-audio preservation, transform keyframes, unsupported feature rejection, and user text containing FFmpeg-special characters.
- [ ] Assert generated commands contain no shell string, no raw user text in filter arguments, and no path outside the job workspace.
- [ ] Implement filter-complex scripts and UTF-8 text/ASS files inside the workspace; pass the script using `-filter_complex_script`.
- [ ] Implement H.264/AAC MP4 presets with explicit pixel format, fast-start metadata, deterministic stream mapping, duration validation, and progress parsing.
- [ ] Deduplicate exports by `(composition_hash, preset)` and reuse a healthy existing artifact.
- [ ] Write integration tests that render fixture compositions with no B-roll, stock image pan/zoom, stock video, and generated video; validate output with ffprobe, confirm dialogue audio remains mapped, and confirm cancellation terminates the FFmpeg process group.
- [ ] Run focused/full tests; expect success.
- [ ] Commit with `feat: render versioned clip exports safely`.

### Task 25: Add complete timeline, asset, sound, text, and scene editing

**Files:**
- Create: `frontend/features/editor/AssetsPanel.tsx`
- Create: `frontend/features/editor/SourceMonitor.tsx`
- Create: `frontend/features/editor/TimelineToolbar.tsx`
- Create: `frontend/features/editor/SceneList.tsx`
- Create: `frontend/features/editor/TextPanel.tsx`
- Create: `frontend/features/editor/AudioPanel.tsx`
- Extend: `frontend/features/editor/store.ts`
- Extend: `frontend/tests/editor-advanced.test.tsx`
- Create: `frontend/e2e/editor-advanced.spec.ts`

**Interfaces:**
- Produces: assets/import, mark-in/out, add, scene/speaker labels, drag/resize, split-left/right, duplicate, delete, extract audio, snapping, ripple, zoom, bookmarks, text, sound, and multi-track controls.

- [ ] Add reducer/store tests for each operation and verify undo/redo restores byte-equivalent canonical composition JSON.
- [ ] Add interaction tests for keyboard and pointer operations, minimum item duration, collision behavior, snapping threshold, ripple shifts, track locking, and bookmark navigation.
- [ ] Implement accessible controls and deterministic timeline math in integer milliseconds.
- [ ] Extend the backend composition validator and render compiler tests for each new track/item operation before enabling its UI.
- [ ] Run backend schema/compiler tests and frontend unit/E2E tests; expect success.
- [ ] Commit with `feat: complete multi-track timeline editing`.

### Task 26: Add styling, karaoke, keyframes, templates, motion, and smart crop

**Files:**
- Create: `frontend/features/editor/KaraokePanel.tsx`
- Create: `frontend/features/editor/KeyframeEditor.tsx`
- Create: `frontend/features/editor/TemplatesPanel.tsx`
- Create: `frontend/features/editor/MotionPanel.tsx`
- Create: `backend/src/clipah/renders/templates.py`
- Create: `backend/src/clipah/assets/smart_crop.py`
- Create: `backend/tests/unit/test_smart_crop.py`
- Extend: render compiler and editor tests.

**Interfaces:**
- Produces: caption timing/style editing, transform/opacity/text keyframes, blend modes, versioned templates/motion presets, and overrideable face/active-speaker crop suggestions.

- [ ] Write tests for every style field in Section 9, word timing non-overlap, karaoke active-word calculation, keyframe interpolation, template expansion, motion duration bounds, and safe fallback when no face is detected.
- [ ] Implement built-in immutable template and motion definitions with explicit version numbers; compositions reference both ID and version.
- [ ] Implement smart-crop suggestions as optional keyframes generated from detected face boxes and transcript speaker segments; never overwrite user-authored keyframes.
- [ ] Add preview/render golden-frame fixtures for normal captions, karaoke, transform keyframes, blended text/image overlays, motion presets, and smart crop.
- [ ] Define the perceptual gate as SSIM at or above 0.97 for static frames after allowing documented font-rasterization masks; fail CI below the threshold.
- [ ] Run backend/frontend/golden-frame suites; expect success.
- [ ] Commit with `feat: add advanced editor styling and smart crop`.

### Task 27: Add feature-flagged authenticated YouTube connections

**Files:**
- Create: `backend/src/clipah/source_connectors/secrets.py`
- Create: `backend/src/clipah/source_connectors/cookies.py`
- Create: `backend/src/clipah/source_connectors/connections.py`
- Create: `backend/src/clipah/source_connectors/authenticated_youtube.py`
- Create: `backend/migrations/versions/0002_source_connections.py`
- Create: `backend/src/clipah/api/routes/source_connections.py`
- Create: `backend/tests/unit/test_youtube_cookie_validation.py`
- Create: `backend/tests/integration/test_source_connections.py`
- Create: `backend/tests/security/test_source_secret_redaction.py`
- Create: `frontend/features/uploads/YouTubeConnectionDialog.tsx`
- Create: `frontend/tests/youtube-connection.test.tsx`
- Create: `docs/security/youtube-import.md`

**Interfaces:**
- Consumes: `SourceImporter`, `SecretLease`, job workspaces, Workspace-scoped storage, and the public importer from Task 10.
- Produces: `SourceConnectionService.create_youtube_cookie_connection(...)`, `lease(connection_id, job_id) -> SecretLease`, revoke/list endpoints, and authenticated-import UI guarded by `AUTHENTICATED_YOUTUBE_IMPORT_ENABLED`.

- [ ] Write parser tests for Mozilla/Netscape format, CRLF/LF line endings, expiry, secure/httpOnly flags, the documented minimum YouTube-authentication domain/name allowlist, unrelated-domain rejection, control characters, duplicate cookie resolution, expired rows, files above 256 KiB, and malformed input.
- [ ] Run `cd backend && uv run pytest tests/unit/test_youtube_cookie_validation.py -v`; expect missing-module failure.
- [ ] Implement streaming validation that returns normalized cookie records without logging values and serializes only the minimum accepted rows.
- [ ] Implement envelope encryption through an injected `SecretStore`; persist only an opaque secret reference, Workspace, authorizing actor, domain scope, consent timestamp, expiry no later than seven days, and revocation state.
- [ ] Materialize a leased Netscape jar as a `0600` file inside the validated job workspace, pass it to yt-dlp as an argument-array value, redact the path from diagnostics, and remove it on success, failure, cancellation, and worker shutdown.
- [ ] Add integration tests proving members cannot list, lease, revoke, or import with another Workspace's connections and cannot exceed their Workspace role; revocation blocks new leases; expiry maps to `SOURCE_CONNECTION_EXPIRED`; retries never reuse another connection; plaintext cookie values do not appear in Postgres, Redis, logs, Sentry fixtures, job events, or object storage.
- [ ] Keep hosted `--cookies-from-browser` disabled. Document it only as an explicit local/self-hosted command where the worker and browser belong to the same user.
- [ ] Add consent and ownership-attestation UI explaining cookie rotation, account restriction/ban risk, revocation, expiry, and supported use. Require the user to confirm before upload and show the connected account only through non-secret metadata.
- [ ] Keep production enablement blocked until `docs/security/youtube-import.md` records product-specific legal approval, data retention, incident response, the yt-dlp/EJS/Deno versions, and any pinned PO Token plugin revision. The default environment value remains `false`.
- [ ] Run security, integration, frontend, and import smoke suites; expect success with the feature disabled and in an explicitly enabled test profile.
- [ ] Commit with `feat: add guarded youtube source connections`.

### Task 28: Model semantic beats and generate deterministic B-roll plans

**Files:**
- Create: `backend/src/clipah/broll/models.py`
- Create: `backend/src/clipah/broll/repository.py`
- Create: `backend/src/clipah/broll/planner.py`
- Create: `backend/src/clipah/broll/placement.py`
- Create: `backend/migrations/versions/0003_broll_suggestions.py`
- Create: `backend/src/clipah/jobs/broll_plan_task.py`
- Create: `backend/src/clipah/api/routes/broll.py`
- Create: `backend/tests/unit/test_broll_planner.py`
- Create: `backend/tests/unit/test_broll_placement.py`
- Create: `backend/tests/integration/test_broll_plans.py`

**Interfaces:**
- Produces: `VisualIntent`, `VisualBeat`, `BrollCoverage`, `BrollPlanner.plan(...)`, `place_suggestions(...)`, persisted `broll_suggestions`, and B-roll plan/list endpoints.

- [ ] Write strict-schema tests requiring beat word IDs, subject, action, setting, mood, Indonesian/English search terms, portrait suitability, exclusions, factual-risk flags, confidence, and placement reason; reject free-form timestamps and unknown transcript words.
- [ ] Write placement tests for `minimal`, `balanced`, and `dynamic` density, 2-5 second default shots, source-duration bounds, sentence/scene boundaries, first-hook face reveal, punchline, demonstration, emotional pause, repeated visual intent, and insufficient visual opportunity.
- [ ] Run focused tests; expect missing planner/placement failures.
- [ ] Implement a Groq strict-schema planner over the canonical transcript slice and detected scene boundaries; translate search intent rather than transcript text so Indonesian concepts retain local meaning.
- [ ] Resolve every beat and placement to authoritative transcript word IDs locally. Deterministic placement code, not the model, owns final timeline milliseconds and density enforcement.
- [ ] Persist proposed suggestions and model metadata idempotently by `(candidate_id, planner_version, coverage)`; planning never edits a composition or creates a generated asset.
- [ ] Return zero suggestions, not a failure, when a face, punchline, demonstration, or culturally sensitive passage should remain uninterrupted.
- [ ] Run focused/full tests and a fake-provider job replay; expect identical suggestions without duplicate rows.
- [ ] Commit with `feat: plan explainable broll suggestions`.

### Task 29: Retrieve, license, and rerank user-owned and stock B-roll

**Files:**
- Create: `backend/src/clipah/broll/retriever.py`
- Create: `backend/src/clipah/broll/user_asset_retriever.py`
- Create: `backend/src/clipah/broll/pexels_adapter.py`
- Create: `backend/src/clipah/broll/pixabay_adapter.py`
- Create: `backend/src/clipah/broll/reranker.py`
- Create: `backend/src/clipah/broll/search_cache.py`
- Create: `backend/migrations/versions/0004_asset_provenance.py`
- Create: `backend/src/clipah/jobs/broll_retrieve_task.py`
- Create: `backend/tests/contract/test_stock_providers.py`
- Create: `backend/tests/integration/test_broll_retrieval.py`
- Create: `backend/evals/broll/manifest.json`
- Create: `backend/src/clipah/broll/evaluation.py`
- Create: `scripts/run-broll-eval.sh`
- Create: `docs/legal/asset-provenance.md`

**Interfaces:**
- Consumes: `VisualIntent` and `BrollRetriever`.
- Produces: user-asset-first retrieval, `PexelsBrollRetriever`, `PixabayBrollRetriever`, `VisualReranker`, normalized `ExternalAssetCandidate`, private accepted assets, and complete `asset_provenance`.

- [ ] Write provider contract fixtures for query/orientation/safe-search filters, timeouts, rate limits, pagination, malformed metadata, deleted upstream assets, attribution fields, and deterministic normalization without making network calls in CI.
- [ ] Write ranking tests covering semantic relevance, sampled-frame relevance, technical quality, 9:16 crop viability, brand exclusions, local/cultural fit, source repetition, author repetition, and a minimum relevance threshold.
- [ ] Run focused tests; expect missing adapters.
- [ ] Search accepted user assets first. Query Pexels and Pixabay only when local results are insufficient, using both Indonesian and English intent terms while preserving the original concept.
- [ ] Cache provider search responses for their required duration, enforce request budgets, and store only selected media in Clipah object storage; do not permanently hotlink provider media or systematically download search results.
- [ ] Before an asset becomes selectable, persist provider, provider asset ID, source URL, author, license/terms snapshot, retrieval date, query, moderation, checksum, and attribution text. Reject incomplete provenance.
- [ ] Normalize selected stock into proxy/original asset variants once, validate with ffprobe, and reuse by checksum within the same Workspace without exposing it across Workspaces.
- [ ] Add an adapter-neutral visual reranker with a deterministic fake. Keep embedding/vision SDK types out of domain models and record the reranker model/version when enabled.
- [ ] Add at least 30 labeled Indonesian/English visual intents with relevant, irrelevant, culturally mismatched, unsafe, repetitive, and portrait-incompatible candidates. Require 100% provenance completeness, zero unsafe selections, and top-three relevance at or above 80% on the checked-in fake corpus before editor integration.
- [ ] Run contract/integration tests and an opt-in live smoke limited to one search per provider; expect success and no permanent unselected downloads.
- [ ] Commit with `feat: retrieve provenance-aware stock broll`.

### Task 30: Integrate editable B-roll suggestions into the clip editor

**Files:**
- Create: `frontend/features/broll/BrollPanel.tsx`
- Create: `frontend/features/broll/BrollSuggestionCard.tsx`
- Create: `frontend/features/broll/CoverageControl.tsx`
- Create: `frontend/features/broll/ProvenancePopover.tsx`
- Extend: `frontend/features/editor/store.ts`
- Extend: `frontend/features/editor/Timeline.tsx`
- Extend: `backend/src/clipah/editor/use_cases.py`
- Create: `frontend/tests/broll-editor.test.tsx`
- Create: `frontend/e2e/broll-review.spec.ts`
- Extend: `backend/tests/integration/test_edit_revisions.py`

**Interfaces:**
- Consumes: B-roll endpoints, selected assets, `CompositionV1`, and the chosen `BrowserEditorEngine`.
- Produces: plan/request controls, coverage selection, alternatives, provenance display, accept/reject/replace/remove actions, and B-roll composition items with `preserveDialogueAudio=true` by default.

- [ ] Write store tests proving a proposed suggestion cannot affect preview/export until accepted, acceptance creates one overlay item, repeated acceptance is idempotent, replacement retains decision history, removal deletes only the composition item, and undo/redo restores exact prior JSON.
- [ ] Write UI tests for loading/empty/error/quota states, confidence and placement explanations, source/author/license links, AI-generated badges, keyboard operation, and screen-reader labels.
- [ ] Run frontend and backend focused tests; expect failure before the feature exists.
- [ ] Implement coverage controls that start disabled for new projects and require an explicit `Suggest B-roll` action.
- [ ] Insert accepted suggestions at deterministic placement times as normal editable video/image overlay items, preserve dialogue audio, and allow trim, move, crop, transition, opacity, replace, and delete through existing editor operations.
- [ ] Save each accept/replace/remove decision transactionally with the new immutable edit revision and use optimistic concurrency to prevent double-placement from two tabs.
- [ ] Show provenance and generated status in the editor and clip-detail page; export metadata may omit provider internals but must retain internal audit linkage.
- [ ] Run unit/E2E, schema, render compiler, and golden-frame tests; expect success for stock video, stock image with pan/zoom, and an edit with no B-roll.
- [ ] Commit with `feat: add editable broll copilot`.

### Task 31: Add quota-aware generated-media fallback

**Files:**
- Create: `backend/src/clipah/broll/generation.py`
- Create: `backend/src/clipah/broll/fal_adapter.py`
- Create: `backend/src/clipah/broll/runway_adapter.py`
- Create: `backend/src/clipah/jobs/broll_generate_task.py`
- Create: `backend/src/clipah/api/routes/generation_webhooks.py`
- Create: `backend/tests/contract/test_generation_providers.py`
- Create: `backend/tests/integration/test_broll_generation.py`
- Create: `frontend/features/broll/GenerationConfirmDialog.tsx`
- Create: `frontend/tests/broll-generation.test.tsx`
- Create: `docs/operations/generative-media.md`

**Interfaces:**
- Produces: `GenerativeMediaProvider`, `GenerationEstimate`, a configured fal adapter for generated still/video jobs, an optional Runway video adapter, signed webhook handling, quota reservation/reconciliation, and generated asset provenance.

- [ ] Write contract tests for estimate, submit, status/webhook completion, timeout, provider rejection, moderation rejection, cancellation, retry idempotency, unknown model, and normalized cost/usage fields.
- [ ] Write admission tests proving stock relevance above threshold suppresses the generation offer, generated still is offered before video, video requires a second confirmation, quotas reserve atomically, failed jobs release reservations, and actual cost reconciles once.
- [ ] Run focused tests; expect missing-provider failures.
- [ ] Implement provider-neutral request/result models and async adapters. Validate webhook signatures and replay timestamps, poll only as a bounded fallback, and never keep an API request open during generation.
- [ ] Exclude OpenAI Sora model IDs at configuration validation because this plan must not introduce a dependency scheduled to shut down on 2026-09-24.
- [ ] Present estimated output count, duration, resolution, latency class, and quota/cost before confirmation. Require project-owner or editor permission and record the accepting user.
- [ ] Moderate prompt and output, validate returned media with the normal ingest pipeline, calculate checksum, and persist prompt, provider, model/version, seed, moderation, cost, and source type before creating a new proposed suggestion.
- [ ] Add budget, concurrency, maximum-duration, retry, and circuit-breaker configuration. Default generated video to disabled and make provider absence a normal unavailable state.
- [ ] Run fake-provider integration/E2E tests plus opt-in sandbox smoke tests; expect no billable network call in ordinary CI.
- [ ] Commit with `feat: add guarded generative broll fallback`.

### Task 32: Add context-safe clip variants and platform packaging

**Files:**
- Create: `backend/src/clipah/variants/models.py`
- Create: `backend/src/clipah/variants/generator.py`
- Create: `backend/src/clipah/variants/context_safety.py`
- Create: `backend/src/clipah/variants/claim_evidence.py`
- Create: `backend/migrations/versions/0005_variants_and_claim_evidence.py`
- Create: `backend/src/clipah/api/routes/variants.py`
- Create: `backend/src/clipah/api/routes/claim_evidence.py`
- Create: `backend/tests/unit/test_context_safety.py`
- Create: `backend/tests/integration/test_clip_variants.py`
- Create: `frontend/features/clips/ScoreBreakdown.tsx`
- Create: `frontend/features/clips/ContextWarnings.tsx`
- Create: `frontend/features/clips/VariantLab.tsx`
- Create: `frontend/features/clips/EvidencePanel.tsx`
- Create: `frontend/e2e/clip-variants.spec.ts`

**Interfaces:**
- Produces: explainable context warnings, user-provided claim evidence and citation-overlay suggestions, `ClipVariantGenerator.generate(...)`, up to three hook strategies, authoritative duration/platform variants, and variant-to-edit creation.

- [ ] Create labeled tests for cut-off questions/payoffs, missing negation, pronouns without antecedents, missing speaker attribution, removed caveats, misleading claim boundaries, incomplete list steps, and safe complete thoughts in Indonesian, English, and code-switched speech.
- [ ] Run focused tests; expect missing context-safety implementation.
- [ ] Implement strict-schema context assessment and deterministic validation against surrounding transcript words. Store warning type, severity, evidence word IDs, and a suggested safe boundary; never replace authoritative transcript text.
- [ ] Generate no more than three distinct hooks and only requested 20/30/45/60/90-second variants that map to real word boundaries, preserve a complete thought, and pass configured context-safety severity gates.
- [ ] Add TikTok, Instagram Reels, and YouTube Shorts packaging metadata without assuming platform upload integration: aspect, safe zones, title length guidance, caption style recommendation, and export preset.
- [ ] Show score explanations and warnings before edit creation. Let the user compare variants against one proxy without duplicating source, transcript, or assets.
- [ ] Let users attach a normalized HTTPS source URL, title, publisher, and retrieval date to an exact claim word range. Render it as a reviewable citation-overlay suggestion; do not label a claim verified unless a user explicitly sets the status and preserve the actor/audit record.
- [ ] Reject private-network URLs, executable schemes, embedded credentials, oversized metadata, cross-candidate word IDs, and raw HTML. Link display uses safe text and opens with appropriate external-link isolation.
- [ ] Extend the evaluation harness with warning recall/precision, variant boundary validity, semantic preservation review, and user acceptance metrics.
- [ ] Run unit/integration/E2E and highlight eval suites; expect success.
- [ ] Commit with `feat: add context-safe clip variants`.

### Task 33: Add brand kits, reusable templates, and moment-to-campaign outputs

**Files:**
- Create: `backend/src/clipah/brands/models.py`
- Create: `backend/src/clipah/brands/use_cases.py`
- Create: `backend/src/clipah/campaigns/models.py`
- Create: `backend/src/clipah/campaigns/generator.py`
- Create: `backend/migrations/versions/0006_brand_campaigns.py`
- Create: `backend/src/clipah/api/routes/brand_kits.py`
- Create: `backend/src/clipah/api/routes/templates.py`
- Create: `backend/src/clipah/api/routes/campaigns.py`
- Create: `backend/tests/integration/test_brand_kits.py`
- Create: `backend/tests/integration/test_campaign_outputs.py`
- Create: `frontend/features/brand-kits/BrandKitEditor.tsx`
- Create: `frontend/features/templates/TemplateLibrary.tsx`
- Create: `frontend/features/campaigns/CampaignPanel.tsx`
- Create: `frontend/e2e/brand-campaign.spec.ts`

**Interfaces:**
- Produces: versioned brand kits/templates, composition constraint validation, and approved-edit-derived title, post copy, CTA, hashtags, thumbnail brief, and localized campaign variants.

- [ ] Write tests for Workspace scoping, immutable template versioning, archived-template rendering, font/logo asset authorization, color validation, caption safe zones, visual exclusions, required attribution, forbidden claim patterns, and deletion while referenced.
- [ ] Implement brand constraints as data evaluated by both the editor and render compiler; return actionable violations and never silently mutate user edits.
- [ ] Apply a selected brand-kit/template version when creating an edit, but store the exact version reference so later brand changes do not rewrite old revisions.
- [ ] Generate campaign outputs only from an approved immutable edit revision and its canonical transcript. Strict output contains platform, title, post copy, CTA, hashtags, thumbnail text/visual brief, language, and model metadata.
- [ ] Add Indonesian/English localization and terminology dictionaries without translating speaker quotes or inventing facts. Surface claim/context warnings alongside generated copy.
- [ ] Build `/dashboard/brand-kits` and `/dashboard/templates` CRUD/version UX plus a clip-detail campaign panel with copy/export actions. Campaign generation never auto-publishes; the user may explicitly open the Task 43 publication composer with one approved immutable revision preselected.
- [ ] Run integration/E2E/render validation tests; expect success.
- [ ] Commit with `feat: add brand and campaign workflows`.

### Task 34: Build the searchable creator content library

**Files:**
- Create: `backend/src/clipah/search/models.py`
- Create: `backend/src/clipah/search/indexer.py`
- Create: `backend/src/clipah/search/use_cases.py`
- Create: `backend/src/clipah/api/routes/search.py`
- Create: `backend/migrations/versions/0007_content_search.py`
- Create: `backend/tests/integration/test_content_search.py`
- Create: `frontend/features/search/GlobalSearch.tsx`
- Extend: `frontend/app/dashboard/clips/page.tsx`
- Extend: `frontend/app/dashboard/assets/page.tsx`
- Create: `frontend/e2e/content-search.spec.ts`

**Interfaces:**
- Produces: Workspace/permission-scoped Postgres full-text search over project names, transcript words, speakers/guests, topics, claims, clip titles, tags, and campaign outputs.

- [ ] Write migration/index tests for Indonesian and English token search, quoted phrases, normalized names, typo-tolerant project/title matching, pagination stability, deleted content, and strict cross-Workspace/member isolation.
- [ ] Run focused tests; expect missing index/search failures.
- [ ] Implement weighted Postgres full-text and trigram search first; do not add a vector database. Store derived search documents that can be rebuilt from durable domain records.
- [ ] Return result type, highlighted safe text fragments, project/clip deep link, time range, speaker, and permission-filtered metadata; never return raw provider payloads or storage keys.
- [ ] Add global search and filters for project, guest/speaker, topic, content type, source language, date, and export state. Support opening a transcript result at its proxy timecode.
- [ ] Add a rebuild command and prove reindexing is idempotent and does not change source rows.
- [ ] Run integration/E2E/load tests; require p95 below 500 ms for the checked-in 100k-document fixture on the reference database.
- [ ] Commit with `feat: add searchable creator library`.

### Task 35: Add Workspace collaboration, project review, and accessibility quality gates

**Files:**
- Create: `backend/src/clipah/workspaces/memberships.py`
- Create: `backend/src/clipah/editor/reviews.py`
- Create: `backend/src/clipah/api/routes/workspace_memberships.py`
- Create: `backend/src/clipah/api/routes/edit_reviews.py`
- Create: `backend/migrations/versions/0008_workspace_collaboration.py`
- Create: `backend/tests/contract/test_workspace_permissions.py`
- Create: `backend/tests/integration/test_edit_reviews.py`
- Create: `frontend/features/team/TeamSettings.tsx`
- Create: `frontend/features/reviews/ReviewPanel.tsx`
- Create: `frontend/features/editor/AccessibilityPanel.tsx`
- Create: `frontend/tests/accessibility-quality.test.tsx`
- Create: `frontend/e2e/team-review.spec.ts`

**Interfaces:**
- Produces: Workspace roles `owner`, `admin`, `editor`, `reviewer`, and `viewer`; Workspace invitations and ownership transfer; timestamp/item-anchored comments; request-changes/approve states; and accessibility warnings for captions and platform safe zones.

- [ ] Write a permission matrix test for every Workspace, project, asset, job, candidate, edit, B-roll, render, brand, review, Social Account, and publication action. Guessed UUIDs must remain indistinguishable from missing resources; owners/admins manage membership according to policy, editors mutate edits and prepare publications, reviewers comment/approve, and viewers read.
- [ ] Write review tests for immutable revision targets, timestamp/item anchors, comment resolution, stale approval after a new edit revision, request-changes, approval, removed member access, and audit history.
- [ ] Run backend tests; expect missing membership/review failures.
- [ ] Implement expiring Workspace invite links with hashed tokens and explicit roles; do not require an outbound email provider. Acceptance binds an authenticated user, invalidates the token, and never trusts invite email as authentication proof.
- [ ] Enforce last-owner protection, explicit ownership transfer, role-change audit events, immediate access loss after member removal, and cancellation/re-evaluation of unauthorized pending publications.
- [ ] Replace owner-only checks with `WorkspaceAuthorizer`, composite tenant foreign keys, and RLS while retaining `workspaces/{workspace_id}/` object keys. Record actor ID on every mutation, approval, connection, and publication decision.
- [ ] Implement caption reading-speed, minimum-duration, overlap, contrast, line-count, safe-zone, and platform-UI collision checks with fixed documented thresholds. Warnings are actionable, deterministic, and do not silently alter content.
- [ ] Build team settings, revision review/comments, approval status, and editor accessibility panel with keyboard navigation, focus management, screen-reader labels, reduced-motion support, and WCAG 2.2 AA automated checks.
- [ ] Run permission, integration, axe, Playwright, and regression suites; expect success with the collaboration feature flag disabled for plans that do not include it.
- [ ] Commit with `feat: add accessible review workflows`.

### Task 36: Implement Social Account connections and encrypted OAuth Grants

**Files:**
- Create: `backend/src/clipah/social_accounts/models.py`
- Create: `backend/src/clipah/social_accounts/repository.py`
- Create: `backend/src/clipah/social_accounts/oauth.py`
- Create: `backend/src/clipah/social_accounts/secrets.py`
- Create: `backend/src/clipah/social_accounts/use_cases.py`
- Create: `backend/src/clipah/api/routes/social_accounts.py`
- Create: `backend/migrations/versions/0009_social_accounts.py`
- Create: `backend/tests/integration/test_social_accounts.py`
- Create: `backend/tests/security/test_oauth_grants.py`

**Interfaces:**
- Produces: `SocialAccountService.connect/list/get_capabilities/disconnect`, short-lived `OAuthGrantLease`, provider-neutral connection states, and encrypted-at-rest OAuth Grant storage separate from login identities and source-import credentials.
- Consumes: `CurrentWorkspace`, `WorkspaceAuthorizer`, application Secret Manager/KMS, and provider settings from Task 1.

- [ ] Write failing tests for Workspace isolation, role permissions, CSRF `state`, PKCE S256, exact redirect URI, minimum scopes, declined scopes, callback replay, duplicate provider accounts, capability refresh, disconnect, revocation, and audit history.
- [ ] Prove Google Login, YouTube publishing, Instagram publishing, TikTok publishing, and yt-dlp source authorization remain separate credential families; matching emails or provider names never link them automatically.
- [ ] Write security tests proving access/refresh tokens, client secrets, authorization codes, and decrypted grant material never enter logs, exceptions, API responses, job arguments, analytics, database plaintext columns, or general caches.
- [ ] Implement envelope encryption with key version metadata, bounded in-memory decryption, sanitized provider errors, serialized token refresh, optimistic `token_version`, rotation support, and deny-by-default behavior when KMS is unavailable.
- [ ] Store Social Account identity, display metadata, connection status, scopes, capability snapshot, and timestamps separately from the encrypted OAuth Grant; expose only redacted status through the API.
- [ ] Implement disconnect as an idempotent workflow that marks the account unavailable immediately, cancels or pauses eligible future Publications, attempts provider revocation, and retains a minimal audit tombstone.
- [ ] Run focused integration/security tests plus the full backend suite; expect success.
- [ ] Commit with `feat: add secure social account connections`.

### Task 37: Build the Publication domain, state machine, scheduler, and idempotency foundation

**Files:**
- Create: `backend/src/clipah/publishing/models.py`
- Create: `backend/src/clipah/publishing/repository.py`
- Create: `backend/src/clipah/publishing/state_machine.py`
- Create: `backend/src/clipah/publishing/use_cases.py`
- Create: `backend/src/clipah/publishing/scheduler.py`
- Create: `backend/src/clipah/publishing/outbox.py`
- Create: `backend/src/clipah/publishing/tasks.py`
- Create: `backend/src/clipah/api/routes/publications.py`
- Create: `backend/migrations/versions/0010_publications.py`
- Create: `backend/tests/integration/test_publications.py`
- Create: `backend/tests/contract/test_publication_states.py`

**Interfaces:**
- Produces: immutable `PublicationBatch`, one independent `Publication` per destination, append-only `PublicationAttempt`/`ProviderEvent`, explicit draft/preflight/confirm APIs, `PublicationScheduler.claim_due`, transactional outbox delivery, and the state machine from Section 4.

- [ ] Write a transition matrix that rejects illegal transitions and permits recovery from `awaiting_approval`, `scheduled`, `preparing`, `transferring`, `processing`, `published`, `failed`, and `canceled` without erasing history.
- [ ] Test that a confirmation snapshots the exact edit revision, master artifact checksum, copy/metadata, Social Account, capability version, consent fields, actor, schedule timezone/UTC instant, and provider policy version; later edit or account changes cannot silently rewrite it.
- [ ] Test one destination equals one independently retryable Publication, so partial success never rolls back or duplicates another platform's successful post.
- [ ] Implement request idempotency and provider idempotency/checkpoint rules for pre-confirmation retries, ambiguous timeouts, worker crashes, scheduler restarts, and duplicate webhook/poll events.
- [ ] Claim due work with bounded batches and `FOR UPDATE SKIP LOCKED`; write outbox records in the same transaction as state changes, use UTC internally, preserve the requested IANA timezone for display, and recompute authorization/capabilities at dispatch.
- [ ] Reject non-terminal cancellation once a provider can no longer guarantee cancellation; report the provider truth instead of presenting false local success.
- [ ] Run state, concurrency, crash-recovery, and full backend tests; expect success.
- [ ] Commit with `feat: add durable publication orchestration`.

### Task 38: Build immutable provider renditions and publication preflight

**Files:**
- Create: `backend/src/clipah/publishing/renditions.py`
- Create: `backend/src/clipah/publishing/profiles.py`
- Create: `backend/src/clipah/publishing/preflight.py`
- Create: `backend/src/clipah/publishing/render_tasks.py`
- Create: `backend/tests/unit/test_publication_preflight.py`
- Create: `backend/tests/integration/test_social_renditions.py`

**Interfaces:**
- Produces: deterministic provider-profile validation and cached immutable renditions keyed by `(master_artifact_checksum, provider, profile_version)`.
- Consumes: approved render artifacts from Task 27, provenance from Tasks 28–30, and caption/brand/accessibility checks from Tasks 33–35.

- [ ] Write tests for provider file-size, duration, codec, container, aspect ratio, resolution, frame-rate, audio, caption, thumbnail, disclosure, safe-zone, watermark, and metadata constraints using versioned capability fixtures.
- [ ] Establish a high-quality 9:16 MP4 master and create a provider rendition only when the current provider profile requires it; retries reuse the exact checksum and never re-render mutable editor state.
- [ ] Validate both before user confirmation and immediately before dispatch. A changed provider capability or expired approval moves the Publication to `awaiting_approval` with a clear diff instead of silently applying a new default.
- [ ] Reject TikTok-bound media containing Clipah or third-party promotional watermarks and surface non-destructive remediation; do not remove watermarks automatically.
- [ ] Store rendition provenance, FFmpeg command/config version, source checksums, validation report, and retention linkage without exposing private object keys.
- [ ] Run golden-media, preflight, caching, and cancellation tests; expect success.
- [ ] Commit with `feat: add social publication renditions`.

### Task 39: Implement the official YouTube Shorts publishing adapter

**Files:**
- Create: `backend/src/clipah/publishing/providers/youtube/oauth.py`
- Create: `backend/src/clipah/publishing/providers/youtube/adapter.py`
- Create: `backend/src/clipah/publishing/providers/youtube/resumable.py`
- Create: `backend/src/clipah/publishing/providers/youtube/status.py`
- Create: `backend/tests/contract/test_youtube_publisher.py`
- Create: `backend/tests/integration/test_youtube_publications.py`

**Interfaces:**
- Implements: `SocialPublisher` for the official YouTube Data API and `youtube.upload` authorization.

- [ ] Write contract tests for OAuth consent/scopes, channel identity, audit-limited privacy, title/description/tags/category, made-for-kids selection, synthetic-media disclosure, native scheduling, resumable upload checkpoints, status polling, quota errors, token expiry, revocation, and ambiguous completion.
- [ ] Treat Shorts as an eligibility/result of the uploaded video's properties, not a separate unofficial upload endpoint; preflight the current duration/aspect requirements and never promise classification.
- [ ] Until the Google API project passes the required audit, force API uploads to the permitted private visibility and make the restriction explicit in preflight and confirmation; enable broader visibility only through a reviewed feature flag.
- [ ] Use the provider's native `publishAt` scheduling when eligible. Persist encrypted resumable-session/checkpoint material, recover after interruption, and reconcile the authoritative video ID/status before retrying bytes.
- [ ] Keep captions and custom thumbnail operations explicit and independently retryable when supported; failure must not falsify the base video's published state.
- [ ] Run fake-server contract tests and an opt-in sandbox smoke test; expect success.
- [ ] Commit with `feat: publish shorts through youtube api`.

### Task 40: Implement the official Instagram Reels publishing adapter

**Files:**
- Create: `backend/src/clipah/publishing/providers/instagram/oauth.py`
- Create: `backend/src/clipah/publishing/providers/instagram/adapter.py`
- Create: `backend/src/clipah/publishing/providers/instagram/containers.py`
- Create: `backend/src/clipah/api/routes/instagram_webhooks.py`
- Create: `backend/tests/contract/test_instagram_publisher.py`
- Create: `backend/tests/integration/test_instagram_publications.py`

**Interfaces:**
- Implements: `SocialPublisher` for Instagram API with Instagram Login, Reels media-container creation, processing reconciliation, and publish.

- [ ] Write contract tests for eligible professional accounts, minimum publishing scopes, long-lived token refresh, disconnected accounts, Reels container creation, pull-URL expiry, container polling, publish, daily content-publishing limits, idempotent reconciliation, deauthorization, and data-deletion callbacks.
- [ ] Generate a short-lived HTTPS media URL restricted to the expected provider fetch path and lifetime; verify the rendition is fetchable before creating a container and never place provider tokens in its URL.
- [ ] Implement Clipah-side scheduling that creates/publishes close enough to the requested time to stay inside Instagram container lifetime; recover safely when creation succeeded but the response was lost.
- [ ] Present Instagram's actual publishing semantics without inventing unsupported privacy, draft, or native scheduling controls.
- [ ] Verify/de-duplicate webhook events and reconcile by polling whenever event delivery is absent, duplicated, late, or out of order.
- [ ] Run fake-server contract tests and an opt-in sandbox smoke test; expect success.
- [ ] Commit with `feat: publish reels through instagram api`.

### Task 41: Implement TikTok draft fallback and audited Direct Post adapter

**Files:**
- Create: `backend/src/clipah/publishing/providers/tiktok/oauth.py`
- Create: `backend/src/clipah/publishing/providers/tiktok/adapter.py`
- Create: `backend/src/clipah/publishing/providers/tiktok/transfers.py`
- Create: `backend/src/clipah/api/routes/tiktok_webhooks.py`
- Create: `backend/tests/contract/test_tiktok_publisher.py`
- Create: `backend/tests/integration/test_tiktok_publications.py`

**Interfaces:**
- Implements: `SocialPublisher` through TikTok Content Posting API, with user-authorized draft upload as the production fallback until the app/account is approved for Direct Post.

- [ ] Write contract tests for Login Kit, minimum scopes, refresh rotation, `creator_info` refresh at composer/dispatch time, available privacy choices, interaction toggles, commercial-content disclosures, explicit consent, music restrictions, inbox/draft upload, Direct Post, pull-from-URL verification, webhook/poll reconciliation, and revoked authorization.
- [ ] Do not preselect privacy or silently infer promotional disclosures. Require the user to choose from current provider-returned options and confirm TikTok-specific declarations immediately before submission.
- [ ] Keep Direct Post disabled behind an audit/capability flag. Before approval, route an explicitly labeled TikTok destination to the official draft/inbox flow and clearly state the remaining in-app TikTok action.
- [ ] After approval, enable Direct Post without changing the Publication contract; re-run capability/preflight and return to `awaiting_approval` if the destination behavior changes from draft to direct.
- [ ] Enforce duration/file constraints, verified media-domain/prefix requirements, no promotional watermark, and truthful status messaging; never automate TikTok's consumer UI.
- [ ] Run fake-server contract tests and an opt-in sandbox smoke test; expect success.
- [ ] Commit with `feat: add audited tiktok publishing`.

### Task 42: Complete multi-destination scheduling, dispatch, and reconciliation

**Files:**
- Create: `backend/src/clipah/publishing/dispatcher.py`
- Create: `backend/src/clipah/publishing/reconciler.py`
- Create: `backend/src/clipah/publishing/webhooks.py`
- Create: `backend/tests/integration/test_publication_dispatch.py`
- Create: `backend/tests/integration/test_publication_recovery.py`
- Create: `backend/tests/e2e/test_multi_destination_publish.py`

**Interfaces:**
- Produces: end-to-end publish-now and scheduled dispatch, provider/account locking, webhook intake, polling fallback, partial-success aggregation, and safe retry/cancel operations.

- [ ] Test batches containing YouTube, Instagram, and TikTok destinations where each possible destination fails before transfer, during transfer, during provider processing, and after an ambiguous response; successful siblings remain published and failed siblings remain independently retryable.
- [ ] Test immediate publish, future schedule, daylight-saving/timezone display, edit/account/membership changes before dispatch, token refresh races, provider throttling, worker termination, scheduler restart, duplicate delivery, webhook replay, and reconciliation after an outage.
- [ ] Acquire bounded provider/account locks and quota reservations at dispatch, release them on every terminal path, honor provider retry hints, apply jittered backoff, and stop retrying permanent policy/authorization failures.
- [ ] Revalidate Workspace permission, Social Account status, rendition checksum, consent snapshot, provider capabilities, and schedule policy immediately before external side effects.
- [ ] Aggregate batch state from child Publications without hiding partial success. Retry/cancel APIs address explicit destination IDs and never replay already-published siblings.
- [ ] Add retention-safe reconciliation for stuck `transferring`/`processing` states and an operator command that observes provider truth before changing state.
- [ ] Run concurrency, recovery, webhook security, and multi-destination E2E tests; expect success.
- [ ] Commit with `feat: orchestrate scheduled social publishing`.

### Task 43: Build Connections, publishing dashboard, composer, history, and rollout gates

**Files:**
- Create: `frontend/features/publishing/`
- Create: `frontend/features/connections/`
- Create: `frontend/app/dashboard/publishing/page.tsx`
- Create: `frontend/app/dashboard/publishing/[batchId]/page.tsx`
- Complete: `frontend/app/dashboard/settings/connections/page.tsx`
- Create: `frontend/tests/publication-composer.test.tsx`
- Create: `frontend/tests/publication-history.test.tsx`
- Create: `frontend/e2e/social-publishing.spec.ts`
- Create: `scripts/run-social-provider-contracts.sh`
- Create: `docs/operations/social-publishing.md`

**Interfaces:**
- Produces: Social Account connection management, explicit multi-destination composer, publish-now/schedule controls, per-platform metadata/consent tabs, list/calendar/history views, batch detail, retry/cancel controls, and staged provider rollout.

- [ ] Test connection loading/expired/revoked/error states, capability changes, role gating, keyboard and screen-reader behavior, unsaved metadata, timezones, schedule validation, explicit destination selection, publish confirmation, duplicate submit, and partial-success presentation.
- [ ] Never silently select an account, destination, privacy value, disclosure, or schedule. Confirmation names every destination/account, whether delivery is direct or draft, the exact artifact revision, requested time, visibility, disclosures, and the irreversible external effect.
- [ ] Show one independent status timeline per destination with provider-returned identifiers/links when safe, actionable retry reasons, account reconnect, `awaiting_approval` diffs, and honest cancel limitations.
- [ ] Implement YouTube-specific audience/synthetic-content/privacy controls, Instagram's supported Reels fields, and TikTok creator/privacy/interaction/commercial-content/consent UX from current capabilities rather than shared lowest-common-denominator fields.
- [ ] Launch behind Workspace/provider flags in this order: YouTube private/audited restrictions, Instagram Reels, TikTok draft fallback, TikTok Direct Post after audit, then multi-destination scheduling. Require provider contract tests, sandbox evidence, policy review, support runbook, and rollback switch at every gate.
- [ ] Write the operator runbook for credentials, app review/audit evidence, callback domains, webhook verification, token revocation, stuck-publication reconciliation, quota alerts, privacy/data-deletion callbacks, and provider incident rollback.
- [ ] Run frontend unit/E2E/accessibility suites and `scripts/run-social-provider-contracts.sh --adapter=fake`; expect success.
- [ ] Commit with `feat: add social publishing workspace`.

### Task 44: Add structured observability, provider usage, and operational dashboards

**Files:**
- Create: `backend/src/clipah/observability/logging.py`
- Create: `backend/src/clipah/observability/tracing.py`
- Create: `backend/src/clipah/observability/metrics.py`
- Create: `backend/src/clipah/observability/usage.py`
- Create: `backend/tests/unit/test_observability.py`
- Create: `docs/operations/observability.md`

**Interfaces:**
- Produces: correlated request/job/Workspace/project/Publication log context, OpenTelemetry spans, Sentry error capture, metrics, and Workspace-attributed provider usage records.

- [ ] Write tests that capture logs and assert presence of request/job/Workspace/project/Publication IDs and absence of session tokens, OAuth tokens, authorization codes, provider keys, encrypted grant/checkpoint values, signed URLs, transcript content, and local filesystem paths.
- [ ] Instrument HTTP requests, Celery tasks, database/RLS calls, object storage, source and Social Account connections, transcription/highlight/stock/generation/social providers, FFmpeg stages, SSE connections, scheduler claims, outbox delivery, webhooks, polling, retries, cancellation, B-roll decisions, campaign generation, and Publication reconciliation.
- [ ] Emit counters/histograms for queue/scheduler latency, stage duration, success/failure/retry, uploaded/rendered bytes, provider units/cost, OAuth refresh health, connection expiry, webhook signature/replay failures, Publication time-to-publish, partial success, stuck state age, provider quota/throttle, candidate count, context warnings, B-roll decisions, generation cost per exported minute, render speed ratio, active jobs, and model/config/capability versions.
- [ ] Add a staging readiness/deprecation monitor for configured external model IDs and SDK/API versions; alert before announced shutdown dates and fail readiness only when a configured model is already retired.
- [ ] Document alerts for API error rate, worker queue/scheduler age, repeated provider failure, source/Social Account expiry, token-refresh failure, invalid webhook spikes, stuck Publications, stock/social quota exhaustion, generation circuit breaker, render failure rate, storage failure, and retention backlog with explicit thresholds based on a seven-day baseline after launch.
- [ ] Run observability tests and a local trace smoke test; expect success.
- [ ] Commit with `feat: instrument processing and provider usage`.

### Task 45: Implement retention, Workspace/project recovery, and account deletion

**Files:**
- Create: `backend/src/clipah/retention/policy.py`
- Create: `backend/src/clipah/retention/tasks.py`
- Create: `backend/src/clipah/retention/use_cases.py`
- Create: `backend/src/clipah/api/routes/account.py`
- Create: `backend/tests/integration/test_retention.py`
- Create: `docs/operations/data-retention.md`

**Interfaces:**
- Produces: scheduled cleanup, 30-day project recovery, Workspace deletion/transfer safeguards, and user-account deletion workflow.

- [ ] Use a fake clock to test every retention duration in Section 7, source/Social Account expiry and revocation, OAuth Grant/checkpoint deletion, rejected generated drafts, unselected stock previews, active-job/Publication exclusion, Workspace-prefix containment, retry after object-store failure, session revocation, audit tombstones, and database deletion ordering.
- [ ] Implement retention as tombstone-driven batches with row locking and bounded object listings; record each failed attempt without broadening its target prefix.
- [ ] Make soft deletion immediately hide the project and prevent new jobs while preserving recovery until `eligible_at`.
- [ ] Require recent authentication for user-account deletion, revoke sessions immediately, prevent deletion by the last Workspace owner until ownership is transferred or the Workspace is explicitly deleted, remove memberships, cancel that actor's unauthorized future work, and enqueue only exact Workspace/user tombstones.
- [ ] On Workspace deletion, revoke source/social grants, cancel unpublished scheduled Publications where possible, preserve minimal compliance/audit records, and clearly state that already-published external posts remain on their platforms unless separately removed there.
- [ ] Run focused/full tests; expect success.
- [ ] Commit with `feat: enforce recoverable data retention`.

### Task 46: Containerize local and production processes with pinned media tooling

**Files:**
- Create: `infra/docker/backend.Dockerfile`
- Create: `infra/docker/frontend.Dockerfile`
- Complete: `infra/compose.yaml`
- Create: `infra/railway/api.toml`
- Create: `infra/railway/worker-ingest-ai.toml`
- Create: `infra/railway/worker-source-import.toml`
- Create: `infra/railway/worker-broll.toml`
- Create: `infra/railway/worker-render.toml`
- Create: `infra/railway/worker-social-publish.toml`
- Create: `infra/railway/worker-social-reconcile.toml`
- Create: `infra/railway/scheduler.toml`
- Create: `scripts/verify-runtime.sh`
- Remove after cutover: `nixpacks.toml`.

**Interfaces:**
- Produces: reproducible frontend, API, isolated source-import, AI, B-roll, render, social-publish, social-reconcile worker, and scheduler images.

- [ ] Pin an FFmpeg build/version and assert required encoders, filters, fonts, and ffprobe at image build and worker startup.
- [ ] Pin yt-dlp, yt-dlp-ejs, Deno, and any approved PO Token plugin only in the source-import image; give that image no transcription, LLM, generation, render, login, or social-publishing credentials.
- [ ] Run containers as non-root with read-only root filesystems, writable job temp volumes, CPU/memory limits, health checks, graceful shutdown, and no baked secrets.
- [ ] Configure source-import, AI, B-roll retrieval/generation, render, social-rendition, social-publish, and reconciliation concurrency independently; set Celery prefetch to one for media/generation/publishing queues and enforce provider/account-specific admission limits.
- [ ] Give publishing workers only the provider credentials they require; access OAuth Grants through the bounded Secret Manager/KMS path, and keep decrypted values out of environment dumps, health endpoints, and crash reports.
- [ ] Add a Compose smoke script that migrates the database, seeds a fixture user/personal Workspace/project, processes a fixture video, obtains candidates, plans/retrieves fake B-roll, accepts one suggestion, saves an edit, renders an export, publishes it through fake YouTube/Instagram/TikTok adapters, and verifies the MP4, preserved dialogue audio, partial-success state, and idempotent retry.
- [ ] Run `docker compose -f infra/compose.yaml up --build` and `scripts/verify-runtime.sh`; expect all health and smoke checks to pass.
- [ ] Commit with `build: add reproducible production services`.

### Task 47: Add CI, security scanning, load tests, and recovery drills

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/security.yml`
- Create: `.github/workflows/python-314-compat.yml`
- Create: `backend/tests/security/`
- Create: `tests/load/k6.js`
- Create: `docs/operations/recovery.md`

**Interfaces:**
- Produces: required pull-request gates and documented operational recovery procedures.

- [ ] Configure CI jobs for backend lint/type/test/coverage, frontend lint/type/unit/build, OpenAPI/schema generation cleanliness, Postgres/RLS/Redis/MinIO integration, editor-engine and social-provider contracts, Playwright E2E, FFmpeg golden frames, and Docker smoke.
- [ ] Add separate non-blocking Python 3.14 compatibility CI and keep production pinned to 3.13 until every dependency and media smoke test passes.
- [ ] Add pip-audit, pnpm audit with a documented severity gate, secret scanning, Semgrep/Bandit rules, container scanning, provider/editor dependency license reporting, and a gate against unknown or prohibited licenses.
- [ ] Add security tests for cross-Workspace UUID access and RLS bypass, membership removal, CSRF, session fixation, OAuth state/PKCE/callback replay, token/checkpoint leakage, webhook signature/replay, open redirects, unsafe CORS, upload key manipulation, path traversal, SSRF, provider media-fetch URLs, decompression/media bombs, cookie-jar leakage, malicious PO Token plugin output, stock metadata/LLM HTML, FFmpeg filter injection, and signed URL expiry.
- [ ] Add k6 scenarios for dashboard reads, upload-session creation, job polling/SSE, render admission, Publication draft/confirm, scheduler claims, webhook bursts, and reconciliation; assert p95 API latency below 500 ms excluding uploads/providers, zero cross-Workspace leakage, and correct limit responses.
- [ ] Document and execute local drills for API restart during work, yt-dlp extractor breakage, source/Social Account expiry, worker termination during FFmpeg/generation/social transfer, Redis restart, transcription/model/stock/generation/social provider timeout, webhook loss/replay, scheduler pause, OAuth refresh race, ambiguous publish completion, object-store outage, migration rollback, and idempotent task replay.
- [ ] Commit with `ci: enforce production quality and recovery gates`.

### Task 48: Migrate, cut over, remove legacy behavior, and update product documentation

**Files:**
- Create: `scripts/legacy-smoke.sh`
- Create: `scripts/new-stack-smoke.sh`
- Create: `docs/operations/cutover.md`
- Modify: `README.md`
- Modify: `ENVIRONMENT_SETUP.md`
- Remove after the rollback window: `app.py`, `templates/`, `static/script.js`, `static/style.css`, root `requirements.txt`, obsolete root frontend files, and unused dependencies.

**Interfaces:**
- Produces: an auditable feature-flagged cutover and a clean repository with one frontend and one backend package.

- [ ] Inventory environment variables and map each legacy key to the new configuration; explicitly remove unused `google-generativeai`, Flask-CORS, MoviePy after FFmpeg parity is proven, the OpenAI-compatible Groq shim, retired Llama 4 Scout IDs, global debug defaults, and misleading Gemini/Groq documentation.
- [ ] Deploy database/storage/job foundations with the new UI hidden behind `NEW_CLIPAH_ENABLED`.
- [ ] Run old and new smoke scripts against the same sanitized fixture inputs and compare candidate timing, titles, subtitles, and final-media validity; document intentional differences.
- [ ] Enable the core new flow for one internal Workspace, then 10%, 50%, and 100% of eligible traffic. At each stage require 24 hours without cross-Workspace incidents, data loss, queue/scheduler-age alerts, or a render failure rate above 5% before advancing.
- [ ] Roll out social publishing only through Task 43's provider gates; TikTok remains official draft fallback until Direct Post audit approval, and no provider flag may bypass required consent, privacy, disclosure, or review restrictions.
- [ ] Preserve the legacy deployment for a seven-day rollback window. Rollback changes routing only; durable new records and objects remain intact.
- [ ] After the window, remove the legacy Flask routes, global state, thread launcher, shared files, duplicate frontend, obsolete dependencies, and Nixpacks configuration.
- [ ] Update README and environment documentation with architecture, Workspace/account model, dashboard routes, local setup, migrations, workers, storage, transcription/model routing, yt-dlp/cookie risk, editor-engine ADR, B-roll providers/provenance, generation quotas, brand/campaign workflows, official social-provider setup/audits/scopes, scheduling behavior, data-deletion callbacks, testing, deployment, retention, and incident links.
- [ ] Run every command in Section 12 and verify `git grep` finds no `processing_status`, `threading.Thread`, `main_video.mp4`, `cookies.txt`, `nocheckcertificate`, `innerHTML`, or legacy `/process`, `/status`, `/download`, `/reset` application routes.
- [ ] Commit with `refactor: complete production cutover`.

---

## 12. Final Verification Matrix

The rebuild is complete only when every command and behavioral gate below passes on a clean checkout.

### Automated commands

```bash
cd backend
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest --cov=clipah --cov-fail-under=90
uv run alembic upgrade head

cd ../frontend
pnpm install --frozen-lockfile
pnpm lint
pnpm typecheck
pnpm test --run
pnpm build
pnpm playwright test

cd ..
docker compose -f infra/compose.yaml up --build -d
scripts/verify-runtime.sh
scripts/new-stack-smoke.sh
scripts/run-highlight-eval.sh --adapter=fake
scripts/run-transcription-eval.sh --adapter=fake
scripts/run-broll-eval.sh --adapter=fake
scripts/run-social-provider-contracts.sh --adapter=fake
scripts/check-editor-licenses.sh
```

### Required behavioral proofs

- Every new user receives one personal Workspace; changing an identity email does not create a new user, and equal emails from different issuers never auto-link accounts.
- Two Workspaces can upload and process videos concurrently without status, file, candidate, edit, Social Account, Publication, or download leakage; composite tenant foreign keys and RLS reject deliberate cross-Workspace access even when application filters are omitted in a test fixture.
- Workspace role changes and member removal take effect immediately, the last owner cannot leave/delete their account without transfer or explicit Workspace deletion, and a removed member cannot dispatch previously prepared Publications.
- Killing and restarting an API process does not affect running jobs.
- Killing a worker causes a recoverable task to retry without duplicate transcript, candidate, revision, or artifact records.
- Retrying every create request with the same idempotency key returns the same resource.
- A project becomes reviewable before any final clip render exists.
- One source transcription supplies all candidate excerpts and caption words.
- Indonesian, English, and code-switched fixtures route to an eligible transcription model and retain provider word/speaker metadata.
- No configured production model ID is retired; extraction and reranking responses pass strict schema plus local domain validation.
- Model output cannot create timestamps outside authoritative transcript words.
- Context warnings catch labeled misleading/incomplete cuts and every accepted variant maps to authoritative word boundaries.
- Untrusted title, summary, transcript, filename, and watermark text render as text and cannot execute HTML, JavaScript, shell, path, or FFmpeg filter syntax.
- Every editor action changes only composition revisions; the source asset checksum remains unchanged.
- The selected browser editor adapter passes the recorded license, long-timeline, memory, seeking, Safari-degradation, serialization, and preview/render parity gates.
- Preview/render golden frames meet the SSIM gate.
- Proposed B-roll never changes a composition. Accepted B-roll remains editable, preserves dialogue audio by default, and retains complete provenance.
- Stock retrieval precedes generation; a generated-video job cannot start without an estimate, quota reservation, moderation, and explicit confirmation.
- Revoked/expired source connections cannot be leased, plaintext cookie material appears in no durable store or telemetry, and public import works without account cookies.
- Login sessions, source-import credentials, and Social Account OAuth Grants remain separate; no plaintext OAuth token, authorization code, resumable checkpoint, or client secret appears in durable application data, logs, events, analytics, API responses, or job arguments.
- Brand/template constraints are deterministic and immutable versions keep old edits reproducible.
- Search and collaboration return only resources permitted to the current Workspace role.
- Canceling source import, ingest, analysis, B-roll retrieval/generation, campaign generation, rendering, or an eligible unpublished Publication reaches a truthful terminal/paused state, reconciles any quota reservation, and cleans only that job's temporary workspace; an irreversible provider-side post is never mislabeled canceled.
- Publication confirmation snapshots an immutable artifact revision, account, metadata, consent, visibility, capability version, actor, and schedule; later edits or provider changes cannot silently alter it.
- Publish-now and scheduled work survive API/worker/scheduler restarts. Duplicate delivery and ambiguous provider responses reconcile without duplicate external posts wherever the official provider API supports recovery.
- Each YouTube/Instagram/TikTok destination is an independent Publication: one failure produces visible partial success and retrying it never republishes successful siblings.
- YouTube audit restrictions, Instagram account/container/content-publishing limits, and TikTok creator/privacy/disclosure/consent rules are enforced from current capabilities. TikTok uses the official draft fallback until Direct Post audit approval.
- Disconnecting a Social Account immediately blocks new dispatch, safely cancels or pauses eligible future Publications, attempts token revocation, and retains no reusable plaintext grant.
- Signed download URLs expire after five minutes and private object keys are never returned by ordinary metadata endpoints.
- Project, Workspace, and account retention delete only exact `workspaces/{workspace_id}/` prefixes and are recoverable according to policy; deleting Clipah data does not falsely claim to delete posts already published externally.
- Production starts with debug disabled and refuses missing secrets or dependency health.

### Manual acceptance scenarios

1. Upload a one-hour Indonesian/code-switched podcast, verify Universal-2 routing and real diarization, review context-safe ranked clips with explanations, create three hook/duration variants, edit captions/crop, export a 9:16 MP4, and download it.
2. Import a public English YouTube video without cookies, cancel during transcription, retry, and complete the project without duplicate provider calls after the successful stage.
3. In an explicitly enabled test environment, connect an expiring cookie jar, import an authorized restricted fixture, revoke the connection, prove another import fails cleanly, and verify no plaintext credential remains.
4. Request `balanced` B-roll, inspect Indonesian/English search intent and provenance, accept stock image/video suggestions, replace and remove items, verify dialogue audio, and export the approved edit.
5. Force stock relevance below threshold, review a still/video estimate, reject one generation without charge, approve one fake-provider generation, edit its placement, and verify generated provenance plus quota reconciliation.
6. Open the same edit in two browsers, create a revision conflict, choose the latest server revision, reapply the local change, and save successfully.
7. Exercise undo/redo, split, ripple, snapping, bookmarks, text, sound, karaoke, motion, template, keyframe, smart-crop, B-roll, accessibility, and brand constraint controls and verify the exported result.
8. Generate platform packaging and campaign outputs from an approved revision, search the old transcript by guest/topic/claim, and open the result at the correct timecode.
9. Create a team Workspace, invite an editor and reviewer, verify their permission boundaries, leave a timestamped comment, request changes, create a new revision, approve it, remove the editor, and confirm the prior approval is stale and removed access is immediate.
10. Connect test YouTube, Instagram, and TikTok Social Accounts; verify scopes/capabilities and redacted status, rotate/expire one token, reconnect it, disconnect another, and confirm login plus yt-dlp credentials are unaffected.
11. Publish an approved immutable clip immediately to fake YouTube, Instagram, and TikTok destinations; force one failure, verify visible partial success, retry only that destination, and prove the successful destinations were not duplicated.
12. Schedule a multi-destination batch in `Asia/Jakarta`, restart the API, scheduler, and workers before the due time, change one provider capability, and verify the unchanged destinations publish while the changed destination returns to `awaiting_approval` with an explicit diff.
13. Exercise YouTube audience/synthetic-content/privacy fields, Instagram professional-account/container flow, and TikTok refreshed creator-info/privacy/interactions/commercial-content consent. Verify TikTok is labeled as draft fallback until the audited Direct Post flag is enabled.
14. Soft-delete and restore a project, transfer Workspace ownership, then delete a user account and a separate test Workspace; verify sessions, memberships, source/social grants, scheduled Publications, generated drafts, database rows, audit tombstones, and exact object prefixes follow retention policy while already-published external posts remain untouched.

---

## Execution Guidance

- Execute tasks in numerical order. Tasks within one phase may be parallelized only when their listed interfaces are already merged.
- Start each task from a green branch, use its focused failing test first, and run the full relevant suite before its commit.
- Review each task independently for specification compliance and code quality before beginning the next task.
- Keep schema and OpenAPI artifacts generated and committed; CI must fail when generation produces an uncommitted diff.
- Record changes to the approved scope directly in this file with rationale before implementation so later tasks consume one source of truth.
