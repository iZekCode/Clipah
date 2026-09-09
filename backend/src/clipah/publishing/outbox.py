"""Transactional Publication outbox persistence and delivery acknowledgement."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.models import PublicationOutbox


class PublicationOutboxService:
    """Write and read restart-safe scalar-only dispatch messages."""

    def __init__(self, session: Session) -> None:
        """Bind one transaction without owning its commit boundary."""
        self.session = session

    def enqueue(
        self,
        *,
        workspace_id: UUID,
        publication_id: UUID,
        topic: str,
        operation_key: str,
        available_at: datetime,
    ) -> PublicationOutbox:
        """Create one operation message or return the existing durable intent."""
        existing = self.session.scalar(
            select(PublicationOutbox).where(
                PublicationOutbox.workspace_id == workspace_id,
                PublicationOutbox.operation_key == operation_key,
            )
        )
        if existing is not None:
            return existing
        message = PublicationOutbox(
            id=uuid4(),
            workspace_id=workspace_id,
            publication_id=publication_id,
            topic=topic,
            payload={
                "workspace_id": str(workspace_id),
                "publication_id": str(publication_id),
            },
            operation_key=operation_key,
            available_at=available_at,
            claimed_at=None,
            delivered_at=None,
            attempt_count=0,
            created_at=available_at,
        )
        self.session.add(message)
        self.session.flush()
        return message

    def pending(self, *, now: datetime, limit: int) -> tuple[PublicationOutbox, ...]:
        """Return a bounded stable page while leaving unacknowledged work recoverable."""
        if limit < 1:
            return ()
        return tuple(
            self.session.scalars(
                select(PublicationOutbox)
                .where(
                    PublicationOutbox.delivered_at.is_(None),
                    PublicationOutbox.available_at <= now,
                )
                .order_by(PublicationOutbox.available_at, PublicationOutbox.id)
                .limit(limit)
            )
        )

    def acknowledge(self, *, message_id: UUID, delivered_at: datetime) -> None:
        """Mark one successfully handed-off message delivered exactly once."""
        message = self.session.scalar(
            select(PublicationOutbox).where(PublicationOutbox.id == message_id).with_for_update()
        )
        if message is None or message.delivered_at is not None:
            return
        message.delivered_at = delivered_at
        message.attempt_count += 1
        self.session.flush()
