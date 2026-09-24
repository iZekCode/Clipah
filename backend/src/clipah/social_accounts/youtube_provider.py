"""Google's OAuth and the YouTube Data API, as one Social Account connection needs them.

This is the provider side of connecting a YouTube channel: redeeming the authorization
code Google returns, naming the channel the grant speaks for, refreshing and revoking the
grant. Publishing a video is a separate adapter; nothing here uploads anything.

Provider payloads never leave this module. Each answer is reduced to the few fields the
connection service stores, and every provider refusal becomes one of the service's own
errors, so neither a token nor Google's error text can reach a response or a log.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import httpx

from clipah.social_accounts.models import (
    OAuthGrantMaterial,
    OAuthTokenResult,
    PublishingCapabilities,
    RefreshedGrant,
    SocialAccountIdentity,
)
from clipah.social_accounts.oauth import (
    SocialAuthorizationError,
    SocialDestinationMissingError,
    SocialProviderGrantRejectedError,
    SocialProviderUnavailableError,
)

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
YOUTUBE_API_ORIGIN = "https://www.googleapis.com"
CAPABILITY_VERSION = "youtube-channel-v1"
REQUEST_TIMEOUT_SECONDS = 15.0


class YouTubeOAuthProvider:
    """Connect one YouTube channel through Google's Authorization Code flow with PKCE."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        audit_approved: bool,
        clock: Callable[[], datetime],
        client: httpx.Client | None = None,
        token_endpoint: str = GOOGLE_TOKEN_ENDPOINT,
        revoke_endpoint: str = GOOGLE_REVOKE_ENDPOINT,
        api_origin: str = YOUTUBE_API_ORIGIN,
    ) -> None:
        """Bind one OAuth client registration, the deployment's audit state, and a clock."""
        self._client_id = client_id
        self._client_secret = client_secret
        self._audit_approved = audit_approved
        self._clock = clock
        self._client = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
        self._token_endpoint = token_endpoint
        self._revoke_endpoint = revoke_endpoint
        self._api_origin = api_origin.rstrip("/")

    def authorization_endpoint(self) -> str:
        """Return Google's authorization endpoint, the only one a ceremony may send to."""
        return GOOGLE_AUTHORIZATION_ENDPOINT

    def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> OAuthTokenResult:
        """Redeem one authorization code for a grant that can be refreshed offline.

        A code Google refuses is the ceremony's failure, not an outage: it was already
        used, expired, or issued for another redirect, and asking again cannot help.
        """
        payload = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
            },
            refused=SocialAuthorizationError("YouTube authorization code was refused"),
        )
        material = self._material(payload, previous_refresh_token=None)
        if material.refresh_token is None:
            # Without one the connection dies with its first access token, an hour later.
            raise SocialAuthorizationError("YouTube authorization returned no refresh token")
        return OAuthTokenResult(material=material, granted_scopes=_scopes(payload))

    def account_identity(self, *, grant: OAuthGrantMaterial) -> SocialAccountIdentity:
        """Name the one channel this grant publishes to.

        A Google account may exist without a channel; connecting it would store a
        destination nothing can be published to, so that is refused here.
        """
        channel = self._channel(grant)
        snippet = channel.get("snippet")
        snippet = snippet if isinstance(snippet, dict) else {}
        title = snippet.get("title")
        return SocialAccountIdentity(
            external_account_id=str(channel["id"]),
            display_name=title.strip()[:200]
            if isinstance(title, str) and title.strip()
            else "YouTube channel",
            avatar_url=_thumbnail(snippet),
            account_type="channel",
        )

    def capabilities(self, *, grant: OAuthGrantMaterial) -> PublishingCapabilities:
        """Describe what this channel may be asked for from this deployment.

        YouTube decides visibility for an unaudited API project, not the channel: every
        upload it accepts from one is private. The snapshot says so, so the composer can.
        """
        del grant
        privacy = ["private", "unlisted", "public"] if self._audit_approved else ["private"]
        return PublishingCapabilities(
            version=CAPABILITY_VERSION,
            values={"privacy_options": privacy, "audit_approved": self._audit_approved},
        )

    def refresh(self, *, grant: OAuthGrantMaterial) -> RefreshedGrant:
        """Trade the refresh token for a fresh access token, keeping it when Google does.

        Google rarely rotates a refresh token; when the answer carries none, the one
        already held stays valid and must be kept, or the next refresh would have nothing.
        """
        if grant.refresh_token is None:
            raise SocialProviderGrantRejectedError("YouTube grant has no refresh token")
        payload = self._token_request(
            {"grant_type": "refresh_token", "refresh_token": grant.refresh_token},
            refused=SocialProviderGrantRejectedError("YouTube refused to refresh this grant"),
        )
        material = self._material(payload, previous_refresh_token=grant.refresh_token)
        return RefreshedGrant(material=material, granted_scopes=_scopes(payload))

    def revoke(self, *, grant: OAuthGrantMaterial) -> None:
        """Ask Google to forget this grant; a grant it already forgot is revoked too."""
        token = grant.refresh_token or grant.access_token
        try:
            response = self._client.post(
                self._revoke_endpoint,
                data={"token": token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as error:
            raise SocialProviderUnavailableError("YouTube revocation is unavailable") from error
        if response.status_code >= 500:
            raise SocialProviderUnavailableError("YouTube revocation is unavailable")

    def _token_request(self, form: dict[str, str], *, refused: Exception) -> dict[str, Any]:
        """Post one form to Google's token endpoint, authenticated as this client."""
        try:
            response = self._client.post(
                self._token_endpoint,
                data={**form, "client_id": self._client_id, "client_secret": self._client_secret},
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as error:
            raise SocialProviderUnavailableError("Google OAuth is unavailable") from error
        if response.status_code >= 500 or response.status_code == 429:
            raise SocialProviderUnavailableError("Google OAuth is unavailable")
        if response.status_code >= 400:
            raise refused
        payload = _json_object(response)
        if not isinstance(payload.get("access_token"), str) or not payload["access_token"]:
            raise SocialProviderUnavailableError("Google OAuth answered without a token")
        return payload

    def _material(
        self, payload: dict[str, Any], *, previous_refresh_token: str | None
    ) -> OAuthGrantMaterial:
        """Normalize one token answer, dating its expiry from this deployment's clock."""
        expires_in = payload.get("expires_in")
        expires_at = (
            self._clock() + timedelta(seconds=int(expires_in))
            if isinstance(expires_in, int | float) and expires_in > 0
            else None
        )
        refresh = payload.get("refresh_token")
        return OAuthGrantMaterial(
            access_token=str(payload["access_token"]),
            refresh_token=refresh
            if isinstance(refresh, str) and refresh
            else previous_refresh_token,
            token_type="Bearer",
            access_token_expires_at=expires_at,
        )

    def _channel(self, grant: OAuthGrantMaterial) -> dict[str, Any]:
        """Read the grant's own channel, the one ``mine=true`` names."""
        try:
            response = self._client.get(
                f"{self._api_origin}/youtube/v3/channels",
                params={"part": "id,snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {grant.access_token}"},
            )
        except httpx.HTTPError as error:
            raise SocialProviderUnavailableError("YouTube is unavailable") from error
        if response.status_code in {401, 403}:
            raise SocialAuthorizationError("YouTube refused to name this grant's channel")
        if response.status_code >= 400:
            raise SocialProviderUnavailableError("YouTube is unavailable")
        items = _json_object(response).get("items")
        channels = (
            [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
        )
        if not channels or not isinstance(channels[0].get("id"), str) or not channels[0]["id"]:
            raise SocialDestinationMissingError("this Google account has no YouTube channel")
        return channels[0]


def _scopes(payload: dict[str, Any]) -> frozenset[str]:
    """Read the space-separated scopes Google actually granted."""
    scope = payload.get("scope")
    return frozenset(scope.split()) if isinstance(scope, str) else frozenset()


def _thumbnail(snippet: dict[str, Any]) -> str | None:
    """Pick the channel's smallest HTTPS avatar, if Google gave one."""
    thumbnails = snippet.get("thumbnails")
    if not isinstance(thumbnails, dict):
        return None
    for size in ("default", "medium", "high"):
        entry = thumbnails.get(size)
        url = entry.get("url") if isinstance(entry, dict) else None
        if isinstance(url, str) and url.startswith("https://"):
            return url[:2048]
    return None


def _json_object(response: httpx.Response) -> dict[str, Any]:
    """Decode one JSON object, treating anything else as the provider being unwell."""
    try:
        payload = response.json()
    except ValueError as error:
        raise SocialProviderUnavailableError("Google answered with malformed JSON") from error
    if not isinstance(payload, dict):
        raise SocialProviderUnavailableError("Google answered with malformed JSON")
    return payload
