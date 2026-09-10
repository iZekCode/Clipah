"""Batch aggregation and provider-truth reconciliation for stuck destinations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.models import Publication, PublicationAttempt
from clipah.publishing.models import PublicationStatus
from clipah.publishing.state_machine import transition

STUCK_TRANSFERRING_AFTER = timedelta(hours=1)
STUCK_PROCESSING_AFTER = timedelta(hours=6)

_TERMINAL = frozenset(
    {
        PublicationStatus.PUBLISHED,
        PublicationStatus.PERMANENT_FAILED,
        PublicationStatus.CANCELLED,
    }
)
_FAILED = frozenset({PublicationStatus.PERMANENT_FAILED})


class BatchState(StrEnum):
    """What one convenience batch can truthfully say about its own destinations."""

    UNKNOWN = "unknown"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    PARTIALLY_FAILED = "partially_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class BatchAggregate:
    """One batch's state derived from its children without hiding partial success."""

    batch_id: UUID
    state: BatchState
    total: int
    published: int
    failed: int
    cancelled: int
    pending: int


class ObservedState(StrEnum):
    """What an operator or worker actually saw at the provider."""

    PUBLISHED = "published"
    PROCESSING = "processing"
    FAILED = "failed"
    ABSENT = "absent"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProviderObservation:
    """One recorded look at provider truth for exactly one destination."""

    publication_id: UUID
    state: ObservedState
    provider_publication_id: str | None = None
    failure_code: str | None = None


class ProviderTruthObserver(Protocol):
    """Read authoritative provider state without changing anything durable."""

    def observe(self, *, publication: Publication, now: datetime) -> ProviderObservation:
        """Return what the provider currently reports for one destination."""


class ReconciliationRefusedError(Exception):
    """A state change was attempted without an observation that justifies it."""


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """What reconciliation decided for one destination, and why."""

    publication_id: UUID
    observed: ObservedState
    previous_status: PublicationStatus
    status: PublicationStatus
    changed: bool


def aggregate_batch(session: Session, *, workspace_id: UUID, batch_id: UUID) -> BatchAggregate:
    """Summarize one batch from its children, inside its Workspace boundary only."""
    publications = tuple(
        session.scalars(
            select(Publication).where(
                Publication.workspace_id == workspace_id,
                Publication.batch_id == batch_id,
            )
        )
    )
    total = len(publications)
    published = sum(1 for item in publications if item.status is PublicationStatus.PUBLISHED)
    failed = sum(1 for item in publications if item.status in _FAILED)
    cancelled = sum(1 for item in publications if item.status is PublicationStatus.CANCELLED)
    pending = sum(1 for item in publications if item.status not in _TERMINAL)
    if total == 0:
        state = BatchState.UNKNOWN
    elif pending:
        state = BatchState.IN_PROGRESS
    elif published == total:
        state = BatchState.COMPLETED
    elif cancelled == total:
        state = BatchState.CANCELLED
    elif published:
        state = BatchState.PARTIALLY_FAILED
    elif failed:
        state = BatchState.FAILED
    else:
        state = BatchState.PARTIALLY_FAILED
    return BatchAggregate(
        batch_id=batch_id,
        state=state,
        total=total,
        published=published,
        failed=failed,
        cancelled=cancelled,
        pending=pending,
    )


