"""HTTP adapters for Workspace Brand Kits and their published versions."""

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
from clipah.brands.models import BrandKitDefinition
from clipah.brands.repository import BrandKitSummary, BrandRepository
from clipah.brands.use_cases import (
    BrandArchivedError,
    BrandAssetForbiddenError,
    BrandDefinitionInvalidError,
    BrandNotFoundError,
    archive_brand_kit,
    create_brand_kit,
    get_brand_kit,
    get_brand_kit_version,
    list_brand_kits,
    update_brand_kit,
)
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["brand-kits"])
ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_READ))
]
WritableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_WRITE))
]


class BrandKitCreateRequest(BaseModel):
    """Accept one strictly shaped Brand Kit publication."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1, max_length=120)
    # The definition itself is validated by the domain, which owns what a rule may be.
    definition: dict[str, Any]


class BrandKitUpdateRequest(BaseModel):
    """Accept a rename, a new version of the rules, or both."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str | None = Field(default=None, min_length=1, max_length=120)
    definition: dict[str, Any] | None = None


class BrandKitResponse(BaseModel):
    """One Brand Kit at its newest published version."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    name: str
    version: int
    # Typed rather than a free-form object, so the browser generates the same rules the
    # backend enforces instead of hand-writing them a second time.
    definition: BrandKitDefinition
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    archived_at: datetime | None = Field(alias="archivedAt")

    @field_serializer("created_at", "updated_at", "archived_at")
    def serialize_timestamp(self, value: datetime | None) -> str | None:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return None if value is None else value.isoformat()


class BrandKitVersionResponse(BaseModel):
    """One published version, exactly as it was published."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    brand_kit_id: UUID = Field(alias="brandKitId")
    version: int
    definition: BrandKitDefinition
    created_at: datetime = Field(alias="createdAt")

    @field_serializer("created_at")
    def serialize_timestamp(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return value.isoformat()


class BrandKitListResponse(BaseModel):
    """Every Brand Kit this Workspace offers, newest first."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    brand_kits: tuple[BrandKitResponse, ...] = Field(alias="brandKits")


@router.post(
    "/brand-kits",
    status_code=201,
    response_model=BrandKitResponse,
    dependencies=[Depends(require_csrf)],
)
def create(
    request: Request,
    session: DatabaseSession,
    workspace: WritableWorkspace,
    body: BrandKitCreateRequest,
) -> BrandKitResponse:
    """Publish one Brand Kit and the first version of its rules."""
    try:
        summary = create_brand_kit(
            BrandRepository(session),
            access=workspace.access,
            name=body.name,
            document=body.definition,
            now=auth_components_for(request).now(),
        )
    except BrandDefinitionInvalidError as error:
        raise ApiError(status_code=422, code="BRAND_DEFINITION_INVALID") from error
    except BrandAssetForbiddenError as error:
        raise ApiError(status_code=422, code="BRAND_ASSET_FORBIDDEN") from error
    session.commit()
    return _kit_body(summary)


@router.get("/brand-kits", response_model=BrandKitListResponse)
def list_collection(
    session: DatabaseSession,
    workspace: ReadableWorkspace,
    include_archived: Annotated[bool, Query(alias="include_archived")] = False,
) -> BrandKitListResponse:
    """List the kits this Workspace still uses, or every kit it has published."""
    summaries = list_brand_kits(
        BrandRepository(session), access=workspace.access, include_archived=include_archived
    )
    return BrandKitListResponse(brandKits=tuple(_kit_body(summary) for summary in summaries))


@router.get("/brand-kits/{brand_kit_id}", response_model=BrandKitResponse)
def read(
    brand_kit_id: UUID, session: DatabaseSession, workspace: ReadableWorkspace
) -> BrandKitResponse:
    """Read one Brand Kit at its newest published version."""
    try:
        summary = get_brand_kit(
            BrandRepository(session), access=workspace.access, brand_kit_id=brand_kit_id
        )
    except BrandNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return _kit_body(summary)


@router.get("/brand-kits/{brand_kit_id}/versions/{version}", response_model=BrandKitVersionResponse)
def read_version(
    brand_kit_id: UUID,
    version: int,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
) -> BrandKitVersionResponse:
    """Read exactly the rules one composition declares it was judged against."""
    try:
        stored = get_brand_kit_version(
            BrandRepository(session),
            access=workspace.access,
            brand_kit_id=brand_kit_id,
            version=version,
        )
    except BrandNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return BrandKitVersionResponse(
        brandKitId=stored.brand_kit_id,
        version=stored.version,
        definition=BrandKitDefinition.model_validate(stored.definition),
        createdAt=stored.created_at,
    )


@router.patch(
    "/brand-kits/{brand_kit_id}",
    response_model=BrandKitResponse,
    dependencies=[Depends(require_csrf)],
)
def update(
    request: Request,
    brand_kit_id: UUID,
    session: DatabaseSession,
    workspace: WritableWorkspace,
    body: BrandKitUpdateRequest,
) -> BrandKitResponse:
    """Rename one kit, publish its next version of rules, or both."""
    try:
        summary = update_brand_kit(
            BrandRepository(session),
            access=workspace.access,
            brand_kit_id=brand_kit_id,
            name=body.name,
            document=body.definition,
            now=auth_components_for(request).now(),
        )
    except BrandNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except BrandArchivedError as error:
        raise ApiError(status_code=409, code="BRAND_KIT_ARCHIVED") from error
    except BrandDefinitionInvalidError as error:
        raise ApiError(status_code=422, code="BRAND_DEFINITION_INVALID") from error
    except BrandAssetForbiddenError as error:
        raise ApiError(status_code=422, code="BRAND_ASSET_FORBIDDEN") from error
    session.commit()
    return _kit_body(summary)


@router.delete("/brand-kits/{brand_kit_id}", status_code=204, dependencies=[Depends(require_csrf)])
def archive(
    request: Request,
    brand_kit_id: UUID,
    session: DatabaseSession,
    workspace: WritableWorkspace,
) -> Response:
    """Stop offering one kit without destroying the versions clips were judged against."""
    try:
        archive_brand_kit(
            BrandRepository(session),
            access=workspace.access,
            brand_kit_id=brand_kit_id,
            now=auth_components_for(request).now(),
        )
    except BrandNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    session.commit()
    return Response(status_code=204)


def _kit_body(summary: BrandKitSummary) -> BrandKitResponse:
    """Render exactly what a member is allowed to read about one kit."""
    return BrandKitResponse(
        id=summary.brand_kit_id,
        name=summary.name,
        version=summary.version,
        definition=BrandKitDefinition.model_validate(summary.definition),
        createdAt=summary.created_at,
        updatedAt=summary.updated_at,
        archivedAt=summary.archived_at,
    )
