"""Contracts for official YouTube publication without unofficial Shorts behavior."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from clipah.publishing.providers.youtube.adapter import (
    YouTubeAttachment,
    YouTubeAttachmentKind,
    YouTubeAuditRestrictionError,
    YouTubeChannelMismatchError,
    YouTubePolicy,
    YouTubePrivacy,
    YouTubePublisher,
    YouTubePublishRequest,
    shorts_eligibility,
)
from clipah.publishing.providers.youtube.oauth import (
    YOUTUBE_CAPTION_SCOPE,
    YOUTUBE_UPLOAD_SCOPE,
    YouTubeScopeMissingError,
    require_youtube_publish_scopes,
)
from clipah.publishing.providers.youtube.resumable import (
    UploadCheckpoint,
    UploadProgress,
    YouTubeAmbiguousCompletionError,
    YouTubeUploadContext,
)
from clipah.publishing.providers.youtube.status import (
    AttachmentState,
    YouTubeAmbiguousStatusError,
    YouTubeOutcome,
    YouTubeQuotaExhaustedError,
    YouTubeRateLimitedError,
    YouTubeReconnectRequiredError,
    YouTubeUnavailableError,
)

NOW = datetime(2026, 9, 10, 5, 0, tzinfo=UTC)
TOKEN = SecretStr("youtube-access-token")
MEDIA = b"m" * 600_000
UPLOAD_CONTEXT = YouTubeUploadContext(
    workspace_id=UUID("00000000-0000-0000-0000-000000000001"),
    publication_id=UUID("00000000-0000-0000-0000-000000000002"),
    attempt=1,
)


class _Vault:
    """Hold fake session secrets only long enough to exercise the adapter boundary."""

    def __init__(self) -> None:
        """Start without stored provider material."""
        self.context: YouTubeUploadContext | None = None
        self._secret: SecretStr | None = None

    def store(self, context: YouTubeUploadContext, session_uri: SecretStr) -> str:
        """Bind one secret URI to the exact publication attempt."""
        self.context = context
        self._secret = session_uri
        return "vault://opaque/youtube-session"

    @contextmanager
    def lease(self, reference: str, context: YouTubeUploadContext) -> Iterator[SecretStr]:
        """Lease a stored secret only for its original attempt."""
        assert reference == "vault://opaque/youtube-session"
        assert context == self.context
        assert self._secret is not None
        yield self._secret


class _Media:
    """Immutable in-memory provider rendition for contract tests."""

    content_type = "video/mp4"
    size_bytes = len(MEDIA)
    sha256 = bytes.fromhex("ab" * 32)

    def read_range(self, start: int, end: int) -> bytes:
        """Return the requested half-open range."""
        return MEDIA[start:end]


def _request(**changes: object) -> YouTubePublishRequest:
    """Build one valid request whose overrides isolate each contract boundary."""
    values: dict[str, object] = {
        "title": "A useful short",
        "description": "A concise description",
        "tags": ("education", "creator"),
        "category_id": "27",
        "made_for_kids": False,
        "contains_synthetic_media": False,
        "requested_privacy": YouTubePrivacy.PRIVATE,
    }
    values.update(changes)
    return YouTubePublishRequest.model_validate(values)


def test_upload_scope_is_required_but_caption_scope_is_separate() -> None:
    """A base upload must not demand the broader caption-management permission."""
    require_youtube_publish_scopes(frozenset({YOUTUBE_UPLOAD_SCOPE}), captions=False)
    with pytest.raises(YouTubeScopeMissingError):
        require_youtube_publish_scopes(frozenset({YOUTUBE_UPLOAD_SCOPE}), captions=True)
    require_youtube_publish_scopes(
        frozenset({YOUTUBE_UPLOAD_SCOPE, YOUTUBE_CAPTION_SCOPE}), captions=True
    )


def test_upload_scope_cannot_be_omitted() -> None:
    """A partial OAuth grant must fail before Clipah attempts provider work."""
    with pytest.raises(YouTubeScopeMissingError):
        require_youtube_publish_scopes(frozenset(), captions=False)


def test_shorts_is_only_an_eligibility_result() -> None:
    """Clipah may report eligibility but cannot guarantee YouTube classification."""
    result = shorts_eligibility(width=1080, height=1920, duration_ms=180_000)

    assert result.eligible is True
    assert result.classification_guaranteed is False


@pytest.mark.parametrize(
    ("width", "height", "duration_ms"),
    ((1920, 1080, 60_000), (1080, 1920, 180_001), (0, 1920, 60_000)),
)
def test_shorts_eligibility_rejects_nonvertical_long_or_invalid_media(
    width: int, height: int, duration_ms: int
) -> None:
    """Eligibility must reflect the current property boundary without an upload endpoint claim."""
    assert shorts_eligibility(width=width, height=height, duration_ms=duration_ms).eligible is False


def test_publish_request_accepts_strict_complete_values() -> None:
    """Validated metadata should retain individual tags and an aware schedule exactly."""
    scheduled_for = datetime(2026, 9, 11, 3, 0, tzinfo=UTC)

    request = _request(scheduled_for=scheduled_for)

    assert request.tags == ("education", "creator")
    assert request.scheduled_for == scheduled_for


@pytest.mark.parametrize(
    ("changes", "field"),
    (
        ({"title": ""}, "title"),
        ({"title": "x" * 101}, "title"),
        ({"description": "é" * 2_501}, "description"),
        ({"category_id": "film"}, "category_id"),
        ({"tags": ()}, "tags"),
        ({"tags": ("valid", "")}, "tags"),
        ({"scheduled_for": datetime(2026, 9, 11, 3, 0)}, "scheduled_for"),
    ),
)
def test_publish_request_rejects_values_youtube_cannot_reproduce(
    changes: dict[str, object], field: str
) -> None:
    """Malformed frozen choices must fail locally rather than drift at dispatch."""
    with pytest.raises(ValidationError) as caught:
        _request(**changes)

    assert field in str(caught.value)


def test_attachment_descriptors_are_explicit_and_bounded() -> None:
    """Optional media work must carry enough frozen facts for an independent retry."""
    caption = YouTubeAttachment(
        kind=YouTubeAttachmentKind.CAPTION,
        content_type="text/vtt",
        size_bytes=12,
        language="en",
        name="English",
    )
    thumbnail = YouTubeAttachment(
        kind=YouTubeAttachmentKind.THUMBNAIL,
        content_type="image/jpeg",
        size_bytes=1_000,
    )

    assert caption.language == "en"
    assert thumbnail.language is None


@pytest.mark.parametrize(
    "values",
    (
        {"kind": "caption", "content_type": "text/vtt", "size_bytes": 1},
        {
            "kind": "thumbnail",
            "content_type": "image/gif",
            "size_bytes": 1_000,
        },
        {
            "kind": "thumbnail",
            "content_type": "image/jpeg",
            "size_bytes": 2_097_153,
        },
    ),
)
def test_attachment_descriptors_reject_incomplete_or_unsupported_values(
    values: dict[str, object],
) -> None:
    """Bad attachment evidence must not survive until an external request."""
    with pytest.raises(ValidationError):
        YouTubeAttachment.model_validate(values)


def _publisher(
    handler: httpx.MockTransport | object,
    *,
    audit_approved: bool = False,
    vault: _Vault | None = None,
) -> YouTubePublisher:
    """Build an adapter against a deterministic fake Google boundary."""
    transport = (
        handler if isinstance(handler, httpx.MockTransport) else httpx.MockTransport(handler)
    )
    return YouTubePublisher(
        client=httpx.Client(transport=transport),
        policy=YouTubePolicy(audit_approved=audit_approved, now=NOW),
        api_origin="https://youtube.test",
        checkpoint_vault=vault,
        upload_context=UPLOAD_CONTEXT if vault is not None else None,
    )


def test_channel_check_uses_mine_and_requires_the_exact_connected_channel() -> None:
    """A valid token for a different channel must not redirect the Publication."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"items": [{"id": "channel-other"}]})

    publisher = _publisher(respond)

    with pytest.raises(YouTubeChannelMismatchError):
        publisher.confirm_destination(access_token=TOKEN, expected_account_id="channel-expected")

    assert requests[0].url == httpx.URL(
        "https://youtube.test/youtube/v3/channels?part=id%2Csnippet&mine=true"
    )
    assert requests[0].headers["Authorization"] == "Bearer youtube-access-token"


