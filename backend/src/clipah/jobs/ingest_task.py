"""Durable INGEST stage runner with short transactions and idempotent Asset persistence."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from uuid import uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.assets.ffmpeg import MEDIA_PROCESS_TIMEOUT, FFmpegRunner, MediaProcessError
from clipah.assets.ingest import (
    AssetIngestor,
    HttpxSourceDownloader,
    IngestArtifact,
    IngestIntegrityError,
    IngestResult,
    SourceAsset,
    SourceDownloadError,
)
from clipah.assets.keys import derived_asset_key
from clipah.assets.probe import MediaValidationError, sniff_mime
from clipah.assets.storage import ObjectStoreUnavailableError, S3ObjectStore
from clipah.config import Settings
from clipah.db import RuntimeRole, session_scope
from clipah.jobs.models import (
    JobCancelledError,
    JobContext,
    RetryableJobError,
    TerminalJobError,
)
from clipah.jobs.use_cases import update_job_progress
from clipah.jobs.workspace import job_workspace
from clipah.models import Asset, AssetKind, AssetSourceType

IngestorFactory = Callable[[Settings], AssetIngestor]
INGEST_INTEGRITY_CODE = "ASSET_INGEST_INTEGRITY"
SOURCE_NOT_FOUND_CODE = "ASSET_SOURCE_NOT_FOUND"


class IngestStageRunner:
    """Run external ingest outside transactions and atomically converge Asset metadata."""

    def __init__(self, *, ingestor_factory: IngestorFactory) -> None:
        """Bind production or deterministic ingest dependency creation."""
        self._ingestor_factory = ingestor_factory

    def __call__(self, context: JobContext) -> None:
        """Process one source and expose only stable retryable or terminal Job codes."""
        try:
            context.raise_if_cancelled()
            source = self._load_source(context)
            if self._already_complete(context, source):
                return
            with job_workspace(context.job_id) as workspace:
                result = self._ingestor_factory(context.settings).ingest(
                    source=source,
                    workspace=workspace,
                    cancellation_check=context.raise_if_cancelled,
                    progress=lambda stage, ratio: self._report_progress(
                        context, stage=stage, progress=ratio
                    ),
                )
            context.raise_if_cancelled()
            _validate_result_shape(source, result)
            self._persist(context, result)
        except JobCancelledError:
            raise
        except SourceDownloadError as error:
            raise RetryableJobError(str(error) or "ASSET_SOURCE_UNAVAILABLE") from None
        except ObjectStoreUnavailableError:
            raise RetryableJobError("ASSET_STORAGE_UNAVAILABLE") from None
        except MediaValidationError as error:
            raise TerminalJobError(error.code) from None
        except MediaProcessError as error:
            if error.code == MEDIA_PROCESS_TIMEOUT:
                raise RetryableJobError(error.code) from None
            raise TerminalJobError(error.code) from None
        except IngestIntegrityError:
            raise TerminalJobError(INGEST_INTEGRITY_CODE) from None

    def _load_source(self, context: JobContext) -> SourceAsset:
        """Detach the single source Asset selected by the Job's tenant and Project."""
        with _transaction(context) as session:
            sources = session.scalars(
                select(Asset)
                .where(
                    Asset.workspace_id == context.workspace_id,
                    Asset.project_id == context.project_id,
                    Asset.kind == AssetKind.SOURCE,
                )
                .order_by(Asset.created_at, Asset.id)
                .limit(2)
            ).all()
            if len(sources) != 1:
                raise TerminalJobError(SOURCE_NOT_FOUND_CODE)
            source = sources[0]
            return SourceAsset(
                asset_id=source.id,
                workspace_id=source.workspace_id,
                project_id=source.project_id,
                source_type=source.source_type,
                storage_key=source.storage_key,
                content_type=source.content_type,
                size_bytes=source.size_bytes,
                sha256=source.sha256,
            )

    def _already_complete(self, context: JobContext, source: SourceAsset) -> bool:
        """Reuse a complete deterministic Asset set without repeating external media work."""
        kinds = (AssetKind.PROXY, AssetKind.THUMBNAIL, AssetKind.TRANSCRIPTION_AUDIO)
        identifiers = {uuid5(source.asset_id, kind.value): kind for kind in kinds}
        with _transaction(context) as session:
            source_row = session.scalar(
                select(Asset).where(
                    Asset.workspace_id == context.workspace_id,
                    Asset.id == source.asset_id,
                    Asset.project_id == context.project_id,
                )
            )
            rows = session.scalars(
                select(Asset).where(
                    Asset.workspace_id == context.workspace_id,
                    Asset.project_id == context.project_id,
                    Asset.id.in_(identifiers),
                )
            ).all()
            if source_row is None or not _source_metadata_complete(source_row) or len(rows) != 3:
                return False
            return all(
                _derived_metadata_complete(
                    row,
                    expected_kind=identifiers[row.id],
                    expected_key=derived_asset_key(
                        workspace_id=context.workspace_id,
                        project_id=context.project_id,
                        source_asset_id=source.asset_id,
                        kind=identifiers[row.id],
                    ),
                )
                for row in rows
            )

    def _report_progress(self, context: JobContext, *, stage: str, progress: float) -> None:
        """Persist one normalized progress event in its own short worker transaction."""
        with _transaction(context) as session:
            update_job_progress(
                session,
                workspace_id=context.workspace_id,
                job_id=context.job_id,
                stage=stage,
                progress=progress,
                now=datetime.now(tz=UTC),
            )

    def _persist(self, context: JobContext, result: IngestResult) -> None:
        """Verify existing identities before updating or inserting the complete Asset set."""
        artifacts = (
            result.source,
            result.proxy,
            result.thumbnail,
            result.transcription_audio,
        )
        if (
            result.source.asset_id
            in {
                result.proxy.asset_id,
                result.thumbnail.asset_id,
                result.transcription_audio.asset_id,
            }
            or len({artifact.asset_id for artifact in artifacts}) != 4
        ):
            raise IngestIntegrityError("ingest returned duplicate Asset identities")
        identifiers = sorted((artifact.asset_id for artifact in artifacts), key=str)
        with _transaction(context) as session:
            existing = {
                asset.id: asset
                for asset in session.scalars(
                    select(Asset)
                    .where(
                        Asset.workspace_id == context.workspace_id,
                        Asset.id.in_(identifiers),
                    )
                    .order_by(Asset.id)
                    .with_for_update()
                )
            }
            source = existing.get(result.source.asset_id)
            if source is None or source.kind is not AssetKind.SOURCE:
                raise IngestIntegrityError("source Asset disappeared during ingest")
            _verify_source(source, result.source)
            for artifact in artifacts[1:]:
                row = existing.get(artifact.asset_id)
                if row is not None:
                    _verify_derived(row, artifact)

            _apply_source_metadata(source, result.source)
            for artifact in artifacts[1:]:
                if artifact.asset_id not in existing:
                    session.add(_asset_row(context, artifact))
            session.flush()


