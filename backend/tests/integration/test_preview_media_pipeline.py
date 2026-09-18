"""Integration contracts for durable, retry-safe preview media.

Previews are decoration: a creator's clips must never wait on them, and a retry must never
draw them twice. These tests hold the runner to the same standard ingest is held to.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from clipah.assets.ingest import IngestArtifact, IngestIntegrityError
from clipah.assets.preview_builder import (
    PreviewInputs,
    PreviewMediaResult,
    storyboard_asset_id,
    waveform_asset_id,
)
from clipah.assets.preview_media import WaveformInputError
from clipah.assets.storage import ObjectStoreUnavailableError
from clipah.db import RuntimeRole
from clipah.jobs.models import JobContext, RetryableJobError, TerminalJobError
from clipah.jobs.preview_media_task import PreviewMediaStageRunner
from clipah.jobs.tasks import stage_runners
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
from support import provision_identity, runtime_settings


class StaticMaker:
    """Return two sheets and a waveform without touching FFmpeg or storage."""

    def __init__(self) -> None:
        self.inputs: list[PreviewInputs] = []

    def build(
        self, *, inputs: PreviewInputs, workspace: Path, cancellation_check: Callable[[], None]
    ) -> PreviewMediaResult:
        cancellation_check()
        assert workspace.is_dir()
        self.inputs.append(inputs)
        prefix = (
            f"workspaces/{inputs.workspace_id}/projects/{inputs.project_id}/derived/"
            f"{inputs.source_asset_id}"
        )
        sheets = tuple(
            IngestArtifact(
                asset_id=storyboard_asset_id(inputs.source_asset_id, index),
                kind=AssetKind.STORYBOARD,
                source_type=AssetSourceType.DERIVED,
                storage_key=f"{prefix}/storyboard-v1/sheet-{index:04d}.jpg",
                content_type="image/jpeg",
                size_bytes=40,
                sha256=hashlib.sha256(f"sheet-{index}".encode()).digest(),
                duration_ms=200_000 if index == 0 else 5_000,
                width=1600,
                height=900,
                video_codec=None,
                audio_codec=None,
            )
            for index in range(2)
        )
        waveform = IngestArtifact(
            asset_id=waveform_asset_id(inputs.source_asset_id),
            kind=AssetKind.WAVEFORM,
            source_type=AssetSourceType.DERIVED,
            storage_key=f"{prefix}/waveform-v1.bin",
            content_type="application/octet-stream",
            size_bytes=4_100,
            sha256=hashlib.sha256(b"peaks").digest(),
            duration_ms=205_000,
            width=None,
            height=None,
            video_codec=None,
            audio_codec=None,
        )
        return PreviewMediaResult(sheets=sheets, waveform=waveform)


class FailingMaker(StaticMaker):
    """Raise one boundary failure on build."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def build(self, **kwargs: object) -> PreviewMediaResult:  # type: ignore[override]
        del kwargs
        raise self.error


@pytest.mark.integration
def test_the_preview_runner_is_registered_for_its_job_kind() -> None:
    """A queued preview Job must never fall through to the unsupported-kind failure."""
    assert JobKind.PREVIEW_MEDIA in stage_runners()


@pytest.mark.integration
def test_previews_are_recorded_once_and_a_redelivery_reuses_them(engine: Engine) -> None:
    """Two deliveries of one Job converge on one sheet set and one waveform."""
    context, _ = _seed(engine, suffix=f"preview-{uuid4().hex[:8]}")
    maker = StaticMaker()
    runner = PreviewMediaStageRunner(maker_factory=lambda _settings: maker)

    runner(context)
    runner(context)

    assert len(maker.inputs) == 1
    recorded = maker.inputs[0]
    assert (recorded.proxy_width, recorded.proxy_height, recorded.duration_ms) == (
        1280,
        720,
        205_000,
    )
    with Session(engine) as session:
        kinds = session.scalars(
            select(Asset.kind).where(
                Asset.workspace_id == context.workspace_id,
                Asset.kind.in_([AssetKind.STORYBOARD, AssetKind.WAVEFORM]),
            )
        ).all()
        project = session.get(Project, context.project_id)
    assert sorted(kind.value for kind in kinds) == ["storyboard", "storyboard", "waveform"]
    assert project is not None and project.status is ProjectStatus.TRANSCRIBING