def test_channel_check_accepts_only_one_matching_channel() -> None:
    """The destination proof must reject absent or ambiguous channel results."""
    publisher = _publisher(
        lambda _request: httpx.Response(200, json={"items": [{"id": "channel-1"}]})
    )

    publisher.confirm_destination(access_token=TOKEN, expected_account_id="channel-1")


def test_upload_metadata_maps_every_frozen_youtube_choice() -> None:
    """Provider JSON must be derived only from validated frozen Publication choices."""
    publisher = _publisher(lambda _request: httpx.Response(500), audit_approved=True)
    request = _request(
        requested_privacy=YouTubePrivacy.UNLISTED,
        made_for_kids=True,
        contains_synthetic_media=True,
    )

    assert publisher.upload_metadata(request) == {
        "snippet": {
            "title": "A useful short",
            "description": "A concise description",
            "tags": ["education", "creator"],
            "categoryId": "27",
        },
        "status": {
            "privacyStatus": "unlisted",
            "selfDeclaredMadeForKids": True,
            "containsSyntheticMedia": True,
        },
    }


def test_unaudited_upload_is_explicitly_forced_private() -> None:
    """Google's audit restriction must override a broader requested visibility."""
    policy = YouTubePolicy(audit_approved=False, now=NOW)

    assert policy.effective_privacy(_request(requested_privacy=YouTubePrivacy.PUBLIC)) is (
        YouTubePrivacy.PRIVATE
    )
    assert policy.confirmation_evidence(_request(requested_privacy=YouTubePrivacy.PUBLIC)) == {
        "requestedPrivacy": "public",
        "effectivePrivacy": "private",
        "restriction": "youtube_compliance_audit_required",
    }


