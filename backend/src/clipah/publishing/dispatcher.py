"""Bounded, revalidated, quota-aware dispatch of one Publication to its provider."""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from clipah.jobs.admission import QuotaExceededError, QuotaLedger
from clipah.models import (
    Publication,
    QuotaReservationStatus,
    QuotaResource,
    SocialRendition,
    WorkspaceQuotaReservation,
)
from clipah.observability.logging import get_logger, log_context
from clipah.observability.metrics import record_publication_outcome
from clipah.publishing.models import PublicationStatus
from clipah.publishing.outbox import PublicationOutboxService
from clipah.publishing.repository import publication_provider
from clipah.publishing.state_machine import transition
from clipah.publishing.tasks import (
    PublicationDispatchInvalidError,
    revalidate_publication_dispatch,
)
from clipah.social_accounts.models import SocialProvider

# One stable namespace keeps this advisory lock from colliding with admission or quota.
ACCOUNT_LOCK_NAMESPACE = 0x0C11_9A09
BASE_BACKOFF = timedelta(seconds=30)
MAX_BACKOFF = timedelta(hours=1)
ACCOUNT_BUSY_BACKOFF = timedelta(seconds=15)
PUBLICATION_QUOTA_UNITS = Decimal(1)
QUOTA_REFERENCE_KIND = "publication"
_logger = get_logger(__name__)

_DISPATCHABLE = PublicationStatus.PREFLIGHTING


class FailureKind(StrEnum):
    """How one provider refusal must be treated by retry policy."""

    RETRYABLE = "retryable"
    RECONNECT = "reconnect"
    PERMANENT = "permanent"
    AMBIGUOUS = "ambiguous"


class DispatchOutcome(StrEnum):
    """The complete set of ways one dispatch attempt can end."""

    DELIVERED = "delivered"
    NOT_DUE = "not_due"
    NOT_DISPATCHABLE = "not_dispatchable"
    ACCOUNT_BUSY = "account_busy"
    RETRYABLE = "retryable"
    RECONNECT_REQUIRED = "reconnect_required"
    PERMANENT_FAILED = "permanent_failed"
    AMBIGUOUS = "ambiguous"


class DeliveryFailedError(Exception):
    """One classified, secret-free provider refusal a driver hands to the dispatcher."""

    def __init__(self, *, code: str, kind: FailureKind, retry_after: int | None = None) -> None:
        """Retain only the stable code, its retry class, and any safe provider hint."""
        super().__init__(code)
        self.code = code
        self.kind = kind
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """The safe outcome of one dispatch attempt for one destination."""

    publication_id: UUID
    outcome: DispatchOutcome
    status: PublicationStatus
    next_attempt_at: datetime | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class DispatchPolicy:
    """Deployment policy every destination is dispatched under."""

    youtube_audit_approved: bool = False
    tiktok_direct_post_approved: bool = False
    max_attempts: int = 5


class DestinationDriver(Protocol):
    """One provider's side of dispatch, called only after every revalidation passed."""

    def deliver(self, *, session: Session, publication: Publication, now: datetime) -> None:
        """Perform provider work and record its success, or raise a `DeliveryFailedError`."""


def failure_from_provider_error(error: Exception) -> DeliveryFailedError:
    """Classify any adapter's sanitized error once, so retry policy stays identical."""
    from clipah.publishing.providers.instagram.containers import (
        InstagramPermanentError,
        InstagramProviderError,
        InstagramReconnectRequiredError,
    )
    from clipah.publishing.providers.tiktok.transfers import (
        TikTokPermanentError,
        TikTokProviderError,
        TikTokReconnectRequiredError,
    )
    from clipah.publishing.providers.youtube.status import (
        YouTubeAmbiguousStatusError,
        YouTubePermanentError,
        YouTubeProviderError,
        YouTubeReconnectRequiredError,
    )

    families = (
        (
            YouTubeReconnectRequiredError,
            InstagramReconnectRequiredError,
            TikTokReconnectRequiredError,
        ),
        (YouTubePermanentError, InstagramPermanentError, TikTokPermanentError),
        (YouTubeProviderError, InstagramProviderError, TikTokProviderError),
    )
    code = getattr(error, "code", "provider_error")
    retry_after = getattr(error, "retry_after", None)
    if isinstance(error, YouTubeAmbiguousStatusError):
        kind = FailureKind.AMBIGUOUS
    elif isinstance(error, families[0]):
        kind = FailureKind.RECONNECT
    elif isinstance(error, families[1]):
        kind = FailureKind.PERMANENT
    elif isinstance(error, families[2]):
        kind = FailureKind.RETRYABLE
    else:
        kind = FailureKind.PERMANENT
    return DeliveryFailedError(
        code=str(code),
        kind=kind,
        retry_after=retry_after if isinstance(retry_after, int) else None,
    )


