"""Least-privilege Instagram Login policy and long-lived token maintenance."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

import httpx
from pydantic import SecretStr

from clipah.publishing.providers.instagram.containers import (
    InstagramPermanentError,
    InstagramUnavailableError,
    normalize_instagram_response,
)

INSTAGRAM_BASIC_SCOPE = "instagram_business_basic"
INSTAGRAM_PUBLISH_SCOPE = "instagram_business_content_publish"

LONG_LIVED_TOKEN_LIFETIME = timedelta(days=60)
LONG_LIVED_REFRESH_MINIMUM_AGE = timedelta(hours=24)
LONG_LIVED_REFRESH_LEAD = timedelta(days=7)


class InstagramScopeMissingError(Exception):
    """The OAuth Grant lacks permission for the requested Instagram operation."""


class InstagramAccountIneligibleError(Exception):
    """The destination is not an Instagram professional account that may publish."""


class InstagramAccountType(StrEnum):
    """Account kinds Instagram reports for one authorized Login identity."""

    BUSINESS = "BUSINESS"
    MEDIA_CREATOR = "MEDIA_CREATOR"


def require_instagram_publish_scopes(scopes: frozenset[str]) -> None:
    """Reject a partial grant that cannot read identity and publish content."""
    required = {INSTAGRAM_BASIC_SCOPE, INSTAGRAM_PUBLISH_SCOPE}
    if not required.issubset(scopes):
        raise InstagramScopeMissingError("required Instagram publishing scope is unavailable")


def require_professional_account(account_type: str | None) -> InstagramAccountType:
    """Accept only the exact professional account types Instagram permits to publish."""
    if account_type is None:
        raise InstagramAccountIneligibleError(
            "Instagram publishing requires a professional account"
        )
    try:
        return InstagramAccountType(account_type)
    except ValueError as error:
        raise InstagramAccountIneligibleError(
            "Instagram publishing requires a professional account"
        ) from error


@dataclass(frozen=True, slots=True)
class LongLivedToken:
    """The validity window of one long-lived Instagram Login credential."""

    issued_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        """Reject a window that cannot schedule one reproducible refresh."""
        if self.issued_at.utcoffset() is None or self.expires_at.utcoffset() is None:
            raise ValueError("long-lived token window must be timezone aware")
        if self.expires_at <= self.issued_at:
            raise ValueError("long-lived token must expire after it was issued")


@dataclass(frozen=True, slots=True)
class RefreshedLongLivedToken:
    """One rotated credential plus the window Instagram granted it."""

    access_token: SecretStr = field(repr=False)
    token: LongLivedToken

    def __str__(self) -> str:
        """Describe credential metadata without exposing reusable material."""
        return f"RefreshedLongLivedToken(expires_at={self.token.expires_at.isoformat()})"


def long_lived_refresh_due(token: LongLivedToken, *, now: datetime) -> bool:
    """Report whether Instagram would accept a refresh for this credential now."""
    if now >= token.expires_at:
        return False
    if now - token.issued_at < LONG_LIVED_REFRESH_MINIMUM_AGE:
        return False
    return token.expires_at - now <= LONG_LIVED_REFRESH_LEAD


class InstagramOAuthClient:
    """Official HTTP boundary for Instagram Login credential maintenance."""

    def __init__(self, *, client: httpx.Client, graph_origin: str, api_version: str) -> None:
        """Bind an injected transport to one exact Graph origin and API version."""
        self._client = client
        self._origin = graph_origin.rstrip("/")
        self._api_version = api_version

    def refresh_long_lived_token(
        self, *, access_token: SecretStr, now: datetime
    ) -> RefreshedLongLivedToken:
        """Rotate one long-lived credential without placing it in a request URL."""
        try:
            response = self._client.get(
                f"{self._origin}/{self._api_version}/refresh_access_token",
                params={"grant_type": "ig_refresh_token"},
                headers={"Authorization": f"Bearer {access_token.get_secret_value()}"},
            )
        except httpx.TimeoutException as error:
            raise InstagramUnavailableError(
                "Instagram publishing is temporarily unavailable"
            ) from error
        normalize_instagram_response(response)
        try:
            payload = response.json()
        except ValueError as error:
            raise InstagramPermanentError("Instagram token response is malformed") from error
        token = payload.get("access_token") if isinstance(payload, dict) else None
        expires_in = payload.get("expires_in") if isinstance(payload, dict) else None
        if (
            not isinstance(token, str)
            or not token
            or not isinstance(expires_in, int)
            or isinstance(expires_in, bool)
            or expires_in <= 0
        ):
            raise InstagramPermanentError("Instagram token response is malformed")
        return RefreshedLongLivedToken(
            access_token=SecretStr(token),
            token=LongLivedToken(
                issued_at=now,
                expires_at=now + timedelta(seconds=expires_in),
            ),
        )
