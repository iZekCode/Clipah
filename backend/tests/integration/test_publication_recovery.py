"""Real-Postgres contracts for webhook intake, reconciliation, and restart recovery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from clipah.models import ProviderEvent, Publication, PublicationOutbox
from clipah.publishing.models import PublicationStatus
from clipah.publishing.outbox import PublicationOutboxService
from clipah.publishing.reconciler import (
    ObservedState,
    ProviderObservation,
    ReconciliationRefusedError,
    find_stuck_publications,
    operator_reconcile,
    reconcile_stuck_publications,
)
from clipah.publishing.scheduler import PublicationScheduler
from clipah.publishing.use_cases import retry_publication
from clipah.publishing.webhooks import (
    ProviderDelivery,
    ProviderDeliveryUnknownAccountError,
    record_provider_delivery,
)
from clipah.social_accounts.models import SocialProvider
from clipah.workspaces.models import (
    PublishingRolePolicy,
    WorkspaceAccess,
    WorkspaceRole,
)
from integration.test_publication_dispatch import _seed_multi_destination_batch

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 10, 5, 0, tzinfo=UTC)


class _Observer:
    """Report one scripted provider truth and record every destination it was asked about."""

    def __init__(self, state: ObservedState, **fields: object) -> None:
        """Start with the state this observer will always report."""
        self.state = state
        self.fields = fields
        self.observed: list[Any] = []

    def observe(self, *, publication: Publication, now: datetime) -> ProviderObservation:
        """Return the scripted observation for exactly the destination asked about."""
        del now
        self.observed.append(publication.id)
        return ProviderObservation(
            publication_id=publication.id,
            state=self.state,
            provider_publication_id=self.fields.get("provider_publication_id"),  # type: ignore[arg-type]
            failure_code=self.fields.get("failure_code"),  # type: ignore[arg-type]
        )


def _set_status(
    engine: Engine,
    publication_id: Any,
    *,
    status: str,
    dispatched_at: datetime | None = None,
    processing_at: datetime | None = None,
    provider_publication_id: str | None = None,
) -> None:
    """Move one destination to the durable state a recovery scenario starts from."""
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE publications
                SET status = :status, dispatched_at = :dispatched_at,
                    processing_at = :processing_at,
                    provider_publication_id = :provider_publication_id
                WHERE id = :id
                """
            ),
            {
                "id": publication_id,
                "status": status,
                "dispatched_at": dispatched_at,
                "processing_at": processing_at,
                "provider_publication_id": provider_publication_id,
            },
        )


def _access(seed: dict[str, Any]) -> WorkspaceAccess:
    """Return the owner standing provisioned by the seed graph."""
    return WorkspaceAccess(
        workspace_id=seed["workspace"],
        user_id=seed["user"],
        role=WorkspaceRole.OWNER,
        publishing_role_policy=PublishingRolePolicy.OWNER_ADMIN_EDITOR,
    )


def _delivery(seed: dict[str, Any], **changes: Any) -> ProviderDelivery:
    """Build one verified provider delivery for the seeded YouTube destination."""
    values: dict[str, Any] = {
        "provider": SocialProvider.YOUTUBE,
        "external_account_id": f"channel-{seed['suffix']}",
        "event_type": "publication.status",
        "provider_event_id": "provider-event-1",
        "provider_publication_id": "provider-video-1",
        "normalized_status": "published",
        "payload": b'{"status":"published"}',
        "signature_valid": True,
        "received_at": NOW,
    }
    values.update(changes)
    return ProviderDelivery(**values)


def _seed(engine: Engine, suffix: str, **kwargs: Any) -> dict[str, Any]:
    """Seed one batch and remember the suffix its external account id was built from."""
    seed = _seed_multi_destination_batch(engine, suffix=suffix, **kwargs)
    return {**seed, "suffix": suffix}


# --- Stuck-state reconciliation -----------------------------------------------------


