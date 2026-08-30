"""Google OpenID Connect Authorization Code flow with PKCE S256."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx
from authlib.integrations.httpx_client import OAuth2Client
from authlib.jose import JsonWebKey, JsonWebToken

from clipah.auth.models import (
    AuthorizationError,
    AuthorizationRedirect,
    OidcProfile,
    PendingAuthorization,
)

GOOGLE_ISSUER = "https://accounts.google.com"
GOOGLE_DISCOVERY_URL = f"{GOOGLE_ISSUER}/.well-known/openid-configuration"
ACCEPTED_GOOGLE_ISSUERS = frozenset({GOOGLE_ISSUER, "accounts.google.com"})
REQUESTED_SCOPES = ("openid", "email", "profile")
AUTHORIZATION_LIFETIME = timedelta(minutes=15)
CLOCK_SKEW = timedelta(minutes=2)
# Google signs ID tokens with RS256; accepting any other algorithm invites key confusion.
ACCEPTED_ID_TOKEN_ALGORITHMS = ["RS256"]


@dataclass(frozen=True, slots=True)
class IdTokenClaims:
    """The verified ID token claims an adapter may hand back to the flow."""

    issuer: str
    subject: str
    audience: str
    nonce: str
    email: str
    email_verified: bool
    name: str
    picture: str | None
    issued_at: datetime
    expires_at: datetime


class OidcProvider(Protocol):
    """The provider boundary the login ceremony depends on."""

    def authorization_endpoint(self) -> str:
        """Return the provider endpoint a browser should be redirected to."""
        ...

    def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> IdTokenClaims: ...


class AuthlibGoogleProvider:
    """Exchange authorization codes with Google and verify the returned ID token."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        discovery_url: str = GOOGLE_DISCOVERY_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        """Keep provider credentials and HTTP wiring private to this adapter."""
        self._client_id = client_id
        self._client_secret = client_secret
        self._discovery_url = discovery_url
        self._transport = transport
        self._timeout = timeout
        self._metadata: dict[str, Any] | None = None

    def authorization_endpoint(self) -> str:
        """Return Google's advertised authorization endpoint."""
        return str(self._discover()["authorization_endpoint"])

    def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> IdTokenClaims:
        """Redeem one authorization code and return only provider-neutral claims."""
        metadata = self._discover()
        client = OAuth2Client(
            client_id=self._client_id,
            client_secret=self._client_secret,
            transport=self._transport,
            timeout=self._timeout,
        )
        try:
            with client:
                token = client.fetch_token(
                    str(metadata["token_endpoint"]),
                    grant_type="authorization_code",
                    code=code,
                    code_verifier=code_verifier,
                    redirect_uri=redirect_uri,
                )
        except Exception as error:
            # Provider transport and protocol detail must never reach the caller.
            raise AuthorizationError("authorization code exchange failed") from error

        id_token = token.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise AuthorizationError("provider returned no ID token")
        return self._verify_id_token(id_token, jwks_uri=str(metadata["jwks_uri"]))

    def _verify_id_token(self, id_token: str, *, jwks_uri: str) -> IdTokenClaims:
        """Verify the ID token signature before any claim is trusted."""
        key_set = JsonWebKey.import_key_set(self._get_json(jwks_uri))
        try:
            claims = JsonWebToken(ACCEPTED_ID_TOKEN_ALGORITHMS).decode(id_token, key_set)
        except Exception as error:
            # Any verification failure is equally fatal and equally unexplained.
            raise AuthorizationError("ID token signature is not trusted") from error
        return _claims_from_payload(dict(claims))

    def _discover(self) -> dict[str, Any]:
        """Read and memoize the provider's OpenID configuration."""
        if self._metadata is None:
            self._metadata = self._get_json(self._discovery_url)
        return self._metadata

    def _get_json(self, url: str) -> dict[str, Any]:
        """Fetch one provider JSON document through the configured transport."""
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
                response = client.get(url)
                response.raise_for_status()
                document: dict[str, Any] = response.json()
        except Exception as error:
            # Provider transport and protocol detail must never reach the caller.
            raise AuthorizationError("provider metadata is unavailable") from error
        return document


