"""Real-Postgres contracts for multi-destination Publication dispatch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from clipah.jobs.admission import QuotaLedger
from clipah.models import (
    Publication,
    PublicationOutbox,
    QuotaReservationStatus,
    QuotaResource,
    WorkspaceQuotaReservation,
)
from clipah.publishing.dispatcher import (
    ACCOUNT_BUSY_BACKOFF,
    MAX_BACKOFF,
    DeliveryFailedError,
    DestinationDriver,
    DispatchOutcome,
    DispatchPolicy,
    FailureKind,
    PublicationDispatcher,
    backoff_delay,
    failure_from_provider_error,
)
from clipah.publishing.models import PublicationStatus
from clipah.publishing.providers.instagram.containers import (
    InstagramRateLimitedError,
    InstagramReconnectRequiredError,
)
from clipah.publishing.providers.tiktok.transfers import TikTokPermanentError
from clipah.publishing.providers.youtube.status import (
    YouTubePermanentError,
    YouTubeReconnectRequiredError,
    YouTubeUnavailableError,
)
from clipah.publishing.reconciler import BatchState, aggregate_batch
from clipah.publishing.use_cases import (
    PublicationRetryBlockedError,
    retry_publication,
)
from clipah.social_accounts.models import SocialProvider
from clipah.workspaces.models import (
    PublishingRolePolicy,
    WorkspaceAccess,
    WorkspaceRole,
)
from integration.test_publications import _seed_publication

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 10, 5, 0, tzinfo=UTC)
QUOTA_LIMITS = {resource: 100 for resource in QuotaResource}
PROVIDERS = ("youtube", "instagram", "tiktok")


class _RecordingDriver:
    """Deliver one destination by recording success or raising a classified failure."""

    def __init__(self, *, failure: DeliveryFailedError | None = None) -> None:
        """Start with no observed deliveries and one optional scripted failure."""
        self.failure = failure
        self.delivered: list[UUID] = []

    def deliver(self, *, session: Session, publication: Publication, now: datetime) -> None:
        """Record provider truth for one destination or raise its sanitized failure."""
        del session
        if self.failure is not None:
            raise self.failure
        self.delivered.append(publication.id)
        publication.status = PublicationStatus.TRANSFERRING
        publication.dispatched_at = now
        publication.transferred_at = now
        publication.provider_publication_id = f"provider-{publication.id}"
        publication.status = PublicationStatus.PROCESSING
        publication.processing_at = now


def _drivers(**failures: DeliveryFailedError | None) -> dict[SocialProvider, DestinationDriver]:
    """Build one driver per provider, optionally scripted to fail."""
    return {
        SocialProvider(name): _RecordingDriver(failure=failures.get(name)) for name in PROVIDERS
    }


def _seed_multi_destination_batch(
    engine: Engine,
    *,
    suffix: str,
    providers: tuple[str, ...] = PROVIDERS,
    status: str = "preflighting",
    scheduled_for: datetime | None = None,
    display_timezone: str = "UTC",
    rendition_digest: bytes | None = None,
    consent_confirmed: bool = True,
) -> dict[str, Any]:
    """Create one batch holding one preflighting destination per named provider."""
    seed = _seed_publication(engine, suffix=suffix)
    destinations: dict[str, UUID] = {}
    with engine.begin() as connection:
        for index, provider in enumerate(providers):
            rendition_id = uuid4()
            connection.execute(
                text(
                    """
                    INSERT INTO social_renditions
                        (id, workspace_id, render_artifact_id, source_sha256, provider,
                         profile_version, output_sha256, storage_key, size_bytes, duration_ms,
                         provenance, validation_report, reused_master, created_at)
                    VALUES (:id, :workspace, :render, :source, :provider, '2026-09-09', :output,
                            :key, 600000, 1000, '{}', '{}', false, :now)
                    """
                ),
                {
                    "id": rendition_id,
                    "workspace": seed["workspace"],
                    "render": seed["render"],
                    "source": seed["digest"],
                    "output": rendition_digest or seed["digest"],
                    "provider": provider,
                    "key": f"social/{provider}.mp4",
                    "now": NOW,
                },
            )
            if index == 0:
                account_id = seed["account"]
                publication_id = seed["publication"]
                connection.execute(
                    text("UPDATE social_accounts SET provider = :provider WHERE id = :account"),
                    {"account": account_id, "provider": provider},
                )
            else:
                account_id = uuid4()
                publication_id = uuid4()
                connection.execute(
                    text(
                        """
                        INSERT INTO social_accounts
                            (id, workspace_id, provider, external_account_id, display_name,
                             login_family, api_version, connection_status, capability_snapshot,
                             authorized_by_user_id, created_at)
                        VALUES (:id, :workspace, :provider, :external, 'Destination',
                                'social_oauth', 'v1', 'active',
                                '{"version":"cap-v1","values":{}}', :user, :now)
                        """
                    ),
                    {
                        "id": account_id,
                        "workspace": seed["workspace"],
                        "provider": provider,
                        "external": f"{provider}-{suffix}",
                        "user": seed["user"],
                        "now": NOW,
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO publications
                            (id, workspace_id, batch_id, social_account_id, edit_revision_id,
                             render_artifact_id, artifact_sha256, metadata_snapshot,
                             provider_options, consent_snapshot, display_timezone, status,
                             idempotency_key, provider_operation_key, created_at)
                        VALUES (:id, :workspace, :batch, :account, :revision, :render, :digest,
                                '{}', '{}', '{"confirmed": true}', :timezone, 'draft',
                                :key, :operation, :now)
                        """
                    ),
                    {
                        "id": publication_id,
                        "workspace": seed["workspace"],
                        "batch": seed["batch"],
                        "account": account_id,
                        "revision": seed["revision"],
                        "render": seed["render"],
                        "digest": seed["digest"],
                        "timezone": display_timezone,
                        "key": f"{suffix}-{provider}",
                        "operation": f"{suffix}-{provider}-op",
                        "now": NOW,
                    },
                )
            connection.execute(
                text(
                    """
                    UPDATE publications
                    SET status = :status, social_rendition_id = :rendition,
                        approved_by_user_id = :user, approved_at = :now, attempt_count = 0,
                        capability_version = 'cap-v1', display_timezone = :timezone,
                        scheduled_for = :scheduled_for,
                        consent_snapshot = CAST(:consent AS jsonb),
                        checkpoint_metadata = '{"preflightCapabilityVersion": "cap-v1"}'
                    WHERE id = :publication
                    """
                ),
                {
                    "publication": publication_id,
                    "rendition": rendition_id,
                    "user": seed["user"],
                    "now": NOW,
                    "status": status,
                    "timezone": display_timezone,
                    "scheduled_for": scheduled_for,
                    "consent": '{"confirmed": true}' if consent_confirmed else "{}",
                },
            )
            destinations[provider] = publication_id
    return {**seed, "destinations": destinations}