def test_audited_future_schedule_uses_private_publish_at() -> None:
    """A reviewed public schedule must use YouTube's native private-to-public transition."""
    scheduled_for = datetime(2026, 9, 11, 5, 0, tzinfo=UTC)
    publisher = _publisher(lambda _request: httpx.Response(500), audit_approved=True)

    metadata = publisher.upload_metadata(
        _request(requested_privacy=YouTubePrivacy.PUBLIC, scheduled_for=scheduled_for)
    )

    assert metadata["status"] == {
        "privacyStatus": "private",
        "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": False,
        "publishAt": "2026-09-11T05:00:00+00:00",
    }


@pytest.mark.parametrize(
    "publish_request",
    (
        _request(
            requested_privacy=YouTubePrivacy.PUBLIC,
            scheduled_for=datetime(2026, 9, 11, 5, 0, tzinfo=UTC),
        ),
        _request(
            requested_privacy=YouTubePrivacy.UNLISTED,
            scheduled_for=datetime(2026, 9, 11, 5, 0, tzinfo=UTC),
        ),
        _request(
            requested_privacy=YouTubePrivacy.PUBLIC,
            scheduled_for=datetime(2026, 9, 10, 4, 59, tzinfo=UTC),
        ),
    ),
)
def test_invalid_or_unaudited_schedule_fails_before_provider_http(
    publish_request: YouTubePublishRequest,
) -> None:
    """Clipah must not send a schedule whose eventual visibility is not permitted."""
    audit_approved = (
        publish_request.scheduled_for is not None and publish_request.scheduled_for <= NOW
    )
    publisher = _publisher(
        lambda _request: pytest.fail("provider request was attempted"),
        audit_approved=audit_approved,
    )

    with pytest.raises(YouTubeAuditRestrictionError):
        publisher.upload_metadata(publish_request)


