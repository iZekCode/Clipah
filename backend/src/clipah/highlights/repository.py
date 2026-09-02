"""Private persistence for analysis admission and review-safe Clip Candidate reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from clipah.jobs.models import JobSnapshot
from clipah.jobs.repository import snapshot_of
from clipah.models import ClipCandidate, Job, Project, ProjectStatus, Transcript

_ANALYSIS_IDEMPOTENCY_LOCK_NAMESPACE = 0x0C11_9A16


@dataclass(frozen=True, slots=True)
class AnalysisProjectState:
    """The locked Project facts that decide whether analysis may start."""

    project_id: UUID
    status: ProjectStatus
    transcript_count: int


@dataclass(frozen=True, slots=True)
class CandidateBoundary:
    """A stable place immediately after one globally ranked candidate."""

    rank: int
    candidate_id: UUID


@dataclass(frozen=True, slots=True)
class CandidateSummary:
    """The review evidence safe to carry outside SQLAlchemy."""

    candidate_id: UUID
    project_id: UUID
    rank: int
    score: float
    hook: str
    payoff: str
    reason: str
    category: str
    tags: tuple[str, ...]
    start_ms: int
    end_ms: int
    transcript_excerpt: str
    context_dependencies: tuple[str, ...]
    score_breakdown: dict[str, Any]
    context_warnings: tuple[str, ...]
    visual_opportunities: tuple[str, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CandidatePage:
    """One ranked review page and the boundary for the next page."""

    candidates: tuple[CandidateSummary, ...]
    next_boundary: CandidateBoundary | None


class HighlightRepository:
    """Keep analysis and candidate ORM details behind one tenant-scoped boundary."""

    def __init__(self, session: Session) -> None:
        """Bind persistence to the request transaction holding verified RLS context."""
        self._session = session

    def lock_idempotency(self, *, workspace_id: UUID, key: str) -> None:
        """Serialize one Workspace analysis key before checking or creating its Job."""
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:subject))"),
            {
                "namespace": _ANALYSIS_IDEMPOTENCY_LOCK_NAMESPACE,
                "subject": f"{workspace_id}:{key}",
            },
        )

    def job_by_key(self, *, workspace_id: UUID, key: str) -> JobSnapshot | None:
        """Return the Job already bound to one Workspace idempotency key."""
        job = self._session.scalar(
            select(Job).where(Job.workspace_id == workspace_id, Job.idempotency_key == key)
        )
        return None if job is None else snapshot_of(job)

    def lock_analysis_project(
        self, *, workspace_id: UUID, project_id: UUID
    ) -> AnalysisProjectState | None:
        """Lock one active Project and count its canonical Transcript rows."""
        project = self._session.scalar(
            select(Project)
            .where(
                Project.workspace_id == workspace_id,
                Project.id == project_id,
                Project.archived_at.is_(None),
            )
            .with_for_update()
        )
        if project is None:
            return None
        transcript_count = self._session.scalar(
            select(func.count())
            .select_from(Transcript)
            .where(
                Transcript.workspace_id == workspace_id,
                Transcript.project_id == project_id,
            )
        )
        return AnalysisProjectState(
            project_id=project.id,
            status=project.status,
            transcript_count=int(transcript_count or 0),
        )

    def mark_analyzing(self, *, workspace_id: UUID, project_id: UUID) -> None:
        """Move the locked transcribed Project into its durable analysis state."""
        project = self._session.scalar(
            select(Project).where(
                Project.workspace_id == workspace_id,
                Project.id == project_id,
            )
        )
        if project is None:
            raise RuntimeError("locked Project disappeared")
        project.status = ProjectStatus.ANALYZING
        self._session.flush()

    def project_is_visible(self, *, workspace_id: UUID, project_id: UUID) -> bool:
        """Report whether one active Project belongs to the selected Workspace."""
        return (
            self._session.scalar(
                select(Project.id).where(
                    Project.workspace_id == workspace_id,
                    Project.id == project_id,
                    Project.status == ProjectStatus.READY,
                    Project.archived_at.is_(None),
                )
            )
            is not None
        )

    def candidate_page(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        limit: int,
        after: CandidateBoundary | None,
    ) -> CandidatePage:
        """Read exposed candidates in stable global-rank order."""
        conditions = [
            ClipCandidate.workspace_id == workspace_id,
            ClipCandidate.project_id == project_id,
            ClipCandidate.model_metadata["exposed"].as_boolean().is_(True),
        ]
        if after is not None:
            conditions.append(
                or_(
                    ClipCandidate.rank > after.rank,
                    and_(
                        ClipCandidate.rank == after.rank,
                        ClipCandidate.id > after.candidate_id,
                    ),
                )
            )
        rows = list(
            self._session.scalars(
                select(ClipCandidate)
                .where(*conditions)
                .order_by(ClipCandidate.rank, ClipCandidate.id)
                .limit(limit + 1)
            )
        )
        visible = rows[:limit]
        next_boundary = None
        if len(rows) > limit:
            last = visible[-1]
            next_boundary = CandidateBoundary(rank=last.rank, candidate_id=last.id)
        return CandidatePage(
            candidates=tuple(_candidate_summary(row) for row in visible),
            next_boundary=next_boundary,
        )

    def candidate_by_id(
        self, *, workspace_id: UUID, project_id: UUID, candidate_id: UUID
    ) -> CandidateSummary | None:
        """Read one exposed candidate only through its owning Workspace and Project."""
        row = self._session.scalar(
            select(ClipCandidate).where(
                ClipCandidate.workspace_id == workspace_id,
                ClipCandidate.project_id == project_id,
                ClipCandidate.id == candidate_id,
                ClipCandidate.model_metadata["exposed"].as_boolean().is_(True),
            )
        )
        return None if row is None else _candidate_summary(row)


def _candidate_summary(row: ClipCandidate) -> CandidateSummary:
    """Detach only public review evidence from one persisted candidate."""
    opportunities = tuple(item for item in row.visual_opportunities if isinstance(item, str))
    return CandidateSummary(
        candidate_id=row.id,
        project_id=row.project_id,
        rank=row.rank,
        score=float(row.score),
        hook=row.hook,
        payoff=row.payoff,
        reason=row.reason,
        category=row.category,
        tags=tuple(row.tags),
        start_ms=row.start_ms,
        end_ms=row.end_ms,
        transcript_excerpt=row.transcript_excerpt,
        context_dependencies=tuple(row.context_dependencies),
        score_breakdown=dict(row.score_breakdown),
        context_warnings=tuple(row.context_warnings),
        visual_opportunities=opportunities,
        created_at=row.created_at,
    )
