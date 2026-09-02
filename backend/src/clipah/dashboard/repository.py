"""Private tenant-scoped persistence behind the Workspace overview read."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from clipah.dashboard.schemas import ActiveJobSummary, TopCandidateSummary
from clipah.jobs.admission import ACTIVE_JOB_STATUSES
from clipah.models import ClipCandidate, Job, Project, ProjectStatus
from clipah.projects.schemas import ProjectSummary


class DashboardRepository:
    """Answer every overview question from one Workspace's visible rows only."""

    def __init__(self, session: Session) -> None:
        """Bind the overview reads to the request transaction that proved membership."""
        self._session = session

    def active_project_count(self, *, workspace_id: UUID) -> int:
        """Count the Projects this Workspace has not soft-deleted."""
        total = self._session.scalar(
            select(func.count())
            .select_from(Project)
            .where(Project.workspace_id == workspace_id, Project.archived_at.is_(None))
        )
        return int(total or 0)

    def recent_projects(self, *, workspace_id: UUID, limit: int) -> tuple[ProjectSummary, ...]:
        """Return the Projects touched most recently, newest first."""
        projects = self._session.scalars(
            select(Project)
            .where(Project.workspace_id == workspace_id, Project.archived_at.is_(None))
            .order_by(Project.updated_at.desc(), Project.id)
            .limit(limit)
        )
        return tuple(
            ProjectSummary(
                project_id=project.id,
                workspace_id=project.workspace_id,
                name=project.name,
                status=project.status.value,
                source_kind=project.source_kind.value,
                created_at=project.created_at,
                updated_at=project.updated_at,
            )
            for project in projects
        )

    def active_jobs(self, *, workspace_id: UUID, limit: int) -> tuple[ActiveJobSummary, ...]:
        """Return the Jobs that have not reached a terminal state, newest first."""
        jobs = self._session.scalars(
            select(Job)
            .where(Job.workspace_id == workspace_id, Job.status.in_(ACTIVE_JOB_STATUSES))
            .order_by(Job.created_at.desc(), Job.id)
            .limit(limit)
        )
        return tuple(
            ActiveJobSummary(
                job_id=job.id,
                project_id=job.project_id,
                kind=job.kind,
                status=job.status,
                stage=job.stage,
                progress=job.progress,
                updated_at=job.updated_at,
            )
            for job in jobs
        )

    def top_candidates(self, *, workspace_id: UUID, limit: int) -> tuple[TopCandidateSummary, ...]:
        """Return the best exposed Clip Candidates from Projects a member may review."""
        rows = self._session.execute(
            select(ClipCandidate, Project.name)
            .join(
                Project,
                (Project.workspace_id == ClipCandidate.workspace_id)
                & (Project.id == ClipCandidate.project_id),
            )
            .where(
                ClipCandidate.workspace_id == workspace_id,
                ClipCandidate.model_metadata["exposed"].as_boolean().is_(True),
                Project.archived_at.is_(None),
                Project.status == ProjectStatus.READY,
            )
            .order_by(ClipCandidate.rank, ClipCandidate.id)
            .limit(limit)
        )
        return tuple(
            TopCandidateSummary(
                candidate_id=candidate.id,
                project_id=candidate.project_id,
                project_name=project_name,
                rank=candidate.rank,
                score=candidate.score,
                hook=candidate.hook,
                category=candidate.category,
                start_ms=candidate.start_ms,
                end_ms=candidate.end_ms,
            )
            for candidate, project_name in rows
        )
