"""Atomic creation and safe replay of durable source-import intent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from clipah.assets.youtube import NormalizedYouTubeUrl
from clipah.jobs.admission import AdmissionPolicy
from clipah.jobs.use_cases import create_job
from clipah.models import JobKind, JobStatus, SourceImport, SourceImportStatus
from clipah.source_imports.repository import SourceImportRepository
from clipah.workspaces.models import WorkspaceAccess


class SourceImportConflictError(Exception):
    """An idempotency key was already bound to different durable work."""


class SourceImportProjectNotFoundError(Exception):
    """The selected active Project is unavailable in this Workspace."""


@dataclass(frozen=True, slots=True)
class SourceImportSnapshot:
    """Immutable identifiers and states returned after durable create or replay."""

    source_import_id: UUID
    job_id: UUID
    status: SourceImportStatus
    job_status: JobStatus


def create_source_import(
    session: Session,
    *,
    policy: AdmissionPolicy,
    access: WorkspaceAccess,
    project_id: UUID,
    source: NormalizedYouTubeUrl,
    idempotency_key: str,
    source_import_id: UUID,
    now: datetime,
) -> SourceImportSnapshot:
    """Create one Job and SourceImport atomically, or replay their exact payload."""
    repository = SourceImportRepository(session)
    repository.lock_idempotency(workspace_id=access.workspace_id, key=idempotency_key)
    if repository.active_project(workspace_id=access.workspace_id, project_id=project_id) is None:
        raise SourceImportProjectNotFoundError(str(project_id))
    existing_job = repository.job_by_key(workspace_id=access.workspace_id, key=idempotency_key)
    if existing_job is not None:
        existing = repository.by_job(workspace_id=access.workspace_id, job_id=existing_job.id)
        if (
            existing_job.kind is not JobKind.SOURCE_IMPORT
            or existing_job.project_id != project_id
            or existing is None
            or existing.project_id != project_id
            or existing.normalized_source_url != source.canonical_url
            or existing.source_video_id != source.video_id
        ):
            raise SourceImportConflictError(idempotency_key)
        return _snapshot(existing, existing_job.status)

    job = create_job(
        session,
        policy=policy,
        access=access,
        project_id=project_id,
        kind=JobKind.SOURCE_IMPORT,
        idempotency_key=idempotency_key,
        now=now,
    )
    source_import = SourceImport(
        id=source_import_id,
        workspace_id=access.workspace_id,
        project_id=project_id,
        normalized_source_url=source.canonical_url,
        source_video_id=source.video_id,
        status=SourceImportStatus.QUEUED,
        job_id=job.job_id,
    )
    repository.add(source_import)
    return _snapshot(source_import, job.status)


def _snapshot(source_import: SourceImport, job_status: JobStatus) -> SourceImportSnapshot:
    """Detach the response fields from ORM state while requiring a linked Job."""
    if source_import.job_id is None:
        raise SourceImportConflictError(str(source_import.id))
    return SourceImportSnapshot(
        source_import_id=source_import.id,
        job_id=source_import.job_id,
        status=source_import.status,
        job_status=job_status,
    )
