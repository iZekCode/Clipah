"""Durable CLIP_POSTERS stage runner: one sharp portrait poster per exposed moment.

It runs beside the belt once analysis has ranked a Project's moments, follows the preview
runner's shape — short tenant transactions around external work, deterministic identities,
and reuse on redelivery — and only ever adds derived Assets, so a reviewer is never kept
waiting on it: until a poster exists, the card draws its storyboard tile instead.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.assets.clip_posters import (
    ClipPosterBuilder,
    ClipPosterMaker,
    PosterInputs,
    PosterMoment,
    poster_asset_id,
    poster_time_ms,
)
from clipah.assets.ffmpeg import MEDIA_PROCESS_TIMEOUT, FFmpegRunner, MediaProcessError
from clipah.assets.ingest import (
    HttpxSourceDownloader,
    IngestArtifact,
    IngestIntegrityError,
    SourceDownloadError,
)
from clipah.assets.storage import ObjectStoreUnavailableError, observed_s3_store
from clipah.config import Settings
from clipah.db import RuntimeRole, session_scope
from clipah.jobs.models import JobCancelledError, JobContext, RetryableJobError, TerminalJobError
from clipah.jobs.preview_media_task import asset_row, derivative, matches_artifact
from clipah.jobs.workspace import job_workspace
from clipah.models import Asset, AssetKind, ClipCandidate

MakerFactory = Callable[[Settings], ClipPosterMaker]
INPUT_MISSING_CODE = "CLIP_POSTERS_INPUT_MISSING"
INTEGRITY_CODE = "CLIP_POSTERS_INTEGRITY"


class ClipPostersStageRunner:
    """Draw the posters still missing outside transactions and record them atomically."""

    def __init__(self, *, maker_factory: MakerFactory) -> None:
        """Bind production or deterministic poster construction."""
        self._maker_factory = maker_factory

    def __call__(self, context: JobContext) -> None:
        """Process one Project and expose only stable retryable or terminal codes."""
        try:
            context.raise_if_cancelled()
            inputs = self._load_inputs(context)
            if not inputs.moments:
                return
            with job_workspace(context.job_id) as workspace:
                artifacts = self._maker_factory(context.settings).build(
                    inputs=inputs,
                    workspace=workspace,
                    cancellation_check=context.raise_if_cancelled,
                )
            context.raise_if_cancelled()
            self._persist(context, artifacts)
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
        except IngestIntegrityError:
            raise TerminalJobError(INTEGRITY_CODE) from None

    def _load_inputs(self, context: JobContext) -> PosterInputs:
        """Read the proxy and every exposed moment that has no poster yet, best first."""
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
            if proxy is None or proxy.width is None or proxy.height is None:
                raise TerminalJobError(INPUT_MISSING_CODE)
            moments = session.execute(
                select(ClipCandidate.id, ClipCandidate.start_ms, ClipCandidate.end_ms)
                .where(
                    ClipCandidate.workspace_id == context.workspace_id,
                    ClipCandidate.project_id == context.project_id,
                    ClipCandidate.model_metadata["exposed"].as_boolean().is_(True),
                )
                .order_by(ClipCandidate.rank)
            ).all()
            drawn = set(
                session.scalars(
                    select(Asset.id).where(
                        Asset.workspace_id == context.workspace_id,
                        Asset.project_id == context.project_id,
                        Asset.kind == AssetKind.POSTER,
                    )
                )
            )
            return PosterInputs(
                source_asset_id=source.id,
                workspace_id=context.workspace_id,
                project_id=context.project_id,
                proxy_key=proxy.storage_key,
                proxy_size=proxy.size_bytes,
                proxy_sha256=proxy.sha256,
                proxy_width=proxy.width,
                proxy_height=proxy.height,
                moments=tuple(
                    PosterMoment(
                        candidate_id=row.id, at_ms=poster_time_ms(row.start_ms, row.end_ms)
                    )
                    for row in moments
                    if poster_asset_id(source.id, row.id) not in drawn
                ),
            )

    def _persist(self, context: JobContext, artifacts: tuple[IngestArtifact, ...]) -> None:
        """Verify existing identities, then insert every missing poster in one transaction."""
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
                    raise IngestIntegrityError("poster Asset metadata mismatch")
            for artifact in artifacts:
                if artifact.asset_id not in existing:
                    session.add(asset_row(context, artifact))
            session.flush()


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


def production_poster_maker(settings: Settings) -> ClipPosterMaker:
    """Compose object storage, private download, and the pinned FFmpeg runner."""
    if (
        settings.object_store_bucket is None
        or settings.object_store_access_key_id is None
        or settings.object_store_secret_access_key is None
    ):
        raise RuntimeError("poster worker requires configured object storage")
    store = observed_s3_store(
        bucket=settings.object_store_bucket,
        endpoint_url=settings.object_store_endpoint,
        access_key_id=settings.object_store_access_key_id.get_secret_value(),
        secret_access_key=settings.object_store_secret_access_key.get_secret_value(),
    )
    return ClipPosterBuilder(store=store, downloader=HttpxSourceDownloader(), media=_media_runner())


@lru_cache(maxsize=1)
def _media_runner() -> FFmpegRunner:
    """One runner per worker process; ingest readiness already validated the tools."""
    return FFmpegRunner()


clip_posters_stage_runner = ClipPostersStageRunner(maker_factory=production_poster_maker)
