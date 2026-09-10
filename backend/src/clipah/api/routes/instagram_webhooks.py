"""Verification-only ingestion for Meta's Instagram deauthorization and deletion callbacks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, cast
from urllib.parse import parse_qsl, quote

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from starlette.responses import JSONResponse, Response

from clipah.api.errors import ApiError
from clipah.config import Settings
from clipah.observability.metrics import count

INSTAGRAM_WEBHOOK_INVALID_CODE = "INSTAGRAM_WEBHOOK_INVALID"
INSTAGRAM_WEBHOOK_REPLAY_SECONDS = 300
INSTAGRAM_SIGNING_ALGORITHM = "HMAC-SHA256"
CONFIRMATION_CODE_LENGTH = 32

router = APIRouter(prefix="/api/v1")


class InstagramWebhookKind(StrEnum):
    """The two Meta callbacks Clipah is registered to receive."""

    DEAUTHORIZATION = "deauthorization"
    DATA_DELETION = "data_deletion"


class InstagramWebhookEvent(BaseModel):
    """The complete sanitized signal permitted to cross the Instagram callback boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: InstagramWebhookKind
    external_user_id: str = Field(min_length=1, max_length=64, strict=True)
    event_digest: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)
    issued_at: datetime
    received_at: datetime

    @field_validator("issued_at", "received_at")
    @classmethod
    def require_utc_instants(cls, value: datetime) -> datetime:
        """Keep callback times unambiguous for later durable reconciliation."""
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("Instagram callback times must be timezone-aware UTC")
        return value


class InstagramWebhookVerificationError(Exception):
    """A failed Meta signature or payload check carrying one stable safe code."""

    def __init__(self) -> None:
        """Discard verification internals and provider-controlled values."""
        super().__init__(INSTAGRAM_WEBHOOK_INVALID_CODE)
        self.code = INSTAGRAM_WEBHOOK_INVALID_CODE


class InstagramWebhookSink(Protocol):
    """Awaited ingestion seam that records and reconciles one callback durably."""

    async def __call__(self, *, event: InstagramWebhookEvent) -> None:
        """Record and dispatch one sanitized callback idempotently by event digest."""