def _dispatcher(
    session: Session,
    *,
    drivers: dict[SocialProvider, DestinationDriver],
    jitter: float = 1.0,
    max_attempts: int = 5,
) -> PublicationDispatcher:
    """Build a dispatcher whose randomness and policy are pinned for the test."""
    return PublicationDispatcher(
        session,
        drivers=drivers,
        quota=QuotaLedger(session, limits=QUOTA_LIMITS),
        policy=DispatchPolicy(max_attempts=max_attempts),
        jitter=lambda: jitter,
    )


def _dispatch_all(
    engine: Engine, seed: dict[str, Any], drivers: dict[SocialProvider, DestinationDriver]
) -> dict[str, DispatchOutcome]:
    """Dispatch every destination of one batch in its own transaction."""
    results: dict[str, DispatchOutcome] = {}
    for provider, publication_id in seed["destinations"].items():
        with Session(engine) as session, session.begin():
            results[provider] = (
                _dispatcher(session, drivers=drivers)
                .dispatch(workspace_id=seed["workspace"], publication_id=publication_id, now=NOW)
                .outcome
            )
    return results


def _statuses(engine: Engine, seed: dict[str, Any]) -> dict[str, PublicationStatus]:
    """Read the durable status of every destination in one batch."""
    with Session(engine) as session:
        return {
            provider: publication.status
            for provider, publication_id in seed["destinations"].items()
            if (publication := session.get(Publication, publication_id)) is not None
        }


