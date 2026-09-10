# YouTube Shorts Publishing Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish one frozen, approved Social Rendition to its exact YouTube channel through the
official Data API with audit-safe privacy, resumable recovery, truthful processing status, and
independently retryable attachments.

**Architecture:** A typed YouTube adapter owns HTTP schemas and provider semantics behind a small
generic publisher protocol. Resumable session URIs live only in an injected encrypted checkpoint
vault; Postgres stores the opaque reference and safe progress. A narrow persistence coordinator
applies adapter results to one locked Publication, while Task 42 retains queue and multi-provider
dispatch ownership.

**Tech Stack:** Python 3.13, Pydantic v2, httpx, SQLAlchemy 2, Postgres, pytest,
`httpx.MockTransport`.

**Spec:** `docs/superpowers/specs/2026-09-10-youtube-shorts-publisher-design.md`

## Global Constraints

- Work only in `backend/` and rebuild documentation.
- Do not use browser automation, scraping, or unofficial YouTube endpoints.
- Never persist or log access tokens, authorization headers, resumable session URIs, signed media
  URLs, private object keys, or raw provider response bodies.
- Treat Shorts as provider-determined classification; expose only eligibility.
- `youtube.upload` is the minimum publish scope; `youtube.force-ssl` is optional and required only
  for timed-caption upload.
- `youtube_audit_approved` remains the existing fail-closed deployment flag and defaults to false.
- Before audit approval, effective upload privacy is private and frozen confirmation evidence must
  describe that restriction.
- Every ambiguous transfer result must be reconciled before more bytes or a new session are sent.
- Attachment failure must never erase or replay a completed base video upload.
- Do not implement Task 42 worker dispatch, account locks, backoff scheduling, or batch aggregation.
- Do not commit, push, or rebase; the owner creates the single Task 39 commit after all gates pass.

---

### Task 1: Define the typed publisher and YouTube policy values

**Files:**

- Create: `backend/src/clipah/publishing/providers/base.py`
- Create: `backend/src/clipah/publishing/providers/youtube/oauth.py`
- Create: `backend/src/clipah/publishing/providers/youtube/adapter.py`
- Create: `backend/tests/contract/test_youtube_publisher.py`

**Interfaces:**

- Produces: `SocialPublisher[RequestT, CheckpointT, StatusT]`, `YouTubePublishRequest`,
  `YouTubePrivacy`, `YouTubeAttachment`, `YouTubePolicy`, `shorts_eligibility`, and
  `require_youtube_publish_scopes`.
- Consumes: `SocialProvider`, frozen Publication metadata/provider options, aware UTC clocks, and
  Task 38 `MediaFacts`.

- [x] **Step 1: Write closed-value and OAuth scope tests**

```python
def test_upload_scope_is_required_but_caption_scope_is_separate() -> None:
    require_youtube_publish_scopes(frozenset({YOUTUBE_UPLOAD_SCOPE}), captions=False)
    with pytest.raises(YouTubeScopeMissingError):
        require_youtube_publish_scopes(frozenset({YOUTUBE_UPLOAD_SCOPE}), captions=True)
    require_youtube_publish_scopes(
        frozenset({YOUTUBE_UPLOAD_SCOPE, YOUTUBE_CAPTION_SCOPE}), captions=True
    )


def test_shorts_is_only_an_eligibility_result() -> None:
    result = shorts_eligibility(width=1080, height=1920, duration_ms=180_000)
    assert result.eligible is True
    assert result.classification_guaranteed is False
```

- [x] **Step 2: Run the tests and confirm they fail on missing modules**

Run: `uv run pytest -q tests/contract/test_youtube_publisher.py -k 'scope or eligibility'`

Expected: collection fails because the YouTube provider modules do not exist.

- [x] **Step 3: Add the generic protocol and strict immutable YouTube values**

```python
RequestT = TypeVar("RequestT")
CheckpointT = TypeVar("CheckpointT")
StatusT = TypeVar("StatusT")


@runtime_checkable
class SocialPublisher(Protocol[RequestT, CheckpointT, StatusT]):
    provider: SocialProvider

    def confirm_destination(self, *, access_token: SecretStr, expected_account_id: str) -> None: ...
    def begin(
        self, *, request: RequestT, media: PublicationMedia, access_token: SecretStr
    ) -> CheckpointT: ...
    def poll(self, *, provider_id: str, access_token: SecretStr) -> StatusT: ...
```

