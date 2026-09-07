"""Feature-gated review comments and decisions for immutable Edit Revisions."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from clipah.api.dependencies import (
    CurrentWorkspace,
    DatabaseSession,
    auth_components_for,
    require_csrf,
    require_workspace,
    settings_for,
)
from clipah.api.errors import ApiError
from clipah.editor.accessibility import (
    AccessibilitySeverity,
    AccessibilityWarning,
    analyze_accessibility,
)
from clipah.editor.reviews import (
    ReviewAnchor,
    ReviewComment,
    ReviewDecision,
    ReviewInvalidError,
    ReviewNotFoundError,
    ReviewSummary,
    add_comment,
    record_decision,
    resolve_comment,
    review_summary,
    revision_document,
)
from clipah.models import EditReviewDecisionKind, ReviewAnchorKind
from clipah.variants.models import Platform
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["edit-reviews"])
ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_READ))
]
ReviewableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.REVIEW_DECIDE))
]


class ReviewAnchorRequest(BaseModel):
    """One timestamp or item anchor supplied with a comment."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    kind: ReviewAnchorKind
    timestamp_ms: int | None = Field(default=None, alias="timestampMs", ge=0)
    item_id: str | None = Field(default=None, alias="itemId", min_length=1, max_length=128)


class ReviewCommentRequest(BaseModel):
    """One plain-text comment against an exact immutable Revision."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    revision_id: UUID = Field(alias="revisionId")
    text: str = Field(min_length=1, max_length=4_000)
    anchor: ReviewAnchorRequest


class ReviewDecisionRequest(BaseModel):
    """One approval or request for changes against exact bytes."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    revision_id: UUID = Field(alias="revisionId")
    decision: EditReviewDecisionKind


class CommentResolutionRequest(BaseModel):
    """The next derived resolution state for one immutable comment."""

    model_config = ConfigDict(extra="forbid")

    resolved: bool


class ReviewAnchorResponse(BaseModel):
    """The stable location one comment identifies."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    kind: ReviewAnchorKind
    timestamp_ms: int | None = Field(default=None, alias="timestampMs")
    item_id: str | None = Field(default=None, alias="itemId")


class ReviewCommentResponse(BaseModel):
    """One review comment with its derived resolution state."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    revision_id: UUID = Field(alias="revisionId")
    revision: int
    actor_user_id: UUID = Field(alias="actorUserId")
    actor_display_name: str = Field(alias="actorDisplayName")
    text: str
    anchor: ReviewAnchorResponse
    resolved: bool
    created_at: datetime = Field(alias="createdAt")

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        """Return an explicit-offset audit timestamp."""
        return value.isoformat()


class ReviewDecisionResponse(BaseModel):
    """One historical decision and whether it still targets current bytes."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    revision_id: UUID = Field(alias="revisionId")
    revision: int
    actor_user_id: UUID = Field(alias="actorUserId")
    actor_display_name: str = Field(alias="actorDisplayName")
    decision: EditReviewDecisionKind
    current: bool
    created_at: datetime = Field(alias="createdAt")

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        """Return an explicit-offset audit timestamp."""
        return value.isoformat()


class ReviewSummaryResponse(BaseModel):
    """The current review state and immutable history for one Edit."""

    model_config = ConfigDict(extra="forbid")

    approved: bool
    comments: tuple[ReviewCommentResponse, ...]
    decisions: tuple[ReviewDecisionResponse, ...]


class AccessibilityWarningResponse(BaseModel):
    """One stable and actionable composition-quality warning."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    code: str
    severity: AccessibilitySeverity
    action: str
    item_id: str | None = Field(default=None, alias="itemId")
    start_ms: int | None = Field(default=None, alias="startMs")
    end_ms: int | None = Field(default=None, alias="endMs")
    measured: float | None = None
    threshold: float | None = None


