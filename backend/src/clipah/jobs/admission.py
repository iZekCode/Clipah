"""Workspace admission control for durable background work and metered resources."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session

from clipah.auth.limits import (
    RateLimitBucket,
    RateLimiter,
    RateLimitExceededError,
)
from clipah.config import Settings
from clipah.models import (
    Job,
    JobKind,
    JobStatus,
    Project,
    QuotaReservationStatus,
    QuotaResource,
    WorkspaceQuotaReservation,
)
from clipah.observability.metrics import count

ACTIVE_JOB_STATUSES = (
    JobStatus.QUEUED,
    JobStatus.RUNNING,
    JobStatus.RETRYING,
    JobStatus.CANCEL_REQUESTED,
)
# One arbitrary but stable namespace keeps these advisory locks from colliding with
# any other advisory lock the application takes on the same Workspace.
_ADMISSION_LOCK_NAMESPACE = 0x0C11_9A07
_QUOTA_LOCK_NAMESPACE = 0x0C11_9A08
ANALYSIS_WINDOW = timedelta(hours=1)
# Job kinds that spend a metered Workspace budget. Kinds absent here cost no quota.
QUOTA_FOR_JOB_KIND: Mapping[JobKind, QuotaResource] = {
    JobKind.ANALYZE: QuotaResource.ANALYSES,
    JobKind.BROLL_RETRIEVE: QuotaResource.STOCK_REQUESTS,
    JobKind.BROLL_GENERATE: QuotaResource.GENERATED_IMAGES,
    JobKind.SOCIAL_PUBLISH: QuotaResource.SOCIAL_PUBLICATIONS,
}


class ConcurrencyLimitError(Exception):
    """Raised when a Workspace already holds every concurrent job slot it is allowed."""


class QuotaExceededError(Exception):
    """Raised when a Workspace has spent this month's budget for one metered resource."""

    def __init__(self, resource: QuotaResource, *, retry_after: timedelta) -> None:
        """Carry the resource and the wait until the next period without leaking balances."""
        super().__init__(resource.value)
        self.resource = resource
        self.retry_after = retry_after


class ProjectUnavailableError(Exception):
    """Raised when work is admitted against a Project that is deleted or was never visible.

    A soft-deleted Project stays recoverable, but it stops accepting work the moment it
    is deleted: a job admitted afterwards would either be wasted or would resurrect data
    the member asked to remove.
    """


class QuotaReservationNotFoundError(Exception):
    """Raised when a reconciliation names a reservation this Workspace does not hold."""


class JobAdmission:
    """Admit new work only while a Workspace stays inside its concurrency allowance."""

    def __init__(self, session: Session, *, limit: int) -> None:
        """Bind admission to one open transaction and the configured plan limit."""
        self._session = session
        self._limit = limit

    def reserve(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        kind: JobKind,
        idempotency_key: str,
        stage: str = "queued",
    ) -> Job:
        """Count this Workspace's unfinished jobs under a lock, then create one more."""
        self._lock_workspace(workspace_id)
        self._require_active_project(workspace_id=workspace_id, project_id=project_id)
        active = self._session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.workspace_id == workspace_id, Job.status.in_(ACTIVE_JOB_STATUSES))
        )
        if (active or 0) >= self._limit:
            raise ConcurrencyLimitError("workspace concurrency limit reached")
        job = Job(
            id=uuid4(),
            workspace_id=workspace_id,
            project_id=project_id,
            kind=kind,
            status=JobStatus.QUEUED,
            stage=stage,
            idempotency_key=idempotency_key,
        )
        self._session.add(job)
        self._session.flush()
        return job

    def _require_active_project(self, *, workspace_id: UUID, project_id: UUID) -> None:
        """Refuse work for a Project that is deleted, locking it against a concurrent delete.

        The row is locked rather than merely read, so a delete arriving while admission
        is deciding either loses the race or wins it completely.
        """
        project = self._session.scalar(
            select(Project)
            .where(
                Project.workspace_id == workspace_id,
                Project.id == project_id,
                Project.archived_at.is_(None),
            )
            .with_for_update()
        )
        if project is None:
            raise ProjectUnavailableError("project is unavailable")

    def _lock_workspace(self, workspace_id: UUID) -> None:
        """Serialize admission for one Workspace so two callers cannot read one free slot."""
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:workspace_id))"),
            {"namespace": _ADMISSION_LOCK_NAMESPACE, "workspace_id": str(workspace_id)},
        )


