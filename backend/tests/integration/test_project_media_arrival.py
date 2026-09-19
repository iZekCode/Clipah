"""A Project says when its video is on its way, and stops saying so when it is not.

`uploading` covers both ways a video arrives: a file upload in progress and a YouTube
import the worker is downloading. A failed or cancelled import returns the Project to
`created`, where the member can try again; marking it `failed` would end it for good,
because the pipeline never restarts a failed Project.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import Engine, select

from clipah.assets.storage import FakeObjectStore
from clipah.assets.youtube import NormalizedYouTubeUrl
from clipah.db import RuntimeRole, session_scope
from clipah.jobs.use_cases import fail_job, request_job_cancellation, start_job
from clipah.models import Project, ProjectStatus
from clipah.source_imports.dispatch import RecordingJobDispatcher
from harness import NOW, Browser, Clock, StubGoogleProvider, build_app, sign_in
from support import runtime_settings

SOURCE = NormalizedYouTubeUrl(
    canonical_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    video_id="dQw4w9WgXcQ",
    host="www.youtube.com",
    addresses=frozenset(),
)


def _signed_in() -> tuple[Browser, UUID, UUID]:
    clock = Clock(NOW)
    app, flow, _ = build_app(
        clock,
        StubGoogleProvider(clock),
        object_store=FakeObjectStore(now=clock),
        job_dispatcher=RecordingJobDispatcher(),
        source_url_validator=lambda _: SOURCE,
    )
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    user_id = UUID(browser.get("/api/v1/me").json()["id"])
    return browser, workspace_id, user_id


def _project(browser: Browser, workspace_id: UUID, kind: str) -> UUID:
    response = browser.request(
        "POST",
        f"/api/v1/projects?workspace_id={workspace_id}",
        headers={"Idempotency-Key": f"arrival-{kind}"},
        json={"name": "Arrival", "sourceKind": kind},
    )
    assert response.status_code == 201
    return UUID(response.json()["id"])


def _status(engine: Engine, project_id: UUID) -> ProjectStatus:
    with engine.connect() as connection:
        status = connection.scalar(select(Project.status).where(Project.id == project_id))
    assert status is not None
    return status


def _import(browser: Browser, workspace_id: UUID, project_id: UUID) -> UUID:
    response = browser.request(
        "POST",
        f"/api/v1/projects/{project_id}/youtube-imports?workspace_id={workspace_id}",
        headers={"Idempotency-Key": "arrival-import"},
        json={"url": "https://youtu.be/dQw4w9WgXcQ"},
    )
    assert response.status_code == 202
    return UUID(response.json()["jobId"])


def _upload(browser: Browser, workspace_id: UUID, project_id: UUID) -> str:
    response = browser.request(
        "POST",
        f"/api/v1/projects/{project_id}/uploads?workspace_id={workspace_id}",
        json={"filename": "episode.mp4", "contentType": "video/mp4", "contentLength": 1024},
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _worker(workspace_id: UUID, user_id: UUID):  # type: ignore[no-untyped-def]
    return session_scope(
        settings=runtime_settings(RuntimeRole.WORKER),
        workspace_id=workspace_id,
        user_id=user_id,
        runtime_role=RuntimeRole.WORKER,
    )


@pytest.mark.integration
def test_an_accepted_import_says_the_video_is_on_its_way(
    engine: Engine, clean_database: None
) -> None:
    """The download is work in progress, not a Project waiting for anything."""
    del clean_database
    browser, workspace_id, _ = _signed_in()
    project_id = _project(browser, workspace_id, "public_url")
    assert _status(engine, project_id) is ProjectStatus.CREATED

    _import(browser, workspace_id, project_id)

    assert _status(engine, project_id) is ProjectStatus.UPLOADING


@pytest.mark.integration
def test_a_failed_import_returns_the_project_to_waiting_so_it_can_be_retried(
    engine: Engine, clean_database: None
) -> None:
    """A retry keeps the Project on its way; only a final failure lets it go."""
    del clean_database
    browser, workspace_id, user_id = _signed_in()
    project_id = _project(browser, workspace_id, "public_url")
    job_id = _import(browser, workspace_id, project_id)
    now = datetime.now(tz=UTC)

    with _worker(workspace_id, user_id) as session:
        start_job(session, workspace_id=workspace_id, job_id=job_id, now=now)
        fail_job(
            session,
            workspace_id=workspace_id,
            job_id=job_id,
            error_code="SOURCE_UNAVAILABLE",
            retryable=True,
            now=now,
        )
    assert _status(engine, project_id) is ProjectStatus.UPLOADING

    with _worker(workspace_id, user_id) as session:
        start_job(session, workspace_id=workspace_id, job_id=job_id, now=now)
        fail_job(
            session,
            workspace_id=workspace_id,
            job_id=job_id,
            error_code="SOURCE_PRIVATE",
            retryable=False,
            now=now,
        )
    assert _status(engine, project_id) is ProjectStatus.CREATED


@pytest.mark.integration
def test_a_cancelled_import_returns_the_project_to_waiting(
    engine: Engine, clean_database: None
) -> None:
    """Nothing is on its way once the member stops the import."""
    del clean_database
    browser, workspace_id, user_id = _signed_in()
    project_id = _project(browser, workspace_id, "public_url")
    job_id = _import(browser, workspace_id, project_id)

    with _worker(workspace_id, user_id) as session:
        request_job_cancellation(
            session, workspace_id=workspace_id, job_id=job_id, now=datetime.now(tz=UTC)
        )

    assert _status(engine, project_id) is ProjectStatus.CREATED


@pytest.mark.integration
def test_a_started_upload_says_the_video_is_on_its_way_until_it_is_abandoned(
    engine: Engine, clean_database: None
) -> None:
    """Aborting the only upload leaves nothing on its way."""
    del clean_database
    browser, workspace_id, _ = _signed_in()
    project_id = _project(browser, workspace_id, "upload")

    upload_id = _upload(browser, workspace_id, project_id)
    assert _status(engine, project_id) is ProjectStatus.UPLOADING

    aborted = browser.request(
        "DELETE",
        f"/api/v1/projects/{project_id}/uploads/{upload_id}?workspace_id={workspace_id}",
    )
    assert aborted.status_code == 204
    assert _status(engine, project_id) is ProjectStatus.CREATED


@pytest.mark.integration
def test_an_abandoned_upload_does_not_hide_another_one_still_arriving(
    engine: Engine, clean_database: None
) -> None:
    """A second upload in progress keeps the Project on its way."""
    del clean_database
    browser, workspace_id, _ = _signed_in()
    project_id = _project(browser, workspace_id, "upload")
    first = _upload(browser, workspace_id, project_id)
    _upload(browser, workspace_id, project_id)

    browser.request(
        "DELETE", f"/api/v1/projects/{project_id}/uploads/{first}?workspace_id={workspace_id}"
    )

    assert _status(engine, project_id) is ProjectStatus.UPLOADING