`PublicationMedia` is a protocol exposing immutable `content_type`, `size_bytes`, `sha256`, and
`read_range(start, end) -> bytes`. Implement Pydantic models with
`ConfigDict(extra="forbid", frozen=True, strict=True)` and explicit
validators for title length, UTF-8 description bytes, numeric category ID, aware UTC scheduling,
non-empty tags, timed caption descriptors, and thumbnail descriptors. `shorts_eligibility` returns
an immutable result whose `classification_guaranteed` is always false.

- [x] **Step 4: Implement exact OAuth constants and caption-scope expansion**

```python
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_READ_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
YOUTUBE_CAPTION_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"


def require_youtube_publish_scopes(scopes: frozenset[str], *, captions: bool) -> None:
    required = {YOUTUBE_UPLOAD_SCOPE}
    if captions:
        required.add(YOUTUBE_CAPTION_SCOPE)
    if not required.issubset(scopes):
        raise YouTubeScopeMissingError("required YouTube publishing scope is unavailable")
```

Keep the existing initial Social Account policy unchanged: upload/read remain minimum scopes and
caption authorization is separately expanded.

- [x] **Step 5: Run the focused contract tests**

Run: `uv run pytest -q tests/contract/test_youtube_publisher.py -k 'scope or eligibility or values'`

Expected: all selected tests pass.

---

### Task 2: Build exact channel, metadata, audit, and scheduling requests

**Files:**

- Modify: `backend/src/clipah/publishing/providers/youtube/adapter.py`
- Modify: `backend/src/clipah/publishing/preflight.py`
- Modify: `backend/src/clipah/publishing/use_cases.py`
- Modify: `backend/src/clipah/api/routes/publications.py`
- Modify: `backend/tests/contract/test_youtube_publisher.py`
- Modify: `backend/tests/integration/test_publications.py`

**Interfaces:**

- Produces: `YouTubePublisher.confirm_destination`, `YouTubePublisher.upload_metadata`,
  `YouTubePolicy.effective_privacy`, and durable `youtubePolicy` confirmation evidence.
- Consumes: `Settings.youtube_audit_approved`, Task 38 preflight reports, Social Account external
  channel ID, and frozen Publication metadata/options/consent.

- [x] **Step 1: Write fake-server tests for channel identity and request mapping**

```python
def test_channel_check_requires_the_exact_connected_channel() -> None:
    publisher = _publisher(
        lambda request: httpx.Response(200, json={"items": [{"id": "channel-other"}]})
    )
    with pytest.raises(YouTubeChannelMismatchError):
        publisher.confirm_destination(access_token=TOKEN, expected_account_id="channel-expected")
```

Assert the exact `channels.list` request and add request-body cases for title, UTF-8 description,
individual tags, category ID, `selfDeclaredMadeForKids`, `containsSyntheticMedia`, privacy, and
`publishAt`. An unaudited future schedule must fail before HTTP because scheduling would eventually
make the upload non-private.

- [x] **Step 2: Run the channel/policy tests and confirm failures**

Run: `uv run pytest -q tests/contract/test_youtube_publisher.py -k 'channel or metadata or audit or schedule'`

Expected: selected tests fail because the HTTP and policy methods are absent.

- [x] **Step 3: Implement channel validation and request serialization**

Use `GET /youtube/v3/channels?part=id,snippet&mine=true`, require one returned channel whose ID
equals `expected_account_id`, and discard provider display fields after validation. Construct
`videos.insert` JSON only from validated model fields:

```python
return {
    "snippet": {
        "title": request.title,
        "description": request.description,
        "tags": list(request.tags),
        "categoryId": request.category_id,
    },
    "status": {
        "privacyStatus": policy.effective_privacy(request).value,
        "selfDeclaredMadeForKids": request.made_for_kids,
        "containsSyntheticMedia": request.contains_synthetic_media,
        **({"publishAt": request.scheduled_for.isoformat()} if request.scheduled_for else {}),
    },
}
```

