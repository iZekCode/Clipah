"""TikTok delivery policy, publish-status truth, and durable Publication state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.models import Publication, PublicationAttempt, SocialAccount, SocialRendition
from clipah.publishing.models import PublicationStatus
from clipah.publishing.state_machine import transition
from clipah.social_accounts.models import SocialProvider

PULL_URL_MIN_LIFETIME = timedelta(minutes=5)
PULL_URL_MAX_LIFETIME = timedelta(hours=1)
TIKTOK_PERMALINK_PREFIX = "https://www.tiktok.com/"

FORBIDDEN_PULL_URL_QUERY_KEYS = frozenset(
    {
        "access_token",
        "authorization",
        "bearer",
        "client_secret",
        "code",
        "id_token",
        "oauth_token",
        "refresh_token",
        "token",
    }
)

_RECONNECT_ERROR_CODES = frozenset(
    {
        "access_token_invalid",
        "invalid_grant",
        "scope_not_authorized",
        "scope_permission_missed",
        "unauthorized",
    }
)
_RATE_LIMIT_ERROR_CODES = frozenset(
    {"rate_limit_exceeded", "spam_risk_too_many_posts", "reached_active_user_cap"}
)
_UNAVAILABLE_ERROR_CODES = frozenset({"internal_error"})
_URL_OWNERSHIP_ERROR_CODES = frozenset({"url_ownership_unverified"})
_DOCUMENTED_FAILURE_REASONS = frozenset(
    {
        "file_format_check_failed",
        "duration_check_failed",
        "frame_rate_check_failed",
        "picture_size_check_failed",
        "internal",
        "video_pull_failed",
        "photo_pull_failed",
        "publish_cancelled",
    }
)


class TikTokProviderError(Exception):
    """Stable secret-free failure returned by the official TikTok boundary."""

    code = "tiktok_provider_error"

    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        """Retain only a fixed message and an optional safe retry delay."""
        super().__init__(message)
        self.retry_after = retry_after


class TikTokReconnectRequiredError(TikTokProviderError):
    """The Login Kit grant is expired, revoked, or missing a required scope."""

    code = "tiktok_reconnect_required"


class TikTokRateLimitedError(TikTokProviderError):
    """TikTok temporarily limited this creator or application from posting."""

    code = "tiktok_rate_limited"


class TikTokUnavailableError(TikTokProviderError):
    """The provider boundary failed in a retryable way."""

    code = "tiktok_unavailable"


class TikTokPermanentError(TikTokProviderError):
    """TikTok permanently rejected validated metadata, policy, or media."""

    code = "tiktok_permanent_failure"


class TikTokUrlOwnershipError(TikTokPermanentError):
    """TikTok will not pull from a URL prefix this application has not verified."""

    code = "tiktok_url_ownership_unverified"


class TikTokAccountMismatchError(TikTokPermanentError):
    """The live grant does not identify exactly the selected TikTok account."""

    code = "tiktok_account_mismatch"


class TikTokPullUrlError(Exception):
    """A media capability violates Clipah's own fetch-URL policy before it is used."""


class DeliveryMode(StrEnum):
    """How one approved artifact actually reaches a TikTok destination."""

    DRAFT_INBOX = "draft_inbox"
    DIRECT_POST = "direct_post"


class PublishState(StrEnum):
    """Normalized authoritative TikTok publish states."""

    PROCESSING = "processing"
    DELIVERED_TO_INBOX = "delivered_to_inbox"
    PUBLISHED = "published"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class TikTokPublishStatus:
    """Safe provider state for one authoritative publish identifier."""

    publish_id: str
    state: PublishState
    failure_code: str | None
    public_post_id: str | None


@dataclass(frozen=True, slots=True)
class TikTokPullUrl:
    """One short-lived HTTPS capability inside a TikTok-verified URL prefix."""

    url: str
    expires_at: datetime

    def __str__(self) -> str:
        """Describe the capability without printing a reusable fetch URL."""
        return f"TikTokPullUrl(expires_at={self.expires_at.isoformat()})"


