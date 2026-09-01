"""Integration contracts for durable, Workspace-scoped public YouTube imports."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from queue import Queue
from threading import Barrier, Thread
from uuid import UUID

import pytest
from httpx import Cookies
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from clipah.assets.storage import StoredObject
from clipah.assets.youtube import (
    NormalizedYouTubeUrl,
    SourceImportError,
    SourcePrivateError,
    SourceTlsError,
    SourceTooLongError,
    SourceUnavailableError,
    SourceUnsupportedError,
)
from clipah.db import RuntimeRole
from clipah.jobs.models import (
    JobCancelledError,
    JobContext,
    RetryableJobError,
    TerminalJobError,
)
from clipah.jobs.source_import_task import SourceImportStageRunner
from clipah.models import (
    Asset,
    Job,
    JobStatus,
    SourceImport,
    SourceImportStatus,
    WorkspaceRole,
    WorkspaceStatus,
)
from clipah.source_imports.dispatch import RecordingJobDispatcher
from harness import NOW, Browser, Clock, StubGoogleProvider, assert_error, build_app, sign_in
from support import runtime_settings


class SuccessfulImporter:
    """Return deterministic media metadata without touching a public provider."""

    def __init__(self) -> None:
        """Start with no imported object keys."""
        self.keys: list[str] = []

    def import_source(
        self,
        source: NormalizedYouTubeUrl,
        *,
        workspace: Path,
        object_key: str,
        cancellation_check: object,
    ) -> StoredObject:
        """Record the deterministic key and return complete immutable metadata."""
        del source, workspace, cancellation_check
        self.keys.append(object_key)
        return StoredObject(
            key=object_key,
            content_type="video/mp4",
            content_length=1234,
            sha256=b"x" * 32,
            duration_ms=42_000,
        )


class PrivateImporter(SuccessfulImporter):
    """Refuse a source with a fixed code and no provider detail."""

    def __init__(self, error_type: type[SourceImportError] = SourcePrivateError) -> None:
        """Select the stable terminal source error raised by this fake."""
        super().__init__()
        self.error_type = error_type

    def import_source(self, *args: object, **kwargs: object) -> StoredObject:
        """Model a permanent provider-policy refusal without raw detail."""
        del args, kwargs
        raise self.error_type


class FlakyImporter(SuccessfulImporter):
    """Fail once with the only retryable source code, then succeed."""

    def __init__(self) -> None:
        """Start before the one planned transient failure."""
        super().__init__()
        self.attempts = 0

    def import_source(self, *args: object, **kwargs: object) -> StoredObject:
        """Fail the first delivery and delegate every later delivery to success."""
        self.attempts += 1
        if self.attempts == 1:
            raise SourceUnavailableError
        return super().import_source(*args, **kwargs)  # type: ignore[arg-type]


def _workspace_id(browser: Browser) -> UUID:
    """Return the signed-in browser's selected personal Workspace."""
    return UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])


def _create_project(browser: Browser, workspace_id: UUID, *, key: str = "youtube-project") -> UUID:
    """Create one public-URL Project through the product API."""
    response = browser.request(
        "POST",
        f"/api/v1/projects?workspace_id={workspace_id}",
        headers={"Idempotency-Key": key},
        json={"name": "Remote source", "sourceKind": "public_url"},
    )
    assert response.status_code == 201
    return UUID(response.json()["id"])


def _path(workspace_id: UUID, project_id: UUID) -> str:
    """Build the import URL for an authorized tenant selection."""
    return f"/api/v1/projects/{project_id}/youtube-imports?workspace_id={workspace_id}"


def _normalized(_: str) -> NormalizedYouTubeUrl:
    """Return one stable admission result without public DNS in route tests."""
    return NormalizedYouTubeUrl(
        canonical_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        video_id="dQw4w9WgXcQ",
        host="www.youtube.com",
        addresses=frozenset(),
    )


