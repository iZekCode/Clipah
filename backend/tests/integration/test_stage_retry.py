"""A pipeline stage that failed can be tried again, and a failure no longer strands a Project.

Preparing the video, transcribing it, and finding its moments each move the Project to
`failed` when they fail for good. Retrying queues the same stage again, puts the Project
back in that stage's status, and hands the Job to the worker queue for its kind.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select

from clipah.db import RuntimeRole, session_scope
from clipah.jobs.admission import admission_policy
from clipah.jobs.use_cases import (
    create_job,
    fail_job,
    request_job_cancellation,
    start_job,
    succeed_job,
)
from clipah.models import (
    Job,
    JobKind,
    JobStatus,
    Project,
    ProjectStatus,
    PublishingRolePolicy,
    WorkspaceRole,
)
from clipah.source_imports.dispatch import RecordingJobDispatcher
from clipah.workspaces.models import WorkspaceAccess
from harness import NOW, Browser, Clock, StubGoogleProvider, assert_error, build_app, sign_in
from support import runtime_settings

STAGE_STATUS = {
    JobKind.INGEST: ProjectStatus.INGESTING,
    JobKind.TRANSCRIBE: ProjectStatus.TRANSCRIBING,
    JobKind.ANALYZE: ProjectStatus.ANALYZING,
}


class _Studio:
    """One signed-in member, one Project, and the dispatcher their retries go through."""

    def __init__(self) -> None:
        clock = Clock(NOW)
        self.dispatcher = RecordingJobDispatcher()
        app, flow, _ = build_app(clock, StubGoogleProvider(clock), job_dispatcher=self.dispatcher)
        self.browser = Browser(app)
        sign_in(self.browser, flow)
        self.workspace_id = UUID(
            self.browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"]
        )
        self.user_id = UUID(self.browser.get("/api/v1/me").json()["id"])
        created = self.browser.request(
            "POST",
            f"/api/v1/projects?workspace_id={self.workspace_id}",
            headers={"Idempotency-Key": f"retry-{uuid4()}"},
            json={"name": "Retry", "sourceKind": "upload"},
        )
        assert created.status_code == 201
        self.project_id = UUID(created.json()["id"])

    def run_stage(self, kind: JobKind, *, outcome: str) -> UUID:
        """Put the Project in the stage's status, then admit, start, and end one Job of it."""
        now = datetime.now(tz=UTC)
        with self._session(RuntimeRole.API) as session:
            project = session.get(Project, self.project_id)
            assert project is not None
            project.status = STAGE_STATUS[kind]
            if kind is JobKind.ANALYZE:
                project.transcript_count = 1
            job_id = create_job(
                session,
                policy=admission_policy(runtime_settings()),
                access=self._access(),
                project_id=self.project_id,
                kind=kind,
                idempotency_key=f"stage-{uuid4()}",
                now=now,
            ).job_id
        with self._session(RuntimeRole.WORKER) as session:
            if outcome == "canceled":
                request_job_cancellation(
                    session, workspace_id=self.workspace_id, job_id=job_id, now=now
                )
                return job_id
            start_job(session, workspace_id=self.workspace_id, job_id=job_id, now=now)
            if outcome == "succeeded":
                succeed_job(session, workspace_id=self.workspace_id, job_id=job_id, now=now)
            else:
                fail_job(
                    session,
                    workspace_id=self.workspace_id,
                    job_id=job_id,
                    error_code="STAGE_BROKE",
                    retryable=False,
                    now=now,
                )
        return job_id

    def retry(self):  # type: ignore[no-untyped-def]
        return self.browser.request(
            "POST", f"/api/v1/projects/{self.project_id}/retry?workspace_id={self.workspace_id}"
        )

    def status(self, engine: Engine) -> ProjectStatus:
        with engine.connect() as connection:
            status = connection.scalar(select(Project.status).where(Project.id == self.project_id))
        assert status is not None
        return status

    def _access(self) -> WorkspaceAccess:
        return WorkspaceAccess(
            workspace_id=self.workspace_id,
            user_id=self.user_id,
            role=WorkspaceRole.OWNER,
            publishing_role_policy=PublishingRolePolicy.OWNER_ADMIN_EDITOR,
        )

    def _session(self, role: RuntimeRole):  # type: ignore[no-untyped-def]
        return session_scope(
            settings=runtime_settings(role),
            workspace_id=self.workspace_id,
            user_id=self.user_id,
            runtime_role=role,
        )


