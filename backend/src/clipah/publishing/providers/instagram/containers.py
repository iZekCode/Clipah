"""Instagram Reels container lifecycle, from creation policy to durable Publication state."""

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

CONTAINER_LIFETIME = timedelta(hours=24)
CONTAINER_CREATION_LEAD = timedelta(minutes=30)
PUBLISH_DEADLINE_MARGIN = timedelta(minutes=15)
PULL_URL_MIN_LIFETIME = timedelta(minutes=5)
PULL_URL_MAX_LIFETIME = timedelta(hours=1)
INSTAGRAM_PERMALINK_PREFIX = "https://www.instagram.com/"

FORBIDDEN_PULL_URL_QUERY_KEYS = frozenset(
    {
        "access_token",
        "appsecret_proof",
        "authorization",
        "bearer",
        "client_secret",
        "id_token",
        "ig_token",
        "oauth_token",
        "token",
    }
)

_RECONNECT_ERROR_CODES = frozenset({10, 102, 190, 200, 458, 459, 460, 463, 467})
_RATE_LIMIT_ERROR_CODES = frozenset({4, 17, 32, 613})
_UNAVAILABLE_ERROR_CODES = frozenset({1, 2})
_PUBLISHING_LIMIT_ERROR_CODES = frozenset({9})
_PUBLISHING_LIMIT_SUBCODES = frozenset({2207042})


class InstagramProviderError(Exception):
    """Stable secret-free failure returned by the official Instagram boundary."""

    code = "instagram_provider_error"

    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        """Retain only a fixed message and an optional safe retry delay."""
        super().__init__(message)
        self.retry_after = retry_after


class InstagramReconnectRequiredError(InstagramProviderError):
    """The OAuth Grant is expired, revoked, or missing a required permission."""

    code = "instagram_reconnect_required"


class InstagramRateLimitedError(InstagramProviderError):
    """Instagram temporarily limited this publishing operation."""

    code = "instagram_rate_limited"


class InstagramPublishingLimitError(InstagramProviderError):
    """The destination has spent its rolling daily content-publishing allowance."""

    code = "instagram_publishing_limit_reached"


class InstagramUnavailableError(InstagramProviderError):
    """The provider boundary failed in a retryable way."""

    code = "instagram_unavailable"


class InstagramPermanentError(InstagramProviderError):
    """Instagram permanently rejected validated metadata, policy, or media."""

    code = "instagram_permanent_failure"


class InstagramAmbiguousPublishError(InstagramProviderError):
    """A publish result cannot be proved or disproved without risking a duplicate post."""

    code = "instagram_ambiguous_publish"


class InstagramMediaUnreachableError(InstagramProviderError):
    """The frozen rendition is not fetchable at the capability Instagram would pull."""

    code = "instagram_media_unreachable"


class InstagramAccountMismatchError(InstagramProviderError):
    """The live grant does not identify exactly the selected Instagram account."""

    code = "instagram_account_mismatch"


class InstagramPullUrlError(Exception):
    """A media capability violates Clipah's own fetch-URL policy before it is used."""


class ContainerState(StrEnum):
    """Normalized authoritative Instagram media-container states."""

    IN_PROGRESS = "in_progress"
    FINISHED = "finished"
    PUBLISHED = "published"
    ERROR = "error"
    EXPIRED = "expired"


class SchedulingAction(StrEnum):
    """What Clipah's own scheduler may safely do for one destination right now."""

    WAIT = "wait"
    CREATE = "create"
    PUBLISH = "publish"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class InstagramContainerStatus:
    """Safe provider state for one authoritative media container."""

    container_id: str
    state: ContainerState
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class MediaPullUrl:
    """One short-lived HTTPS capability Instagram is permitted to fetch."""

    url: str
    expires_at: datetime

    def __str__(self) -> str:
        """Describe the capability without printing a reusable fetch URL."""
        return f"MediaPullUrl(expires_at={self.expires_at.isoformat()})"


