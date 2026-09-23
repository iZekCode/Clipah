"""Integration contracts for backfilling posters onto Projects analysed before they existed."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from clipah.models import Asset, AssetKind, AssetSourceType, Job, JobKind
from clipah.source_imports.dispatch import RecordingJobDispatcher
from clipah.studio.backfill_clip_posters import backfill_clip_posters, main
from clipah.studio.backfill_preview_media import BackfillOutcome
from integration.test_backfill_preview_media import NOW, _project
from integration.test_clip_posters_pipeline import _moments
from support import provision_identity, runtime_settings


@pytest.mark.integration
def test_backfill_admits_only_ranked_proxied_projects_that_have_no_poster(engine: Engine) -> None:
    """Unanalysed, unproxied, and already postered Projects are left alone."""
    user_id, workspace_id = provision_identity(engine, suffix=f"posters-bf-{uuid4().hex[:8]}")
    eligible = _ranked(engine, workspace_id, user_id)
    _project(engine, workspace_id, user_id, proxy=True)
    postered = _ranked(engine, workspace_id, user_id, postered=True)
    dispatcher = RecordingJobDispatcher()

    results = backfill_clip_posters(
        settings=runtime_settings(),
        dispatcher=dispatcher,
        workspace_id=workspace_id,
        user_id=user_id,
        now=NOW,
    )

    assert [(result.project_id, result.outcome) for result in results] == [
        (eligible, BackfillOutcome.ADMITTED)
    ]
    assert dispatcher.kinds == [JobKind.CLIP_POSTERS]
    assert postered not in {result.project_id for result in results}


@pytest.mark.integration
def test_backfill_is_idempotent_and_a_dry_run_changes_nothing(engine: Engine) -> None:
    """Running the command twice, or rehearsing it, never buys a second Job."""
    user_id, workspace_id = provision_identity(engine, suffix=f"posters-idem-{uuid4().hex[:8]}")
    project_id = _ranked(engine, workspace_id, user_id)

    outcomes = [
        backfill_clip_posters(
            settings=runtime_settings(),
            dispatcher=RecordingJobDispatcher(),
            workspace_id=workspace_id,
            user_id=user_id,
            now=NOW,
            dry_run=dry_run,
        )[0].outcome
        for dry_run in (True, False, False)
    ]

    assert outcomes == [
        BackfillOutcome.WOULD_ADMIT,
        BackfillOutcome.ADMITTED,
        BackfillOutcome.ALREADY_RUNNING,
    ]
    with Session(engine) as session:
        jobs = session.scalars(
            select(Job.id).where(Job.project_id == project_id, Job.kind == JobKind.CLIP_POSTERS)
        ).all()
    assert len(jobs) == 1


@pytest.mark.unit
def test_the_command_requires_both_identifiers() -> None:
    """A backfill with no named tenant is refused before any settings are read."""
    with pytest.raises(SystemExit):
        main(["--dry-run"])


def _ranked(engine: Engine, workspace_id: UUID, user_id: UUID, *, postered: bool = False) -> UUID:
    """A proxied Project whose analysis ranked moments, optionally with one poster drawn."""
    project_id = _project(engine, workspace_id, user_id, proxy=True)
    with Session(engine) as session:
        source_id = session.scalars(
            select(Asset.id).where(Asset.project_id == project_id, Asset.kind == AssetKind.SOURCE)
        ).one()

    class Context:
        """The two identities `_moments` reads from a Job context."""

        def __init__(self) -> None:
            self.workspace_id = workspace_id
            self.project_id = project_id

    best, _, _ = _moments(engine, Context(), source_id)  # type: ignore[arg-type]
    if postered:
        with engine.begin() as connection:
            connection.execute(
                Asset.__table__.insert().values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    project_id=project_id,
                    kind=AssetKind.POSTER,
                    source_type=AssetSourceType.DERIVED,
                    storage_key=(
                        f"workspaces/{workspace_id}/projects/{project_id}/derived/{source_id}"
                        f"/posters-v1/{best}.jpg"
                    ),
                    content_type="image/jpeg",
                    size_bytes=10,
                    sha256=b"p" * 32,
                )
            )
    return project_id