def _access(seed: dict[str, Any]) -> WorkspaceAccess:
    """Return the owner standing provisioned by the seed graph."""
    return WorkspaceAccess(
        workspace_id=seed["workspace"],
        user_id=seed["user"],
        role=WorkspaceRole.OWNER,
        publishing_role_policy=PublishingRolePolicy.OWNER_ADMIN_EDITOR,
    )


# --- Backoff policy -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("attempt", "expected_seconds"),
    ((0, 30), (1, 60), (2, 120), (3, 240)),
)
def test_backoff_doubles_from_the_configured_base(attempt: int, expected_seconds: int) -> None:
    """Repeated provider trouble must wait longer each time rather than hammering."""
    assert backoff_delay(attempt=attempt, retry_after=None, jitter=1.0) == timedelta(
        seconds=expected_seconds
    )


def test_backoff_is_jittered_so_workers_do_not_retry_in_lockstep() -> None:
    """Full jitter keeps many recovering destinations from colliding on one instant."""
    assert backoff_delay(attempt=3, retry_after=None, jitter=0.25) == timedelta(seconds=60)
    assert backoff_delay(attempt=3, retry_after=None, jitter=0.0) == timedelta(seconds=0)


def test_backoff_honours_a_longer_provider_retry_hint() -> None:
    """A provider that names its own wait must not be retried sooner than it asked."""
    assert backoff_delay(attempt=0, retry_after=900, jitter=1.0) == timedelta(seconds=900)
    assert backoff_delay(attempt=0, retry_after=1, jitter=1.0) == timedelta(seconds=30)


def test_backoff_never_exceeds_the_configured_ceiling() -> None:
    """An unbounded delay would strand a destination past any operator's patience."""
    assert backoff_delay(attempt=40, retry_after=None, jitter=1.0) == MAX_BACKOFF
    assert backoff_delay(attempt=0, retry_after=10_000_000, jitter=1.0) == MAX_BACKOFF


@pytest.mark.parametrize(
    ("error", "kind"),
    (
        (YouTubeUnavailableError("unavailable"), FailureKind.RETRYABLE),
        (YouTubeReconnectRequiredError("reconnect"), FailureKind.RECONNECT),
        (YouTubePermanentError("permanent"), FailureKind.PERMANENT),
        (InstagramRateLimitedError("limited", retry_after=120), FailureKind.RETRYABLE),
        (InstagramReconnectRequiredError("reconnect"), FailureKind.RECONNECT),
        (TikTokPermanentError("permanent"), FailureKind.PERMANENT),
    ),
)
def test_every_provider_error_classifies_without_the_dispatcher_knowing_the_provider(
    error: Exception, kind: FailureKind
) -> None:
    """One classification point keeps retry policy identical across three providers."""
    failure = failure_from_provider_error(error)

    assert failure.kind is kind
    assert failure.code == error.code


def test_a_rate_limit_hint_survives_classification() -> None:
    """A provider's own wait must reach the backoff calculation unchanged."""
    failure = failure_from_provider_error(InstagramRateLimitedError("limited", retry_after=120))

    assert failure.retry_after == 120


# --- Immediate multi-destination dispatch -------------------------------------------


def test_every_destination_of_one_batch_dispatches_independently(
    engine: Engine, clean_database: None
) -> None:
    """A publish-now batch must reach every provider without a shared failure point."""
    seed = _seed_multi_destination_batch(engine, suffix="dispatch-all")
    drivers = _drivers()

    outcomes = _dispatch_all(engine, seed, drivers)

    assert set(outcomes.values()) == {DispatchOutcome.DELIVERED}
    assert _statuses(engine, seed) == dict.fromkeys(PROVIDERS, PublicationStatus.PROCESSING)
    with Session(engine) as session:
        assert (
            aggregate_batch(session, workspace_id=seed["workspace"], batch_id=seed["batch"]).state
            is BatchState.IN_PROGRESS
        )