def backoff_delay(*, attempt: int, retry_after: int | None, jitter: float) -> timedelta:
    """Wait longer after each failure, never less than the provider's own hint."""
    exponent = min(max(attempt, 0), 20)
    computed = BASE_BACKOFF * (2**exponent)
    if retry_after is not None and retry_after > 0:
        hinted = timedelta(seconds=retry_after)
        if hinted >= computed:
            return min(hinted, MAX_BACKOFF)
    bounded = min(computed, MAX_BACKOFF)
    return timedelta(seconds=bounded.total_seconds() * min(max(jitter, 0.0), 1.0))


class PublicationDispatcher:
    """Drive one destination through revalidation, locking, quota, and provider work."""

    def __init__(
        self,
        session: Session,
        *,
        drivers: Mapping[SocialProvider, DestinationDriver],
        quota: QuotaLedger,
        policy: DispatchPolicy,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        """Bind one open transaction, the provider drivers, and deterministic policy."""
        self._session = session
        self._drivers = drivers
        self._quota = quota
        self._policy = policy
        self._jitter = jitter

    def dispatch(
        self, *, workspace_id: UUID, publication_id: UUID, now: datetime
    ) -> DispatchResult:
        """Deliver one destination exactly once, or explain safely why it did not."""
        publication = self._session.scalar(
            select(Publication)
            .where(
                Publication.workspace_id == workspace_id,
                Publication.id == publication_id,
            )
            .with_for_update()
        )
        if publication is None:
            raise PublicationDispatchInvalidError(str(publication_id))
        if publication.status is not _DISPATCHABLE:
            return _result(publication, DispatchOutcome.NOT_DISPATCHABLE)
        if publication.scheduled_for is not None and publication.scheduled_for > now:
            return _result(publication, DispatchOutcome.NOT_DUE)
        if not self._claim_account(publication):
            return _result(
                publication,
                DispatchOutcome.ACCOUNT_BUSY,
                next_attempt_at=now + ACCOUNT_BUSY_BACKOFF,
            )

        summary = revalidate_publication_dispatch(
            self._session,
            workspace_id=workspace_id,
            publication_id=publication_id,
            now=now,
            youtube_audit_approved=self._policy.youtube_audit_approved,
            tiktok_direct_post_approved=self._policy.tiktok_direct_post_approved,
        )
        if summary.status is not _DISPATCHABLE:
            self._session.refresh(publication)
            self._release_quota(publication, now=now)
            return _result(publication, DispatchOutcome.NOT_DISPATCHABLE)

        frozen = self._frozen_evidence_failure(publication)
        if frozen is not None:
            return self._apply_failure(publication, failure=frozen, now=now)
        if publication.attempt_count >= self._policy.max_attempts:
            return self._apply_failure(
                publication,
                failure=DeliveryFailedError(
                    code="retry_budget_exhausted", kind=FailureKind.PERMANENT
                ),
                now=now,
            )

        try:
            self._reserve_quota(publication, now=now)
        except QuotaExceededError as error:
            return self._apply_failure(
                publication,
                failure=DeliveryFailedError(
                    code="publication_quota_exceeded",
                    kind=FailureKind.RETRYABLE,
                    retry_after=int(error.retry_after.total_seconds()),
                ),
                now=now,
            )

        driver = self._drivers.get(_account_provider(self._session, publication))
        if driver is None:
            return self._apply_failure(
                publication,
                failure=DeliveryFailedError(
                    code="provider_unsupported", kind=FailureKind.PERMANENT
                ),
                now=now,
            )
        try:
            driver.deliver(session=self._session, publication=publication, now=now)
        except DeliveryFailedError as failure:
            return self._apply_failure(publication, failure=failure, now=now)
        self._settle_quota(publication, now=now)
        self._session.flush()
        _record_outcome(
            self._session, publication, outcome=DispatchOutcome.DELIVERED.value, now=now
        )
        return _result(publication, DispatchOutcome.DELIVERED)

    def _claim_account(self, publication: Publication) -> bool:
        """Hold one Social Account's provider work for the life of this transaction."""
        claimed = self._session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:namespace, hashtext(:subject))"),
            {
                "namespace": ACCOUNT_LOCK_NAMESPACE,
                "subject": f"{publication.workspace_id}:{publication.social_account_id}",
            },
        )
        return bool(claimed)

    def _frozen_evidence_failure(self, publication: Publication) -> DeliveryFailedError | None:
        """Reprove the exact bytes and consent this destination was approved with."""
        if publication.consent_snapshot.get("confirmed") is not True:
            return DeliveryFailedError(code="consent_evidence_missing", kind=FailureKind.PERMANENT)
        rendition = self._session.scalar(
            select(SocialRendition).where(
                SocialRendition.workspace_id == publication.workspace_id,
                SocialRendition.id == publication.social_rendition_id,
            )
        )
        if rendition is None or bytes(rendition.output_sha256) != bytes(
            publication.artifact_sha256
        ):
            return DeliveryFailedError(
                code="rendition_checksum_mismatch", kind=FailureKind.PERMANENT
            )
        return None

    def _apply_failure(
        self, publication: Publication, *, failure: DeliveryFailedError, now: datetime
    ) -> DispatchResult:
        """Move one destination onto the truthful state its failure kind names."""
        if failure.kind is FailureKind.RECONNECT:
            target = PublicationStatus.RECONNECT_REQUIRED
            outcome = DispatchOutcome.RECONNECT_REQUIRED
            message = "Reconnect this Social Account before retrying."
        elif failure.kind is FailureKind.PERMANENT:
            target = PublicationStatus.PERMANENT_FAILED
            outcome = DispatchOutcome.PERMANENT_FAILED
            message = "The provider rejected this publication."
        else:
            target = PublicationStatus.RETRYABLE_FAILED
            outcome = (
                DispatchOutcome.AMBIGUOUS
                if failure.kind is FailureKind.AMBIGUOUS
                else DispatchOutcome.RETRYABLE
            )
            message = "Publishing is temporarily unavailable."
        publication.status = transition(current=publication.status, target=target).current
        publication.normalized_error_code = failure.code
        publication.sanitized_error_message = message
        next_attempt_at: datetime | None = None
        if failure.kind is FailureKind.AMBIGUOUS:
            checkpoint = dict(publication.checkpoint_metadata or {})
            checkpoint["ambiguous"] = True
            checkpoint.pop("reconciled", None)
            publication.checkpoint_metadata = checkpoint
        elif failure.kind is FailureKind.RETRYABLE:
            next_attempt_at = now + backoff_delay(
                attempt=publication.attempt_count,
                retry_after=failure.retry_after,
                jitter=self._jitter(),
            )
            publication.next_attempt_at = next_attempt_at
            PublicationOutboxService(self._session).enqueue(
                workspace_id=publication.workspace_id,
                publication_id=publication.id,
                topic="publication.preflight",
                operation_key=(
                    f"{publication.provider_operation_key}:retry:{publication.attempt_count + 1}"
                ),
                available_at=next_attempt_at,
            )
        if target is PublicationStatus.PERMANENT_FAILED:
            publication.failed_at = now
        # A retryable attempt will run again and still costs one publication, and an
        # ambiguous one may already have spent it, so only terminal refusals give it back.
        if failure.kind in {FailureKind.RECONNECT, FailureKind.PERMANENT}:
            self._release_quota(publication, now=now)
        self._session.flush()
        _record_outcome(
            self._session, publication, outcome=outcome.value, now=now, code=failure.code
        )
        return _result(
            publication,
            outcome,
            next_attempt_at=next_attempt_at,
            error_code=failure.code,
        )

    def _existing_reservation(self, publication: Publication) -> WorkspaceQuotaReservation | None:
        """Find the single reservation this destination may already hold."""
        return self._session.scalar(
            select(WorkspaceQuotaReservation).where(
                WorkspaceQuotaReservation.workspace_id == publication.workspace_id,
                WorkspaceQuotaReservation.resource == QuotaResource.SOCIAL_PUBLICATIONS,
                WorkspaceQuotaReservation.reference_kind == QUOTA_REFERENCE_KIND,
                WorkspaceQuotaReservation.reference_id == publication.id,
            )
        )

    def _reserve_quota(self, publication: Publication, *, now: datetime) -> None:
        """Hold exactly one publication unit for the life of this destination."""
        if self._existing_reservation(publication) is not None:
            return
        self._quota.reserve(
            workspace_id=publication.workspace_id,
            resource=QuotaResource.SOCIAL_PUBLICATIONS,
            units=PUBLICATION_QUOTA_UNITS,
            reference_kind=QUOTA_REFERENCE_KIND,
            reference_id=publication.id,
            now=now,
        )

    def _settle_quota(self, publication: Publication, *, now: datetime) -> None:
        """Charge the one unit a delivered destination actually spent."""
        self._quota.settle_if_reserved(
            workspace_id=publication.workspace_id,
            resource=QuotaResource.SOCIAL_PUBLICATIONS,
            reference_kind=QUOTA_REFERENCE_KIND,
            reference_id=publication.id,
            actual_units=PUBLICATION_QUOTA_UNITS,
            now=now,
        )

    def _release_quota(self, publication: Publication, *, now: datetime) -> None:
        """Return the whole estimate a destination that never published still holds."""
        reservation = self._existing_reservation(publication)
        if reservation is None or reservation.status is not QuotaReservationStatus.RESERVED:
            return
        self._quota.release_if_reserved(
            workspace_id=publication.workspace_id,
            resource=QuotaResource.SOCIAL_PUBLICATIONS,
            reference_kind=QUOTA_REFERENCE_KIND,
            reference_id=publication.id,
            now=now,
        )