class InstagramWebhookVerifier:
    """Verify Meta `signed_request` payloads against this deployment's app secret."""

    def __init__(self, *, app_secret: SecretStr) -> None:
        """Bind the one registered secret permitted to authenticate a callback."""
        self._app_secret = app_secret

    def verify(
        self, *, kind: InstagramWebhookKind, signed_request: str, now: datetime
    ) -> InstagramWebhookEvent:
        """Validate signature, algorithm, and replay window before anything is persisted."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise InstagramWebhookVerificationError
        encoded_signature, _, encoded_payload = signed_request.partition(".")
        if not encoded_signature or not encoded_payload or not encoded_payload.isascii():
            raise InstagramWebhookVerificationError
        signature = _decode(encoded_signature)
        expected = hmac.new(
            self._app_secret.get_secret_value().encode("utf-8"),
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected):
            raise InstagramWebhookVerificationError

        payload = _payload_object(_decode(encoded_payload))
        if payload.get("algorithm") != INSTAGRAM_SIGNING_ALGORITHM:
            raise InstagramWebhookVerificationError
        issued_at = _issued_at(payload.get("issued_at"))
        if abs(now.timestamp() - issued_at.timestamp()) > INSTAGRAM_WEBHOOK_REPLAY_SECONDS:
            raise InstagramWebhookVerificationError
        user_id = payload.get("user_id")
        if not isinstance(user_id, str) or not 0 < len(user_id) <= 64:
            raise InstagramWebhookVerificationError

        digest = hashlib.sha256(f"{kind.value}:{encoded_payload}".encode()).hexdigest()
        try:
            return InstagramWebhookEvent(
                kind=kind,
                external_user_id=user_id,
                event_digest=digest,
                issued_at=issued_at,
                received_at=now.astimezone(UTC),
            )
        except ValueError:
            raise InstagramWebhookVerificationError from None


@router.post("/webhooks/instagram/deauthorization", status_code=204)
async def receive_deauthorization(request: Request) -> Response:
    """Verify one deauthorization callback, await its injected sink, and acknowledge it."""
    event = _verified_event(
        request,
        kind=InstagramWebhookKind.DEAUTHORIZATION,
        signed_request=_signed_request(await request.body()),
    )
    await _sink(request)(event=event)
    return Response(status_code=204)


@router.post("/webhooks/instagram/data-deletion")
async def receive_data_deletion(request: Request) -> Response:
    """Verify one deletion callback and return the status URL and code Meta requires."""
    event = _verified_event(
        request,
        kind=InstagramWebhookKind.DATA_DELETION,
        signed_request=_signed_request(await request.body()),
    )
    settings = cast(Settings, request.app.state.settings)
    if settings.frontend_origin is None:
        raise ApiError(status_code=503, code="SERVICE_UNAVAILABLE")
    await _sink(request)(event=event)
    confirmation_code = event.event_digest[:CONFIRMATION_CODE_LENGTH]
    origin = settings.frontend_origin.rstrip("/")
    return JSONResponse(
        status_code=200,
        content={
            "url": f"{origin}/data-deletion?code={quote(confirmation_code, safe='')}",
            "confirmation_code": confirmation_code,
        },
    )


def _signed_request(body: bytes) -> str:
    """Read the one form field Meta delivers without adding a multipart dependency."""
    try:
        fields = dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))
    except UnicodeDecodeError:
        raise ApiError(status_code=400, code=INSTAGRAM_WEBHOOK_INVALID_CODE) from None
    value = fields.get("signed_request")
    if not value:
        raise ApiError(status_code=400, code=INSTAGRAM_WEBHOOK_INVALID_CODE)
    return value


def _verified_event(
    request: Request, *, kind: InstagramWebhookKind, signed_request: str
) -> InstagramWebhookEvent:
    """Refuse an unconfigured boundary and verify the delivery exactly once."""
    verifier = cast(InstagramWebhookVerifier | None, request.app.state.instagram_webhook_verifier)
    sink = cast(InstagramWebhookSink | None, request.app.state.instagram_webhook_sink)
    clock = cast(Callable[[], datetime], request.app.state.instagram_webhook_clock)
    if verifier is None or sink is None:
        raise ApiError(status_code=503, code="SERVICE_UNAVAILABLE")
    try:
        verified = verifier.verify(kind=kind, signed_request=signed_request, now=clock())
    except InstagramWebhookVerificationError:
        count("clipah.webhook.rejected", provider="instagram", reason="verification_failed")
        raise ApiError(status_code=400, code=INSTAGRAM_WEBHOOK_INVALID_CODE) from None
    count("clipah.webhook.accepted", provider="instagram", outcome="recorded")
    return verified


def _sink(request: Request) -> InstagramWebhookSink:
    """Return the configured ingestion seam proved present during verification."""
    return cast(InstagramWebhookSink, request.app.state.instagram_webhook_sink)


def _decode(value: str) -> bytes:
    """Decode one strict base64url segment without accepting alternate forms."""
    if not value.isascii():
        raise InstagramWebhookVerificationError
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError):
        raise InstagramWebhookVerificationError from None


def _payload_object(raw: bytes) -> dict[str, Any]:
    """Decode an authenticated UTF-8 JSON object after its bytes were verified."""
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise InstagramWebhookVerificationError from None
    if not isinstance(payload, dict):
        raise InstagramWebhookVerificationError
    return payload


def _issued_at(value: object) -> datetime:
    """Read one bounded Unix second as an aware UTC instant."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise InstagramWebhookVerificationError
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError):
        raise InstagramWebhookVerificationError from None
