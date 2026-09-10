"""Bounded restart-safe claiming of scheduled Publications."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.models import Publication
from clipah.observability.logging import get_logger, log_context
from clipah.observability.metrics import observe
from clipah.publishing.models import PublicationStatus
from clipah.publishing.outbox import PublicationOutboxService
from clipah.publishing.repository import publication_provider
from clipah.publishing.state_machine import transition

_logger = get_logger(__name__)


class PublicationScheduler:
    """Claim due destination work inside the caller's tenant-scoped transaction."""

    def __init__(self, session: Session) -> None:
        """Bind the transaction that will also hold outbox writes."""
        self._session = session

    def claim_due(self, *, now: datetime, limit: int) -> list[UUID]:
        """Lock a bounded due page with skip-locked and record dispatch atomically."""
        if limit < 1:
            return []
        publications = tuple(
            self._session.scalars(
                select(Publication)
                .where(
                    Publication.status == PublicationStatus.SCHEDULED,
                    Publication.scheduled_for.is_not(None),
                    Publication.scheduled_for <= now,
                )
                .order_by(Publication.scheduled_for, Publication.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        outbox = PublicationOutboxService(self._session)
        for publication in publications:
            _record_claim(self._session, publication, now=now)
            publication.status = transition(
                current=publication.status, target=PublicationStatus.PREFLIGHTING
            ).current
            outbox.enqueue(
                workspace_id=publication.workspace_id,
                publication_id=publication.id,
                topic="publication.preflight",
                operation_key=(
                    f"{publication.provider_operation_key}:attempt:{publication.attempt_count}"
                ),
                available_at=now,
            )
        self._session.flush()
        return [publication.id for publication in publications]


def _record_claim(session: Session, publication: Publication, *, now: datetime) -> None:
    """Measure how late the scheduler was to a destination that was already due.

    Lateness here is the difference between a member's chosen time and the time their
    video actually starts being published, which is the only latency they can see.
    """
    scheduled_for = publication.scheduled_for
    if scheduled_for is None:
        return
    late_ms = (now - scheduled_for).total_seconds() * 1000
    provider = publication_provider(session, publication)
    observe("clipah.scheduler.latency", max(late_ms, 0.0), provider=provider)
    with log_context(
        workspaceId=publication.workspace_id,
        publicationId=publication.id,
        batchId=publication.batch_id,
    ):
        _logger.info(
            "publication.claimed",
            provider=provider,
            latencyMs=round(late_ms, 3),
            attempt=publication.attempt_count,
        )
