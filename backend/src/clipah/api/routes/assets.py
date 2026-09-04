"""HTTP adapter for the Project media the editor's assets panel may place."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from clipah.api.dependencies import CurrentWorkspace, DatabaseSession, require_workspace
from clipah.api.errors import ApiError
from clipah.assets.library import ProjectNotFoundError, project_assets
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["assets"])
ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_READ))
]


class ProjectAssetResponse(BaseModel):
    """One asset of a Project, in the shape the editor needs to place it."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    kind: str
    content_type: str = Field(alias="contentType")
    size_bytes: int = Field(alias="sizeBytes")
    duration_ms: int | None = Field(alias="durationMs")
    width: int | None
    height: int | None
    created_at: datetime = Field(alias="createdAt")

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return value.isoformat()


class ProjectAssetsResponse(BaseModel):
    """Every asset one Project holds that a composition is allowed to name."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    assets: list[ProjectAssetResponse]


@router.get("/projects/{project_id}/assets", response_model=ProjectAssetsResponse)
def index(
    project_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
) -> ProjectAssetsResponse:
    """List the placeable media of one Project this member may read."""
    try:
        assets = project_assets(session, access=workspace.access, project_id=project_id)
    except ProjectNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return ProjectAssetsResponse(
        assets=[
            ProjectAssetResponse(
                id=asset.asset_id,
                kind=asset.kind.value,
                contentType=asset.content_type,
                sizeBytes=asset.size_bytes,
                durationMs=asset.duration_ms,
                width=asset.width,
                height=asset.height,
                createdAt=asset.created_at,
            )
            for asset in assets
        ]
    )
