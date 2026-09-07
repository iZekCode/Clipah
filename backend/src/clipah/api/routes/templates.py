"""HTTP adapters for Workspace-owned templates and their published versions."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from clipah.api.dependencies import (
    CurrentWorkspace,
    DatabaseSession,
    auth_components_for,
    require_csrf,
    require_workspace,
)
from clipah.api.errors import ApiError
from clipah.brands.models import WorkspaceTemplateDefinition
from clipah.brands.repository import BrandRepository, TemplateSummary
from clipah.brands.use_cases import (
    BrandArchivedError,
    BrandDefinitionInvalidError,
    BrandNotFoundError,
    archive_template,
    create_template,
    get_template,
    get_template_version,
    list_templates,
    update_template,
)
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["templates"])
ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_READ))
]
WritableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_WRITE))
]


class TemplateCreateRequest(BaseModel):
    """Accept one strictly shaped template publication."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1, max_length=120)
    brand_kit_id: UUID | None = Field(alias="brandKitId", default=None)
    # The definition itself is validated by the domain, which owns what a look may be.
    definition: dict[str, Any]


class TemplateUpdateRequest(BaseModel):
    """Accept a rename, a re-pointing at a Brand Kit, a new version, or any of them."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str | None = Field(default=None, min_length=1, max_length=120)
    brand_kit_id: UUID | None = Field(alias="brandKitId", default=None)
    definition: dict[str, Any] | None = None


class TemplateResponse(BaseModel):
    """One Workspace-owned look at its newest published version."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    brand_kit_id: UUID | None = Field(alias="brandKitId")
    name: str
    kind: str
    version: int
    # Typed rather than a free-form object, so the browser generates the same look the
    # backend publishes instead of hand-writing it a second time.
    definition: WorkspaceTemplateDefinition
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    archived_at: datetime | None = Field(alias="archivedAt")

    @field_serializer("created_at", "updated_at", "archived_at")
    def serialize_timestamp(self, value: datetime | None) -> str | None:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return None if value is None else value.isoformat()


class TemplateVersionResponse(BaseModel):
    """One published look version, exactly as it was published."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    template_id: UUID = Field(alias="templateId")
    version: int
    definition: WorkspaceTemplateDefinition
    created_at: datetime = Field(alias="createdAt")

    @field_serializer("created_at")
    def serialize_timestamp(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return value.isoformat()


class TemplateListResponse(BaseModel):
    """Every look this Workspace offers, newest first."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    templates: tuple[TemplateResponse, ...]


@router.post(
    "/templates",
    status_code=201,
    response_model=TemplateResponse,
    dependencies=[Depends(require_csrf)],
)
def create(
    request: Request,
    session: DatabaseSession,
    workspace: WritableWorkspace,
    body: TemplateCreateRequest,
) -> TemplateResponse:
    """Publish one Workspace-owned look at version 1."""
    try:
        summary = create_template(
            BrandRepository(session),
            access=workspace.access,
            name=body.name,
            brand_kit_id=body.brand_kit_id,
            document=body.definition,
            now=auth_components_for(request).now(),
        )
    except BrandDefinitionInvalidError as error:
        raise ApiError(status_code=422, code="BRAND_DEFINITION_INVALID") from error
    except BrandNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    session.commit()
    return _template_body(summary)


@router.get("/templates", response_model=TemplateListResponse)
def list_collection(
    session: DatabaseSession,
    workspace: ReadableWorkspace,
    include_archived: Annotated[bool, Query(alias="include_archived")] = False,
) -> TemplateListResponse:
    """List the looks this Workspace still offers, or every look it has published."""
    summaries = list_templates(
        BrandRepository(session), access=workspace.access, include_archived=include_archived
    )
    return TemplateListResponse(templates=tuple(_template_body(summary) for summary in summaries))


@router.get("/templates/{template_id}", response_model=TemplateResponse)
def read(
    template_id: UUID, session: DatabaseSession, workspace: ReadableWorkspace
) -> TemplateResponse:
    """Read one look at its newest published version."""
    try:
        summary = get_template(
            BrandRepository(session), access=workspace.access, template_id=template_id
        )
    except BrandNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return _template_body(summary)


@router.get("/templates/{template_id}/versions/{version}", response_model=TemplateVersionResponse)
def read_version(
    template_id: UUID,
    version: int,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
) -> TemplateVersionResponse:
    """Read exactly the look one composition declares it was built from."""
    try:
        stored = get_template_version(
            BrandRepository(session),
            access=workspace.access,
            template_id=template_id,
            version=version,
        )
    except BrandNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return TemplateVersionResponse(
        templateId=stored.template_id,
        version=stored.version,
        definition=WorkspaceTemplateDefinition.model_validate(stored.definition),
        createdAt=stored.created_at,
    )


@router.patch(
    "/templates/{template_id}",
    response_model=TemplateResponse,
    dependencies=[Depends(require_csrf)],
)
def update(
    request: Request,
    template_id: UUID,
    session: DatabaseSession,
    workspace: WritableWorkspace,
    body: TemplateUpdateRequest,
) -> TemplateResponse:
    """Publish the next version of one look, rename it, or re-point it at a Brand Kit."""
    try:
        summary = update_template(
            BrandRepository(session),
            access=workspace.access,
            template_id=template_id,
            name=body.name,
            brand_kit_id=body.brand_kit_id,
            document=body.definition,
            now=auth_components_for(request).now(),
        )
    except BrandNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except BrandArchivedError as error:
        raise ApiError(status_code=409, code="TEMPLATE_ARCHIVED") from error
    except BrandDefinitionInvalidError as error:
        raise ApiError(status_code=422, code="BRAND_DEFINITION_INVALID") from error
    session.commit()
    return _template_body(summary)


@router.delete("/templates/{template_id}", status_code=204, dependencies=[Depends(require_csrf)])
def archive(
    request: Request,
    template_id: UUID,
    session: DatabaseSession,
    workspace: WritableWorkspace,
) -> Response:
    """Stop offering one look without breaking the compositions that already use it."""
    try:
        archive_template(
            BrandRepository(session),
            access=workspace.access,
            template_id=template_id,
            now=auth_components_for(request).now(),
        )
    except BrandNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    session.commit()
    return Response(status_code=204)


def _template_body(summary: TemplateSummary) -> TemplateResponse:
    """Render exactly what a member is allowed to read about one look."""
    return TemplateResponse(
        id=summary.template_id,
        brandKitId=summary.brand_kit_id,
        name=summary.name,
        kind=summary.kind,
        version=summary.version,
        definition=WorkspaceTemplateDefinition.model_validate(summary.definition),
        createdAt=summary.created_at,
        updatedAt=summary.updated_at,
        archivedAt=summary.archived_at,
    )
