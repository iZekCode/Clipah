"""Integration contracts for durable job state, cancellation, and Celery execution."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from celery import Celery
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from clipah.celery_app import (
    MAX_ATTEMPTS,
    QUEUE_FOR_JOB_KIND,
    configure_celery,
    create_celery_app,
)
from clipah.db import RuntimeRole, session_scope
from clipah.jobs import tasks as task_module
from clipah.jobs.admission import ConcurrencyLimitError, admission_policy
from clipah.jobs.models import (
    InvalidJobTransitionError,
    JobCancelledError,
    JobContext,
    JobEventType,
    JobNotFoundError,
    RetryableJobError,
    TerminalJobError,
)
from clipah.jobs.tasks import run_job, stage_runners
from clipah.jobs.use_cases import (
    create_job,
    fail_job,
    job_events,
    job_snapshot,
    request_job_cancellation,
    start_job,
    succeed_job,
    update_job_progress,
)
from clipah.models import (
    JobKind,
    JobStatus,
    Project,
    PublishingRolePolicy,
    SourceKind,
    WorkspaceMembership,
    WorkspaceRole,
)
from clipah.workspaces.models import WorkspaceAccess
from harness import NOW, Browser, Clock, StubGoogleProvider, assert_error, build_app, sign_in
from support import provision_identity, runtime_settings

QUEUED_STAGE = "queued"


@pytest.fixture
def clock() -> Clock:
    """Give every job test one hand-wound clock its timestamps are measured against."""
    return Clock(NOW)


@pytest.fixture(autouse=True)
def _isolated_stage_runners() -> Iterator[None]:
    """Keep one test's fake stage runner out of every other test."""
    original = dict(stage_runners())
    yield
    stage_runners().clear()
    stage_runners().update(original)


@pytest.mark.integration
def test_creating_a_job_admits_it_and_records_queued_state(engine: Engine, clock: Clock) -> None:
    """A durable job must exist before any worker is told about it."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="create")

    with _api_session(workspace_id, user_id) as session:
        snapshot = create_job(
            session,
            policy=admission_policy(runtime_settings()),
            access=_access(workspace_id, user_id),
            project_id=project_id,
            kind=JobKind.INGEST,
            idempotency_key="create-one",
            now=clock(),
        )
        job_id = snapshot.job_id
        assert snapshot.status is JobStatus.QUEUED

    with _api_session(workspace_id, user_id) as session:
        stored = job_snapshot(session, workspace_id=workspace_id, job_id=job_id)
        events = job_events(session, workspace_id=workspace_id, job_id=job_id)
        assert stored.attempt == 0
        assert [event.event_type for event in events] == [JobEventType.CREATED]


@pytest.mark.integration
def test_repeating_one_idempotency_key_returns_the_same_job(engine: Engine, clock: Clock) -> None:
    """A retried submit must never create a second unit of paid work."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="idempotent")
    first = _queued_job(workspace_id, user_id, project_id, clock, key="same-key")

    with _api_session(workspace_id, user_id) as session:
        second = create_job(
            session,
            policy=admission_policy(runtime_settings()),
            access=_access(workspace_id, user_id),
            project_id=project_id,
            kind=JobKind.INGEST,
            idempotency_key="same-key",
            now=clock(),
        )
        assert second.job_id == first


@pytest.mark.integration
def test_creation_refuses_work_beyond_the_concurrency_allowance(
    engine: Engine, clock: Clock
) -> None:
    """Job creation is where plan limits are spent, so the worker never sees illegal work."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="admission")
    policy = admission_policy(runtime_settings(concurrent_jobs_per_workspace=1))

    with _api_session(workspace_id, user_id) as session:
        create_job(
            session,
            policy=policy,
            access=_access(workspace_id, user_id),
            project_id=project_id,
            kind=JobKind.INGEST,
            idempotency_key="first",
            now=clock(),
        )

    with _api_session(workspace_id, user_id) as session, pytest.raises(ConcurrencyLimitError):
        create_job(
            session,
            policy=policy,
            access=_access(workspace_id, user_id),
            project_id=project_id,
            kind=JobKind.INGEST,
            idempotency_key="second",
            now=clock(),
        )


@pytest.mark.integration
def test_a_queued_job_starts_running_and_records_its_attempt(engine: Engine, clock: Clock) -> None:
    """Starting is the transition that makes a worker's ownership of the job durable."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="start")
    job_id = _queued_job(workspace_id, user_id, project_id, clock, key="start")

    with _worker_session(workspace_id, user_id) as session:
        started = start_job(session, workspace_id=workspace_id, job_id=job_id, now=clock())
        assert started.status is JobStatus.RUNNING
        assert started.attempt == 1

    assert _event_types(workspace_id, user_id, job_id) == [
        JobEventType.CREATED,
        JobEventType.STARTED,
    ]