def test_resumable_begin_stores_the_secret_location_before_returning_checkpoint() -> None:
    """A resumable URI must leave the HTTP response only through the encrypted vault."""
    requests: list[httpx.Request] = []
    vault = _Vault()

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"Location": "https://upload.test/session-secret"})

    checkpoint = _publisher(respond, vault=vault).begin(
        request=_request(), media=_Media(), access_token=TOKEN
    )

    assert requests[0].url == httpx.URL(
        "https://youtube.test/upload/youtube/v3/videos?uploadType=resumable&part=snippet%2Cstatus"
    )
    assert requests[0].headers["X-Upload-Content-Length"] == str(len(MEDIA))
    assert requests[0].headers["X-Upload-Content-Type"] == "video/mp4"
    assert checkpoint.secret_reference == "vault://opaque/youtube-session"
    assert checkpoint.safe_dict() == {
        "totalBytes": len(MEDIA),
        "acknowledgedBytes": 0,
        "sourceSha256": "ab" * 32,
        "generation": 1,
        "finalRequestAmbiguous": False,
    }
    assert "session-secret" not in repr(checkpoint)


def test_chunk_transfer_sends_one_aligned_contiguous_range() -> None:
    """A retry checkpoint must advance only through YouTube's acknowledged byte range."""
    requests: list[httpx.Request] = []
    vault = _Vault()
    vault.store(UPLOAD_CONTEXT, SecretStr("https://upload.test/session-secret"))

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(308, headers={"Range": "bytes=0-262143"})

    checkpoint = UploadCheckpoint(
        secret_reference="vault://opaque/youtube-session",
        total_bytes=len(MEDIA),
        acknowledged_bytes=0,
        source_sha256=bytes.fromhex("ab" * 32),
        generation=1,
        final_request_ambiguous=False,
    )

    advanced = _publisher(respond, vault=vault).transfer(
        checkpoint=checkpoint,
        read_range=_Media().read_range,
        access_token=TOKEN,
    )

    assert requests[0].headers["Content-Range"] == f"bytes 0-262143/{len(MEDIA)}"
    assert requests[0].content == MEDIA[:262_144]
    assert advanced.acknowledged_bytes == 262_144
    assert advanced.progress is UploadProgress.ACTIVE


def test_interrupted_upload_queries_status_before_resuming() -> None:
    """Ambiguous bytes must be reconciled before another contiguous chunk is eligible."""
    responses = iter(
        (
            httpx.Response(308, headers={"Range": "bytes=0-262143"}),
            httpx.Response(308, headers={"Range": "bytes=0-524287"}),
        )
    )
    vault = _Vault()
    vault.store(UPLOAD_CONTEXT, SecretStr("https://upload.test/session-secret"))
    publisher = _publisher(lambda _request: next(responses), vault=vault)
    ambiguous = UploadCheckpoint(
        secret_reference="vault://opaque/youtube-session",
        total_bytes=len(MEDIA),
        acknowledged_bytes=0,
        source_sha256=bytes.fromhex("ab" * 32),
        generation=1,
        final_request_ambiguous=True,
    )

    reconciled = publisher.query_upload(checkpoint=ambiguous, access_token=TOKEN)
    resumed = publisher.transfer(
        checkpoint=reconciled,
        read_range=_Media().read_range,
        access_token=TOKEN,
    )

    assert reconciled.acknowledged_bytes == 262_144
    assert reconciled.final_request_ambiguous is False
    assert resumed.acknowledged_bytes == 524_288


def test_timeout_marks_the_checkpoint_ambiguous_without_advancing_bytes() -> None:
    """A missing response cannot be interpreted as either acceptance or rejection."""
    vault = _Vault()
    vault.store(UPLOAD_CONTEXT, SecretStr("https://upload.test/session-secret"))
    checkpoint = UploadCheckpoint(
        secret_reference="vault://opaque/youtube-session",
        total_bytes=len(MEDIA),
        acknowledged_bytes=0,
        source_sha256=bytes.fromhex("ab" * 32),
        generation=1,
        final_request_ambiguous=False,
    )

    ambiguous = _publisher(
        lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("timeout", request=request)),
        vault=vault,
    ).transfer(checkpoint=checkpoint, read_range=_Media().read_range, access_token=TOKEN)

    assert ambiguous.acknowledged_bytes == 0
    assert ambiguous.final_request_ambiguous is True