@pytest.mark.parametrize("failing", PROVIDERS)
def test_one_failing_destination_never_holds_back_its_siblings(
    engine: Engine, clean_database: None, failing: str
) -> None:
    """Partial success must stay visible instead of failing a whole batch."""
    seed = _seed_multi_destination_batch(engine, suffix=f"dispatch-partial-{failing}")
    drivers = _drivers(
        **{failing: DeliveryFailedError(code="youtube_unavailable", kind=FailureKind.RETRYABLE)}
    )

    outcomes = _dispatch_all(engine, seed, drivers)

    assert outcomes[failing] is DispatchOutcome.RETRYABLE
    statuses = _statuses(engine, seed)
    assert statuses[failing] is PublicationStatus.RETRYABLE_FAILED
    assert all(
        statuses[provider] is PublicationStatus.PROCESSING
        for provider in PROVIDERS
        if provider != failing
    )


@pytest.mark.parametrize(
    ("kind", "expected_outcome", "expected_status"),
    (
        (FailureKind.RETRYABLE, DispatchOutcome.RETRYABLE, PublicationStatus.RETRYABLE_FAILED),
        (
            FailureKind.RECONNECT,
            DispatchOutcome.RECONNECT_REQUIRED,
            PublicationStatus.RECONNECT_REQUIRED,
        ),
        (
            FailureKind.PERMANENT,
            DispatchOutcome.PERMANENT_FAILED,
            PublicationStatus.PERMANENT_FAILED,
        ),
        (FailureKind.AMBIGUOUS, DispatchOutcome.AMBIGUOUS, PublicationStatus.RETRYABLE_FAILED),
    ),
)
def test_each_failure_kind_reaches_its_truthful_durable_state(
    engine: Engine,
    clean_database: None,
    kind: FailureKind,
    expected_outcome: DispatchOutcome,
    expected_status: PublicationStatus,
) -> None:
    """The four ways a delivery can fail must not collapse into one local state."""
    seed = _seed_multi_destination_batch(
        engine, suffix=f"dispatch-kind-{kind.value}", providers=("youtube",)
    )
    drivers = _drivers(youtube=DeliveryFailedError(code="provider_code", kind=kind))

    outcomes = _dispatch_all(engine, seed, drivers)

    assert outcomes["youtube"] is expected_outcome
    assert _statuses(engine, seed)["youtube"] is expected_status


def test_a_permanent_or_authorization_failure_schedules_no_further_attempt(
    engine: Engine, clean_database: None
) -> None:
    """Retrying a policy or authorization refusal would only repeat the refusal."""
    for kind in (FailureKind.PERMANENT, FailureKind.RECONNECT):
        seed = _seed_multi_destination_batch(
            engine, suffix=f"dispatch-stop-{kind.value}", providers=("youtube",)
        )
        drivers = _drivers(youtube=DeliveryFailedError(code="provider_code", kind=kind))

        _dispatch_all(engine, seed, drivers)

        with Session(engine) as session:
            publication = session.get(Publication, seed["destinations"]["youtube"])
            assert publication is not None
            assert publication.next_attempt_at is None
            assert _outbox_count(session, publication.id) == 0


def test_a_retryable_failure_schedules_exactly_one_backed_off_attempt(
    engine: Engine, clean_database: None
) -> None:
    """A recoverable outage must leave durable, jittered, self-healing work behind."""
    seed = _seed_multi_destination_batch(
        engine, suffix="dispatch-retry-schedule", providers=("youtube",)
    )
    drivers = _drivers(
        youtube=DeliveryFailedError(
            code="youtube_rate_limited", kind=FailureKind.RETRYABLE, retry_after=900
        )
    )

    _dispatch_all(engine, seed, drivers)

    with Session(engine) as session:
        publication = session.get(Publication, seed["destinations"]["youtube"])
        assert publication is not None
        assert publication.next_attempt_at == NOW + timedelta(seconds=900)
        assert publication.normalized_error_code == "youtube_rate_limited"
        assert _outbox_count(session, publication.id) == 1


