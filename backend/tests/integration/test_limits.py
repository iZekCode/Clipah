"""Integration contracts for rate limits, Workspace quotas, and job admission."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from datetime import timedelta
from decimal import Decimal
from queue import Queue
from threading import Thread
from uuid import UUID, uuid4

import pytest
from httpx import Response
from redis import Redis
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from clipah.auth.limits import RateLimitBucket, RateLimitExceededError, RedisRateLimiter
from clipah.db import session_scope
from clipah.jobs.admission import (
    ConcurrencyLimitError,
    JobAdmission,
    QuotaExceededError,
    QuotaLedger,
    QuotaResource,
    admission_policy,
    admit_job,
)
from clipah.models import Job, JobKind, JobStatus, Project, SourceKind
from harness import NOW, Browser, Clock, StubGoogleProvider, assert_error, build_app, sign_in
from support import provision_identity, redis_client, runtime_settings


@pytest.fixture
def limiter_clock() -> Clock:
    """Give every limiter test one hand-wound clock the window is measured against."""
    return Clock(NOW)


@pytest.fixture
def redis_connection() -> Iterator[Redis]:
    """Expose the real Redis the sliding-window script must run inside."""
    with redis_client() as connection:
        yield connection


@pytest.mark.integration
def test_sliding_window_admits_exactly_the_configured_limit(
    redis_connection: Redis, limiter_clock: Clock
) -> None:
    """A limit is a promise about a count, so the request after it must be refused."""
    limiter = RedisRateLimiter(redis_connection, now=limiter_clock)
    subject = f"user:{uuid4()}"

    decisions = [
        limiter.check(
            subject=subject, bucket=RateLimitBucket.WRITE, limit=3, window=timedelta(minutes=1)
        )
        for _ in range(4)
    ]

    assert [decision.allowed for decision in decisions] == [True, True, True, False]
    assert [decision.remaining for decision in decisions] == [2, 1, 0, 0]


@pytest.mark.integration
def test_a_refused_request_reports_when_the_window_frees_a_slot(
    redis_connection: Redis, limiter_clock: Clock
) -> None:
    """A client can only back off correctly when the refusal states how long to wait."""
    limiter = RedisRateLimiter(redis_connection, now=limiter_clock)
    subject = f"user:{uuid4()}"
    window = timedelta(minutes=1)
    limiter.check(subject=subject, bucket=RateLimitBucket.WRITE, limit=1, window=window)
    limiter_clock.advance(timedelta(seconds=20))

    refused = limiter.check(subject=subject, bucket=RateLimitBucket.WRITE, limit=1, window=window)

    assert refused.allowed is False
    assert refused.retry_after == timedelta(seconds=40)


@pytest.mark.integration
def test_the_window_slides_instead_of_resetting_on_a_fixed_boundary(
    redis_connection: Redis, limiter_clock: Clock
) -> None:
    """A fixed-bucket reset would let a caller send double the limit across a boundary."""
    limiter = RedisRateLimiter(redis_connection, now=limiter_clock)
    subject = f"user:{uuid4()}"
    window = timedelta(minutes=1)
    limiter.check(subject=subject, bucket=RateLimitBucket.READ, limit=1, window=window)

    limiter_clock.advance(timedelta(seconds=59))
    still_refused = limiter.check(
        subject=subject, bucket=RateLimitBucket.READ, limit=1, window=window
    )
    limiter_clock.advance(timedelta(seconds=1))
    admitted_again = limiter.check(
        subject=subject, bucket=RateLimitBucket.READ, limit=1, window=window
    )

    assert still_refused.allowed is False
    assert admitted_again.allowed is True


@pytest.mark.integration
def test_subjects_and_buckets_hold_independent_allowances(
    redis_connection: Redis, limiter_clock: Clock
) -> None:
    """One exhausted caller or bucket must never spend another caller's allowance."""
    limiter = RedisRateLimiter(redis_connection, now=limiter_clock)
    exhausted = f"user:{uuid4()}"
    neighbour = f"user:{uuid4()}"
    window = timedelta(minutes=1)
    limiter.check(subject=exhausted, bucket=RateLimitBucket.WRITE, limit=1, window=window)

    same_bucket_other_subject = limiter.check(
        subject=neighbour, bucket=RateLimitBucket.WRITE, limit=1, window=window
    )
    other_bucket_same_subject = limiter.check(
        subject=exhausted, bucket=RateLimitBucket.READ, limit=1, window=window
    )

    assert same_bucket_other_subject.allowed is True
    assert other_bucket_same_subject.allowed is True