- [x] **Step 4: Add audit evidence to Publication preflight and confirmation**

Thread `Settings.youtube_audit_approved` from the Publication route into
`preflight_publication_draft(..., youtube_audit_approved: bool = False)`. For YouTube only,
normalize `provider_options["privacy"]` and store:

```python
{
    "requestedPrivacy": requested.value,
    "effectivePrivacy": effective.value,
    "restriction": None if audit_approved else "youtube_compliance_audit_required",
}
```

Confirmation requires this exact current policy evidence. When audit status changes, dispatch
returns to awaiting approval with a `youtubePolicy` diff rather than changing privacy silently.

- [x] **Step 5: Run contract and Publication integration tests**

Run: `uv run pytest -q tests/contract/test_youtube_publisher.py tests/integration/test_publications.py -k 'youtube or audit or schedule'`

Expected: all selected tests pass.

---

### Task 3: Implement encrypted resumable-session checkpoints

**Files:**

- Create: `backend/src/clipah/publishing/providers/youtube/resumable.py`
- Modify: `backend/src/clipah/publishing/providers/youtube/adapter.py`
- Modify: `backend/tests/contract/test_youtube_publisher.py`

**Interfaces:**

- Produces: `YouTubeCheckpointVault`, `YouTubeUploadContext`, `UploadCheckpoint`,
  `UploadProgress`, `YouTubePublisher.begin`, `query_upload`, and `transfer`.
- Consumes: injected `httpx.Client`, immutable media size/checksum/range reader, access-token
  lease, and a vault that returns an opaque durable reference.

- [x] **Step 1: Write initiation, chunk, interruption, and ambiguity tests**

```python
def test_interrupted_upload_queries_status_before_resuming() -> None:
    responses = iter(
        (
            httpx.Response(308, headers={"Range": "bytes=0-524287"}),
            httpx.Response(308, headers={"Range": "bytes=0-1048575"}),
        )
    )
    publisher = _publisher(lambda request: next(responses))
    reconciled = publisher.query_upload(checkpoint=_ambiguous_checkpoint(), access_token=TOKEN)
    resumed = publisher.transfer(
        checkpoint=reconciled,
        read_range=lambda start, end: MEDIA[start:end],
        access_token=TOKEN,
    )
    assert reconciled.acknowledged_bytes == 524_288
    assert resumed.acknowledged_bytes == 1_048_576
```

Also test exact initiation headers, vault context binding, 256 KiB alignment, final short chunk,
malformed/missing `Range`, server ranges beyond total size, timeout marking ambiguity, completed
status responses, expired pre-final session restart eligibility, and expired post-final ambiguity
blocking replay.

- [x] **Step 2: Run resumable tests and confirm failures**

Run: `uv run pytest -q tests/contract/test_youtube_publisher.py -k 'resumable or chunk or ambiguous or checkpoint'`

Expected: selected tests fail because resumable types and operations are absent.

- [x] **Step 3: Implement safe checkpoint values and vault protocol**

```python
@dataclass(frozen=True, slots=True)
class UploadCheckpoint:
    secret_reference: str
    total_bytes: int
    acknowledged_bytes: int
    source_sha256: bytes
    generation: int
    final_request_ambiguous: bool

    def safe_dict(self) -> dict[str, object]:
        return {
            "totalBytes": self.total_bytes,
            "acknowledgedBytes": self.acknowledged_bytes,
            "sourceSha256": self.source_sha256.hex(),
            "generation": self.generation,
            "finalRequestAmbiguous": self.final_request_ambiguous,
        }
```

`YouTubeCheckpointVault.store(context, session_uri) -> str` and
`lease(reference, context) -> ContextManager[SecretStr]` are injected boundaries. The session URI
never appears in dataclass repr, exceptions, safe dictionaries, request metadata, or logs.

- [x] **Step 4: Implement begin, status query, and one-chunk transfer**

Initiate with `POST /upload/youtube/v3/videos?uploadType=resumable&part=snippet,status`, then store
the `Location` URI before returning. Query with empty `PUT` and `Content-Range: bytes */TOTAL`.
Transfer one contiguous chunk per method call with `Content-Range: bytes START-END/TOTAL` and return
the next immutable checkpoint. Convert a timeout or retryable 5xx into an ambiguous checkpoint
instead of guessing the acknowledged byte count.

