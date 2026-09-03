"""The conveyor belt that carries one Project from arrival to ranked candidates.

Every stage already knew how to do its own work; none of them knew what came next, so a
Project stopped wherever its last Job finished and no Project ever reached the
`transcribing` status that analysis admission requires. This module is the only place that
knows the order of the pipeline, and the only place that moves a Project between stages.

Advancement is idempotent by construction: the successor's idempotency key names the Job it
follows, so a completion delivered twice buys the Workspace one stage, not two.
"""

from __future__ import annotations

from datetime import datetime
from types import MappingProxyType
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.jobs.admission import AdmissionPolicy
from clipah.jobs.models import JobSnapshot
from clipah.jobs.use_cases import create_job
from clipah.models import JobKind, Project, ProjectStatus
from clipah.workspaces.models import WorkspaceAccess

#: What each stage hands its Project to when it succeeds. Analysis ends the belt.
NEXT_STAGE = MappingProxyType(
    {
        JobKind.SOURCE_IMPORT: JobKind.INGEST,
        JobKind.INGEST: JobKind.TRANSCRIBE,
        JobKind.TRANSCRIBE: JobKind.ANALYZE,
    }
)

#: The status a Project is in while one stage is working on it.
STAGE_STATUS = MappingProxyType(
    {
        JobKind.INGEST: ProjectStatus.INGESTING,
        JobKind.TRANSCRIBE: ProjectStatus.TRANSCRIBING,
        JobKind.ANALYZE: ProjectStatus.ANALYZING,
    }
)

#: Statuses no completed stage may move a Project out of.
_SETTLED = frozenset({ProjectStatus.FAILED, ProjectStatus.ARCHIVED})


def pipeline_key(*, after_job_id: UUID, kind: JobKind) -> str:
    """Name one successor by the Job it follows, so a replay cannot duplicate it."""
    return f"pipeline:{after_job_id}:{kind.value}"


def start_stage(
    session: Session,
    *,
    policy: AdmissionPolicy,
    access: WorkspaceAccess,
    project_id: UUID,
    kind: JobKind,
    idempotency_key: str,
    now: datetime,
) -> JobSnapshot | None:
    """Admit one pipeline stage and move its Project into the status that stage means.

    Returns nothing when the Project is no longer somewhere work may be added to: a
    deleted Project must not be revived, and one that has already failed stays failed
    however late another stage reports success.
    """
    project = _live_project(session, workspace_id=access.workspace_id, project_id=project_id)
    if project is None:
        return None

    job = create_job(
        session,
        policy=policy,
        access=access,
        project_id=project_id,
        kind=kind,
        idempotency_key=idempotency_key,
        now=now,
    )
    status = STAGE_STATUS.get(kind)
    if status is not None:
        project.status = status
    return job


def advance_after(
    session: Session,
    *,
    policy: AdmissionPolicy,
    access: WorkspaceAccess,
    project_id: UUID,
    completed_kind: JobKind,
    completed_job_id: UUID,
    now: datetime,
) -> JobSnapshot | None:
    """Start whatever follows one finished stage, or nothing at the end of the belt."""
    following = NEXT_STAGE.get(completed_kind)
    if following is None:
        return None
    return start_stage(
        session,
        policy=policy,
        access=access,
        project_id=project_id,
        kind=following,
        idempotency_key=pipeline_key(after_job_id=completed_job_id, kind=following),
        now=now,
    )


def _live_project(session: Session, *, workspace_id: UUID, project_id: UUID) -> Project | None:
    """Lock one Project the pipeline may still move, or report that there is none."""
    project = session.scalar(
        select(Project)
        .where(
            Project.workspace_id == workspace_id,
            Project.id == project_id,
            Project.archived_at.is_(None),
        )
        .with_for_update()
    )
    if project is None or project.status in _SETTLED:
        return None
    return project