@pytest.mark.integration
def test_job_admission_stops_at_the_configured_concurrency_limit(engine: Engine) -> None:
    """A Workspace that already holds its allowance of unfinished jobs must wait."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="admission")

    with _tenant_session(workspace_id, user_id) as session:
        admission = JobAdmission(session, limit=2)
        for index in range(2):
            admission.reserve(
                workspace_id=workspace_id,
                project_id=project_id,
                kind=JobKind.ANALYZE,
                idempotency_key=f"job-{index}",
            )

        with pytest.raises(ConcurrencyLimitError):
            admission.reserve(
                workspace_id=workspace_id,
                project_id=project_id,
                kind=JobKind.ANALYZE,
                idempotency_key="job-overflow",
            )


@pytest.mark.integration
def test_a_terminal_job_returns_its_concurrency_slot(engine: Engine) -> None:
    """Finished work must not keep occupying an allowance forever."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="terminal")

    with _tenant_session(workspace_id, user_id) as session:
        admission = JobAdmission(session, limit=1)
        first = admission.reserve(
            workspace_id=workspace_id,
            project_id=project_id,
            kind=JobKind.RENDER,
            idempotency_key="first",
        )
        first.status = JobStatus.SUCCEEDED
        session.flush()

        second = admission.reserve(
            workspace_id=workspace_id,
            project_id=project_id,
            kind=JobKind.RENDER,
            idempotency_key="second",
        )
        assert second.status is JobStatus.QUEUED
        assert second.id != first.id


@pytest.mark.integration
def test_one_exhausted_workspace_never_blocks_another(engine: Engine) -> None:
    """Concurrency is a tenant budget, so a neighbour's queue is not our concern."""
    busy_user, busy_workspace, busy_project = _workspace_with_project(engine, suffix="busy")
    quiet_user, quiet_workspace, quiet_project = _workspace_with_project(engine, suffix="quiet")

    with _tenant_session(busy_workspace, busy_user) as session:
        JobAdmission(session, limit=1).reserve(
            workspace_id=busy_workspace,
            project_id=busy_project,
            kind=JobKind.INGEST,
            idempotency_key="busy",
        )

    with _tenant_session(quiet_workspace, quiet_user) as session:
        admitted = JobAdmission(session, limit=1).reserve(
            workspace_id=quiet_workspace,
            project_id=quiet_project,
            kind=JobKind.INGEST,
            idempotency_key="quiet",
        )
        assert admitted.workspace_id == quiet_workspace


