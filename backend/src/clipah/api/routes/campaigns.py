"""HTTP adapters for campaign copy derived from an approved Edit Revision.

Nothing here publishes. A Campaign Output is text a member reads, copies, and decides
about; sending anything to a platform is the publication composer's job, and it starts
from a Revision a member selects there rather than from a call made here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from clipah.api.dependencies import (
    CurrentWorkspace,
    DatabaseSession,
    require_csrf,
    require_workspace,
    settings_for,
)
from clipah.api.errors import ApiError
from clipah.campaigns.models import CampaignLanguage
from clipah.campaigns.repository import CampaignOutputSummary
from clipah.campaigns.use_cases import (
    CampaignApprovalRequiredError,
    CampaignTargetNotFoundError,
    generate_outputs,
    list_outputs,
)
from clipah.variants.models import Platform
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["campaigns"])
ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_READ))
]
WritableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.EDIT_WRITE))
]


class CampaignRequestBody(BaseModel):
    """The three choices a member makes: which cut, for where, and in what language."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # Named rather than implied: copy is derived from one exact immutable Revision, so a
    # save that lands between reading and asking cannot silently change what it describes.
    revision: int = Field(ge=1)
    platforms: tuple[Platform, ...] = Field(min_length=1, max_length=3)
    languages: tuple[CampaignLanguage, ...] = Field(min_length=1, max_length=2)


class ThumbnailBriefResponse(BaseModel):
    """What a thumbnail has to carry, as the person making it reads it."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    text: str
    visual_direction: str = Field(alias="visualDirection")
    avoid: tuple[str, ...]


class CampaignWarningResponse(BaseModel):
    """One caveat a member reads beside the copy, never instead of it."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: str
    detail: str


class CampaignModelMetadataResponse(BaseModel):
    """What produced this copy, so a member can tell how much to trust it."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    generator: str
    version: int
    deterministic: bool
    provider: str | None = None
    model: str | None = None


class CampaignOutputResponse(BaseModel):
    """One complete piece of supporting copy, for one destination in one language."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    revision: int
    platform: str
    language: str
    title: str
    post_copy: str = Field(alias="postCopy")
    cta: str
    hashtags: tuple[str, ...]
    thumbnail_brief: ThumbnailBriefResponse = Field(alias="thumbnailBrief")
    warnings: tuple[CampaignWarningResponse, ...]
    model_metadata: CampaignModelMetadataResponse = Field(alias="modelMetadata")
    created_at: datetime = Field(alias="createdAt")

    @field_serializer("created_at")
    def serialize_timestamp(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return value.isoformat()


class CampaignOutputListResponse(BaseModel):
    """The copy derived from one Edit, ordered so two readings can be compared."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    campaign_outputs: tuple[CampaignOutputResponse, ...] = Field(alias="campaignOutputs")


@router.post(
    "/edits/{edit_id}/campaign-outputs",
    status_code=201,
    response_model=CampaignOutputListResponse,
    dependencies=[Depends(require_csrf)],
)
def create(
    request: Request,
    edit_id: UUID,
    body: CampaignRequestBody,
    session: DatabaseSession,
    workspace: WritableWorkspace,
) -> CampaignOutputListResponse:
    """Derive supporting copy from one exact immutable Revision of this Edit."""
    try:
        outputs = generate_outputs(
            session,
            access=workspace.access,
            edit_id=edit_id,
            revision=body.revision,
            platforms=body.platforms,
            languages=body.languages,
            require_approval=settings_for(request).collaboration_enabled,
        )
    except CampaignTargetNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except CampaignApprovalRequiredError as error:
        raise ApiError(status_code=409, code="REVIEW_APPROVAL_REQUIRED") from error
    session.commit()
    return _list_body(outputs)


@router.get("/edits/{edit_id}/campaign-outputs", response_model=CampaignOutputListResponse)
def list_collection(
    edit_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
    revision: Annotated[int, Query(ge=1)] = 1,
) -> CampaignOutputListResponse:
    """List the copy already derived from this Edit's Revisions."""
    try:
        outputs = list_outputs(session, access=workspace.access, edit_id=edit_id, revision=revision)
    except CampaignTargetNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return _list_body(outputs)


def _list_body(outputs: tuple[CampaignOutputSummary, ...]) -> CampaignOutputListResponse:
    """Render every piece of copy a member is allowed to read."""
    return CampaignOutputListResponse(
        campaignOutputs=tuple(_output_body(output) for output in outputs)
    )


def _output_body(output: CampaignOutputSummary) -> CampaignOutputResponse:
    """Render one stored output without trusting the shape it was stored in."""
    brief = output.thumbnail_brief
    metadata = output.model_metadata
    return CampaignOutputResponse(
        id=output.output_id,
        revision=output.revision,
        platform=output.platform.value,
        language=output.language.value,
        title=output.title,
        postCopy=output.post_copy,
        cta=output.cta,
        hashtags=output.hashtags,
        thumbnailBrief=ThumbnailBriefResponse(
            text=str(brief.get("text", "")),
            visualDirection=str(brief.get("visual_direction", "")),
            avoid=_strings(brief.get("avoid")),
        ),
        warnings=tuple(
            CampaignWarningResponse(
                type=str(warning.get("type", "")), detail=str(warning.get("detail", ""))
            )
            for warning in output.warnings
        ),
        modelMetadata=CampaignModelMetadataResponse(
            generator=str(metadata.get("generator", "")),
            version=int(_number(metadata.get("version"))),
            deterministic=bool(metadata.get("deterministic", False)),
            provider=_optional(metadata.get("provider")),
            model=_optional(metadata.get("model")),
        ),
        createdAt=output.created_at,
    )


def _strings(value: object) -> tuple[str, ...]:
    """Read a stored list without trusting that it is still a list of strings."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value)


def _number(value: object) -> float:
    """Read one stored number without trusting its stored type."""
    return float(value) if isinstance(value, (int, float)) else 0.0


def _optional(value: object) -> str | None:
    """Read one optional stored string."""
    return None if value is None else str(value)
