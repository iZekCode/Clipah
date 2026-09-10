"""Strict TikTok declarations, the audit gate, and the Content Posting adapter."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from enum import StrEnum

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from clipah.publishing.providers.base import PublicationMedia
from clipah.publishing.providers.tiktok.transfers import (
    DeliveryMode,
    TikTokAccountMismatchError,
    TikTokPermanentError,
    TikTokPublishStatus,
    TikTokPullUrl,
    TikTokUnavailableError,
    TransferCheckpoint,
    normalize_tiktok_response,
    parse_publish_status,
)
from clipah.social_accounts.models import SocialProvider

CREATOR_INFO_MAX_AGE = timedelta(minutes=5)
CONSENT_MAX_AGE = timedelta(minutes=5)
DRAFT_REMAINING_ACTION = "Open the TikTok app and finish posting the video waiting in your inbox."


class TikTokPrivacyLevel(StrEnum):
    """Visibility values the TikTok Content Posting API documents."""

    PUBLIC_TO_EVERYONE = "PUBLIC_TO_EVERYONE"
    MUTUAL_FOLLOW_FRIENDS = "MUTUAL_FOLLOW_FRIENDS"
    FOLLOWER_OF_CREATOR = "FOLLOWER_OF_CREATOR"
    SELF_ONLY = "SELF_ONLY"


class TikTokDeclarationError(Exception):
    """One TikTok rule refuses this submission, named by a stable public code."""

    def __init__(self, code: str, message: str) -> None:
        """Retain only the fixed code and public message shown to the member."""
        super().__init__(message)
        self.code = code


class TikTokCreatorInfo(BaseModel):
    """One freshly read snapshot of what this creator may currently choose."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    creator_username: str = Field(min_length=1, max_length=255)
    privacy_level_options: tuple[TikTokPrivacyLevel, ...] = Field(min_length=1)
    comment_disabled: bool
    duet_disabled: bool
    stitch_disabled: bool
    max_video_post_duration_sec: int = Field(gt=0)
    fetched_at: datetime

    @field_validator("fetched_at")
    @classmethod
    def require_aware_fetch_time(cls, value: datetime) -> datetime:
        """Keep freshness checks tied to one reproducible UTC instant."""
        if value.utcoffset() is None:
            raise ValueError("fetched_at must include an offset")
        return value


class TikTokConsent(BaseModel):
    """The declarations a member must make explicitly before every submission."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    music_usage_confirmed: bool
    consumer_terms_accepted: bool
    branded_content_terms_accepted: bool = False
    confirmed_at: datetime

    @field_validator("confirmed_at")
    @classmethod
    def require_aware_confirmation(cls, value: datetime) -> datetime:
        """Keep consent freshness tied to one reproducible UTC instant."""
        if value.utcoffset() is None:
            raise ValueError("confirmed_at must include an offset")
        return value


class TikTokPostRequest(BaseModel):
    """Frozen TikTok choices in which nothing is preselected or inferred."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    title: str = Field(default="", max_length=2_200)
    privacy_level: TikTokPrivacyLevel
    disable_comment: bool
    disable_duet: bool
    disable_stitch: bool
    video_cover_timestamp_ms: int | None = Field(default=None, ge=0)
    brand_content_toggle: bool
    brand_organic_toggle: bool
    is_aigc: bool
    consent: TikTokConsent