@pytest.mark.integration
@pytest.mark.slow
def test_fifty_concurrent_reservations_admit_exactly_the_limit(engine: Engine) -> None:
    """Counting outside a lock would let simultaneous callers all read the same free slot."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="race")
    outcomes: Queue[bool] = Queue()

    def reserve(index: int) -> None:
        try:
            with _tenant_session(workspace_id, user_id) as session:
                JobAdmission(session, limit=5).reserve(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    kind=JobKind.ANALYZE,
                    idempotency_key=f"race-{index}",
                )
            outcomes.put(True)
        except ConcurrencyLimitError:
            outcomes.put(False)

    threads = [Thread(target=reserve, args=(index,)) for index in range(50)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    admitted = [outcomes.get() for _ in range(50)]
    assert admitted.count(True) == 5
    assert admitted.count(False) == 45
    with _tenant_session(workspace_id, user_id) as session:
        stored = session.scalar(
            select(func.count()).select_from(Job).where(Job.workspace_id == workspace_id)
        )
    assert stored == 5


@pytest.mark.integration
def test_a_workspace_budget_refuses_the_request_that_would_exceed_it(engine: Engine) -> None:
    """A monthly budget only means something if the overspending request is refused."""
    user_id, workspace_id, _ = _workspace_with_project(engine, suffix="budget")

    with _tenant_session(workspace_id, user_id) as session:
        ledger = QuotaLedger(session, limits={QuotaResource.STOCK_REQUESTS: 3})
        for _ in range(3):
            ledger.reserve(
                workspace_id=workspace_id,
                resource=QuotaResource.STOCK_REQUESTS,
                units=Decimal(1),
                reference_kind="job",
                reference_id=uuid4(),
                now=NOW,
            )

        with pytest.raises(QuotaExceededError) as refusal:
            ledger.reserve(
                workspace_id=workspace_id,
                resource=QuotaResource.STOCK_REQUESTS,
                units=Decimal(1),
                reference_kind="job",
                reference_id=uuid4(),
                now=NOW,
            )

        assert refusal.value.retry_after > timedelta(0)


@pytest.mark.integration
def test_settling_an_overestimate_returns_the_unused_budget(engine: Engine) -> None:
    """Estimates are charged up front, so the real cost must replace them on completion."""
    user_id, workspace_id, _ = _workspace_with_project(engine, suffix="settle")
    reference_id = uuid4()

    with _tenant_session(workspace_id, user_id) as session:
        ledger = QuotaLedger(session, limits={QuotaResource.GENERATED_SECONDS: 60})
        ledger.reserve(
            workspace_id=workspace_id,
            resource=QuotaResource.GENERATED_SECONDS,
            units=Decimal(60),
            reference_kind="job",
            reference_id=reference_id,
            now=NOW,
        )

        ledger.settle(
            workspace_id=workspace_id,
            resource=QuotaResource.GENERATED_SECONDS,
            reference_kind="job",
            reference_id=reference_id,
            actual_units=Decimal(20),
            now=NOW,
        )
        consumed = ledger.consumed(
            workspace_id=workspace_id, resource=QuotaResource.GENERATED_SECONDS, now=NOW
        )
        remaining_admits = ledger.reserve(
            workspace_id=workspace_id,
            resource=QuotaResource.GENERATED_SECONDS,
            units=Decimal(40),
            reference_kind="job",
            reference_id=uuid4(),
            now=NOW,
        )

        assert consumed == Decimal(20)
        assert remaining_admits.estimated_units == Decimal(40)


@pytest.mark.integration
def test_a_failed_job_releases_the_whole_reservation(engine: Engine) -> None:
    """Work that never happened must not spend a Workspace's monthly allowance."""
    user_id, workspace_id, _ = _workspace_with_project(engine, suffix="release")
    reference_id = uuid4()

    with _tenant_session(workspace_id, user_id) as session:
        ledger = QuotaLedger(session, limits={QuotaResource.GENERATED_VIDEOS: 1})
        ledger.reserve(
            workspace_id=workspace_id,
            resource=QuotaResource.GENERATED_VIDEOS,
            units=Decimal(1),
            reference_kind="job",
            reference_id=reference_id,
            now=NOW,
        )

        ledger.release(
            workspace_id=workspace_id,
            resource=QuotaResource.GENERATED_VIDEOS,
            reference_kind="job",
            reference_id=reference_id,
            now=NOW,
        )

        assert ledger.consumed(
            workspace_id=workspace_id, resource=QuotaResource.GENERATED_VIDEOS, now=NOW
        ) == Decimal(0)


@pytest.mark.integration
def test_budgets_are_separate_per_resource_and_per_workspace(engine: Engine) -> None:
    """Spending one budget must never reduce a different resource or another tenant."""
    spender_user, spender_workspace, _ = _workspace_with_project(engine, suffix="spender")
    neighbour_user, neighbour_workspace, _ = _workspace_with_project(engine, suffix="neighbour")
    limits = {QuotaResource.GENERATED_IMAGES: 1, QuotaResource.SOCIAL_PUBLICATIONS: 1}

    with _tenant_session(spender_workspace, spender_user) as session:
        ledger = QuotaLedger(session, limits=limits)
        ledger.reserve(
            workspace_id=spender_workspace,
            resource=QuotaResource.GENERATED_IMAGES,
            units=Decimal(1),
            reference_kind="job",
            reference_id=uuid4(),
            now=NOW,
        )
        other_resource = ledger.reserve(
            workspace_id=spender_workspace,
            resource=QuotaResource.SOCIAL_PUBLICATIONS,
            units=Decimal(1),
            reference_kind="publication",
            reference_id=uuid4(),
            now=NOW,
        )
        assert other_resource.resource is QuotaResource.SOCIAL_PUBLICATIONS

    with _tenant_session(neighbour_workspace, neighbour_user) as session:
        neighbour = QuotaLedger(session, limits=limits).reserve(
            workspace_id=neighbour_workspace,
            resource=QuotaResource.GENERATED_IMAGES,
            units=Decimal(1),
            reference_kind="job",
            reference_id=uuid4(),
            now=NOW,
        )
        assert neighbour.workspace_id == neighbour_workspace