def test_only_destinations_stuck_past_their_threshold_are_claimed(
    engine: Engine, clean_database: None
) -> None:
    """Reconciliation must not disturb work that is merely still in progress."""
    seed = _seed(engine, "recovery-threshold")
    stuck_id = seed["destinations"]["youtube"]
    fresh_id = seed["destinations"]["instagram"]
    _set_status(engine, stuck_id, status="transferring", dispatched_at=NOW - timedelta(hours=2))
    _set_status(engine, fresh_id, status="transferring", dispatched_at=NOW - timedelta(minutes=5))

    with Session(engine) as session, session.begin():
        claimed = [item.id for item in find_stuck_publications(session, now=NOW, limit=10)]

    assert claimed == [stuck_id]


def test_a_terminal_destination_is_never_reconciled(engine: Engine, clean_database: None) -> None:
    """A published destination must be immune to any later observation."""
    seed = _seed(engine, "recovery-terminal")
    publication_id = seed["destinations"]["youtube"]
    _set_status(
        engine,
        publication_id,
        status="processing",
        processing_at=NOW - timedelta(days=1),
    )
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE publications SET status = 'published' WHERE id = :id"),
            {"id": publication_id},
        )

    with Session(engine) as session, session.begin():
        results = reconcile_stuck_publications(
            session, observer=_Observer(ObservedState.FAILED), now=NOW, limit=10
        )

    assert results == ()
    with Session(engine) as session:
        publication = session.get(Publication, publication_id)
        assert publication is not None
        assert publication.status is PublicationStatus.PUBLISHED


def test_a_stuck_destination_the_provider_published_is_completed(
    engine: Engine, clean_database: None
) -> None:
    """A lost completion must be recovered from provider truth, not assumed."""
    seed = _seed(engine, "recovery-published")
    publication_id = seed["destinations"]["youtube"]
    _set_status(
        engine, publication_id, status="transferring", dispatched_at=NOW - timedelta(hours=2)
    )

    with Session(engine) as session, session.begin():
        results = reconcile_stuck_publications(
            session,
            observer=_Observer(ObservedState.PUBLISHED, provider_publication_id="provider-video-9"),
            now=NOW,
            limit=10,
        )

    assert len(results) == 1
    assert results[0].changed is True
    with Session(engine) as session:
        publication = session.get(Publication, publication_id)
        assert publication is not None
        assert publication.status is PublicationStatus.PUBLISHED
        assert publication.provider_publication_id == "provider-video-9"
        assert publication.published_at == NOW


def test_a_stuck_destination_the_provider_never_created_fails_permanently(
    engine: Engine, clean_database: None
) -> None:
    """A destination the provider has no record of must stop occupying capacity."""
    seed = _seed(engine, "recovery-absent")
    publication_id = seed["destinations"]["youtube"]
    _set_status(engine, publication_id, status="processing", processing_at=NOW - timedelta(days=1))

    with Session(engine) as session, session.begin():
        reconcile_stuck_publications(
            session, observer=_Observer(ObservedState.ABSENT), now=NOW, limit=10
        )

    with Session(engine) as session:
        publication = session.get(Publication, publication_id)
        assert publication is not None
        assert publication.status is PublicationStatus.PERMANENT_FAILED
        assert publication.normalized_error_code == "provider_absent"


def test_an_unreadable_provider_leaves_a_stuck_destination_exactly_as_it_was(
    engine: Engine, clean_database: None
) -> None:
    """An outage during reconciliation must never be mistaken for provider truth."""
    seed = _seed(engine, "recovery-unknown")
    publication_id = seed["destinations"]["youtube"]
    _set_status(engine, publication_id, status="processing", processing_at=NOW - timedelta(days=1))

    with Session(engine) as session, session.begin():
        results = reconcile_stuck_publications(
            session, observer=_Observer(ObservedState.UNKNOWN), now=NOW, limit=10
        )

    assert results[0].changed is False
    with Session(engine) as session:
        publication = session.get(Publication, publication_id)
        assert publication is not None
        assert publication.status is PublicationStatus.PROCESSING


def test_reconciliation_claims_a_bounded_page(engine: Engine, clean_database: None) -> None:
    """One recovery pass after an outage must not try to drain everything at once."""
    seed = _seed(engine, "recovery-bounded")
    for publication_id in seed["destinations"].values():
        _set_status(
            engine, publication_id, status="transferring", dispatched_at=NOW - timedelta(hours=2)
        )

    with Session(engine) as session, session.begin():
        claimed = [item.id for item in find_stuck_publications(session, now=NOW, limit=2)]

    assert len(claimed) == 2


