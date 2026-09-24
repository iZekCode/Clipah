"""Durable CLIP_COVER stage runner: one designed cover per clip's current Revision.

A member asks for a cover from the editor, and the Job draws every cover the Project is
still missing: the current Revision of each clip whose composition designs one. It follows
the poster runner's shape — short tenant transactions around external work, deterministic
identities, and reuse on redelivery — and only ever adds derived Assets.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.assets.clip_covers import (
    ClipCoverBuilder,
    ClipCoverMaker,
    CoverInputs,
    CoverRevision,
    StoredPicture,
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
from clipah.editor.models import CompositionValidationError, WatermarkKind, parse_composition
from clipah.jobs.models import JobCancelledError, JobContext, RetryableJobError, TerminalJobError
from clipah.jobs.preview_media_task import asset_row, derivative, matches_artifact
from clipah.jobs.workspace import job_workspace
from clipah.models import Asset, AssetKind, ClipCandidate, ClipEdit, ClipEditRevision
from clipah.renders.cover import CoverUnavailableError, cover_asset_id

MakerFactory = Callable[[Settings], ClipCoverMaker]
INPUT_MISSING_CODE = "CLIP_COVER_INPUT_MISSING"
INTEGRITY_CODE = "CLIP_COVER_INTEGRITY"
UNDRAWABLE_CODE = "CLIP_COVER_UNDRAWABLE"


class ClipCoverStageRunner:
    """Draw the covers still missing outside transactions and record them atomically."""

    def __init__(self, *, maker_factory: MakerFactory) -> None:
        """Bind production or deterministic cover construction."""
        self._maker_factory = maker_factory

    def __call__(self, context: JobContext) -> None:
        """Process one Project and expose only stable retryable or terminal codes."""
        try:
            context.raise_if_cancelled()
            inputs = self._load_inputs(context)
            if not inputs.revisions:
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
        except CoverUnavailableError:
            raise TerminalJobError(UNDRAWABLE_CODE) from None
        except IngestIntegrityError:
            raise TerminalJobError(INTEGRITY_CODE) from None

    def _load_inputs(self, context: JobContext) -> CoverInputs:
        """Read the proxy and every current Revision that designs a cover not yet drawn."""
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
            drawn = set(
                session.scalars(
                    select(Asset.id).where(
                        Asset.workspace_id == context.workspace_id,
                        Asset.project_id == context.project_id,
                        Asset.kind == AssetKind.COVER,
                    )
                )
            )
            revisions = [
                revision
                for revision in _current_revisions(session, context)
                if cover_asset_id(revision.revision_id) not in drawn
            ]
            return CoverInputs(
                source_asset_id=source.id,
                workspace_id=context.workspace_id,
                project_id=context.project_id,
                proxy_key=proxy.storage_key,
                proxy_size=proxy.size_bytes,
                proxy_sha256=proxy.sha256,
                proxy_width=proxy.width,
                proxy_height=proxy.height,
                revisions=tuple(revisions),
            )

    def _persist(self, context: JobContext, artifacts: tuple[IngestArtifact, ...]) -> None:
        """Verify existing identities, then insert every missing cover in one transaction."""
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
                    raise IngestIntegrityError("cover Asset metadata mismatch")
            for artifact in artifacts:
                if artifact.asset_id not in existing:
                    session.add(asset_row(context, artifact))
            session.flush()


def _current_revisions(session: Session, context: JobContext) -> list[CoverRevision]:
    """Each clip's current Revision in this Project that designs a cover, oldest clip first."""
    rows = session.execute(
        select(ClipEditRevision.id, ClipEditRevision.composition)
        .join(
            ClipEdit,
            (ClipEdit.workspace_id == ClipEditRevision.workspace_id)
            & (ClipEdit.id == ClipEditRevision.clip_edit_id)
            & (ClipEdit.current_revision == ClipEditRevision.revision),
        )
        .join(
            ClipCandidate,
            (ClipCandidate.workspace_id == ClipEdit.workspace_id)
            & (ClipCandidate.id == ClipEdit.candidate_id),
        )
        .where(
            ClipEditRevision.workspace_id == context.workspace_id,
            ClipCandidate.project_id == context.project_id,
        )
        .order_by(ClipEdit.created_at, ClipEdit.id)
    ).all()
    revisions: list[CoverRevision] = []
    for revision_id, document in rows:
        try:
            composition = parse_composition(document)
        except CompositionValidationError:
            raise TerminalJobError(INTEGRITY_CODE) from None
        if composition.cover is None:
            continue
        picture = None
        mark = composition.watermark
        if mark is not None and mark.kind is WatermarkKind.IMAGE:
            stored = session.scalars(
                select(Asset).where(
                    Asset.workspace_id == context.workspace_id,
                    Asset.project_id == context.project_id,
                    Asset.id == mark.asset_id,
                )
            ).first()
            if stored is None:
                raise TerminalJobError(INPUT_MISSING_CODE)
            picture = StoredPicture(
                key=stored.storage_key, size=stored.size_bytes, sha256=stored.sha256
            )
        revisions.append(
            CoverRevision(
                revision_id=revision_id, composition=composition, watermark_picture=picture
            )
        )
    return revisions


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


def production_cover_maker(settings: Settings) -> ClipCoverMaker:
    """Compose object storage, private download, and the pinned FFmpeg runner."""
    if (
        settings.object_store_bucket is None
        or settings.object_store_access_key_id is None
        or settings.object_store_secret_access_key is None
    ):
        raise RuntimeError("cover worker requires configured object storage")
    store = observed_s3_store(
        bucket=settings.object_store_bucket,
        endpoint_url=settings.object_store_endpoint,
        access_key_id=settings.object_store_access_key_id.get_secret_value(),
        secret_access_key=settings.object_store_secret_access_key.get_secret_value(),
    )
    return ClipCoverBuilder(store=store, downloader=HttpxSourceDownloader(), media=_media_runner())


@lru_cache(maxsize=1)
def _media_runner() -> FFmpegRunner:
    """One runner per worker process; render readiness already validated the tools."""
    return FFmpegRunner()


clip_cover_stage_runner = ClipCoverStageRunner(maker_factory=production_cover_maker)