- [x] **Step 5: Run resumable contract tests**

Run: `uv run pytest -q tests/contract/test_youtube_publisher.py -k 'resumable or chunk or ambiguous or checkpoint'`

Expected: all selected tests pass.

---

### Task 4: Normalize YouTube processing and provider failures

**Files:**

- Create: `backend/src/clipah/publishing/providers/youtube/status.py`
- Modify: `backend/src/clipah/publishing/providers/youtube/adapter.py`
- Modify: `backend/tests/contract/test_youtube_publisher.py`

**Interfaces:**

- Produces: `YouTubeStatus`, `YouTubeOutcome`, `parse_youtube_status`, and sanitized typed errors.
- Consumes: `videos.list(part=status,processingDetails)`, bounded HTTP status/reason fields, and
  optional integer `Retry-After`.

- [x] **Step 1: Write polling and normalized-error tests**

Cover `processing`, `succeeded`, `failed`, and `rejected`; known upload/processing reasons; missing
known video; malformed payload; HTTP 401; 429; `quotaExceeded`; 500/502/503/504; and permanent
metadata/policy failures. Assert exception strings contain no token, session URI, or response body.

- [x] **Step 2: Prove processing, missing-result, and error mappings fail before implementation**

Run: `uv run pytest -q tests/contract/test_youtube_publisher.py -k 'processing or quota or revoked'`

Expected: selected tests fail because status normalization is absent.

- [x] **Step 3: Implement strict response parsing and stable normalized errors**

```python
class YouTubeOutcome(StrEnum):
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class YouTubeStatus:
    video_id: str
    outcome: YouTubeOutcome
    privacy: YouTubePrivacy
    scheduled_for: datetime | None
    failure_code: str | None
```

Parse only the requested `status` and `processingDetails` fields. Normalize 401/invalid credentials
to `YouTubeReconnectRequiredError`; 429 and quota reasons to `YouTubeRateLimitedError` or
`YouTubeQuotaExhaustedError`; timeouts and 500/502/503/504 to `YouTubeUnavailableError`; and fixed
metadata/policy/media reasons to `YouTubePermanentError`. Retain a valid integer `Retry-After`
without exposing other headers or provider bodies.

- [x] **Step 4: Implement `videos.list` polling**

```python
response = client.get(
    f"{api_origin}/youtube/v3/videos",
    params={"part": "status,processingDetails", "id": provider_id},
    headers=_authorization(access_token),
)
```

Require exactly one matching video ID. An empty list for a known ID raises
`YouTubeAmbiguousCompletionError`; it never returns a result that permits re-upload.

- [x] **Step 5: Run status/error tests**

Run: `uv run pytest -q tests/contract/test_youtube_publisher.py -k 'processing or quota or revoked'`

Expected: all selected tests pass.

---

### Task 5: Keep thumbnail and timed-caption work independently retryable

**Files:**

- Modify: `backend/src/clipah/publishing/providers/youtube/adapter.py`
- Modify: `backend/src/clipah/publishing/providers/youtube/status.py`
- Modify: `backend/tests/contract/test_youtube_publisher.py`

**Interfaces:**

- Produces: `YouTubePublisher.set_thumbnail`, `YouTubePublisher.insert_caption`,
  `AttachmentState`, and `AttachmentResult`.
- Consumes: an already-created video ID, explicit attachment descriptors, bytes readers, granted
  scopes, and provider capability evidence.

- [x] **Step 1: Write independent attachment contract tests**

```python
def test_caption_retry_never_calls_video_insert_again() -> None:
    calls = _attachment_transport(caption_responses=(httpx.Response(503), httpx.Response(200)))
    publisher = _publisher(calls.transport)
    with pytest.raises(YouTubeUnavailableError):
        publisher.insert_caption(
            video_id="video-1", descriptor=_caption(), body=b"WEBVTT\n", access_token=TOKEN
        )
    result = publisher.insert_caption(
        video_id="video-1", descriptor=_caption(), body=b"WEBVTT\n", access_token=TOKEN
    )
    assert result.state is AttachmentState.SUCCEEDED
    assert all("/videos" not in request.url.path for request in calls.requests)
```

