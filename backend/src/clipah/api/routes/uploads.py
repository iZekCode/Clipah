"""HTTP adapters for Workspace-authorized direct multipart source-media upload."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from clipah.api.dependencies import (
    CurrentWorkspace,
    DatabaseSession,
    auth_components_for,
    object_store_for,
    require_csrf,
    require_workspace,
)
from clipah.api.errors import ApiError
from clipah.assets.storage import CompletedPart, ObjectStore
from clipah.assets.uploads import (
    MAX_UPLOAD_BYTES,
    CreateUploadCommand,
    UploadConflictError,
    UploadNotFoundError,
    UploadValidationError,
    abort_upload,
    complete_upload,
    create_upload,
    sign_part,
)
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["uploads"])

WritableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_WRITE))
]
PartNumber = Annotated[int, Path(ge=1, le=10_000)]


class CreateUploadRequest(BaseModel):
    """Explicit client display and content metadata for one source-media upload intent."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    filename: Annotated[str, Field(min_length=1, max_length=1024)]
    content_type: Annotated[str, Field(alias="contentType", min_length=1, max_length=255)]
    content_length: Annotated[int, Field(alias="contentLength", gt=0, le=MAX_UPLOAD_BYTES)]


class CompletedPartRequest(BaseModel):
    """Public completion proof containing a part number and provider opaque ETag."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    part_number: Annotated[int, Field(alias="partNumber", ge=1, le=10_000)]
    etag: Annotated[str, Field(min_length=1, max_length=1024)]


class CompleteUploadRequest(BaseModel):
    """Completion body containing every part selected by the browser multipart client."""

    model_config = ConfigDict(extra="forbid")

    parts: Annotated[list[CompletedPartRequest], Field(min_length=1)]


@router.post(
    "/projects/{project_id}/uploads", status_code=201, dependencies=[Depends(require_csrf)]
)
def create(
    request: Request,
    project_id: UUID,
    session: DatabaseSession,
    workspace: WritableWorkspace,
    payload: CreateUploadRequest,
    store: Annotated[ObjectStore, Depends(object_store_for)],
) -> dict[str, str]:
    """Create a durable source upload without returning private storage identifiers."""
    try:
        created = create_upload(
            session,
            store,
            access=workspace.access,
            project_id=project_id,
            command=CreateUploadCommand(
                filename=payload.filename,
                content_type=payload.content_type,
                content_length=payload.content_length,
            ),
            now=auth_components_for(request).now(),
        )
    except UploadNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except UploadValidationError as error:
        raise ApiError(status_code=422, code="VALIDATION_ERROR") from error
    return {"id": str(created.upload_id), "expiresAt": created.expires_at.isoformat()}


@router.post(
    "/projects/{project_id}/uploads/{upload_id}/parts/{part_number}",
    dependencies=[Depends(require_csrf)],
)
def sign_part_route(
    request: Request,
    project_id: UUID,
    upload_id: UUID,
    part_number: PartNumber,
    session: DatabaseSession,
    workspace: WritableWorkspace,
    store: Annotated[ObjectStore, Depends(object_store_for)],
) -> dict[str, str | int]:
    """Issue one five-minute PUT capability only for an active authorized upload part."""
    try:
        signed = sign_part(
            session,
            store,
            access=workspace.access,
            project_id=project_id,
            upload_id=upload_id,
            part_number=part_number,
            now=auth_components_for(request).now(),
        )
    except UploadNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except UploadConflictError as error:
        raise ApiError(status_code=409, code="CONFLICT") from error
    except UploadValidationError as error:
        raise ApiError(status_code=422, code="VALIDATION_ERROR") from error
    return {
        "partNumber": part_number,
        "url": signed.url,
        "expiresAt": signed.expires_at.isoformat(),
    }


@router.post(
    "/projects/{project_id}/uploads/{upload_id}/complete",
    dependencies=[Depends(require_csrf)],
)
def complete(
    request: Request,
    project_id: UUID,
    upload_id: UUID,
    session: DatabaseSession,
    workspace: WritableWorkspace,
    payload: CompleteUploadRequest,
    store: Annotated[ObjectStore, Depends(object_store_for)],
) -> dict[str, str | int]:
    """Finalize one active upload, verify metadata, and return a five-minute download URL."""
    try:
        completed = complete_upload(
            session,
            store,
            access=workspace.access,
            project_id=project_id,
            upload_id=upload_id,
            parts=[
                CompletedPart(part_number=part.part_number, etag=part.etag)
                for part in payload.parts
            ],
            now=auth_components_for(request).now(),
        )
    except UploadNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except UploadConflictError as error:
        raise ApiError(status_code=409, code="CONFLICT") from error
    except UploadValidationError as error:
        raise ApiError(status_code=422, code="VALIDATION_ERROR") from error
    return {
        "id": str(completed.upload_id),
        "contentType": completed.object.content_type,
        "contentLength": completed.object.content_length,
        "downloadUrl": completed.download.url,
        "downloadExpiresAt": completed.download.expires_at.isoformat(),
    }


@router.delete(
    "/projects/{project_id}/uploads/{upload_id}",
    status_code=204,
    dependencies=[Depends(require_csrf)],
)
def abort(
    request: Request,
    project_id: UUID,
    upload_id: UUID,
    session: DatabaseSession,
    workspace: WritableWorkspace,
    store: Annotated[ObjectStore, Depends(object_store_for)],
) -> Response:
    """Abort only the recorded provider upload after Workspace and Project authorization."""
    try:
        abort_upload(
            session,
            store,
            access=workspace.access,
            project_id=project_id,
            upload_id=upload_id,
            now=auth_components_for(request).now(),
        )
    except UploadNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except UploadConflictError as error:
        raise ApiError(status_code=409, code="CONFLICT") from error
    return Response(status_code=204)