@pytest.mark.integration
def test_a_new_calendar_month_starts_a_fresh_budget(engine: Engine) -> None:
    """Monthly budgets that never reset would permanently lock a paying Workspace out."""
    user_id, workspace_id, _ = _workspace_with_project(engine, suffix="rollover")

    with _tenant_session(workspace_id, user_id) as session:
        ledger = QuotaLedger(session, limits={QuotaResource.ANALYSES: 1})
        ledger.reserve(
            workspace_id=workspace_id,
            resource=QuotaResource.ANALYSES,
            units=Decimal(1),
            reference_kind="job",
            reference_id=uuid4(),
            now=NOW,
        )

        next_month = ledger.reserve(
            workspace_id=workspace_id,
            resource=QuotaResource.ANALYSES,
            units=Decimal(1),
            reference_kind="job",
            reference_id=uuid4(),
            now=NOW + timedelta(days=35),
        )

        assert next_month.period_start.month != NOW.month


@pytest.mark.integration
@pytest.mark.slow
def test_concurrent_reservations_never_oversell_one_budget(engine: Engine) -> None:
    """Two processes reading the same remaining balance would both spend the last unit."""
    user_id, workspace_id, _ = _workspace_with_project(engine, suffix="oversell")
    outcomes: Queue[bool] = Queue()

    def reserve() -> None:
        try:
            with _tenant_session(workspace_id, user_id) as session:
                QuotaLedger(session, limits={QuotaResource.GENERATED_IMAGES: 3}).reserve(
                    workspace_id=workspace_id,
                    resource=QuotaResource.GENERATED_IMAGES,
                    units=Decimal(1),
                    reference_kind="job",
                    reference_id=uuid4(),
                    now=NOW,
                )
            outcomes.put(True)
        except QuotaExceededError:
            outcomes.put(False)

    threads = [Thread(target=reserve) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    admitted = [outcomes.get() for _ in range(20)]
    assert admitted.count(True) == 3


@pytest.mark.integration
def test_each_social_account_holds_its_own_provider_allowance(
    redis_connection: Redis, limiter_clock: Clock
) -> None:
    """Provider limits are per connected account, so one account cannot starve another."""
    limiter = RedisRateLimiter(redis_connection, now=limiter_clock)
    window = timedelta(hours=24)
    exhausted_account = f"social_account:{uuid4()}"
    second_account = f"social_account:{uuid4()}"
    limiter.check(
        subject=exhausted_account, bucket=RateLimitBucket.SOCIAL_PUBLISH, limit=1, window=window
    )

    same_account = limiter.check(
        subject=exhausted_account, bucket=RateLimitBucket.SOCIAL_PUBLISH, limit=1, window=window
    )
    other_account = limiter.check(
        subject=second_account, bucket=RateLimitBucket.SOCIAL_PUBLISH, limit=1, window=window
    )

    assert same_account.allowed is False
    assert other_account.allowed is True


@pytest.mark.integration
def test_a_fourth_analysis_within_one_hour_is_refused(
    engine: Engine, redis_connection: Redis
) -> None:
    """Free analyses are capped hourly, so the fourth request in that hour must wait."""
    clock = Clock(NOW)
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="analysis")
    policy = admission_policy(runtime_settings(), RedisRateLimiter(redis_connection, now=clock))

    with _tenant_session(workspace_id, user_id) as session:
        for index in range(3):
            admit_job(
                session,
                policy=policy,
                workspace_id=workspace_id,
                project_id=project_id,
                user_id=user_id,
                kind=JobKind.ANALYZE,
                idempotency_key=f"analysis-{index}",
                now=clock(),
            )

        with pytest.raises(RateLimitExceededError) as refusal:
            admit_job(
                session,
                policy=policy,
                workspace_id=workspace_id,
                project_id=project_id,
                user_id=user_id,
                kind=JobKind.ANALYZE,
                idempotency_key="analysis-overflow",
                now=clock(),
            )

        assert refusal.value.retry_after <= timedelta(hours=1)


@pytest.mark.integration
def test_admitting_an_analysis_charges_the_monthly_analyses_budget(
    engine: Engine, redis_connection: Redis
) -> None:
    """Admission is the moment a Workspace's metered allowance is actually spent."""
    clock = Clock(NOW)
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="charge")
    policy = admission_policy(runtime_settings(), RedisRateLimiter(redis_connection, now=clock))

    with _tenant_session(workspace_id, user_id) as session:
        job = admit_job(
            session,
            policy=policy,
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
            kind=JobKind.ANALYZE,
            idempotency_key="charged",
            now=clock(),
        )

        charged = QuotaLedger(session, limits=policy.quota_limits).consumed(
            workspace_id=workspace_id, resource=QuotaResource.ANALYSES, now=clock()
        )
        assert job.kind is JobKind.ANALYZE
        assert charged == Decimal(1)


