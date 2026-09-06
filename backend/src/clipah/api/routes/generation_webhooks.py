"""Verification-only ingestion for signed fal generated-media webhooks."""

from __future__ import annotations

import base64
import hashlib
import json
import math
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol, cast

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.responses import Response

from clipah.api.errors import ApiError
from clipah.broll.generation import GenerationStatus

FAL_JWKS_URL = "https://rest.fal.ai/.well-known/jwks.json"
FAL_WEBHOOK_REPLAY_SECONDS = 300
FAL_JWKS_MAX_CACHE_SECONDS = 86_400
FAL_WEBHOOK_INVALID_CODE = "GENERATION_WEBHOOK_INVALID"

router = APIRouter(prefix="/api/v1")


class FalWebhookEvent(BaseModel):
    """The complete sanitized signal permitted to cross the fal webhook boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    provider_request_id: str = Field(min_length=1, max_length=256, strict=True)
    status: GenerationStatus
    payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)
    received_at: datetime

    @field_validator("received_at")
    @classmethod
    def require_utc_received_at(cls, value: datetime) -> datetime:
        """Keep webhook receipt times unambiguous for later durable reconciliation."""
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("fal webhook receipt time must be timezone-aware UTC")
        return value


class GenerationWebhookSink(Protocol):
    """Awaited ingestion seam implemented durably with tenant context in Task 4."""

    async def __call__(self, *, event: FalWebhookEvent) -> None:
        """Record and dispatch one sanitized event idempotently by provider request ID."""


class FalWebhookVerificationError(Exception):
    """A failed fal signature or payload check carrying one stable safe code."""

    def __init__(self) -> None:
        """Discard verification internals and provider-controlled values."""
        super().__init__(FAL_WEBHOOK_INVALID_CODE)
        self.code = FAL_WEBHOOK_INVALID_CODE


class FalWebhookVerifier:
    """Verify fal ED25519 messages against a bounded in-memory JWKS cache."""

    def __init__(
        self,
        *,
        client: httpx.Client,
        monotonic: Callable[[], float],
        cache_ttl_seconds: float,
        http_timeout_seconds: float,
    ) -> None:
        """Bind HTTP and time dependencies while enforcing fal's cache ceiling."""
        if (
            not math.isfinite(cache_ttl_seconds)
            or not 0 < cache_ttl_seconds <= FAL_JWKS_MAX_CACHE_SECONDS
        ):
            raise ValueError("fal JWKS cache must be between zero and 24 hours")
        if not math.isfinite(http_timeout_seconds) or http_timeout_seconds <= 0:
            raise ValueError("fal JWKS timeout must be positive and finite")
        self._client = client
        self._monotonic = monotonic
        self._cache_ttl_seconds = cache_ttl_seconds
        self._http_timeout_seconds = http_timeout_seconds
        self._cached_keys: tuple[Ed25519PublicKey, ...] | None = None
        self._cached_at: float | None = None

    def verify(
        self,
        *,
        headers: Mapping[str, str],
        body: bytes,
        now: datetime,
    ) -> FalWebhookEvent:
        """Validate signature, replay time, raw digest, and the minimal event schema."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise FalWebhookVerificationError
        normalized_headers = {name.casefold(): value for name, value in headers.items()}
        request_id = _required_header(normalized_headers, "x-fal-webhook-request-id")
        user_id = _required_header(normalized_headers, "x-fal-webhook-user-id")
        timestamp_text = _required_header(normalized_headers, "x-fal-webhook-timestamp")
        signature_text = _required_header(normalized_headers, "x-fal-webhook-signature")
        timestamp = _timestamp(timestamp_text)
        if abs(now.timestamp() - timestamp) > FAL_WEBHOOK_REPLAY_SECONDS:
            raise FalWebhookVerificationError

        digest = hashlib.sha256(body).hexdigest()
        message = "\n".join((request_id, user_id, timestamp_text, digest)).encode("utf-8")
        signature = _signature(signature_text)
        if not any(_signature_matches(key, signature, message) for key in self._keys()):
            raise FalWebhookVerificationError

        payload = _body_object(body)
        if payload.get("request_id") != request_id:
            raise FalWebhookVerificationError
        raw_status = payload.get("status")
        if raw_status == "OK":
            status = GenerationStatus.SUCCEEDED
        elif raw_status == "ERROR":
            status = GenerationStatus.FAILED
        else:
            raise FalWebhookVerificationError
        try:
            return FalWebhookEvent(
                provider_request_id=request_id,
                status=status,
                payload_digest=digest,
                received_at=now.astimezone(UTC),
            )
        except ValueError:
            raise FalWebhookVerificationError from None

    def _keys(self) -> tuple[Ed25519PublicKey, ...]:
        """Return cached verification keys or refresh from fal after the cache bound."""
        cache_now = self._monotonic()
        if (
            self._cached_keys is not None
            and self._cached_at is not None
            and cache_now - self._cached_at <= self._cache_ttl_seconds
        ):
            return self._cached_keys
        try:
            response = self._client.get(FAL_JWKS_URL, timeout=self._http_timeout_seconds)
        except httpx.HTTPError:
            raise FalWebhookVerificationError from None
        if response.status_code != 200:
            raise FalWebhookVerificationError
        payload = _response_object(response)
        key_values = payload.get("keys")
        if not isinstance(key_values, list):
            raise FalWebhookVerificationError
        keys = tuple(key for value in key_values if (key := _decode_key(value)) is not None)
        if not keys:
            raise FalWebhookVerificationError
        self._cached_keys = keys
        self._cached_at = cache_now
        return keys


@router.post("/webhooks/generation/fal", status_code=204)
async def receive_fal_webhook(request: Request) -> Response:
    """Verify the raw delivery once, await its injected sink, and acknowledge it."""
    verifier = cast(FalWebhookVerifier | None, request.app.state.generation_webhook_verifier)
    sink = cast(GenerationWebhookSink | None, request.app.state.generation_webhook_sink)
    clock = cast(Callable[[], datetime], request.app.state.generation_webhook_clock)
    if verifier is None or sink is None:
        raise ApiError(status_code=503, code="SERVICE_UNAVAILABLE")
    body = await request.body()
    try:
        event = verifier.verify(headers=request.headers, body=body, now=clock())
    except FalWebhookVerificationError:
        raise ApiError(status_code=400, code=FAL_WEBHOOK_INVALID_CODE) from None
    await sink(event=event)
    return Response(status_code=204)


def _required_header(headers: Mapping[str, str], name: str) -> str:
    """Read one non-blank signature header or fail closed."""
    value = headers.get(name)
    if value is None or not value or value != value.strip():
        raise FalWebhookVerificationError
    return value


def _timestamp(value: str) -> int:
    """Decode a base-ten Unix second without accepting alternate representations."""
    if not value.isascii() or not value.isdecimal():
        raise FalWebhookVerificationError
    try:
        return int(value)
    except ValueError:
        raise FalWebhookVerificationError from None


def _signature(value: str) -> bytes:
    """Decode exactly one hexadecimal ED25519 signature."""
    try:
        signature = bytes.fromhex(value)
    except ValueError:
        raise FalWebhookVerificationError from None
    if len(signature) != 64:
        raise FalWebhookVerificationError
    return signature


def _signature_matches(key: Ed25519PublicKey, signature: bytes, message: bytes) -> bool:
    """Return whether one public key authenticates the exact message bytes."""
    try:
        key.verify(signature, message)
    except InvalidSignature:
        return False
    return True


def _decode_key(value: object) -> Ed25519PublicKey | None:
    """Decode one strict OKP/Ed25519 JWKS value, ignoring unrelated or malformed keys."""
    if not isinstance(value, dict) or value.get("kty") != "OKP" or value.get("crv") != "Ed25519":
        return None
    encoded = value.get("x")
    if not isinstance(encoded, str):
        return None
    try:
        padding = "=" * (-len(encoded) % 4)
        raw = base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
        return Ed25519PublicKey.from_public_bytes(raw)
    except (ValueError, TypeError):
        return None


def _body_object(body: bytes) -> dict[str, Any]:
    """Decode a signed UTF-8 JSON object only after authenticating its raw bytes."""
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise FalWebhookVerificationError from None
    if not isinstance(payload, dict):
        raise FalWebhookVerificationError
    return payload


def _response_object(response: httpx.Response) -> dict[str, Any]:
    """Decode a JWKS response without retaining provider-controlled response text."""
    try:
        payload = response.json()
    except ValueError:
        raise FalWebhookVerificationError from None
    if not isinstance(payload, dict):
        raise FalWebhookVerificationError
    return payload