@dataclass(frozen=True, slots=True)
class TransferCheckpoint:
    """Durable non-secret progress for one TikTok delivery."""

    publish_id: str | None = None
    mode: DeliveryMode = DeliveryMode.DRAFT_INBOX
    created_at: datetime | None = None
    init_ambiguous: bool = False
    public_post_id: str | None = None

    def __post_init__(self) -> None:
        """Reject progress that could authorize an unsafe or impossible next step."""
        if self.publish_id is None:
            if not self.init_ambiguous or self.created_at is not None:
                raise ValueError("a publish-less checkpoint must record init ambiguity")
        elif self.created_at is None or self.created_at.utcoffset() is None:
            raise ValueError("a submitted delivery requires an aware submission time")
        if self.public_post_id is not None and not self.public_post_id:
            raise ValueError("public post ID must not be blank")

    def safe_dict(self) -> dict[str, object]:
        """Serialize only progress that is safe for Postgres and audit evidence."""
        return {
            "publishId": self.publish_id,
            "mode": self.mode.value,
            "createdAt": None if self.created_at is None else self.created_at.isoformat(),
            "initAmbiguous": self.init_ambiguous,
            "publicPostId": self.public_post_id,
        }


def verified_pull_url(
    *,
    url: str,
    expected_key: str,
    verified_prefixes: tuple[str, ...],
    now: datetime,
    expires_at: datetime,
) -> TikTokPullUrl:
    """Bind one bounded capability to a rendition inside a verified TikTok prefix."""
    if now.utcoffset() is None or expires_at.utcoffset() is None:
        raise TikTokPullUrlError("media capability window must be timezone aware")
    lifetime = expires_at - now
    if not PULL_URL_MIN_LIFETIME <= lifetime <= PULL_URL_MAX_LIFETIME:
        raise TikTokPullUrlError("media capability lifetime is outside the agreed window")
    if not verified_prefixes:
        raise TikTokPullUrlError("this deployment has no TikTok-verified URL prefix")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise TikTokPullUrlError("media capability must be an HTTPS URL")
    if parts.username is not None or parts.password is not None:
        raise TikTokPullUrlError("media capability must not carry embedded credentials")
    if not parts.path.endswith(f"/{expected_key}"):
        raise TikTokPullUrlError("media capability does not name the frozen rendition")
    if not any(url.startswith(prefix) for prefix in verified_prefixes):
        raise TikTokPullUrlError("media capability is outside every verified URL prefix")
    keys = {name.casefold() for name, _value in parse_qsl(parts.query, keep_blank_values=True)}
    if keys & FORBIDDEN_PULL_URL_QUERY_KEYS:
        raise TikTokPullUrlError("media capability must not carry a provider credential")
    return TikTokPullUrl(url=url, expires_at=expires_at)


def normalize_tiktok_response(response: httpx.Response) -> dict[str, Any]:
    """Return one successful payload or raise the stable error TikTok reported."""
    payload = _payload(response)
    code = _error_code(payload)
    if 200 <= response.status_code < 300 and code in {None, "ok"}:
        data = payload.get("data")
        return data if isinstance(data, dict) else {}
    if response.status_code == 401 or (code is not None and code in _RECONNECT_ERROR_CODES):
        raise TikTokReconnectRequiredError("TikTok authorization must be reconnected")
    if code is not None and code in _URL_OWNERSHIP_ERROR_CODES:
        raise TikTokUrlOwnershipError("TikTok has not verified this media URL prefix")
    if response.status_code == 429 or (code is not None and code in _RATE_LIMIT_ERROR_CODES):
        raise TikTokRateLimitedError(
            "TikTok publishing is temporarily limited",
            retry_after=_retry_after(response.headers.get("Retry-After")),
        )
    if response.status_code in {500, 502, 503, 504} or (
        code is not None and code in _UNAVAILABLE_ERROR_CODES
    ):
        raise TikTokUnavailableError("TikTok publishing is temporarily unavailable")
    raise TikTokPermanentError("TikTok rejected the publishing operation")