def test_an_exhausted_retry_budget_becomes_permanent_rather_than_endless(
    engine: Engine, clean_database: None
) -> None:
    """A destination must stop consuming capacity once its attempts are spent."""
    seed = _seed_multi_destination_batch(
        engine, suffix="dispatch-exhausted", providers=("youtube",)
    )
    publication_id = seed["destinations"]["youtube"]
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE publications SET attempt_count = 5 WHERE id = :id"),
            {"id": publication_id},
        )
    drivers = _drivers(
        youtube=DeliveryFailedError(code="youtube_unavailable", kind=FailureKind.RETRYABLE)
    )

    with Session(engine) as session, session.begin():
        result = _dispatcher(session, drivers=drivers).dispatch(
            workspace_id=seed["workspace"], publication_id=publication_id, now=NOW
        )

    assert result.outcome is DispatchOutcome.PERMANENT_FAILED
    with Session(engine) as session:
        publication = session.get(Publication, publication_id)
        assert publication is not None
        assert publication.status is PublicationStatus.PERMANENT_FAILED
        assert publication.normalized_error_code == "retry_budget_exhausted"


# --- Scheduling ---------------------------------------------------------------------


def test_a_destination_scheduled_for_later_is_not_delivered_early(
    engine: Engine, clean_database: None
) -> None:
    """A stale dispatch message must never publish before the requested instant."""
    seed = _seed_multi_destination_batch(
        engine,
        suffix="dispatch-not-due",
        providers=("youtube",),
        scheduled_for=NOW + timedelta(hours=2),
    )
    drivers = _drivers()

    outcomes = _dispatch_all(engine, seed, drivers)

    assert outcomes["youtube"] is DispatchOutcome.NOT_DUE
    assert _statuses(engine, seed)["youtube"] is PublicationStatus.PREFLIGHTING


def test_a_display_timezone_survives_dispatch_unchanged(
    engine: Engine, clean_database: None
) -> None:
    """The instant is UTC, but the member's own zone must remain reproducible."""
    scheduled_for = datetime(2026, 10, 25, 1, 30, tzinfo=ZoneInfo("Europe/Berlin"))
    seed = _seed_multi_destination_batch(
        engine,
        suffix="dispatch-dst",
        providers=("youtube",),
        scheduled_for=scheduled_for.astimezone(UTC),
        display_timezone="Europe/Berlin",
    )

    _dispatch_all(engine, seed, _drivers())

    with Session(engine) as session:
        publication = session.get(Publication, seed["destinations"]["youtube"])
        assert publication is not None
        assert publication.display_timezone == "Europe/Berlin"
        assert publication.scheduled_for == scheduled_for.astimezone(UTC)


def test_a_destination_that_is_no_longer_preflighting_is_left_alone(
    engine: Engine, clean_database: None
) -> None:
    """A duplicate dispatch message must not restart work that already finished."""
    seed = _seed_multi_destination_batch(
        engine, suffix="dispatch-duplicate", providers=("youtube",)
    )
    drivers = _drivers()

    first = _dispatch_all(engine, seed, drivers)
    second = _dispatch_all(engine, seed, drivers)

    assert first["youtube"] is DispatchOutcome.DELIVERED
    assert second["youtube"] is DispatchOutcome.NOT_DISPATCHABLE
    assert len(drivers[SocialProvider.YOUTUBE].delivered) == 1  # type: ignore[union-attr]


# --- Revalidation immediately before side effects -----------------------------------


def test_a_disconnected_account_is_refused_before_any_provider_call(
    engine: Engine, clean_database: None
) -> None:
    """A revoked destination must reach reconnection instead of a provider request."""
    seed = _seed_multi_destination_batch(
        engine, suffix="dispatch-disconnected", providers=("youtube",)
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE social_accounts SET connection_status = 'revoked', revoked_at = :now "
                "WHERE id = :account"
            ),
            {"account": seed["account"], "now": NOW},
        )
    drivers = _drivers()

    outcomes = _dispatch_all(engine, seed, drivers)

    assert outcomes["youtube"] is DispatchOutcome.NOT_DISPATCHABLE
    assert _statuses(engine, seed)["youtube"] is PublicationStatus.RECONNECT_REQUIRED
    assert drivers[SocialProvider.YOUTUBE].delivered == []  # type: ignore[union-attr]