# --- Operator command ---------------------------------------------------------------


def test_the_operator_command_refuses_to_change_state_without_an_observation(
    engine: Engine, clean_database: None
) -> None:
    """An operator must not be able to declare a state the provider did not confirm."""
    seed = _seed(engine, "operator-unknown")
    publication_id = seed["destinations"]["youtube"]
    _set_status(engine, publication_id, status="processing", processing_at=NOW)

    with (
        Session(engine) as session,
        session.begin(),
        pytest.raises(ReconciliationRefusedError),
    ):
        operator_reconcile(
            session,
            workspace_id=seed["workspace"],
            publication_id=publication_id,
            observer=_Observer(ObservedState.UNKNOWN),
            now=NOW,
        )


def test_the_operator_command_refuses_a_destination_that_is_not_stuck(
    engine: Engine, clean_database: None
) -> None:
    """Reconciliation is for stuck work, not a back door into any lifecycle state."""
    seed = _seed(engine, "operator-not-stuck")

    with (
        Session(engine) as session,
        session.begin(),
        pytest.raises(ReconciliationRefusedError),
    ):
        operator_reconcile(
            session,
            workspace_id=seed["workspace"],
            publication_id=seed["destinations"]["youtube"],
            observer=_Observer(ObservedState.PUBLISHED),
            now=NOW,
        )


def test_the_operator_command_cannot_reach_another_workspace(
    engine: Engine, clean_database: None
) -> None:
    """A guessed destination identifier must be indistinguishable from a missing one."""
    first = _seed(engine, "operator-tenant-a")
    second = _seed(engine, "operator-tenant-b")
    _set_status(engine, first["destinations"]["youtube"], status="processing", processing_at=NOW)

    with (
        Session(engine) as session,
        session.begin(),
        pytest.raises(ReconciliationRefusedError),
    ):
        operator_reconcile(
            session,
            workspace_id=second["workspace"],
            publication_id=first["destinations"]["youtube"],
            observer=_Observer(ObservedState.PUBLISHED),
            now=NOW,
        )


def test_an_ambiguous_destination_the_provider_published_is_resolved_not_retried(
    engine: Engine, clean_database: None
) -> None:
    """Reconciling an unproved delivery must adopt the post rather than repeat it."""
    seed = _seed(engine, "operator-ambiguous")
    publication_id = seed["destinations"]["youtube"]
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE publications SET status = 'retryable_failed', "
                "checkpoint_metadata = '{\"ambiguous\": true}' WHERE id = :id"
            ),
            {"id": publication_id},
        )

    with Session(engine) as session, session.begin():
        result = operator_reconcile(
            session,
            workspace_id=seed["workspace"],
            publication_id=publication_id,
            observer=_Observer(ObservedState.PUBLISHED, provider_publication_id="provider-video-7"),
            now=NOW,
        )

    assert result.changed is True
    with Session(engine) as session:
        publication = session.get(Publication, publication_id)
        assert publication is not None
        assert publication.status is PublicationStatus.PUBLISHED
        assert publication.provider_publication_id == "provider-video-7"


def test_an_ambiguous_destination_the_provider_never_published_becomes_retryable(
    engine: Engine, clean_database: None
) -> None:
    """Once provider truth says nothing was posted, an ordinary retry is safe again."""
    seed = _seed(engine, "operator-ambiguous-absent")
    publication_id = seed["destinations"]["youtube"]
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE publications SET status = 'retryable_failed', "
                "checkpoint_metadata = '{\"ambiguous\": true}' WHERE id = :id"
            ),
            {"id": publication_id},
        )

    with Session(engine) as session, session.begin():
        operator_reconcile(
            session,
            workspace_id=seed["workspace"],
            publication_id=publication_id,
            observer=_Observer(ObservedState.ABSENT),
            now=NOW,
        )
    with Session(engine) as session, session.begin():
        summary = retry_publication(
            session, access=_access(seed), publication_id=publication_id, now=NOW
        )

    assert summary.status is PublicationStatus.PREFLIGHTING


# --- Webhook intake -----------------------------------------------------------------


