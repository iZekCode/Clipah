"""Requesting an export, reusing one that already exists, and reading it back.

An export is expensive and perfectly reproducible: the same composition at the same
preset always produces the same file. So a render is deduplicated by the composition hash
the Edit Revision already carries, and a healthy artifact is handed back rather than made
again. Everything else here is the ordinary admission path — prove standing, admit one
durable Job, and record what that Job is for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Row, Select, select
from sqlalchemy.orm import Session

from clipah.assets.storage import ObjectStore, SignedUrl
from clipah.jobs.admission import AdmissionPolicy, admit_job
from clipah.models import (
    ClipCandidate,
    ClipEdit,
    ClipEditRevision,
    JobKind,
    Project,
    RenderArtifact,
    RenderRequest,
)
from clipah.renders.models import RenderPreset
from clipah.workspaces.models import WorkspaceAccess

RENDER_DOWNLOAD_TTL = timedelta(minutes=5)


class RenderNotFoundError(Exception):
    """The Edit or the export does not exist, or the caller may not know that it does."""


@dataclass(frozen=True, slots=True)
class RenderArtifactSummary:
    """One finished export, as it is safe to show a member of its Workspace."""

    render_id: UUID
    edit_id: UUID
    revision_id: UUID
    preset: RenderPreset
    duration_ms: int
    size_bytes: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RenderAdmission:
    """The answer to one export request: an existing file, or the Job making one."""

    artifact: RenderArtifactSummary | None
    job_id: UUID | None


@dataclass(frozen=True, slots=True)
class RenderTarget:
    """What one admitted render Job was created to produce."""

    job_id: UUID
    workspace_id: UUID
    project_id: UUID
    edit_id: UUID
    revision_id: UUID
    preset: RenderPreset
    composition_hash: bytes
    composition: dict[str, object]


def request_render(
    session: Session,
    *,
    policy: AdmissionPolicy,
    access: WorkspaceAccess,
    edit_id: UUID,
    preset: RenderPreset,
    idempotency_key: str,
    now: datetime,
) -> RenderAdmission:
    """Reuse a healthy export of this exact composition, or admit the Job that makes one."""
    revision = _current_revision(session, workspace_id=access.workspace_id, edit_id=edit_id)
    if revision is None:
        raise RenderNotFoundError(str(edit_id))
    existing = _healthy_artifact(
        session,
        workspace_id=access.workspace_id,
        composition_hash=revision.composition_hash,
        preset=preset,
    )
    if existing is not None:
        return RenderAdmission(artifact=existing, job_id=None)
    job = admit_job(
        session,
        policy=policy,
        workspace_id=access.workspace_id,
        project_id=revision.project_id,
        user_id=access.user_id,
        kind=JobKind.RENDER,
        idempotency_key=idempotency_key,
        now=now,
    )
    session.add(
        RenderRequest(
            workspace_id=access.workspace_id,
            clip_edit_revision_id=revision.revision_id,
            job_id=job.id,
            preset=preset.value,
            composition_hash=revision.composition_hash,
            requested_by_user_id=access.user_id,
        )
    )
    session.flush()
    return RenderAdmission(artifact=None, job_id=job.id)


def get_render(
    session: Session, *, access: WorkspaceAccess, render_id: UUID
) -> RenderArtifactSummary:
    """Read one export, hiding another Workspace's export behind the same absence."""
    summary = _artifact_by_id(session, workspace_id=access.workspace_id, render_id=render_id)
    if summary is None:
        raise RenderNotFoundError(str(render_id))
    return summary


def render_download(
    session: Session,
    store: ObjectStore,
    *,
    access: WorkspaceAccess,
    render_id: UUID,
) -> SignedUrl:
    """Sign one five-minute capability for an export this member may read."""
    key = session.scalar(
        select(RenderArtifact.storage_key).where(
            RenderArtifact.workspace_id == access.workspace_id,
            RenderArtifact.id == render_id,
        )
    )
    if key is None:
        raise RenderNotFoundError(str(render_id))
    return store.sign_download(key=key, expires_in=RENDER_DOWNLOAD_TTL)