@pytest.mark.integration
def test_valid_request_is_committed_before_uuid_only_dispatch(engine: Engine) -> None:
    """Workers must receive identifiers only after both durable rows exist."""
    clock = Clock(NOW)
    dispatcher = RecordingJobDispatcher()
    app, flow, _ = build_app(
        clock,
        StubGoogleProvider(clock),
        job_dispatcher=dispatcher,
        source_url_validator=lambda _: NormalizedYouTubeUrl(
            canonical_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            video_id="dQw4w9WgXcQ",
            host="www.youtube.com",
            addresses=frozenset(),
        ),
    )
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    project_id = _create_project(browser, workspace_id)

    response = browser.request(
        "POST",
        _path(workspace_id, project_id),
        headers={"Idempotency-Key": "youtube-one"},
        json={"url": "https://youtu.be/dQw4w9WgXcQ"},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    source_import_id = UUID(response.json()["sourceImportId"])
    job_id = UUID(response.json()["jobId"])
    assert dispatcher.calls == [
        (job_id, workspace_id, UUID(browser.get("/api/v1/me").json()["id"]))
    ]
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(SourceImport)) == 1
        assert connection.scalar(select(func.count()).select_from(Job)) == 1
        assert (
            connection.scalar(select(SourceImport.id).where(SourceImport.job_id == job_id))
            == source_import_id
        )


@pytest.mark.integration
def test_replay_converges_and_payload_mismatch_conflicts(engine: Engine) -> None:
    """Exact replay may redispatch, while changed payload may not reuse its key."""
    clock = Clock(NOW)
    dispatcher = RecordingJobDispatcher()

    def normalize(url: str) -> NormalizedYouTubeUrl:
        """Map symbolic inputs onto distinct normalized video identities."""
        video_id = "dQw4w9WgXcQ" if "first" in url else "9bZkp7q19f0"
        return NormalizedYouTubeUrl(
            canonical_url=f"https://www.youtube.com/watch?v={video_id}",
            video_id=video_id,
            host="www.youtube.com",
            addresses=frozenset(),
        )

    app, flow, _ = build_app(
        clock,
        StubGoogleProvider(clock),
        job_dispatcher=dispatcher,
        source_url_validator=normalize,
    )
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    project_id = _create_project(browser, workspace_id)
    headers = {"Idempotency-Key": "youtube-replay"}

    first = browser.request(
        "POST", _path(workspace_id, project_id), headers=headers, json={"url": "first"}
    )
    replay = browser.request(
        "POST", _path(workspace_id, project_id), headers=headers, json={"url": "first"}
    )
    mismatch = browser.request(
        "POST", _path(workspace_id, project_id), headers=headers, json={"url": "second"}
    )
    other_project_id = _create_project(browser, workspace_id, key="youtube-project-other")
    other_project = browser.request(
        "POST", _path(workspace_id, other_project_id), headers=headers, json={"url": "first"}
    )

    assert first.status_code == replay.status_code == 202
    assert first.json() == replay.json()
    assert len(dispatcher.calls) == 2
    assert_error(mismatch, status_code=409, code="CONFLICT")
    assert_error(other_project, status_code=409, code="CONFLICT")
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(SourceImport)) == 1
    with engine.begin() as connection:
        connection.execute(
            Job.__table__.update()
            .where(Job.id == UUID(first.json()["jobId"]))
            .values(kind="ingest")
        )
    wrong_kind = browser.request(
        "POST", _path(workspace_id, project_id), headers=headers, json={"url": "first"}
    )
    assert_error(wrong_kind, status_code=409, code="CONFLICT")