class TikTokPolicy(BaseModel):
    """Fail-closed audit policy deciding how a destination actually delivers."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    direct_post_approved: bool = False
    now: datetime

    @field_validator("now")
    @classmethod
    def require_aware_now(cls, value: datetime) -> datetime:
        """Keep policy evaluation deterministic and tied to one UTC-capable instant."""
        if value.utcoffset() is None:
            raise ValueError("now must include an offset")
        return value

    def delivery_mode(self) -> DeliveryMode:
        """Route to the official draft flow until Direct Post is approved."""
        return DeliveryMode.DIRECT_POST if self.direct_post_approved else DeliveryMode.DRAFT_INBOX

    def confirmation_evidence(self) -> dict[str, str | None]:
        """Describe requested and effective delivery without silently changing either."""
        mode = self.delivery_mode()
        draft = mode is DeliveryMode.DRAFT_INBOX
        return {
            "requestedMode": DeliveryMode.DIRECT_POST.value,
            "effectiveMode": mode.value,
            "restriction": "tiktok_direct_post_audit_required" if draft else None,
            "remainingUserAction": DRAFT_REMAINING_ACTION if draft else None,
        }


class TikTokPublisher:
    """Official HTTP adapter for one exact TikTok creator and frozen declarations."""

    provider = SocialProvider.TIKTOK

    def __init__(
        self,
        *,
        client: httpx.Client,
        api_origin: str,
        policy: TikTokPolicy,
        media_url_provider: Callable[[], TikTokPullUrl],
        clock: Callable[[], datetime],
    ) -> None:
        """Bind an injected transport, audit policy, capability source, and clock."""
        self._client = client
        self._origin = api_origin.rstrip("/")
        self._policy = policy
        self._media_url_provider = media_url_provider
        self._clock = clock

    def confirm_destination(self, *, access_token: SecretStr, expected_account_id: str) -> None:
        """Require the live grant to name exactly the frozen TikTok open identifier."""
        payload = self._get(
            "/v2/user/info/", params={"fields": "open_id,username"}, access_token=access_token
        )
        user = payload.get("user")
        open_id = user.get("open_id") if isinstance(user, dict) else None
        if open_id != expected_account_id:
            raise TikTokAccountMismatchError("TikTok account identity does not match")

    def creator_info(self, *, access_token: SecretStr) -> TikTokCreatorInfo:
        """Read the creator's current provider-permitted choices at this instant."""
        payload = self._post("/v2/post/publish/creator_info/query/", access_token=access_token)
        try:
            return TikTokCreatorInfo.model_validate(
                {
                    "creator_username": payload.get("creator_username"),
                    "privacy_level_options": tuple(
                        _privacy_options(payload.get("privacy_level_options"))
                    ),
                    "comment_disabled": payload.get("comment_disabled"),
                    "duet_disabled": payload.get("duet_disabled"),
                    "stitch_disabled": payload.get("stitch_disabled"),
                    "max_video_post_duration_sec": payload.get("max_video_post_duration_sec"),
                    "fetched_at": self._clock(),
                }
            )
        except ValueError as error:
            raise TikTokPermanentError("TikTok creator information is malformed") from error

    def require_current_declarations(
        self,
        *,
        request: TikTokPostRequest,
        creator_info: TikTokCreatorInfo,
        duration_ms: int,
        mode: DeliveryMode,
        now: datetime,
    ) -> None:
        """Apply TikTok's own rules to fresh evidence immediately before submission."""
        consent = request.consent
        if not timedelta(0) <= now - creator_info.fetched_at <= CREATOR_INFO_MAX_AGE:
            raise TikTokDeclarationError(
                "creator_info_stale", "Refresh the TikTok options before posting."
            )
        if not timedelta(0) <= now - consent.confirmed_at <= CONSENT_MAX_AGE:
            raise TikTokDeclarationError(
                "consent_stale", "Confirm the TikTok declarations again before posting."
            )
        if not consent.music_usage_confirmed or not consent.consumer_terms_accepted:
            raise TikTokDeclarationError(
                "consent_missing", "Confirm TikTok's music and terms declarations."
            )
        if duration_ms > creator_info.max_video_post_duration_sec * 1_000:
            raise TikTokDeclarationError(
                "duration_not_permitted", "This creator cannot post a video this long."
            )
        if (
            request.brand_content_toggle or request.brand_organic_toggle
        ) and not consent.branded_content_terms_accepted:
            raise TikTokDeclarationError(
                "branded_content_terms", "Accept TikTok's branded content terms."
            )
        if mode is not DeliveryMode.DIRECT_POST:
            return
        if request.privacy_level not in creator_info.privacy_level_options:
            raise TikTokDeclarationError(
                "privacy_level_option_mismatch", "Choose a visibility TikTok currently offers."
            )
        if request.brand_content_toggle and request.privacy_level is TikTokPrivacyLevel.SELF_ONLY:
            raise TikTokDeclarationError(
                "branded_content_visibility", "Branded content cannot be posted privately."
            )
        if (
            (creator_info.comment_disabled and not request.disable_comment)
            or (creator_info.duet_disabled and not request.disable_duet)
            or (creator_info.stitch_disabled and not request.disable_stitch)
        ):
            raise TikTokDeclarationError(
                "interaction_unavailable", "This creator cannot enable that interaction."
            )

    def begin(
        self,
        *,
        request: TikTokPostRequest,
        media: PublicationMedia,
        access_token: SecretStr,
        creator_info: TikTokCreatorInfo | None = None,
        duration_ms: int = 0,
    ) -> TransferCheckpoint:
        """Submit one delivery through the mode current audit policy permits."""
        del media
        mode = self._policy.delivery_mode()
        if creator_info is not None:
            self.require_current_declarations(
                request=request,
                creator_info=creator_info,
                duration_ms=duration_ms,
                mode=mode,
                now=self._clock(),
            )
        pull_url = self._media_url_provider()
        source_info = {"source": "PULL_FROM_URL", "video_url": pull_url.url}
        if mode is DeliveryMode.DIRECT_POST:
            path = "/v2/post/publish/video/init/"
            body: dict[str, object] = {
                "post_info": self.post_info(request),
                "source_info": source_info,
            }
        else:
            path = "/v2/post/publish/inbox/video/init/"
            body = {"source_info": source_info}
        try:
            payload = self._post(path, access_token=access_token, json=body)
        except httpx.TimeoutException:
            return TransferCheckpoint(mode=mode, init_ambiguous=True)
        publish_id = payload.get("publish_id")
        if not isinstance(publish_id, str) or not publish_id:
            raise TikTokPermanentError("TikTok publish response is malformed")
        return TransferCheckpoint(publish_id=publish_id, mode=mode, created_at=self._clock())

    def post_info(self, request: TikTokPostRequest) -> dict[str, object]:
        """Serialize one validated Direct Post request into exact provider fields."""
        info: dict[str, object] = {
            "title": request.title,
            "privacy_level": request.privacy_level.value,
            "disable_comment": request.disable_comment,
            "disable_duet": request.disable_duet,
            "disable_stitch": request.disable_stitch,
            "brand_content_toggle": request.brand_content_toggle,
            "brand_organic_toggle": request.brand_organic_toggle,
            "is_aigc": request.is_aigc,
        }
        if request.video_cover_timestamp_ms is not None:
            info["video_cover_timestamp_ms"] = request.video_cover_timestamp_ms
        return info

    def poll(self, *, provider_id: str, access_token: SecretStr) -> TikTokPublishStatus:
        """Read the authoritative publish state for one known publish identifier."""
        payload = self._post(
            "/v2/post/publish/status/fetch/",
            access_token=access_token,
            json={"publish_id": provider_id},
        )
        return parse_publish_status(publish_id=provider_id, payload=payload)

    def _get(
        self, path: str, *, params: dict[str, str], access_token: SecretStr
    ) -> dict[str, object]:
        """Issue one authorized read and return only its normalized data payload."""
        try:
            response = self._client.get(
                f"{self._origin}{path}", params=params, headers=_authorization(access_token)
            )
        except httpx.TimeoutException as error:
            raise TikTokUnavailableError("TikTok publishing is temporarily unavailable") from error
        return normalize_tiktok_response(response)

    def _post(
        self,
        path: str,
        *,
        access_token: SecretStr,
        json: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Issue one authorized write and return only its normalized data payload."""
        response = self._client.post(
            f"{self._origin}{path}",
            json=json if json is not None else {},
            headers={
                **_authorization(access_token),
                "Content-Type": "application/json; charset=UTF-8",
            },
        )
        return normalize_tiktok_response(response)


def _authorization(access_token: SecretStr) -> dict[str, str]:
    """Construct a short-lived provider header without retaining plaintext state."""
    return {"Authorization": f"Bearer {access_token.get_secret_value()}"}


def _privacy_options(value: object) -> list[TikTokPrivacyLevel]:
    """Accept only visibility options this deployment can reproduce exactly."""
    if not isinstance(value, list) or not value:
        raise TikTokPermanentError("TikTok creator information is malformed")
    try:
        return [TikTokPrivacyLevel(option) for option in value]
    except ValueError as error:
        raise TikTokPermanentError("TikTok creator information is malformed") from error