class AccessibilityResponse(BaseModel):
    """Accessibility advice calculated for one exact immutable Revision."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    revision_id: UUID = Field(alias="revisionId")
    warnings: tuple[AccessibilityWarningResponse, ...]


def _enabled(request: Request) -> None:
    """Hide collaboration review routes while the feature is disabled."""
    if not settings_for(request).collaboration_enabled:
        raise ApiError(status_code=404, code="NOT_FOUND")


@router.post(
    "/edits/{edit_id}/review-comments",
    status_code=201,
    response_model=ReviewCommentResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_csrf)],
)
def create_review_comment(
    request: Request,
    edit_id: UUID,
    payload: ReviewCommentRequest,
    session: DatabaseSession,
    workspace: ReviewableWorkspace,
) -> ReviewCommentResponse:
    """Append one comment to an immutable Revision."""
    _enabled(request)
    try:
        comment = add_comment(
            session,
            access=workspace.access,
            edit_id=edit_id,
            revision_id=payload.revision_id,
            text=payload.text,
            anchor=ReviewAnchor(
                kind=payload.anchor.kind,
                timestamp_ms=payload.anchor.timestamp_ms,
                item_id=payload.anchor.item_id,
            ),
            now=auth_components_for(request).now(),
        )
    except ReviewNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except ReviewInvalidError as error:
        raise ApiError(status_code=422, code="REVIEW_INVALID") from error
    return _comment_body(comment)


@router.post(
    "/edits/{edit_id}/reviews",
    status_code=201,
    response_model=ReviewDecisionResponse,
    dependencies=[Depends(require_csrf)],
)
def create_review_decision(
    request: Request,
    edit_id: UUID,
    payload: ReviewDecisionRequest,
    session: DatabaseSession,
    workspace: ReviewableWorkspace,
) -> ReviewDecisionResponse:
    """Append one approval or request for changes."""
    _enabled(request)
    try:
        decision = record_decision(
            session,
            access=workspace.access,
            edit_id=edit_id,
            revision_id=payload.revision_id,
            decision=payload.decision,
            now=auth_components_for(request).now(),
        )
    except ReviewNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return _decision_body(decision)


@router.post(
    "/edit-review-comments/{comment_id}/resolution",
    response_model=ReviewCommentResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_csrf)],
)
def update_comment_resolution(
    request: Request,
    comment_id: UUID,
    payload: CommentResolutionRequest,
    session: DatabaseSession,
    workspace: ReviewableWorkspace,
) -> ReviewCommentResponse:
    """Append one resolution or reopening event for a review comment."""
    _enabled(request)
    try:
        comment = resolve_comment(
            session,
            access=workspace.access,
            comment_id=comment_id,
            resolved=payload.resolved,
            now=auth_components_for(request).now(),
        )
    except ReviewNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return _comment_body(comment)


@router.get("/edits/{edit_id}/reviews", response_model=ReviewSummaryResponse)
def show_edit_review(
    request: Request,
    edit_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
) -> ReviewSummaryResponse:
    """Read the current answer and immutable review history."""
    _enabled(request)
    try:
        summary = review_summary(session, access=workspace.access, edit_id=edit_id)
    except ReviewNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return _summary_body(summary)


@router.get(
    "/edits/{edit_id}/accessibility",
    response_model=AccessibilityResponse,
    response_model_exclude_none=True,
)
def show_accessibility_quality(
    edit_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
    revision_id: Annotated[UUID, Query()],
    platform: Platform | None = None,
) -> AccessibilityResponse:
    """Measure one immutable Revision without changing its composition."""
    try:
        document = revision_document(
            session,
            workspace_id=workspace.access.workspace_id,
            edit_id=edit_id,
            revision_id=revision_id,
        )
    except ReviewNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return AccessibilityResponse(
        revisionId=revision_id,
        warnings=tuple(
            _accessibility_body(item) for item in analyze_accessibility(document, platform=platform)
        ),
    )


def _summary_body(summary: ReviewSummary) -> ReviewSummaryResponse:
    """Render one review summary without exposing persistence details."""
    return ReviewSummaryResponse(
        approved=summary.approved,
        comments=tuple(_comment_body(comment) for comment in summary.comments),
        decisions=tuple(_decision_body(decision) for decision in summary.decisions),
    )


def _comment_body(comment: ReviewComment) -> ReviewCommentResponse:
    """Render one review comment."""
    anchor = comment.anchor
    return ReviewCommentResponse(
        id=comment.comment_id,
        revisionId=comment.revision_id,
        revision=comment.revision,
        actorUserId=comment.actor_user_id,
        actorDisplayName=comment.actor_display_name,
        text=comment.text,
        anchor=ReviewAnchorResponse(
            kind=anchor.kind, timestampMs=anchor.timestamp_ms, itemId=anchor.item_id
        ),
        resolved=comment.resolved,
        createdAt=comment.created_at,
    )


def _decision_body(decision: ReviewDecision) -> ReviewDecisionResponse:
    """Render one review decision."""
    return ReviewDecisionResponse(
        id=decision.decision_id,
        revisionId=decision.revision_id,
        revision=decision.revision,
        actorUserId=decision.actor_user_id,
        actorDisplayName=decision.actor_display_name,
        decision=decision.decision,
        current=decision.current,
        createdAt=decision.created_at,
    )


def _accessibility_body(warning: AccessibilityWarning) -> AccessibilityWarningResponse:
    """Render one deterministic accessibility warning."""
    return AccessibilityWarningResponse(
        code=warning.code,
        severity=warning.severity,
        action=warning.action,
        itemId=warning.item_id,
        startMs=warning.start_ms,
        endMs=warning.end_ms,
        measured=warning.measured,
        threshold=warning.threshold,
    )