def _verify_source(existing: Asset, observed: IngestArtifact) -> None:
    """Require immutable source identity and any prior metadata to match the validated bytes."""
    immutable = (
        existing.id == observed.asset_id,
        existing.kind is AssetKind.SOURCE,
        existing.source_type is observed.source_type,
        existing.storage_key == observed.storage_key,
        existing.size_bytes == observed.size_bytes,
        existing.sha256 == observed.sha256,
        existing.duration_ms in {None, observed.duration_ms},
        existing.width in {None, observed.width},
        existing.height in {None, observed.height},
        existing.video_codec in {None, observed.video_codec},
        existing.audio_codec in {None, observed.audio_codec},
    )
    if not all(immutable):
        raise IngestIntegrityError("source Asset metadata mismatch")


def _validate_result_shape(source: SourceAsset, result: IngestResult) -> None:
    """Independently enforce all server-owned identities returned by the ingest adapter."""
    if (
        result.source.asset_id != source.asset_id
        or result.source.kind is not AssetKind.SOURCE
        or result.source.source_type is not source.source_type
        or result.source.storage_key != source.storage_key
        or result.source.size_bytes != source.size_bytes
        or result.source.sha256 != source.sha256
    ):
        raise IngestIntegrityError("ingest returned a conflicting source identity")
    derivatives = (
        result.proxy,
        result.thumbnail,
        result.transcription_audio,
    )
    expected_kinds = (
        AssetKind.PROXY,
        AssetKind.THUMBNAIL,
        AssetKind.TRANSCRIPTION_AUDIO,
    )
    for artifact, expected_kind in zip(derivatives, expected_kinds, strict=True):
        expected_id = uuid5(source.asset_id, expected_kind.value)
        expected_key = derived_asset_key(
            workspace_id=source.workspace_id,
            project_id=source.project_id,
            source_asset_id=source.asset_id,
            kind=expected_kind,
        )
        if (
            artifact.asset_id != expected_id
            or artifact.kind is not expected_kind
            or artifact.source_type is not AssetSourceType.DERIVED
            or artifact.storage_key != expected_key
        ):
            raise IngestIntegrityError("ingest returned a conflicting derivative identity")


def _source_metadata_complete(source: Asset) -> bool:
    """Recognize a source row whose required ingest metadata is already durable."""
    return bool(
        source.kind is AssetKind.SOURCE
        and source.size_bytes > 0
        and len(source.sha256) == 32
        and source.duration_ms is not None
        and source.width is not None
        and source.height is not None
        and source.video_codec
        and source.audio_codec
    )


