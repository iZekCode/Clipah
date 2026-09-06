"""Signed fal webhook verification and ingestion contracts exercised in process."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from clipah.api.app import create_app
from clipah.api.routes.generation_webhooks import (
    FalWebhookEvent,
    FalWebhookVerificationError,
    FalWebhookVerifier,
)
from clipah.broll.generation import GenerationStatus
from clipah.config import Environment, Settings

NOW = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
BODY = b'{"request_id":"fal-request","status":"OK","payload":{"seed":7}}'


def _public_key_x(private_key: Ed25519PrivateKey) -> str:
    """Encode one raw ED25519 public key in JWKS base64url form."""
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _signed_headers(
    private_key: Ed25519PrivateKey,
    *,
    body: bytes = BODY,
    request_id: str = "fal-request",
    user_id: str = "fal-user",
    timestamp: str | None = None,
) -> dict[str, str]:
    """Sign the exact documented four-line fal message for one raw request body."""
    timestamp = timestamp or str(int(NOW.timestamp()))
    digest = hashlib.sha256(body).hexdigest()
    message = "\n".join((request_id, user_id, timestamp, digest)).encode()
    return {
        "X-Fal-Webhook-Request-Id": request_id,
        "X-Fal-Webhook-User-Id": user_id,
        "X-Fal-Webhook-Timestamp": timestamp,
        "X-Fal-Webhook-Signature": private_key.sign(message).hex(),
    }


def _verifier(
    private_key: Ed25519PrivateKey,
    *,
    tick: list[float] | None = None,
    requests: list[httpx.Request] | None = None,
) -> FalWebhookVerifier:
    """Build a verifier against a local JWKS response and hand-wound cache clock."""
    cache_clock = tick or [0.0]

    def respond(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return httpx.Response(
            200,
            json={
                "keys": [
                    {
                        "kty": "OKP",
                        "crv": "Ed25519",
                        "x": _public_key_x(private_key),
                    }
                ]
            },
        )

    return FalWebhookVerifier(
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        monotonic=lambda: cache_clock[0],
        cache_ttl_seconds=60,
        http_timeout_seconds=5,
    )


@pytest.mark.unit
def test_fal_webhook_verifies_raw_body_and_returns_only_sanitized_fields() -> None:
    """A valid signature must retain identity, status, digest, and receipt time only."""
    private_key = Ed25519PrivateKey.generate()
    verifier = _verifier(private_key)

    event = verifier.verify(headers=_signed_headers(private_key), body=BODY, now=NOW)

    assert event == FalWebhookEvent(
        provider_request_id="fal-request",
        status=GenerationStatus.SUCCEEDED,
        payload_digest=hashlib.sha256(BODY).hexdigest(),
        received_at=NOW,
    )
    assert '"seed"' not in repr(event)
    assert "fal-user" not in repr(event)


@pytest.mark.unit
def test_fal_webhook_event_requires_an_explicit_utc_receipt_time() -> None:
    """A persisted wakeup must not carry a local offset that obscures replay ordering."""
    with pytest.raises(ValueError):
        FalWebhookEvent(
            provider_request_id="fal-request",
            status=GenerationStatus.SUCCEEDED,
            payload_digest="0" * 64,
            received_at=NOW.astimezone(timezone(timedelta(hours=7))),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "missing",
    (
        "X-Fal-Webhook-Request-Id",
        "X-Fal-Webhook-User-Id",
        "X-Fal-Webhook-Timestamp",
        "X-Fal-Webhook-Signature",
    ),
)
def test_fal_webhook_requires_all_four_signature_headers(missing: str) -> None:
    """No unsigned or partially signed request may reach the ingestion sink."""
    private_key = Ed25519PrivateKey.generate()
    headers = _signed_headers(private_key)
    del headers[missing]

    with pytest.raises(FalWebhookVerificationError) as raised:
        _verifier(private_key).verify(headers=headers, body=BODY, now=NOW)

    assert raised.value.code == "GENERATION_WEBHOOK_INVALID"


@pytest.mark.unit
@pytest.mark.parametrize("offset_seconds", (-301, 301))
def test_fal_webhook_rejects_timestamps_outside_the_replay_window(offset_seconds: int) -> None:
    """Deliveries outside either side of the five-minute clock window are replays."""
    private_key = Ed25519PrivateKey.generate()
    timestamp = str(int((NOW + timedelta(seconds=offset_seconds)).timestamp()))

    with pytest.raises(FalWebhookVerificationError):
        _verifier(private_key).verify(
            headers=_signed_headers(private_key, timestamp=timestamp),
            body=BODY,
            now=NOW,
        )


@pytest.mark.unit
@pytest.mark.parametrize("offset_seconds", (-300, 300))
def test_fal_webhook_accepts_replay_window_boundaries(offset_seconds: int) -> None:
    """Exactly five minutes of documented clock skew remains valid in either direction."""
    private_key = Ed25519PrivateKey.generate()
    timestamp = str(int((NOW + timedelta(seconds=offset_seconds)).timestamp()))

    event = _verifier(private_key).verify(
        headers=_signed_headers(private_key, timestamp=timestamp),
        body=BODY,
        now=NOW,
    )

    assert event.provider_request_id == "fal-request"


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutation",
    ("bad_hex", "bad_signature", "wrong_body", "wrong_order", "bad_key", "bad_timestamp"),
)
def test_fal_webhook_fails_closed_for_tampering_and_bad_key_material(mutation: str) -> None:
    """Malformed signatures, key material, timestamps, and raw-body changes must all fail."""
    private_key = Ed25519PrivateKey.generate()
    headers = _signed_headers(private_key)
    body = BODY
    verifier = _verifier(private_key)

    if mutation == "bad_hex":
        headers["X-Fal-Webhook-Signature"] = "not-hex"
    elif mutation == "bad_signature":
        headers["X-Fal-Webhook-Signature"] = "00" * 64
    elif mutation == "wrong_body":
        body += b" "
    elif mutation == "wrong_order":
        digest = hashlib.sha256(body).hexdigest()
        wrong = "\n".join(("fal-user", "fal-request", headers["X-Fal-Webhook-Timestamp"], digest))
        headers["X-Fal-Webhook-Signature"] = private_key.sign(wrong.encode()).hex()
    elif mutation == "bad_key":
        verifier = _verifier(Ed25519PrivateKey.generate())
    else:
        headers["X-Fal-Webhook-Timestamp"] = "not-a-timestamp"

    with pytest.raises(FalWebhookVerificationError):
        verifier.verify(headers=headers, body=body, now=NOW)


@pytest.mark.unit
def test_fal_webhook_jwks_cache_refreshes_after_its_bound() -> None:
    """Key rotation must become visible after the configured cache lifetime, never later."""
    private_key = Ed25519PrivateKey.generate()
    tick = [10.0]
    requests: list[httpx.Request] = []
    verifier = _verifier(private_key, tick=tick, requests=requests)

    verifier.verify(headers=_signed_headers(private_key), body=BODY, now=NOW)
    verifier.verify(headers=_signed_headers(private_key), body=BODY, now=NOW)
    tick[0] = 71.0
    verifier.verify(headers=_signed_headers(private_key), body=BODY, now=NOW)

    assert len(requests) == 2
    assert requests[0].url == "https://rest.fal.ai/.well-known/jwks.json"


@pytest.mark.unit
def test_fal_webhook_jwks_failure_retains_no_provider_exception_chain() -> None:
    """JWKS transport details must not survive inside the stable verification failure."""
    private_key = Ed25519PrivateKey.generate()

    def fail(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider URL https://secret.example")

    verifier = FalWebhookVerifier(
        client=httpx.Client(transport=httpx.MockTransport(fail)),
        monotonic=lambda: 0.0,
        cache_ttl_seconds=60,
        http_timeout_seconds=5,
    )

    with pytest.raises(FalWebhookVerificationError) as raised:
        verifier.verify(headers=_signed_headers(private_key), body=BODY, now=NOW)

    assert str(raised.value) == "GENERATION_WEBHOOK_INVALID"
    assert raised.value.__cause__ is None


class _IdempotentAwaitedSink:
    """Record one dispatch per request identity after crossing an actual await point."""

    def __init__(self) -> None:
        """Start without recorded requests or completed awaits."""
        self.seen: set[str] = set()
        self.dispatches: list[FalWebhookEvent] = []
        self.await_completed = False

    async def __call__(self, *, event: FalWebhookEvent) -> None:
        """Suppress duplicate request IDs and expose that the coroutine completed."""
        await asyncio.sleep(0)
        self.await_completed = True
        if event.provider_request_id in self.seen:
            return
        self.seen.add(event.provider_request_id)
        self.dispatches.append(event)


async def _post_webhook(app: FastAPI, *, body: bytes, headers: Mapping[str, str]) -> httpx.Response:
    """Deliver raw bytes to the in-process webhook endpoint without JSON re-encoding."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        return await client.post(
            "/api/v1/webhooks/generation/fal",
            content=body,
            headers=dict(headers),
        )