class QuotaLedger:
    """Hold, reconcile, and release one Workspace's monthly budget for metered resources."""

    def __init__(self, session: Session, *, limits: Mapping[QuotaResource, int]) -> None:
        """Bind the ledger to one open transaction and the configured plan budgets."""
        self._session = session
        self._limits = limits

    def reserve(
        self,
        *,
        workspace_id: UUID,
        resource: QuotaResource,
        units: Decimal,
        reference_kind: str,
        reference_id: UUID,
        now: datetime,
    ) -> WorkspaceQuotaReservation:
        """Charge an estimate against this month's budget, or refuse the whole request."""
        self._lock(workspace_id, resource)
        period_start = _period_start(now)
        limit = Decimal(self._limits[resource])
        if self._charged(workspace_id, resource, period_start) + units > limit:
            count(
                "clipah.provider.quota",
                provider="clipah",
                quotaResource=resource.value,
                outcome="refused",
            )
            raise QuotaExceededError(resource, retry_after=_period_end(period_start) - now)
        reservation = WorkspaceQuotaReservation(
            id=uuid4(),
            workspace_id=workspace_id,
            resource=resource,
            period_start=period_start,
            status=QuotaReservationStatus.RESERVED,
            estimated_units=units,
            reference_kind=reference_kind,
            reference_id=reference_id,
        )
        self._session.add(reservation)
        self._session.flush()
        return reservation

    def settle(
        self,
        *,
        workspace_id: UUID,
        resource: QuotaResource,
        reference_kind: str,
        reference_id: UUID,
        actual_units: Decimal,
        now: datetime,
    ) -> WorkspaceQuotaReservation:
        """Replace an estimate with the cost the provider actually charged."""
        reservation = self._load(workspace_id, resource, reference_kind, reference_id)
        reservation.status = QuotaReservationStatus.SETTLED
        reservation.actual_units = actual_units
        reservation.settled_at = now
        self._session.flush()
        return reservation

    def release(
        self,
        *,
        workspace_id: UUID,
        resource: QuotaResource,
        reference_kind: str,
        reference_id: UUID,
        now: datetime,
    ) -> WorkspaceQuotaReservation:
        """Return the whole estimate once the work reaches a terminal unbilled state."""
        reservation = self._load(workspace_id, resource, reference_kind, reference_id)
        reservation.status = QuotaReservationStatus.RELEASED
        reservation.actual_units = Decimal(0)
        reservation.settled_at = now
        self._session.flush()
        return reservation

    def settle_if_reserved(
        self,
        *,
        workspace_id: UUID,
        resource: QuotaResource,
        reference_kind: str,
        reference_id: UUID,
        actual_units: Decimal,
        now: datetime,
    ) -> WorkspaceQuotaReservation | None:
        """Settle one reservation exactly once, leaving a terminal row untouched.

        A generation may complete, be redelivered, and complete again; a reservation the
        first completion already settled must not be charged a second time, and one this
        Workspace never held is not an error a worker can act on.
        """
        try:
            reservation = self._load(workspace_id, resource, reference_kind, reference_id)
        except QuotaReservationNotFoundError:
            return None
        if reservation.status is not QuotaReservationStatus.RESERVED:
            return reservation
        reservation.status = QuotaReservationStatus.SETTLED
        reservation.actual_units = actual_units
        reservation.settled_at = now
        self._session.flush()
        return reservation

    def release_if_reserved(
        self,
        *,
        workspace_id: UUID,
        resource: QuotaResource,
        reference_kind: str,
        reference_id: UUID,
        now: datetime,
    ) -> WorkspaceQuotaReservation | None:
        """Release one still-held reservation, preserving usage already settled."""
        try:
            reservation = self._load(workspace_id, resource, reference_kind, reference_id)
        except QuotaReservationNotFoundError:
            return None
        if reservation.status is not QuotaReservationStatus.RESERVED:
            return reservation
        reservation.status = QuotaReservationStatus.RELEASED
        reservation.actual_units = Decimal(0)
        reservation.settled_at = now
        self._session.flush()
        return reservation

    def consumed(self, *, workspace_id: UUID, resource: QuotaResource, now: datetime) -> Decimal:
        """Report the budget this Workspace currently holds for the period containing ``now``."""
        return self._charged(workspace_id, resource, _period_start(now))

    def _charged(self, workspace_id: UUID, resource: QuotaResource, period_start: date) -> Decimal:
        """Charge open reservations at their estimate and closed ones at their real cost."""
        charge = case(
            (
                WorkspaceQuotaReservation.status == QuotaReservationStatus.RESERVED,
                WorkspaceQuotaReservation.estimated_units,
            ),
            else_=func.coalesce(WorkspaceQuotaReservation.actual_units, 0),
        )
        total = self._session.scalar(
            select(func.coalesce(func.sum(charge), 0)).where(
                WorkspaceQuotaReservation.workspace_id == workspace_id,
                WorkspaceQuotaReservation.resource == resource,
                WorkspaceQuotaReservation.period_start == period_start,
            )
        )
        return Decimal(total or 0)

    def _load(
        self,
        workspace_id: UUID,
        resource: QuotaResource,
        reference_kind: str,
        reference_id: UUID,
    ) -> WorkspaceQuotaReservation:
        """Lock exactly one reservation of this tenant before reconciling it."""
        reservation = self._session.scalar(
            select(WorkspaceQuotaReservation)
            .where(
                WorkspaceQuotaReservation.workspace_id == workspace_id,
                WorkspaceQuotaReservation.resource == resource,
                WorkspaceQuotaReservation.reference_kind == reference_kind,
                WorkspaceQuotaReservation.reference_id == reference_id,
            )
            .with_for_update()
        )
        if reservation is None:
            raise QuotaReservationNotFoundError("quota reservation unavailable")
        return reservation

    def _lock(self, workspace_id: UUID, resource: QuotaResource) -> None:
        """Serialize one Workspace's budget for one resource across concurrent callers."""
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:subject))"),
            {
                "namespace": _QUOTA_LOCK_NAMESPACE,
                "subject": f"{workspace_id}:{resource.value}",
            },
        )