@pytest.mark.integration
def test_progress_cannot_be_reported_for_a_job_that_never_started(
    engine: Engine, clock: Clock
) -> None:
    """Progress on a queued job would report work nobody is doing."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="progress")
    job_id = _queued_job(workspace_id, user_id, project_id, clock, key="progress")

    with (
        _worker_session(workspace_id, user_id) as session,
        pytest.raises(InvalidJobTransitionError),
    ):
        update_job_progress(
            session,
            workspace_id=workspace_id,
            job_id=job_id,
            stage="probing",
            progress=0.5,
            now=clock(),
        )


@pytest.mark.integration
def test_a_running_job_reports_stage_progress(engine: Engine, clock: Clock) -> None:
    """The dashboard can only show honest progress if the worker records it."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="stage")
    job_id = _queued_job(workspace_id, user_id, project_id, clock, key="stage")

    with _worker_session(workspace_id, user_id) as session:
        start_job(session, workspace_id=workspace_id, job_id=job_id, now=clock())
        updated = update_job_progress(
            session,
            workspace_id=workspace_id,
            job_id=job_id,
            stage="probing",
            progress=0.25,
            now=clock(),
        )
        assert updated.stage == "probing"
        assert updated.progress == pytest.approx(0.25)

    assert _event_types(workspace_id, user_id, job_id)[-1] is JobEventType.PROGRESS


@pytest.mark.integration
def test_a_terminal_job_emits_its_terminal_event_exactly_once(engine: Engine, clock: Clock) -> None:
    """A duplicated success would tell every subscriber the work happened twice."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="terminal")
    job_id = _queued_job(workspace_id, user_id, project_id, clock, key="terminal")

    with _worker_session(workspace_id, user_id) as session:
        start_job(session, workspace_id=workspace_id, job_id=job_id, now=clock())
        succeed_job(session, workspace_id=workspace_id, job_id=job_id, now=clock())

    with (
        _worker_session(workspace_id, user_id) as session,
        pytest.raises(InvalidJobTransitionError),
    ):
        succeed_job(session, workspace_id=workspace_id, job_id=job_id, now=clock())

    assert _event_types(workspace_id, user_id, job_id).count(JobEventType.SUCCEEDED) == 1


@pytest.mark.integration
def test_a_recoverable_failure_returns_the_job_to_retrying(engine: Engine, clock: Clock) -> None:
    """A transient provider error must leave the same job recoverable, not dead."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="retrying")
    job_id = _queued_job(workspace_id, user_id, project_id, clock, key="retrying")

    with _worker_session(workspace_id, user_id) as session:
        start_job(session, workspace_id=workspace_id, job_id=job_id, now=clock())
        failed = fail_job(
            session,
            workspace_id=workspace_id,
            job_id=job_id,
            error_code="PROVIDER_UNAVAILABLE",
            retryable=True,
            now=clock(),
        )
        assert failed.status is JobStatus.RETRYING

    with _worker_session(workspace_id, user_id) as session:
        restarted = start_job(session, workspace_id=workspace_id, job_id=job_id, now=clock())
        assert restarted.status is JobStatus.RUNNING
        assert restarted.job_id == job_id
        assert restarted.attempt == 2


@pytest.mark.integration
def test_an_unrecoverable_failure_is_terminal(engine: Engine, clock: Clock) -> None:
    """A permanent failure must stop consuming attempts and stay honest about it."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="failed")
    job_id = _queued_job(workspace_id, user_id, project_id, clock, key="failed")

    with _worker_session(workspace_id, user_id) as session:
        start_job(session, workspace_id=workspace_id, job_id=job_id, now=clock())
        failed = fail_job(
            session,
            workspace_id=workspace_id,
            job_id=job_id,
            error_code="ASSET_INVALID_CODEC",
            retryable=False,
            now=clock(),
        )
        assert failed.status is JobStatus.FAILED
        assert failed.error_code == "ASSET_INVALID_CODEC"

    with (
        _worker_session(workspace_id, user_id) as session,
        pytest.raises(InvalidJobTransitionError),
    ):
        start_job(session, workspace_id=workspace_id, job_id=job_id, now=clock())


@pytest.mark.integration
def test_cancelling_a_queued_job_reaches_a_terminal_state_without_running(
    engine: Engine, clock: Clock
) -> None:
    """Work nobody has started can be truthfully cancelled immediately."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="cancel-queued")
    job_id = _queued_job(workspace_id, user_id, project_id, clock, key="cancel-queued")

    with _api_session(workspace_id, user_id) as session:
        cancelled = request_job_cancellation(
            session, workspace_id=workspace_id, job_id=job_id, now=clock()
        )
        assert cancelled.status is JobStatus.CANCELED

    assert _event_types(workspace_id, user_id, job_id)[-1] is JobEventType.CANCELED