@pytest.mark.unit
def test_fal_webhook_route_awaits_an_idempotent_sink_then_returns_204() -> None:
    """Acknowledgement must follow sink completion while duplicate delivery dispatches once."""
    private_key = Ed25519PrivateKey.generate()
    sink = _IdempotentAwaitedSink()
    app = create_app(
        Settings(environment=Environment.TEST),
        generation_webhook_verifier=_verifier(private_key),
        generation_webhook_sink=sink,
        generation_webhook_clock=lambda: NOW,
    )
    headers = _signed_headers(private_key)

    first = asyncio.run(_post_webhook(app, body=BODY, headers=headers))
    second = asyncio.run(_post_webhook(app, body=BODY, headers=headers))

    assert first.status_code == second.status_code == 204
    assert first.content == second.content == b""
    assert sink.await_completed is True
    assert len(sink.dispatches) == 1
    assert sink.dispatches[0].payload_digest == hashlib.sha256(BODY).hexdigest()


@pytest.mark.unit
def test_fal_webhook_route_rejects_invalid_signatures_before_the_sink() -> None:
    """A failed verifier must never hand untrusted provider data to the ingestion boundary."""
    private_key = Ed25519PrivateKey.generate()
    sink = _IdempotentAwaitedSink()
    app = create_app(
        Settings(environment=Environment.TEST),
        generation_webhook_verifier=_verifier(private_key),
        generation_webhook_sink=sink,
        generation_webhook_clock=lambda: NOW,
    )
    headers = _signed_headers(private_key)
    headers["X-Fal-Webhook-Signature"] = "00" * 64

    response = asyncio.run(_post_webhook(app, body=BODY, headers=headers))

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "GENERATION_WEBHOOK_INVALID"
    assert sink.dispatches == []


@pytest.mark.unit
def test_fal_webhook_rejection_carries_its_own_public_message() -> None:
    """An unregistered code would answer with another failure's message, hiding the cause."""
    private_key = Ed25519PrivateKey.generate()
    app = create_app(
        Settings(environment=Environment.TEST),
        generation_webhook_verifier=_verifier(private_key),
        generation_webhook_sink=_IdempotentAwaitedSink(),
        generation_webhook_clock=lambda: NOW,
    )
    headers = _signed_headers(private_key)
    headers["X-Fal-Webhook-Signature"] = "00" * 64

    response = asyncio.run(_post_webhook(app, body=BODY, headers=headers))

    assert response.json()["error"]["message"] == "This delivery could not be verified."
