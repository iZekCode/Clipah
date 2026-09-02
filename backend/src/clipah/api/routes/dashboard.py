"""HTTP adapter for the one Workspace overview read the dashboard renders from."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from clipah.api.dependencies import (
    CurrentWorkspace,
    DatabaseSession,
    auth_components_for,
    require_workspace,
    settings_for,
)
from clipah.dashboard.schemas import (
    ActiveJobSummary,
    DashboardSummary,
    TopCandidateSummary,
    UsageSummary,
)
from clipah.dashboard.use_cases import summarize_dashboard
from clipah.jobs.admission import admission_policy
from clipah.models import JobKind, JobStatus, QuotaResource, WorkspaceRole
from clipah.projects.schemas import ProjectSummary
from clipah.workspaces.models import WorkspaceAction, WorkspaceSummary

router = APIRouter(prefix="/api/v1", tags=["dashboard"])

ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_READ))
]


class DashboardWorkspaceResponse(BaseModel):
    """The Workspace the overview belongs to, and the caller's standing in it."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    name: str
    role: WorkspaceRole


class DashboardProjectResponse(BaseModel):
    """One recently touched Project as the overview lists it."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    name: str
    status: str
    source_kind: str = Field(alias="sourceKind")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    @field_serializer("created_at", "updated_at")
    def serialize_timestamp(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return value.isoformat()


class DashboardProjectsResponse(BaseModel):
    """How much work this Workspace holds, and the part of it worth showing first."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    active_count: int = Field(alias="activeCount")
    recent: tuple[DashboardProjectResponse, ...]


class DashboardJobResponse(BaseModel):
    """One unfinished Job the job center has to keep announcing."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    project_id: UUID = Field(alias="projectId")
    kind: JobKind
    status: JobStatus
    stage: str
    progress: float
    updated_at: datetime = Field(alias="updatedAt")

    @field_serializer("updated_at")
    def serialize_updated_at(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return value.isoformat()


class DashboardJobsResponse(BaseModel):
    """The Jobs this Workspace still has in flight."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    active: tuple[DashboardJobResponse, ...]


class DashboardUsageResponse(BaseModel):
    """What one metered resource has cost against its monthly budget."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    resource: QuotaResource
    consumed: float
    limit: int


class DashboardCandidateResponse(BaseModel):
    """One highly ranked Clip Candidate waiting for review."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    project_id: UUID = Field(alias="projectId")
    project_name: str = Field(alias="projectName")
    rank: int
    score: float
    hook: str
    category: str
    start_ms: int = Field(alias="startMs")
    end_ms: int = Field(alias="endMs")


class DashboardSummaryResponse(BaseModel):
    """The whole overview screen, answered by one read."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    workspace: DashboardWorkspaceResponse
    projects: DashboardProjectsResponse
    jobs: DashboardJobsResponse
    usage: tuple[DashboardUsageResponse, ...]
    top_candidates: tuple[DashboardCandidateResponse, ...] = Field(alias="topCandidates")


@router.get("/dashboard/summary", response_model=DashboardSummaryResponse)
def show(
    request: Request, session: DatabaseSession, workspace: ReadableWorkspace
) -> DashboardSummaryResponse:
    """Summarize one Workspace for a member who has already proven they belong to it."""
    summary = summarize_dashboard(
        session,
        access=workspace.access,
        policy=admission_policy(settings_for(request)),
        now=auth_components_for(request).now(),
    )
    return _summary_body(summary)


def _summary_body(summary: DashboardSummary) -> DashboardSummaryResponse:
    """Render only the overview facts an authorized Workspace member may see."""
    return DashboardSummaryResponse(
        workspace=_workspace_body(summary.workspace),
        projects=DashboardProjectsResponse(
            activeCount=summary.active_project_count,
            recent=tuple(_project_body(project) for project in summary.recent_projects),
        ),
        jobs=DashboardJobsResponse(active=tuple(_job_body(job) for job in summary.active_jobs)),
        usage=tuple(_usage_body(usage) for usage in summary.usage),
        topCandidates=tuple(_candidate_body(candidate) for candidate in summary.top_candidates),
    )


def _workspace_body(workspace: WorkspaceSummary) -> DashboardWorkspaceResponse:
    """Name the Workspace and the caller's role without repeating its settings."""
    return DashboardWorkspaceResponse(
        id=workspace.workspace_id, name=workspace.name, role=workspace.role
    )


def _project_body(project: ProjectSummary) -> DashboardProjectResponse:
    """Render one Project entry of the overview."""
    return DashboardProjectResponse(
        id=project.project_id,
        name=project.name,
        status=project.status,
        sourceKind=project.source_kind,
        createdAt=project.created_at,
        updatedAt=project.updated_at,
    )


def _job_body(job: ActiveJobSummary) -> DashboardJobResponse:
    """Render one in-flight Job of the overview."""
    return DashboardJobResponse(
        id=job.job_id,
        projectId=job.project_id,
        kind=job.kind,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        updatedAt=job.updated_at,
    )


def _usage_body(usage: UsageSummary) -> DashboardUsageResponse:
    """Render one metered resource of the overview."""
    return DashboardUsageResponse(
        resource=usage.resource, consumed=float(usage.consumed), limit=usage.limit
    )


def _candidate_body(candidate: TopCandidateSummary) -> DashboardCandidateResponse:
    """Render one reviewable Clip Candidate of the overview."""
    return DashboardCandidateResponse(
        id=candidate.candidate_id,
        projectId=candidate.project_id,
        projectName=candidate.project_name,
        rank=candidate.rank,
        score=candidate.score,
        hook=candidate.hook,
        category=candidate.category,
        startMs=candidate.start_ms,
        endMs=candidate.end_ms,
    )
