"""HTTP adapters for requesting, reading, and downloading one clip export."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime
from math import ceil
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
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
from clipah.jobs.admission import ConcurrencyLimitError, QuotaExceededError, admission_policy
from clipah.models import JobKind
from clipah.renders.models import RenderPreset
from clipah.renders.use_cases import (
    RenderArtifactSummary,
    RenderNotFoundError,
    get_render,
    render_download,
    request_render,
)
from clipah.source_imports.dispatch import JobDispatcher
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["renders"])
ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_READ))
]
EditableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.EDIT_WRITE))
]
IdempotencyHeader = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]


class RenderRequestBody(BaseModel):
    """The one thing a member chooses when they export a clip."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    preset: RenderPreset


class RenderResponse(BaseModel):
    """One export: the finished file, or the Job that is producing it."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    status: str
    id: UUID | None = None
    job_id: UUID | None = Field(default=None, alias="jobId")
    edit_id: UUID | None = Field(default=None, alias="editId")
    revision_id: UUID | None = Field(default=None, alias="revisionId")
    preset: RenderPreset | None = None
    duration_ms: int | None = Field(default=None, alias="durationMs")
    size_bytes: int | None = Field(default=None, alias="sizeBytes")
    created_at: datetime | None = Field(default=None, alias="createdAt")

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime | None) -> str | None:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return None if value is None else value.isoformat()


class RenderDownloadResponse(BaseModel):
    """One time-bounded capability to read a finished export."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    url: str
    expires_at: datetime = Field(alias="expiresAt")

    @field_serializer("expires_at")
    def serialize_expires_at(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return value.isoformat()


@router.post(
    "/edits/{edit_id}/renders",
    response_model=RenderResponse,
    dependencies=[Depends(require_csrf)],
)
def create(
    request: Request,
    edit_id: UUID,
    body: RenderRequestBody,
    session: DatabaseSession,
    workspace: EditableWorkspace,
    idempotency_key: IdempotencyHeader,
    response: Response,
) -> RenderResponse:
    """Hand back an identical export, or admit the durable Job that produces one."""
    dispatcher: JobDispatcher = request.app.state.job_dispatcher
    try:
        admission = request_render(
            session,
            policy=admission_policy(settings_for(request), rate_limiter_for(request)),
            access=workspace.access,
            edit_id=edit_id,
            preset=body.preset,
            idempotency_key=idempotency_key,
            now=auth_components_for(request).now(),
        )
    except RenderNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
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

    if admission.artifact is not None:
        return _artifact_body(admission.artifact)

    session.commit()
    if admission.job_id is not None:
        with suppress(Exception):
            dispatcher.dispatch(
                job_id=admission.job_id,
                workspace_id=workspace.access.workspace_id,
                user_id=workspace.access.user_id,
                kind=JobKind.RENDER,
            )
    response.status_code = 202
    return RenderResponse(status="rendering", jobId=admission.job_id)


@router.get("/renders/{render_id}", response_model=RenderResponse)
def show(
    render_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
) -> RenderResponse:
    """Read one export, hiding another Workspace's export behind the same absence."""
    try:
        artifact = get_render(session, access=workspace.access, render_id=render_id)
    except RenderNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return _artifact_body(artifact)


@router.get("/renders/{render_id}/download-url", response_model=RenderDownloadResponse)
def download(
    render_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
    store: Annotated[ObjectStore, Depends(object_store_for)],
) -> RenderDownloadResponse:
    """Sign one five-minute capability for an export this member may read."""
    try:
        signed = render_download(session, store, access=workspace.access, render_id=render_id)
    except RenderNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return RenderDownloadResponse(url=signed.url, expiresAt=signed.expires_at)


def _artifact_body(artifact: RenderArtifactSummary) -> RenderResponse:
    """Render one finished export without exposing its private storage key."""
    return RenderResponse(
        status="ready",
        id=artifact.render_id,
        editId=artifact.edit_id,
        revisionId=artifact.revision_id,
        preset=artifact.preset,
        durationMs=artifact.duration_ms,
        sizeBytes=artifact.size_bytes,
        createdAt=artifact.created_at,
    )