def _account_provider(session: Session, publication: Publication) -> SocialProvider:
    """Read the provider of the Social Account this destination is bound to."""
    from clipah.models import SocialAccount

    provider = session.scalar(
        select(SocialAccount.provider).where(
            SocialAccount.workspace_id == publication.workspace_id,
            SocialAccount.id == publication.social_account_id,
        )
    )
    if provider is None:
        raise PublicationDispatchInvalidError("Social Account is unavailable")
    return provider


def _record_outcome(
    session: Session,
    publication: Publication,
    *,
    outcome: str,
    now: datetime,
    code: str | None = None,
) -> None:
    """Report one dispatch attempt's end, and how long an approved member waited for it."""
    approved_at = publication.approved_at
    seconds = None
    if publication.status is PublicationStatus.PUBLISHED and approved_at is not None:
        seconds = (now - approved_at).total_seconds()
    provider = publication_provider(session, publication)
    record_publication_outcome(
        provider=provider,
        outcome=outcome,
        code=code,
        seconds_to_publish=seconds,
    )
    with log_context(
        workspaceId=publication.workspace_id,
        publicationId=publication.id,
        batchId=publication.batch_id,
    ):
        _logger.info(
            "publication.attempt.finished",
            provider=provider,
            outcome=outcome,
            code=code or "none",
            status=publication.status.value,
            attempt=publication.attempt_count,
        )


def _result(
    publication: Publication,
    outcome: DispatchOutcome,
    *,
    next_attempt_at: datetime | None = None,
    error_code: str | None = None,
) -> DispatchResult:
    """Detach the safe dispatch outcome from its locked ORM row."""
    return DispatchResult(
        publication_id=publication.id,
        outcome=outcome,
        status=publication.status,
        next_attempt_at=next_attempt_at,
        error_code=error_code,
    )
