"""End-to-end publishing of one approved clip to three destinations at once."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from clipah.models import Publication, PublicationOutbox
from clipah.publishing.dispatcher import (
    DeliveryFailedError,
    DispatchOutcome,
    FailureKind,
)
from clipah.publishing.models import PublicationStatus
from clipah.publishing.outbox import PublicationOutboxService
from clipah.publishing.reconciler import (
    BatchState,
    ObservedState,
    ProviderObservation,
    aggregate_batch,
    operator_reconcile,
)
from clipah.publishing.scheduler import PublicationScheduler
from clipah.publishing.use_cases import cancel_publication, retry_publication
from clipah.publishing.webhooks import ProviderDelivery, record_provider_delivery
from clipah.social_accounts.models import SocialProvider
from clipah.workspaces.models import (
    PublishingRolePolicy,
    WorkspaceAccess,
    WorkspaceRole,
)
from integration.test_publication_dispatch import (
    PROVIDERS,
    _dispatcher,
    _drivers,
    _seed_multi_destination_batch,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 10, 5, 0, tzinfo=UTC)


class _PublishedObserver:
    """Report that the provider published exactly the destination it was asked about."""

    def observe(self, *, publication: Publication, now: datetime) -> ProviderObservation:
        """Return published truth carrying one authoritative provider identifier."""
        del now
        return ProviderObservation(
            publication_id=publication.id,
            state=ObservedState.PUBLISHED,
            provider_publication_id=f"observed-{publication.id}",
        )


def _access(seed: dict[str, Any]) -> WorkspaceAccess:
    """Return the owner standing provisioned by the seed graph."""
    return WorkspaceAccess(
        workspace_id=seed["workspace"],
        user_id=seed["user"],
        role=WorkspaceRole.OWNER,
        publishing_role_policy=PublishingRolePolicy.OWNER_ADMIN_EDITOR,
    )


def _dispatch(engine: Engine, seed: dict[str, Any], drivers: Any, *, now: datetime = NOW) -> Any:
    """Dispatch every destination of one batch, each in its own transaction."""
    outcomes: dict[str, DispatchOutcome] = {}
    for provider, publication_id in seed["destinations"].items():
        with Session(engine) as session, session.begin():
            outcomes[provider] = (
                _dispatcher(session, drivers=drivers)
                .dispatch(workspace_id=seed["workspace"], publication_id=publication_id, now=now)
                .outcome
            )
    return outcomes


def _publish_all(engine: Engine, seed: dict[str, Any]) -> None:
    """Mark every processing destination published, as its provider eventually would."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE publications SET status = 'published', published_at = :now "
                "WHERE batch_id = :batch AND status = 'processing'"
            ),
            {"batch": seed["batch"], "now": NOW},
        )


def _aggregate(engine: Engine, seed: dict[str, Any]) -> Any:
    """Read one batch's aggregate state outside any dispatch transaction."""
    with Session(engine) as session:
        return aggregate_batch(session, workspace_id=seed["workspace"], batch_id=seed["batch"])


def test_one_clip_reaches_three_destinations_and_the_batch_says_so(
    engine: Engine, clean_database: None
) -> None:
    """A publish-now batch must complete every destination and report completion once."""
    seed = _seed_multi_destination_batch(engine, suffix="e2e-happy")

    outcomes = _dispatch(engine, seed, _drivers())
    assert _aggregate(engine, seed).state is BatchState.IN_PROGRESS
    _publish_all(engine, seed)

    assert set(outcomes.values()) == {DispatchOutcome.DELIVERED}
    aggregate = _aggregate(engine, seed)
    assert aggregate.state is BatchState.COMPLETED
    assert aggregate.published == 3


def test_one_failed_destination_is_retried_alone_and_the_batch_recovers(
    engine: Engine, clean_database: None
) -> None:
    """Partial success must stay visible, and recovery must touch only what failed."""
    seed = _seed_multi_destination_batch(engine, suffix="e2e-partial")

    _dispatch(
        engine,
        seed,
        _drivers(
            instagram=DeliveryFailedError(
                code="instagram_unavailable", kind=FailureKind.RETRYABLE, retry_after=60
            )
        ),
    )
    _publish_all(engine, seed)
    partial = _aggregate(engine, seed)

    with Session(engine) as session, session.begin():
        retry_publication(
            session,
            access=_access(seed),
            publication_id=seed["destinations"]["instagram"],
            now=NOW,
        )
    healthy = _drivers()
    with Session(engine) as session, session.begin():
        _dispatcher(session, drivers=healthy).dispatch(
            workspace_id=seed["workspace"],
            publication_id=seed["destinations"]["instagram"],
            now=NOW,
        )
    _publish_all(engine, seed)

    assert partial.state is BatchState.IN_PROGRESS
    assert partial.published == 2
    final = _aggregate(engine, seed)
    assert final.state is BatchState.COMPLETED
    assert final.published == 3
    assert len(healthy[SocialProvider.YOUTUBE].delivered) == 0  # type: ignore[union-attr]
    assert len(healthy[SocialProvider.TIKTOK].delivered) == 0  # type: ignore[union-attr]


def test_a_permanently_refused_destination_leaves_the_batch_partially_failed(
    engine: Engine, clean_database: None
) -> None:
    """A policy refusal on one provider must not erase two successful publications."""
    seed = _seed_multi_destination_batch(engine, suffix="e2e-permanent")

    _dispatch(
        engine,
        seed,
        _drivers(
            tiktok=DeliveryFailedError(code="tiktok_permanent_failure", kind=FailureKind.PERMANENT)
        ),
    )
    _publish_all(engine, seed)

    aggregate = _aggregate(engine, seed)
    assert aggregate.state is BatchState.PARTIALLY_FAILED
    assert aggregate.published == 2
    assert aggregate.failed == 1


