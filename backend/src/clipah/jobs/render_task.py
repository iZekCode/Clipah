"""Durable RENDER stage runner: compile one composition, encode it, and store the file.

Everything expensive happens outside a transaction and inside the Job's own workspace.
The stage is safe to redeliver: an export of the same composition at the same preset is
recognized before any media is downloaded, and the artifact row carries the composition
hash it was produced from, so two deliveries converge on one file.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clipah.assets.ffmpeg import MEDIA_PROCESS_TIMEOUT, FFmpegRunner, MediaProcessError
from clipah.assets.ingest import SIGNED_DOWNLOAD_TTL, HttpxSourceDownloader, SourceDownloader
from clipah.assets.keys import render_artifact_key
from clipah.assets.storage import ObjectStore, ObjectStoreUnavailableError, S3ObjectStore
from clipah.config import Settings
from clipah.db import RuntimeRole, session_scope
from clipah.editor.models import CompositionValidationError, collect_asset_ids, parse_composition
from clipah.jobs.models import (
    JobCancelledError,
    JobContext,
    RetryableJobError,
    TerminalJobError,
)
from clipah.jobs.use_cases import update_job_progress
from clipah.jobs.workspace import job_workspace
from clipah.models import Asset, RenderArtifact
from clipah.renders.compiler import compile_render_plan, input_path
from clipah.renders.ffmpeg_renderer import FFmpegRenderer, RenderExecutionError
from clipah.renders.models import (
    RENDER_FAILED,
    RenderAsset,
    RenderCompilationError,
    RenderOutput,
    RenderPlan,
    Watermark,
)
from clipah.renders.use_cases import RenderTarget, healthy_artifact, render_target

RENDER_TARGET_MISSING = "RENDER_TARGET_MISSING"
RENDER_INTEGRITY = "RENDER_INTEGRITY"
MAX_RENDER_INPUT_BYTES = 4 * 1024 * 1024 * 1024

RendererFactory = Callable[[Settings], FFmpegRenderer]
StoreFactory = Callable[[Settings], ObjectStore]
DownloaderFactory = Callable[[Settings], SourceDownloader]


class RenderStageRunner:
    """Turn one Edit Revision into one stored MP4, or fail with a stable public code."""

    def __init__(
        self,
        *,
        renderer_factory: RendererFactory,
        store_factory: StoreFactory,
        downloader_factory: DownloaderFactory,
    ) -> None:
        """Bind production or deterministic render dependencies."""
        self._renderer_factory = renderer_factory
        self._store_factory = store_factory
        self._downloader_factory = downloader_factory

    def __call__(self, context: JobContext) -> None:
        """Render one export, reusing an identical one rather than encoding it twice."""
        try:
            context.raise_if_cancelled()
            target = self._load_target(context)
            if self._already_rendered(context, target):
                return
            store = self._store_factory(context.settings)
            with job_workspace(context.job_id) as workspace:
                plan = self._prepare(context, target, store=store, workspace=workspace)
                context.raise_if_cancelled()
                output = self._renderer_factory(context.settings).render(
                    plan,
                    workspace=workspace,
                    cancellation_check=context.raise_if_cancelled,
                    progress=lambda ratio: self._report_progress(context, ratio),
                )
                context.raise_if_cancelled()
                self._store_output(context, target, output, store=store)
        except JobCancelledError:
            raise
        except RenderCompilationError as error:
            raise TerminalJobError(error.code) from None
        except RenderExecutionError as error:
            raise TerminalJobError(error.code) from None
        except CompositionValidationError:
            raise TerminalJobError(RENDER_INTEGRITY) from None
        except ObjectStoreUnavailableError:
            raise RetryableJobError("ASSET_STORAGE_UNAVAILABLE") from None
        except MediaProcessError as error:
            if error.code == MEDIA_PROCESS_TIMEOUT:
                raise RetryableJobError(error.code) from None
            raise TerminalJobError(RENDER_FAILED) from None

    def _load_target(self, context: JobContext) -> RenderTarget:
        """Read what this Job was admitted to render, in its own short transaction."""
        with _transaction(context) as session:
            target = render_target(
                session, workspace_id=context.workspace_id, job_id=context.job_id
            )
        if target is None:
            raise TerminalJobError(RENDER_TARGET_MISSING)
        return target

    def _already_rendered(self, context: JobContext, target: RenderTarget) -> bool:
        """Recognize an identical export before any media is downloaded or encoded."""
        with _transaction(context) as session:
            return (
                healthy_artifact(
                    session,
                    workspace_id=context.workspace_id,
                    composition_hash=target.composition_hash,
                    preset=target.preset,
                )
                is not None
            )

    def _prepare(
        self,
        context: JobContext,
        target: RenderTarget,
        *,
        store: ObjectStore,
        workspace: Path,
    ) -> RenderPlan:
        """Download every asset the composition names and compile the plan that uses them."""
        composition = parse_composition(target.composition)
        wanted = collect_asset_ids(composition)
        with _transaction(context) as session:
            rows = session.scalars(
                select(Asset).where(
                    Asset.workspace_id == context.workspace_id,
                    Asset.project_id == target.project_id,
                    Asset.id.in_(wanted),
                )
            ).all()
            table = {
                row.id: (
                    RenderAsset(
                        asset_id=row.id,
                        kind=row.kind,
                        content_type=row.content_type,
                        duration_ms=row.duration_ms,
                        width=row.width,
                        height=row.height,
                    ),
                    row.storage_key,
                    row.size_bytes,
                )
                for row in rows
            }
        if set(table) != set(wanted):
            raise TerminalJobError(RENDER_INTEGRITY)

        (workspace / "inputs").mkdir(parents=True, exist_ok=True)
        downloader = self._downloader_factory(context.settings)
        for asset_id, (_asset, storage_key, size_bytes) in table.items():
            context.raise_if_cancelled()
            signed = store.sign_download(key=storage_key, expires_in=SIGNED_DOWNLOAD_TTL)
            destination = input_path(workspace, asset_id)
            with destination.open("wb") as handle:
                downloader.download(
                    signed.url,
                    handle,
                    expected_size=size_bytes,
                    max_bytes=MAX_RENDER_INPUT_BYTES,
                    cancellation_check=context.raise_if_cancelled,
                )
        return compile_render_plan(
            composition,
            assets={asset_id: entry[0] for asset_id, entry in table.items()},
            preset=target.preset,
            workspace=workspace,
            watermark=_watermark(context.settings),
        )

    def _store_output(
        self,
        context: JobContext,
        target: RenderTarget,
        output: RenderOutput,
        *,
        store: ObjectStore,
    ) -> None:
        """Upload the finished export, verify the stored bytes, and record the artifact."""
        key = render_artifact_key(
            workspace_id=context.workspace_id,
            project_id=target.project_id,
            revision_id=target.revision_id,
            preset=target.preset.value,
        )
        with output.path.open("rb") as handle:
            stored = store.put_file(
                key=key, content_type="video/mp4", file=handle, sha256=output.sha256
            )
        observed = store.head_object(key=key)
        if any(
            candidate.key != key
            or candidate.content_length != output.size_bytes
            or candidate.sha256 != output.sha256
            for candidate in (stored, observed)
        ):
            store.delete_object(key=key)
            raise TerminalJobError(RENDER_INTEGRITY)

        with _transaction(context) as session:
            try:
                with session.begin_nested():
                    session.add(
                        RenderArtifact(
                            id=uuid4(),
                            workspace_id=context.workspace_id,
                            clip_edit_revision_id=target.revision_id,
                            job_id=context.job_id,
                            preset=target.preset.value,
                            composition_hash=target.composition_hash,
                            storage_key=key,
                            size_bytes=output.size_bytes,
                            duration_ms=output.duration_ms,
                        )
                    )
            except IntegrityError:
                # Another delivery of this Job stored the same export first; that file is
                # byte-identical, because the same composition and preset produced it.
                if (
                    healthy_artifact(
                        session,
                        workspace_id=context.workspace_id,
                        composition_hash=target.composition_hash,
                        preset=target.preset,
                    )
                    is None
                ):
                    raise TerminalJobError(RENDER_INTEGRITY) from None

    def _report_progress(self, context: JobContext, ratio: float) -> None:
        """Persist one normalized progress event in its own short worker transaction."""
        with _transaction(context) as session:
            update_job_progress(
                session,
                workspace_id=context.workspace_id,
                job_id=context.job_id,
                stage="render",
                progress=ratio,
                now=datetime.now(tz=UTC),
            )


def _watermark(settings: Settings) -> Watermark | None:
    """Read the brand mark this deployment burns into exports, if it burns one."""
    text = settings.render_watermark_text
    return None if not text else Watermark(text=text)


@contextmanager
def _transaction(context: JobContext) -> Iterator[Session]:
    """Open one short least-privilege worker transaction for this Job's tenant."""
    with session_scope(
        settings=context.settings,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        runtime_role=RuntimeRole.WORKER,
    ) as session:
        yield session


