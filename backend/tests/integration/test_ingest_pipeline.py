"""Integration contracts for durable, retry-safe media ingest persistence."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from clipah.assets.ingest import (
    IngestArtifact,
    IngestResult,
    SourceAsset,
    SourceDownloadError,
)
from clipah.assets.storage import ObjectStoreUnavailableError
from clipah.db import RuntimeRole
from clipah.jobs.ingest_task import IngestStageRunner
from clipah.jobs.models import JobContext, RetryableJobError, TerminalJobError
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

SOURCE_DIGEST = hashlib.sha256(b"source").digest()


class StaticIngestor:
    """Return complete deterministic metadata without invoking host media tooling."""

    def __init__(self) -> None:
        """Start before any Job workspace has been observed."""
        self.workspaces: list[Path] = []

    def ingest(
        self,
        *,
        source: SourceAsset,
        workspace: Path,
        cancellation_check: Callable[[], None],
        progress: Callable[[str, float], None],
    ) -> IngestResult:
        """Prove cancellation and workspace setup, then return one complete result."""
        cancellation_check()
        assert workspace.is_dir()
        assert workspace.stat().st_mode & 0o777 == 0o700
        self.workspaces.append(workspace)
        progress("proxy", 0.5)
        source_result = IngestArtifact(
            asset_id=source.asset_id,
            kind=AssetKind.SOURCE,
            source_type=source.source_type,
            storage_key=source.storage_key,
            content_type="video/mp4",
            size_bytes=source.size_bytes,
            sha256=source.sha256,
            duration_ms=42_000,
            width=1920,
            height=1080,
            video_codec="h264",
            audio_codec="aac",
        )
        return IngestResult(
            source=source_result,
            proxy=_derived(source, AssetKind.PROXY),
            thumbnail=_derived(source, AssetKind.THUMBNAIL),
            transcription_audio=_derived(source, AssetKind.TRANSCRIPTION_AUDIO),
        )


class UnavailableIngestor(StaticIngestor):
    """Model a transient failure at the private object download boundary."""

    def ingest(self, **kwargs: object) -> IngestResult:
        """Fail with a fixed code and no signed URL or provider detail."""
        del kwargs
        raise SourceDownloadError("ASSET_SOURCE_UNAVAILABLE")


class UnavailableStorageIngestor(StaticIngestor):
    """Model a transient failure at the private object-store provider boundary."""

    def ingest(self, **kwargs: object) -> IngestResult:
        """Fail with the provider-neutral storage exception."""
        del kwargs
        raise ObjectStoreUnavailableError("provider secret")


class WrongKeyIngestor(StaticIngestor):
    """Return one derivative that escapes the server-owned Workspace prefix."""

    def ingest(self, **kwargs: object) -> IngestResult:
        """Corrupt only the proxy key while preserving otherwise plausible metadata."""
        result = super().ingest(**kwargs)  # type: ignore[arg-type]
        return replace(result, proxy=replace(result.proxy, storage_key="other-workspace/proxy"))


def _derived(source: SourceAsset, kind: AssetKind) -> IngestArtifact:
    """Build complete immutable metadata for one deterministic derivative kind."""
    content_types = {
        AssetKind.PROXY: "video/mp4",
        AssetKind.THUMBNAIL: "image/jpeg",
        AssetKind.TRANSCRIPTION_AUDIO: "audio/wav",
    }
    sizes = {AssetKind.PROXY: 100, AssetKind.THUMBNAIL: 20, AssetKind.TRANSCRIPTION_AUDIO: 80}
    return IngestArtifact(
        asset_id=uuid5(source.asset_id, kind.value),
        kind=kind,
        source_type=AssetSourceType.DERIVED,
        storage_key=(
            f"workspaces/{source.workspace_id}/projects/{source.project_id}/"
            f"derived/{source.asset_id}/{kind.value}"
        ),
        content_type=content_types[kind],
        size_bytes=sizes[kind],
        sha256=hashlib.sha256(kind.value.encode()).digest(),
        duration_ms=None if kind is AssetKind.THUMBNAIL else 42_000,
        width=1280 if kind in {AssetKind.PROXY, AssetKind.THUMBNAIL} else None,
        height=720 if kind in {AssetKind.PROXY, AssetKind.THUMBNAIL} else None,
        video_codec="h264" if kind is AssetKind.PROXY else None,
        audio_codec=(
            "aac"
            if kind is AssetKind.PROXY
            else "pcm_s16le"
            if kind is AssetKind.TRANSCRIPTION_AUDIO
            else None
        ),
    )


@pytest.mark.integration
def test_ingest_runner_is_registered_for_the_existing_durable_job_kind() -> None:
    """Queued INGEST Jobs must never fall through to the unsupported-kind failure."""
    assert JobKind.INGEST in stage_runners()


@pytest.mark.integration
def test_worker_persists_source_metadata_and_reuses_three_derivatives_on_retry(
    engine: Engine, clean_database: None
) -> None:
    """Repeated delivery must converge on one complete Asset set and deterministic identities."""
    del clean_database
    context, source_id = _seed_ingest(engine, suffix="ingest-success")
    ingestor = StaticIngestor()
    runner = IngestStageRunner(ingestor_factory=lambda _: ingestor)

    runner(context)
    runner(context)

    with Session(engine) as session:
        assets = session.scalars(
            select(Asset).where(Asset.workspace_id == context.workspace_id).order_by(Asset.kind)
        ).all()
        source = session.get(Asset, source_id)
        job = session.get(Job, context.job_id)
        assert source is not None
        assert job is not None
        assert len(assets) == 4
        assert source.duration_ms == 42_000
        assert (source.width, source.height) == (1920, 1080)
        assert source.video_codec == "h264"
        assert source.audio_codec == "aac"
        assert {asset.kind for asset in assets} == {
            AssetKind.SOURCE,
            AssetKind.PROXY,
            AssetKind.THUMBNAIL,
            AssetKind.TRANSCRIPTION_AUDIO,
        }
        assert job.stage == "proxy"
        assert float(job.progress) == pytest.approx(0.5)
    assert len(ingestor.workspaces) == 1
    assert all(not workspace.exists() for workspace in ingestor.workspaces)


@pytest.mark.integration
def test_conflicting_retry_rolls_back_the_entire_missing_derivative_set(
    engine: Engine, clean_database: None
) -> None:
    """One incompatible deterministic identity must prevent every partial metadata insert."""
    del clean_database
    context, source_id = _seed_ingest(engine, suffix="ingest-conflict")
    conflicting_id = uuid5(source_id, AssetKind.THUMBNAIL.value)
    with engine.begin() as connection:
        connection.execute(
            Asset.__table__.insert().values(
                id=conflicting_id,
                workspace_id=context.workspace_id,
                project_id=context.project_id,
                kind=AssetKind.THUMBNAIL,
                source_type=AssetSourceType.DERIVED,
                storage_key="conflicting-key",
                content_type="image/jpeg",
                size_bytes=999,
                sha256=b"z" * 32,
            )
        )

    with pytest.raises(TerminalJobError, match=r"^ASSET_INGEST_INTEGRITY$"):
        IngestStageRunner(ingestor_factory=lambda _: StaticIngestor())(context)

    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Asset)
                .where(Asset.workspace_id == context.workspace_id)
            )
            == 2
        )


@pytest.mark.integration
def test_private_download_failure_is_retryable_and_sanitized(
    engine: Engine, clean_database: None
) -> None:
    """A transient object-store read must retain retry semantics without its signed URL."""
    del clean_database
    context, _ = _seed_ingest(engine, suffix="ingest-download")

    with pytest.raises(RetryableJobError, match=r"^ASSET_SOURCE_UNAVAILABLE$"):
        IngestStageRunner(ingestor_factory=lambda _: UnavailableIngestor())(context)


@pytest.mark.integration
def test_object_store_failure_is_retryable_and_sanitized(
    engine: Engine, clean_database: None
) -> None:
    """A provider outage must retry under one fixed code without leaking diagnostics."""
    del clean_database
    context, _ = _seed_ingest(engine, suffix="ingest-storage")

    with pytest.raises(RetryableJobError, match=r"^ASSET_STORAGE_UNAVAILABLE$"):
        IngestStageRunner(ingestor_factory=lambda _: UnavailableStorageIngestor())(context)


@pytest.mark.integration
def test_worker_rejects_a_derivative_outside_the_deterministic_tenant_prefix(
    engine: Engine, clean_database: None
) -> None:
    """A faulty adapter must never persist an object key belonging to another tenant prefix."""
    del clean_database
    context, _ = _seed_ingest(engine, suffix="ingest-wrong-key")

    with pytest.raises(TerminalJobError, match=r"^ASSET_INGEST_INTEGRITY$"):
        IngestStageRunner(ingestor_factory=lambda _: WrongKeyIngestor())(context)

    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Asset)
                .where(Asset.workspace_id == context.workspace_id)
            )
            == 1
        )


def _seed_ingest(engine: Engine, *, suffix: str) -> tuple[JobContext, UUID]:
    """Create one running INGEST Job and its single source Asset."""
    user_id, workspace_id = provision_identity(engine, suffix=suffix)
    project_id = uuid4()
    job_id = uuid4()
    source_id = uuid4()
    now = datetime.now(tz=UTC)
    with engine.begin() as connection:
        connection.execute(
            Project.__table__.insert().values(
                id=project_id,
                workspace_id=workspace_id,
                created_by_user_id=user_id,
                name=f"Ingest {suffix}",
                status=ProjectStatus.INGESTING,
                source_kind=SourceKind.PUBLIC_URL,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            Job.__table__.insert().values(
                id=job_id,
                workspace_id=workspace_id,
                project_id=project_id,
                kind=JobKind.INGEST,
                status=JobStatus.RUNNING,
                stage="queued",
                progress=0,
                attempt=1,
                idempotency_key=f"ingest-{suffix}",
            )
        )
        connection.execute(
            Asset.__table__.insert().values(
                id=source_id,
                workspace_id=workspace_id,
                project_id=project_id,
                kind=AssetKind.SOURCE,
                source_type=AssetSourceType.SOURCE_IMPORT,
                storage_key=(
                    f"workspaces/{workspace_id}/projects/{project_id}/source-import/original"
                ),
                content_type="video/mp4",
                size_bytes=6,
                duration_ms=42_000,
                sha256=SOURCE_DIGEST,
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