@pytest.mark.integration
def test_an_exhausted_monthly_budget_refuses_admission_before_work_is_created(
    engine: Engine, redis_connection: Redis
) -> None:
    """A refused job must leave no durable row behind to be picked up by a worker."""
    clock = Clock(NOW)
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="exhausted")
    policy = admission_policy(
        runtime_settings(monthly_analyses=0),
        RedisRateLimiter(redis_connection, now=clock),
    )

    with _tenant_session(workspace_id, user_id) as session:
        with pytest.raises(QuotaExceededError):
            admit_job(
                session,
                policy=policy,
                workspace_id=workspace_id,
                project_id=project_id,
                user_id=user_id,
                kind=JobKind.ANALYZE,
                idempotency_key="never-created",
                now=clock(),
            )
        session.rollback()

    with _tenant_session(workspace_id, user_id) as session:
        stored = session.scalar(
            select(func.count()).select_from(Job).where(Job.workspace_id == workspace_id)
        )
    assert stored == 0


@pytest.mark.integration
def test_read_requests_are_limited_per_user_and_state_their_retry_delay(
    engine: Engine, redis_connection: Redis
) -> None:
    """A caller past the read allowance must be refused and told how long to wait."""
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    app, flow, settings = build_app(
        clock, provider, rate_limiter=RedisRateLimiter(redis_connection, now=clock)
    )
    browser = Browser(app)
    sign_in(browser, flow)
    assert settings.read_requests_per_minute == 60

    responses = [browser.get("/api/v1/workspaces") for _ in range(61)]

    assert [response.status_code for response in responses[:60]] == [200] * 60
    assert_error(responses[60], status_code=429, code="RATE_LIMITED")
    assert int(responses[60].headers["Retry-After"]) > 0


@pytest.mark.integration
def test_write_requests_hold_a_separate_smaller_allowance(
    engine: Engine, redis_connection: Redis
) -> None:
    """Writes cost more than reads, so they carry their own tighter budget."""
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    app, flow, _ = build_app(
        clock,
        provider,
        rate_limiter=RedisRateLimiter(redis_connection, now=clock),
        write_requests_per_minute=1,
    )
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])

    first = _create_project(browser, workspace_id, key="write-one")
    second = _create_project(browser, workspace_id, key="write-two")
    still_reads = browser.get("/api/v1/workspaces")

    assert first.status_code == 201
    assert_error(second, status_code=429, code="RATE_LIMITED")
    assert still_reads.status_code == 200


@pytest.mark.integration
def test_one_throttled_user_never_spends_another_users_allowance(
    engine: Engine, redis_connection: Redis
) -> None:
    """Limits are per User, so a noisy account cannot lock everyone else out."""
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    app, flow, _ = build_app(
        clock,
        provider,
        rate_limiter=RedisRateLimiter(redis_connection, now=clock),
        read_requests_per_minute=1,
    )
    throttled = Browser(app)
    sign_in(throttled, flow)
    throttled.get("/api/v1/workspaces")

    provider.identify(subject="216000111222333", email="second@example.com", name="Second")
    neighbour = Browser(app)
    sign_in(neighbour, flow)

    assert_error(throttled.get("/api/v1/workspaces"), status_code=429, code="RATE_LIMITED")
    assert neighbour.get("/api/v1/workspaces").status_code == 200


def _create_project(browser: Browser, workspace_id: UUID, *, key: str) -> Response:
    """Create one Project through the public write surface under test."""
    return browser.request(
        "POST",
        f"/api/v1/projects?workspace_id={workspace_id}",
        headers={"Idempotency-Key": key},
        json={"name": "Limits", "sourceKind": "upload"},
    )


def _workspace_with_project(engine: Engine, *, suffix: str) -> tuple[UUID, UUID, UUID]:
    """Provision one Workspace owner and an active Project that jobs can belong to."""
    user_id, workspace_id = provision_identity(engine, suffix=f"{suffix}-{uuid4().hex[:8]}")
    project_id = uuid4()
    with _tenant_session(workspace_id, user_id) as session:
        session.add(
            Project(
                id=project_id,
                workspace_id=workspace_id,
                created_by_user_id=user_id,
                name="Limits",
                source_kind=SourceKind.UPLOAD,
            )
        )
    return user_id, workspace_id, project_id


def _tenant_session(workspace_id: UUID, user_id: UUID) -> AbstractContextManager[Session]:
    """Open one least-privilege transaction already holding this tenant's row context."""
    return session_scope(settings=runtime_settings(), workspace_id=workspace_id, user_id=user_id)
