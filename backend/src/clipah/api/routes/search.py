"""HTTP adapter for one Workspace's searchable content library."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from clipah.api.dependencies import CurrentWorkspace, DatabaseSession, require_workspace
from clipah.api.errors import ApiError
from clipah.search.models import (
    ExportState,
    HitBoundary,
    SearchEntityType,
    SearchHit,
    SearchLanguage,
    SearchQuery,
)
from clipah.search.use_cases import MAX_PAGE_SIZE, SearchQueryError, search_content
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["search"])
ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_READ))
]


class SearchFragmentResponse(BaseModel):
    """One run of result text, and whether it is the run that matched.

    The server marks a match; it never formats one. Provider and speaker text reaches the
    browser as text, and the client decides what a highlight looks like.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    text: str
    highlighted: bool


class SearchResultResponse(BaseModel):
    """One strict allowlist of the evidence a search result is allowed to carry."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    type: SearchEntityType
    entity_id: UUID = Field(alias="entityId")
    project_id: UUID = Field(alias="projectId")
    project_name: str = Field(alias="projectName")
    title: str
    fragments: tuple[SearchFragmentResponse, ...]
    speaker: str | None
    topics: tuple[str, ...]
    tags: tuple[str, ...]
    language: SearchLanguage
    start_ms: int | None = Field(alias="startMs")
    end_ms: int | None = Field(alias="endMs")
    export_state: ExportState = Field(alias="exportState")
    deep_link: str = Field(alias="deepLink")
    created_at: datetime = Field(alias="createdAt")
    score: float

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return value.isoformat()


class SearchPageResponse(BaseModel):
    """One ranked page of library results and its opaque continuation cursor."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    results: tuple[SearchResultResponse, ...]
    next_cursor: str | None = Field(alias="nextCursor")


@router.get("/search", response_model=SearchPageResponse)
def search(
    session: DatabaseSession,
    workspace: ReadableWorkspace,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    entity_types: Annotated[list[SearchEntityType] | None, Query(alias="type")] = None,
    project_id: Annotated[UUID | None, Query(alias="projectId")] = None,
    speaker: str | None = None,
    topic: str | None = None,
    language: SearchLanguage | None = None,
    export_state: Annotated[ExportState | None, Query(alias="exportState")] = None,
    created_after: Annotated[datetime | None, Query(alias="createdAfter")] = None,
    created_before: Annotated[datetime | None, Query(alias="createdBefore")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    cursor: str | None = None,
) -> SearchPageResponse:
    """Search everything one Workspace has made, in the order it answers the question."""
    try:
        page = search_content(
            session,
            access=workspace.access,
            query=SearchQuery(
                text=q,
                entity_types=tuple(entity_types or ()),
                project_id=project_id,
                speaker=speaker,
                topic=topic,
                language=language,
                export_state=export_state,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
                after=_decode_cursor(cursor) if cursor is not None else None,
            ),
        )
    except SearchQueryError as error:
        raise ApiError(status_code=422, code="VALIDATION_ERROR") from error
    except ValueError as error:
        raise ApiError(status_code=422, code="VALIDATION_ERROR") from error
    return SearchPageResponse(
        results=tuple(_result_body(hit) for hit in page.hits),
        nextCursor=(None if page.next_boundary is None else _encode_cursor(page.next_boundary)),
    )


def _result_body(hit: SearchHit) -> SearchResultResponse:
    """Render one ranked hit into the public camelCase representation."""
    return SearchResultResponse(
        id=hit.document_id,
        type=hit.entity_type,
        entityId=hit.entity_id,
        projectId=hit.project_id,
        projectName=hit.project_name,
        title=hit.title,
        fragments=tuple(
            SearchFragmentResponse(text=fragment.text, highlighted=fragment.highlighted)
            for fragment in hit.fragments
        ),
        speaker=hit.speaker,
        topics=hit.topics,
        tags=hit.tags,
        language=hit.language,
        startMs=hit.start_ms,
        endMs=hit.end_ms,
        exportState=hit.export_state,
        deepLink=hit.deep_link,
        createdAt=hit.created_at,
        score=hit.score,
    )


def _encode_cursor(boundary: HitBoundary) -> str:
    """Encode a score and UUID as an opaque URL-safe pagination boundary."""
    raw = f"{boundary.score!r}|{boundary.document_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> HitBoundary:
    """Decode one opaque cursor, or refuse it rather than restarting from the top."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        score, document_id = base64.urlsafe_b64decode(padded).decode().split("|", 1)
        return HitBoundary(score=float(score), document_id=UUID(document_id))
    except (ValueError, UnicodeDecodeError, binascii.Error) as error:
        raise ApiError(status_code=422, code="VALIDATION_ERROR") from error
