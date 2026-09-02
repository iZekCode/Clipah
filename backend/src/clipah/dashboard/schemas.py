"""Dashboard overview values independent from SQLAlchemy and HTTP."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from clipah.models import JobKind, JobStatus, QuotaResource
from clipah.projects.schemas import ProjectSummary
from clipah.workspaces.models import WorkspaceSummary


@dataclass(frozen=True, slots=True)
class ActiveJobSummary:
    """One unfinished Job as the overview's job center needs to announce it."""

    job_id: UUID
    project_id: UUID
    kind: JobKind
    status: JobStatus
    stage: str
    progress: float
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class UsageSummary:
    """What one metered resource has cost this Workspace inside the current period."""

    resource: QuotaResource
    consumed: Decimal
    limit: int


@dataclass(frozen=True, slots=True)
class TopCandidateSummary:
    """One highly ranked Clip Candidate, named by the Project it was found in."""

    candidate_id: UUID
    project_id: UUID
    project_name: str
    rank: int
    score: float
    hook: str
    category: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    """Everything the Workspace overview screen renders, read in one transaction."""

    workspace: WorkspaceSummary
    active_project_count: int
    recent_projects: tuple[ProjectSummary, ...]
    active_jobs: tuple[ActiveJobSummary, ...]
    usage: tuple[UsageSummary, ...]
    top_candidates: tuple[TopCandidateSummary, ...]
