"""Strict Instagram Reels request values and the official publishing adapter."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from clipah.publishing.providers.base import PublicationMedia
from clipah.publishing.providers.instagram.containers import (
    ContainerCheckpoint,
    ContainerState,
    InstagramAccountMismatchError,
    InstagramAmbiguousPublishError,
    InstagramContainerStatus,
    InstagramMediaUnreachableError,
    InstagramPermanentError,
    InstagramUnavailableError,
    MediaPullUrl,
    PublishingAllowance,
    normalize_instagram_response,
    parse_container_status,
    parse_publishing_limit,
    safe_permalink,
)
from clipah.publishing.providers.instagram.oauth import require_professional_account
from clipah.social_accounts.models import SocialProvider

MEDIA_LOOKBACK_LIMIT = 25
REELS_PRODUCT_TYPE = "REELS"


class InstagramPublishRequest(BaseModel):
    """Frozen, validated Reels metadata limited to fields Instagram actually offers."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    caption: str = Field(default="", max_length=2_200)
    share_to_feed: bool = True
    cover_url: str | None = Field(default=None, max_length=2_048)
    thumb_offset_ms: int | None = Field(default=None, ge=0)
    audio_name: str | None = Field(default=None, min_length=1, max_length=255)
    location_id: str | None = Field(default=None, pattern=r"^[0-9]+$")
    collaborators: tuple[str, ...] = ()

    @field_validator("cover_url")
    @classmethod
    def require_https_cover(cls, value: str | None) -> str | None:
        """Keep the optional cover a capability Instagram can actually fetch."""
        if value is not None and not value.startswith("https://"):
            raise ValueError("cover_url must be an HTTPS URL")
        return value

    @field_validator("collaborators")
    @classmethod
    def require_distinct_bounded_handles(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Apply Instagram's collaborator bounds before a container is created."""
        if len(value) > 3:
            raise ValueError("Instagram accepts at most three collaborators")
        if any(not handle.strip() or len(handle) > 30 for handle in value):
            raise ValueError("collaborators must be nonblank Instagram handles")
        if len(set(value)) != len(value):
            raise ValueError("collaborators must be distinct")
        return value


@dataclass(frozen=True, slots=True)
class PublishedMedia:
    """The authoritative identity of one Reel Instagram has published."""

    media_id: str
    permalink: str | None


class InstagramPublisher:
    """Official HTTP adapter for one exact Instagram professional account."""

    provider = SocialProvider.INSTAGRAM

    def __init__(
        self,
        *,
        client: httpx.Client,
        graph_origin: str,
        api_version: str,
        account_id: str,
        media_url_provider: Callable[[], MediaPullUrl],
        clock: Callable[[], datetime],
    ) -> None:
        """Bind an injected transport, destination, capability source, and clock."""
        self._client = client
        self._origin = graph_origin.rstrip("/")
        self._api_version = api_version
        self._account_id = account_id
        self._media_url_provider = media_url_provider
        self._clock = clock

    def confirm_destination(self, *, access_token: SecretStr, expected_account_id: str) -> None:
        """Require the live grant to name exactly the frozen professional account."""
        payload = self._read(
            self._get("me", params={"fields": "user_id,account_type"}, access_token=access_token)
        )
        if payload.get("user_id") != expected_account_id:
            raise InstagramAccountMismatchError("Instagram account identity does not match")
        account_type = payload.get("account_type")
        require_professional_account(account_type if isinstance(account_type, str) else None)

    def publishing_allowance(self, *, access_token: SecretStr) -> PublishingAllowance:
        """Read the destination's live rolling daily content-publishing allowance."""
        response = self._get(
            f"{self._account_id}/content_publishing_limit",
            params={"fields": "config,quota_usage"},
            access_token=access_token,
        )
        return parse_publishing_limit(self._read(response))

    def verify_media_reachable(self, *, pull_url: MediaPullUrl, media: PublicationMedia) -> None:
        """Prove the frozen rendition is fetchable before Instagram is asked to pull it."""
        try:
            response = self._client.head(pull_url.url)
        except httpx.TimeoutException as error:
            raise InstagramUnavailableError(
                "Instagram publishing is temporarily unavailable"
            ) from error
        content_type = response.headers.get("Content-Type", "")
        content_length = response.headers.get("Content-Length")
        if (
            not 200 <= response.status_code < 300
            or not content_type.startswith("video/")
            or content_length != str(media.size_bytes)
        ):
            raise InstagramMediaUnreachableError("the approved rendition is not fetchable")

    def begin(
        self,
        *,
        request: InstagramPublishRequest,
        media: PublicationMedia,
        access_token: SecretStr,
    ) -> ContainerCheckpoint:
        """Create one Reels container from a freshly proven short-lived capability."""
        pull_url = self._media_url_provider()
        self.verify_media_reachable(pull_url=pull_url, media=media)
        try:
            response = self._client.post(
                f"{self._origin}/{self._api_version}/{self._account_id}/media",
                data=self.container_fields(request=request, pull_url=pull_url),
                headers=_authorization(access_token),
            )
        except httpx.TimeoutException:
            return ContainerCheckpoint(creation_ambiguous=True)
        normalize_instagram_response(response)
        payload = self._read(response)
        container_id = payload.get("id")
        if not isinstance(container_id, str) or not container_id:
            raise InstagramPermanentError("Instagram container response is malformed")
        return ContainerCheckpoint(container_id=container_id, created_at=self._clock())

    def container_fields(
        self, *, request: InstagramPublishRequest, pull_url: MediaPullUrl
    ) -> dict[str, str]:
        """Serialize one validated request into the exact container form fields."""
        fields: dict[str, str] = {
            "media_type": "REELS",
            "video_url": pull_url.url,
        }
        if request.caption:
            fields["caption"] = request.caption
        fields["share_to_feed"] = "true" if request.share_to_feed else "false"
        if request.cover_url is not None:
            fields["cover_url"] = request.cover_url
        if request.thumb_offset_ms is not None:
            fields["thumb_offset"] = str(request.thumb_offset_ms)
        if request.audio_name is not None:
            fields["audio_name"] = request.audio_name
        if request.location_id is not None:
            fields["location_id"] = request.location_id
        if request.collaborators:
            fields["collaborators"] = json.dumps(list(request.collaborators), separators=(",", ":"))
        return fields

    def poll(self, *, provider_id: str, access_token: SecretStr) -> InstagramContainerStatus:
        """Read the authoritative processing state for one known container."""
        response = self._get(
            provider_id,
            params={"fields": "status_code,status"},
            access_token=access_token,
        )
        return parse_container_status(container_id=provider_id, payload=self._read(response))

    def publish(self, *, container_id: str, access_token: SecretStr) -> PublishedMedia:
        """Publish exactly one finished container and adopt the media Instagram created."""
        try:
            response = self._client.post(
                f"{self._origin}/{self._api_version}/{self._account_id}/media_publish",
                data={"creation_id": container_id},
                headers=_authorization(access_token),
            )
        except httpx.TimeoutException as error:
            raise InstagramAmbiguousPublishError(
                "Instagram publication cannot be safely proved"
            ) from error
        if response.status_code in {500, 502, 503, 504}:
            raise InstagramAmbiguousPublishError("Instagram publication cannot be safely proved")
        normalize_instagram_response(response)
        payload = self._read(response)
        media_id = payload.get("id")
        if not isinstance(media_id, str) or not media_id:
            raise InstagramPermanentError("Instagram publish response is malformed")
        return PublishedMedia(
            media_id=media_id,
            permalink=self._permalink(media_id=media_id, access_token=access_token),
        )

    def reconcile_publish(
        self, *, container_id: str, attempted_at: datetime, access_token: SecretStr
    ) -> PublishedMedia | None:
        """Resolve one ambiguous publish from provider truth instead of repeating it."""
        status = self.poll(provider_id=container_id, access_token=access_token)
        if status.state is not ContainerState.PUBLISHED:
            return None
        response = self._get(
            f"{self._account_id}/media",
            params={
                "fields": "id,media_product_type,timestamp",
                "limit": str(MEDIA_LOOKBACK_LIMIT),
            },
            access_token=access_token,
        )
        payload = self._read(response)
        entries = payload.get("data")
        if not isinstance(entries, list):
            raise InstagramPermanentError("Instagram media response is malformed")
        candidates = [
            entry["id"]
            for entry in entries
            if isinstance(entry, dict)
            and isinstance(entry.get("id"), str)
            and entry.get("media_product_type") == REELS_PRODUCT_TYPE
            and _published_at(entry.get("timestamp")) >= attempted_at
        ]
        if len(candidates) != 1:
            raise InstagramAmbiguousPublishError("Instagram publication cannot be safely proved")
        media_id = candidates[0]
        return PublishedMedia(
            media_id=media_id,
            permalink=self._permalink(media_id=media_id, access_token=access_token),
        )

    def _permalink(self, *, media_id: str, access_token: SecretStr) -> str | None:
        """Read the public link for one published media without failing the publish."""
        try:
            response = self._get(
                media_id, params={"fields": "permalink"}, access_token=access_token
            )
        except (httpx.HTTPError, InstagramPermanentError, InstagramUnavailableError):
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        return safe_permalink(payload.get("permalink") if isinstance(payload, dict) else None)

    def _get(self, path: str, *, params: dict[str, str], access_token: SecretStr) -> httpx.Response:
        """Issue one authorized read against the configured Graph API version."""
        try:
            response = self._client.get(
                f"{self._origin}/{self._api_version}/{path}",
                params=params,
                headers=_authorization(access_token),
            )
        except httpx.TimeoutException as error:
            raise InstagramUnavailableError(
                "Instagram publishing is temporarily unavailable"
            ) from error
        normalize_instagram_response(response)
        return response

    @staticmethod
    def _read(response: httpx.Response) -> dict[str, object]:
        """Decode one successful provider response without retaining provider text."""
        try:
            payload = response.json()
        except ValueError as error:
            raise InstagramPermanentError("Instagram response is malformed") from error
        if not isinstance(payload, dict):
            raise InstagramPermanentError("Instagram response is malformed")
        return payload


def _authorization(access_token: SecretStr) -> dict[str, str]:
    """Construct a short-lived provider header without retaining plaintext state."""
    return {"Authorization": f"Bearer {access_token.get_secret_value()}"}


def _published_at(value: object) -> datetime:
    """Parse one aware provider timestamp, refusing evidence Clipah cannot order."""
    if not isinstance(value, str):
        raise InstagramPermanentError("Instagram media response is malformed")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise InstagramPermanentError("Instagram media response is malformed") from error
    if parsed.utcoffset() is None:
        raise InstagramPermanentError("Instagram media response is malformed")
    return parsed
