"""Strict YouTube publication request values and provider policy."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from enum import StrEnum
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from clipah.publishing.providers.base import PublicationMedia
from clipah.publishing.providers.youtube.oauth import require_youtube_publish_scopes
from clipah.publishing.providers.youtube.resumable import (
    UploadCheckpoint,
    UploadProgress,
    YouTubeAmbiguousCompletionError,
    YouTubeCheckpointVault,
    YouTubeResumableError,
    YouTubeUploadContext,
)
from clipah.social_accounts.models import SocialProvider

YOUTUBE_CHUNK_SIZE = 256 * 1024


class YouTubeAuditRestrictionError(Exception):
    """The requested visibility or schedule is unavailable under current audit policy."""


class YouTubeChannelMismatchError(Exception):
    """The live OAuth Grant does not identify exactly the selected YouTube channel."""


class YouTubePrivacy(StrEnum):
    """Visibility values accepted by the YouTube Data API."""

    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"


class YouTubeAttachmentKind(StrEnum):
    """Optional YouTube operations that follow base-video creation."""

    CAPTION = "caption"
    THUMBNAIL = "thumbnail"


class YouTubeAttachment(BaseModel):
    """Frozen descriptor for one independently retryable attachment."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: YouTubeAttachmentKind
    content_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    language: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=150)

    @model_validator(mode="after")
    def validate_kind_specific_values(self) -> YouTubeAttachment:
        """Require caption identity and enforce YouTube thumbnail upload bounds."""
        if self.kind is YouTubeAttachmentKind.CAPTION:
            if self.content_type != "text/vtt" or self.language is None or self.name is None:
                raise ValueError("caption attachments require WebVTT language and name")
        elif self.content_type not in {"image/jpeg", "image/png"} or self.size_bytes > 2_097_152:
            raise ValueError("thumbnail attachments require a JPEG or PNG no larger than 2 MiB")
        return self


