"""Provider-neutral webhook intake that records evidence and wakes a worker."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.models import Publication, SocialAccount
from clipah.publishing.models import PublicationStatus
from clipah.publishing.outbox import PublicationOutboxService
from clipah.publishing.repository import PublicationRepository
from clipah.social_accounts.models import SocialProvider

RECONCILE_TOPIC = "publication.reconcile"

_RECONCILABLE = frozenset(
    {
        PublicationStatus.TRANSFERRING,
        PublicationStatus.PROCESSING,
        PublicationStatus.SCHEDULED,
        PublicationStatus.RETRYABLE_FAILED,
    }
)


class ProviderDeliveryUnknownAccountError(Exception):
    """The delivery names a destination account this deployment does not hold."""


@dataclass(frozen=True, slots=True)
class ProviderDelivery:
    """One already-verified provider delivery, reduced to fields safe to persist."""

    provider: SocialProvider
    external_account_id: str
    event_type: str
    provider_event_id: str | None
    provider_publication_id: str | None
    normalized_status: str | None
    payload: bytes
    signature_valid: bool
    received_at: datetime


@dataclass(frozen=True, slots=True)
class WebhookIntakeResult:
    """What intake durably did with one delivery, without acting on its contents."""

    event_id: UUID
    publication_id: UUID | None
    accepted: bool
    duplicate: bool
    reconcile_enqueued: bool


def record_provider_delivery(
    session: Session, *, delivery: ProviderDelivery
) -> WebhookIntakeResult:
    """Deduplicate one delivery, keep it as evidence, and queue reconciliation only."""
    account = _resolve_account(session, delivery=delivery)
    publication = _resolve_publication(session, account=account, delivery=delivery)
    repository = PublicationRepository(session)
    existing_ids = _known_event_ids(session, account=account, delivery=delivery)
    event = repository.record_provider_event(
        workspace_id=account.workspace_id,
        social_account_id=account.id,
        publication_id=publication.id if publication is not None else None,
        provider_event_id=delivery.provider_event_id,
        payload=delivery.payload,
        event_type=delivery.event_type,
        signature_valid=delivery.signature_valid,
        normalized_status=delivery.normalized_status,
        received_at=delivery.received_at,
    )
    duplicate = event.id in existing_ids
    enqueued = False
    if (
        delivery.signature_valid
        and not duplicate
        and publication is not None
        and publication.status in _RECONCILABLE
    ):
        PublicationOutboxService(session).enqueue(
            workspace_id=account.workspace_id,
            publication_id=publication.id,
            topic=RECONCILE_TOPIC,
            operation_key=f"{publication.provider_operation_key}:reconcile:{event.id}",
            available_at=delivery.received_at,
        )
        enqueued = True
    session.flush()
    return WebhookIntakeResult(
        event_id=event.id,
        publication_id=publication.id if publication is not None else None,
        accepted=delivery.signature_valid and not duplicate,
        duplicate=duplicate,
        reconcile_enqueued=enqueued,
    )


def _resolve_account(session: Session, *, delivery: ProviderDelivery) -> SocialAccount:
    """Find the one Social Account this provider identity belongs to."""
    accounts = tuple(
        session.scalars(
            select(SocialAccount).where(
                SocialAccount.provider == delivery.provider,
                SocialAccount.external_account_id == delivery.external_account_id,
            )
        )
    )
    if len(accounts) != 1:
        raise ProviderDeliveryUnknownAccountError("provider account is unavailable")
    return accounts[0]


def _resolve_publication(
    session: Session, *, account: SocialAccount, delivery: ProviderDelivery
) -> Publication | None:
    """Find the destination this delivery is about, inside that account only."""
    if delivery.provider_publication_id is None:
        return None
    return session.scalar(
        select(Publication).where(
            Publication.workspace_id == account.workspace_id,
            Publication.social_account_id == account.id,
            Publication.provider_publication_id == delivery.provider_publication_id,
        )
    )


def _known_event_ids(
    session: Session, *, account: SocialAccount, delivery: ProviderDelivery
) -> frozenset[UUID]:
    """Record which provider events already existed before this delivery arrived."""
    from clipah.models import ProviderEvent

    statement = select(ProviderEvent.id).where(
        ProviderEvent.workspace_id == account.workspace_id,
        ProviderEvent.social_account_id == account.id,
    )
    if delivery.provider_event_id is not None:
        statement = statement.where(ProviderEvent.provider_event_id == delivery.provider_event_id)
    else:
        statement = statement.where(
            ProviderEvent.provider_event_id.is_(None),
            ProviderEvent.payload_hash == hashlib.sha256(delivery.payload).digest(),
            ProviderEvent.event_type == delivery.event_type,
        )
    return frozenset(session.scalars(statement))