@pytest.mark.integration
def test_authentication_csrf_and_viewer_role_are_enforced(engine: Engine) -> None:
    """The import route must preserve the shared browser and Workspace write boundaries."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), source_url_validator=_normalized)
    anonymous = Browser(app)
    anonymous.cookies.set("clipah_csrf", "anonymous-csrf")
    unknown_project = UUID("00000000-0000-0000-0000-000000000001")
    unauthenticated = anonymous.request(
        "POST",
        _path(unknown_project, unknown_project),
        headers={"Idempotency-Key": "anonymous"},
        csrf_token="anonymous-csrf",
        json={"url": "source"},
    )
    assert_error(unauthenticated, status_code=401, code="UNAUTHENTICATED")

    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    project_id = _create_project(browser, workspace_id)
    missing_csrf = browser.request(
        "POST",
        _path(workspace_id, project_id),
        headers={"Idempotency-Key": "csrf"},
        csrf_token="",
        json={"url": "source"},
    )
    assert_error(missing_csrf, status_code=403, code="CSRF_FAILED")

    user_id = UUID(browser.get("/api/v1/me").json()["id"])
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workspace_memberships SET role = :role "
                "WHERE workspace_id = :workspace_id AND user_id = :user_id"
            ),
            {"role": WorkspaceRole.VIEWER.value, "workspace_id": workspace_id, "user_id": user_id},
        )
    forbidden = browser.request(
        "POST",
        _path(workspace_id, project_id),
        headers={"Idempotency-Key": "viewer"},
        json={"url": "source"},
    )
    assert_error(forbidden, status_code=403, code="FORBIDDEN")


@pytest.mark.integration
def test_archived_project_blocks_idempotent_redispatch(engine: Engine) -> None:
    """A replay must re-prove that its Project remains active before broker dispatch."""
    clock = Clock(NOW)
    dispatcher = RecordingJobDispatcher()
    app, flow, _ = build_app(
        clock,
        StubGoogleProvider(clock),
        job_dispatcher=dispatcher,
        source_url_validator=_normalized,
    )
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    project_id = _create_project(browser, workspace_id)
    headers = {"Idempotency-Key": "archive-replay"}
    assert (
        browser.request(
            "POST", _path(workspace_id, project_id), headers=headers, json={"url": "source"}
        ).status_code
        == 202
    )
    assert (
        browser.request(
            "DELETE", f"/api/v1/projects/{project_id}?workspace_id={workspace_id}"
        ).status_code
        == 204
    )

    replay = browser.request(
        "POST", _path(workspace_id, project_id), headers=headers, json={"url": "source"}
    )

    assert_error(replay, status_code=404, code="NOT_FOUND")
    assert len(dispatcher.calls) == 1


@pytest.mark.integration
def test_cross_workspace_and_disabled_workspace_are_indistinguishable(engine: Engine) -> None:
    """Tenant and lifecycle failures must reveal neither Project nor Workspace existence."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), source_url_validator=_normalized)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    project_id = _create_project(browser, workspace_id)
    other = browser.request("POST", "/api/v1/workspaces", json={"name": "Other", "kind": "team"})
    assert other.status_code == 201
    other_workspace_id = UUID(other.json()["id"])
    other_project_id = _create_project(browser, other_workspace_id, key="youtube-project-other")

    cross_workspace = browser.request(
        "POST",
        _path(workspace_id, other_project_id),
        headers={"Idempotency-Key": "cross-workspace"},
        json={"url": "source"},
    )
    assert_error(cross_workspace, status_code=404, code="NOT_FOUND")

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE workspaces SET status = :status WHERE id = :workspace_id"),
            {"status": WorkspaceStatus.SUSPENDED.value, "workspace_id": workspace_id},
        )
    disabled = browser.request(
        "POST",
        _path(workspace_id, project_id),
        headers={"Idempotency-Key": "disabled"},
        json={"url": "source"},
    )
    assert_error(disabled, status_code=404, code="NOT_FOUND")


@pytest.mark.integration
def test_route_admission_limit_creates_no_second_source_import(engine: Engine) -> None:
    """Concurrency refusal must roll back source intent along with rejected Job admission."""
    clock = Clock(NOW)
    app, flow, _ = build_app(
        clock,
        StubGoogleProvider(clock),
        source_url_validator=_normalized,
        concurrent_jobs_per_workspace=1,
    )
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    project_id = _create_project(browser, workspace_id)
    assert (
        browser.request(
            "POST",
            _path(workspace_id, project_id),
            headers={"Idempotency-Key": "first-slot"},
            json={"url": "source"},
        ).status_code
        == 202
    )

    refused = browser.request(
        "POST",
        _path(workspace_id, project_id),
        headers={"Idempotency-Key": "second-slot"},
        json={"url": "source"},
    )

    assert_error(refused, status_code=429, code="CONCURRENCY_LIMIT")
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(SourceImport)) == 1