class YouTubePublishRequest(BaseModel):
    """Frozen, validated metadata and consent for one YouTube upload."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    title: str = Field(min_length=1, max_length=100)
    description: str
    tags: tuple[str, ...] = Field(min_length=1)
    category_id: str = Field(pattern=r"^[0-9]+$")
    made_for_kids: bool
    contains_synthetic_media: bool
    requested_privacy: YouTubePrivacy
    scheduled_for: datetime | None = None
    caption: YouTubeAttachment | None = None
    thumbnail: YouTubeAttachment | None = None

    @field_validator("title")
    @classmethod
    def require_nonblank_title(cls, value: str) -> str:
        """Reject a title that contains no visible characters."""
        if not value.strip():
            raise ValueError("title must not be blank")
        return value

    @field_validator("description")
    @classmethod
    def bound_description_bytes(cls, value: str) -> str:
        """Apply YouTube's byte limit rather than a Unicode code-point limit."""
        if len(value.encode("utf-8")) > 5_000:
            raise ValueError("description exceeds 5000 UTF-8 bytes")
        return value

    @field_validator("tags")
    @classmethod
    def require_individual_nonblank_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Keep tags as distinct, nonblank provider values."""
        if any(not tag.strip() for tag in value):
            raise ValueError("tags must not contain blank values")
        return value

    @field_validator("scheduled_for")
    @classmethod
    def require_aware_schedule(cls, value: datetime | None) -> datetime | None:
        """Reject a schedule that cannot identify one reproducible UTC instant."""
        if value is not None and value.utcoffset() is None:
            raise ValueError("scheduled_for must include an offset")
        return value

    @model_validator(mode="after")
    def require_matching_attachment_kinds(self) -> YouTubePublishRequest:
        """Prevent a descriptor from being persisted under the wrong operation."""
        if self.caption is not None and self.caption.kind is not YouTubeAttachmentKind.CAPTION:
            raise ValueError("caption must carry a caption descriptor")
        if (
            self.thumbnail is not None
            and self.thumbnail.kind is not YouTubeAttachmentKind.THUMBNAIL
        ):
            raise ValueError("thumbnail must carry a thumbnail descriptor")
        return self


class YouTubePolicy(BaseModel):
    """Fail-closed deployment policy frozen into YouTube confirmation evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    audit_approved: bool = False
    now: datetime

    @field_validator("now")
    @classmethod
    def require_aware_now(cls, value: datetime) -> datetime:
        """Keep policy evaluation deterministic and tied to one UTC-capable instant."""
        if value.utcoffset() is None:
            raise ValueError("now must include an offset")
        return value

    def effective_privacy(self, request: YouTubePublishRequest) -> YouTubePrivacy:
        """Apply audit restrictions and YouTube's private scheduling requirement."""
        return self.effective_privacy_for(
            requested=request.requested_privacy,
            scheduled_for=request.scheduled_for,
        )

    def effective_privacy_for(
        self, *, requested: YouTubePrivacy, scheduled_for: datetime | None
    ) -> YouTubePrivacy:
        """Apply visibility policy to already-normalized durable choices."""
        if scheduled_for is not None:
            if (
                not self.audit_approved
                or requested is not YouTubePrivacy.PUBLIC
                or scheduled_for <= self.now
            ):
                raise YouTubeAuditRestrictionError("YouTube scheduling policy is unavailable")
            return YouTubePrivacy.PRIVATE
        if not self.audit_approved:
            return YouTubePrivacy.PRIVATE
        return requested

    def confirmation_evidence(self, request: YouTubePublishRequest) -> dict[str, str | None]:
        """Describe requested and effective visibility without silently changing either."""
        return {
            "requestedPrivacy": request.requested_privacy.value,
            "effectivePrivacy": self.effective_privacy(request).value,
            "restriction": None if self.audit_approved else "youtube_compliance_audit_required",
        }

    def confirmation_evidence_for(
        self, *, requested: YouTubePrivacy, scheduled_for: datetime | None
    ) -> dict[str, str | None]:
        """Freeze policy evidence from normalized durable Publication fields."""
        return {
            "requestedPrivacy": requested.value,
            "effectivePrivacy": self.effective_privacy_for(
                requested=requested, scheduled_for=scheduled_for
            ).value,
            "restriction": None if self.audit_approved else "youtube_compliance_audit_required",
        }