def parse_publish_status(*, publish_id: str, payload: object) -> TikTokPublishStatus:
    """Parse only the requested status fields for exactly one known publish identifier."""
    if not isinstance(payload, dict):
        raise TikTokPermanentError("TikTok publish status is malformed")
    states = {
        "PROCESSING_UPLOAD": PublishState.PROCESSING,
        "PROCESSING_DOWNLOAD": PublishState.PROCESSING,
        "SEND_TO_USER_INBOX": PublishState.DELIVERED_TO_INBOX,
        "PUBLISH_COMPLETE": PublishState.PUBLISHED,
        "FAILED": PublishState.FAILED,
    }
    raw_state = payload.get("status")
    state = states.get(raw_state) if isinstance(raw_state, str) else None
    if state is None:
        raise TikTokPermanentError("TikTok publish status is malformed")
    return TikTokPublishStatus(
        publish_id=publish_id,
        state=state,
        failure_code=(
            _safe_failure_code(payload.get("fail_reason")) if state is PublishState.FAILED else None
        ),
        public_post_id=_public_post_id(payload.get("publicaly_available_post_id")),
    )


def safe_permalink(value: object) -> str | None:
    """Keep only a provider link that actually points at TikTok."""
    if not isinstance(value, str) or not value.startswith(TIKTOK_PERMALINK_PREFIX):
        return None
    return value if len(value) <= 2_048 else None


class TikTokPublicationInvalidError(Exception):
    """The durable Publication cannot safely accept this TikTok result."""