def test_a_capability_change_returns_the_destination_to_approval(
    engine: Engine, clean_database: None
) -> None:
    """A provider that changed what it permits must not have choices assumed for it."""
    seed = _seed_multi_destination_batch(
        engine, suffix="dispatch-capability", providers=("youtube",)
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE social_accounts SET capability_snapshot = "
                '\'{"version":"cap-v2","values":{}}\' WHERE id = :account'
            ),
            {"account": seed["account"]},
        )
    drivers = _drivers()

    outcomes = _dispatch_all(engine, seed, drivers)

    assert outcomes["youtube"] is DispatchOutcome.NOT_DISPATCHABLE
    assert _statuses(engine, seed)["youtube"] is PublicationStatus.AWAITING_APPROVAL
    assert drivers[SocialProvider.YOUTUBE].delivered == []  # type: ignore[union-attr]


def test_a_rendition_whose_bytes_changed_is_refused_before_any_provider_call(
    engine: Engine, clean_database: None
) -> None:
    """Only the exact approved bytes may reach a provider."""
    seed = _seed_multi_destination_batch(
        engine,
        suffix="dispatch-checksum",
        providers=("youtube",),
        rendition_digest=bytes.fromhex("cd" * 32),
    )
    drivers = _drivers()

    outcomes = _dispatch_all(engine, seed, drivers)

    assert outcomes["youtube"] is DispatchOutcome.PERMANENT_FAILED
    assert _statuses(engine, seed)["youtube"] is PublicationStatus.PERMANENT_FAILED
    assert drivers[SocialProvider.YOUTUBE].delivered == []  # type: ignore[union-attr]


def test_a_missing_consent_snapshot_is_refused_before_any_provider_call(
    engine: Engine, clean_database: None
) -> None:
    """A destination whose consent evidence vanished cannot be published on its behalf."""
    seed = _seed_multi_destination_batch(
        engine,
        suffix="dispatch-consent",
        providers=("youtube",),
        consent_confirmed=False,
    )
    drivers = _drivers()

    outcomes = _dispatch_all(engine, seed, drivers)

    assert outcomes["youtube"] is DispatchOutcome.PERMANENT_FAILED
    assert drivers[SocialProvider.YOUTUBE].delivered == []  # type: ignore[union-attr]


def test_a_revoked_membership_cancels_instead_of_publishing(
    engine: Engine, clean_database: None
) -> None:
    """Someone who lost publishing authority must not have their queued work run."""
    seed = _seed_multi_destination_batch(
        engine, suffix="dispatch-membership", providers=("youtube",)
    )
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM workspace_memberships WHERE workspace_id = :workspace"),
            {"workspace": seed["workspace"]},
        )
    drivers = _drivers()

    outcomes = _dispatch_all(engine, seed, drivers)

    assert outcomes["youtube"] is DispatchOutcome.NOT_DISPATCHABLE
    assert _statuses(engine, seed)["youtube"] is PublicationStatus.CANCELLED
    assert drivers[SocialProvider.YOUTUBE].delivered == []  # type: ignore[union-attr]


# --- Provider and account locking ---------------------------------------------------


def test_two_workers_never_dispatch_the_same_account_at_once(
    engine: Engine, clean_database: None
) -> None:
    """One account's provider work must be serialized across every worker."""
    seed = _seed_multi_destination_batch(engine, suffix="dispatch-lock", providers=("youtube",))
    publication_id = seed["destinations"]["youtube"]
    drivers = _drivers()

    holder = Session(engine)
    holder.begin()
    try:
        holder.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:subject))"),
            {
                "namespace": 0x0C11_9A09,
                "subject": f"{seed['workspace']}:{seed['account']}",
            },
        )
        with Session(engine) as session, session.begin():
            result = _dispatcher(session, drivers=drivers).dispatch(
                workspace_id=seed["workspace"], publication_id=publication_id, now=NOW
            )
    finally:
        holder.rollback()
        holder.close()

    assert result.outcome is DispatchOutcome.ACCOUNT_BUSY
    assert result.next_attempt_at == NOW + ACCOUNT_BUSY_BACKOFF
    assert drivers[SocialProvider.YOUTUBE].delivered == []  # type: ignore[union-attr]
    assert _statuses(engine, seed)["youtube"] is PublicationStatus.PREFLIGHTING


# --- Quota reservations -------------------------------------------------------------


