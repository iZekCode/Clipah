"""Single-use OAuth Authorization Code ceremonies for Social Accounts."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from urllib.parse import urlencode
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken

from clipah.social_accounts.models import (
    OAuthGrantMaterial,
    OAuthTokenResult,
    PublishingCapabilities,
    RefreshedGrant,
    SocialAccountIdentity,
    SocialProvider,
)

AUTHORIZATION_LIFETIME = timedelta(minutes=15)
CEREMONY_KEY_CONTEXT = "clipah-social-oauth-ceremony-v1:"

YOUTUBE_SCOPES = frozenset(
    {
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/youtube.upload",
    }
)
INSTAGRAM_SCOPES = frozenset({"instagram_business_basic", "instagram_business_content_publish"})
TIKTOK_SCOPES = frozenset({"user.info.basic", "video.upload"})


class SocialAuthorizationError(Exception):
    """Base refusal for an untrusted Social Account authorization ceremony."""


class AuthorizationStateError(SocialAuthorizationError):
    """The callback did not carry the exact state created for this ceremony."""


class AuthorizationExpiredError(SocialAuthorizationError):
    """The callback arrived outside the ceremony's short validity window."""


class AuthorizationBindingError(SocialAuthorizationError):
    """The callback provider or redirect URI differs from the original request."""


class SocialScopeMissingError(SocialAuthorizationError):
    """The provider result omitted permission required for the requested connection."""


class SocialProviderGrantRejectedError(Exception):
    """The provider reports that this reusable authorization is no longer valid."""


class SocialProviderUnavailableError(Exception):
    """The provider cannot complete a safe OAuth operation right now."""


@dataclass(frozen=True, slots=True)
class ProviderPolicy:
    """Immutable OAuth registration and least-privilege policy for one provider."""

    provider: SocialProvider
    client_id: str
    redirect_uri: str
    api_version: str
    authorization_endpoint: str
    minimum_scopes: frozenset[str]


@dataclass(frozen=True, slots=True)
class PendingSocialAuthorization:
    """Secret browser bindings for one Social Account OAuth callback."""

    provider: SocialProvider
    state: str
    code_verifier: str
    redirect_uri: str
    requested_scopes: frozenset[str]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SocialAuthorizationRedirect:
    """Provider URL plus the pending bindings the browser must return."""

    authorization_url: str
    pending: PendingSocialAuthorization
    code_challenge: str
    code_challenge_method: str


@dataclass(frozen=True, slots=True)
class SocialOAuthCookie:
    """Authenticated browser-held identity and secret bindings for one ceremony."""

    ceremony_id: UUID
    workspace_id: UUID
    actor_user_id: UUID
    pending: PendingSocialAuthorization


class SocialOAuthProvider(Protocol):
    """External OAuth/account operations required by the connection service."""

    def authorization_endpoint(self) -> str:
        """Return the provider's trusted authorization endpoint."""

    def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> OAuthTokenResult:
        """Redeem an authorization code without leaking provider payload types."""

    def account_identity(self, *, grant: OAuthGrantMaterial) -> SocialAccountIdentity:
        """Return authoritative identity for the Social Account destination."""

    def capabilities(self, *, grant: OAuthGrantMaterial) -> PublishingCapabilities:
        """Return current provider choices safe to persist as a snapshot."""

    def refresh(self, *, grant: OAuthGrantMaterial) -> RefreshedGrant:
        """Refresh credentials and normalize any rotated token material."""

    def revoke(self, *, grant: OAuthGrantMaterial) -> None:
        """Attempt to revoke provider authorization."""


def provider_policy(
    provider: SocialProvider, *, client_id: str, redirect_uri: str, api_version: str
) -> ProviderPolicy:
    """Build the fixed initial authorization policy for one provider."""
    endpoints = {
        SocialProvider.YOUTUBE: "https://accounts.google.com/o/oauth2/v2/auth",
        SocialProvider.INSTAGRAM: "https://www.instagram.com/oauth/authorize",
        SocialProvider.TIKTOK: "https://www.tiktok.com/v2/auth/authorize/",
    }
    scopes = {
        SocialProvider.YOUTUBE: YOUTUBE_SCOPES,
        SocialProvider.INSTAGRAM: INSTAGRAM_SCOPES,
        SocialProvider.TIKTOK: TIKTOK_SCOPES,
    }
    return ProviderPolicy(
        provider=provider,
        client_id=client_id,
        redirect_uri=redirect_uri,
        api_version=api_version,
        authorization_endpoint=endpoints[provider],
        minimum_scopes=scopes[provider],
    )


