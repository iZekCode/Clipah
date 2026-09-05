"""HTTP adapters for B-roll plan admission and review-safe suggestion reads."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime
from math import ceil
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from clipah.api.dependencies import (
    CurrentWorkspace,
    DatabaseSession,
    auth_components_for,
    rate_limiter_for,
    require_csrf,
    require_workspace,
    settings_for,
)
from clipah.api.errors import ApiError
from clipah.broll.models import BrollCoverage, BrollSourceType, BrollSuggestionStatus
from clipah.broll.repository import BrollRepository, ProvenanceSummary, SuggestionSummary
from clipah.broll.use_cases import (
    BrollPlanConflictError,
    BrollTargetNotFoundError,
    list_suggestions,
    start_broll_plan,
    start_broll_retrieval,
)
from clipah.jobs.admission import ConcurrencyLimitError, QuotaExceededError, admission_policy
from clipah.models import JobKind, JobStatus
from clipah.source_imports.dispatch import JobDispatcher
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["broll"])
ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_READ))
]
WritableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.EDIT_WRITE))
]
IdempotencyHeader = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]


class BrollPlanBody(BaseModel):
    """The one choice a member makes when asking for B-roll: how busy the cut should be."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    coverage: BrollCoverage = BrollCoverage.BALANCED


class BrollPlanJobResponse(BaseModel):
    """The durable Job identity returned after planning admission."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    job_id: UUID = Field(alias="jobId")
    status: JobStatus


class VisualIntentResponse(BaseModel):
    """What a reviewer is told about the picture one suggestion asks for."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    subject: str
    action: str
    setting: str
    mood: str
    portrait_suitable: bool = Field(alias="portraitSuitable")
    factual_risk_flags: tuple[str, ...] = Field(alias="factualRiskFlags")
    confidence: float


class SearchTermsResponse(BaseModel):
    """The Indonesian and English intent terms a retriever will search on."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: tuple[str, ...]
    en: tuple[str, ...]


class ProvenanceResponse(BaseModel):
    """Where one accepted picture came from, in the words a reviewer may be shown."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    provider: str
    author: str
    author_url: str = Field(alias="authorUrl")
    source_url: str = Field(alias="sourceUrl")
    license_name: str = Field(alias="licenseName")
    license_url: str = Field(alias="licenseUrl")
    attribution_text: str = Field(alias="attributionText")
    generated: bool


class BrollSuggestionResponse(BaseModel):
    """One strict allowlist of B-roll proposal evidence."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    project_id: UUID = Field(alias="projectId")
    candidate_id: UUID = Field(alias="candidateId")
    coverage: BrollCoverage
    planner_version: str = Field(alias="plannerVersion")
    beat_start_word_id: str = Field(alias="beatStartWordId")
    beat_end_word_id: str = Field(alias="beatEndWordId")
    start_ms: int = Field(alias="startMs")
    end_ms: int = Field(alias="endMs")
    duration_ms: int = Field(alias="durationMs")
    visual_intent: VisualIntentResponse = Field(alias="visualIntent")
    search_terms: SearchTermsResponse = Field(alias="searchTerms")
    exclusions: tuple[str, ...]
    status: BrollSuggestionStatus
    placement_reason: str = Field(alias="placementReason")
    source_type: BrollSourceType | None = Field(alias="sourceType")
    asset_id: UUID | None = Field(alias="assetId")
    provenance: ProvenanceResponse | None
    relevance_score: float | None = Field(alias="relevanceScore")
    created_at: datetime = Field(alias="createdAt")
    decided_at: datetime | None = Field(alias="decidedAt")

    @field_serializer("created_at", "decided_at")
    def serialize_timestamp(self, value: datetime | None) -> str | None:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return None if value is None else value.isoformat()


class BrollSuggestionListResponse(BaseModel):
    """One clip's proposals, in the order they occur inside the clip."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    suggestions: tuple[BrollSuggestionResponse, ...]


@router.post(
    "/projects/{project_id}/candidates/{candidate_id}/broll-plans",
    status_code=202,
    response_model=BrollPlanJobResponse,
    dependencies=[Depends(require_csrf)],
)
def create_plan(
    request: Request,
    project_id: UUID,
    candidate_id: UUID,
    body: BrollPlanBody,
    session: DatabaseSession,
    workspace: WritableWorkspace,
    idempotency_key: IdempotencyHeader,
) -> BrollPlanJobResponse:
    """Commit planning intent before a best-effort UUID-only dispatch."""
    dispatcher: JobDispatcher = request.app.state.job_dispatcher
    try:
        snapshot = start_broll_plan(
            session,
            policy=admission_policy(settings_for(request), rate_limiter_for(request)),
            access=workspace.access,
            project_id=project_id,
            candidate_id=candidate_id,
            coverage=body.coverage,
            idempotency_key=idempotency_key,
            now=auth_components_for(request).now(),
        )
    except BrollTargetNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except BrollPlanConflictError as error:
        raise ApiError(status_code=409, code="CONFLICT") from error
    # Planning spends a concurrency slot and no metered budget: the plan's limit table
    # names no B-roll planning quota and no hourly allowance, so neither a quota nor an
    # hourly refusal can arise here. Charging for planning is deferred with the rest of
    # operational cost accounting.
    except ConcurrencyLimitError as error:
        raise ApiError(status_code=429, code="CONCURRENCY_LIMIT") from error

    session.commit()
    if snapshot.status is JobStatus.QUEUED:
        with suppress(Exception):
            dispatcher.dispatch(
                job_id=snapshot.job_id,
                workspace_id=workspace.access.workspace_id,
                user_id=workspace.access.user_id,
                kind=JobKind.BROLL_PLAN,
            )
    return BrollPlanJobResponse(jobId=snapshot.job_id, status=snapshot.status)


