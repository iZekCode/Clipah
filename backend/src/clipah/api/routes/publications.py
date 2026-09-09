"""Explicit HTTP ceremony for durable social Publications."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from sqlalchemy import select

from clipah.api.dependencies import (
    CurrentWorkspace,
    DatabaseSession,
    auth_components_for,
    require_csrf,
    require_workspace,
)
from clipah.api.errors import ApiError
from clipah.models import PublicationBatch
from clipah.publishing.models import (
    PublicationBatchSummary,
    PublicationDestinationDraft,
    PublicationSummary,
)
from clipah.publishing.state_machine import PublicationCancellationRejectedError
from clipah.publishing.use_cases import (
    PublicationIdempotencyConflictError,
    PublicationInvalidError,
    PublicationNotFoundError,
    PublicationRetryBlockedError,
    cancel_publication,
    confirm_publication_draft,
    get_publication,
    get_publication_batch,
    list_publications,
    preflight_publication_draft,
    prepare_publication_draft,
    retry_publication,
)
from clipah.workspaces.models import WorkspaceAccess, WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["publications"])
ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_READ))
]
EditableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.EDIT_WRITE))
]
PublishingWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PUBLISH))
]
IdempotencyHeader = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]


class PublicationDestinationBody(BaseModel):
    """Validated choices for one independently recoverable destination."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    social_account_id: UUID = Field(alias="socialAccountId")
    metadata: dict[str, Any] = Field(default_factory=dict)
    provider_options: dict[str, Any] = Field(default_factory=dict, alias="providerOptions")
    consent: dict[str, Any] = Field(default_factory=dict)
    scheduled_for: datetime | None = Field(default=None, alias="scheduledFor")
    display_timezone: str = Field(alias="displayTimezone", min_length=1, max_length=255)


class PreparePublicationBody(BaseModel):
    """The exact render and destinations whose approval will later be frozen."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    render_artifact_id: UUID = Field(alias="renderArtifactId")
    destinations: tuple[PublicationDestinationBody, ...] = Field(min_length=1, max_length=20)


class PublicationResponse(BaseModel):
    """One safe destination lifecycle projection."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    social_account_id: UUID = Field(alias="socialAccountId")
    status: str
    scheduled_for: datetime | None = Field(alias="scheduledFor")
    display_timezone: str = Field(alias="displayTimezone")

    @field_serializer("scheduled_for")
    def serialize_schedule(self, value: datetime | None) -> str | None:
        """Keep scheduled instants explicit and offset-aware."""
        return None if value is None else value.isoformat()


class PublicationBatchResponse(BaseModel):
    """An immutable convenience group around independent destinations."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    edit_revision_id: UUID = Field(alias="editRevisionId")
    render_artifact_id: UUID = Field(alias="renderArtifactId")
    publications: list[PublicationResponse]


class PublicationsResponse(BaseModel):
    """Every publication visible in the requested Workspace."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    publications: list[PublicationResponse]


@router.post(
    "/edits/{edit_id}/revisions/{revision}/publication-drafts",
    response_model=PublicationBatchResponse,
    dependencies=[Depends(require_csrf)],
)
def prepare(
    request: Request,
    edit_id: UUID,
    revision: int,
    body: PreparePublicationBody,
    session: DatabaseSession,
    workspace: EditableWorkspace,
    idempotency_key: IdempotencyHeader,
    response: Response,
) -> PublicationBatchResponse:
    """Prepare an idempotent batch without performing an external side effect."""
    try:
        existing = get_publication_batch_by_key(
            session, access=workspace.access, idempotency_key=idempotency_key
        )
        summary = prepare_publication_draft(
            session,
            access=workspace.access,
            edit_id=edit_id,
            revision=revision,
            render_artifact_id=body.render_artifact_id,
            destinations=tuple(_destination(item) for item in body.destinations),
            idempotency_key=idempotency_key,
            now=auth_components_for(request).now(),
        )
    except PublicationNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except PublicationInvalidError as error:
        raise ApiError(status_code=422, code="PUBLICATION_INVALID") from error
    except PublicationIdempotencyConflictError as error:
        raise ApiError(status_code=409, code="IDEMPOTENCY_CONFLICT") from error
    session.commit()
    response.status_code = 200 if existing else 201
    return _batch_response(summary)


def get_publication_batch_by_key(
    session: DatabaseSession, *, access: WorkspaceAccess, idempotency_key: str
) -> bool:
    """Report whether this key already exists without broadening the public use-case surface."""
    return (
        session.scalar(
            select(PublicationBatch.id).where(
                PublicationBatch.workspace_id == access.workspace_id,
                PublicationBatch.idempotency_key == idempotency_key,
            )
        )
        is not None
    )


@router.get("/publication-drafts/{draft_id}", response_model=PublicationBatchResponse)
@router.get("/publication-batches/{draft_id}", response_model=PublicationBatchResponse)
def show_batch(
    draft_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
) -> PublicationBatchResponse:
    """Read one draft or confirmed batch through its stable batch identifier."""
    try:
        return _batch_response(
            get_publication_batch(session, access=workspace.access, batch_id=draft_id)
        )
    except PublicationNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error


