"""Authorization Code + PKCE contracts for Google Login Identities."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from authlib.jose import JsonWebKey, jwt

from clipah.auth.google_oidc import (
    GOOGLE_ISSUER,
    AuthlibGoogleProvider,
    GoogleOidcFlow,
    IdTokenClaims,
)
from clipah.auth.models import AuthorizationError, OidcProfile

CLIENT_ID = "clipah-test-client.apps.googleusercontent.com"
CLIENT_SECRET = "clipah-test-client-secret"
REDIRECT_URI = "https://app.example.com/api/v1/auth/google/callback"
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


class RecordingProvider:
    """A deterministic stand-in for Google's token endpoint."""

    def __init__(self, claims: IdTokenClaims) -> None:
        self.claims = claims
        self.exchanges: list[dict[str, str]] = []

    def authorization_endpoint(self) -> str:
        return "https://accounts.google.com/o/oauth2/v2/auth"

    def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> IdTokenClaims:
        self.exchanges.append(
            {"code": code, "code_verifier": code_verifier, "redirect_uri": redirect_uri}
        )
        return self.claims


def claims(**overrides: object) -> IdTokenClaims:
    """Build the claim set a correct Google ID token would carry."""
    defaults: dict[str, object] = {
        "issuer": GOOGLE_ISSUER,
        "subject": "108422224444555566667",
        "audience": CLIENT_ID,
        "nonce": "",
        "email": "creator@example.com",
        "email_verified": True,
        "name": "Creator Example",
        "picture": "https://lh3.googleusercontent.com/a/avatar",
        "issued_at": NOW - timedelta(seconds=5),
        "expires_at": NOW + timedelta(minutes=30),
    }
    return IdTokenClaims(**{**defaults, **overrides})  # type: ignore[arg-type]


def flow(provider: RecordingProvider) -> GoogleOidcFlow:
    """Build the flow under test with the injected fake provider."""
    return GoogleOidcFlow(provider=provider, client_id=CLIENT_ID, redirect_uri=REDIRECT_URI)


@pytest.mark.unit
def test_start_binds_state_nonce_and_an_s256_pkce_challenge() -> None:
    """A downgraded challenge method or a short state would break login integrity."""
    provider = RecordingProvider(claims())

    redirect = flow(provider).start(now=NOW)

    query = parse_qs(urlparse(redirect.authorization_url).query)
    expected_challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(redirect.pending.code_verifier.encode("ascii")).digest()
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    assert query["response_type"] == ["code"]
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == [REDIRECT_URI]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [expected_challenge]
    assert query["state"] == [redirect.pending.state]
    assert query["nonce"] == [redirect.pending.nonce]
    assert set(query["scope"][0].split()) == {"openid", "email", "profile"}
    assert len(redirect.pending.state) >= 32
    assert len(redirect.pending.nonce) >= 32
    assert 43 <= len(redirect.pending.code_verifier) <= 128


@pytest.mark.unit
def test_start_never_reuses_state_nonce_or_verifier() -> None:
    """Reusing any of the three bindings would let one login replay another."""
    provider = RecordingProvider(claims())
    ceremony = flow(provider)

    first = ceremony.start(now=NOW).pending
    second = ceremony.start(now=NOW).pending

    assert first.state != second.state
    assert first.nonce != second.nonce
    assert first.code_verifier != second.code_verifier


@pytest.mark.unit
def test_complete_returns_the_provider_profile_and_sends_the_verifier() -> None:
    """The verifier must travel to the token endpoint, not the challenge."""
    provider = RecordingProvider(claims())
    ceremony = flow(provider)
    redirect = ceremony.start(now=NOW)
    provider.claims = claims(nonce=redirect.pending.nonce)

    profile = ceremony.complete(
        code="auth-code", state=redirect.pending.state, pending=redirect.pending, now=NOW
    )

    assert profile == OidcProfile(
        issuer=GOOGLE_ISSUER,
        subject="108422224444555566667",
        email="creator@example.com",
        email_verified=True,
        display_name="Creator Example",
        avatar_url="https://lh3.googleusercontent.com/a/avatar",
    )
    assert provider.exchanges == [
        {
            "code": "auth-code",
            "code_verifier": redirect.pending.code_verifier,
            "redirect_uri": REDIRECT_URI,
        }
    ]