def test_expired_session_after_ambiguous_final_request_blocks_replay() -> None:
    """A lost final response plus an expired session cannot safely authorize new bytes."""
    vault = _Vault()
    vault.store(UPLOAD_CONTEXT, SecretStr("https://upload.test/session-secret"))
    checkpoint = UploadCheckpoint(
        secret_reference="vault://opaque/youtube-session",
        total_bytes=len(MEDIA),
        acknowledged_bytes=len(MEDIA) - 10,
        source_sha256=bytes.fromhex("ab" * 32),
        generation=1,
        final_request_ambiguous=True,
    )
    publisher = _publisher(lambda _request: httpx.Response(404), vault=vault)

    with pytest.raises(YouTubeAmbiguousCompletionError):
        publisher.query_upload(checkpoint=checkpoint, access_token=TOKEN)


@pytest.mark.parametrize(
    ("processing_status", "upload_status", "expected"),
    (
        ("processing", "uploaded", YouTubeOutcome.PROCESSING),
        ("succeeded", "processed", YouTubeOutcome.SUCCEEDED),
        ("failed", "failed", YouTubeOutcome.FAILED),
        ("terminated", "rejected", YouTubeOutcome.REJECTED),
    ),
)
def test_poll_normalizes_authoritative_processing_states(
    processing_status: str, upload_status: str, expected: YouTubeOutcome
) -> None:
    """The durable Publication state must follow YouTube's processing result truthfully."""
    publisher = _publisher(
        lambda _request: httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "video-1",
                        "status": {
                            "privacyStatus": "private",
                            "uploadStatus": upload_status,
                        },
                        "processingDetails": {
                            "processingStatus": processing_status,
                            "processingFailureReason": (
                                "codec" if expected is YouTubeOutcome.FAILED else None
                            ),
                        },
                    }
                ]
            },
        )
    )

    status = publisher.poll(provider_id="video-1", access_token=TOKEN)

    assert status.video_id == "video-1"
    assert status.outcome is expected
    assert status.failure_code == ("codec" if expected is YouTubeOutcome.FAILED else None)


def test_poll_missing_known_video_is_ambiguous_and_never_authorizes_reupload() -> None:
    """A missing known ID cannot prove that the original upload never completed."""
    publisher = _publisher(lambda _request: httpx.Response(200, json={"items": []}))

    with pytest.raises(YouTubeAmbiguousStatusError):
        publisher.poll(provider_id="video-1", access_token=TOKEN)


@pytest.mark.parametrize(
    ("response", "error_type"),
    (
        (
            httpx.Response(401, json={"error": {"message": "secret body"}}),
            YouTubeReconnectRequiredError,
        ),
        (
            httpx.Response(
                403,
                json={"error": {"errors": [{"reason": "quotaExceeded"}], "message": "secret body"}},
            ),
            YouTubeQuotaExhaustedError,
        ),
        (httpx.Response(429, headers={"Retry-After": "17"}), YouTubeRateLimitedError),
        (httpx.Response(503, text="secret provider body"), YouTubeUnavailableError),
    ),
)
def test_poll_normalizes_provider_failures_without_exposing_bodies(
    response: httpx.Response, error_type: type[Exception]
) -> None:
    """Provider diagnostics must become fixed safe errors before reaching durable state."""
    publisher = _publisher(lambda _request: response)

    with pytest.raises(error_type) as caught:
        publisher.poll(provider_id="video-1", access_token=TOKEN)

    assert "secret" not in str(caught.value)
    assert "youtube-access-token" not in str(caught.value)


def test_rate_limit_retains_only_a_valid_integer_retry_after() -> None:
    """Safe scheduling may retain bounded delay evidence without copying provider headers."""
    publisher = _publisher(lambda _request: httpx.Response(429, headers={"Retry-After": "17"}))

    with pytest.raises(YouTubeRateLimitedError) as caught:
        publisher.poll(provider_id="video-1", access_token=TOKEN)

    assert caught.value.retry_after == 17


def test_channel_check_normalizes_expired_or_revoked_grant() -> None:
    """Destination proof must surface reconnection rather than a false channel mismatch."""
    publisher = _publisher(lambda _request: httpx.Response(401, text="provider secret"))

    with pytest.raises(YouTubeReconnectRequiredError):
        publisher.confirm_destination(access_token=TOKEN, expected_account_id="channel-1")