@pytest.mark.integration
def test_concurrent_identical_submissions_converge(engine: Engine) -> None:
    """The advisory lock must turn a two-request race into one durable Job and import."""
    clock = Clock(NOW)
    dispatcher = RecordingJobDispatcher()
    app, flow, _ = build_app(
        clock,
        StubGoogleProvider(clock),
        job_dispatcher=dispatcher,
        source_url_validator=_normalized,
    )
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    project_id = _create_project(browser, workspace_id)
    barrier = Barrier(2)
    outcomes: Queue[tuple[int, dict[str, str]]] = Queue()

    def submit() -> None:
        """Release one cloned signed-in browser into the shared-key race."""
        contender = Browser(app)
        contender.cookies = Cookies(browser.cookies)
        barrier.wait()
        response = contender.request(
            "POST",
            _path(workspace_id, project_id),
            headers={"Idempotency-Key": "concurrent"},
            json={"url": "source"},
        )
        outcomes.put((response.status_code, response.json()))

    threads = [Thread(target=submit) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    responses = [outcomes.get_nowait() for _ in range(2)]

    assert [status for status, _ in responses] == [202, 202]
    assert responses[0][1] == responses[1][1]
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Job)) == 1
        assert connection.scalar(select(func.count()).select_from(SourceImport)) == 1


@pytest.mark.integration
def test_unsafe_source_and_dispatch_failure_are_sanitized_and_durable(engine: Engine) -> None:
    """Unsafe URLs create nothing, while broker outages preserve committed work."""
    clock = Clock(NOW)
    dispatcher = RecordingJobDispatcher(fail=True)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), job_dispatcher=dispatcher)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    project_id = _create_project(browser, workspace_id)

    unsafe = browser.request(
        "POST",
        _path(workspace_id, project_id),
        headers={"Idempotency-Key": "unsafe"},
        json={"url": "http://127.0.0.1/private"},
    )
    assert_error(unsafe, status_code=422, code="SOURCE_UNSUPPORTED")

    app.state.source_url_validator = lambda _: NormalizedYouTubeUrl(
        canonical_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        video_id="dQw4w9WgXcQ",
        host="www.youtube.com",
        addresses=frozenset(),
    )
    accepted = browser.request(
        "POST",
        _path(workspace_id, project_id),
        headers={"Idempotency-Key": "broker-down"},
        json={"url": "accepted"},
    )
    assert accepted.status_code == 202
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(SourceImport)) == 1


@pytest.mark.integration
def test_worker_retry_is_deterministic_and_creates_one_asset(engine: Engine) -> None:
    """Repeated delivery must retain one key and one immutable source Asset."""
    context, source_import_id = _seed_running_source_import(engine, suffix="worker-success")
    importer = SuccessfulImporter()
    runner = SourceImportStageRunner(
        importer_factory=lambda _: importer,
        validator=lambda _: NormalizedYouTubeUrl(
            canonical_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            video_id="dQw4w9WgXcQ",
            host="www.youtube.com",
            addresses=frozenset(),
        ),
    )

    runner(context)
    runner(context)

    with Session(engine) as session:
        source = session.scalar(select(SourceImport).where(SourceImport.id == source_import_id))
        asset = session.scalar(select(Asset).where(Asset.id == source_import_id))
        assert source is not None
        assert asset is not None
        assert source.status is SourceImportStatus.COMPLETED
        assert asset.content_type == "video/mp4"
        assert asset.size_bytes == 1234
        assert asset.sha256 == b"x" * 32
        assert asset.duration_ms == 42_000
        assert importer.keys == [asset.storage_key, asset.storage_key]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("error_type", "code"),
    [
        (SourceUnsupportedError, "SOURCE_UNSUPPORTED"),
        (SourcePrivateError, "SOURCE_PRIVATE"),
        (SourceTooLongError, "SOURCE_TOO_LONG"),
        (SourceTlsError, "SOURCE_TLS_FAILED"),
    ],
)
def test_worker_maps_source_codes_without_provider_text(
    engine: Engine, error_type: type[SourceImportError], code: str
) -> None:
    """Permanent provider refusals persist only their fixed public code."""
    context, source_import_id = _seed_running_source_import(engine, suffix="worker-private")
    runner = SourceImportStageRunner(
        importer_factory=lambda _: PrivateImporter(error_type),
        validator=lambda _: NormalizedYouTubeUrl(
            canonical_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            video_id="dQw4w9WgXcQ",
            host="www.youtube.com",
            addresses=frozenset(),
        ),
    )

    with pytest.raises(TerminalJobError, match=rf"^{code}$"):
        runner(context)

    with Session(engine) as session:
        source = session.scalar(select(SourceImport).where(SourceImport.id == source_import_id))
        assert source is not None
        assert source.status is SourceImportStatus.FAILED
        assert session.scalar(select(func.count()).select_from(Asset)) == 0