def test_an_ambiguous_destination_is_reconciled_before_it_may_be_retried(
    engine: Engine, clean_database: None
) -> None:
    """The one path that could duplicate a post must go through provider truth first."""
    seed = _seed_multi_destination_batch(engine, suffix="e2e-ambiguous")

    _dispatch(
        engine,
        seed,
        _drivers(
            youtube=DeliveryFailedError(
                code="youtube_ambiguous_completion", kind=FailureKind.AMBIGUOUS
            )
        ),
    )
    _publish_all(engine, seed)

    with Session(engine) as session, session.begin():
        result = operator_reconcile(
            session,
            workspace_id=seed["workspace"],
            publication_id=seed["destinations"]["youtube"],
            observer=_PublishedObserver(),
            now=NOW,
        )

    assert result.status is PublicationStatus.PUBLISHED
    assert _aggregate(engine, seed).state is BatchState.COMPLETED


def test_a_scheduled_batch_dispatches_only_once_its_requested_time_arrives(
    engine: Engine, clean_database: None
) -> None:
    """A future schedule must survive a scheduler restart without publishing early."""
    scheduled_for = NOW + timedelta(hours=3)
    seed = _seed_multi_destination_batch(
        engine, suffix="e2e-scheduled", status="scheduled", scheduled_for=scheduled_for
    )

    with Session(engine) as session, session.begin():
        early = PublicationScheduler(session).claim_due(now=NOW, limit=10)
    with Session(engine) as session, session.begin():
        due = PublicationScheduler(session).claim_due(now=scheduled_for, limit=10)
    with Session(engine) as session, session.begin():
        restart = PublicationScheduler(session).claim_due(now=scheduled_for, limit=10)
    outcomes = _dispatch(engine, seed, _drivers(), now=scheduled_for)
    _publish_all(engine, seed)

    assert early == []
    assert len(due) == 3
    assert restart == []
    assert set(outcomes.values()) == {DispatchOutcome.DELIVERED}
    with Session(engine) as session:
        assert len(tuple(session.scalars(select(PublicationOutbox)))) == 3
    assert _aggregate(engine, seed).state is BatchState.COMPLETED


def test_a_cancelled_destination_is_never_dispatched_with_its_siblings(
    engine: Engine, clean_database: None
) -> None:
    """Cancelling one destination must remove only that one from the batch."""
    seed = _seed_multi_destination_batch(
        engine,
        suffix="e2e-cancel",
        status="scheduled",
        scheduled_for=NOW + timedelta(hours=1),
    )

    with Session(engine) as session, session.begin():
        cancel_publication(
            session,
            access=_access(seed),
            publication_id=seed["destinations"]["tiktok"],
            provider_cancellable=True,
            now=NOW,
        )
    with Session(engine) as session, session.begin():
        due = PublicationScheduler(session).claim_due(now=NOW + timedelta(hours=2), limit=10)
    drivers = _drivers()
    outcomes = _dispatch(engine, seed, drivers, now=NOW + timedelta(hours=2))
    _publish_all(engine, seed)

    assert len(due) == 2
    assert outcomes["tiktok"] is DispatchOutcome.NOT_DISPATCHABLE
    assert drivers[SocialProvider.TIKTOK].delivered == []  # type: ignore[union-attr]
    aggregate = _aggregate(engine, seed)
    assert aggregate.state is BatchState.PARTIALLY_FAILED
    assert aggregate.published == 2
    assert aggregate.cancelled == 1


def test_a_provider_webhook_during_processing_queues_one_reconciliation(
    engine: Engine, clean_database: None
) -> None:
    """A live delivery must reach the batch through a worker, never through the route."""
    seed = _seed_multi_destination_batch(engine, suffix="e2e-webhook")
    _dispatch(engine, seed, _drivers())
    publication_id = seed["destinations"]["youtube"]
    with Session(engine) as session:
        publication = session.get(Publication, publication_id)
        assert publication is not None
        provider_publication_id = publication.provider_publication_id
    assert provider_publication_id is not None

    delivery = ProviderDelivery(
        provider=SocialProvider.YOUTUBE,
        external_account_id="channel-e2e-webhook",
        event_type="publication.status",
        provider_event_id="event-1",
        provider_publication_id=provider_publication_id,
        normalized_status="published",
        payload=b'{"status":"published"}',
        signature_valid=True,
        received_at=NOW,
    )
    with Session(engine) as session, session.begin():
        first = record_provider_delivery(session, delivery=delivery)
        second = record_provider_delivery(session, delivery=delivery)

    assert first.reconcile_enqueued is True
    assert second.reconcile_enqueued is False
    with Session(engine) as session:
        publication = session.get(Publication, publication_id)
        assert publication is not None
        assert publication.status is PublicationStatus.PROCESSING
        reconcile = [
            message.topic
            for message in PublicationOutboxService(session).pending(now=NOW, limit=10)
            if message.topic == "publication.reconcile"
        ]
        assert reconcile == ["publication.reconcile"]


def test_every_destination_of_a_batch_keeps_its_own_display_timezone(
    engine: Engine, clean_database: None
) -> None:
    """One batch may span zones, and each destination must report its member's own."""
    seed = _seed_multi_destination_batch(
        engine,
        suffix="e2e-timezone",
        status="scheduled",
        scheduled_for=NOW + timedelta(hours=1),
        display_timezone="Asia/Jakarta",
    )

    with Session(engine) as session:
        zones = {
            session.get(Publication, publication_id).display_timezone  # type: ignore[union-attr]
            for publication_id in seed["destinations"].values()
        }

    assert zones == {"Asia/Jakarta"}
    assert set(seed["destinations"]) == set(PROVIDERS)