class ShortsEligibility(BaseModel):
    """Property-based eligibility that never promises provider classification."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    eligible: bool
    classification_guaranteed: bool = False


def shorts_eligibility(*, width: int, height: int, duration_ms: int) -> ShortsEligibility:
    """Report current square-or-vertical, at-most-three-minute eligibility."""
    eligible = (
        width > 0 and height > 0 and duration_ms > 0 and width <= height and duration_ms <= 180_000
    )
    return ShortsEligibility(eligible=eligible)


class YouTubePublisher:
    """Official HTTP adapter for one exact YouTube channel and frozen request."""

    provider = SocialProvider.YOUTUBE

    def __init__(
        self,
        *,
        client: httpx.Client,
        policy: YouTubePolicy,
        api_origin: str,
        checkpoint_vault: YouTubeCheckpointVault | None = None,
        upload_context: YouTubeUploadContext | None = None,
    ) -> None:
        """Bind deterministic policy and an injected HTTP transport."""
        self._client = client
        self._policy = policy
        self._api_origin = api_origin.rstrip("/")
        self._checkpoint_vault = checkpoint_vault
        self._upload_context = upload_context

    def confirm_destination(self, *, access_token: SecretStr, expected_account_id: str) -> None:
        """Require the live grant to identify exactly the frozen channel ID."""
        try:
            response = self._client.get(
                f"{self._api_origin}/youtube/v3/channels",
                params={"part": "id,snippet", "mine": "true"},
                headers=_authorization(access_token),
            )
        except httpx.TimeoutException as error:
            from clipah.publishing.providers.youtube.status import YouTubeUnavailableError

            raise YouTubeUnavailableError(
                "YouTube publishing is temporarily unavailable"
            ) from error
        _normalize_response(response)
        try:
            payload = response.json()
        except ValueError as error:
            raise YouTubeChannelMismatchError("YouTube channel identity is unavailable") from error
        items = payload.get("items") if isinstance(payload, dict) else None
        ids = (
            [item.get("id") for item in items if isinstance(item, dict)]
            if isinstance(items, list)
            else []
        )
        if ids != [expected_account_id]:
            raise YouTubeChannelMismatchError("YouTube channel identity does not match")

    def upload_metadata(self, request: YouTubePublishRequest) -> dict[str, Any]:
        """Serialize one validated request into the exact videos.insert metadata body."""
        status: dict[str, object] = {
            "privacyStatus": self._policy.effective_privacy(request).value,
            "selfDeclaredMadeForKids": request.made_for_kids,
            "containsSyntheticMedia": request.contains_synthetic_media,
        }
        if request.scheduled_for is not None:
            status["publishAt"] = request.scheduled_for.isoformat()
        return {
            "snippet": {
                "title": request.title,
                "description": request.description,
                "tags": list(request.tags),
                "categoryId": request.category_id,
            },
            "status": status,
        }

    def begin(
        self,
        *,
        request: YouTubePublishRequest,
        media: PublicationMedia,
        access_token: SecretStr,
    ) -> UploadCheckpoint:
        """Initiate one resumable session and vault its secret URI before returning."""
        vault, context = self._resumable_dependencies()
        response = self._client.post(
            f"{self._api_origin}/upload/youtube/v3/videos",
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                **_authorization(access_token),
                "X-Upload-Content-Length": str(media.size_bytes),
                "X-Upload-Content-Type": media.content_type,
            },
            json=self.upload_metadata(request),
        )
        _normalize_response(response)
        location = response.headers.get("Location")
        if not location:
            raise YouTubeResumableError("YouTube resumable initiation failed")
        reference = vault.store(context, SecretStr(location))
        return UploadCheckpoint(
            secret_reference=reference,
            total_bytes=media.size_bytes,
            acknowledged_bytes=0,
            source_sha256=media.sha256,
            generation=1,
            final_request_ambiguous=False,
        )

    def query_upload(
        self, *, checkpoint: UploadCheckpoint, access_token: SecretStr
    ) -> UploadCheckpoint:
        """Reconcile provider progress before retrying any ambiguous transfer."""
        vault, context = self._resumable_dependencies()
        with vault.lease(checkpoint.secret_reference, context) as session_uri:
            response = self._client.put(
                session_uri.get_secret_value(),
                headers={
                    **_authorization(access_token),
                    "Content-Length": "0",
                    "Content-Range": f"bytes */{checkpoint.total_bytes}",
                },
                content=b"",
            )
        if response.status_code == 308:
            acknowledged = _acknowledged_bytes(
                response.headers.get("Range"),
                total_bytes=checkpoint.total_bytes,
                allow_empty=True,
            )
            if acknowledged < checkpoint.acknowledged_bytes:
                raise YouTubeResumableError("YouTube checkpoint moved backwards")
            return replace(
                checkpoint,
                acknowledged_bytes=acknowledged,
                final_request_ambiguous=False,
                progress=UploadProgress.ACTIVE,
            )
        if response.status_code in {200, 201}:
            return _completed_checkpoint(checkpoint, response)
        if response.status_code == 404:
            if checkpoint.final_request_ambiguous:
                raise YouTubeAmbiguousCompletionError(
                    "YouTube completion cannot be safely disproved"
                )
            return replace(checkpoint, progress=UploadProgress.EXPIRED_RESTARTABLE)
        _normalize_response(response)
        raise YouTubeResumableError("YouTube resumable status is unavailable")

    def transfer(
        self,
        *,
        checkpoint: UploadCheckpoint,
        read_range: Callable[[int, int], bytes],
        access_token: SecretStr,
    ) -> UploadCheckpoint:
        """Send exactly one aligned contiguous chunk from a reconciled checkpoint."""
        if checkpoint.final_request_ambiguous:
            raise YouTubeResumableError("ambiguous upload must be reconciled before transfer")
        if checkpoint.progress is not UploadProgress.ACTIVE:
            raise YouTubeResumableError("upload session is not active")
        start = checkpoint.acknowledged_bytes
        end = min(start + YOUTUBE_CHUNK_SIZE, checkpoint.total_bytes)
        if start >= end:
            raise YouTubeResumableError("upload has no remaining bytes")
        body = read_range(start, end)
        if len(body) != end - start:
            raise YouTubeResumableError("media reader returned an incomplete range")
        vault, context = self._resumable_dependencies()
        try:
            with vault.lease(checkpoint.secret_reference, context) as session_uri:
                response = self._client.put(
                    session_uri.get_secret_value(),
                    headers={
                        **_authorization(access_token),
                        "Content-Length": str(len(body)),
                        "Content-Type": "video/mp4",
                        "Content-Range": (f"bytes {start}-{end - 1}/{checkpoint.total_bytes}"),
                    },
                    content=body,
                )
        except httpx.TimeoutException:
            return replace(checkpoint, final_request_ambiguous=True)
        if response.status_code == 308:
            acknowledged = _acknowledged_bytes(
                response.headers.get("Range"),
                total_bytes=checkpoint.total_bytes,
                allow_empty=False,
            )
            if acknowledged < end:
                raise YouTubeResumableError("YouTube did not acknowledge the complete chunk")
            return replace(checkpoint, acknowledged_bytes=acknowledged)
        if response.status_code in {200, 201}:
            return _completed_checkpoint(checkpoint, response)
        if response.status_code in {500, 502, 503, 504}:
            return replace(checkpoint, final_request_ambiguous=True)
        _normalize_response(response)
        raise YouTubeResumableError("YouTube rejected the upload chunk")

    def poll(self, *, provider_id: str, access_token: SecretStr) -> object:
        """Read and normalize the authoritative processing state for one known video."""
        from clipah.publishing.providers.youtube.status import (
            YouTubeUnavailableError,
            normalize_youtube_response,
            parse_youtube_status,
        )

        try:
            response = self._client.get(
                f"{self._api_origin}/youtube/v3/videos",
                params={"part": "status,processingDetails", "id": provider_id},
                headers=_authorization(access_token),
            )
        except httpx.TimeoutException as error:
            raise YouTubeUnavailableError(
                "YouTube publishing is temporarily unavailable"
            ) from error
        normalize_youtube_response(response)
        try:
            payload = response.json()
        except ValueError as error:
            from clipah.publishing.providers.youtube.status import YouTubePermanentError

            raise YouTubePermanentError("YouTube status response is malformed") from error
        return parse_youtube_status(provider_id=provider_id, payload=payload)

    def set_thumbnail(
        self,
        *,
        video_id: str,
        descriptor: YouTubeAttachment,
        body: bytes,
        supported: bool,
        access_token: SecretStr,
    ) -> object:
        """Set one explicit custom thumbnail without changing base-video state."""
        from clipah.publishing.providers.youtube.status import (
            AttachmentResult,
            AttachmentState,
            normalize_youtube_response,
        )

        if descriptor.kind is not YouTubeAttachmentKind.THUMBNAIL:
            raise ValueError("thumbnail operation requires a thumbnail descriptor")
        if not supported:
            return AttachmentResult(video_id=video_id, state=AttachmentState.UNSUPPORTED)
        _require_attachment_body(descriptor, body)
        response = self._client.post(
            f"{self._api_origin}/upload/youtube/v3/thumbnails/set",
            params={"videoId": video_id},
            headers={
                **_authorization(access_token),
                "Content-Type": descriptor.content_type,
            },
            content=body,
        )
        normalize_youtube_response(response)
        resource_id = _safe_resource_id(response)
        return AttachmentResult(
            video_id=video_id,
            state=AttachmentState.SUCCEEDED,
            resource_id=resource_id,
        )

    def insert_caption(
        self,
        *,
        video_id: str,
        descriptor: YouTubeAttachment,
        body: bytes,
        granted_scopes: frozenset[str],
        access_token: SecretStr,
    ) -> object:
        """Insert one timed caption track independently from the base upload."""
        from clipah.publishing.providers.youtube.status import (
            AttachmentResult,
            AttachmentState,
            normalize_youtube_response,
        )

        require_youtube_publish_scopes(granted_scopes, captions=True)
        if descriptor.kind is not YouTubeAttachmentKind.CAPTION:
            raise ValueError("caption operation requires a caption descriptor")
        _require_attachment_body(descriptor, body)
        metadata = json.dumps(
            {
                "snippet": {
                    "videoId": video_id,
                    "language": descriptor.language,
                    "name": descriptor.name,
                    "isDraft": False,
                }
            },
            separators=(",", ":"),
        )
        response = self._client.post(
            f"{self._api_origin}/upload/youtube/v3/captions",
            params={"part": "snippet"},
            headers=_authorization(access_token),
            files={
                "metadata": ("metadata.json", metadata, "application/json"),
                "media": ("captions.vtt", body, descriptor.content_type),
            },
        )
        normalize_youtube_response(response)
        return AttachmentResult(
            video_id=video_id,
            state=AttachmentState.SUCCEEDED,
            resource_id=_safe_resource_id(response),
        )

    def _resumable_dependencies(
        self,
    ) -> tuple[YouTubeCheckpointVault, YouTubeUploadContext]:
        """Require a tenant-bound secret vault before resumable provider work."""
        if self._checkpoint_vault is None or self._upload_context is None:
            raise YouTubeResumableError("YouTube resumable storage is unavailable")
        return self._checkpoint_vault, self._upload_context


def _authorization(access_token: SecretStr) -> dict[str, str]:
    """Construct a short-lived provider header without retaining plaintext state."""
    return {"Authorization": f"Bearer {access_token.get_secret_value()}"}


def _normalize_response(response: httpx.Response) -> None:
    """Apply shared sanitized provider-error mapping without an import cycle."""
    from clipah.publishing.providers.youtube.status import normalize_youtube_response

    normalize_youtube_response(response)


def _acknowledged_bytes(value: str | None, *, total_bytes: int, allow_empty: bool) -> int:
    """Convert YouTube's inclusive Range header into the next safe byte offset."""
    if value is None:
        if allow_empty:
            return 0
        raise YouTubeResumableError("YouTube omitted the acknowledged byte range")
    prefix = "bytes=0-"
    if not value.startswith(prefix):
        raise YouTubeResumableError("YouTube returned a malformed byte range")
    try:
        acknowledged = int(value.removeprefix(prefix)) + 1
    except ValueError as error:
        raise YouTubeResumableError("YouTube returned a malformed byte range") from error
    if not 0 < acknowledged <= total_bytes:
        raise YouTubeResumableError("YouTube returned an impossible byte range")
    return acknowledged


def _completed_checkpoint(
    checkpoint: UploadCheckpoint, response: httpx.Response
) -> UploadCheckpoint:
    """Extract one authoritative video ID from a successful resumable response."""
    try:
        payload = response.json()
    except ValueError as error:
        raise YouTubeResumableError("YouTube completion response is malformed") from error
    video_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(video_id, str) or not video_id:
        raise YouTubeResumableError("YouTube completion response has no video ID")
    return replace(
        checkpoint,
        acknowledged_bytes=checkpoint.total_bytes,
        final_request_ambiguous=False,
        progress=UploadProgress.COMPLETE,
        provider_video_id=video_id,
    )


def _require_attachment_body(descriptor: YouTubeAttachment, body: bytes) -> None:
    """Require bytes to match the immutable descriptor used for confirmation."""
    if len(body) != descriptor.size_bytes:
        raise ValueError("attachment bytes do not match the frozen descriptor")


def _safe_resource_id(response: httpx.Response) -> str | None:
    """Read only a bounded provider resource ID from a successful response."""
    try:
        payload = response.json()
    except ValueError:
        return None
    resource_id = payload.get("id") if isinstance(payload, dict) else None
    return resource_id if isinstance(resource_id, str) and 0 < len(resource_id) <= 512 else None
