"""Durable PREVIEW_MEDIA stage runner: storyboard sheets and waveform peaks for one source.

It follows the ingest runner's shape — short tenant transactions around external work,
deterministic identities, and reuse on redelivery — and it only ever adds derived Assets,
so nothing a creator is waiting on can be delayed or changed by it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.assets.ffmpeg import MEDIA_PROCESS_TIMEOUT, FFmpegRunner, MediaProcessError
from clipah.assets.ingest import (
    HttpxSourceDownloader,
    IngestArtifact,
    IngestIntegrityError,
    SourceDownloadError,
)
from clipah.assets.keys import derived_asset_key
from clipah.assets.preview_builder import (
    PreviewInputs,
    PreviewMediaBuilder,
    PreviewMediaMaker,
    PreviewMediaResult,
    waveform_asset_id,
)
from clipah.assets.preview_media import WaveformInputError
from clipah.assets.storage import ObjectStoreUnavailableError, observed_s3_store
from clipah.config import Settings
from clipah.db import RuntimeRole, session_scope
from clipah.jobs.models import JobCancelledError, JobContext, RetryableJobError, TerminalJobError
from clipah.jobs.workspace import job_workspace
from clipah.models import Asset, AssetKind

MakerFactory = Callable[[Settings], PreviewMediaMaker]
INPUT_MISSING_CODE = "PREVIEW_MEDIA_INPUT_MISSING"
INTEGRITY_CODE = "PREVIEW_MEDIA_INTEGRITY"
INVALID_AUDIO_CODE = "PREVIEW_MEDIA_INVALID_AUDIO"


class PreviewMediaStageRunner:
    """Build previews outside transactions and record them atomically."""

    def __init__(self, *, maker_factory: MakerFactory) -> None:
        """Bind production or deterministic preview construction."""
        self._maker_factory = maker_factory

    def __call__(self, context: JobContext) -> None:
        """Process one source and expose only stable retryable or terminal codes."""
        try:
            context.raise_if_cancelled()
            inputs = self._load_inputs(context)
            if self._already_complete(context, inputs):
                return
            with job_workspace(context.job_id) as workspace:
                result = self._maker_factory(context.settings).build(
                    inputs=inputs,
                    workspace=workspace,
                    cancellation_check=context.raise_if_cancelled,
                )
            context.raise_if_cancelled()
            self._persist(context, result)
        except JobCancelledError:
            raise
        except SourceDownloadError as error:
            raise RetryableJobError(str(error) or "ASSET_SOURCE_UNAVAILABLE") from None
        except ObjectStoreUnavailableError:
            raise RetryableJobError("ASSET_STORAGE_UNAVAILABLE") from None
        except MediaProcessError as error:
            if error.code == MEDIA_PROCESS_TIMEOUT:
                raise RetryableJobError(error.code) from None
            raise TerminalJobError(error.code) from None
        except WaveformInputError:
            raise TerminalJobError(INVALID_AUDIO_CODE) from None
        except IngestIntegrityError:
            raise TerminalJobError(INTEGRITY_CODE) from None

    def _load_inputs(self, context: JobContext) -> PreviewInputs:
        """Read the single source and its recorded proxy and transcription audio."""
        with _transaction(context) as session:
            sources = session.scalars(
                select(Asset)
                .where(
                    Asset.workspace_id == context.workspace_id,
                    Asset.project_id == context.project_id,
                    Asset.kind == AssetKind.SOURCE,
                )
                .limit(2)
            ).all()
            if len(sources) != 1:
                raise TerminalJobError(INPUT_MISSING_CODE)
            source = sources[0]
            proxy = derivative(session, context, source.id, AssetKind.PROXY)
            audio = derivative(session, context, source.id, AssetKind.TRANSCRIPTION_AUDIO)
            if (
                proxy is None
                or audio is None
                or proxy.width is None
                or proxy.height is None
                or proxy.duration_ms is None
            ):
                raise TerminalJobError(INPUT_MISSING_CODE)
            return PreviewInputs(
                source_asset_id=source.id,
                workspace_id=context.workspace_id,
                project_id=context.project_id,
                proxy_key=proxy.storage_key,
                proxy_size=proxy.size_bytes,
                proxy_sha256=proxy.sha256,
                proxy_width=proxy.width,
                proxy_height=proxy.height,
                duration_ms=proxy.duration_ms,
                audio_key=audio.storage_key,
                audio_size=audio.size_bytes,
                audio_sha256=audio.sha256,
            )

    def _already_complete(self, context: JobContext, inputs: PreviewInputs) -> bool:
        """A recorded waveform and at least one sheet mean an earlier delivery finished."""
        with _transaction(context) as session:
            waveform = session.get(Asset, waveform_asset_id(inputs.source_asset_id))
            sheet = session.scalar(
                select(Asset.id)
                .where(
                    Asset.workspace_id == context.workspace_id,
                    Asset.project_id == context.project_id,
                    Asset.kind == AssetKind.STORYBOARD,
                )
                .limit(1)
            )
            return waveform is not None and sheet is not None

    def _persist(self, context: JobContext, result: PreviewMediaResult) -> None:
        """Verify existing identities, then insert every missing artifact in one transaction."""
        artifacts = (*result.sheets, result.waveform)
        identifiers = sorted((artifact.asset_id for artifact in artifacts), key=str)
        with _transaction(context) as session:
            existing = {
                row.id: row
                for row in session.scalars(
                    select(Asset)
                    .where(Asset.workspace_id == context.workspace_id, Asset.id.in_(identifiers))
                    .order_by(Asset.id)
                    .with_for_update()
                )
            }
            for artifact in artifacts:
                row = existing.get(artifact.asset_id)
                if row is not None and not matches_artifact(row, artifact):
                    raise IngestIntegrityError("preview Asset metadata mismatch")
            for artifact in artifacts:
                if artifact.asset_id not in existing:
                    session.add(asset_row(context, artifact))
            session.flush()


def derivative(
    session: Session, context: JobContext, source_id: UUID, kind: AssetKind
) -> Asset | None:
    """Find one ingest derivative of a source by the storage key ingest always writes it under.

    Ingest also derives the row identity from the source, but rows restored by hand keep the
    key layout while carrying other identities; the key is the part both always share.
    """
    key = derived_asset_key(
        workspace_id=context.workspace_id,
        project_id=context.project_id,
        source_asset_id=source_id,
        kind=kind,
    )
    return session.scalar(
        select(Asset)
        .where(
            Asset.workspace_id == context.workspace_id,
            Asset.project_id == context.project_id,
            Asset.kind == kind,
            Asset.storage_key == key,
        )
        .order_by(Asset.created_at, Asset.id)
        .limit(1)
    )


def matches_artifact(row: Asset, artifact: IngestArtifact) -> bool:
    """Whether a recorded row describes exactly this artifact."""
    return (
        row.kind is artifact.kind
        and row.storage_key == artifact.storage_key
        and row.content_type == artifact.content_type
        and row.size_bytes == artifact.size_bytes
        and row.sha256 == artifact.sha256
        and row.duration_ms == artifact.duration_ms
        and row.width == artifact.width
        and row.height == artifact.height
    )


def asset_row(context: JobContext, artifact: IngestArtifact) -> Asset:
    """Convert one artifact description into its tenant-scoped row."""
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


def production_preview_maker(settings: Settings) -> PreviewMediaMaker:
    """Compose object storage, private download, and the pinned FFmpeg runner."""
    if (
        settings.object_store_bucket is None
        or settings.object_store_access_key_id is None
        or settings.object_store_secret_access_key is None
    ):
        raise RuntimeError("preview worker requires configured object storage")
    store = observed_s3_store(
        bucket=settings.object_store_bucket,
        endpoint_url=settings.object_store_endpoint,
        access_key_id=settings.object_store_access_key_id.get_secret_value(),
        secret_access_key=settings.object_store_secret_access_key.get_secret_value(),
    )
    return PreviewMediaBuilder(
        store=store, downloader=HttpxSourceDownloader(), media=_media_runner()
    )


@lru_cache(maxsize=1)
def _media_runner() -> FFmpegRunner:
    """One runner per worker process; ingest readiness already validated the tools."""
    return FFmpegRunner()


preview_media_stage_runner = PreviewMediaStageRunner(maker_factory=production_preview_maker)