@pytest.mark.integration
def test_cancelling_a_running_job_records_the_request_for_the_worker(
    engine: Engine, clock: Clock
) -> None:
    """Only the worker can end running work, so the API records intent instead."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="cancel-running")
    job_id = _queued_job(workspace_id, user_id, project_id, clock, key="cancel-running")

    with _worker_session(workspace_id, user_id) as session:
        start_job(session, workspace_id=workspace_id, job_id=job_id, now=clock())
    with _api_session(workspace_id, user_id) as session:
        requested = request_job_cancellation(
            session, workspace_id=workspace_id, job_id=job_id, now=clock()
        )
        assert requested.status is JobStatus.CANCEL_REQUESTED
        assert requested.cancel_requested_at is not None


@pytest.mark.integration
def test_a_terminal_job_cannot_be_cancelled(engine: Engine, clock: Clock) -> None:
    """Cancelling finished work would report a state the system cannot deliver."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="cancel-terminal")
    job_id = _queued_job(workspace_id, user_id, project_id, clock, key="cancel-terminal")

    with _worker_session(workspace_id, user_id) as session:
        start_job(session, workspace_id=workspace_id, job_id=job_id, now=clock())
        succeed_job(session, workspace_id=workspace_id, job_id=job_id, now=clock())

    with _api_session(workspace_id, user_id) as session, pytest.raises(InvalidJobTransitionError):
        request_job_cancellation(session, workspace_id=workspace_id, job_id=job_id, now=clock())


@pytest.mark.integration
def test_a_job_context_refuses_to_continue_once_cancellation_is_requested(
    engine: Engine, clock: Clock
) -> None:
    """Cancellation is only honest if a worker checks it between stages."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="context")
    job_id = _queued_job(workspace_id, user_id, project_id, clock, key="context")

    with _worker_session(workspace_id, user_id) as session:
        start_job(session, workspace_id=workspace_id, job_id=job_id, now=clock())
    context = JobContext(
        job_id=job_id,
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
        attempt=1,
        settings=runtime_settings(RuntimeRole.WORKER),
    )
    context.raise_if_cancelled()

    with _api_session(workspace_id, user_id) as session:
        request_job_cancellation(session, workspace_id=workspace_id, job_id=job_id, now=clock())

    with pytest.raises(JobCancelledError):
        context.raise_if_cancelled()


@pytest.mark.integration
def test_a_missing_job_is_reported_as_missing(engine: Engine) -> None:
    """A Workspace must never be told anything about a Job it does not hold."""
    user_id, workspace_id, _ = _workspace_with_project(engine, suffix="missing")

    with _api_session(workspace_id, user_id) as session, pytest.raises(JobNotFoundError):
        job_snapshot(session, workspace_id=workspace_id, job_id=uuid4())


@pytest.mark.unit
def test_every_job_kind_is_routed_to_its_own_queue() -> None:
    """A render must never be able to starve the ingest queue behind it."""
    assert set(QUEUE_FOR_JOB_KIND) == set(JobKind)
    assert QUEUE_FOR_JOB_KIND[JobKind.RENDER] == "render"
    assert QUEUE_FOR_JOB_KIND[JobKind.ANALYZE] == "ai"
    assert QUEUE_FOR_JOB_KIND[JobKind.CLEANUP] == "maintenance"
    assert QUEUE_FOR_JOB_KIND[JobKind.SOURCE_IMPORT] == "source_import"


@pytest.mark.unit
def test_celery_configuration_keeps_provider_concurrency_outside_task_code() -> None:
    """Prefetch and retry policy are deployment settings, not decisions inside a task."""
    settings = runtime_settings(RuntimeRole.WORKER, redis_url="redis://localhost:56380/1")

    app = configure_celery(create_celery_app(), settings)

    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.task_acks_late is True
    assert app.conf.task_default_retry_delay > 0
    assert app.conf.broker_url == "redis://localhost:56380/1"


@pytest.mark.unit
def test_task_arguments_carry_only_identifier_strings() -> None:
    """An ORM object, session, or token in a broker message would leak a trust boundary."""
    job_id, workspace_id, user_id = uuid4(), uuid4(), uuid4()

    signature = run_job.s(str(job_id), str(workspace_id), str(user_id))

    assert signature.args == (str(job_id), str(workspace_id), str(user_id))
    assert all(isinstance(argument, str) for argument in signature.args)
    assert [UUID(argument) for argument in signature.args] == [job_id, workspace_id, user_id]


@pytest.mark.integration
def test_an_eager_task_runs_its_registered_stage_and_succeeds(engine: Engine, clock: Clock) -> None:
    """The whole point of a durable job is that finishing it is recorded."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="eager")
    job_id = _queued_job(workspace_id, user_id, project_id, clock, key="eager")
    seen: list[UUID] = []

    with _eager_celery():
        stage_runners()[JobKind.INGEST] = lambda context: seen.append(context.job_id)
        run_job.apply(args=(str(job_id), str(workspace_id), str(user_id))).get()

    assert seen == [job_id]
    assert _status(workspace_id, user_id, job_id) is JobStatus.SUCCEEDED
    assert _event_types(workspace_id, user_id, job_id)[-1] is JobEventType.SUCCEEDED


