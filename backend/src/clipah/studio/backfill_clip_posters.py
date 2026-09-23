"""Give Projects analysed before posters existed a sharp poster for each ranked moment.

Like the preview backfill, it works inside one named Workspace as one named member, through
ordinary Job admission, so it can never reach another tenant and never skips a limit.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from clipah.config import Settings
from clipah.db import session_scope
from clipah.models import Asset, AssetKind, ClipCandidate, JobKind, Project
from clipah.source_imports.dispatch import JobDispatcher
from clipah.studio.backfill_preview_media import BackfillResult, backfill_one
from clipah.workspaces.authorization import DatabaseWorkspaceAuthorizer
from clipah.workspaces.models import WorkspaceAction


def backfill_clip_posters(
    *,
    settings: Settings,
    dispatcher: JobDispatcher,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
    dry_run: bool = False,
    retry_failed: bool = False,
) -> tuple[BackfillResult, ...]:
    """Admit poster work for every active Project with exposed moments and no poster yet."""
    with session_scope(settings=settings, workspace_id=workspace_id, user_id=user_id) as session:
        DatabaseWorkspaceAuthorizer(session).require(
            user_id=user_id, workspace_id=workspace_id, action=WorkspaceAction.PROJECT_WRITE
        )
        ranked = select(ClipCandidate.project_id).where(
            ClipCandidate.workspace_id == workspace_id,
            ClipCandidate.model_metadata["exposed"].as_boolean().is_(True),
        )
        proxied = select(Asset.project_id).where(
            Asset.workspace_id == workspace_id, Asset.kind == AssetKind.PROXY
        )
        postered = select(Asset.project_id).where(
            Asset.workspace_id == workspace_id, Asset.kind == AssetKind.POSTER
        )
        project_ids = session.scalars(
            select(Project.id)
            .where(
                Project.workspace_id == workspace_id,
                Project.archived_at.is_(None),
                Project.id.in_(ranked),
                Project.id.in_(proxied),
                Project.id.not_in(postered),
            )
            .order_by(Project.created_at, Project.id)
        ).all()

    return tuple(
        backfill_one(
            settings=settings,
            dispatcher=dispatcher,
            workspace_id=workspace_id,
            user_id=user_id,
            project_id=project_id,
            kind=JobKind.CLIP_POSTERS,
            key_name="clip-posters-v1",
            now=now,
            dry_run=dry_run,
            retry_failed=retry_failed,
        )
        for project_id in project_ids
    )


def main(argv: list[str] | None = None) -> int:
    """Run the backfill for one Workspace and print one line per Project."""
    parser = argparse.ArgumentParser(description="Backfill portrait posters for ranked moments.")
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
    for result in backfill_clip_posters(
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