@dataclass(frozen=True, slots=True)
class AdmissionPolicy:
    """Every plan limit one Workspace must satisfy before durable work is created."""

    quota_limits: Mapping[QuotaResource, int]
    concurrent_jobs: int
    analyses_per_hour: int
    limiter: RateLimiter | None = None


def admission_policy(settings: Settings, limiter: RateLimiter | None = None) -> AdmissionPolicy:
    """Read the deployment's configured plan limits into one admission policy."""
    return AdmissionPolicy(
        quota_limits={
            QuotaResource.ANALYSES: settings.monthly_analyses,
            QuotaResource.STOCK_REQUESTS: settings.monthly_stock_requests,
            QuotaResource.GENERATED_IMAGES: settings.monthly_generated_images,
            QuotaResource.GENERATED_VIDEOS: settings.monthly_generated_videos,
            QuotaResource.GENERATED_SECONDS: settings.monthly_generated_seconds,
            QuotaResource.SOCIAL_PUBLICATIONS: settings.monthly_social_publications,
        },
        concurrent_jobs=settings.concurrent_jobs_per_workspace,
        analyses_per_hour=settings.analyses_per_hour,
        limiter=limiter,
    )


def admit_job(
    session: Session,
    *,
    policy: AdmissionPolicy,
    workspace_id: UUID,
    project_id: UUID,
    user_id: UUID,
    kind: JobKind,
    idempotency_key: str,
    now: datetime,
    estimated_units: Decimal = Decimal(1),
    quota_units: Mapping[QuotaResource, Decimal] | None = None,
) -> Job:
    """Charge every limit this kind of work is subject to, then create the job row.

    All three checks share the caller's transaction, so a refusal at any point leaves
    no admitted job and no held budget behind. A caller that spends more than one
    metered resource — a generated video costs both a video and its seconds — names
    every resource explicitly instead of relying on the one-resource default.
    """
    # The Redis allowance must run last so a database refusal cannot spend it. A
    # savepoint makes the inverse failure atomic too: callers may translate the
    # limiter exception without having to know tentative rows were flushed first.
    with session.begin_nested():
        job = JobAdmission(session, limit=policy.concurrent_jobs).reserve(
            workspace_id=workspace_id,
            project_id=project_id,
            kind=kind,
            idempotency_key=idempotency_key,
        )
        charges = _charges(kind, estimated_units=estimated_units, quota_units=quota_units)
        ledger = QuotaLedger(session, limits=policy.quota_limits)
        for resource, units in charges.items():
            ledger.reserve(
                workspace_id=workspace_id,
                resource=resource,
                units=units,
                reference_kind="job",
                reference_id=job.id,
                now=now,
            )
        if kind is JobKind.ANALYZE:
            _require_analysis_allowance(policy, user_id=user_id)
    return job


def _charges(
    kind: JobKind,
    *,
    estimated_units: Decimal,
    quota_units: Mapping[QuotaResource, Decimal] | None,
) -> Mapping[QuotaResource, Decimal]:
    """Name every metered resource this admission must hold before the work exists."""
    if quota_units is not None:
        return quota_units
    resource = QUOTA_FOR_JOB_KIND.get(kind)
    return {} if resource is None else {resource: estimated_units}


def _require_analysis_allowance(policy: AdmissionPolicy, *, user_id: UUID) -> None:
    """Spend one hourly analysis slot for the User who asked for the work."""
    if policy.limiter is None:
        return
    decision = policy.limiter.check(
        subject=f"user:{user_id}",
        bucket=RateLimitBucket.ANALYSIS,
        limit=policy.analyses_per_hour,
        window=ANALYSIS_WINDOW,
    )
    if not decision.allowed:
        raise RateLimitExceededError(RateLimitBucket.ANALYSIS, retry_after=decision.retry_after)


def _period_start(now: datetime) -> date:
    """Return the first day of the calendar month a budget is measured over."""
    return now.date().replace(day=1)


def _period_end(period_start: date) -> datetime:
    """Return the instant the budget containing ``period_start`` resets."""
    year = period_start.year + (1 if period_start.month == 12 else 0)
    month = 1 if period_start.month == 12 else period_start.month + 1
    return datetime(year, month, 1, tzinfo=UTC)
