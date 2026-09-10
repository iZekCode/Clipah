"""Tenant-scoped persistence for Publications and append-only provider evidence."""

from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.models import ProviderEvent, Publication, PublicationAttempt, SocialAccount

UNKNOWN_PROVIDER = "unknown"


def publication_provider(session: Session, publication: Publication) -> str:
    """Name the provider one destination belongs to, for telemetry that must not fail.

    A Publication records the Social Account rather than the provider, so this reads the
    account. It answers ``unknown`` instead of raising, because a missing account is a
    problem for dispatch to refuse and never a reason to lose a metric.
    """
    provider = session.scalar(
        select(SocialAccount.provider).where(
            SocialAccount.workspace_id == publication.workspace_id,
            SocialAccount.id == publication.social_account_id,
        )
    )
    return UNKNOWN_PROVIDER if provider is None else str(provider)


class PublicationRepository:
    """Keep every Publication query explicit about its Workspace boundary."""

    def __init__(self, session: Session) -> None:
        """Bind one transaction without owning its commit boundary."""
        self.session = session

    def publication(
        self, *, workspace_id: UUID, publication_id: UUID, for_update: bool = False
    ) -> Publication | None:
        """Find one destination only inside its Workspace, optionally locking it."""
        statement = select(Publication).where(
            Publication.workspace_id == workspace_id,
            Publication.id == publication_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def append_attempt(
        self,
        *,
        workspace_id: UUID,
        publication_id: UUID,
        attempt: int,
        stage: str,
        started_at: datetime,
        provider_request_id: str | None = None,
        byte_checkpoint: int | None = None,
        request_metadata: dict[str, object] | None = None,
        response_metadata: dict[str, object] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        finished_at: datetime | None = None,
    ) -> PublicationAttempt:
        """Append one secret-free stage observation to immutable history."""
        record = PublicationAttempt(
            id=uuid4(),
            workspace_id=workspace_id,
            publication_id=publication_id,
            attempt=attempt,
            stage=stage,
            provider_request_id=provider_request_id,
            byte_checkpoint=byte_checkpoint,
            request_metadata=request_metadata or {},
            response_metadata=response_metadata or {},
            error_code=error_code,
            error_message=error_message,
            started_at=started_at,
            finished_at=finished_at,
            created_at=started_at,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def record_provider_event(
        self,
        *,
        workspace_id: UUID,
        social_account_id: UUID,
        publication_id: UUID | None,
        provider_event_id: str | None,
        payload: bytes,
        event_type: str,
        signature_valid: bool,
        normalized_status: str | None,
        received_at: datetime,
        encrypted_raw_payload_reference: str | None = None,
    ) -> ProviderEvent:
        """Persist provider evidence once by authoritative ID or stable payload digest."""
        payload_hash = hashlib.sha256(payload).digest()
        statement = select(ProviderEvent).where(
            ProviderEvent.workspace_id == workspace_id,
            ProviderEvent.social_account_id == social_account_id,
        )
        if provider_event_id is not None:
            statement = statement.where(ProviderEvent.provider_event_id == provider_event_id)
        else:
            statement = statement.where(
                ProviderEvent.provider_event_id.is_(None),
                ProviderEvent.payload_hash == payload_hash,
                ProviderEvent.event_type == event_type,
            )
        existing = self.session.scalar(statement)
        if existing is not None:
            return existing
        event = ProviderEvent(
            id=uuid4(),
            workspace_id=workspace_id,
            social_account_id=social_account_id,
            publication_id=publication_id,
            provider_event_id=provider_event_id,
            payload_hash=payload_hash,
            event_type=event_type,
            signature_valid=signature_valid,
            normalized_status=normalized_status,
            received_at=received_at,
            processed_at=None,
            encrypted_raw_payload_reference=encrypted_raw_payload_reference,
        )
        self.session.add(event)
        self.session.flush()
        return event
