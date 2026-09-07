"""HTTP adapters for context-safe Clip Variants."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from clipah.api.dependencies import (
    CurrentWorkspace,
    DatabaseSession,
    context_assessor_for,
    require_csrf,
    require_workspace,
)
from clipah.api.errors import ApiError
from clipah.variants.models import SUPPORTED_TARGET_DURATIONS_MS, Platform
from clipah.variants.repository import VariantSummary
from clipah.variants.use_cases import (
    VariantRequestInvalidError,
    VariantTargetNotFoundError,
    generate_clip_variants,
    list_clip_variants,
)
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["variants"])
ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_READ))
]
WritableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.EDIT_WRITE))
]


class VariantRequestBody(BaseModel):
    """The two choices a member makes: how long, and for where."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    platforms: tuple[Platform, ...] = Field(min_length=1, max_length=3)
    durations_ms: tuple[int, ...] = Field(alias="durationsMs", min_length=1, max_length=5)


class ContextWarningResponse(BaseModel):
    """One evidenced observation about a boundary, as a reviewer reads it."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: str
    severity: str
    evidence_word_ids: tuple[str, ...] = Field(alias="evidenceWordIds")
    suggested_start_word_id: str | None = Field(alias="suggestedStartWordId", default=None)
    suggested_end_word_id: str | None = Field(alias="suggestedEndWordId", default=None)


class PlatformPackagingResponse(BaseModel):
    """What one destination asks of a clip, before anyone tries to publish it."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    platform: str
    aspect_ratio: str = Field(alias="aspectRatio")
    safe_area_top_percent: float = Field(alias="safeAreaTopPercent")
    safe_area_bottom_percent: float = Field(alias="safeAreaBottomPercent")
    safe_area_horizontal_percent: float = Field(alias="safeAreaHorizontalPercent")
    max_title_characters: int = Field(alias="maxTitleCharacters")
    caption_style: str = Field(alias="captionStyle")
    export_preset: str = Field(alias="exportPreset")


class ClipVariantResponse(BaseModel):
    """One proposed alternative cut, with the evidence a member decides on."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    candidate_id: UUID = Field(alias="candidateId")
    hook_strategy: str = Field(alias="hookStrategy")
    platform: str
    target_duration_ms: int = Field(alias="targetDurationMs")
    start_word_id: str = Field(alias="startWordId")
    end_word_id: str = Field(alias="endWordId")
    start_ms: int = Field(alias="startMs")
    end_ms: int = Field(alias="endMs")
    duration_ms: int = Field(alias="durationMs")
    title: str
    rationale: str
    warnings: tuple[ContextWarningResponse, ...]
    packaging: PlatformPackagingResponse
    created_at: datetime = Field(alias="createdAt")

    @field_serializer("created_at")
    def serialize_timestamp(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return value.isoformat()


class ClipVariantListResponse(BaseModel):
    """One candidate's variants, ordered so two readings can be compared."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    variants: tuple[ClipVariantResponse, ...]
    supported_durations_ms: tuple[int, ...] = Field(
        alias="supportedDurationsMs", default=SUPPORTED_TARGET_DURATIONS_MS
    )


@router.post(
    "/projects/{project_id}/candidates/{candidate_id}/variants",
    status_code=201,
    response_model=ClipVariantListResponse,
    dependencies=[Depends(require_csrf)],
)
def create_variants(
    request: Request,
    project_id: UUID,
    candidate_id: UUID,
    body: VariantRequestBody,
    session: DatabaseSession,
    workspace: WritableWorkspace,
) -> ClipVariantListResponse:
    """Offer every honest cut of one candidate at the requested lengths and platforms."""
    try:
        variants = generate_clip_variants(
            session,
            access=workspace.access,
            project_id=project_id,
            candidate_id=candidate_id,
            platforms=body.platforms,
            durations_ms=body.durations_ms,
            assessor=context_assessor_for(request),
        )
    except VariantTargetNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except VariantRequestInvalidError as error:
        raise ApiError(status_code=422, code="VARIANT_REQUEST_INVALID") from error

    session.commit()
    return _list_body(variants)


@router.get(
    "/projects/{project_id}/candidates/{candidate_id}/variants",
    response_model=ClipVariantListResponse,
)
def list_collection(
    project_id: UUID,
    candidate_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
) -> ClipVariantListResponse:
    """List the variants already offered for one candidate."""
    try:
        variants = list_clip_variants(
            session,
            access=workspace.access,
            project_id=project_id,
            candidate_id=candidate_id,
        )
    except VariantTargetNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return _list_body(variants)


def _list_body(variants: tuple[VariantSummary, ...]) -> ClipVariantListResponse:
    """Render one candidate's variants and the targets a member may still ask for."""
    return ClipVariantListResponse(
        variants=tuple(_variant_body(variant) for variant in variants),
        supportedDurationsMs=SUPPORTED_TARGET_DURATIONS_MS,
    )


def _variant_body(variant: VariantSummary) -> ClipVariantResponse:
    """Render exactly the Variant evidence a reviewer is allowed to inspect."""
    return ClipVariantResponse(
        id=variant.variant_id,
        candidateId=variant.candidate_id,
        hookStrategy=variant.hook_strategy,
        platform=variant.platform,
        targetDurationMs=variant.target_duration_ms,
        startWordId=variant.start_word_id,
        endWordId=variant.end_word_id,
        startMs=variant.start_ms,
        endMs=variant.end_ms,
        durationMs=variant.end_ms - variant.start_ms,
        title=variant.title,
        rationale=variant.rationale,
        warnings=tuple(_warning_body(warning) for warning in variant.warnings),
        packaging=_packaging_body(variant.packaging),
        createdAt=variant.created_at,
    )


def _warning_body(warning: dict[str, object]) -> ContextWarningResponse:
    """Render one stored warning without trusting its stored shape."""
    return ContextWarningResponse(
        type=str(warning["type"]),
        severity=str(warning["severity"]),
        evidenceWordIds=_word_ids(warning.get("evidence_word_ids")),
        suggestedStartWordId=_optional(warning.get("suggested_start_word_id")),
        suggestedEndWordId=_optional(warning.get("suggested_end_word_id")),
    )


def _packaging_body(packaging: dict[str, object]) -> PlatformPackagingResponse:
    """Render what the destination asked of this cut."""
    return PlatformPackagingResponse(
        platform=str(packaging["platform"]),
        aspectRatio=str(packaging["aspect_ratio"]),
        safeAreaTopPercent=_number(packaging["safe_area_top_percent"]),
        safeAreaBottomPercent=_number(packaging["safe_area_bottom_percent"]),
        safeAreaHorizontalPercent=_number(packaging["safe_area_horizontal_percent"]),
        maxTitleCharacters=int(_number(packaging["max_title_characters"])),
        captionStyle=str(packaging["caption_style"]),
        exportPreset=str(packaging["export_preset"]),
    )


def _number(value: object) -> float:
    """Read one stored measurement without trusting its stored type."""
    return float(value) if isinstance(value, (int, float)) else 0.0


def _word_ids(value: object) -> tuple[str, ...]:
    """Read the stored evidence without trusting that it is still a list of strings."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value)


def _optional(value: object) -> str | None:
    """Read one optional stored word ID."""
    return None if value is None else str(value)