@router.post(
    "/publication-drafts/{draft_id}/preflight",
    response_model=PublicationBatchResponse,
    dependencies=[Depends(require_csrf)],
)
def preflight(
    request: Request,
    draft_id: UUID,
    session: DatabaseSession,
    workspace: EditableWorkspace,
) -> PublicationBatchResponse:
    """Validate a prepared batch before asking for irreversible approval."""
    try:
        summary = preflight_publication_draft(
            session,
            access=workspace.access,
            batch_id=draft_id,
            now=auth_components_for(request).now(),
        )
    except PublicationNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except PublicationInvalidError as error:
        raise ApiError(status_code=409, code="PUBLICATION_STATE_CONFLICT") from error
    session.commit()
    return _batch_response(summary)


@router.post(
    "/publication-drafts/{draft_id}/confirm",
    response_model=PublicationBatchResponse,
    dependencies=[Depends(require_csrf)],
)
def confirm(
    request: Request,
    draft_id: UUID,
    session: DatabaseSession,
    workspace: PublishingWorkspace,
) -> PublicationBatchResponse:
    """Freeze approval evidence and schedule or queue each destination independently."""
    try:
        summary = confirm_publication_draft(
            session,
            access=workspace.access,
            batch_id=draft_id,
            now=auth_components_for(request).now(),
        )
    except PublicationNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except PublicationInvalidError as error:
        raise ApiError(status_code=409, code="PUBLICATION_STATE_CONFLICT") from error
    session.commit()
    return _batch_response(summary)


@router.get("/publications", response_model=PublicationsResponse)
def index(session: DatabaseSession, workspace: ReadableWorkspace) -> PublicationsResponse:
    """List safe lifecycle state for one Workspace."""
    return PublicationsResponse(
        publications=[
            _publication_response(item)
            for item in list_publications(session, access=workspace.access)
        ]
    )


@router.get("/publications/{publication_id}", response_model=PublicationResponse)
def show(
    publication_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
) -> PublicationResponse:
    """Read one destination while hiding guessed cross-Workspace identifiers."""
    try:
        return _publication_response(
            get_publication(session, access=workspace.access, publication_id=publication_id)
        )
    except PublicationNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error


@router.post(
    "/publications/{publication_id}/cancel",
    response_model=PublicationResponse,
    dependencies=[Depends(require_csrf)],
)
def cancel(
    request: Request,
    publication_id: UUID,
    session: DatabaseSession,
    workspace: PublishingWorkspace,
) -> PublicationResponse:
    """Cancel only work whose provider side effect can still be stopped truthfully."""
    try:
        summary = cancel_publication(
            session,
            access=workspace.access,
            publication_id=publication_id,
            provider_cancellable=True,
            now=auth_components_for(request).now(),
        )
    except PublicationNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except PublicationCancellationRejectedError as error:
        raise ApiError(status_code=409, code="CANCELLATION_NOT_GUARANTEED") from error
    session.commit()
    return _publication_response(summary)


@router.post(
    "/publications/{publication_id}/retry",
    response_model=PublicationResponse,
    dependencies=[Depends(require_csrf)],
)
def retry(
    request: Request,
    publication_id: UUID,
    session: DatabaseSession,
    workspace: PublishingWorkspace,
) -> PublicationResponse:
    """Queue one reconciled retry without duplicating ambiguous provider work."""
    try:
        summary = retry_publication(
            session,
            access=workspace.access,
            publication_id=publication_id,
            now=auth_components_for(request).now(),
        )
    except PublicationNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except PublicationRetryBlockedError as error:
        raise ApiError(status_code=409, code="PROVIDER_RECONCILIATION_REQUIRED") from error
    except PublicationInvalidError as error:
        raise ApiError(status_code=409, code="PUBLICATION_STATE_CONFLICT") from error
    session.commit()
    return _publication_response(summary)


def _destination(body: PublicationDestinationBody) -> PublicationDestinationDraft:
    """Translate validated HTTP aliases into the domain value."""
    return PublicationDestinationDraft(
        social_account_id=body.social_account_id,
        metadata=body.metadata,
        provider_options=body.provider_options,
        consent=body.consent,
        scheduled_for=body.scheduled_for,
        display_timezone=body.display_timezone,
    )


def _publication_response(summary: PublicationSummary) -> PublicationResponse:
    """Map one safe domain projection to stable camel-case JSON."""
    return PublicationResponse(
        id=summary.publication_id,
        socialAccountId=summary.social_account_id,
        status=summary.status.value,
        scheduledFor=summary.scheduled_for,
        displayTimezone=summary.display_timezone,
    )


def _batch_response(summary: PublicationBatchSummary) -> PublicationBatchResponse:
    """Map one batch without collapsing its independent destination states."""
    return PublicationBatchResponse(
        id=summary.batch_id,
        editRevisionId=summary.edit_revision_id,
        renderArtifactId=summary.render_artifact_id,
        publications=[_publication_response(item) for item in summary.publications],
    )
