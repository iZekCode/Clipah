"""Where one clip's cover picture stands, and asking for it to be drawn.

A cover belongs to a Revision: the one a clip currently points at is the one a member
designed last and saved. Asking for its picture admits one CLIP_COVER Job for the Project,
which draws every cover still missing there; asking again while that Job runs, or once the
picture exists, admits nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.assets.storage import ObjectStore, SignedUrl
from clipah.editor.models import CompositionValidationError, parse_composition
from clipah.jobs.admission import AdmissionPolicy
from clipah.jobs.use_cases import create_job
from clipah.models import (
    Asset,
    AssetKind,
    ClipCandidate,
    ClipEdit,
    ClipEditRevision,
    Job,
    JobKind,
    JobStatus,
    Project,
)
from clipah.renders.cover import cover_asset_id
from clipah.workspaces.models import WorkspaceAccess

COVER_DOWNLOAD_TTL = timedelta(minutes=5)
_UNFINISHED = (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYING, JobStatus.CANCEL_REQUESTED)


class CoverStatus(StrEnum):
    """The five states a clip's cover picture can be in."""

    NONE = "none"
    MISSING = "missing"
    DRAWING = "drawing"
    FAILED = "failed"
    READY = "ready"


class CoverNotFoundError(Exception):
    """An Edit this member cannot see, which reads exactly like one that does not exist."""


class CoverNotDesignedError(Exception):
    """A cover picture was asked for a Revision that designs no cover."""


@dataclass(frozen=True, slots=True)
class CoverState:
    """One clip's cover as it stands for its current Revision."""

    status: CoverStatus
    revision: int
    project_id: UUID
    revision_id: UUID
    storage_key: str | None
    job_id: UUID | None
    error_code: str | None


def cover_state(session: Session, *, access: WorkspaceAccess, edit_id: UUID) -> CoverState:
    """Read the current Revision's cover: undesigned, missing, drawing, failed, or ready."""
    row = session.execute(
        select(
            ClipEditRevision.id,
            ClipEditRevision.revision,
            ClipEditRevision.composition,
            ClipEditRevision.created_at,
            ClipCandidate.project_id,
        )
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
            ClipEditRevision.workspace_id == access.workspace_id,
            ClipEdit.id == edit_id,
            Project.archived_at.is_(None),
        )
    ).first()
    if row is None:
        raise CoverNotFoundError(str(edit_id))
    revision_id, revision, document, saved_at, project_id = row
    base = CoverState(
        status=CoverStatus.NONE,
        revision=revision,
        project_id=project_id,
        revision_id=revision_id,
        storage_key=None,
        job_id=None,
        error_code=None,
    )
    try:
        designed = parse_composition(document).cover is not None
    except CompositionValidationError:
        designed = False
    if not designed:
        return base
    key = session.scalar(
        select(Asset.storage_key).where(
            Asset.workspace_id == access.workspace_id,
            Asset.project_id == project_id,
            Asset.kind == AssetKind.COVER,
            Asset.id == cover_asset_id(revision_id),
        )
    )
    if key is not None:
        return replace(base, status=CoverStatus.READY, storage_key=key)
    latest = session.execute(
        select(Job.id, Job.status, Job.error_code, Job.created_at)
        .where(
            Job.workspace_id == access.workspace_id,
            Job.project_id == project_id,
            Job.kind == JobKind.CLIP_COVER,
        )
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    ).first()
    if latest is None or latest.created_at < saved_at:
        return replace(base, status=CoverStatus.MISSING)
    if latest.status in _UNFINISHED:
        return replace(base, status=CoverStatus.DRAWING, job_id=latest.id)
    if latest.status is JobStatus.FAILED:
        return replace(
            base, status=CoverStatus.FAILED, job_id=latest.id, error_code=latest.error_code
        )
    # The Job finished after this Revision was saved but drew nothing for it; naming it
    # lets the next request be told apart from the one that already ran.
    return replace(base, status=CoverStatus.MISSING, job_id=latest.id)


def request_cover(
    session: Session,
    *,
    policy: AdmissionPolicy,
    access: WorkspaceAccess,
    edit_id: UUID,
    now: datetime,
) -> CoverState:
    """Admit the Job that draws this Revision's cover, unless it exists or is being drawn."""
    state = cover_state(session, access=access, edit_id=edit_id)
    if state.status is CoverStatus.NONE:
        raise CoverNotDesignedError(str(edit_id))
    if state.status in {CoverStatus.READY, CoverStatus.DRAWING}:
        return state
    key = f"cover:{state.revision_id}"
    if state.job_id is not None:
        key = f"{key}:after:{state.job_id}"
    snapshot = create_job(
        session,
        policy=policy,
        access=access,
        project_id=state.project_id,
        kind=JobKind.CLIP_COVER,
        idempotency_key=key,
        now=now,
    )
    return replace(state, status=CoverStatus.DRAWING, job_id=snapshot.job_id, error_code=None)


def sign_cover(store: ObjectStore, state: CoverState, *, download: bool) -> SignedUrl | None:
    """Sign a five-minute capability to show the cover, or to save it as a file."""
    if state.storage_key is None:
        return None
    return store.sign_download(
        key=state.storage_key,
        expires_in=COVER_DOWNLOAD_TTL,
        download_name=f"clipah-cover-{state.revision_id}.jpg" if download else None,
    )
