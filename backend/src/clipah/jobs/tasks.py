"""The one Celery task that runs durable work, and the stage runners it dispatches to."""

from __future__ import annotations

import random
from collections.abc import Callable, Iterator, MutableMapping
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from uuid import UUID

from celery import Task, signals
from celery.exceptions import MaxRetriesExceededError
from redis import Redis, RedisError
from sqlalchemy.orm import Session

from clipah.celery_app import (
    MAX_ATTEMPTS,
    RETRY_BASE_SECONDS,
    RETRY_MAX_SECONDS,
    create_celery_app,
    queue_for,
    settings_for,
)
from clipah.config import Settings
from clipah.db import RuntimeRole, session_scope
from clipah.jobs.admission import admission_policy
from clipah.jobs.events import (
    JobEventNotifier,
    PollingJobEventNotifier,
    RedisJobEventNotifier,
)
from clipah.jobs.models import JobCancelledError, JobContext, RetryableJobError, TerminalJobError
from clipah.jobs.pipeline import advance_after
from clipah.jobs.use_cases import cancel_job, complete_job_after_runner, fail_job, start_job
from clipah.models import JobKind, JobStatus
from clipah.workspaces.authorization import DatabaseWorkspaceAuthorizer
from clipah.workspaces.models import WorkspaceAction

StageRunner = Callable[[JobContext], None]

celery_app = create_celery_app()
UNSUPPORTED_KIND_CODE = "JOB_KIND_UNSUPPORTED"
INTERNAL_ERROR_CODE = "INTERNAL_ERROR"
_STAGE_RUNNERS: dict[JobKind, StageRunner] = {}


def stage_runners() -> MutableMapping[JobKind, StageRunner]:
    """Return the registry later tasks install their real stage implementations into."""
    return _STAGE_RUNNERS


def register_stage_runner(kind: JobKind, runner: StageRunner) -> None:
    """Bind one kind of durable work to the code that performs it."""
    _STAGE_RUNNERS[kind] = runner


def retry_countdown(attempt: int) -> float:
    """Back off exponentially with jitter so a failing provider is not stampeded."""
    ceiling = min(RETRY_BASE_SECONDS * 2 ** max(attempt - 1, 0), RETRY_MAX_SECONDS)
    return random.uniform(ceiling / 2, ceiling)


# Celery ships no type information, so its decorator erases the signature below.
@celery_app.task(bind=True, name="clipah.jobs.run_job", max_retries=MAX_ATTEMPTS - 1)  # type: ignore[untyped-decorator]
def run_job(self: Task, job_id: str, workspace_id: str, user_id: str) -> str:
    """Run one durable job from its identifiers alone, proving standing again first.

    Only UUID strings cross the broker: no ORM object, Session, or credential is ever
    serialized into a message a queue could replay or leak.
    """
    settings = settings_for(self.app)
    identifiers = (UUID(job_id), UUID(workspace_id), UUID(user_id))
    job, workspace, user = identifiers
    notifier = _notifier(settings)

    with _transaction(settings, workspace, user) as session:
        DatabaseWorkspaceAuthorizer(session).require(
            user_id=user, workspace_id=workspace, action=WorkspaceAction.PROJECT_WRITE
        )
        snapshot = start_job(session, workspace_id=workspace, job_id=job, now=_now())
    _announce(notifier, workspace_id=workspace, job_id=job)

    context = JobContext(
        job_id=job,
        workspace_id=workspace,
        project_id=snapshot.project_id,
        user_id=user,
        attempt=snapshot.attempt,
        settings=settings,
    )
    runner = stage_runners().get(snapshot.kind)
    if runner is None:
        _end_attempt(settings, context, error_code=UNSUPPORTED_KIND_CODE, retryable=False)
        _announce(notifier, workspace_id=workspace, job_id=job)
        raise RuntimeError(f"no stage runner is registered for {snapshot.kind.value}")

    try:
        runner(context)
    except JobCancelledError:
        with _transaction(settings, workspace, user) as session:
            cancel_job(session, workspace_id=workspace, job_id=job, now=_now())
        _announce(notifier, workspace_id=workspace, job_id=job)
        return JobStatus.CANCELED.value
    except RetryableJobError as error:
        _end_attempt(settings, context, error_code=_code_of(error), retryable=True)
        _announce(notifier, workspace_id=workspace, job_id=job)
        try:
            raise self.retry(countdown=retry_countdown(snapshot.attempt)) from error
        except MaxRetriesExceededError:
            # A job that keeps its Workspace's concurrency slot forever is worse than
            # one that ends honestly, so an exhausted budget is a permanent failure.
            _end_attempt(settings, context, error_code=_code_of(error), retryable=False)
            _announce(notifier, workspace_id=workspace, job_id=job)
            raise
    except TerminalJobError as error:
        _end_attempt(settings, context, error_code=_code_of(error), retryable=False)
        _announce(notifier, workspace_id=workspace, job_id=job)
        raise
    except Exception:
        _end_attempt(settings, context, error_code=INTERNAL_ERROR_CODE, retryable=False)
        _announce(notifier, workspace_id=workspace, job_id=job)
        raise

    with _transaction(settings, workspace, user) as session:
        completed = complete_job_after_runner(
            session,
            workspace_id=workspace,
            job_id=job,
            now=_now(),
        )
        # The successor is committed in the same transaction as the completion, so a
        # Project can never be recorded as finished with one stage and stranded before
        # the next. Dispatch happens afterwards, and is only a wakeup: the durable row
        # is what a sweep would find.
        following = advance_after(
            session,
            policy=admission_policy(settings),
            access=DatabaseWorkspaceAuthorizer(session).access_for(
                user_id=user, workspace_id=workspace
            ),
            project_id=snapshot.project_id,
            completed_kind=snapshot.kind,
            completed_job_id=job,
            now=_now(),
        )
        following_id = None if following is None else following.job_id
        following_kind = None if following is None else following.kind
    _announce(notifier, workspace_id=workspace, job_id=job)
    if following_id is not None and following_kind is not None:
        _dispatch_next(
            job_id=following_id, workspace_id=workspace, user_id=user, kind=following_kind
        )
        _announce(notifier, workspace_id=workspace, job_id=following_id)
    return completed.status.value