def start_authorization(
    *,
    policy: ProviderPolicy,
    now: datetime,
    random_bytes: Callable[[int], bytes] = secrets.token_bytes,
) -> SocialAuthorizationRedirect:
    """Create state and PKCE bindings plus the exact provider redirect."""
    state = _urlsafe(random_bytes(32))
    verifier = _urlsafe(random_bytes(64))
    challenge = _urlsafe(hashlib.sha256(verifier.encode("ascii")).digest())
    pending = PendingSocialAuthorization(
        provider=policy.provider,
        state=state,
        code_verifier=verifier,
        redirect_uri=policy.redirect_uri,
        requested_scopes=policy.minimum_scopes,
        created_at=now,
    )
    query = urlencode(
        {
            "response_type": "code",
            "client_id": policy.client_id,
            "redirect_uri": policy.redirect_uri,
            "scope": " ".join(sorted(policy.minimum_scopes)),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    separator = "&" if "?" in policy.authorization_endpoint else "?"
    return SocialAuthorizationRedirect(
        authorization_url=f"{policy.authorization_endpoint}{separator}{query}",
        pending=pending,
        code_challenge=challenge,
        code_challenge_method="S256",
    )


def validate_authorization(
    *,
    pending: PendingSocialAuthorization,
    provider: SocialProvider,
    state: str,
    redirect_uri: str,
    now: datetime,
) -> None:
    """Validate callback bindings without returning or describing secret values."""
    if provider is not pending.provider or redirect_uri != pending.redirect_uri:
        raise AuthorizationBindingError("social authorization binding is invalid")
    if not hmac.compare_digest(state, pending.state):
        raise AuthorizationStateError("social authorization state is invalid")
    age = now - pending.created_at
    if age < timedelta(0) or age > AUTHORIZATION_LIFETIME:
        raise AuthorizationExpiredError("social authorization is no longer redeemable")


def require_minimum_scopes(*, granted_scopes: frozenset[str], policy: ProviderPolicy) -> None:
    """Refuse a partial provider grant that cannot fulfill the explicit connection."""
    if not policy.minimum_scopes.issubset(granted_scopes):
        raise SocialScopeMissingError("required social authorization scope was declined")


def seal_social_oauth_cookie(cookie: SocialOAuthCookie, *, secret: str) -> str:
    """Encrypt one Social Account ceremony so the browser cannot read or alter it."""
    pending = cookie.pending
    payload = json.dumps(
        {
            "ceremony_id": str(cookie.ceremony_id),
            "workspace_id": str(cookie.workspace_id),
            "actor_user_id": str(cookie.actor_user_id),
            "provider": pending.provider.value,
            "state": pending.state,
            "code_verifier": pending.code_verifier,
            "redirect_uri": pending.redirect_uri,
            "requested_scopes": sorted(pending.requested_scopes),
            "created_at": pending.created_at.isoformat(),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return _ceremony_cipher(secret).encrypt(payload.encode("utf-8")).decode("ascii")


def open_social_oauth_cookie(sealed: str, *, secret: str) -> SocialOAuthCookie:
    """Recover one intact ceremony cookie without disclosing malformed input."""
    try:
        payload = json.loads(_ceremony_cipher(secret).decrypt(sealed.encode("ascii")))
        pending = PendingSocialAuthorization(
            provider=SocialProvider(str(payload["provider"])),
            state=str(payload["state"]),
            code_verifier=str(payload["code_verifier"]),
            redirect_uri=str(payload["redirect_uri"]),
            requested_scopes=frozenset(str(value) for value in payload["requested_scopes"]),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
        )
        return SocialOAuthCookie(
            ceremony_id=UUID(str(payload["ceremony_id"])),
            workspace_id=UUID(str(payload["workspace_id"])),
            actor_user_id=UUID(str(payload["actor_user_id"])),
            pending=pending,
        )
    except (InvalidToken, ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise SocialAuthorizationError("social authorization cookie is invalid") from None


def _urlsafe(value: bytes) -> str:
    """Encode opaque bytes without padding for OAuth query parameters."""
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _ceremony_cipher(secret: str) -> Fernet:
    """Derive a key used only for Social Account ceremony cookies."""
    digest = hashlib.sha256(f"{CEREMONY_KEY_CONTEXT}{secret}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))
