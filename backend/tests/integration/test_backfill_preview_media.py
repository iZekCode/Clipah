"""Integration contracts for backfilling previews onto Projects ingested before they existed."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from clipah.models import (
    Asset,
    AssetKind,
    AssetSourceType,
    Job,
    JobKind,
    JobStatus,
    Project,
    ProjectStatus,
    SourceKind,
)
from clipah.source_imports.dispatch import RecordingJobDispatcher
from clipah.studio.backfill_preview_media import BackfillOutcome, backfill_preview_media, main
from clipah.workspaces.models import WorkspaceNotFoundError
from support import provision_identity, runtime_settings

NOW = datetime(2026, 9, 17, tzinfo=UTC)


@pytest.mark.integration
def test_backfill_admits_only_projects_with_a_proxy_and_no_storyboard(engine: Engine) -> None:
    """Projects still ingesting, or already previewed, are left alone."""
    user_id, workspace_id = provision_identity(engine, suffix=f"backfill-{uuid4().hex[:8]}")
    eligible = _project(engine, workspace_id, user_id, proxy=True)
    _project(engine, workspace_id, user_id, proxy=False)
    previewed = _project(engine, workspace_id, user_id, proxy=True, storyboard=True)
    dispatcher = RecordingJobDispatcher()

    results = backfill_preview_media(
        settings=runtime_settings(),
        dispatcher=dispatcher,
        workspace_id=workspace_id,
        user_id=user_id,
        now=NOW,
    )

    assert [(result.project_id, result.outcome) for result in results] == [
        (eligible, BackfillOutcome.ADMITTED)
    ]
    assert dispatcher.kinds == [JobKind.PREVIEW_MEDIA]
    assert previewed not in {result.project_id for result in results}


@pytest.mark.integration
def test_backfill_is_idempotent_and_a_dry_run_changes_nothing(engine: Engine) -> None:
    """Running the command twice, or rehearsing it, never buys a second Job."""
    user_id, workspace_id = provision_identity(engine, suffix=f"backfill-idem-{uuid4().hex[:8]}")
    project_id = _project(engine, workspace_id, user_id, proxy=True)

    rehearsal = backfill_preview_media(
        settings=runtime_settings(),
        dispatcher=RecordingJobDispatcher(),
        workspace_id=workspace_id,
        user_id=user_id,
        now=NOW,
        dry_run=True,
    )
    first = backfill_preview_media(
        settings=runtime_settings(),
        dispatcher=RecordingJobDispatcher(),
        workspace_id=workspace_id,
        user_id=user_id,
        now=NOW,
    )
    second = backfill_preview_media(
        settings=runtime_settings(),
        dispatcher=RecordingJobDispatcher(),
        workspace_id=workspace_id,
        user_id=user_id,
        now=NOW,
    )

    assert [result.outcome for result in rehearsal] == [BackfillOutcome.WOULD_ADMIT]
    assert [result.outcome for result in first] == [BackfillOutcome.ADMITTED]
    assert [result.outcome for result in second] == [BackfillOutcome.ALREADY_RUNNING]
    assert _preview_jobs(engine, project_id) == 1


@pytest.mark.integration
def test_a_failed_preview_is_retried_only_when_asked(engine: Engine) -> None:
    """An automatic retry of a broken source would fail the same way forever."""
    user_id, workspace_id = provision_identity(engine, suffix=f"backfill-fail-{uuid4().hex[:8]}")
    project_id = _project(engine, workspace_id, user_id, proxy=True)
    backfill_preview_media(
        settings=runtime_settings(),
        dispatcher=RecordingJobDispatcher(),
        workspace_id=workspace_id,
        user_id=user_id,
        now=NOW,
    )
    with engine.begin() as connection:
        connection.execute(
            Job.__table__.update()
            .where(Job.project_id == project_id)
            .values(status=JobStatus.FAILED, error_code="PREVIEW_MEDIA_INTEGRITY")
        )

    skipped = backfill_preview_media(
        settings=runtime_settings(),
        dispatcher=RecordingJobDispatcher(),
        workspace_id=workspace_id,
        user_id=user_id,
        now=NOW,
    )
    retried = backfill_preview_media(
        settings=runtime_settings(),
        dispatcher=RecordingJobDispatcher(),
        workspace_id=workspace_id,
        user_id=user_id,
        now=NOW,
        retry_failed=True,
    )

    assert [result.outcome for result in skipped] == [BackfillOutcome.PREVIOUSLY_FAILED]
    assert [result.outcome for result in retried] == [BackfillOutcome.ADMITTED]
    assert _preview_jobs(engine, project_id) == 2


@pytest.mark.integration
def test_a_full_workspace_reports_busy_instead_of_failing(engine: Engine) -> None:
    """The operator sees which Projects to run again later."""
    user_id, workspace_id = provision_identity(engine, suffix=f"backfill-busy-{uuid4().hex[:8]}")
    _project(engine, workspace_id, user_id, proxy=True)

    results = backfill_preview_media(
        settings=runtime_settings(concurrent_jobs_per_workspace=0),
        dispatcher=RecordingJobDispatcher(),
        workspace_id=workspace_id,
        user_id=user_id,
        now=NOW,
    )

    assert [result.outcome for result in results] == [BackfillOutcome.BUSY]


@pytest.mark.integration
def test_backfill_refuses_a_user_who_is_not_a_member(engine: Engine) -> None:
    """The command never reaches into a Workspace the named User has no standing in."""
    _, workspace_id = provision_identity(engine, suffix=f"backfill-owner-{uuid4().hex[:8]}")
    outsider, _ = provision_identity(engine, suffix=f"backfill-outsider-{uuid4().hex[:8]}")

    with pytest.raises(WorkspaceNotFoundError):
        backfill_preview_media(
            settings=runtime_settings(),
            dispatcher=RecordingJobDispatcher(),
            workspace_id=workspace_id,
            user_id=outsider,
            now=NOW,
        )


@pytest.mark.unit
def test_the_command_requires_both_identifiers() -> None:
    """A backfill with no named tenant is refused before any settings are read."""
    with pytest.raises(SystemExit):
        main(["--dry-run"])


def _project(
    engine: Engine, workspace_id: UUID, user_id: UUID, *, proxy: bool, storyboard: bool = False
) -> UUID:
    project_id, source_id = uuid4(), uuid4()
    prefix = f"workspaces/{workspace_id}/projects/{project_id}"
    with engine.begin() as connection:
        connection.execute(
            Project.__table__.insert().values(
                id=project_id,
                workspace_id=workspace_id,
                created_by_user_id=user_id,
                name="Backfill",
                status=ProjectStatus.READY,
                source_kind=SourceKind.UPLOAD,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        rows = [(source_id, AssetKind.SOURCE, f"{prefix}/source/{source_id}")]
        if proxy:
            rows.append(
                (uuid5(source_id, "proxy"), AssetKind.PROXY, f"{prefix}/derived/{source_id}/proxy")
            )
        if storyboard:
            rows.append(
                (
                    uuid4(),
                    AssetKind.STORYBOARD,
                    f"{prefix}/derived/{source_id}/storyboard-v1/sheet-0000.jpg",
                )
            )
        for asset_id, kind, key in rows:
            connection.execute(
                Asset.__table__.insert().values(
                    id=asset_id,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    kind=kind,
                    source_type=AssetSourceType.DERIVED
                    if kind is not AssetKind.SOURCE
                    else AssetSourceType.USER_UPLOAD,
                    storage_key=key,
                    content_type="video/mp4",
                    size_bytes=10,
                    sha256=b"b" * 32,
                )
            )
    return project_id


def _preview_jobs(engine: Engine, project_id: UUID) -> int:
    with Session(engine) as session:
        return len(
            session.scalars(
                select(Job.id).where(
                    Job.project_id == project_id, Job.kind == JobKind.PREVIEW_MEDIA
                )
            ).all()
        )