@pytest.mark.integration
def test_a_task_reloads_and_authorizes_the_workspace_before_working(
    engine: Engine, clock: Clock
) -> None:
    """A broker message is untrusted input; standing must be proven again in the worker."""
    owner_id, workspace_id, project_id = _workspace_with_project(engine, suffix="authorize")
    outsider_id, _ = provision_identity(engine, suffix=f"outsider-{uuid4().hex[:8]}")
    job_id = _queued_job(workspace_id, owner_id, project_id, clock, key="authorize")
    seen: list[UUID] = []

    with _eager_celery():
        stage_runners()[JobKind.INGEST] = lambda context: seen.append(context.job_id)
        result = run_job.apply(args=(str(job_id), str(workspace_id), str(outsider_id)))

    assert seen == []
    assert result.failed()
    assert _status(workspace_id, owner_id, job_id) is JobStatus.QUEUED


@pytest.mark.integration
def test_a_recoverable_provider_error_retries_the_same_job(engine: Engine, clock: Clock) -> None:
    """Retrying must resume the durable job, never fork a second one."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="retry")
    job_id = _queued_job(workspace_id, user_id, project_id, clock, key="retry")
    attempts: list[int] = []

    def flaky(context: JobContext) -> None:
        attempts.append(context.attempt)
        if len(attempts) == 1:
            raise RetryableJobError("PROVIDER_UNAVAILABLE")

    with _eager_celery():
        stage_runners()[JobKind.INGEST] = flaky
        run_job.apply(args=(str(job_id), str(workspace_id), str(user_id))).get()

    assert attempts == [1, 2]
    assert _status(workspace_id, user_id, job_id) is JobStatus.SUCCEEDED


@pytest.mark.integration
def test_a_terminal_stage_error_preserves_its_public_code(engine: Engine, clock: Clock) -> None:
    """Permanent source-policy failures must not collapse into an internal error."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="terminal")
    job_id = _queued_job(workspace_id, user_id, project_id, clock, key="terminal")

    def refuse(_: JobContext) -> None:
        raise TerminalJobError("SOURCE_PRIVATE")

    with _eager_celery():
        stage_runners()[JobKind.INGEST] = refuse
        result = run_job.apply(args=(str(job_id), str(workspace_id), str(user_id)))

    assert result.failed()
    with _api_session(workspace_id, user_id) as session:
        finished = job_snapshot(session, workspace_id=workspace_id, job_id=job_id)
    assert finished.status is JobStatus.FAILED
    assert finished.error_code == "SOURCE_PRIVATE"