Also test unsupported thumbnail capability, missing caption scope before HTTP, exact caption
language/name/isDraft metadata, no deprecated `sync` parameter, thumbnail MIME/size bounds, and
permanent attachment failure that preserves the video ID.

- [x] **Step 2: Run attachment tests and confirm failures**

Run: `uv run pytest -q tests/contract/test_youtube_publisher.py -k 'thumbnail or caption'`

Expected: selected tests fail because attachment methods do not exist.

- [x] **Step 3: Implement explicit attachment operations**

```python
class AttachmentState(StrEnum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILED = "retryable_failed"
    PERMANENT_FAILED = "permanent_failed"
    UNSUPPORTED = "unsupported"
```

Use `POST /upload/youtube/v3/captions?part=snippet` with timed caption bytes and
`POST /upload/youtube/v3/thumbnails/set?videoId=...` with validated JPEG/PNG bytes. Return only
safe resource IDs and normalized attachment state. Do not mutate or recreate the base video.

- [x] **Step 4: Run attachment and complete contract suites**

Run: `uv run pytest -q tests/contract/test_youtube_publisher.py`

Expected: all YouTube contract tests pass.

---

### Task 6: Persist safe checkpoints and authoritative video state

**Files:**

- Create: `backend/src/clipah/publishing/providers/youtube/publications.py`
- Create: `backend/tests/integration/test_youtube_publications.py`
- Modify: `backend/tests/integration/test_schema.py` only if a schema expectation changes

**Interfaces:**

- Produces: `YouTubePublicationCoordinator.save_upload_checkpoint`,
  `record_video_created`, `apply_status`, and `record_attachment`.
- Consumes: one locked YouTube Publication, Task 38 bound Social Rendition, safe adapter results,
  `PublicationAttempt`, and the existing transition matrix.

- [x] **Step 1: Write Postgres persistence tests**

```python
def test_checkpoint_persists_only_an_opaque_reference_and_safe_progress(engine: Engine) -> None:
    seed = _seed_youtube_publication(engine)
    with Session(engine) as session, session.begin():
        coordinator = YouTubePublicationCoordinator(session)
        coordinator.save_upload_checkpoint(
            workspace_id=seed.workspace_id,
            publication_id=seed.publication_id,
            secret_reference="vault://opaque/session-1",
            checkpoint=_checkpoint(acknowledged_bytes=524_288),
            now=NOW,
        )
    with Session(engine) as session:
        publication = session.get(Publication, seed.publication_id)
        assert publication.encrypted_checkpoint_reference == "vault://opaque/session-1"
        assert publication.checkpoint_metadata["youtubeUpload"]["acknowledgedBytes"] == 524_288
        assert "session" not in json.dumps(publication.checkpoint_metadata).lower()
```

Also prove the coordinator rejects a non-YouTube account, missing/mismatched rendition, wrong
state, or a checkpoint checksum that differs from the Social Rendition. Test transitions from
preflighting to transferring to processing, exact immutable video ID/permalink retention,
scheduled-versus-published outcomes, append-only safe attempt rows, reconnect/permanent/retryable
failure mapping, and attachment retry that leaves the video ID and base status unchanged.

- [x] **Step 2: Run integration tests and confirm collection failure**

Run: `uv run pytest -q tests/integration/test_youtube_publications.py`

Expected: collection fails because the persistence coordinator is absent.

- [x] **Step 3: Implement locked single-Publication persistence**

```python
publication = session.scalar(
    select(Publication)
    .where(Publication.workspace_id == workspace_id, Publication.id == publication_id)
    .with_for_update()
)
```

Join the Social Account and bound Social Rendition by composite Workspace keys. Store
`checkpoint.safe_dict()` under `checkpoint_metadata["youtubeUpload"]`, never the secret URI. On
video creation, set `provider_publication_id`, derive
`https://www.youtube.com/watch?v={quote(video_id, safe='')}`, and transition transferring to
processing. Apply polling results through the existing `transition` function. Store attachment
state under separate `youtubeThumbnail` and `youtubeCaption` keys without changing a successful
base-video result.