@pytest.mark.integration
@pytest.mark.parametrize("kind", [JobKind.INGEST, JobKind.TRANSCRIBE])
@pytest.mark.parametrize("outcome", ["failed", "canceled"])
def test_a_preparation_or_transcription_that_ends_badly_fails_the_project(
    engine: Engine, clean_database: None, kind: JobKind, outcome: str
) -> None:
    """A stranded Project said it was still working; now it says it failed."""
    del clean_database
    studio = _Studio()

    studio.run_stage(kind, outcome=outcome)

    assert studio.status(engine) is ProjectStatus.FAILED


@pytest.mark.integration
@pytest.mark.parametrize("kind", [JobKind.INGEST, JobKind.TRANSCRIBE, JobKind.ANALYZE])
def test_retrying_queues_the_stage_that_failed_again(
    engine: Engine, clean_database: None, kind: JobKind
) -> None:
    """The same kind of work, the stage's own status, and its own worker queue."""
    del clean_database
    studio = _Studio()
    failed = studio.run_stage(kind, outcome="failed")

    response = studio.retry()

    assert response.status_code == 202
    job_id = UUID(response.json()["jobId"])
    assert job_id != failed
    assert response.json()["kind"] == kind.value
    assert studio.status(engine) is STAGE_STATUS[kind]
    with engine.connect() as connection:
        job = connection.execute(select(Job.kind, Job.status).where(Job.id == job_id)).one()
    assert (job.kind, job.status) == (kind, JobStatus.QUEUED)
    assert studio.dispatcher.calls == [(job_id, studio.workspace_id, studio.user_id)]


@pytest.mark.integration
def test_nothing_is_retried_while_the_project_is_fine_or_already_working(
    engine: Engine, clean_database: None
) -> None:
    """A retry answers only a failure; a second click while the first runs is refused."""
    del clean_database
    studio = _Studio()

    refused = studio.retry()
    assert_error(refused, status_code=409, code="NOTHING_TO_RETRY")
    assert "needs retrying" in refused.json()["error"]["message"]

    studio.run_stage(JobKind.INGEST, outcome="failed")
    assert studio.retry().status_code == 202
    assert_error(studio.retry(), status_code=409, code="NOTHING_TO_RETRY")
    assert studio.status(engine) is ProjectStatus.INGESTING


@pytest.mark.integration
def test_a_later_stage_that_succeeded_is_not_undone_by_an_older_failure(
    engine: Engine, clean_database: None
) -> None:
    """Only the most recent stage decides what a retry would repeat."""
    del clean_database
    studio = _Studio()
    studio.run_stage(JobKind.INGEST, outcome="failed")
    studio.run_stage(JobKind.INGEST, outcome="succeeded")
    studio.run_stage(JobKind.TRANSCRIBE, outcome="succeeded")

    assert_error(studio.retry(), status_code=409, code="NOTHING_TO_RETRY")


@pytest.mark.integration
def test_a_retry_is_refused_for_a_project_the_member_cannot_see(
    engine: Engine, clean_database: None
) -> None:
    """Another Workspace's Project is answered exactly like one that does not exist."""
    del clean_database, engine
    studio = _Studio()

    response = studio.browser.request(
        "POST", f"/api/v1/projects/{uuid4()}/retry?workspace_id={studio.workspace_id}"
    )

    assert_error(response, status_code=404, code="NOT_FOUND")