@pytest.mark.integration
def test_a_project_without_ingest_outputs_fails_terminally(engine: Engine) -> None:
    """Previews of a source ingest never finished would be previews of nothing."""
    context, _ = _seed(engine, suffix=f"preview-missing-{uuid4().hex[:8]}", derivatives=False)

    with pytest.raises(TerminalJobError, match=r"^PREVIEW_MEDIA_INPUT_MISSING$"):
        PreviewMediaStageRunner(maker_factory=lambda _settings: StaticMaker())(context)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("error", "expected", "code"),
    [
        (
            ObjectStoreUnavailableError("secret detail"),
            RetryableJobError,
            "ASSET_STORAGE_UNAVAILABLE",
        ),
        (IngestIntegrityError("detail"), TerminalJobError, "PREVIEW_MEDIA_INTEGRITY"),
        (WaveformInputError("detail"), TerminalJobError, "PREVIEW_MEDIA_INVALID_AUDIO"),
    ],
)
def test_boundary_failures_map_to_stable_codes(
    engine: Engine, error: Exception, expected: type[Exception], code: str
) -> None:
    """No provider or file detail may leave the runner as a Job error code."""
    context, _ = _seed(engine, suffix=f"preview-fail-{uuid4().hex[:8]}")

    with pytest.raises(expected, match=rf"^{code}$"):
        PreviewMediaStageRunner(maker_factory=lambda _settings: FailingMaker(error))(context)


@pytest.mark.integration
def test_a_conflicting_existing_sheet_rolls_back_the_whole_set(engine: Engine) -> None:
    """One sheet recorded with other bytes must stop every insert, not leave a partial set."""
    context, source_id = _seed(engine, suffix=f"preview-conflict-{uuid4().hex[:8]}")
    with engine.begin() as connection:
        connection.execute(
            Asset.__table__.insert().values(
                id=storyboard_asset_id(source_id, 1),
                workspace_id=context.workspace_id,
                project_id=context.project_id,
                kind=AssetKind.STORYBOARD,
                source_type=AssetSourceType.DERIVED,
                storage_key="conflicting-key",
                content_type="image/jpeg",
                size_bytes=1,
                sha256=b"z" * 32,
            )
        )

    with pytest.raises(TerminalJobError, match=r"^PREVIEW_MEDIA_INTEGRITY$"):
        PreviewMediaStageRunner(maker_factory=lambda _settings: StaticMaker())(context)

    with Session(engine) as session:
        waveform = session.get(Asset, waveform_asset_id(source_id))
    assert waveform is None


def _seed(engine: Engine, *, suffix: str, derivatives: bool = True) -> tuple[JobContext, UUID]:
    """Create one running PREVIEW_MEDIA Job and the source, proxy, and audio ingest recorded."""
    user_id, workspace_id = provision_identity(engine, suffix=suffix)
    project_id, job_id, source_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(tz=UTC)
    prefix = f"workspaces/{workspace_id}/projects/{project_id}"
    with engine.begin() as connection:
        connection.execute(
            Project.__table__.insert().values(
                id=project_id,
                workspace_id=workspace_id,
                created_by_user_id=user_id,
                name=f"Preview {suffix}",
                status=ProjectStatus.TRANSCRIBING,
                source_kind=SourceKind.UPLOAD,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            Job.__table__.insert().values(
                id=job_id,
                workspace_id=workspace_id,
                project_id=project_id,
                kind=JobKind.PREVIEW_MEDIA,
                status=JobStatus.RUNNING,
                stage="queued",
                progress=0,
                attempt=1,
                idempotency_key=f"preview-{suffix}",
            )
        )
        rows = [
            dict(
                id=source_id,
                kind=AssetKind.SOURCE,
                source_type=AssetSourceType.USER_UPLOAD,
                storage_key=f"{prefix}/source/{source_id}",
                content_type="video/mp4",
                size_bytes=6,
                duration_ms=205_000,
                width=1920,
                height=1080,
                video_codec="h264",
                audio_codec="aac",
            ),
        ]
        if derivatives:
            rows += [
                dict(
                    id=uuid5(source_id, "proxy"),
                    kind=AssetKind.PROXY,
                    source_type=AssetSourceType.DERIVED,
                    storage_key=f"{prefix}/derived/{source_id}/proxy",
                    content_type="video/mp4",
                    size_bytes=100,
                    duration_ms=205_000,
                    width=1280,
                    height=720,
                    video_codec="h264",
                    audio_codec="aac",
                ),
                dict(
                    id=uuid5(source_id, "transcription_audio"),
                    kind=AssetKind.TRANSCRIPTION_AUDIO,
                    source_type=AssetSourceType.DERIVED,
                    storage_key=f"{prefix}/derived/{source_id}/transcription_audio",
                    content_type="audio/wav",
                    size_bytes=80,
                    duration_ms=205_000,
                    width=None,
                    height=None,
                    video_codec=None,
                    audio_codec="pcm_s16le",
                ),
            ]
        for row in rows:
            connection.execute(
                Asset.__table__.insert().values(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    sha256=hashlib.sha256(str(row["kind"]).encode()).digest(),
                    **row,
                )
            )
    return (
        JobContext(
            job_id=job_id,
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
            attempt=1,
            settings=runtime_settings(RuntimeRole.WORKER),
        ),
        source_id,
    )
