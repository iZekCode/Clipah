"""Workspace-scoped use cases for B-roll plan admission and suggestion review.

`plan.md` does not name this module for Task 28, but `AGENTS.md` requires domain rules to
live in a `use_cases.py` and HTTP concerns to stay in `api/routes/`, so the admission rules
live here rather than inside the route that calls them.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from clipah.broll.models import BrollCoverage
from clipah.broll.repository import BrollRepository, SuggestionSummary
from clipah.jobs.admission import AdmissionPolicy
from clipah.jobs.models import JobSnapshot
from clipah.jobs.use_cases import create_job
from clipah.models import JobKind
from clipah.workspaces.models import WorkspaceAccess


class BrollTargetNotFoundError(Exception):
    """The Project or Clip Candidate is not visible inside this Workspace."""


class BrollPlanConflictError(Exception):
    """The idempotency key is already bound to different planning work."""


def start_broll_plan(
    session: Session,
    *,
    policy: AdmissionPolicy,
    access: WorkspaceAccess,
    project_id: UUID,
    candidate_id: UUID,
    coverage: BrollCoverage,
    idempotency_key: str,
    now: datetime,
) -> JobSnapshot:
    """Create one durable BROLL_PLAN Job after proving the clip is reviewable."""
    repository = BrollRepository(session)
    repository.lock_idempotency(workspace_id=access.workspace_id, key=idempotency_key)
    existing = repository.job_by_key(workspace_id=access.workspace_id, key=idempotency_key)
    if existing is not None:
        if existing.kind is not JobKind.BROLL_PLAN or existing.project_id != project_id:
            raise BrollPlanConflictError(idempotency_key)
        return existing

    target = repository.lock_plan_target(
        workspace_id=access.workspace_id,
        project_id=project_id,
        candidate_id=candidate_id,
    )
    if target is None:
        raise BrollTargetNotFoundError(str(candidate_id))

    job = create_job(
        session,
        policy=policy,
        access=access,
        project_id=project_id,
        kind=JobKind.BROLL_PLAN,
        idempotency_key=idempotency_key,
        now=now,
    )
    repository.record_plan_request(
        workspace_id=access.workspace_id,
        candidate_id=target.candidate_id,
        job_id=job.job_id,
        coverage=coverage,
        requested_by_user_id=access.user_id,
    )
    return job


def list_suggestions(
    repository: BrollRepository,
    *,
    access: WorkspaceAccess,
    project_id: UUID,
    candidate_id: UUID,
) -> tuple[SuggestionSummary, ...]:
    """Return one clip's proposals, refusing a clip this member has no standing on."""
    if not repository.candidate_is_visible(
        workspace_id=access.workspace_id,
        project_id=project_id,
        candidate_id=candidate_id,
    ):
        raise BrollTargetNotFoundError(str(candidate_id))
    return repository.suggestions_for_candidate(
        workspace_id=access.workspace_id,
        candidate_id=candidate_id,
    )