@dataclass(frozen=True, slots=True)
class PublishingAllowance:
    """The destination's live rolling daily content-publishing allowance."""

    quota_usage: int
    quota_total: int

    def __post_init__(self) -> None:
        """Refuse an allowance that cannot describe a real remaining balance."""
        if self.quota_usage < 0 or self.quota_total <= 0:
            raise ValueError("publishing allowance is invalid")

    @property
    def remaining(self) -> int:
        """Return the posts still available inside the provider's rolling window."""
        return max(self.quota_total - self.quota_usage, 0)


@dataclass(frozen=True, slots=True)
class ContainerCheckpoint:
    """Durable non-secret progress for one Instagram Reels container."""

    container_id: str | None = None
    created_at: datetime | None = None
    creation_ambiguous: bool = False
    publish_ambiguous: bool = False
    published_media_id: str | None = None

    def __post_init__(self) -> None:
        """Reject progress that could authorize an unsafe or impossible next step."""
        if self.container_id is None:
            if not self.creation_ambiguous or self.created_at is not None:
                raise ValueError("a container-less checkpoint must record creation ambiguity")
        elif self.created_at is None or self.created_at.utcoffset() is None:
            raise ValueError("a created container requires an aware creation time")
        if self.published_media_id is not None and not self.published_media_id:
            raise ValueError("published media ID must not be blank")

    @property
    def expires_at(self) -> datetime | None:
        """Return the instant after which Instagram discards this container."""
        return None if self.created_at is None else self.created_at + CONTAINER_LIFETIME

    def safe_dict(self) -> dict[str, object]:
        """Serialize only progress that is safe for Postgres and audit evidence."""
        return {
            "containerId": self.container_id,
            "createdAt": None if self.created_at is None else self.created_at.isoformat(),
            "creationAmbiguous": self.creation_ambiguous,
            "publishAmbiguous": self.publish_ambiguous,
            "publishedMediaId": self.published_media_id,
        }


def build_pull_url(
    *, url: str, expected_key: str, now: datetime, expires_at: datetime
) -> MediaPullUrl:
    """Bind one bounded HTTPS capability to exactly the rendition Instagram may fetch."""
    if now.utcoffset() is None or expires_at.utcoffset() is None:
        raise InstagramPullUrlError("media capability window must be timezone aware")
    lifetime = expires_at - now
    if not PULL_URL_MIN_LIFETIME <= lifetime <= PULL_URL_MAX_LIFETIME:
        raise InstagramPullUrlError("media capability lifetime is outside the agreed window")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise InstagramPullUrlError("media capability must be an HTTPS URL")
    if parts.username is not None or parts.password is not None:
        raise InstagramPullUrlError("media capability must not carry embedded credentials")
    if not parts.path.endswith(f"/{expected_key}"):
        raise InstagramPullUrlError("media capability does not name the frozen rendition")
    keys = {name.casefold() for name, _value in parse_qsl(parts.query, keep_blank_values=True)}
    if keys & FORBIDDEN_PULL_URL_QUERY_KEYS:
        raise InstagramPullUrlError("media capability must not carry a provider credential")
    return MediaPullUrl(url=url, expires_at=expires_at)


def normalize_instagram_response(response: httpx.Response) -> None:
    """Raise one stable error for a non-successful Instagram HTTP response."""
    if 200 <= response.status_code < 300:
        return
    code, subcode = _error_codes(response)
    if response.status_code == 401 or code in _RECONNECT_ERROR_CODES:
        raise InstagramReconnectRequiredError("Instagram authorization must be reconnected")
    if code in _PUBLISHING_LIMIT_ERROR_CODES or subcode in _PUBLISHING_LIMIT_SUBCODES:
        raise InstagramPublishingLimitError("Instagram publishing allowance is exhausted")
    if response.status_code == 429 or code in _RATE_LIMIT_ERROR_CODES:
        raise InstagramRateLimitedError(
            "Instagram publishing is temporarily rate limited",
            retry_after=_retry_after(response.headers.get("Retry-After")),
        )
    if response.status_code in {500, 502, 503, 504} or code in _UNAVAILABLE_ERROR_CODES:
        raise InstagramUnavailableError("Instagram publishing is temporarily unavailable")
    raise InstagramPermanentError("Instagram rejected the publishing operation")