def test_a_verified_delivery_is_recorded_and_queued_for_a_worker(
    engine: Engine, clean_database: None
) -> None:
    """A webhook must leave durable evidence and wake a worker, never change state itself."""
    seed = _seed(engine, "webhook-accepted")
    publication_id = seed["destinations"]["youtube"]
    _set_status(
        engine,
        publication_id,
        status="processing",
        processing_at=NOW,
        provider_publication_id="provider-video-1",
    )

    with Session(engine) as session, session.begin():
        result = record_provider_delivery(session, delivery=_delivery(seed))

    assert result.accepted is True
    assert result.duplicate is False
    assert result.publication_id == publication_id
    assert result.reconcile_enqueued is True
    with Session(engine) as session:
        publication = session.get(Publication, publication_id)
        assert publication is not None
        assert publication.status is PublicationStatus.PROCESSING
        assert _outbox_topics(session, publication_id) == ["publication.reconcile"]


def test_a_replayed_delivery_is_recorded_once_and_wakes_one_worker(
    engine: Engine, clean_database: None
) -> None:
    """Duplicate delivery is normal provider behaviour, not a reason to work twice."""
    seed = _seed(engine, "webhook-replay")
    publication_id = seed["destinations"]["youtube"]
    _set_status(
        engine,
        publication_id,
        status="processing",
        processing_at=NOW,
        provider_publication_id="provider-video-1",
    )

    with Session(engine) as session, session.begin():
        first = record_provider_delivery(session, delivery=_delivery(seed))
        second = record_provider_delivery(session, delivery=_delivery(seed))

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.reconcile_enqueued is False
    with Session(engine) as session:
        assert _outbox_topics(session, publication_id) == ["publication.reconcile"]
        assert len(tuple(session.scalars(select(ProviderEvent)))) == 1


def test_an_unsigned_delivery_is_kept_as_evidence_but_never_acted_on(
    engine: Engine, clean_database: None
) -> None:
    """A failed signature is a security observation, not an instruction."""
    seed = _seed(engine, "webhook-unsigned")
    publication_id = seed["destinations"]["youtube"]
    _set_status(
        engine,
        publication_id,
        status="processing",
        processing_at=NOW,
        provider_publication_id="provider-video-1",
    )

    with Session(engine) as session, session.begin():
        result = record_provider_delivery(session, delivery=_delivery(seed, signature_valid=False))

    assert result.accepted is False
    assert result.reconcile_enqueued is False
    with Session(engine) as session:
        assert _outbox_topics(session, publication_id) == []
        event = session.scalar(select(ProviderEvent))
        assert event is not None
        assert event.signature_valid is False


def test_a_delivery_naming_an_unknown_account_is_refused(
    engine: Engine, clean_database: None
) -> None:
    """A webhook for an account this deployment does not hold cannot be attributed."""
    seed = _seed(engine, "webhook-unknown")

    with (
        Session(engine) as session,
        session.begin(),
        pytest.raises(ProviderDeliveryUnknownAccountError),
    ):
        record_provider_delivery(
            session, delivery=_delivery(seed, external_account_id="channel-nobody")
        )


def test_a_delivery_for_an_unmatched_publication_is_still_recorded(
    engine: Engine, clean_database: None
) -> None:
    """Evidence about an account we hold is worth keeping even with no destination."""
    seed = _seed(engine, "webhook-unmatched")

    with Session(engine) as session, session.begin():
        result = record_provider_delivery(
            session, delivery=_delivery(seed, provider_publication_id="provider-video-absent")
        )

    assert result.publication_id is None
    assert result.reconcile_enqueued is False
    with Session(engine) as session:
        assert session.scalar(select(ProviderEvent)) is not None


def test_two_deliveries_without_provider_ids_deduplicate_by_payload(
    engine: Engine, clean_database: None
) -> None:
    """A provider that sends no event identifier must still not be processed twice."""
    seed = _seed(engine, "webhook-hash-dedupe")
    publication_id = seed["destinations"]["youtube"]
    _set_status(
        engine,
        publication_id,
        status="processing",
        processing_at=NOW,
        provider_publication_id="provider-video-1",
    )

    with Session(engine) as session, session.begin():
        first = record_provider_delivery(session, delivery=_delivery(seed, provider_event_id=None))
        second = record_provider_delivery(session, delivery=_delivery(seed, provider_event_id=None))

    assert first.duplicate is False
    assert second.duplicate is True


