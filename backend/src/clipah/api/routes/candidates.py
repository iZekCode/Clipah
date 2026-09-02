"""HTTP adapters for paginated, review-safe Clip Candidate reads."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from clipah.api.dependencies import CurrentWorkspace, DatabaseSession, require_workspace
from clipah.api.errors import ApiError
from clipah.highlights.models import ClipCategory
from clipah.highlights.repository import CandidateBoundary, CandidateSummary, HighlightRepository
from clipah.highlights.use_cases import CandidateNotFoundError, get_candidate, list_candidates
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["candidates"])
ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_READ))
]


class CandidateScoreResponse(BaseModel):
    """The seven explainable dimensions behind one public candidate score."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    hook: float
    payoff: float
    narrative_completeness: float = Field(alias="narrativeCompleteness")
    context_safety: float = Field(alias="contextSafety")
    platform_fit: float = Field(alias="platformFit")
    transcript_confidence: float = Field(alias="transcriptConfidence")
    visual_opportunity: float = Field(alias="visualOpportunity")


class CandidateResponse(BaseModel):
    """One strict allowlist of Clip Candidate review evidence."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    project_id: UUID = Field(alias="projectId")
    rank: int
    score: float
    hook: str
    payoff: str
    reason: str
    category: ClipCategory
    tags: tuple[str, ...]
    start_ms: int = Field(alias="startMs")
    end_ms: int = Field(alias="endMs")
    duration_ms: int = Field(alias="durationMs")
    transcript_excerpt: str = Field(alias="transcriptExcerpt")
    context_dependencies: tuple[str, ...] = Field(alias="contextDependencies")
    score_breakdown: CandidateScoreResponse = Field(alias="scoreBreakdown")
    context_warnings: tuple[str, ...] = Field(alias="contextWarnings")
    visual_opportunities: tuple[str, ...] = Field(alias="visualOpportunities")
    created_at: datetime = Field(alias="createdAt")

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return value.isoformat()


class CandidatePageResponse(BaseModel):
    """One strict ranked candidate page and its opaque continuation cursor."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    candidates: tuple[CandidateResponse, ...]
    next_cursor: str | None = Field(alias="nextCursor")


@router.get(
    "/projects/{project_id}/candidates",
    response_model=CandidatePageResponse,
)
def list_collection(
    project_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    cursor: str | None = None,
) -> CandidatePageResponse:
    """List exposed Clip Candidates in stable global-rank order."""
    try:
        page = list_candidates(
            HighlightRepository(session),
            access=workspace.access,
            project_id=project_id,
            limit=limit,
            after=_decode_cursor(cursor) if cursor is not None else None,
        )
    except CandidateNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except ValueError as error:
        raise ApiError(status_code=422, code="VALIDATION_ERROR") from error
    return CandidatePageResponse(
        candidates=tuple(_candidate_body(candidate) for candidate in page.candidates),
        nextCursor=(None if page.next_boundary is None else _encode_cursor(page.next_boundary)),
    )


@router.get(
    "/projects/{project_id}/candidates/{candidate_id}",
    response_model=CandidateResponse,
)
def show(
    project_id: UUID,
    candidate_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
) -> CandidateResponse:
    """Show one exposed candidate without leaking hidden or cross-Project rows."""
    try:
        candidate = get_candidate(
            HighlightRepository(session),
            access=workspace.access,
            project_id=project_id,
            candidate_id=candidate_id,
        )
    except CandidateNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return _candidate_body(candidate)


def _candidate_body(candidate: CandidateSummary) -> CandidateResponse:
    """Render only the candidate evidence a reviewer is allowed to inspect."""
    breakdown = candidate.score_breakdown
    return CandidateResponse(
        id=candidate.candidate_id,
        projectId=candidate.project_id,
        rank=candidate.rank,
        score=candidate.score,
        hook=candidate.hook,
        payoff=candidate.payoff,
        reason=candidate.reason,
        category=ClipCategory(candidate.category),
        tags=candidate.tags,
        startMs=candidate.start_ms,
        endMs=candidate.end_ms,
        durationMs=candidate.end_ms - candidate.start_ms,
        transcriptExcerpt=candidate.transcript_excerpt,
        contextDependencies=candidate.context_dependencies,
        scoreBreakdown=CandidateScoreResponse(
            hook=float(breakdown["hook"]),
            payoff=float(breakdown["payoff"]),
            narrativeCompleteness=float(breakdown["narrative_completeness"]),
            contextSafety=float(breakdown["context_safety"]),
            platformFit=float(breakdown["platform_fit"]),
            transcriptConfidence=float(breakdown["transcript_confidence"]),
            visualOpportunity=float(breakdown["visual_opportunity"]),
        ),
        contextWarnings=candidate.context_warnings,
        visualOpportunities=candidate.visual_opportunities,
        createdAt=candidate.created_at,
    )


def _encode_cursor(boundary: CandidateBoundary) -> str:
    """Encode a rank and UUID as an opaque URL-safe pagination boundary."""
    raw = f"{boundary.rank}|{boundary.candidate_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> CandidateBoundary:
    """Decode one opaque rank cursor or reject it without query-side effects."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        rank, candidate_id = base64.urlsafe_b64decode(padded).decode().split("|", 1)
        parsed_rank = int(rank)
        if parsed_rank <= 0:
            raise ValueError("candidate cursor rank must be positive")
        return CandidateBoundary(rank=parsed_rank, candidate_id=UUID(candidate_id))
    except (ValueError, UnicodeDecodeError, binascii.Error) as error:
        raise ValueError("invalid candidate cursor") from error