def _derived_metadata_complete(
    asset: Asset, *, expected_kind: AssetKind, expected_key: str
) -> bool:
    """Recognize one complete deterministic derivative eligible for retry reuse."""
    if (
        asset.kind is not expected_kind
        or asset.source_type is not AssetSourceType.DERIVED
        or asset.storage_key != expected_key
        or asset.size_bytes <= 0
        or len(asset.sha256) != 32
    ):
        return False
    if expected_kind is AssetKind.PROXY:
        return bool(
            asset.content_type == "video/mp4"
            and asset.duration_ms is not None
            and asset.width is not None
            and asset.height is not None
            and asset.video_codec == "h264"
            and asset.audio_codec == "aac"
        )
    if expected_kind is AssetKind.THUMBNAIL:
        return bool(
            asset.content_type == "image/jpeg"
            and asset.duration_ms is None
            and asset.width is not None
            and asset.height is not None
            and asset.video_codec is None
            and asset.audio_codec is None
        )
    return bool(
        asset.content_type == "audio/wav"
        and asset.duration_ms is not None
        and asset.width is None
        and asset.height is None
        and asset.video_codec is None
        and asset.audio_codec == "pcm_s16le"
    )


def _verify_derived(existing: Asset, observed: IngestArtifact) -> None:
    """Refuse a retry whose deterministic derived identity points at different metadata."""
    fields = (
        "id",
        "kind",
        "source_type",
        "storage_key",
        "content_type",
        "size_bytes",
        "sha256",
        "duration_ms",
        "width",
        "height",
        "video_codec",
        "audio_codec",
    )
    observed_values = {
        "id": observed.asset_id,
        "kind": observed.kind,
        "source_type": observed.source_type,
        "storage_key": observed.storage_key,
        "content_type": observed.content_type,
        "size_bytes": observed.size_bytes,
        "sha256": observed.sha256,
        "duration_ms": observed.duration_ms,
        "width": observed.width,
        "height": observed.height,
        "video_codec": observed.video_codec,
        "audio_codec": observed.audio_codec,
    }
    if any(getattr(existing, field) != observed_values[field] for field in fields):
        raise IngestIntegrityError("derived Asset metadata mismatch")


def _apply_source_metadata(existing: Asset, observed: IngestArtifact) -> None:
    """Fill the source row with the metadata validated from its immutable bytes."""
    existing.content_type = observed.content_type
    existing.duration_ms = observed.duration_ms
    existing.width = observed.width
    existing.height = observed.height
    existing.video_codec = observed.video_codec
    existing.audio_codec = observed.audio_codec


def _asset_row(context: JobContext, artifact: IngestArtifact) -> Asset:
    """Convert one provider-neutral artifact result into its tenant-scoped ORM row."""
    return Asset(
        id=artifact.asset_id,
        workspace_id=context.workspace_id,
        project_id=context.project_id,
        kind=artifact.kind,
        source_type=artifact.source_type,
        storage_key=artifact.storage_key,
        content_type=artifact.content_type,
        size_bytes=artifact.size_bytes,
        sha256=artifact.sha256,
        duration_ms=artifact.duration_ms,
        width=artifact.width,
        height=artifact.height,
        video_codec=artifact.video_codec,
        audio_codec=artifact.audio_codec,
    )


@contextmanager
def _transaction(context: JobContext) -> Iterator[Session]:
    """Open one least-privilege worker transaction for this Job's tenant."""
    with session_scope(
        settings=context.settings,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        runtime_role=RuntimeRole.WORKER,
    ) as session:
        yield session


def production_asset_ingestor(settings: Settings) -> AssetIngestor:
    """Compose production object storage, private download, and media process adapters."""
    if (
        settings.object_store_bucket is None
        or settings.object_store_access_key_id is None
        or settings.object_store_secret_access_key is None
    ):
        raise RuntimeError("ingest worker requires configured object storage")
    store = S3ObjectStore(
        bucket=settings.object_store_bucket,
        endpoint_url=settings.object_store_endpoint,
        access_key_id=settings.object_store_access_key_id.get_secret_value(),
        secret_access_key=settings.object_store_secret_access_key.get_secret_value(),
    )
    return AssetIngestor(
        store=store,
        downloader=HttpxSourceDownloader(),
        media=_validated_media_runner(),
    )


@lru_cache(maxsize=1)
def _validated_media_runner() -> FFmpegRunner:
    """Validate pinned media tools once before this worker process accepts ingest work."""
    runner = FFmpegRunner()
    runner.validate_versions()
    return runner


def validate_ingest_readiness() -> None:
    """Fail worker startup unless both pinned media tools and native libmagic are usable."""
    _validated_media_runner()
    sniff_mime(Path(__file__))


ingest_stage_runner = IngestStageRunner(ingestor_factory=production_asset_ingestor)