def production_object_store(settings: Settings) -> ObjectStore:
    """Compose the configured object store, refusing to render without one."""
    if (
        settings.object_store_bucket is None
        or settings.object_store_access_key_id is None
        or settings.object_store_secret_access_key is None
    ):
        raise RuntimeError("render worker requires configured object storage")
    return S3ObjectStore(
        bucket=settings.object_store_bucket,
        endpoint_url=settings.object_store_endpoint,
        access_key_id=settings.object_store_access_key_id.get_secret_value(),
        secret_access_key=settings.object_store_secret_access_key.get_secret_value(),
    )


@lru_cache(maxsize=1)
def _validated_renderer() -> FFmpegRenderer:
    """Validate the pinned media tools once before this process accepts render work."""
    FFmpegRunner().validate_versions()
    return FFmpegRenderer()


def validate_render_readiness() -> None:
    """Fail worker startup unless the pinned FFmpeg this renderer was written for is present."""
    _validated_renderer()


render_stage_runner = RenderStageRunner(
    renderer_factory=lambda settings: _validated_renderer(),
    store_factory=production_object_store,
    downloader_factory=lambda settings: HttpxSourceDownloader(),
)


def render_workspace_input(workspace: Path, asset_id: UUID) -> Path:
    """Expose where one asset lands, so a test can stage inputs the way the worker does."""
    return input_path(workspace, asset_id)