@pytest.mark.unit
def test_complete_rejects_a_state_from_a_different_ceremony() -> None:
    """A callback that does not match the stored state is a CSRF attempt."""
    provider = RecordingProvider(claims())
    ceremony = flow(provider)
    pending = ceremony.start(now=NOW).pending
    attacker_state = ceremony.start(now=NOW).pending.state

    with pytest.raises(AuthorizationError):
        ceremony.complete(code="auth-code", state=attacker_state, pending=pending, now=NOW)

    assert provider.exchanges == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides",
    (
        pytest.param({"nonce": "someone-elses-nonce"}, id="replayed_nonce"),
        pytest.param(
            {"audience": "another-client.apps.googleusercontent.com"}, id="wrong_audience"
        ),
        pytest.param({"issuer": "https://accounts.evil.example"}, id="wrong_issuer"),
        pytest.param({"expires_at": NOW - timedelta(seconds=1)}, id="expired_token"),
        pytest.param({"issued_at": NOW + timedelta(minutes=10)}, id="future_issued_at"),
        pytest.param({"subject": ""}, id="empty_subject"),
    ),
)
def test_complete_rejects_untrustworthy_id_token_claims(overrides: dict[str, object]) -> None:
    """Every one of these claims alone is enough to forge or replay a login."""
    provider = RecordingProvider(claims())
    ceremony = flow(provider)
    redirect = ceremony.start(now=NOW)
    provider.claims = claims(**{"nonce": redirect.pending.nonce, **overrides})

    with pytest.raises(AuthorizationError):
        ceremony.complete(
            code="auth-code", state=redirect.pending.state, pending=redirect.pending, now=NOW
        )


@pytest.mark.unit
def test_complete_rejects_an_authorization_older_than_its_lifetime() -> None:
    """A pending ceremony must not stay redeemable indefinitely."""
    provider = RecordingProvider(claims())
    ceremony = flow(provider)
    redirect = ceremony.start(now=NOW)
    provider.claims = claims(nonce=redirect.pending.nonce)

    with pytest.raises(AuthorizationError):
        ceremony.complete(
            code="auth-code",
            state=redirect.pending.state,
            pending=redirect.pending,
            now=NOW + timedelta(minutes=16),
        )


def signed_id_token(key: JsonWebKey, *, nonce: str, **overrides: object) -> str:
    """Produce a real RS256 ID token so signature validation is genuinely exercised."""
    payload: dict[str, object] = {
        "iss": GOOGLE_ISSUER,
        "sub": "108422224444555566667",
        "aud": CLIENT_ID,
        "nonce": nonce,
        "email": "creator@example.com",
        "email_verified": True,
        "name": "Creator Example",
        "picture": "https://lh3.googleusercontent.com/a/avatar",
        "iat": int((NOW - timedelta(seconds=5)).timestamp()),
        "exp": int((NOW + timedelta(minutes=30)).timestamp()),
    }
    payload.update(overrides)
    token = jwt.encode({"alg": "RS256"}, payload, key)
    return token.decode("ascii")


