"""HTTP adapter for the bounded proxy capability a reviewer plays candidates against."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from clipah.api.dependencies import (
    CurrentWorkspace,
    DatabaseSession,
    object_store_for,
    require_workspace,
)
from clipah.api.errors import ApiError
from clipah.assets.playback import ProxyNotFoundError, proxy_playback
from clipah.assets.storage import ObjectStore
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["playback"])
ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_READ))
]


class ProxyPlaybackResponse(BaseModel):
    """One expiring proxy URL and the media shape a player needs before it loads."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    url: str
    expires_at: datetime = Field(alias="expiresAt")
    content_type: str = Field(alias="contentType")
    duration_ms: int | None = Field(alias="durationMs")
    width: int | None
    height: int | None

    @field_serializer("expires_at")
    def serialize_expires_at(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return value.isoformat()


@router.get("/projects/{project_id}/proxy", response_model=ProxyPlaybackResponse)
def show(
    project_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
    store: Annotated[ObjectStore, Depends(object_store_for)],
) -> ProxyPlaybackResponse:
    """Sign one five-minute proxy capability for a Project this member may read."""
    try:
        playback = proxy_playback(session, store, access=workspace.access, project_id=project_id)
    except ProxyNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return ProxyPlaybackResponse(
        url=playback.download.url,
        expiresAt=playback.download.expires_at,
        contentType=playback.content_type,
        durationMs=playback.duration_ms,
        width=playback.width,
        height=playback.height,
    )