def parse_container_status(*, container_id: str, payload: object) -> InstagramContainerStatus:
    """Parse only the requested status fields for exactly one known container."""
    if not isinstance(payload, dict) or payload.get("id") != container_id:
        raise InstagramPermanentError("Instagram container status is malformed")
    raw_state = payload.get("status_code")
    states = {
        "IN_PROGRESS": ContainerState.IN_PROGRESS,
        "FINISHED": ContainerState.FINISHED,
        "PUBLISHED": ContainerState.PUBLISHED,
        "ERROR": ContainerState.ERROR,
        "EXPIRED": ContainerState.EXPIRED,
    }
    state = states.get(raw_state) if isinstance(raw_state, str) else None
    if state is None:
        raise InstagramPermanentError("Instagram container status is malformed")
    failure_code = (
        _safe_failure_code(payload.get("status")) if state is ContainerState.ERROR else None
    )
    return InstagramContainerStatus(
        container_id=container_id,
        state=state,
        failure_code=failure_code,
    )


def parse_publishing_limit(payload: object) -> PublishingAllowance:
    """Parse the live rolling allowance, refusing to assume unlimited headroom."""
    data = payload.get("data") if isinstance(payload, dict) else None
    entry = data[0] if isinstance(data, list) and data else None
    if not isinstance(entry, dict):
        raise InstagramPermanentError("Instagram publishing allowance is unavailable")
    config = entry.get("config")
    usage = entry.get("quota_usage")
    total = config.get("quota_total") if isinstance(config, dict) else None
    if not isinstance(usage, int) or not isinstance(total, int) or isinstance(usage, bool):
        raise InstagramPermanentError("Instagram publishing allowance is unavailable")
    try:
        return PublishingAllowance(quota_usage=usage, quota_total=total)
    except ValueError as error:
        raise InstagramPermanentError("Instagram publishing allowance is unavailable") from error


def require_publishing_headroom(allowance: PublishingAllowance) -> None:
    """Refuse a new Reel locally when the destination has no allowance left."""
    if allowance.remaining <= 0:
        raise InstagramPublishingLimitError("Instagram publishing allowance is exhausted")


def scheduling_decision(
    *,
    checkpoint: ContainerCheckpoint | None,
    scheduled_for: datetime | None,
    now: datetime,
) -> SchedulingAction:
    """Decide the one safe next step, keeping creation close to the requested time."""
    if checkpoint is None or checkpoint.container_id is None or checkpoint.created_at is None:
        if scheduled_for is None or now >= scheduled_for - CONTAINER_CREATION_LEAD:
            return SchedulingAction.CREATE
        return SchedulingAction.WAIT
    if now >= checkpoint.created_at + CONTAINER_LIFETIME - PUBLISH_DEADLINE_MARGIN:
        return SchedulingAction.EXPIRED
    if scheduled_for is not None and now < scheduled_for:
        return SchedulingAction.WAIT
    return SchedulingAction.PUBLISH


def safe_permalink(value: object) -> str | None:
    """Keep only a provider link that actually points at Instagram."""
    if not isinstance(value, str) or not value.startswith(INSTAGRAM_PERMALINK_PREFIX):
        return None
    return value if len(value) <= 2_048 else None


class InstagramPublicationInvalidError(Exception):
    """The durable Publication cannot safely accept this Instagram result."""