def _reservation(
    session: Session, *, workspace_id: UUID, publication_id: UUID
) -> WorkspaceQuotaReservation | None:
    """Read the one publication reservation this Workspace holds, if any."""
    return session.scalar(
        select(WorkspaceQuotaReservation).where(
            WorkspaceQuotaReservation.workspace_id == workspace_id,
            WorkspaceQuotaReservation.resource == QuotaResource.SOCIAL_PUBLICATIONS,
            WorkspaceQuotaReservation.reference_kind == "publication",
            WorkspaceQuotaReservation.reference_id == publication_id,
        )
    )


def _outbox_count(session: Session, publication_id: UUID) -> int:
    """Count the durable dispatch messages waiting for one destination."""
    return len(
        tuple(
            session.scalars(
                select(PublicationOutbox).where(PublicationOutbox.publication_id == publication_id)
            )
        )
    )


def test_a_delivered_destination_settles_exactly_one_publication_unit(
    engine: Engine, clean_database: None
) -> None:
    """A published destination must charge the Workspace budget once and only once."""
    seed = _seed_multi_destination_batch(engine, suffix="dispatch-quota", providers=("youtube",))
    publication_id = seed["destinations"]["youtube"]

    _dispatch_all(engine, seed, _drivers())

    with Session(engine) as session:
        reservation = _reservation(
            session, workspace_id=seed["workspace"], publication_id=publication_id
        )
        assert reservation is not None
        assert reservation.status is QuotaReservationStatus.SETTLED
        assert reservation.actual_units == Decimal(1)


def test_a_terminal_failure_releases_the_whole_reservation(
    engine: Engine, clean_database: None
) -> None:
    """A destination that never published must not spend the Workspace's budget."""
    seed = _seed_multi_destination_batch(
        engine, suffix="dispatch-quota-release", providers=("youtube",)
    )
    drivers = _drivers(
        youtube=DeliveryFailedError(code="youtube_permanent_failure", kind=FailureKind.PERMANENT)
    )

    _dispatch_all(engine, seed, drivers)

    with Session(engine) as session:
        reservation = _reservation(
            session,
            workspace_id=seed["workspace"],
            publication_id=seed["destinations"]["youtube"],
        )
        assert reservation is not None
        assert reservation.status is QuotaReservationStatus.RELEASED


def test_an_ambiguous_delivery_keeps_its_reservation_as_evidence(
    engine: Engine, clean_database: None
) -> None:
    """A delivery that may have published must keep holding the budget it may have spent."""
    seed = _seed_multi_destination_batch(
        engine, suffix="dispatch-quota-ambiguous", providers=("youtube",)
    )
    drivers = _drivers(
        youtube=DeliveryFailedError(code="youtube_ambiguous", kind=FailureKind.AMBIGUOUS)
    )

    _dispatch_all(engine, seed, drivers)

    with Session(engine) as session:
        reservation = _reservation(
            session,
            workspace_id=seed["workspace"],
            publication_id=seed["destinations"]["youtube"],
        )
        assert reservation is not None
        assert reservation.status is QuotaReservationStatus.RESERVED


def test_a_retried_destination_reuses_its_one_reservation(
    engine: Engine, clean_database: None
) -> None:
    """A recoverable retry must not charge the monthly budget a second time."""
    seed = _seed_multi_destination_batch(
        engine, suffix="dispatch-quota-retry", providers=("youtube",)
    )
    publication_id = seed["destinations"]["youtube"]
    failing = _drivers(
        youtube=DeliveryFailedError(code="youtube_unavailable", kind=FailureKind.RETRYABLE)
    )

    _dispatch_all(engine, seed, failing)
    with Session(engine) as session, session.begin():
        retry_publication(session, access=_access(seed), publication_id=publication_id, now=NOW)
    _dispatch_all(engine, seed, _drivers())

    with Session(engine) as session:
        reservations = tuple(
            session.scalars(
                select(WorkspaceQuotaReservation).where(
                    WorkspaceQuotaReservation.reference_id == publication_id
                )
            )
        )
        assert len(reservations) == 1
        assert reservations[0].status is QuotaReservationStatus.SETTLED


# --- Retry and cancel address one destination ---------------------------------------