def google_transport(published_key: JsonWebKey, id_token: str) -> httpx.MockTransport:
    """Serve Google's discovery, JWKS, and token endpoints without network access."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": GOOGLE_ISSUER,
                    "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
                    "token_endpoint": "https://oauth2.googleapis.com/token",
                    "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
                },
            )
        if request.url.path == "/oauth2/v3/certs":
            return httpx.Response(200, json={"keys": [published_key.as_dict(is_private=False)]})
        if request.url.path == "/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "provider-access-token",
                    "token_type": "Bearer",
                    "id_token": id_token,
                },
            )
        return httpx.Response(404, json={"error": "unexpected_endpoint"})

    return httpx.MockTransport(handle)


@pytest.mark.unit
def test_authlib_provider_accepts_only_tokens_signed_by_the_published_google_key() -> None:
    """An unsigned or foreign-key ID token must never become a Login Identity."""
    signing_key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
    foreign_key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
    nonce = "nonce-under-test"

    def provider_for(id_token: str, published: JsonWebKey) -> AuthlibGoogleProvider:
        return AuthlibGoogleProvider(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            transport=google_transport(published, id_token),
        )

    trusted = provider_for(signed_id_token(signing_key, nonce=nonce), signing_key)
    exchanged = trusted.exchange_code(
        code="auth-code", code_verifier="v" * 43, redirect_uri=REDIRECT_URI
    )
    assert exchanged.subject == "108422224444555566667"
    assert exchanged.nonce == nonce

    forged = provider_for(signed_id_token(foreign_key, nonce=nonce), signing_key)
    with pytest.raises(AuthorizationError):
        forged.exchange_code(code="auth-code", code_verifier="v" * 43, redirect_uri=REDIRECT_URI)


@pytest.mark.unit
def test_authlib_provider_never_leaks_provider_tokens_in_its_result() -> None:
    """Access tokens and raw ID tokens must not escape the adapter boundary."""
    signing_key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
    id_token = signed_id_token(signing_key, nonce="nonce-under-test")
    provider = AuthlibGoogleProvider(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        transport=google_transport(signing_key, id_token),
    )

    exchanged = provider.exchange_code(
        code="auth-code", code_verifier="v" * 43, redirect_uri=REDIRECT_URI
    )

    rendered = json.dumps(asdict(exchanged), default=str)
    assert "provider-access-token" not in rendered
    assert id_token not in rendered


@pytest.mark.unit
def test_complete_rejects_an_id_token_without_an_email_address() -> None:
    """A Login Identity without a provider email cannot own a Workspace contact."""
    provider = RecordingProvider(claims())
    ceremony = flow(provider)
    redirect = ceremony.start(now=NOW)
    provider.claims = claims(nonce=redirect.pending.nonce, email="")

    with pytest.raises(AuthorizationError):
        ceremony.complete(
            code="auth-code", state=redirect.pending.state, pending=redirect.pending, now=NOW
        )


@pytest.mark.unit
def test_authlib_provider_reports_googles_advertised_authorization_endpoint() -> None:
    """The endpoint must come from discovery rather than a hardcoded guess."""
    signing_key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
    provider = AuthlibGoogleProvider(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        transport=google_transport(signing_key, signed_id_token(signing_key, nonce="n")),
    )

    assert provider.authorization_endpoint() == "https://accounts.google.com/o/oauth2/v2/auth"


@pytest.mark.unit
@pytest.mark.parametrize(
    "failing_path",
    (
        pytest.param("/.well-known/openid-configuration", id="discovery_unavailable"),
        pytest.param("/oauth2/v3/certs", id="jwks_unavailable"),
        pytest.param("/token", id="token_endpoint_rejects"),
    ),
)
def test_authlib_provider_fails_closed_when_google_is_unusable(failing_path: str) -> None:
    """A provider outage must surface as a login failure, never as an unverified identity."""
    signing_key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
    healthy = google_transport(signing_key, signed_id_token(signing_key, nonce="n"))

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == failing_path:
            return httpx.Response(503, json={"error": "unavailable"})
        return healthy.handler(request)

    provider = AuthlibGoogleProvider(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, transport=httpx.MockTransport(handle)
    )

    with pytest.raises(AuthorizationError):
        provider.exchange_code(code="auth-code", code_verifier="v" * 43, redirect_uri=REDIRECT_URI)


@pytest.mark.unit
def test_authlib_provider_rejects_a_token_response_without_an_id_token() -> None:
    """An access token alone proves nothing about who logged in."""
    signing_key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
    healthy = google_transport(signing_key, signed_id_token(signing_key, nonce="n"))

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(
                200, json={"access_token": "provider-access-token", "token_type": "Bearer"}
            )
        return healthy.handler(request)

    provider = AuthlibGoogleProvider(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, transport=httpx.MockTransport(handle)
    )

    with pytest.raises(AuthorizationError):
        provider.exchange_code(code="auth-code", code_verifier="v" * 43, redirect_uri=REDIRECT_URI)


@pytest.mark.unit
def test_authlib_provider_rejects_an_id_token_without_expiry_or_a_shared_audience() -> None:
    """Missing lifetime claims and multi-client audiences are both unverifiable."""
    signing_key = JsonWebKey.generate_key("RSA", 2048, is_private=True)

    def provider_for(**overrides: object) -> AuthlibGoogleProvider:
        id_token = signed_id_token(signing_key, nonce="n", **overrides)
        return AuthlibGoogleProvider(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            transport=google_transport(signing_key, id_token),
        )

    shared_audience = provider_for(aud=[CLIENT_ID, "another-client"]).exchange_code(
        code="auth-code", code_verifier="v" * 43, redirect_uri=REDIRECT_URI
    )
    assert shared_audience.audience == ""

    with pytest.raises(AuthorizationError):
        provider_for(exp=None).exchange_code(
            code="auth-code", code_verifier="v" * 43, redirect_uri=REDIRECT_URI
        )