class TikTokPublicationCoordinator:
    """Apply safe delivery results to one locked tenant-scoped Publication."""

    def __init__(self, session: Session) -> None:
        """Share the transaction that makes one delivery checkpoint durable."""
        self._session = session

    def save_transfer(
        self,
        *,
        workspace_id: UUID,
        publication_id: UUID,
        checkpoint: TransferCheckpoint,
        now: datetime,
    ) -> None:
        """Persist delivery progress before TikTok processing truth is reconciled."""
        publication, _account, _rendition = self._locked_tiktok_publication(
            workspace_id=workspace_id,
            publication_id=publication_id,
        )
        if publication.status not in {
            PublicationStatus.PREFLIGHTING,
            PublicationStatus.TRANSFERRING,
        }:
            raise TikTokPublicationInvalidError("Publication is not transferable")
        if publication.status is PublicationStatus.PREFLIGHTING:
            publication.status = transition(
                current=publication.status,
                target=PublicationStatus.TRANSFERRING,
            ).current
            publication.dispatched_at = now
        metadata = dict(publication.checkpoint_metadata or {})
        metadata["tiktokTransfer"] = checkpoint.safe_dict()
        publication.checkpoint_metadata = metadata
        if checkpoint.publish_id is not None:
            publication.provider_publication_id = checkpoint.publish_id
            publication.transferred_at = now
        stage = (
            "tiktok_init_ambiguous"
            if checkpoint.publish_id is None
            else f"tiktok_{checkpoint.mode.value}_submitted"
        )
        self._append_attempt(
            publication=publication,
            stage=stage,
            response_metadata={"result": stage},
            now=now,
        )
        self._session.flush()

    def apply_status(
        self,
        *,
        workspace_id: UUID,
        publication_id: UUID,
        status: TikTokPublishStatus,
        poll_sequence: int,
        now: datetime,
    ) -> None:
        """Apply publish truth monotonically, ignoring late or duplicated evidence."""
        publication, _account, _rendition = self._locked_tiktok_publication(
            workspace_id=workspace_id,
            publication_id=publication_id,
        )
        metadata = dict(publication.checkpoint_metadata or {})
        transfer = dict(metadata.get("tiktokTransfer") or {})
        if transfer.get("publishId") != status.publish_id:
            raise TikTokPublicationInvalidError("status names another TikTok delivery")
        last_sequence = transfer.get("lastPollSequence")
        if isinstance(last_sequence, int) and poll_sequence <= last_sequence:
            return
        transfer["lastPollSequence"] = poll_sequence
        transfer["lastState"] = status.state.value
        if status.public_post_id is not None:
            transfer["publicPostId"] = status.public_post_id
        metadata["tiktokTransfer"] = transfer
        publication.checkpoint_metadata = metadata
        mode = transfer.get("mode")
        if publication.status is PublicationStatus.TRANSFERRING:
            self._apply_transfer_state(publication=publication, status=status, mode=mode, now=now)
        elif publication.status is PublicationStatus.PROCESSING:
            self._apply_processing_state(publication=publication, status=status, now=now)
        self._append_attempt(
            publication=publication,
            stage=f"tiktok_status_{poll_sequence}",
            response_metadata={"result": status.state.value},
            error_code=publication.normalized_error_code,
            error_message=publication.sanitized_error_message,
            now=now,
        )
        self._session.flush()

    def apply_error(
        self,
        *,
        workspace_id: UUID,
        publication_id: UUID,
        stage: str,
        error: Exception,
        now: datetime,
    ) -> None:
        """Map one sanitized provider failure onto the truthful durable lifecycle."""
        publication, _account, _rendition = self._locked_tiktok_publication(
            workspace_id=workspace_id,
            publication_id=publication_id,
        )
        if publication.status not in {
            PublicationStatus.PREFLIGHTING,
            PublicationStatus.TRANSFERRING,
            PublicationStatus.PROCESSING,
        }:
            raise TikTokPublicationInvalidError("Publication cannot accept a provider failure")
        if isinstance(error, TikTokReconnectRequiredError):
            target = PublicationStatus.RECONNECT_REQUIRED
            message = "Reconnect the TikTok Social Account before retrying."
        elif isinstance(error, TikTokPermanentError):
            target = PublicationStatus.PERMANENT_FAILED
            message = "TikTok rejected the publishing operation."
        elif isinstance(error, TikTokProviderError):
            target = PublicationStatus.RETRYABLE_FAILED
            message = "TikTok publishing is temporarily unavailable."
        else:
            raise TikTokPublicationInvalidError("provider failure is not normalized")
        publication.status = transition(current=publication.status, target=target).current
        publication.normalized_error_code = error.code
        publication.sanitized_error_message = message
        if target is PublicationStatus.PERMANENT_FAILED:
            publication.failed_at = now
        self._append_attempt(
            publication=publication,
            stage=stage,
            response_metadata={"result": target.value},
            error_code=error.code,
            error_message=message,
            now=now,
        )
        self._session.flush()

    def _apply_transfer_state(
        self,
        *,
        publication: Publication,
        status: TikTokPublishStatus,
        mode: object,
        now: datetime,
    ) -> None:
        """Interpret states that arrive while TikTok is still ingesting the media."""
        if status.state is PublishState.FAILED:
            publication.status = transition(
                current=publication.status,
                target=PublicationStatus.PERMANENT_FAILED,
            ).current
            publication.normalized_error_code = status.failure_code or "tiktok_publish_failed"
            publication.sanitized_error_message = "TikTok could not publish this video."
            publication.failed_at = now
            return
        if status.state is PublishState.DELIVERED_TO_INBOX:
            publication.status = transition(
                current=publication.status,
                target=PublicationStatus.PROCESSING,
            ).current
            publication.processing_at = now
            publication.sanitized_error_message = (
                "The video is in your TikTok inbox. Finish posting it in the TikTok app."
            )
        elif status.state is PublishState.PUBLISHED and mode == DeliveryMode.DIRECT_POST.value:
            publication.status = transition(
                current=publication.status,
                target=PublicationStatus.PROCESSING,
            ).current
            publication.processing_at = now
            self._apply_processing_state(publication=publication, status=status, now=now)

    def _apply_processing_state(
        self,
        *,
        publication: Publication,
        status: TikTokPublishStatus,
        now: datetime,
    ) -> None:
        """Interpret states that arrive after TikTok accepted the media."""
        if status.state is PublishState.PUBLISHED:
            publication.status = transition(
                current=publication.status,
                target=PublicationStatus.PUBLISHED,
            ).current
            publication.published_at = now
            publication.sanitized_error_message = None
            if status.public_post_id is not None:
                publication.provider_permalink = safe_permalink(
                    f"{TIKTOK_PERMALINK_PREFIX}@{_username(publication)}/video/"
                    f"{status.public_post_id}"
                )
        elif status.state is PublishState.FAILED:
            publication.status = transition(
                current=publication.status,
                target=PublicationStatus.PERMANENT_FAILED,
            ).current
            publication.normalized_error_code = status.failure_code or "tiktok_publish_failed"
            publication.sanitized_error_message = "TikTok could not publish this video."
            publication.failed_at = now

    def _locked_tiktok_publication(
        self, *, workspace_id: UUID, publication_id: UUID
    ) -> tuple[Publication, SocialAccount, SocialRendition]:
        """Lock and validate one exact TikTok Publication and bound rendition."""
        publication = self._session.scalar(
            select(Publication)
            .where(
                Publication.workspace_id == workspace_id,
                Publication.id == publication_id,
            )
            .with_for_update()
        )
        if publication is None or publication.social_rendition_id is None:
            raise TikTokPublicationInvalidError("Publication is unavailable")
        account = self._session.scalar(
            select(SocialAccount).where(
                SocialAccount.workspace_id == workspace_id,
                SocialAccount.id == publication.social_account_id,
            )
        )
        rendition = self._session.scalar(
            select(SocialRendition).where(
                SocialRendition.workspace_id == workspace_id,
                SocialRendition.id == publication.social_rendition_id,
                SocialRendition.render_artifact_id == publication.render_artifact_id,
            )
        )
        if (
            account is None
            or rendition is None
            or account.provider is not SocialProvider.TIKTOK
            or rendition.provider is not SocialProvider.TIKTOK
        ):
            raise TikTokPublicationInvalidError("Publication is not a bound TikTok delivery")
        return publication, account, rendition

    def _append_attempt(
        self,
        *,
        publication: Publication,
        stage: str,
        response_metadata: dict[str, object],
        now: datetime,
        provider_request_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Append one bounded secret-free durable stage exactly once."""
        attempt_number = publication.attempt_count + 1
        existing = self._session.scalar(
            select(PublicationAttempt.id).where(
                PublicationAttempt.workspace_id == publication.workspace_id,
                PublicationAttempt.publication_id == publication.id,
                PublicationAttempt.attempt == attempt_number,
                PublicationAttempt.stage == stage,
            )
        )
        if existing is not None:
            return
        self._session.add(
            PublicationAttempt(
                workspace_id=publication.workspace_id,
                publication_id=publication.id,
                attempt=attempt_number,
                stage=stage,
                provider_request_id=provider_request_id,
                request_metadata={"operation": stage},
                response_metadata=response_metadata,
                error_code=error_code,
                error_message=error_message,
                started_at=now,
                finished_at=now,
                created_at=now,
            )
        )


def _username(publication: Publication) -> str:
    """Read the frozen destination handle a TikTok permalink is built from."""
    value = publication.provider_options.get("creatorUsername")
    return value if isinstance(value, str) and value else "unknown"


def _payload(response: httpx.Response) -> dict[str, Any]:
    """Decode one provider response body without retaining provider text."""
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _error_code(payload: dict[str, Any]) -> str | None:
    """Read only the bounded documented error code from a response envelope."""
    error = payload.get("error")
    if isinstance(error, str):
        return error if len(error) <= 64 else None
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return code if isinstance(code, str) and len(code) <= 64 else None


def _retry_after(value: str | None) -> int | None:
    """Retain a bounded integer delay and discard every other header form."""
    try:
        result = int(value) if value is not None else None
    except ValueError:
        return None
    return result if result is not None and 0 <= result <= 86_400 else None


def _safe_failure_code(value: object) -> str:
    """Keep only documented bounded failure reasons."""
    return (
        value
        if isinstance(value, str) and value in _DOCUMENTED_FAILURE_REASONS
        else "tiktok_publish_failed"
    )


def _public_post_id(value: object) -> str | None:
    """Adopt exactly one publicly available post identifier, or none at all."""
    if not isinstance(value, list) or len(value) != 1:
        return None
    identifier = value[0]
    return identifier if isinstance(identifier, str) and 0 < len(identifier) <= 128 else None