def test_an_ambiguous_destination_cannot_be_retried_until_it_is_reconciled(
    engine: Engine, clean_database: None
) -> None:
    """Retrying an unproved delivery is exactly how a duplicate post happens."""
    seed = _seed_multi_destination_batch(
        engine, suffix="dispatch-ambiguous-retry", providers=("youtube",)
    )
    drivers = _drivers(
        youtube=DeliveryFailedError(code="youtube_ambiguous", kind=FailureKind.AMBIGUOUS)
    )
    _dispatch_all(engine, seed, drivers)

    with (
        Session(engine) as session,
        session.begin(),
        pytest.raises(PublicationRetryBlockedError),
    ):
        retry_publication(
            session,
            access=_access(seed),
            publication_id=seed["destinations"]["youtube"],
            now=NOW,
        )


def test_retrying_one_destination_never_replays_a_published_sibling(
    engine: Engine, clean_database: None
) -> None:
    """A retry addresses one destination ID and must leave the rest untouched."""
    seed = _seed_multi_destination_batch(engine, suffix="dispatch-retry-sibling")
    drivers = _drivers(
        tiktok=DeliveryFailedError(code="tiktok_unavailable", kind=FailureKind.RETRYABLE)
    )
    _dispatch_all(engine, seed, drivers)
    delivered_before = {
        provider: len(drivers[SocialProvider(provider)].delivered)  # type: ignore[union-attr]
        for provider in ("youtube", "instagram")
    }

    with Session(engine) as session, session.begin():
        retry_publication(
            session,
            access=_access(seed),
            publication_id=seed["destinations"]["tiktok"],
            now=NOW,
        )
    with Session(engine) as session, session.begin():
        _dispatcher(session, drivers=_drivers()).dispatch(
            workspace_id=seed["workspace"],
            publication_id=seed["destinations"]["tiktok"],
            now=NOW,
        )

    assert {
        provider: len(drivers[SocialProvider(provider)].delivered)  # type: ignore[union-attr]
        for provider in ("youtube", "instagram")
    } == delivered_before
    assert _statuses(engine, seed)["tiktok"] is PublicationStatus.PROCESSING


# --- Batch aggregation --------------------------------------------------------------


@pytest.mark.parametrize(
    ("terminal", "expected"),
    (
        (("published", "published", "published"), BatchState.COMPLETED),
        (("published", "published", "permanent_failed"), BatchState.PARTIALLY_FAILED),
        (
            ("permanent_failed", "permanent_failed", "permanent_failed"),
            BatchState.FAILED,
        ),
        (("cancelled", "cancelled", "cancelled"), BatchState.CANCELLED),
        (("published", "published", "processing"), BatchState.IN_PROGRESS),
    ),
)
def test_batch_state_never_hides_partial_success(
    engine: Engine,
    clean_database: None,
    terminal: tuple[str, str, str],
    expected: BatchState,
) -> None:
    """A batch summary must report what actually happened to each destination."""
    seed = _seed_multi_destination_batch(engine, suffix=f"aggregate-{expected.value}")
    with engine.begin() as connection:
        for provider, status in zip(PROVIDERS, terminal, strict=True):
            connection.execute(
                text("UPDATE publications SET status = :status WHERE id = :id"),
                {"status": status, "id": seed["destinations"][provider]},
            )

    with Session(engine) as session:
        aggregate = aggregate_batch(session, workspace_id=seed["workspace"], batch_id=seed["batch"])

    assert aggregate.state is expected
    assert aggregate.published == terminal.count("published")
    assert aggregate.total == 3


def test_a_batch_from_another_workspace_is_indistinguishable_from_a_missing_one(
    engine: Engine, clean_database: None
) -> None:
    """A guessed batch identifier must not reveal another tenant's publishing state."""
    first = _seed_multi_destination_batch(engine, suffix="aggregate-tenant-a")
    second = _seed_multi_destination_batch(engine, suffix="aggregate-tenant-b")

    with Session(engine) as session:
        aggregate = aggregate_batch(
            session, workspace_id=second["workspace"], batch_id=first["batch"]
        )

    assert aggregate.total == 0
    assert aggregate.state is BatchState.UNKNOWN