@pytest.mark.integration
def test_retryable_attempt_marks_failed_then_returns_to_downloading(engine: Engine) -> None:
    """A transient attempt records failure before the same import recovers."""
    context, source_import_id = _seed_running_source_import(engine, suffix="worker-retry")
    importer = FlakyImporter()
    runner = _runner(importer)

    with pytest.raises(RetryableJobError, match=r"^SOURCE_UNAVAILABLE$"):
        runner(context)
    with Session(engine) as session:
        assert (
            session.scalar(select(SourceImport.status).where(SourceImport.id == source_import_id))
            is SourceImportStatus.FAILED
        )

    runner(context)
    with Session(engine) as session:
        assert (
            session.scalar(select(SourceImport.status).where(SourceImport.id == source_import_id))
            is SourceImportStatus.COMPLETED
        )


@pytest.mark.integration
def test_cancellation_before_download_never_calls_importer_or_creates_asset(engine: Engine) -> None:
    """A pre-existing cancel request must stop before provider or storage work."""
    context, source_import_id = _seed_running_source_import(engine, suffix="worker-cancel")
    with engine.begin() as connection:
        connection.execute(
            Job.__table__.update()
            .where(Job.id == context.job_id)
            .values(cancel_requested_at=datetime.now(tz=UTC))
        )
    importer = SuccessfulImporter()

    with pytest.raises(JobCancelledError):
        _runner(importer)(context)

    assert importer.keys == []
    with Session(engine) as session:
        assert (
            session.scalar(select(SourceImport.status).where(SourceImport.id == source_import_id))
            is SourceImportStatus.CANCELED
        )
        assert session.scalar(select(func.count()).select_from(Asset)) == 0


def _runner(importer: SuccessfulImporter) -> SourceImportStageRunner:
    """Compose a worker runner with deterministic DNS and provider boundaries."""
    return SourceImportStageRunner(
        importer_factory=lambda _: importer,
        validator=lambda _: NormalizedYouTubeUrl(
            canonical_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            video_id="dQw4w9WgXcQ",
            host="www.youtube.com",
            addresses=frozenset(),
        ),
    )


def _seed_running_source_import(engine: Engine, *, suffix: str) -> tuple[JobContext, UUID]:
    """Create the exact running Job and source row a worker may load."""
    from support import provision_identity

    user_id, workspace_id = provision_identity(engine, suffix=suffix)
    settings = runtime_settings(RuntimeRole.WORKER)
    now = datetime.now(tz=UTC)
    with engine.begin() as connection:
        project_id = connection.scalar(
            select(Job.project_id).where(Job.workspace_id == workspace_id).limit(1)
        )
        if project_id is None:
            from uuid import uuid4

            from clipah.models import Project, ProjectStatus, SourceKind

            project_id = uuid4()
            connection.execute(
                Project.__table__.insert().values(
                    id=project_id,
                    workspace_id=workspace_id,
                    created_by_user_id=user_id,
                    name=f"Source {suffix}",
                    status=ProjectStatus.CREATED,
                    source_kind=SourceKind.PUBLIC_URL,
                    created_at=now,
                    updated_at=now,
                )
            )
        from uuid import uuid4

        job_id = uuid4()
        source_import_id = uuid4()
        connection.execute(
            Job.__table__.insert().values(
                id=job_id,
                workspace_id=workspace_id,
                project_id=project_id,
                kind="source_import",
                status=JobStatus.RUNNING,
                stage="queued",
                progress=0,
                attempt=1,
                idempotency_key=f"source-{suffix}",
            )
        )
        connection.execute(
            SourceImport.__table__.insert().values(
                id=source_import_id,
                workspace_id=workspace_id,
                project_id=project_id,
                normalized_source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                source_video_id="dQw4w9WgXcQ",
                status=SourceImportStatus.QUEUED,
                job_id=job_id,
            )
        )
    return (
        JobContext(
            job_id=job_id,
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
            attempt=1,
            settings=settings,
        ),
        source_import_id,
    )
