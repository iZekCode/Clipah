# Direct Social Publishing Options for Clipah

Date: 2026-08-29  
Scope: YouTube Shorts, Instagram Reels, and TikTok direct publishing  
Source policy: first-party platform documentation only. Meta's official Instagram Postman workspace is used where `developers.facebook.com` is difficult to retrieve; the workspace is published by Meta.

## Executive recommendation

Direct publishing is feasible on all three platforms, but it is not one generic "upload video" call:

| Platform | Production eligibility | Upload model | Native scheduling | Completion signal | Short-form classification |
| --- | --- | --- | --- | --- | --- |
| YouTube | Google OAuth verification may be required; a separate YouTube API compliance audit is required to remove private-only uploads from newer unverified projects | Google resumable upload, optionally chunked | Yes, `status.publishAt` | Poll `videos.list`; channel push notifications do not report processing completion | Inferred by YouTube from aspect ratio and duration; there is no Shorts upload flag |
| Instagram | Professional account; Advanced Access/App Review for customer accounts | Public URL pull, or Meta resumable upload in supported login flow | No documented publish-time field; Clipah schedules its own `media_publish` job | Poll media-container status; no publishing webhook is documented | Explicit: create the container with `media_type=REELS` |
| TikTok | App and `video.publish` approval; audit required for non-private posts | Verified-domain URL pull or sequential chunk upload | No documented scheduling field; Clipah schedules initiation | First-class webhooks plus polling fallback | No separate short-form class; this publishes a normal TikTok video |

Build one provider-neutral publishing domain with provider-specific capability adapters. Do not flatten platform choices into one lowest-common-denominator form: TikTok's mandatory consent UI, Instagram's container lifecycle, and YouTube's scheduling semantics must remain visible.

## 1. YouTube Shorts

### Eligibility, OAuth, and scopes

