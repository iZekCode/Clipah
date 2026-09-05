"""Private persistence for B-roll plan admission and review-safe suggestion reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from clipah.broll.models import (
    BrollCoverage,
    BrollSourceType,
    BrollSuggestionStatus,
    CandidateSpan,
)
from clipah.jobs.models import JobSnapshot
from clipah.jobs.repository import snapshot_of
from clipah.models import (
    BrollPlanRequest,
    BrollSuggestion,
    ClipCandidate,
    Job,
    Project,
    ProjectStatus,
    Transcript,
)

_PLAN_IDEMPOTENCY_LOCK_NAMESPACE = 0x0B_7011_01


@dataclass(frozen=True, slots=True)
class PlanTarget:
    """The locked clip a planning Job would cover, and the Project that owns it."""

    project_id: UUID
    candidate_id: UUID
    span: CandidateSpan


@dataclass(frozen=True, slots=True)
class PlanRequest:
    """The clip and coverage one admitted planning Job was created to cover."""

    candidate_id: UUID
    coverage: BrollCoverage


@dataclass(frozen=True, slots=True)
class SuggestionSummary:
    """The proposal evidence safe to carry outside SQLAlchemy."""

    suggestion_id: UUID
    project_id: UUID
    candidate_id: UUID
    coverage: BrollCoverage
    planner_version: str
    beat_start_word_id: str
    beat_end_word_id: str
    start_ms: int
    end_ms: int
    visual_intent: dict[str, Any]
    search_terms: dict[str, Any]
    exclusions: tuple[str, ...]
    status: BrollSuggestionStatus
    placement_reason: str
    source_type: BrollSourceType | None
    relevance_score: float | None
    created_at: datetime
    decided_at: datetime | None


class BrollRepository:
    """Keep suggestion ORM details behind one tenant-scoped boundary."""

    def __init__(self, session: Session) -> None:
        """Bind persistence to the transaction holding verified RLS context."""
        self._session = session

    def lock_idempotency(self, *, workspace_id: UUID, key: str) -> None:
        """Serialize one Workspace planning key before checking or creating its Job."""
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:subject))"),
            {
                "namespace": _PLAN_IDEMPOTENCY_LOCK_NAMESPACE,
                "subject": f"{workspace_id}:{key}",
            },
        )

    def job_by_key(self, *, workspace_id: UUID, key: str) -> JobSnapshot | None:
        """Return the Job already bound to one Workspace idempotency key."""
        job = self._session.scalar(
            select(Job).where(Job.workspace_id == workspace_id, Job.idempotency_key == key)
        )
        return None if job is None else snapshot_of(job)

    def lock_plan_target(
        self, *, workspace_id: UUID, project_id: UUID, candidate_id: UUID
    ) -> PlanTarget | None:
        """Lock one exposed candidate of one ready, active Project in this Workspace."""
        project = self._session.scalar(
            select(Project)
            .where(
                Project.workspace_id == workspace_id,
                Project.id == project_id,
                Project.status == ProjectStatus.READY,
                Project.archived_at.is_(None),
            )
            .with_for_update()
        )
        if project is None:
            return None
        candidate = self._session.scalar(
            select(ClipCandidate).where(
                ClipCandidate.workspace_id == workspace_id,
                ClipCandidate.project_id == project_id,
                ClipCandidate.id == candidate_id,
                ClipCandidate.model_metadata["exposed"].as_boolean().is_(True),
            )
        )
        if candidate is None:
            return None
        return PlanTarget(
            project_id=project.id,
            candidate_id=candidate.id,
            span=CandidateSpan(
                start_word_id=candidate.start_word_id,
                end_word_id=candidate.end_word_id,
                start_ms=candidate.start_ms,
                end_ms=candidate.end_ms,
            ),
        )

    def record_plan_request(
        self,
        *,
        workspace_id: UUID,
        candidate_id: UUID,
        job_id: UUID,
        coverage: BrollCoverage,
        requested_by_user_id: UUID,
    ) -> None:
        """Write what this planning Job was admitted to cover, for the worker to read."""
        self._session.add(
            BrollPlanRequest(
                workspace_id=workspace_id,
                candidate_id=candidate_id,
                job_id=job_id,
                coverage=coverage,
                requested_by_user_id=requested_by_user_id,
            )
        )
        self._session.flush()

    def plan_target_for_job(
        self, *, workspace_id: UUID, job_id: UUID
    ) -> tuple[PlanRequest, UUID, CandidateSpan, Transcript] | None:
        """Read one planning Job's request, its candidate, and that clip's Transcript.

        All three are read together because they cannot disagree. The composite foreign
        keys mean a stored request always names a candidate of this same Workspace, and
        that candidate always names a Transcript of the same Workspace, so this join
        either answers completely or not at all — there is exactly one way to have no
        target, which is no request row this Workspace can see.
        """
        row = self._session.execute(
            select(BrollPlanRequest, ClipCandidate, Transcript)
            .join(
                ClipCandidate,
                (ClipCandidate.workspace_id == BrollPlanRequest.workspace_id)
                & (ClipCandidate.id == BrollPlanRequest.candidate_id),
            )
            .join(
                Transcript,
                (Transcript.workspace_id == ClipCandidate.workspace_id)
                & (Transcript.id == ClipCandidate.transcript_id),
            )
            .where(
                BrollPlanRequest.workspace_id == workspace_id,
                BrollPlanRequest.job_id == job_id,
            )
        ).first()
        if row is None:
            return None
        request, candidate, transcript = row
        return (
            PlanRequest(candidate_id=candidate.id, coverage=request.coverage),
            candidate.project_id,
            CandidateSpan(
                start_word_id=candidate.start_word_id,
                end_word_id=candidate.end_word_id,
                start_ms=candidate.start_ms,
                end_ms=candidate.end_ms,
            ),
            transcript,
        )

    def candidate_is_visible(
        self, *, workspace_id: UUID, project_id: UUID, candidate_id: UUID
    ) -> bool:
        """Report whether one exposed candidate of an active Project is readable here."""
        project = self._session.scalar(
            select(Project.id).where(
                Project.workspace_id == workspace_id,
                Project.id == project_id,
                Project.status == ProjectStatus.READY,
                Project.archived_at.is_(None),
            )
        )
        if project is None:
            return False
        return (
            self._session.scalar(
                select(ClipCandidate.id).where(
                    ClipCandidate.workspace_id == workspace_id,
                    ClipCandidate.project_id == project_id,
                    ClipCandidate.id == candidate_id,
                    ClipCandidate.model_metadata["exposed"].as_boolean().is_(True),
                )
            )
            is not None
        )

    def plan_is_stored(
        self,
        *,
        workspace_id: UUID,
        candidate_id: UUID,
        planner_version: str,
        coverage: BrollCoverage,
    ) -> bool:
        """Report whether this exact plan has already produced its suggestions."""
        stored = self._session.scalar(
            select(func.count())
            .select_from(BrollSuggestion)
            .where(
                BrollSuggestion.workspace_id == workspace_id,
                BrollSuggestion.candidate_id == candidate_id,
                BrollSuggestion.planner_version == planner_version,
                BrollSuggestion.coverage == coverage,
            )
        )
        return bool(stored)

    def suggestions_for_candidate(
        self, *, workspace_id: UUID, candidate_id: UUID
    ) -> tuple[SuggestionSummary, ...]:
        """Read one candidate's proposals in the order they occur in the clip."""
        rows = self._session.scalars(
            select(BrollSuggestion)
            .where(
                BrollSuggestion.workspace_id == workspace_id,
                BrollSuggestion.candidate_id == candidate_id,
            )
            .order_by(BrollSuggestion.start_ms, BrollSuggestion.id)
        )
        return tuple(_summary(row) for row in rows)


def _summary(row: BrollSuggestion) -> SuggestionSummary:
    """Detach only public proposal evidence from one persisted suggestion."""
    return SuggestionSummary(
        suggestion_id=row.id,
        project_id=row.project_id,
        candidate_id=row.candidate_id,
        coverage=row.coverage,
        planner_version=row.planner_version,
        beat_start_word_id=row.beat_start_word_id,
        beat_end_word_id=row.beat_end_word_id,
        start_ms=row.start_ms,
        end_ms=row.end_ms,
        visual_intent=dict(row.visual_intent),
        search_terms=dict(row.search_terms),
        exclusions=tuple(row.exclusions),
        status=row.status,
        placement_reason=row.placement_reason,
        source_type=row.source_type,
        relevance_score=None if row.relevance_score is None else float(row.relevance_score),
        created_at=row.created_at,
        decided_at=row.decided_at,
    )
