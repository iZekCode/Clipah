"""HTTP adapters for Edits and their immutable Revision history."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from clipah.api.dependencies import (
    CurrentWorkspace,
    DatabaseSession,
    auth_components_for,
    require_csrf,
    require_workspace,
)
from clipah.api.errors import ApiError
from clipah.editor.models import CompositionV1, CompositionValidationError
from clipah.editor.repository import EditDetail, EditRepository, RevisionSummary
from clipah.editor.use_cases import (
    BrollDecision,
    BrollDecisionError,
    BrollSuggestionNotFoundError,
    CandidateNotEditableError,
    CompositionAssetError,
    EditNotFoundError,
    EditRevisionConflictError,
    create_edit_from_candidate,
    decide_on_suggestion,
    get_edit,
    list_revisions,
    save_revision,
)
from clipah.workspaces.models import WorkspaceAction

CURRENT_REVISION_HEADER = "X-Clipah-Current-Revision"

router = APIRouter(prefix="/api/v1", tags=["edits"])
ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_READ))
]
EditableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.EDIT_WRITE))
]


class EditResponse(BaseModel):
    """One Edit and the composition its current Revision holds."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    project_id: UUID = Field(alias="projectId")
    candidate_id: UUID = Field(alias="candidateId")
    current_revision: int = Field(alias="currentRevision")
    composition: CompositionV1
    composition_hash: str = Field(alias="compositionHash")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    @field_serializer("created_at", "updated_at")
    def serialize_timestamp(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return value.isoformat()


class RevisionResponse(BaseModel):
    """One entry of an Edit's history, without repeating its whole composition."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    revision: int
    composition_hash: str = Field(alias="compositionHash")
    created_by: UUID = Field(alias="createdBy")
    created_at: datetime = Field(alias="createdAt")

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return value.isoformat()


class RevisionHistoryResponse(BaseModel):
    """One Edit's Revisions, newest first."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    revisions: tuple[RevisionResponse, ...]


class SaveRevisionRequest(BaseModel):
    """One composition offered as the successor of the Revision the caller holds."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    expected_revision: int = Field(alias="expectedRevision", ge=1)
    composition: dict[str, object]


class BrollDecisionBody(BaseModel):
    """What one member decided about one proposed picture."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    suggestion_id: UUID = Field(alias="suggestionId")
    action: BrollDecision
    # Only a replacement names media; every other decision is about the picture the
    # suggestion already holds.
    asset_id: UUID | None = Field(alias="assetId", default=None)


class BrollDecisionRequest(BaseModel):
    """One decision and the composition it produced, offered as the next Revision."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    expected_revision: int = Field(alias="expectedRevision", ge=1)
    composition: dict[str, object]
    decision: BrollDecisionBody


@router.post(
    "/projects/{project_id}/candidates/{candidate_id}/edits",
    response_model=EditResponse,
    dependencies=[Depends(require_csrf)],
)
def create(
    request: Request,
    project_id: UUID,
    candidate_id: UUID,
    session: DatabaseSession,
    workspace: EditableWorkspace,
    response: Response,
) -> EditResponse:
    """Open the one Edit belonging to a reviewed candidate, or reach the existing one."""
    try:
        detail, created = create_edit_from_candidate(
            EditRepository(session),
            access=workspace.access,
            project_id=project_id,
            candidate_id=candidate_id,
            now=auth_components_for(request).now(),
        )
    except CandidateNotEditableError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    response.status_code = 201 if created else 200
    return _edit_body(detail)


@router.get("/edits/{edit_id}", response_model=EditResponse)
def show(
    request: Request,
    edit_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
) -> EditResponse:
    """Read one Edit, hiding another Workspace's Edit behind the same absence."""
    try:
        detail = get_edit(EditRepository(session), access=workspace.access, edit_id=edit_id)
    except EditNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return _edit_body(detail)


@router.put(
    "/edits/{edit_id}",
    response_model=EditResponse,
    dependencies=[Depends(require_csrf)],
)
def save(
    request: Request,
    edit_id: UUID,
    body: SaveRevisionRequest,
    session: DatabaseSession,
    workspace: EditableWorkspace,
) -> EditResponse:
    """Append one composition as the next Revision, or refuse a stale expectation."""
    try:
        detail = save_revision(
            EditRepository(session),
            access=workspace.access,
            edit_id=edit_id,
            expected_revision=body.expected_revision,
            document=dict(body.composition),
            now=auth_components_for(request).now(),
        )
    except EditNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except CompositionValidationError as error:
        raise ApiError(status_code=422, code="COMPOSITION_INVALID") from error
    except CompositionAssetError as error:
        raise ApiError(status_code=422, code="COMPOSITION_ASSET_FORBIDDEN") from error
    except EditRevisionConflictError as error:
        raise ApiError(
            status_code=409,
            code="EDIT_REVISION_CONFLICT",
            headers={CURRENT_REVISION_HEADER: str(error.current_revision)},
        ) from error
    return _edit_body(detail)


@router.post(
    "/edits/{edit_id}/broll-decisions",
    response_model=EditResponse,
    dependencies=[Depends(require_csrf)],
)
def decide(
    request: Request,
    edit_id: UUID,
    body: BrollDecisionRequest,
    session: DatabaseSession,
    workspace: EditableWorkspace,
) -> EditResponse:
    """Record one B-roll decision and the Revision it produced, in one transaction."""
    try:
        detail = decide_on_suggestion(
            EditRepository(session),
            access=workspace.access,
            edit_id=edit_id,
            suggestion_id=body.decision.suggestion_id,
            action=body.decision.action,
            replacement_asset_id=body.decision.asset_id,
            expected_revision=body.expected_revision,
            document=dict(body.composition),
            now=auth_components_for(request).now(),
        )
    except (EditNotFoundError, BrollSuggestionNotFoundError) as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except CompositionValidationError as error:
        raise ApiError(status_code=422, code="COMPOSITION_INVALID") from error
    except BrollDecisionError as error:
        raise ApiError(status_code=422, code="BROLL_DECISION_INVALID") from error
    except CompositionAssetError as error:
        raise ApiError(status_code=422, code="COMPOSITION_ASSET_FORBIDDEN") from error
    except EditRevisionConflictError as error:
        raise ApiError(
            status_code=409,
            code="EDIT_REVISION_CONFLICT",
            headers={CURRENT_REVISION_HEADER: str(error.current_revision)},
        ) from error
    return _edit_body(detail)


@router.get("/edits/{edit_id}/revisions", response_model=RevisionHistoryResponse)
def history(
    request: Request,
    edit_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
) -> RevisionHistoryResponse:
    """List one Edit's Revisions, newest first."""
    try:
        revisions = list_revisions(
            EditRepository(session), access=workspace.access, edit_id=edit_id
        )
    except EditNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return RevisionHistoryResponse(
        revisions=tuple(_revision_body(revision) for revision in revisions)
    )


def _edit_body(detail: EditDetail) -> EditResponse:
    """Render one Edit exactly as its stored Revision holds it."""
    return EditResponse(
        id=detail.edit_id,
        projectId=detail.project_id,
        candidateId=detail.candidate_id,
        currentRevision=detail.current_revision,
        composition=CompositionV1.model_validate(detail.composition),
        compositionHash=detail.composition_hash.hex(),
        createdAt=detail.created_at,
        updatedAt=detail.updated_at,
    )


def _revision_body(revision: RevisionSummary) -> RevisionResponse:
    """Render one history entry without repeating the composition behind it."""
    return RevisionResponse(
        revision=revision.revision,
        compositionHash=revision.composition_hash.hex(),
        createdBy=revision.created_by_user_id,
        createdAt=revision.created_at,
    )