- Create a Google Cloud project, enable YouTube Data API v3, and use user OAuth 2.0. `videos.insert` accepts the narrow `https://www.googleapis.com/auth/youtube.upload` scope; broader `youtube`, `youtube.force-ssl`, and partner scopes also work, but Clipah should request least privilege ([`videos.insert`](https://developers.google.com/youtube/v3/docs/videos/insert)).
- Uploading a caption track requires `youtube.force-ssl` or the YouTube Partner scope, so make caption-track publishing a separately consented capability instead of silently expanding the initial upload scope ([`captions.insert`](https://developers.google.com/youtube/v3/docs/captions/insert)).
- For scheduled/background publishing, request offline access (`access_type=offline`), store the refresh token server-side, and refresh access tokens at `https://oauth2.googleapis.com/token`. Google notes that refresh tokens are returned for offline access and allow calls while the user is absent ([OAuth for web-server apps](https://developers.google.com/identity/protocols/oauth2/web-server)).
- Expose disconnect in Clipah and revoke the token at Google's revocation endpoint; also treat `invalid_grant` as a reconnect-required state. Revocation can invalidate the grant across the project's clients, not merely the current browser session ([YouTube OAuth guide](https://developers.google.com/youtube/v3/guides/auth/server-side-web-apps)).
- Public production apps requesting sensitive/restricted scopes must complete the applicable OAuth verification. Google requires declared least-privilege scopes, verified domains, accurate home/privacy-policy URLs, scope justification, and a demo video ([sensitive-scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification)).
- A second, YouTube-specific gate exists: uploads made by unverified API projects created after 2020-07-28 are forced to private until the project passes a YouTube API compliance audit ([`videos.insert`](https://developers.google.com/youtube/v3/docs/videos/insert)). OAuth verification and the YouTube API compliance audit are different reviews.

### Upload and publish flow

1. Let the user select the connected YouTube channel and explicitly select/deselect YouTube as a destination.
2. Collect title, description, privacy, audience/made-for-kids choice, synthetic-media disclosure where applicable, and optional scheduled time. YouTube's required minimum functionality says an upload client must let users set title, description, and `public`/`private`/`unlisted` privacy; a multi-platform client must let users select and deselect destinations ([required minimum functionality](https://developers.google.com/youtube/terms/required-minimum-functionality)).
3. Initiate `POST https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status` with metadata. Save the session URI from the `Location` response header ([resumable upload protocol](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol)).
4. Upload bytes with `PUT`. On interruption or retriable 5xx, query the session with an empty `PUT` and `Content-Range: bytes */TOTAL`; a `308 Resume Incomplete` response and its `Range` header identify the next byte. Retry with exponential backoff. Session URIs have a finite lifetime.
5. Save the returned YouTube video ID, then poll `videos.list(part=status,processingDetails)` until processing succeeds or fails. YouTube explicitly describes `processingDetails.processingStatus` as polling data ([video implementation guide](https://developers.google.com/youtube/v3/guides/implementation/videos)).
6. Optionally call `thumbnails.set` and `captions.insert` after the video ID exists.

Chunking is supported, but YouTube says it adds request overhead. Chunk sizes must be a multiple of 256 KB except the final chunk; use a large stable chunk or a single resumable transfer from Clipah's server, while retaining resume checkpoints ([resumable upload protocol](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol)).

### Media, metadata, cover, captions, privacy, and scheduling

- API upload maximum: 256 GB; accepted MIME types are `video/*` and `application/octet-stream` ([`videos.insert`](https://developers.google.com/youtube/v3/docs/videos/insert)). Clipah should still emit a conservative common deliverable such as MP4/H.264/AAC, 9:16.
- A standard-channel upload made after 2024-10-15 is categorized as a Short when it is square or vertical and no longer than three minutes. Official Artist Channels use the same rule for uploads after 2025-12-08. A Short over one minute with an active copyright claim is blocked globally ([three-minute Shorts](https://support.google.com/youtube/answer/15424877?hl=en)).
- There is no `SHORTS` endpoint, resource type, or `is_short` input in `videos.insert`. Classification is therefore platform inference from the uploaded media, not an API promise Clipah can force.
- Settable upload fields include title, description, tags, category, localization, privacy, `publishAt`, made-for-kids, and `containsSyntheticMedia` ([`videos.insert`](https://developers.google.com/youtube/v3/docs/videos/insert)). The required UI limits are title <=100 characters and description <=5000 bytes ([required minimum functionality](https://developers.google.com/youtube/terms/required-minimum-functionality)).
- Privacy is `private`, `public`, or `unlisted`. Native scheduling uses `status.publishAt`, but only while the video is private and has never been published; a past timestamp publishes immediately ([video resource](https://developers.google.com/youtube/v3/docs/videos)).
- `thumbnails.set` accepts JPEG/PNG up to 2 MB through the API and costs about 50 quota units. The channel must have thumbnail permission ([`thumbnails.set`](https://developers.google.com/youtube/v3/docs/thumbnails/set)). Current YouTube Help says verified accounts can upload custom Shorts thumbnails in YouTube Studio on desktop; because that Help page describes a Studio UI capability rather than explicitly guaranteeing Data API behavior for Shorts, treat `thumbnails.set` on a Short as a capability probe and keep a graceful fallback ([custom thumbnails](https://support.google.com/youtube/answer/72431)).
- `captions.insert` uploads a separate track up to 100 MB and costs 400 quota units. It requires video ID, language, and track name; `isDraft` is supported. The old automatic `sync` parameter is deprecated, so Clipah should upload timed captions rather than depend on YouTube retiming ([`captions.insert`](https://developers.google.com/youtube/v3/docs/captions/insert)).

### Quotas, review, status, and compliance

- Current default allocation is 100 `videos.insert` calls/day, 100 `search.list` calls/day, and 10,000 units/day combined for other endpoints. A video upload currently costs one unit in its separate Video Uploads bucket; the defaults are explicitly subject to change ([Data API overview](https://developers.google.com/youtube/v3/getting-started)). Do not retain the old 1,600-unit upload assumption.
- More quota requires a compliance audit; YouTube can also require periodic audits and a change-of-control filing ([quota and compliance audits](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits)).
- YouTube's PubSubHubbub notifications cover a channel upload and title/description changes, not processing completion. Use them only as reconciliation hints and poll the owner-only processing fields for authoritative processing state ([push notifications](https://developers.google.com/youtube/v3/guides/push_notifications)).
- Upload status exposes `uploaded`, `processed`, `failed`, and `rejected`, plus machine-readable failure/rejection reasons including codec, conversion, copyright, duplicate, length, and terms-of-use failures ([video resource](https://developers.google.com/youtube/v3/docs/videos)). Persist both normalized and raw provider errors.
- Before upload, require user certification that the content complies with YouTube Community Guidelines. Keep the user in control of the target channel, content, and visibility, and provide privacy/data deletion controls ([developer-policy guidance](https://developers.google.com/youtube/terms/developer-policies-guide)).
- A publishing grant is not a grant to download or modify arbitrary YouTube media. YouTube's API policies prohibit clients from enabling audiovisual downloads or separating/modifying YouTube audio/video. Clipah must keep source-ingestion rights and publishing authorization as separate concerns.

## 2. Instagram Reels

### Preferred login path, accounts, permissions, and review

Prefer **Instagram API with Instagram Login** for a Clipah SaaS integration unless Facebook Page or ad/tagging capabilities are needed:

- It supports Instagram Professional accounts (Business and Creator) without requiring a linked Facebook Page.
- Request `instagram_business_basic` and `instagram_business_content_publish`; the older `business_*` aliases were deprecated on 2025-01-27 ([Meta's official Instagram collection](https://www.postman.com/meta/workspace/instagram/documentation/23987686-9386f468-7714-490f-9bfc-9442db5c8f00)).
- It cannot access ads or tagging. If those become requirements, evaluate Facebook Login for Business separately.
- Standard Access is for professional accounts Clipah owns/manages and has added in App Dashboard. Serving customer accounts Clipah does not own/manage requires Advanced Access and therefore App Review ([Meta's official Instagram collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api?entity=request-23987686-ab559ffb-8e2c-4b0a-b43a-5737b6d2f672)).

The legacy/alternative **Instagram API with Facebook Login** requires a Facebook Page linked to the Instagram Professional account and uses `instagram_basic`, `instagram_content_publish`, `pages_show_list`, and `pages_read_engagement` for the shown publishing/account-discovery flow ([Meta's official Instagram collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api?entity=request-23987686-b217bc6c-d9fb-4d65-bf5e-fcadf650ad5c)). Do not mix the two login families' hosts, IDs, tokens, or scope names in one adapter instance.

### Token lifecycle and disconnect

- Business Login for Instagram issues a short-lived token; exchange it server-side for a long-lived token through `graph.instagram.com/access_token?grant_type=ig_exchange_token`. Long-lived tokens are valid for 60 days ([Instagram access-token reference](https://developers.facebook.com/docs/instagram-platform/reference/access_token)).
- Refresh an unexpired long-lived token after it is at least 24 hours old using `graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token`; a successful refresh resets validity to 60 days ([refresh-token reference](https://developers.facebook.com/docs/instagram-platform/reference/refresh_access_token)). Refresh well before expiry and mark the account reconnect-required if expiry/revocation wins the race.
- On user disconnect, call the current Instagram permissions-deletion endpoint, delete Clipah's encrypted token, cancel unpublished jobs, and honor Meta's deauthorization/data-deletion callbacks ([Instagram permissions reference](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/reference/permissions)).

### Reels publish flow

1. Create an unpublished media container with `POST /{ig-user-id}/media`, `media_type=REELS`, the media source, and optional Reel fields.
2. Poll `GET /{container-id}?fields=status_code,status` until `FINISHED`. States include `EXPIRED`, `ERROR`, `FINISHED`, `IN_PROGRESS`, and `PUBLISHED`; Meta recommends once per minute for no more than five minutes.
3. Publish with `POST /{ig-user-id}/media_publish?creation_id={container-id}`. Store the returned IG media ID.

The current content-publishing guide documents no publishing webhook (`Webhooks: -`), so polling is the primary completion mechanism. Containers expire if not published within 24 hours ([Meta's official content-publishing collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api?entity=request-23987686-ab559ffb-8e2c-4b0a-b43a-5737b6d2f672)).

### Transfer and media constraints

- Simplest server-side path: provide an HTTPS `video_url` reachable by Meta. Meta fetches it, so keep a narrowly scoped signed URL alive until the container reaches a terminal state; do not expose a permanent public asset URL ([official Reels publishing example](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api?entity=request-23987686-d34db4fa-de2e-433a-9449-a55a755b5bc0)).
- The content guide also documents `upload_type=resumable` and `rupload.facebook.com/ig-api-upload/` for large/local videos, but says this option is only for apps implementing Facebook Login for Business. Do not assume Instagram Login and Facebook Login have identical upload capabilities; make transfer mode a provider-connection capability ([Meta's official content-publishing collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api?entity=request-23987686-ab559ffb-8e2c-4b0a-b43a-5737b6d2f672)).
- Official Reel constraints: MOV or MP4 container; H.264 or HEVC video; AAC at 48 kHz; 23–60 FPS; maximum 1,920 horizontal pixels; recommended 9:16; maximum 25 Mbps video and 128 kbps audio; 3 seconds to 15 minutes; maximum 1 GB ([Meta's official Instagram collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api?entity=request-23987686-ab559ffb-8e2c-4b0a-b43a-5737b6d2f672)).

### Caption, cover, privacy, scheduling, and classification

- `media_type=REELS` explicitly tells Instagram to create a Reel. This differs from YouTube's inferred Shorts classification.
- The container supports a caption; there is no separate Reel title. `share_to_feed=true` places the Reel in both Feed and the Reels tab, while `false` keeps it in the Reels tab ([official Reels publishing example](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api?entity=request-23987686-d34db4fa-de2e-433a-9449-a55a755b5bc0)).
- The current IG User media reference documents Reel cover options such as a public `cover_url` or a video-frame `thumb_offset`; expose these as Instagram-only capabilities and validate against the pinned Graph API version ([IG User media reference](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/reference/ig-user/media)).
- The publishing flow exposes no per-post privacy selector. Visibility follows Instagram/account behavior; do not invent `public/private/unlisted` mappings from YouTube.
- No native scheduled publish timestamp is documented. Schedule inside Clipah, then create/publish near the requested time. Do not create the container days early because it expires in 24 hours.
- The API publishes the supplied media and caption; it does not expose Instagram's licensed music-picker/editor flow. Any audio must already be lawfully embedded in the rendered file.

### Limits and operational caveat

- Meta's current general content-publishing guide says 100 API-published posts per rolling 24 hours and provides `GET /{ig-user-id}/content_publishing_limit` for actual usage. However, another section of Meta's same current official collection says 50 posts/24 hours for carousels. Because Meta's first-party documentation is internally inconsistent, Clipah must treat `content_publishing_limit` and provider errors as authoritative rather than hard-code 100 ([official content-publishing collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api?entity=request-23987686-ab559ffb-8e2c-4b0a-b43a-5737b6d2f672)).
- Enforce a per-account reservation ledger for scheduled jobs, but reconcile it with the live limit immediately before publishing.

## 3. TikTok

### Eligibility, OAuth, and audit

- Register a TikTok for Developers app, add Content Posting API, enable Direct Post, obtain approval for `video.publish`, and obtain the same scope from each user ([Direct Post getting started](https://developers.tiktok.com/docs/en/content-posting-api-get-started)).
- Use Login Kit's authorization-code flow. Access tokens last 24 hours; refresh tokens last 365 days. Refresh on the server using `/v2/oauth/token/`, and always store the newly returned refresh token because it may rotate. Revoke on disconnect with `/v2/oauth/revoke/` ([user access-token management](https://developers.tiktok.com/docs/en/oauth-user-access-token-management)).
- Unaudited clients are limited to `SELF_ONLY`, at most five posting users per 24-hour window, and those user accounts must be private. An audit is required for public/friends publishing ([Content Sharing Guidelines](https://developers.tiktok.com/docs/en/content-sharing-guidelines)).
- Both audited and unaudited apps have a 24-hour active-creator cap based on the audit estimate and a creator posting cap that varies by creator, typically around 15 API posts/day shared across all API clients. Treat creator-info/provider errors as runtime authority.
- TikTok says Direct Post must serve authentic creators posting original content. A tool that copies arbitrary content from other platforms, or an internal-only uploader for a team's accounts, is explicitly unacceptable. Clipah's review narrative should emphasize creator-owned source material, substantive clipping/editing value, user preview, and final user control—not cross-platform scraping.

### Mandatory user-facing preflight

Every time the Post-to-TikTok screen is rendered:

1. Call `POST /v2/post/publish/creator_info/query/` with `video.publish`.
2. Display the latest creator nickname/account.
3. Validate duration against `max_video_post_duration_sec`.
4. Show only returned `privacy_level_options`; the user must choose one manually, with no default.
5. Show Comment/Duet/Stitch controls based on returned account restrictions; the user must turn them on manually and none may default on.
6. Show an editable preview and editable caption/hashtags. Obtain explicit upload consent and the required TikTok Music Usage Confirmation.
7. Provide commercial-content disclosure UI. Map "Your brand" to `brand_organic_toggle`, third-party paid partnership to `brand_content_toggle`, enforce its privacy constraints, and display the required policy confirmation.

These are audit requirements, not optional UI polish ([Content Sharing Guidelines](https://developers.tiktok.com/docs/en/content-sharing-guidelines)).

### Direct-post flow and fields

1. After explicit consent, call `POST /v2/post/publish/video/init/` with `video.publish`, `post_info`, and a transfer source.
2. Save the returned `publish_id`. For `FILE_UPLOAD`, also save the one-hour `upload_url` and upload bytes before it expires.
3. Track the result with signed webhooks and `POST /v2/post/publish/status/fetch/` fallback.

Supported post fields include:

- Required privacy from the latest creator options: `PUBLIC_TO_EVERYONE`, `MUTUAL_FOLLOW_FRIENDS`, `FOLLOWER_OF_CREATOR`, or `SELF_ONLY` as allowed.
- `title`, which is actually the video caption, with mentions/hashtags and a maximum of 2,200 UTF-16 code units.
- `disable_comment`, `disable_duet`, and `disable_stitch`.
- `video_cover_timestamp_ms` to select a frame; there is no documented custom cover-image upload for video.
- Required commercial toggles and optional `is_aigc` disclosure, which applies TikTok's creator-labeled AI-generated tag.

See the current [`Direct Post` reference](https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post). No scheduled timestamp exists in the request, so a scheduled Clipah job must refresh creator info and initiate posting at execution time, not at schedule-creation time.

If Clipah wants the user to finish with TikTok's native editor instead, implement the separate Upload-to-TikTok draft flow with `video.upload` and `/v2/post/publish/inbox/video/init/`. It sends an inbox notification and requires the creator to complete publishing in TikTok; it is not direct publish ([Upload API](https://developers.tiktok.com/docs/en/content-posting-api-reference-upload-video)).

### File transfer and media constraints

Use `PULL_FROM_URL` when the render already lives in Clipah's server-side storage; TikTok explicitly says not to use `FILE_UPLOAD` for server-hosted resources. The HTTPS URL must be under a domain or URL prefix verified in the TikTok app, must not redirect, must remain available for up to the one-hour download window, and should be cancellable through TikTok's cancel endpoint ([media transfer guide](https://developers.tiktok.com/docs/en/content-posting-api-media-transfer-guide)).

For device/local uploads, `FILE_UPLOAD` uses sequential `PUT` chunks:

- Chunk size 5–64 MB, except a final chunk may reach 128 MB.
- Files under 5 MB must be one chunk; files over 64 MB must use multiple chunks.
- 1–1,000 chunks, uploaded sequentially with `Content-Range`.
- Intermediate success is HTTP 206; final success is 201. Retry a failed current chunk on retryable 5xx and reconcile using returned uploaded-byte progress.

Current video restrictions are MP4 (recommended), WebM, or MOV; H.264 (recommended), H.265, VP8, or VP9; 23–60 FPS; each dimension 360–4,096 pixels; maximum 4 GB. The init endpoint permits at most 10 minutes, but the creator-specific maximum from creator info may be lower ([media transfer guide](https://developers.tiktok.com/docs/en/content-posting-api-media-transfer-guide)).

### Rate limits, status, webhooks, and restrictions

- Creator-info: 20 requests/minute per user access token ([Query Creator Info](https://developers.tiktok.com/docs/en/content-posting-api-reference-query-creator-info)).
- Direct-post init: 6 requests/minute per user access token ([Direct Post](https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post)).
- Status fetch: 30 requests/minute per user access token ([Get Post Status](https://developers.tiktok.com/docs/en/content-posting-api-reference-get-video-status)).
- Statuses include upload/download processing, inbox delivery for draft uploads, `PUBLISH_COMPLETE`, and `FAILED`. Public post IDs are withheld until moderation completes; moderation is usually under a minute but can take hours.
- Webhook events include publish failed, complete, inbox delivered, publicly available, and no longer publicly available. TikTok delivers at least once, can duplicate events, retries failures with exponential backoff for up to 72 hours, and requires immediate HTTP 200 acknowledgement. Verify webhook signatures and make event handling idempotent ([webhook overview](https://developers.tiktok.com/docs/en/webhooks-overview), [webhook verification](https://developers.tiktok.com/docs/en/webhooks-verification)).
- TikTok forbids Clipah-added brand names, logos, promotional watermarks, links, or promotional text superimposed on shared content. This restriction should be a render preflight rule, not merely a warning ([Content Sharing Guidelines](https://developers.tiktok.com/docs/en/content-sharing-guidelines)).

## 4. Provider-neutral publishing architecture

### Domain records

**`social_accounts`**

- `id`, `workspace_id`, `provider`, `provider_account_id`, display name/avatar.
- Login family/API version, granted scopes, capability snapshot, account type.
- Envelope-encrypted access and refresh tokens, expiry timestamps, token version.
- `connected | refresh_required | revoked | disabled`, last validation time, raw provider metadata.

**`publication_jobs`** — one row per destination, even when the user clicks "publish to all"

- `id`, `clip_id`, immutable `render_artifact_id`, `social_account_id`.
- User-approved common metadata plus versioned provider-specific options.
- `scheduled_for`, timezone captured for display, `not_before`, priority.
- State: `draft -> awaiting_consent -> scheduled -> preflighting -> transferring -> processing -> published`, plus `retryable_failed`, `permanent_failed`, `cancelled`, `reconnect_required`.
- Stable idempotency key, provider upload/session/container/publish/media IDs, permalink.
- Attempt count, next retry, normalized error code, raw provider error, timestamps.

**`publication_attempts` / `provider_events`**

- Append-only request/response metadata with secrets and signed URLs redacted.
- Webhook event ID/hash for deduplication, signature result, received/processed timestamps.
- Audit evidence: who approved which media, account, privacy, disclosures, and metadata version.

### Adapter contract

Each provider adapter should implement:

```text
capabilities(connection) -> supported fields, limits, transfer modes
refreshCredentials(connection)
preflight(job, artifact) -> typed validation issues
begin(job) -> provider checkpoint
transfer(checkpoint, artifact) -> resumable checkpoint
poll(checkpoint) -> normalized status + raw status
cancel(checkpoint) -> best effort
disconnect(connection)
```

Keep provider payloads typed and versioned. Never silently map unsupported fields: for example, YouTube `unlisted` has no Instagram equivalent, Instagram `share_to_feed` has no TikTok equivalent, and TikTok interaction toggles have no YouTube equivalent.

### Execution flow

1. Render one immutable high-quality master, then create provider renditions from explicit profiles. A safe shared baseline is 9:16 MP4/H.264/AAC, but validate duration, bitrate, resolution, file size, and disclosure per destination.
2. At user confirmation, create one job per selected account and snapshot all consented settings. Show partial-success semantics: one provider can fail without rolling back another provider's published post.
3. A durable scheduler claims each job with a lease. Use an outbox/queue transaction and a per-account concurrency lock.
4. Refresh credentials and live capabilities immediately before dispatch. TikTok must refetch creator info; Instagram must check publishing headroom; YouTube must confirm the target channel.
5. Use provider idempotency where available. Where it is not, never blindly repeat a create/publish call after an ambiguous timeout: first reconcile by saved provider ID/status.
6. Persist every resumable checkpoint before sending the next chunk. Resume only the current destination, never re-render or restart all platforms.
7. Webhooks write deduplicated events and return quickly; a worker performs state transitions. Poll with jittered exponential backoff as a fallback, bounded by provider guidance and resource expiry.
8. Expire signed source URLs after the provider pull completes. Never log access tokens, upload URLs, refresh tokens, or asset-signing secrets.
9. On revocation, disable the account, cancel all unpublished jobs, delete or cryptographically erase credentials, and notify the user which schedules need reconnection.

### Scheduling strategy

- **YouTube:** upload early as private and use native `publishAt`; poll processing before the deadline.
- **Instagram:** enqueue a Clipah job near publish time; create the container only when execution begins because it expires in 24 hours.
- **TikTok:** enqueue a Clipah job at publish time, refresh token, then refetch creator info and obtain fresh user-visible consent-compatible settings. For long-future schedules, require a final approval close to execution if TikTok's required options or account capabilities have changed.

### Launch order

1. YouTube private/unlisted publishing with resumable upload and polling.
2. Instagram Login, owned test account, public-URL pull, container polling, then Advanced Access review.
3. TikTok Upload-to-draft as a lower-risk beta if desired, followed by Direct Post after its mandatory UX and audit package are complete.
4. Multi-destination scheduling only after every adapter independently passes revocation, ambiguous-timeout, duplicate-webhook, quota, and partial-failure tests.

The central product requirement is not merely "publish everywhere." It is **publish the exact approved artifact to explicitly selected accounts, with each platform's live rules preserved, and with recoverable evidence of what happened**.
