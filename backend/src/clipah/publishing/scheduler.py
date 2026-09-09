"""Bounded restart-safe claiming of scheduled Publications."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.models import Publication
from clipah.publishing.models import PublicationStatus
from clipah.publishing.outbox import PublicationOutboxService
from clipah.publishing.state_machine import transition


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
