"""Strict YouTube processing parsing and sanitized provider errors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

import httpx

from clipah.publishing.providers.youtube.adapter import YouTubePrivacy


class YouTubeProviderError(Exception):
    """Stable secret-free failure returned by the official YouTube boundary."""

    code = "youtube_provider_error"

    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        """Retain only a fixed message and optional safe retry delay."""
        super().__init__(message)
        self.retry_after = retry_after


class YouTubeReconnectRequiredError(YouTubeProviderError):
    """The OAuth Grant is expired, revoked, or otherwise invalid."""

    code = "youtube_reconnect_required"


class YouTubeRateLimitedError(YouTubeProviderError):
    """YouTube temporarily limited this publishing operation."""

    code = "youtube_rate_limited"


class YouTubeQuotaExhaustedError(YouTubeProviderError):
    """The YouTube API project has exhausted its current quota."""

    code = "youtube_quota_exhausted"


class YouTubeUnavailableError(YouTubeProviderError):
    """The provider boundary failed in a retryable way."""

    code = "youtube_unavailable"


class YouTubePermanentError(YouTubeProviderError):
    """YouTube permanently rejected validated metadata, policy, or media."""

    code = "youtube_permanent_failure"


class YouTubeAmbiguousStatusError(YouTubeProviderError):
    """A known video ID is absent or cannot be reconciled safely."""

    code = "youtube_ambiguous_completion"


class YouTubeOutcome(StrEnum):
    """Normalized authoritative YouTube processing outcomes."""

    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"


class AttachmentState(StrEnum):
    """Durable result of one optional post-upload operation."""

    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILED = "retryable_failed"
    PERMANENT_FAILED = "permanent_failed"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class AttachmentResult:
    """Safe attachment outcome that retains the authoritative base video ID."""

    video_id: str
    state: AttachmentState
    resource_id: str | None = None


@dataclass(frozen=True, slots=True)
class YouTubeStatus:
    """Safe provider state for one authoritative YouTube video ID."""

    video_id: str
    outcome: YouTubeOutcome
    privacy: YouTubePrivacy
    scheduled_for: datetime | None
    failure_code: str | None


def normalize_youtube_response(response: httpx.Response) -> None:
    """Raise one stable error for a non-successful YouTube HTTP response."""
    if 200 <= response.status_code < 300:
        return
    reason = _error_reason(response)
    if response.status_code == 401 or reason in {"authError", "invalidCredentials"}:
        raise YouTubeReconnectRequiredError("YouTube authorization must be reconnected")
    if reason in {"quotaExceeded", "dailyLimitExceeded"}:
        raise YouTubeQuotaExhaustedError("YouTube publishing quota is exhausted")
    if response.status_code == 429:
        raise YouTubeRateLimitedError(
            "YouTube publishing is temporarily rate limited",
            retry_after=_retry_after(response.headers.get("Retry-After")),
        )
    if response.status_code in {500, 502, 503, 504}:
        raise YouTubeUnavailableError("YouTube publishing is temporarily unavailable")
    raise YouTubePermanentError("YouTube rejected the publishing operation")


def parse_youtube_status(*, provider_id: str, payload: object) -> YouTubeStatus:
    """Parse only the requested status fields for exactly one known video ID."""
    if not isinstance(payload, dict):
        raise YouTubePermanentError("YouTube status response is malformed")
    items = payload.get("items")
    if not isinstance(items, list):
        raise YouTubePermanentError("YouTube status response is malformed")
    matches = [item for item in items if isinstance(item, dict) and item.get("id") == provider_id]
    if not matches:
        raise YouTubeAmbiguousStatusError("YouTube video status is ambiguous")
    if len(matches) != 1:
        raise YouTubePermanentError("YouTube status response is malformed")
    item = matches[0]
    status = item.get("status")
    details = item.get("processingDetails")
    if not isinstance(status, dict) or not isinstance(details, dict):
        raise YouTubePermanentError("YouTube status response is malformed")
    raw_privacy = status.get("privacyStatus")
    try:
        privacy = YouTubePrivacy(raw_privacy) if isinstance(raw_privacy, str) else None
    except ValueError as error:
        raise YouTubePermanentError("YouTube status response is malformed") from error
    if privacy is None:
        raise YouTubePermanentError("YouTube status response is malformed")
    processing = details.get("processingStatus")
    upload = status.get("uploadStatus")
    outcome = _outcome(processing=processing, upload=upload)
    scheduled_for = _scheduled_for(status.get("publishAt"))
    failure = details.get("processingFailureReason")
    return YouTubeStatus(
        video_id=provider_id,
        outcome=outcome,
        privacy=privacy,
        scheduled_for=scheduled_for,
        failure_code=_safe_failure_code(failure) if outcome is YouTubeOutcome.FAILED else None,
    )


def _outcome(*, processing: object, upload: object) -> YouTubeOutcome:
    """Map provider processing and upload values onto the closed local lifecycle."""
    if upload == "rejected" or processing == "terminated":
        return YouTubeOutcome.REJECTED
    if upload == "failed" or processing == "failed":
        return YouTubeOutcome.FAILED
    if processing == "succeeded":
        return YouTubeOutcome.SUCCEEDED
    if processing in {"processing", "pending"}:
        return YouTubeOutcome.PROCESSING
    raise YouTubePermanentError("YouTube status response is malformed")


def _scheduled_for(value: object) -> datetime | None:
    """Parse an optional aware provider schedule without accepting local time."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise YouTubePermanentError("YouTube status response is malformed")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise YouTubePermanentError("YouTube status response is malformed") from error
    if parsed.utcoffset() is None:
        raise YouTubePermanentError("YouTube status response is malformed")
    return parsed


def _error_reason(response: httpx.Response) -> str | None:
    """Read only a bounded documented reason from an error response."""
    try:
        payload: Any = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("error"), dict):
        return None
    errors = payload["error"].get("errors")
    if not isinstance(errors, list) or not errors or not isinstance(errors[0], dict):
        return None
    reason = errors[0].get("reason")
    return reason if isinstance(reason, str) and len(reason) <= 64 else None


def _retry_after(value: str | None) -> int | None:
    """Retain a bounded integer delay and discard every other header form."""
    try:
        result = int(value) if value is not None else None
    except ValueError:
        return None
    return result if result is not None and 0 <= result <= 86_400 else None


def _safe_failure_code(value: object) -> str:
    """Keep only documented bounded processing reasons."""
    allowed = {"codec", "fileFormat", "uploadFailed", "tooSmall", "emptyFile"}
    return value if isinstance(value, str) and value in allowed else "processing_failed"