def _claims_from_payload(payload: dict[str, Any]) -> IdTokenClaims:
    """Convert a verified JWT payload into the adapter's neutral claim set."""
    audience = payload.get("aud")
    if isinstance(audience, list):
        audience = audience[0] if len(audience) == 1 else ""
    return IdTokenClaims(
        issuer=str(payload.get("iss", "")),
        subject=str(payload.get("sub", "")),
        audience=str(audience or ""),
        nonce=str(payload.get("nonce", "")),
        email=str(payload.get("email", "")),
        email_verified=bool(payload.get("email_verified", False)),
        name=str(payload.get("name", "")),
        picture=payload.get("picture"),
        issued_at=_timestamp(payload.get("iat")),
        expires_at=_timestamp(payload.get("exp")),
    )


def _timestamp(value: Any) -> datetime:
    """Read a numeric JWT timestamp as an aware UTC instant."""
    if not isinstance(value, int | float):
        raise AuthorizationError("ID token is missing a required timestamp")
    return datetime.fromtimestamp(float(value), tz=UTC)


class GoogleOidcFlow:
    """Own the state, nonce, and PKCE bindings of one Google login ceremony."""

    def __init__(
        self,
        *,
        provider: OidcProvider,
        client_id: str,
        redirect_uri: str,
        accepted_issuers: frozenset[str] = ACCEPTED_GOOGLE_ISSUERS,
        authorization_lifetime: timedelta = AUTHORIZATION_LIFETIME,
        clock_skew: timedelta = CLOCK_SKEW,
    ) -> None:
        """Bind the ceremony to one client registration and one redirect URI."""
        self._provider = provider
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self._accepted_issuers = accepted_issuers
        self._authorization_lifetime = authorization_lifetime
        self._clock_skew = clock_skew

    def start(self, *, now: datetime) -> AuthorizationRedirect:
        """Create single-use bindings and the provider redirect that carries them."""
        pending = PendingAuthorization(
            state=secrets.token_urlsafe(32),
            nonce=secrets.token_urlsafe(32),
            code_verifier=secrets.token_urlsafe(64),
            redirect_uri=self._redirect_uri,
            created_at=now,
        )
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._client_id,
                "redirect_uri": pending.redirect_uri,
                "scope": " ".join(REQUESTED_SCOPES),
                "state": pending.state,
                "nonce": pending.nonce,
                "code_challenge": _s256_challenge(pending.code_verifier),
                "code_challenge_method": "S256",
            }
        )
        endpoint = self._provider.authorization_endpoint()
        separator = "&" if "?" in endpoint else "?"
        return AuthorizationRedirect(
            authorization_url=f"{endpoint}{separator}{query}", pending=pending
        )

    def complete(
        self, *, code: str, state: str, pending: PendingAuthorization, now: datetime
    ) -> OidcProfile:
        """Redeem the callback only when every binding still holds."""
        if not hmac.compare_digest(state, pending.state):
            raise AuthorizationError("authorization state does not match this ceremony")
        age = now - pending.created_at
        if age < -self._clock_skew or age > self._authorization_lifetime:
            raise AuthorizationError("authorization request is no longer redeemable")

        claims = self._provider.exchange_code(
            code=code, code_verifier=pending.code_verifier, redirect_uri=pending.redirect_uri
        )
        self._assert_trustworthy(claims, pending=pending, now=now)
        return OidcProfile(
            issuer=claims.issuer,
            subject=claims.subject,
            email=claims.email,
            email_verified=claims.email_verified,
            display_name=claims.name or claims.email,
            avatar_url=claims.picture,
        )

    def _assert_trustworthy(
        self, claims: IdTokenClaims, *, pending: PendingAuthorization, now: datetime
    ) -> None:
        """Reject any claim set that could belong to another client, login, or time."""
        if claims.issuer not in self._accepted_issuers:
            raise AuthorizationError("ID token issuer is not accepted")
        if not hmac.compare_digest(claims.audience, self._client_id):
            raise AuthorizationError("ID token audience is not this client")
        if not hmac.compare_digest(claims.nonce, pending.nonce):
            raise AuthorizationError("ID token nonce does not match this ceremony")
        if not claims.subject:
            raise AuthorizationError("ID token carries no subject")
        if not claims.email:
            raise AuthorizationError("ID token carries no email address")
        if claims.expires_at <= now:
            raise AuthorizationError("ID token has expired")
        if claims.issued_at > now + self._clock_skew:
            raise AuthorizationError("ID token was issued in the future")


def _s256_challenge(code_verifier: str) -> str:
    """Derive the PKCE S256 challenge for one verifier."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