def test_resumable_begin_normalizes_quota_failure_without_storing_a_session() -> None:
    """Quota exhaustion before a session exists must not create fake checkpoint progress."""
    vault = _Vault()
    publisher = _publisher(
        lambda _request: httpx.Response(
            403,
            json={"error": {"errors": [{"reason": "quotaExceeded"}]}},
        ),
        vault=vault,
    )

    with pytest.raises(YouTubeQuotaExhaustedError):
        publisher.begin(request=_request(), media=_Media(), access_token=TOKEN)

    assert vault.context is None


def test_caption_upload_is_explicit_and_uses_no_deprecated_sync_parameter() -> None:
    """Timed captions must preserve language/name without automatic synchronization."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "caption-1"})

    descriptor = YouTubeAttachment(
        kind=YouTubeAttachmentKind.CAPTION,
        content_type="text/vtt",
        size_bytes=8,
        language="en",
        name="English",
    )

    result = _publisher(respond).insert_caption(
        video_id="video-1",
        descriptor=descriptor,
        body=b"WEBVTT\n\n",
        granted_scopes=frozenset({YOUTUBE_UPLOAD_SCOPE, YOUTUBE_CAPTION_SCOPE}),
        access_token=TOKEN,
    )

    assert requests[0].url == httpx.URL(
        "https://youtube.test/upload/youtube/v3/captions?part=snippet"
    )
    assert b'"videoId":"video-1"' in requests[0].content
    assert b'"language":"en"' in requests[0].content
    assert b'"name":"English"' in requests[0].content
    assert b"sync" not in requests[0].content
    assert result.state is AttachmentState.SUCCEEDED
    assert result.resource_id == "caption-1"


def test_missing_caption_scope_fails_before_http() -> None:
    """Optional caption work must require its separately granted broader scope."""
    descriptor = YouTubeAttachment(
        kind=YouTubeAttachmentKind.CAPTION,
        content_type="text/vtt",
        size_bytes=8,
        language="en",
        name="English",
    )
    publisher = _publisher(lambda _request: pytest.fail("provider request was attempted"))

    with pytest.raises(YouTubeScopeMissingError):
        publisher.insert_caption(
            video_id="video-1",
            descriptor=descriptor,
            body=b"WEBVTT\n\n",
            granted_scopes=frozenset({YOUTUBE_UPLOAD_SCOPE}),
            access_token=TOKEN,
        )


def test_unsupported_thumbnail_returns_explicit_state_without_http() -> None:
    """A missing account capability must not be misreported as provider failure."""
    descriptor = YouTubeAttachment(
        kind=YouTubeAttachmentKind.THUMBNAIL,
        content_type="image/jpeg",
        size_bytes=4,
    )
    publisher = _publisher(lambda _request: pytest.fail("provider request was attempted"))

    result = publisher.set_thumbnail(
        video_id="video-1",
        descriptor=descriptor,
        body=b"jpeg",
        supported=False,
        access_token=TOKEN,
    )

    assert result.state is AttachmentState.UNSUPPORTED
    assert result.video_id == "video-1"


def test_caption_retry_never_calls_video_insert_again() -> None:
    """Retrying an attachment must preserve the already-created base video."""
    requests: list[httpx.Request] = []
    responses = iter((httpx.Response(503), httpx.Response(200, json={"id": "caption-1"})))

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return next(responses)

    descriptor = YouTubeAttachment(
        kind=YouTubeAttachmentKind.CAPTION,
        content_type="text/vtt",
        size_bytes=8,
        language="en",
        name="English",
    )
    publisher = _publisher(respond)
    kwargs = {
        "video_id": "video-1",
        "descriptor": descriptor,
        "body": b"WEBVTT\n\n",
        "granted_scopes": frozenset({YOUTUBE_UPLOAD_SCOPE, YOUTUBE_CAPTION_SCOPE}),
        "access_token": TOKEN,
    }

    with pytest.raises(YouTubeUnavailableError):
        publisher.insert_caption(**kwargs)
    result = publisher.insert_caption(**kwargs)

    assert result.state is AttachmentState.SUCCEEDED
    assert all("/videos" not in request.url.path for request in requests)