@pytest.mark.integration
def test_a_job_that_exhausts_its_retry_budget_ends_as_failed(engine: Engine, clock: Clock) -> None:
    """Retrying forever would hold a concurrency slot no other work could ever use."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="exhausted")
    job_id = _queued_job(workspace_id, user_id, project_id, clock, key="exhausted")
    attempts: list[int] = []

    def always_unavailable(context: JobContext) -> None:
        attempts.append(context.attempt)
        raise RetryableJobError("PROVIDER_UNAVAILABLE")

    with _eager_celery():
        stage_runners()[JobKind.INGEST] = always_unavailable
        result = run_job.apply(args=(str(job_id), str(workspace_id), str(user_id)))

    assert attempts == list(range(1, MAX_ATTEMPTS + 1))
    assert result.failed()
    with _api_session(workspace_id, user_id) as session:
        finished = job_snapshot(session, workspace_id=workspace_id, job_id=job_id)
    assert finished.status is JobStatus.FAILED
    assert finished.error_code == "PROVIDER_UNAVAILABLE"


@pytest.mark.integration
def test_a_task_stops_between_stages_when_cancellation_was_requested(
    engine: Engine, clock: Clock
) -> None:
    """A cancelled job must reach `canceled`, not quietly finish its remaining stages."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="task-cancel")
    job_id = _queued_job(workspace_id, user_id, project_id, clock, key="task-cancel")
    stages: list[str] = []

    def two_stage(context: JobContext) -> None:
        stages.append("first")
        with _api_session(workspace_id, user_id) as session:
            request_job_cancellation(session, workspace_id=workspace_id, job_id=job_id, now=clock())
        context.raise_if_cancelled()
        stages.append("second")

    with _eager_celery():
        stage_runners()[JobKind.INGEST] = two_stage
        run_job.apply(args=(str(job_id), str(workspace_id), str(user_id))).get()

    assert stages == ["first"]
    assert _status(workspace_id, user_id, job_id) is JobStatus.CANCELED
    assert _event_types(workspace_id, user_id, job_id)[-1] is JobEventType.CANCELED


@pytest.mark.integration
def test_the_job_endpoint_shows_one_job_to_its_own_workspace(engine: Engine) -> None:
    """A member must be able to follow the work their Workspace paid for."""
    clock = Clock(NOW)
    browser, workspace_id = _signed_in_workspace(clock)
    user_id = _owner_of(engine, workspace_id)
    project_id = _project_in(workspace_id, user_id)
    job_id = _queued_job(workspace_id, user_id, project_id, clock, key="http-show")

    response = browser.get(f"/api/v1/jobs/{job_id}?workspace_id={workspace_id}")
    body = response.json()

    assert response.status_code == 200
    assert body["id"] == str(job_id)
    assert body["workspaceId"] == str(workspace_id)
    assert body["projectId"] == str(project_id)
    assert body["kind"] == "ingest"
    assert body["status"] == "queued"
    assert body["stage"] == QUEUED_STAGE
    assert body["progress"] == 0.0
    assert body["attempt"] == 0
    assert body["errorCode"] is None


@pytest.mark.integration
def test_a_guessed_job_identifier_is_answered_like_a_missing_one(engine: Engine) -> None:
    """A distinguishable refusal would confirm another Workspace's job exists."""
    clock = Clock(NOW)
    browser, workspace_id = _signed_in_workspace(clock)
    stranger_user, stranger_workspace, stranger_project = _workspace_with_project(
        engine, suffix="stranger"
    )
    foreign_job = _queued_job(
        stranger_workspace, stranger_user, stranger_project, clock, key="stranger"
    )

    guessed = browser.get(f"/api/v1/jobs/{uuid4()}?workspace_id={workspace_id}")
    foreign = browser.get(f"/api/v1/jobs/{foreign_job}?workspace_id={workspace_id}")

    assert_error(guessed, status_code=404, code="NOT_FOUND")
    assert_error(foreign, status_code=404, code="NOT_FOUND")
    assert foreign.json()["error"]["message"] == guessed.json()["error"]["message"]


@pytest.mark.integration
def test_the_cancel_endpoint_records_a_cancellation_request(engine: Engine) -> None:
    """Cancelling from the dashboard must reach the same durable state machine."""
    clock = Clock(NOW)
    browser, workspace_id = _signed_in_workspace(clock)
    user_id = _owner_of(engine, workspace_id)
    project_id = _project_in(workspace_id, user_id)
    job_id = _queued_job(workspace_id, user_id, project_id, clock, key="http-cancel")

    response = browser.request("POST", f"/api/v1/jobs/{job_id}/cancel?workspace_id={workspace_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "canceled"


@pytest.mark.integration
def test_cancelling_a_finished_job_conflicts(engine: Engine) -> None:
    """The API must not pretend it can cancel work that already ended."""
    clock = Clock(NOW)
    browser, workspace_id = _signed_in_workspace(clock)
    user_id = _owner_of(engine, workspace_id)
    project_id = _project_in(workspace_id, user_id)
    job_id = _queued_job(workspace_id, user_id, project_id, clock, key="http-conflict")
    with _worker_session(workspace_id, user_id) as session:
        start_job(session, workspace_id=workspace_id, job_id=job_id, now=clock())
        succeed_job(session, workspace_id=workspace_id, job_id=job_id, now=clock())

    response = browser.request("POST", f"/api/v1/jobs/{job_id}/cancel?workspace_id={workspace_id}")

    assert_error(response, status_code=409, code="CONFLICT")


def _signed_in_workspace(clock: Clock) -> tuple[Browser, UUID]:
    """Drive one full login ceremony and return the browser and its personal Workspace."""
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    return browser, UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])