- [x] **Step 4: Append bounded attempt evidence per durable stage**

Create `PublicationAttempt` rows whose stages are stable and unique within the attempt, such as
`youtube_begin`, `youtube_chunk_<acknowledged_bytes>`, `youtube_status_<poll_sequence>`,
`youtube_thumbnail_<attachment_attempt>`, and `youtube_caption_<attachment_attempt>`. Store only
method/operation names, provider request ID, byte count, normalized result, and sanitized error
code/message.

- [x] **Step 5: Run focused integration and schema contracts**

Run: `uv run pytest -q tests/integration/test_youtube_publications.py tests/integration/test_schema.py`

Expected: all selected tests pass without a new migration.

---

### Task 7: Add the opt-in sandbox smoke test and verify the complete task

**Files:**

- Create: `backend/tests/slow/test_youtube_publisher_smoke.py`
- Modify: `PROGRESS.md`

**Interfaces:**

- Produces: an explicitly gated private-upload smoke test and Task 39 completion evidence.
- Consumes: `CLIPAH_YOUTUBE_PUBLISH_SMOKE`, sandbox access token, expected channel ID, and a
  caller-supplied private media fixture path.

- [x] **Step 1: Add a fail-closed smoke-test gate**

```python
SMOKE_ENABLED = os.getenv("CLIPAH_YOUTUBE_PUBLISH_SMOKE") == "1"


@pytest.mark.slow
@pytest.mark.skipif(not SMOKE_ENABLED, reason="YouTube publish smoke is explicitly opt-in")
def test_private_youtube_upload_in_sandbox() -> None:
    token = os.environ["CLIPAH_YOUTUBE_SANDBOX_ACCESS_TOKEN"]
    channel_id = os.environ["CLIPAH_YOUTUBE_SANDBOX_CHANNEL_ID"]
    media_path = Path(os.environ["CLIPAH_YOUTUBE_SANDBOX_MEDIA_PATH"])
    publisher = _sandbox_publisher()
    publisher.confirm_destination(access_token=SecretStr(token), expected_account_id=channel_id)
    outcome = _upload_private_fixture(
        publisher, media_path=media_path, access_token=SecretStr(token)
    )
    assert outcome.requested_privacy is YouTubePrivacy.PRIVATE
    assert outcome.video_id
```

Define `_sandbox_publisher` in the same test as a concrete `YouTubePublisher` using a bounded
`httpx.Client` and a test-local vault whose plaintext is held only as `SecretStr` for that process.
Define `_upload_private_fixture` as the explicit begin/query/transfer/poll loop; it persists each
safe checkpoint through the same coordinator callbacks used by integration tests.

The test must refuse non-private effective privacy and must not print credentials, session URIs,
or provider bodies. It remains skipped during ordinary CI and local runs.

- [x] **Step 2: Run all Task 39 tests**

Run:
`uv run pytest -q tests/contract/test_youtube_publisher.py tests/integration/test_youtube_publications.py tests/slow/test_youtube_publisher_smoke.py`

Expected: contract/integration tests pass and the sandbox smoke test skips unless explicitly
configured.

- [x] **Step 3: Prove the existing schema remains sufficient**

No migration is planned because Task 37 already created every required Publication field. Run:

```bash
uv run alembic upgrade head
uv run alembic check
```

Expected: the database is already at `0021` and drift detection reports no new upgrade operations.

- [x] **Step 4: Run all four backend gates**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q --cov=clipah --cov-fail-under=90
```

Expected: every command exits zero and total branch coverage remains at least 90%.

- [x] **Step 5: Verify the working tree and update progress**

Run `git diff --check` and inspect `git status --short`. Record exact test totals, coverage,
smoke-test skip behavior, and any migration result in `PROGRESS.md`. Mark Task 39 complete awaiting
the owner's commit and Task 40 next.

- [x] **Step 6: Preserve the uncommitted branch for the owner**

Do not run `git add`, `git commit`, `git push`, or `git rebase`. Report the required owner commit
message exactly:

```text
feat: publish shorts through youtube api
```
