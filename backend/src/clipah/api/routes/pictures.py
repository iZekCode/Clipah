"""HTTP adapter for a member's own picture, such as a logo, uploaded into one Project."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from clipah.api.dependencies import (
    CurrentWorkspace,
    DatabaseSession,
    object_store_for,
    require_csrf,
    require_workspace,
)
from clipah.api.errors import ApiError
from clipah.api.routes.assets import ProjectAssetResponse
from clipah.assets.library import ProjectNotFoundError
from clipah.assets.pictures import (
    MAX_PICTURE_BYTES,
    PictureInvalidError,
    PictureTooLargeError,
    store_picture,
)
from clipah.assets.storage import ObjectStore
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["pictures"])
EditableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.EDIT_WRITE))
]


async def picture_bytes(request: Request) -> bytes:
    """Read the request body, refusing it as soon as it passes the picture bound.

    The declared length is not trusted; the body is counted as it arrives, so a client
    that lies about it is refused at the same point as one that tells the truth.
    """
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_PICTURE_BYTES:
        raise ApiError(status_code=413, code="PICTURE_TOO_LARGE")
    received = bytearray()
    async for chunk in request.stream():
        received.extend(chunk)
        if len(received) > MAX_PICTURE_BYTES:
            raise ApiError(status_code=413, code="PICTURE_TOO_LARGE")
    return bytes(received)


@router.post(
    "/projects/{project_id}/pictures",
    response_model=ProjectAssetResponse,
    status_code=201,
    dependencies=[Depends(require_csrf)],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
def create(
    project_id: UUID,
    session: DatabaseSession,
    workspace: EditableWorkspace,
    store: Annotated[ObjectStore, Depends(object_store_for)],
    data: Annotated[bytes, Depends(picture_bytes)],
) -> ProjectAssetResponse:
    """Keep one picture in this Project, so its clips can be marked with it."""
    try:
        asset = store_picture(
            session, store, access=workspace.access, project_id=project_id, data=data
        )
    except ProjectNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except PictureTooLargeError as error:
        raise ApiError(status_code=413, code="PICTURE_TOO_LARGE") from error
    except PictureInvalidError as error:
        raise ApiError(status_code=422, code="PICTURE_INVALID") from error
    session.commit()
    return ProjectAssetResponse(
        id=asset.asset_id,
        kind=asset.kind.value,
        contentType=asset.content_type,
        sizeBytes=asset.size_bytes,
        durationMs=asset.duration_ms,
        width=asset.width,
        height=asset.height,
        createdAt=asset.created_at,
    )
