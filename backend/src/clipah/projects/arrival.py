"""Whether a Project's video is on its way.

`uploading` means a file upload is in progress or a YouTube import is downloading. It is
entered only from `created`, and left again when nothing is still arriving: a Project
further along never moves backwards because a stray upload started or stopped.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from clipah.models import (
    Job,
    JobKind,
    JobStatus,
    MultipartUpload,
    MultipartUploadStatus,
    Project,
    ProjectStatus,
)

_ARRIVING_UPLOADS = (MultipartUploadStatus.PENDING, MultipartUploadStatus.UPLOADING)
_FINISHED_JOBS = (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELED)


def mark_media_arriving(project: Project) -> None:
    """Say the video is on its way, when the Project was still waiting for one."""
    if project.status is ProjectStatus.CREATED:
        project.status = ProjectStatus.UPLOADING


def release_media_arrival(
    session: Session,
    *,
    workspace_id: UUID,
    project_id: UUID,
    ignoring_upload_id: UUID | None = None,
    ignoring_job_id: UUID | None = None,
) -> None:
    """Return the Project to waiting when no upload or import is still arriving.

    The upload or import that just stopped is named so it is not counted, whatever its
    own row says at this instant.
    """
    project = session.scalar(
        select(Project)
        .where(Project.workspace_id == workspace_id, Project.id == project_id)
        .with_for_update()
    )
    if project is None or project.status is not ProjectStatus.UPLOADING:
        return
    uploads = select(MultipartUpload.id).where(
        MultipartUpload.workspace_id == workspace_id,
        MultipartUpload.project_id == project_id,
        MultipartUpload.status.in_(_ARRIVING_UPLOADS),
    )
    if ignoring_upload_id is not None:
        uploads = uploads.where(MultipartUpload.id != ignoring_upload_id)
    imports = select(Job.id).where(
        Job.workspace_id == workspace_id,
        Job.project_id == project_id,
        Job.kind == JobKind.SOURCE_IMPORT,
        Job.status.not_in(_FINISHED_JOBS),
    )
    if ignoring_job_id is not None:
        imports = imports.where(Job.id != ignoring_job_id)
    if session.scalar(select(exists(uploads))) or session.scalar(select(exists(imports))):
        return
    project.status = ProjectStatus.CREATED
