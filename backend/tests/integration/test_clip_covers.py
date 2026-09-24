"""Integration contracts for a clip's designed cover: asking for it, drawing it, showing it."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from clipah.assets.clip_covers import CoverInputs
from clipah.assets.ingest import IngestArtifact
from clipah.assets.keys import derived_asset_key
from clipah.assets.storage import StoredObject
from clipah.jobs.clip_cover_task import ClipCoverStageRunner
from clipah.jobs.models import TerminalJobError
from clipah.jobs.tasks import stage_runners
from clipah.models import Asset, AssetKind, AssetSourceType, Job, JobKind, JobStatus
from clipah.renders.cover import cover_asset_id, cover_name
from integration.test_render_pipeline import Stage, _context, _staged, _start


class StaticMaker:
    """Describe one cover per requested Revision without touching FFmpeg or storage."""

    def __init__(self) -> None:
        self.inputs: list[CoverInputs] = []

    def build(
        self, *, inputs: CoverInputs, workspace: Path, cancellation_check: Callable[[], None]
    ) -> tuple[IngestArtifact, ...]:
        cancellation_check()
        self.inputs.append(inputs)
        prefix = (
            f"workspaces/{inputs.workspace_id}/projects/{inputs.project_id}/derived/"
            f"{inputs.source_asset_id}"
        )
        return tuple(
            IngestArtifact(
                asset_id=cover_asset_id(revision.revision_id),
                kind=AssetKind.COVER,
                source_type=AssetSourceType.DERIVED,
                storage_key=f"{prefix}/{cover_name(revision.revision_id)}",
                content_type="image/jpeg",
                size_bytes=120_000,
                sha256=hashlib.sha256(str(revision.revision_id).encode()).digest(),
                duration_ms=None,
                width=1080,
                height=1920,
                video_codec=None,
                audio_codec=None,
            )
            for revision in inputs.revisions
        )


@pytest.mark.integration
def test_the_cover_runner_is_registered_for_its_job_kind() -> None:
    """A queued cover Job must never fall through to the unsupported-kind failure."""
    assert JobKind.CLIP_COVER in stage_runners()


@pytest.mark.integration
def test_a_new_clip_designs_a_cover_titled_with_its_hook(engine: Engine) -> None:
    """The first Revision already carries a cover, so a member starts from a finished look."""
    stage = _staged(engine)

    cover = _composition(stage)["cover"]

    assert cover == {"atMs": 1_000, "preset": "bold", "title": "Hook"}
    assert _read(stage).json()["status"] == "missing"


@pytest.mark.integration
def test_asking_for_a_cover_draws_it_once_and_the_grid_shows_it(engine: Engine) -> None:
    """One Job draws the picture; asking again while it runs or once it exists admits nothing."""
    stage = _staged(engine)
    _proxy(stage)

    asked = _ask(stage)
    again = _ask(stage)

    assert asked.status_code == 202
    assert asked.json()["status"] == "drawing"
    assert again.json()["jobId"] == asked.json()["jobId"]
    job_id = UUID(asked.json()["jobId"])
    _start(stage, job_id)
    maker = StaticMaker()
    ClipCoverStageRunner(maker_factory=lambda _settings: maker)(_context(stage, job_id))
    _finish(stage, job_id)

    _uploaded(stage, maker)
    recorded = maker.inputs[0]
    assert [revision.revision_id for revision in recorded.revisions] == [stage.revision_id]
    assert (recorded.proxy_width, recorded.proxy_height) == (1280, 720)
    ready = _read(stage)
    assert ready.json()["status"] == "ready"
    assert "covers-v1/" in ready.json()["url"]
    assert _ask(stage).status_code == 200
    posters = stage.browser.get(
        f"/api/v1/projects/{stage.project_id}/posters?workspace_id={stage.workspace_id}"
    ).json()["posters"]
    assert [poster["height"] for poster in posters] == [1920]


@pytest.mark.integration
def test_a_saved_change_makes_the_old_picture_stale(engine: Engine) -> None:
    """A cover belongs to one Revision: a newer design is missing until it is drawn."""
    stage = _staged(engine)
    _proxy(stage)
    job_id = UUID(_ask(stage).json()["jobId"])
    _start(stage, job_id)
    ClipCoverStageRunner(maker_factory=lambda _settings: StaticMaker())(_context(stage, job_id))
    _finish(stage, job_id)

    document = _composition(stage)
    document["cover"] = {"atMs": 2_000, "preset": "clean", "title": "Another title"}
    _save(stage, document)

    assert _read(stage).json()["status"] == "missing"


@pytest.mark.integration
def test_a_clip_without_a_cover_design_cannot_be_drawn(engine: Engine) -> None:
    """Nothing is drawn for a design nobody made; the refusal says why."""
    stage = _staged(engine)
    document = _composition(stage)
    document.pop("cover")
    _save(stage, document)

    assert _read(stage).json()["status"] == "none"
    refused = _ask(stage)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "COVER_NOT_DESIGNED"


@pytest.mark.integration
def test_another_workspace_cover_reads_as_missing(engine: Engine) -> None:
    """A guessed Edit identifier is indistinguishable from one that does not exist."""
    stage = _staged(engine)

    unknown = stage.browser.get(f"/api/v1/edits/{uuid4()}/cover?workspace_id={stage.workspace_id}")

    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.integration
def test_a_project_without_a_proxy_fails_terminally(engine: Engine) -> None:
    """A cover of a source ingest never finished would be a cover of nothing."""
    stage = _staged(engine)
    job_id = UUID(_ask(stage).json()["jobId"])
    _start(stage, job_id)

    with pytest.raises(TerminalJobError, match=r"^CLIP_COVER_INPUT_MISSING$"):
        ClipCoverStageRunner(maker_factory=lambda _settings: StaticMaker())(_context(stage, job_id))


def _ask(stage: Stage) -> Any:
    """Ask for the staged clip's cover picture."""
    return stage.browser.request(
        "POST", f"/api/v1/edits/{stage.edit_id}/cover?workspace_id={stage.workspace_id}"
    )