def render_target(session: Session, *, workspace_id: UUID, job_id: UUID) -> RenderTarget | None:
    """Read back what one admitted render Job was created to produce."""
    row = session.execute(
        select(
            RenderRequest,
            ClipEditRevision,
            ClipEdit.id,
            ClipCandidate.project_id,
        )
        .join(
            ClipEditRevision,
            (ClipEditRevision.workspace_id == RenderRequest.workspace_id)
            & (ClipEditRevision.id == RenderRequest.clip_edit_revision_id),
        )
        .join(
            ClipEdit,
            (ClipEdit.workspace_id == ClipEditRevision.workspace_id)
            & (ClipEdit.id == ClipEditRevision.clip_edit_id),
        )
        .join(
            ClipCandidate,
            (ClipCandidate.workspace_id == ClipEdit.workspace_id)
            & (ClipCandidate.id == ClipEdit.candidate_id),
        )
        .where(RenderRequest.workspace_id == workspace_id, RenderRequest.job_id == job_id)
    ).first()
    if row is None:
        return None
    request, revision, edit_id, project_id = row
    return RenderTarget(
        job_id=job_id,
        workspace_id=workspace_id,
        project_id=project_id,
        edit_id=edit_id,
        revision_id=revision.id,
        preset=RenderPreset(request.preset),
        composition_hash=request.composition_hash,
        composition=dict(revision.composition),
    )


def healthy_artifact(
    session: Session, *, workspace_id: UUID, composition_hash: bytes, preset: RenderPreset
) -> RenderArtifactSummary | None:
    """Find an export of exactly this composition at exactly this preset."""
    return _healthy_artifact(
        session,
        workspace_id=workspace_id,
        composition_hash=composition_hash,
        preset=preset,
    )


@dataclass(frozen=True, slots=True)
class _CurrentRevision:
    """The Revision an export would be made from, and the Project it belongs to."""

    revision_id: UUID
    project_id: UUID
    composition_hash: bytes


def _current_revision(
    session: Session, *, workspace_id: UUID, edit_id: UUID
) -> _CurrentRevision | None:
    """Read the Revision one Edit currently points at, inside one active Project."""
    row = session.execute(
        select(ClipEditRevision.id, ClipCandidate.project_id, ClipEditRevision.composition_hash)
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
        .join(
            Project,
            (Project.workspace_id == ClipCandidate.workspace_id)
            & (Project.id == ClipCandidate.project_id),
        )
        .where(
            ClipEditRevision.workspace_id == workspace_id,
            ClipEdit.id == edit_id,
            Project.archived_at.is_(None),
        )
    ).first()
    if row is None:
        return None
    revision_id, project_id, composition_hash = row
    return _CurrentRevision(
        revision_id=revision_id, project_id=project_id, composition_hash=composition_hash
    )


def _healthy_artifact(
    session: Session, *, workspace_id: UUID, composition_hash: bytes, preset: RenderPreset
) -> RenderArtifactSummary | None:
    """Read one stored export, ignoring a row that carries no bytes."""
    row = session.execute(
        _artifact_query().where(
            RenderArtifact.workspace_id == workspace_id,
            RenderArtifact.composition_hash == composition_hash,
            RenderArtifact.preset == preset.value,
            RenderArtifact.size_bytes > 0,
        )
    ).first()
    return None if row is None else _summary(row)


def _artifact_by_id(
    session: Session, *, workspace_id: UUID, render_id: UUID
) -> RenderArtifactSummary | None:
    """Read one stored export by its own identifier."""
    row = session.execute(
        _artifact_query().where(
            RenderArtifact.workspace_id == workspace_id, RenderArtifact.id == render_id
        )
    ).first()
    return None if row is None else _summary(row)


def _artifact_query() -> Select[tuple[RenderArtifact, UUID]]:
    """The one join every export read shares, so they all answer identically."""
    return select(RenderArtifact, ClipEditRevision.clip_edit_id).join(
        ClipEditRevision,
        (ClipEditRevision.workspace_id == RenderArtifact.workspace_id)
        & (ClipEditRevision.id == RenderArtifact.clip_edit_revision_id),
    )


def _summary(row: Row[tuple[RenderArtifact, UUID]]) -> RenderArtifactSummary:
    """Render one stored export as the value the API and the worker both read."""
    artifact, edit_id = row
    return RenderArtifactSummary(
        render_id=artifact.id,
        edit_id=edit_id,
        revision_id=artifact.clip_edit_revision_id,
        preset=RenderPreset(artifact.preset),
        duration_ms=artifact.duration_ms,
        size_bytes=artifact.size_bytes,
        created_at=artifact.created_at,
    )