class InstagramPublicationCoordinator:
    """Apply safe container results to one locked tenant-scoped Publication."""

    def __init__(self, session: Session) -> None:
        """Share the transaction that makes one container checkpoint durable."""
        self._session = session

    def save_container(
        self,
        *,
        workspace_id: UUID,
        publication_id: UUID,
        checkpoint: ContainerCheckpoint,
        now: datetime,
    ) -> None:
        """Persist container progress before Clipah asks Instagram to publish it."""
        publication, _account, _rendition = self._locked_instagram_publication(
            workspace_id=workspace_id,
            publication_id=publication_id,
        )
        if publication.status not in {
            PublicationStatus.PREFLIGHTING,
            PublicationStatus.TRANSFERRING,
        }:
            raise InstagramPublicationInvalidError("Publication is not transferable")
        if publication.status is PublicationStatus.PREFLIGHTING:
            publication.status = transition(
                current=publication.status,
                target=PublicationStatus.TRANSFERRING,
            ).current
            publication.dispatched_at = now
        metadata = dict(publication.checkpoint_metadata or {})
        metadata["instagramContainer"] = checkpoint.safe_dict()
        publication.checkpoint_metadata = metadata
        stage = (
            "instagram_container_ambiguous"
            if checkpoint.container_id is None
            else "instagram_container_created"
        )
        self._append_attempt(
            publication=publication,
            stage=stage,
            response_metadata={"result": stage},
            now=now,
        )
        self._session.flush()

    def record_media_published(
        self,
        *,
        workspace_id: UUID,
        publication_id: UUID,
        media_id: str,
        permalink: str | None,
        provider_request_id: str | None,
        now: datetime,
    ) -> None:
        """Retain one authoritative media ID and move transfer truth to processing."""
        publication, _account, _rendition = self._locked_instagram_publication(
            workspace_id=workspace_id,
            publication_id=publication_id,
        )
        if publication.provider_publication_id is not None:
            if publication.provider_publication_id != media_id:
                raise InstagramPublicationInvalidError("Publication already names another media")
            return
        if publication.status is not PublicationStatus.TRANSFERRING or not media_id:
            raise InstagramPublicationInvalidError("Publication cannot record Instagram media")
        publication.provider_publication_id = media_id
        publication.provider_permalink = safe_permalink(permalink)
        publication.status = transition(
            current=publication.status,
            target=PublicationStatus.PROCESSING,
        ).current
        publication.transferred_at = now
        publication.processing_at = now
        metadata = dict(publication.checkpoint_metadata or {})
        container = dict(metadata.get("instagramContainer") or {})
        container["publishedMediaId"] = media_id
        container["publishAmbiguous"] = False
        metadata["instagramContainer"] = container
        publication.checkpoint_metadata = metadata
        self._append_attempt(
            publication=publication,
            stage="instagram_media_published",
            provider_request_id=provider_request_id,
            response_metadata={"result": "media_published"},
            now=now,
        )
        self._session.flush()

    def apply_status(
        self,
        *,
        workspace_id: UUID,
        publication_id: UUID,
        status: InstagramContainerStatus,
        poll_sequence: int,
        now: datetime,
    ) -> None:
        """Apply container truth monotonically, ignoring late or duplicated evidence."""
        publication, _account, _rendition = self._locked_instagram_publication(
            workspace_id=workspace_id,
            publication_id=publication_id,
        )
        metadata = dict(publication.checkpoint_metadata or {})
        container = dict(metadata.get("instagramContainer") or {})
        if container.get("containerId") != status.container_id:
            raise InstagramPublicationInvalidError("status names another Instagram container")
        last_sequence = container.get("lastPollSequence")
        if isinstance(last_sequence, int) and poll_sequence <= last_sequence:
            return
        container["lastPollSequence"] = poll_sequence
        container["lastState"] = status.state.value
        metadata["instagramContainer"] = container
        publication.checkpoint_metadata = metadata
        if publication.status is PublicationStatus.TRANSFERRING:
            self._apply_transfer_state(publication=publication, status=status, now=now)
        elif publication.status is PublicationStatus.PROCESSING:
            self._apply_processing_state(publication=publication, status=status, now=now)
        self._append_attempt(
            publication=publication,
            stage=f"instagram_status_{poll_sequence}",
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
        publication, _account, _rendition = self._locked_instagram_publication(
            workspace_id=workspace_id,
            publication_id=publication_id,
        )
        if publication.status not in {
            PublicationStatus.PREFLIGHTING,
            PublicationStatus.TRANSFERRING,
            PublicationStatus.PROCESSING,
        }:
            raise InstagramPublicationInvalidError("Publication cannot accept a provider failure")
        if isinstance(error, InstagramReconnectRequiredError):
            target = PublicationStatus.RECONNECT_REQUIRED
            message = "Reconnect the Instagram Social Account before retrying."
        elif isinstance(error, InstagramPermanentError):
            target = PublicationStatus.PERMANENT_FAILED
            message = "Instagram rejected the publishing operation."
        elif isinstance(error, InstagramProviderError):
            target = PublicationStatus.RETRYABLE_FAILED
            message = "Instagram publishing is temporarily unavailable."
        else:
            raise InstagramPublicationInvalidError("provider failure is not normalized")
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
        status: InstagramContainerStatus,
        now: datetime,
    ) -> None:
        """Interpret container states that arrive before Clipah has published."""
        if status.state is ContainerState.ERROR:
            publication.status = transition(
                current=publication.status,
                target=PublicationStatus.PERMANENT_FAILED,
            ).current
            publication.normalized_error_code = status.failure_code or "instagram_container_failed"
            publication.sanitized_error_message = "Instagram could not process this Reel."
            publication.failed_at = now
        elif status.state is ContainerState.EXPIRED:
            publication.status = transition(
                current=publication.status,
                target=PublicationStatus.RETRYABLE_FAILED,
            ).current
            publication.normalized_error_code = "instagram_container_expired"
            publication.sanitized_error_message = "Instagram publishing is temporarily unavailable."

    def _apply_processing_state(
        self,
        *,
        publication: Publication,
        status: InstagramContainerStatus,
        now: datetime,
    ) -> None:
        """Interpret container states that arrive after Clipah published one media."""
        if status.state is ContainerState.PUBLISHED:
            publication.status = transition(
                current=publication.status,
                target=PublicationStatus.PUBLISHED,
            ).current
            publication.published_at = now
        elif status.state is ContainerState.ERROR:
            publication.status = transition(
                current=publication.status,
                target=PublicationStatus.PERMANENT_FAILED,
            ).current
            publication.normalized_error_code = status.failure_code or "instagram_container_failed"
            publication.sanitized_error_message = "Instagram could not process this Reel."
            publication.failed_at = now

    def _locked_instagram_publication(
        self, *, workspace_id: UUID, publication_id: UUID
    ) -> tuple[Publication, SocialAccount, SocialRendition]:
        """Lock and validate one exact Instagram Publication and bound rendition."""
        publication = self._session.scalar(
            select(Publication)
            .where(
                Publication.workspace_id == workspace_id,
                Publication.id == publication_id,
            )
            .with_for_update()
        )
        if publication is None or publication.social_rendition_id is None:
            raise InstagramPublicationInvalidError("Publication is unavailable")
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
            or account.provider is not SocialProvider.INSTAGRAM
            or rendition.provider is not SocialProvider.INSTAGRAM
        ):
            raise InstagramPublicationInvalidError("Publication is not a bound Instagram delivery")
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


def _error_codes(response: httpx.Response) -> tuple[int | None, int | None]:
    """Read only the bounded documented numeric codes from an error response."""
    try:
        payload: Any = response.json()
    except ValueError:
        return None, None
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return None, None
    code = error.get("code")
    subcode = error.get("error_subcode")
    return (
        code if isinstance(code, int) and not isinstance(code, bool) else None,
        subcode if isinstance(subcode, int) and not isinstance(subcode, bool) else None,
    )


def _retry_after(value: str | None) -> int | None:
    """Retain a bounded integer delay and discard every other header form."""
    try:
        result = int(value) if value is not None else None
    except ValueError:
        return None
    return result if result is not None and 0 <= result <= 86_400 else None


def _safe_failure_code(value: object) -> str:
    """Keep only the bounded numeric provider reason from a free-form status line."""
    if not isinstance(value, str):
        return "instagram_container_failed"
    for token in value.replace(":", " ").replace("-", " ").split():
        if token.isascii() and token.isdecimal() and 4 <= len(token) <= 10:
            return token
    return "instagram_container_failed"
