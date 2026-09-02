"""Workspace-scoped use cases for analysis admission and Clip Candidate review."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from clipah.highlights.repository import (
    CandidateBoundary,
    CandidatePage,
    CandidateSummary,
    HighlightRepository,
)
from clipah.jobs.admission import AdmissionPolicy
from clipah.jobs.models import JobSnapshot
from clipah.jobs.use_cases import create_job
from clipah.models import JobKind, ProjectStatus
from clipah.workspaces.models import WorkspaceAccess


class AnalysisProjectNotFoundError(Exception):
    """The Project is absent, archived, or belongs to another Workspace."""


class AnalysisConflictError(Exception):
    """The Project state or idempotency binding cannot start this analysis."""


class CandidateNotFoundError(Exception):
    """The Project or Clip Candidate is not visible inside this Workspace."""


def start_analysis(
    session: Session,
    *,
    policy: AdmissionPolicy,
    access: WorkspaceAccess,
    project_id: UUID,
    idempotency_key: str,
    now: datetime,
) -> JobSnapshot:
    """Create one durable ANALYZE Job after proving transcript and Project readiness."""
    repository = HighlightRepository(session)
    repository.lock_idempotency(workspace_id=access.workspace_id, key=idempotency_key)
    existing = repository.job_by_key(workspace_id=access.workspace_id, key=idempotency_key)
    if existing is not None:
        if existing.kind is not JobKind.ANALYZE or existing.project_id != project_id:
            raise AnalysisConflictError(idempotency_key)
        return existing

    project = repository.lock_analysis_project(
        workspace_id=access.workspace_id,
        project_id=project_id,
    )
    if project is None:
        raise AnalysisProjectNotFoundError(str(project_id))
    if project.status is not ProjectStatus.TRANSCRIBING or project.transcript_count != 1:
        raise AnalysisConflictError(project.status.value)

    job = create_job(
        session,
        policy=policy,
        access=access,
        project_id=project_id,
        kind=JobKind.ANALYZE,
        idempotency_key=idempotency_key,
        now=now,
    )
    repository.mark_analyzing(workspace_id=access.workspace_id, project_id=project_id)
    return job


def list_candidates(
    repository: HighlightRepository,
    *,
    access: WorkspaceAccess,
    project_id: UUID,
    limit: int,
    after: CandidateBoundary | None,
) -> CandidatePage:
    """Return one exposed ranked page without admitting hidden or foreign rows."""
    if not repository.project_is_visible(workspace_id=access.workspace_id, project_id=project_id):
        raise CandidateNotFoundError(str(project_id))
    return repository.candidate_page(
        workspace_id=access.workspace_id,
        project_id=project_id,
        limit=limit,
        after=after,
    )


def get_candidate(
    repository: HighlightRepository,
    *,
    access: WorkspaceAccess,
    project_id: UUID,
    candidate_id: UUID,
) -> CandidateSummary:
    """Return one exposed candidate through both tenant and Project ownership."""
    if not repository.project_is_visible(workspace_id=access.workspace_id, project_id=project_id):
        raise CandidateNotFoundError(str(project_id))
    candidate = repository.candidate_by_id(
        workspace_id=access.workspace_id,
        project_id=project_id,
        candidate_id=candidate_id,
    )
    if candidate is None:
        raise CandidateNotFoundError(str(candidate_id))
    return candidate
