"""Durable source-import stage runner with deterministic retry convergence."""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from clipah.assets.source_validation import validate_youtube_url
from clipah.assets.storage import S3ObjectStore, StoredObject
from clipah.assets.youtube import (
    NormalizedYouTubeUrl,
    SourceImporter,
    SourceImportError,
)
from clipah.config import Settings
from clipah.db import RuntimeRole, session_scope
from clipah.jobs.models import (
    JobCancelledError,
    JobContext,
    RetryableJobError,
    TerminalJobError,
)
from clipah.jobs.workspace import job_workspace
from clipah.models import Asset, AssetKind, AssetSourceType, SourceImport, SourceImportStatus
from clipah.source_connectors.yt_dlp_adapter import (
    HttpxSourcePreflight,
    SubprocessCommandRunner,
    YtDlpSourceImporter,
)
from clipah.source_imports.repository import SourceImportRepository

ImporterFactory = Callable[[Settings], SourceImporter]
SourceValidator = Callable[[str], NormalizedYouTubeUrl]


class SourceImportIntegrityError(Exception):
    """A deterministic Asset exists with fields that do not match this import."""


class SourceImportStageRunner:
    """Download outside transactions and converge persistence on deterministic IDs."""

    def __init__(
        self,
        *,
        importer_factory: ImporterFactory,
        validator: SourceValidator = validate_youtube_url,
    ) -> None:
        """Bind provider creation and DNS validation to this stage runner."""
        self._importer_factory = importer_factory
        self._validator = validator

    def __call__(self, context: JobContext) -> None:
        """Run one isolated attempt and expose only stable coded failures."""
        try:
            context.raise_if_cancelled()
            source_import = self._mark_downloading(context)
            source = self._validator(source_import.normalized_source_url)
            object_key = _object_key(context, source_import)
            with job_workspace(context.job_id) as workspace:
                stored = self._importer_factory(context.settings).import_source(
                    source,
                    workspace=workspace,
                    object_key=object_key,
                    cancellation_check=context.raise_if_cancelled,
                )
            context.raise_if_cancelled()
            self._complete(context, source_import=source_import, stored=stored)
        except JobCancelledError:
            self._set_status(context, SourceImportStatus.CANCELED)
            raise
        except SourceImportError as error:
            self._set_status(context, SourceImportStatus.FAILED)
            if error.retryable:
                raise RetryableJobError(error.code) from None
            raise TerminalJobError(error.code) from None

    def _mark_downloading(self, context: JobContext) -> SourceImport:
        """Reload the exact import and announce the start of this attempt."""
        with _transaction(context) as session:
            source = SourceImportRepository(session).lock_for_worker(
                workspace_id=context.workspace_id,
                project_id=context.project_id,
                job_id=context.job_id,
            )
            source.status = SourceImportStatus.DOWNLOADING
            session.flush()
            return _detached_source(source)

    def _complete(
        self, context: JobContext, *, source_import: SourceImport, stored: StoredObject
    ) -> None:
        """Insert or verify the deterministic Asset and complete the source intent."""
        if stored.sha256 is None or stored.duration_ms is None:
            raise SourceImportIntegrityError("importer returned incomplete immutable metadata")
        with _transaction(context) as session:
            repository = SourceImportRepository(session)
            locked = repository.lock_for_worker(
                workspace_id=context.workspace_id,
                project_id=context.project_id,
                job_id=context.job_id,
            )
            candidate = Asset(
                id=source_import.id,
                workspace_id=context.workspace_id,
                project_id=context.project_id,
                kind=AssetKind.SOURCE,
                source_type=AssetSourceType.SOURCE_IMPORT,
                storage_key=stored.key,
                content_type=stored.content_type,
                size_bytes=stored.content_length,
                duration_ms=stored.duration_ms,
                sha256=stored.sha256,
            )
            existing = repository.asset(
                workspace_id=context.workspace_id, source_import_id=source_import.id
            )
            if existing is None:
                repository.add_asset(candidate)
            else:
                _verify_asset(existing, candidate)
            locked.status = SourceImportStatus.COMPLETED
            session.flush()

    def _set_status(self, context: JobContext, status: SourceImportStatus) -> None:
        """Persist one terminal source state in its own short transaction."""
        with _transaction(context) as session:
            source = SourceImportRepository(session).lock_for_worker(
                workspace_id=context.workspace_id,
                project_id=context.project_id,
                job_id=context.job_id,
            )
            source.status = status
            session.flush()


def _detached_source(source: SourceImport) -> SourceImport:
    """Copy the fields needed after the short transaction closes."""
    return SourceImport(
        id=source.id,
        workspace_id=source.workspace_id,
        project_id=source.project_id,
        normalized_source_url=source.normalized_source_url,
        source_video_id=source.source_video_id,
        authorization_attested_at=source.authorization_attested_at,
        status=source.status,
        job_id=source.job_id,
        created_at=source.created_at,
    )


def _object_key(context: JobContext, source: SourceImport) -> str:
    """Build the one server-owned storage identity stable across retries."""
    return (
        f"workspaces/{context.workspace_id}/projects/{context.project_id}/"
        f"source-imports/{source.id}/original"
    )


def _verify_asset(existing: Asset, candidate: Asset) -> None:
    """Refuse to overwrite a deterministic ID whose immutable fields differ."""
    immutable = (
        "workspace_id",
        "project_id",
        "kind",
        "source_type",
        "storage_key",
        "content_type",
        "size_bytes",
        "duration_ms",
        "sha256",
    )
    if any(getattr(existing, name) != getattr(candidate, name) for name in immutable):
        raise SourceImportIntegrityError("deterministic source Asset metadata mismatch")


@contextmanager
def _transaction(context: JobContext) -> Iterator[Session]:
    """Open a least-privilege worker transaction for this Job's tenant."""
    with session_scope(
        settings=context.settings,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        runtime_role=RuntimeRole.WORKER,
    ) as session:
        yield session


def _resolve(host: str) -> tuple[str, ...]:
    """Resolve all stream-capable addresses for one validated HTTPS host."""
    return tuple(
        {str(item[4][0]) for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    )


def production_source_importer(settings: Settings) -> SourceImporter:
    """Compose the independently deployed source connector from pinned adapters."""
    if (
        settings.object_store_bucket is None
        or settings.object_store_access_key_id is None
        or settings.object_store_secret_access_key is None
    ):
        raise RuntimeError("source-import worker requires configured object storage")
    store = S3ObjectStore(
        bucket=settings.object_store_bucket,
        endpoint_url=settings.object_store_endpoint,
        access_key_id=settings.object_store_access_key_id.get_secret_value(),
        secret_access_key=settings.object_store_secret_access_key.get_secret_value(),
    )
    return YtDlpSourceImporter(
        store=store,
        runner=SubprocessCommandRunner(),
        preflight=HttpxSourcePreflight(resolver=_resolve),
        resolver=_resolve,
    )


source_import_stage_runner = SourceImportStageRunner(importer_factory=production_source_importer)