# --- Restart and duplicate dispatch messages ----------------------------------------


def test_a_scheduler_restart_claims_each_due_destination_once(
    engine: Engine, clean_database: None
) -> None:
    """Two scheduler passes after a restart must not double-dispatch one destination."""
    seed = _seed(
        engine,
        "recovery-scheduler",
        status="scheduled",
        scheduled_for=NOW - timedelta(minutes=1),
    )
    assert len(seed["destinations"]) == 3

    with Session(engine) as session, session.begin():
        first = PublicationScheduler(session).claim_due(now=NOW, limit=10)
    with Session(engine) as session, session.begin():
        second = PublicationScheduler(session).claim_due(now=NOW, limit=10)

    assert len(first) == 3
    assert second == []
    with Session(engine) as session:
        messages = tuple(session.scalars(select(PublicationOutbox)))
        assert len(messages) == 3


def test_an_outbox_message_is_acknowledged_exactly_once(
    engine: Engine, clean_database: None
) -> None:
    """A worker that crashes after delivery must not lose or repeat the acknowledgement."""
    seed = _seed(
        engine,
        "recovery-outbox",
        status="scheduled",
        scheduled_for=NOW - timedelta(minutes=1),
    )

    with Session(engine) as session, session.begin():
        PublicationScheduler(session).claim_due(now=NOW, limit=10)
    with Session(engine) as session, session.begin():
        outbox = PublicationOutboxService(session)
        acknowledged = outbox.pending(now=NOW, limit=10)[0].id
        outbox.acknowledge(message_id=acknowledged, delivered_at=NOW)
        outbox.acknowledge(message_id=acknowledged, delivered_at=NOW + timedelta(minutes=1))

    with Session(engine) as session:
        message = session.get(PublicationOutbox, acknowledged)
        assert message is not None
        assert message.delivered_at == NOW
        assert message.attempt_count == 1
        assert len(PublicationOutboxService(session).pending(now=NOW, limit=10)) == 2
    assert seed["workspace"] is not None


def test_a_duplicate_enqueue_for_one_attempt_creates_one_message(
    engine: Engine, clean_database: None
) -> None:
    """Restart-safe dispatch means the same operation key is one durable intent."""
    seed = _seed(engine, "recovery-duplicate-enqueue")
    publication_id = seed["destinations"]["youtube"]

    with Session(engine) as session, session.begin():
        outbox = PublicationOutboxService(session)
        for _ in range(3):
            outbox.enqueue(
                workspace_id=seed["workspace"],
                publication_id=publication_id,
                topic="publication.preflight",
                operation_key="one-operation",
                available_at=NOW,
            )

    with Session(engine) as session:
        assert _outbox_topics(session, publication_id) == ["publication.preflight"]


def _outbox_topics(session: Session, publication_id: Any) -> list[str]:
    """Read the durable dispatch topics waiting for one destination."""
    return [
        message.topic
        for message in session.scalars(
            select(PublicationOutbox)
            .where(PublicationOutbox.publication_id == publication_id)
            .order_by(PublicationOutbox.created_at, PublicationOutbox.id)
        )
    ]


def test_reconciliation_after_an_outage_recovers_every_stuck_destination(
    engine: Engine, clean_database: None
) -> None:
    """A provider outage must leave nothing permanently stranded once it ends."""
    seed = _seed(engine, "recovery-outage")
    for publication_id in seed["destinations"].values():
        _set_status(
            engine,
            publication_id,
            status="processing",
            processing_at=NOW - timedelta(days=1),
            provider_publication_id=f"provider-{uuid4()}",
        )

    with Session(engine) as session, session.begin():
        results = reconcile_stuck_publications(
            session, observer=_Observer(ObservedState.PUBLISHED), now=NOW, limit=10
        )

    assert len(results) == 3
    assert all(result.changed for result in results)
    with Session(engine) as session:
        statuses = {
            session.get(Publication, publication_id).status  # type: ignore[union-attr]
            for publication_id in seed["destinations"].values()
        }
    assert statuses == {PublicationStatus.PUBLISHED}