@router.post(
    "/projects/{project_id}/candidates/{candidate_id}/broll-retrievals",
    status_code=202,
    response_model=BrollPlanJobResponse,
    dependencies=[Depends(require_csrf)],
)
def create_retrieval(
    request: Request,
    project_id: UUID,
    candidate_id: UUID,
    body: BrollPlanBody,
    session: DatabaseSession,
    workspace: WritableWorkspace,
    idempotency_key: IdempotencyHeader,
) -> BrollPlanJobResponse:
    """Commit the intent to find pictures for one plan before dispatching the search."""
    dispatcher: JobDispatcher = request.app.state.job_dispatcher
    try:
        snapshot = start_broll_retrieval(
            session,
            policy=admission_policy(settings_for(request), rate_limiter_for(request)),
            access=workspace.access,
            project_id=project_id,
            candidate_id=candidate_id,
            coverage=body.coverage,
            idempotency_key=idempotency_key,
            now=auth_components_for(request).now(),
        )
    except BrollTargetNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except BrollPlanConflictError as error:
        raise ApiError(status_code=409, code="CONFLICT") from error
    except QuotaExceededError as error:
        raise ApiError(
            status_code=429,
            code="QUOTA_EXCEEDED",
            retry_after_seconds=max(ceil(error.retry_after.total_seconds()), 1),
        ) from error
    except ConcurrencyLimitError as error:
        raise ApiError(status_code=429, code="CONCURRENCY_LIMIT") from error

    session.commit()
    if snapshot.status is JobStatus.QUEUED:
        with suppress(Exception):
            dispatcher.dispatch(
                job_id=snapshot.job_id,
                workspace_id=workspace.access.workspace_id,
                user_id=workspace.access.user_id,
                kind=JobKind.BROLL_RETRIEVE,
            )
    return BrollPlanJobResponse(jobId=snapshot.job_id, status=snapshot.status)


@router.get(
    "/projects/{project_id}/candidates/{candidate_id}/broll-suggestions",
    response_model=BrollSuggestionListResponse,
)
def list_collection(
    project_id: UUID,
    candidate_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
) -> BrollSuggestionListResponse:
    """List one clip's proposals without admitting a foreign or hidden candidate."""
    try:
        suggestions = list_suggestions(
            BrollRepository(session),
            access=workspace.access,
            project_id=project_id,
            candidate_id=candidate_id,
        )
    except BrollTargetNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return BrollSuggestionListResponse(
        suggestions=tuple(_suggestion_body(item) for item in suggestions)
    )


def _suggestion_body(suggestion: SuggestionSummary) -> BrollSuggestionResponse:
    """Render only the proposal evidence a reviewer is allowed to inspect."""
    intent = suggestion.visual_intent
    terms = suggestion.search_terms
    return BrollSuggestionResponse(
        id=suggestion.suggestion_id,
        projectId=suggestion.project_id,
        candidateId=suggestion.candidate_id,
        coverage=suggestion.coverage,
        plannerVersion=suggestion.planner_version,
        beatStartWordId=suggestion.beat_start_word_id,
        beatEndWordId=suggestion.beat_end_word_id,
        startMs=suggestion.start_ms,
        endMs=suggestion.end_ms,
        durationMs=suggestion.end_ms - suggestion.start_ms,
        visualIntent=VisualIntentResponse(
            subject=str(intent["subject"]),
            action=str(intent["action"]),
            setting=str(intent["setting"]),
            mood=str(intent["mood"]),
            portraitSuitable=bool(intent["portrait_suitable"]),
            factualRiskFlags=tuple(str(flag) for flag in intent["factual_risk_flags"]),
            confidence=float(intent["confidence"]),
        ),
        searchTerms=SearchTermsResponse(
            id=tuple(str(term) for term in terms["id"]),
            en=tuple(str(term) for term in terms["en"]),
        ),
        exclusions=suggestion.exclusions,
        status=suggestion.status,
        placementReason=suggestion.placement_reason,
        sourceType=suggestion.source_type,
        assetId=suggestion.asset_id,
        provenance=_provenance_body(suggestion.provenance),
        relevanceScore=suggestion.relevance_score,
        createdAt=suggestion.created_at,
        decidedAt=suggestion.decided_at,
    )


def _provenance_body(provenance: ProvenanceSummary | None) -> ProvenanceResponse | None:
    """Render the attribution of one picture, or report that there is none to show."""
    if provenance is None:
        return None
    return ProvenanceResponse(
        provider=provenance.provider,
        author=provenance.author,
        authorUrl=provenance.author_url,
        sourceUrl=provenance.source_url,
        licenseName=provenance.license_name,
        licenseUrl=provenance.license_url,
        attributionText=provenance.attribution_text,
        generated=provenance.generated,
    )