def find_stuck_publications(
    session: Session,
    *,
    now: datetime,
    limit: int,
    transferring_after: timedelta = STUCK_TRANSFERRING_AFTER,
    processing_after: timedelta = STUCK_PROCESSING_AFTER,
) -> tuple[Publication, ...]:
    """Claim a bounded page of destinations that have waited longer than they should."""
    if limit < 1:
        return ()
    return tuple(
        session.scalars(
            select(Publication)
            .where(
                (
                    (Publication.status == PublicationStatus.TRANSFERRING)
                    & (Publication.dispatched_at.is_not(None))
                    & (Publication.dispatched_at <= now - transferring_after)
                )
                | (
                    (Publication.status == PublicationStatus.PROCESSING)
                    & (Publication.processing_at.is_not(None))
                    & (Publication.processing_at <= now - processing_after)
                )
            )
            .order_by(Publication.created_at, Publication.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )


def reconcile_publication(
    session: Session,
    *,
    publication: Publication,
    observation: ProviderObservation,
    now: datetime,
) -> ReconciliationResult:
    """Apply provider truth to one stuck destination, and nothing that was not observed."""
    if observation.publication_id != publication.id:
        raise ReconciliationRefusedError("observation names another destination")
    previous = publication.status
    if previous in _TERMINAL:
        return ReconciliationResult(
            publication_id=publication.id,
            observed=observation.state,
            previous_status=previous,
            status=previous,
            changed=False,
        )
    if observation.state in {ObservedState.UNKNOWN, ObservedState.PROCESSING}:
        _record(session, publication=publication, observation=observation, now=now)
        return ReconciliationResult(
            publication_id=publication.id,
            observed=observation.state,
            previous_status=previous,
            status=previous,
            changed=False,
        )
    if observation.state is ObservedState.PUBLISHED:
        if previous is PublicationStatus.TRANSFERRING:
            publication.status = transition(
                current=publication.status, target=PublicationStatus.PROCESSING
            ).current
            publication.processing_at = publication.processing_at or now
        if observation.provider_publication_id is not None:
            publication.provider_publication_id = observation.provider_publication_id
        publication.status = transition(
            current=publication.status, target=PublicationStatus.PUBLISHED
        ).current
        publication.published_at = now
        publication.normalized_error_code = None
        publication.sanitized_error_message = None
    else:
        publication.status = transition(
            current=publication.status, target=PublicationStatus.PERMANENT_FAILED
        ).current
        publication.normalized_error_code = observation.failure_code or (
            "provider_absent" if observation.state is ObservedState.ABSENT else "provider_failed"
        )
        publication.sanitized_error_message = "The provider did not publish this video."
        publication.failed_at = now
    checkpoint = dict(publication.checkpoint_metadata or {})
    checkpoint["reconciled"] = True
    publication.checkpoint_metadata = checkpoint
    _record(session, publication=publication, observation=observation, now=now)
    session.flush()
    return ReconciliationResult(
        publication_id=publication.id,
        observed=observation.state,
        previous_status=previous,
        status=publication.status,
        changed=publication.status is not previous,
    )


def reconcile_stuck_publications(
    session: Session,
    *,
    observer: ProviderTruthObserver,
    now: datetime,
    limit: int,
    transferring_after: timedelta = STUCK_TRANSFERRING_AFTER,
    processing_after: timedelta = STUCK_PROCESSING_AFTER,
) -> tuple[ReconciliationResult, ...]:
    """Observe provider truth for a bounded page of stuck destinations, then apply it."""
    stuck = find_stuck_publications(
        session,
        now=now,
        limit=limit,
        transferring_after=transferring_after,
        processing_after=processing_after,
    )
    results: list[ReconciliationResult] = []
    for publication in stuck:
        observation = observer.observe(publication=publication, now=now)
        results.append(
            reconcile_publication(
                session, publication=publication, observation=observation, now=now
            )
        )
    return tuple(results)


def operator_reconcile(
    session: Session,
    *,
    workspace_id: UUID,
    publication_id: UUID,
    observer: ProviderTruthObserver,
    now: datetime,
) -> ReconciliationResult:
    """Let an operator unstick one destination only from what the provider reports."""
    publication = session.scalar(
        select(Publication)
        .where(
            Publication.workspace_id == workspace_id,
            Publication.id == publication_id,
        )
        .with_for_update()
    )
    if publication is None:
        raise ReconciliationRefusedError("destination is unavailable")
    if publication.status not in {
        PublicationStatus.TRANSFERRING,
        PublicationStatus.PROCESSING,
        PublicationStatus.RETRYABLE_FAILED,
    }:
        raise ReconciliationRefusedError("destination is not awaiting reconciliation")
    observation = observer.observe(publication=publication, now=now)
    if observation.state is ObservedState.UNKNOWN:
        raise ReconciliationRefusedError("provider truth was not observed")
    if publication.status is PublicationStatus.RETRYABLE_FAILED:
        return _reconcile_retryable(
            session, publication=publication, observation=observation, now=now
        )
    return reconcile_publication(session, publication=publication, observation=observation, now=now)


def _reconcile_retryable(
    session: Session,
    *,
    publication: Publication,
    observation: ProviderObservation,
    now: datetime,
) -> ReconciliationResult:
    """Resolve an ambiguous failed attempt without letting a retry duplicate a post."""
    previous = publication.status
    checkpoint = dict(publication.checkpoint_metadata or {})
    checkpoint["reconciled"] = True
    if observation.state is ObservedState.PUBLISHED:
        checkpoint["ambiguous"] = False
        publication.checkpoint_metadata = checkpoint
        publication.status = transition(
            current=publication.status, target=PublicationStatus.PREFLIGHTING
        ).current
        publication.status = transition(
            current=publication.status, target=PublicationStatus.TRANSFERRING
        ).current
        if observation.provider_publication_id is not None:
            publication.provider_publication_id = observation.provider_publication_id
        publication.status = transition(
            current=publication.status, target=PublicationStatus.PROCESSING
        ).current
        publication.processing_at = now
        publication.status = transition(
            current=publication.status, target=PublicationStatus.PUBLISHED
        ).current
        publication.published_at = now
        publication.normalized_error_code = None
        publication.sanitized_error_message = None
    else:
        publication.checkpoint_metadata = checkpoint
    _record(session, publication=publication, observation=observation, now=now)
    session.flush()
    return ReconciliationResult(
        publication_id=publication.id,
        observed=observation.state,
        previous_status=previous,
        status=publication.status,
        changed=publication.status is not previous,
    )


def _record(
    session: Session,
    *,
    publication: Publication,
    observation: ProviderObservation,
    now: datetime,
) -> None:
    """Append one bounded secret-free reconciliation observation exactly once."""
    stage = f"reconcile_{observation.state.value}"
    attempt_number = publication.attempt_count + 1
    existing = session.scalar(
        select(PublicationAttempt.id).where(
            PublicationAttempt.workspace_id == publication.workspace_id,
            PublicationAttempt.publication_id == publication.id,
            PublicationAttempt.attempt == attempt_number,
            PublicationAttempt.stage == stage,
        )
    )
    if existing is not None:
        return
    session.add(
        PublicationAttempt(
            workspace_id=publication.workspace_id,
            publication_id=publication.id,
            attempt=attempt_number,
            stage=stage,
            request_metadata={"operation": "reconcile"},
            response_metadata={"observed": observation.state.value},
            error_code=observation.failure_code,
            started_at=now,
            finished_at=now,
            created_at=now,
        )
    )