def _access(workspace_id: UUID, user_id: UUID) -> WorkspaceAccess:
    """Stand in for the Workspace standing an HTTP caller has already proven."""
    return WorkspaceAccess(
        workspace_id=workspace_id,
        user_id=user_id,
        role=WorkspaceRole.OWNER,
        publishing_role_policy=PublishingRolePolicy.OWNER_ADMIN_EDITOR,
    )


def _queued_job(
    workspace_id: UUID, user_id: UUID, project_id: UUID, clock: Clock, *, key: str
) -> UUID:
    """Create one admitted, queued Job the way the API would."""
    with _api_session(workspace_id, user_id) as session:
        snapshot = create_job(
            session,
            policy=admission_policy(runtime_settings()),
            access=_access(workspace_id, user_id),
            project_id=project_id,
            kind=JobKind.INGEST,
            idempotency_key=key,
            now=clock(),
            estimated_units=Decimal(1),
        )
        return snapshot.job_id


def _event_types(workspace_id: UUID, user_id: UUID, job_id: UUID) -> list[JobEventType]:
    """Read the durable event history one subscriber would replay."""
    with _api_session(workspace_id, user_id) as session:
        return [
            event.event_type
            for event in job_events(session, workspace_id=workspace_id, job_id=job_id)
        ]


def _status(workspace_id: UUID, user_id: UUID, job_id: UUID) -> JobStatus:
    """Read the durable status a later reader would observe."""
    with _api_session(workspace_id, user_id) as session:
        return job_snapshot(session, workspace_id=workspace_id, job_id=job_id).status


def _project_in(workspace_id: UUID, user_id: UUID) -> UUID:
    """Create one active Project inside an existing Workspace."""
    project_id = uuid4()
    with _api_session(workspace_id, user_id) as session:
        session.add(
            Project(
                id=project_id,
                workspace_id=workspace_id,
                created_by_user_id=user_id,
                name="Jobs",
                source_kind=SourceKind.UPLOAD,
            )
        )
    return project_id


def _owner_of(engine: Engine, workspace_id: UUID) -> UUID:
    """Return the owner of one Workspace created through the login ceremony."""
    with Session(engine) as session:
        return session.scalars(
            select(WorkspaceMembership.user_id).where(
                WorkspaceMembership.workspace_id == workspace_id
            )
        ).one()


def _workspace_with_project(engine: Engine, *, suffix: str) -> tuple[UUID, UUID, UUID]:
    """Provision one Workspace owner and an active Project jobs can belong to."""
    user_id, workspace_id = provision_identity(engine, suffix=f"{suffix}-{uuid4().hex[:8]}")
    return user_id, workspace_id, _project_in(workspace_id, user_id)


def _api_session(workspace_id: UUID, user_id: UUID) -> AbstractContextManager[Session]:
    """Open one API-role transaction already holding this tenant's row context."""
    return session_scope(settings=runtime_settings(), workspace_id=workspace_id, user_id=user_id)


def _worker_session(workspace_id: UUID, user_id: UUID) -> AbstractContextManager[Session]:
    """Open one worker-role transaction already holding this tenant's row context."""
    return session_scope(
        settings=runtime_settings(RuntimeRole.WORKER),
        workspace_id=workspace_id,
        user_id=user_id,
        runtime_role=RuntimeRole.WORKER,
    )


@contextmanager
def _eager_celery() -> Iterator[Celery]:
    """Run Celery tasks in-process so a whole retry sequence stays deterministic."""
    app = configure_celery(
        task_module.celery_app,
        runtime_settings(RuntimeRole.WORKER, redis_url="redis://localhost:56380/1"),
    )
    restore: dict[str, object] = {
        name: app.conf[name]
        for name in ("task_always_eager", "task_eager_propagates", "task_default_retry_delay")
    }
    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = False
    app.conf.task_default_retry_delay = 0
    try:
        yield app
    finally:
        app.conf.update(restore)
