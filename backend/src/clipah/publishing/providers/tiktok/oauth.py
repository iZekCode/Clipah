"""Least-privilege Login Kit scope policy and rotating TikTok credential refresh."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
from pydantic import SecretStr

from clipah.publishing.providers.tiktok.transfers import (
    TikTokPermanentError,
    TikTokUnavailableError,
    normalize_tiktok_response,
)

TIKTOK_BASIC_SCOPE = "user.info.basic"
TIKTOK_UPLOAD_SCOPE = "video.upload"
TIKTOK_PUBLISH_SCOPE = "video.publish"


class TikTokScopeMissingError(Exception):
    """The Login Kit grant lacks permission for the requested TikTok operation."""


def require_tiktok_publish_scopes(scopes: frozenset[str], *, direct_post: bool) -> None:
    """Reject a grant missing draft upload or explicitly requested Direct Post access."""
    required = {TIKTOK_BASIC_SCOPE, TIKTOK_UPLOAD_SCOPE}
    if direct_post:
        required.add(TIKTOK_PUBLISH_SCOPE)
    if not required.issubset(scopes):
        raise TikTokScopeMissingError("required TikTok publishing scope is unavailable")


@dataclass(frozen=True, slots=True)
class RotatedTikTokGrant:
    """One rotated Login Kit credential pair and the windows TikTok granted it."""

    access_token: SecretStr = field(repr=False)
    refresh_token: SecretStr = field(repr=False)
    access_token_expires_at: datetime
    refresh_token_expires_at: datetime
    granted_scopes: frozenset[str]
    open_id: str

    def __str__(self) -> str:
        """Describe credential metadata without exposing reusable material."""
        return f"RotatedTikTokGrant(open_id={self.open_id!r})"


class TikTokOAuthClient:
    """Official HTTP boundary for Login Kit credential maintenance."""

    def __init__(
        self,
        *,
        client: httpx.Client,
        api_origin: str,
        client_key: str,
        client_secret: SecretStr,
    ) -> None:
        """Bind an injected transport to one exact registered TikTok application."""
        self._client = client
        self._origin = api_origin.rstrip("/")
        self._client_key = client_key
        self._client_secret = client_secret

    def refresh(self, *, refresh_token: SecretStr, now: datetime) -> RotatedTikTokGrant:
        """Exchange one refresh token and require TikTok's documented rotation."""
        try:
            response = self._client.post(
                f"{self._origin}/v2/oauth/token/",
                data={
                    "client_key": self._client_key,
                    "client_secret": self._client_secret.get_secret_value(),
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token.get_secret_value(),
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.TimeoutException as error:
            raise TikTokUnavailableError("TikTok publishing is temporarily unavailable") from error
        normalize_tiktok_response(response)
        payload = _flat_payload(response)
        rotated = _require_text(payload, "refresh_token")
        if rotated == refresh_token.get_secret_value():
            raise TikTokPermanentError("TikTok did not rotate the refresh token")
        scope = payload.get("scope")
        if not isinstance(scope, str):
            raise TikTokPermanentError("TikTok token response is malformed")
        return RotatedTikTokGrant(
            access_token=SecretStr(_require_text(payload, "access_token")),
            refresh_token=SecretStr(rotated),
            access_token_expires_at=now
            + timedelta(seconds=_require_positive(payload, "expires_in")),
            refresh_token_expires_at=now
            + timedelta(seconds=_require_positive(payload, "refresh_expires_in")),
            granted_scopes=frozenset(part for part in scope.split(",") if part),
            open_id=_require_text(payload, "open_id"),
        )


def _flat_payload(response: httpx.Response) -> dict[str, object]:
    """Read the token endpoint's flat body, which carries no data envelope."""
    try:
        payload = response.json()
    except ValueError as error:
        raise TikTokPermanentError("TikTok token response is malformed") from error
    if not isinstance(payload, dict):
        raise TikTokPermanentError("TikTok token response is malformed")
    return payload


def _require_text(payload: dict[str, object], key: str) -> str:
    """Read one non-empty bounded provider string or refuse the whole response."""
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value) > 2_048:
        raise TikTokPermanentError("TikTok token response is malformed")
    return value


def _require_positive(payload: dict[str, object], key: str) -> int:
    """Read one positive provider lifetime or refuse the whole response."""
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise TikTokPermanentError("TikTok token response is malformed")
    return value