def _dispatch_next(*, job_id: UUID, workspace_id: UUID, user_id: UUID, kind: JobKind) -> None:
    """Wake the next stage without letting a broker failure undo finished work."""
    try:
        run_job.apply_async(
            args=(str(job_id), str(workspace_id), str(user_id)), queue=queue_for(kind)
        )
    except Exception:
        return


def _end_attempt(
    settings: Settings, context: JobContext, *, error_code: str, retryable: bool
) -> None:
    """Record the outcome of one failed attempt in its own short transaction."""
    with _transaction(settings, context.workspace_id, context.user_id) as session:
        fail_job(
            session,
            workspace_id=context.workspace_id,
            job_id=context.job_id,
            error_code=error_code,
            retryable=retryable,
            now=_now(),
        )


def _code_of(error: RetryableJobError | TerminalJobError) -> str:
    """Use the stable code a stage runner raised, or a generic one if it named none."""
    return str(error) or INTERNAL_ERROR_CODE


@contextmanager
def _transaction(settings: Settings, workspace_id: UUID, user_id: UUID) -> Iterator[Session]:
    """Open one short worker transaction holding this tenant's row context."""
    with session_scope(
        settings=settings,
        workspace_id=workspace_id,
        user_id=user_id,
        runtime_role=RuntimeRole.WORKER,
    ) as session:
        yield session


def _announce(notifier: JobEventNotifier, *, workspace_id: UUID, job_id: UUID) -> None:
    """Wake subscribers after a committed transition, never at the cost of the job.

    The durable history in Postgres is the record; a wakeup is only an optimization,
    so an unreachable broker must not fail work that already succeeded.
    """
    try:
        notifier.notify(workspace_id=workspace_id, job_id=job_id)
    except RedisError:
        return


def _notifier(settings: Settings) -> JobEventNotifier:
    """Return the wakeup transport this deployment was configured for."""
    if settings.redis_url is None:
        return PollingJobEventNotifier()
    return RedisJobEventNotifier(_redis(settings.redis_url))


@lru_cache(maxsize=4)
def _redis(redis_url: str) -> Redis:
    """Share one connection pool per configured Redis URL in this process."""
    return Redis.from_url(redis_url)


def _now() -> datetime:
    """Return the instant a worker records its transitions at."""
    return datetime.now(tz=UTC)


from clipah.jobs.analyze_task import analyze_stage_runner  # noqa: E402
from clipah.jobs.ingest_task import (  # noqa: E402
    ingest_stage_runner,
    validate_ingest_readiness,
)
from clipah.jobs.source_import_task import source_import_stage_runner  # noqa: E402
from clipah.jobs.transcribe_task import transcribe_stage_runner  # noqa: E402

_STAGE_RUNNERS.setdefault(JobKind.SOURCE_IMPORT, source_import_stage_runner)
_STAGE_RUNNERS.setdefault(JobKind.INGEST, ingest_stage_runner)
_STAGE_RUNNERS.setdefault(JobKind.TRANSCRIBE, transcribe_stage_runner)
_STAGE_RUNNERS.setdefault(JobKind.ANALYZE, analyze_stage_runner)


def _worker_accepts_ingest(queues: object) -> bool:
    """Treat the default worker or any explicitly named ingest queue as ingest-capable."""
    if queues is None:
        return True
    if isinstance(queues, str):
        names = {name.strip() for name in queues.split(",")}
    elif isinstance(queues, (tuple, list, set, frozenset)):
        names = {str(getattr(queue, "name", queue)).strip() for queue in queues}
    else:
        return False
    return "ingest" in names


@signals.celeryd_init.connect  # type: ignore[untyped-decorator]
def _validate_ingest_worker_startup(
    *, options: dict[str, object] | None = None, **_kwargs: object
) -> None:
    """Validate native ingest dependencies before an ingest-capable worker starts."""
    if _worker_accepts_ingest((options or {}).get("queues")):
        validate_ingest_readiness()
