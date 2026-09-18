"""Give Projects ingested before previews existed their storyboard and waveform.

The command works inside one named Workspace as one named member, through ordinary Job
admission, so it can never reach another tenant and never skips a limit the product enforces.
"""

from __future__ import annotations

import argparse
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select

from clipah.config import Settings
from clipah.db import session_scope
from clipah.jobs.admission import ConcurrencyLimitError, admission_policy
from clipah.jobs.use_cases import create_job
from clipah.models import Asset, AssetKind, Job, JobKind, JobStatus, Project
from clipah.source_imports.dispatch import JobDispatcher
from clipah.workspaces.authorization import DatabaseWorkspaceAuthorizer
from clipah.workspaces.models import WorkspaceAction

_UNFINISHED = (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYING, JobStatus.CANCEL_REQUESTED)


class BackfillOutcome(StrEnum):
    """What the command did, or would do, for one Project."""

    ADMITTED = "admitted"
    WOULD_ADMIT = "would_admit"
    ALREADY_RUNNING = "already_running"
    PREVIOUSLY_FAILED = "previously_failed"
    BUSY = "busy"


@dataclass(frozen=True, slots=True)
class BackfillResult:
    """One Project's outcome."""

    project_id: UUID
    outcome: BackfillOutcome
    job_id: UUID | None


def backfill_preview_media(
    *,
    settings: Settings,
    dispatcher: JobDispatcher,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
    dry_run: bool = False,
    retry_failed: bool = False,
) -> tuple[BackfillResult, ...]:
    """Admit preview work for every active Project with a proxy and no storyboard."""
    with session_scope(settings=settings, workspace_id=workspace_id, user_id=user_id) as session:
        DatabaseWorkspaceAuthorizer(session).require(
            user_id=user_id, workspace_id=workspace_id, action=WorkspaceAction.PROJECT_WRITE
        )
        proxied = select(Asset.project_id).where(
            Asset.workspace_id == workspace_id, Asset.kind == AssetKind.PROXY
        )
        previewed = select(Asset.project_id).where(
            Asset.workspace_id == workspace_id, Asset.kind == AssetKind.STORYBOARD
        )
        project_ids = session.scalars(
            select(Project.id)
            .where(
                Project.workspace_id == workspace_id,
                Project.archived_at.is_(None),
                Project.id.in_(proxied),
                Project.id.not_in(previewed),
            )
            .order_by(Project.created_at, Project.id)
        ).all()

    return tuple(
        _backfill_one(
            settings=settings,
            dispatcher=dispatcher,
            workspace_id=workspace_id,
            user_id=user_id,
            project_id=project_id,
            now=now,
            dry_run=dry_run,
            retry_failed=retry_failed,
        )
        for project_id in project_ids
    )


def _backfill_one(
    *,
    settings: Settings,
    dispatcher: JobDispatcher,
    workspace_id: UUID,
    user_id: UUID,
    project_id: UUID,
    now: datetime,
    dry_run: bool,
    retry_failed: bool,
) -> BackfillResult:
    """Decide and admit one Project in its own transaction, so one refusal stops nothing else."""
    with session_scope(settings=settings, workspace_id=workspace_id, user_id=user_id) as session:
        latest = session.execute(
            select(Job.id, Job.status)
            .where(
                Job.workspace_id == workspace_id,
                Job.project_id == project_id,
                Job.kind == JobKind.PREVIEW_MEDIA,
            )
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
        ).first()
        key = f"backfill:{project_id}:preview-media-v1"
        if latest is not None:
            if latest.status in _UNFINISHED:
                return BackfillResult(project_id, BackfillOutcome.ALREADY_RUNNING, latest.id)
            if not retry_failed:
                return BackfillResult(project_id, BackfillOutcome.PREVIOUSLY_FAILED, latest.id)
            key = f"{key}:after:{latest.id}"
        if dry_run:
            return BackfillResult(project_id, BackfillOutcome.WOULD_ADMIT, None)
        access = DatabaseWorkspaceAuthorizer(session).require(
            user_id=user_id, workspace_id=workspace_id, action=WorkspaceAction.PROJECT_WRITE
        )
        try:
            snapshot = create_job(
                session,
                policy=admission_policy(settings),
                access=access,
                project_id=project_id,
                kind=JobKind.PREVIEW_MEDIA,
                idempotency_key=key,
                now=now,
            )
        except ConcurrencyLimitError:
            return BackfillResult(project_id, BackfillOutcome.BUSY, None)
    # The committed Job is the record; a wakeup that fails is found again by the sweep.
    with suppress(Exception):
        dispatcher.dispatch(
            job_id=snapshot.job_id,
            workspace_id=workspace_id,
            user_id=user_id,
            kind=JobKind.PREVIEW_MEDIA,
        )
    return BackfillResult(project_id, BackfillOutcome.ADMITTED, snapshot.job_id)


def main(argv: list[str] | None = None) -> int:
    """Run the backfill for one Workspace and print one line per Project."""
    parser = argparse.ArgumentParser(description="Backfill storyboard and waveform previews.")
    parser.add_argument("--workspace-id", type=UUID, required=True)
    parser.add_argument("--user-id", type=UUID, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    arguments = parser.parse_args(argv)

    from clipah.celery_app import configure_celery
    from clipah.jobs.tasks import celery_app
    from clipah.source_imports.dispatch import CeleryJobDispatcher

    settings = Settings()
    configure_celery(celery_app, settings)
    for result in backfill_preview_media(
        settings=settings,
        dispatcher=CeleryJobDispatcher(),
        workspace_id=arguments.workspace_id,
        user_id=arguments.user_id,
        now=datetime.now(tz=UTC),
        dry_run=arguments.dry_run,
        retry_failed=arguments.retry_failed,
    ):
        print(f"{result.project_id} {result.outcome.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
