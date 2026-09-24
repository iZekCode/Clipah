"""HTTP adapters for a clip's cover picture: where it stands, drawing it, and saving it."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime
from math import ceil
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from clipah.api.dependencies import (
    CurrentWorkspace,
    DatabaseSession,
    auth_components_for,
    object_store_for,
    rate_limiter_for,
    require_csrf,
    require_workspace,
    settings_for,
)
from clipah.api.errors import ApiError
from clipah.assets.storage import ObjectStore
from clipah.auth.limits import RateLimitExceededError
from clipah.editor.covers import (
    CoverNotDesignedError,
    CoverNotFoundError,
    CoverState,
    CoverStatus,
    cover_state,
    request_cover,
    sign_cover,
)
from clipah.jobs.admission import ConcurrencyLimitError, QuotaExceededError, admission_policy
from clipah.models import JobKind
from clipah.source_imports.dispatch import JobDispatcher
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["covers"])
ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_READ))
]
EditableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.EDIT_WRITE))
]


class CoverResponse(BaseModel):
    """One clip's cover for its current Revision, with a short-lived link once it exists."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    status: CoverStatus
    revision: int
    job_id: UUID | None = Field(alias="jobId")
    error_code: str | None = Field(alias="errorCode")
    url: str | None
    expires_at: datetime | None = Field(alias="expiresAt")

    @field_serializer("expires_at")
    def serialize_expires_at(self, value: datetime | None) -> str | None:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return None if value is None else value.isoformat()


@router.get("/edits/{edit_id}/cover", response_model=CoverResponse)
def show(
    edit_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
    store: Annotated[ObjectStore, Depends(object_store_for)],
    download: Annotated[bool, Query()] = False,
) -> CoverResponse:
    """Read where the current Revision's cover stands, signing its picture when it exists."""
    try:
        state = cover_state(session, access=workspace.access, edit_id=edit_id)
    except CoverNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return _body(state, store, download=download)


@router.post(
    "/edits/{edit_id}/cover",
    response_model=CoverResponse,
    dependencies=[Depends(require_csrf)],
)
def create(
    request: Request,
    edit_id: UUID,
    session: DatabaseSession,
    workspace: EditableWorkspace,
    store: Annotated[ObjectStore, Depends(object_store_for)],
    response: Response,
) -> CoverResponse:
    """Draw the current Revision's cover, unless it already exists or is being drawn."""
    dispatcher: JobDispatcher = request.app.state.job_dispatcher
    try:
        before = cover_state(session, access=workspace.access, edit_id=edit_id)
        state = request_cover(
            session,
            policy=admission_policy(settings_for(request), rate_limiter_for(request)),
            access=workspace.access,
            edit_id=edit_id,
            now=auth_components_for(request).now(),
        )
    except CoverNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except CoverNotDesignedError as error:
        raise ApiError(status_code=409, code="COVER_NOT_DESIGNED") from error
    except RateLimitExceededError as error:
        raise ApiError(
            status_code=429,
            code="RATE_LIMITED",
            retry_after_seconds=max(ceil(error.retry_after.total_seconds()), 1),
        ) from error
    except QuotaExceededError as error:
        raise ApiError(
            status_code=429,
            code="QUOTA_EXCEEDED",
            retry_after_seconds=max(ceil(error.retry_after.total_seconds()), 1),
        ) from error
    except ConcurrencyLimitError as error:
        raise ApiError(status_code=429, code="CONCURRENCY_LIMIT") from error
    if state.status is CoverStatus.READY:
        return _body(state, store, download=False)
    session.commit()
    admitted = state.job_id is not None and state.job_id != before.job_id
    if admitted and state.job_id is not None:
        with suppress(Exception):
            dispatcher.dispatch(
                job_id=state.job_id,
                workspace_id=workspace.access.workspace_id,
                user_id=workspace.access.user_id,
                kind=JobKind.CLIP_COVER,
            )
    response.status_code = 202
    return _body(state, store, download=False)


def _body(state: CoverState, store: ObjectStore, *, download: bool) -> CoverResponse:
    """Describe one cover without its private storage key."""
    signed = sign_cover(store, state, download=download)
    return CoverResponse(
        status=state.status,
        revision=state.revision,
        jobId=state.job_id,
        errorCode=state.error_code,
        url=None if signed is None else signed.url,
        expiresAt=None if signed is None else signed.expires_at,
    )