def _read(stage: Stage) -> Any:
    """Read where the staged clip's cover picture stands."""
    return stage.browser.get(
        f"/api/v1/edits/{stage.edit_id}/cover?workspace_id={stage.workspace_id}"
    )


def _composition(stage: Stage) -> dict[str, Any]:
    """The staged clip's current composition, as the editor loads it."""
    body = stage.browser.get(f"/api/v1/edits/{stage.edit_id}?workspace_id={stage.workspace_id}")
    composition: dict[str, Any] = body.json()["composition"]
    return composition


def _save(stage: Stage, document: dict[str, Any]) -> None:
    """Save one changed composition as the next Revision."""
    current = stage.browser.get(
        f"/api/v1/edits/{stage.edit_id}?workspace_id={stage.workspace_id}"
    ).json()["currentRevision"]
    saved = stage.browser.request(
        "PUT",
        f"/api/v1/edits/{stage.edit_id}?workspace_id={stage.workspace_id}",
        json={"expectedRevision": current, "composition": document},
    )
    assert saved.status_code == 200, saved.json()


def _uploaded(stage: Stage, maker: StaticMaker) -> None:
    """Record in the fake store every cover the maker described, as its upload would."""
    for inputs in maker.inputs:
        prefix = (
            f"workspaces/{inputs.workspace_id}/projects/{inputs.project_id}/derived/"
            f"{inputs.source_asset_id}"
        )
        for revision in inputs.revisions:
            key = f"{prefix}/{cover_name(revision.revision_id)}"
            stage.store.objects[key] = StoredObject(
                key=key, content_type="image/jpeg", content_length=120_000
            )


def _proxy(stage: Stage) -> None:
    """Record the proxy ingest writes beside the staged source."""
    key = derived_asset_key(
        workspace_id=stage.workspace_id,
        project_id=stage.project_id,
        source_asset_id=stage.source_asset_id,
        kind=AssetKind.PROXY,
    )
    with stage.engine.begin() as connection:
        connection.execute(
            Asset.__table__.insert().values(
                id=uuid4(),
                workspace_id=stage.workspace_id,
                project_id=stage.project_id,
                kind=AssetKind.PROXY,
                source_type=AssetSourceType.DERIVED,
                storage_key=key,
                content_type="video/mp4",
                size_bytes=2_048,
                duration_ms=60_000,
                width=1280,
                height=720,
                sha256=b"p" * 32,
            )
        )


def _finish(stage: Stage, job_id: UUID) -> None:
    """Mark one run Job succeeded, as the worker's wrapper does after its runner returns."""
    with stage.engine.begin() as connection:
        connection.execute(
            text("UPDATE jobs SET status = :status WHERE id = :id"),
            {"status": JobStatus.SUCCEEDED.value, "id": job_id},
        )
    with Session(stage.engine) as session:
        assert session.scalar(select(Job.status).where(Job.id == job_id)) is JobStatus.SUCCEEDED
