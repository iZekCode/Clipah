"""Verification-only ingestion for signed TikTok Content Posting webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, cast

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from starlette.responses import Response

from clipah.api.errors import ApiError
from clipah.observability.metrics import count

TIKTOK_WEBHOOK_INVALID_CODE = "TIKTOK_WEBHOOK_INVALID"
TIKTOK_WEBHOOK_REPLAY_SECONDS = 300
TIKTOK_SIGNATURE_HEADER = "tiktok-signature"

router = APIRouter(prefix="/api/v1")


class TikTokWebhookEventKind(StrEnum):
    """The TikTok deliveries Clipah is registered to reconcile."""

    PUBLISH_COMPLETE = "publish_complete"
    PUBLISH_FAILED = "publish_failed"
    INBOX_DELIVERED = "inbox_delivered"
    AUTHORIZATION_REMOVED = "authorization_removed"


_EVENT_KINDS = {
    "post.publish.complete": TikTokWebhookEventKind.PUBLISH_COMPLETE,
    "post.publish.failed": TikTokWebhookEventKind.PUBLISH_FAILED,
    "post.publish.inbox_delivered": TikTokWebhookEventKind.INBOX_DELIVERED,
    "authorization.removed": TikTokWebhookEventKind.AUTHORIZATION_REMOVED,
}


class TikTokWebhookEvent(BaseModel):
    """The complete sanitized signal permitted to cross the TikTok webhook boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: TikTokWebhookEventKind
    external_account_id: str = Field(min_length=1, max_length=255, strict=True)
    publish_id: str | None = Field(default=None, max_length=255, strict=True)
    event_digest: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)
    received_at: datetime

    @field_validator("received_at")
    @classmethod
    def require_utc_receipt(cls, value: datetime) -> datetime:
        """Keep webhook receipt times unambiguous for later durable reconciliation."""
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("TikTok webhook receipt time must be timezone-aware UTC")
        return value


class TikTokWebhookVerificationError(Exception):
    """A failed TikTok signature or payload check carrying one stable safe code."""

    def __init__(self) -> None:
        """Discard verification internals and provider-controlled values."""
        super().__init__(TIKTOK_WEBHOOK_INVALID_CODE)
        self.code = TIKTOK_WEBHOOK_INVALID_CODE


class TikTokWebhookSink(Protocol):
    """Awaited ingestion seam that records and reconciles one delivery durably."""

    async def __call__(self, *, event: TikTokWebhookEvent) -> None:
        """Record and dispatch one sanitized delivery idempotently by event digest."""


class TikTokWebhookVerifier:
    """Verify TikTok's timestamped HMAC over the exact raw delivery bytes."""

    def __init__(self, *, client_key: str, client_secret: SecretStr) -> None:
        """Bind the one registered application permitted to report publish truth."""
        self._client_key = client_key
        self._client_secret = client_secret

    def verify(
        self, *, headers: Mapping[str, str], body: bytes, now: datetime
    ) -> TikTokWebhookEvent:
        """Validate signature, replay window, and application identity before persistence."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise TikTokWebhookVerificationError
        normalized = {name.casefold(): value for name, value in headers.items()}
        timestamp_text, signature = _signature_parts(normalized.get(TIKTOK_SIGNATURE_HEADER))
        timestamp = _timestamp(timestamp_text)
        if abs(now.timestamp() - timestamp) > TIKTOK_WEBHOOK_REPLAY_SECONDS:
            raise TikTokWebhookVerificationError
        expected = hmac.new(
            self._client_secret.get_secret_value().encode("utf-8"),
            f"{timestamp_text}.".encode() + body,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected):
            raise TikTokWebhookVerificationError

        payload = _body_object(body)
        if payload.get("client_key") != self._client_key:
            raise TikTokWebhookVerificationError
        raw_event = payload.get("event")
        kind = _EVENT_KINDS.get(raw_event) if isinstance(raw_event, str) else None
        open_id = payload.get("user_openid")
        if kind is None or not isinstance(open_id, str) or not 0 < len(open_id) <= 255:
            raise TikTokWebhookVerificationError
        publish_id = (
            None
            if kind is TikTokWebhookEventKind.AUTHORIZATION_REMOVED
            else _publish_id(payload.get("content"))
        )
        digest = hashlib.sha256(b"tiktok:" + hashlib.sha256(body).digest()).hexdigest()
        try:
            return TikTokWebhookEvent(
                kind=kind,
                external_account_id=open_id,
                publish_id=publish_id,
                event_digest=digest,
                received_at=now.astimezone(UTC),
            )
        except ValueError:
            raise TikTokWebhookVerificationError from None


@router.post("/webhooks/tiktok", status_code=204)
async def receive_tiktok_webhook(request: Request) -> Response:
    """Verify the raw delivery once, await its injected sink, and acknowledge it."""
    verifier = cast(TikTokWebhookVerifier | None, request.app.state.tiktok_webhook_verifier)
    sink = cast(TikTokWebhookSink | None, request.app.state.tiktok_webhook_sink)
    clock = cast(Callable[[], datetime], request.app.state.tiktok_webhook_clock)
    if verifier is None or sink is None:
        raise ApiError(status_code=503, code="SERVICE_UNAVAILABLE")
    body = await request.body()
    try:
        event = verifier.verify(headers=request.headers, body=body, now=clock())
    except TikTokWebhookVerificationError:
        count("clipah.webhook.rejected", provider="tiktok", reason="verification_failed")
        raise ApiError(status_code=400, code=TIKTOK_WEBHOOK_INVALID_CODE) from None
    await sink(event=event)
    count("clipah.webhook.accepted", provider="tiktok", outcome="recorded")
    return Response(status_code=204)


def _signature_parts(value: str | None) -> tuple[str, bytes]:
    """Split the documented `t=<seconds>,s=<hex>` header into its two parts."""
    if value is None:
        raise TikTokWebhookVerificationError
    fields: dict[str, str] = {}
    for part in value.split(","):
        name, separator, item = part.partition("=")
        if not separator:
            raise TikTokWebhookVerificationError
        fields[name.strip()] = item.strip()
    timestamp = fields.get("t")
    signature = fields.get("s")
    if not timestamp or not signature:
        raise TikTokWebhookVerificationError
    try:
        decoded = bytes.fromhex(signature)
    except ValueError:
        raise TikTokWebhookVerificationError from None
    if len(decoded) != 32:
        raise TikTokWebhookVerificationError
    return timestamp, decoded


def _timestamp(value: str) -> int:
    """Decode a base-ten Unix second without accepting alternate representations."""
    if not value.isascii() or not value.isdecimal():
        raise TikTokWebhookVerificationError
    try:
        return int(value)
    except ValueError:
        raise TikTokWebhookVerificationError from None


def _body_object(body: bytes) -> dict[str, Any]:
    """Decode a signed UTF-8 JSON object only after authenticating its raw bytes."""
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise TikTokWebhookVerificationError from None
    if not isinstance(payload, dict):
        raise TikTokWebhookVerificationError
    return payload


def _publish_id(content: object) -> str | None:
    """Read only a bounded publish identifier from the nested content document."""
    if not isinstance(content, str):
        return None
    try:
        document = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(document, dict):
        return None
    publish_id = document.get("publish_id")
    return publish_id if isinstance(publish_id, str) and 0 < len(publish_id) <= 255 else None
