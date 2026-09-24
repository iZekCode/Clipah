"""HTTP adapters for the creator studio's browsing reads.

Clips, exports, and library media are each browsable across one Workspace, and each
browsable thing can be previewed through a short-lived capability. Nothing here writes,
and nothing here exposes a storage key, provider output, or another Workspace's row.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from clipah.api.dependencies import (
    CurrentWorkspace,
    DatabaseSession,
    object_store_for,
    require_workspace,
)
from clipah.api.errors import ApiError
from clipah.api.routes.candidates import CandidateResponse, candidate_body
from clipah.assets.storage import ObjectStore
from clipah.highlights.models import ClipCategory
from clipah.models import AssetKind, AssetSourceType
from clipah.studio.use_cases import (
    ClipBoundary,
    ClipOrder,
    ClipStage,
    ClipSummary,
    ExportListState,
    ExportSummary,
    LibraryAssetSummary,
    MediaPreview,
    StudioNotFoundError,
    TimeBoundary,
    asset_preview,
    browse_assets,
    browse_clips,
    clip_detail,
    list_exports,
    project_posters,
    project_storyboard,
    project_thumbnail,
    project_transcript,
    project_waveform,
)
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["studio"])
ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_READ))
]
PageLimit = Annotated[int, Query(ge=1, le=100)]


def _timestamp(value: datetime) -> str:
    """Preserve the API's established explicit UTC-offset timestamp shape."""
    return value.isoformat()


class ClipSummaryResponse(BaseModel):
    """One browsable moment, the Project it belongs to, and how far it has got."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    project_id: UUID = Field(alias="projectId")
    project_name: str = Field(alias="projectName")
    rank: int
    score: float
    hook: str
    reason: str
    category: ClipCategory
    start_ms: int = Field(alias="startMs")
    end_ms: int = Field(alias="endMs")
    duration_ms: int = Field(alias="durationMs")
    stage: ClipStage
    edit_id: UUID | None = Field(alias="editId")
    current_revision: int | None = Field(alias="currentRevision")
    export_count: int = Field(alias="exportCount")
    created_at: datetime = Field(alias="createdAt")
    edit_updated_at: datetime | None = Field(alias="editUpdatedAt")

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return _timestamp(value)

    @field_serializer("edit_updated_at")
    def serialize_edit_updated_at(self, value: datetime | None) -> str | None:
        """When the clip's Edit was last saved, or nothing if it has none."""
        return None if value is None else _timestamp(value)


class ClipPageResponse(BaseModel):
    """One page of browsable moments and its opaque continuation cursor."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    clips: tuple[ClipSummaryResponse, ...]
    next_cursor: str | None = Field(alias="nextCursor")


class ExportResponse(BaseModel):
    """One requested export: the finished file, or the state of the Job making it."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    job_id: UUID = Field(alias="jobId")
    status: str
    error_code: str | None = Field(alias="errorCode")
    preset: str
    project_id: UUID = Field(alias="projectId")
    project_name: str = Field(alias="projectName")
    candidate_id: UUID = Field(alias="candidateId")
    clip_title: str = Field(alias="clipTitle")
    edit_id: UUID = Field(alias="editId")
    revision_id: UUID = Field(alias="revisionId")
    revision: int
    render_id: UUID | None = Field(alias="renderId")
    duration_ms: int | None = Field(alias="durationMs")
    size_bytes: int | None = Field(alias="sizeBytes")
    created_at: datetime = Field(alias="createdAt")
    completed_at: datetime | None = Field(alias="completedAt")

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return _timestamp(value)

    @field_serializer("completed_at")
    def serialize_completed_at(self, value: datetime | None) -> str | None:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return None if value is None else _timestamp(value)


class ExportPageResponse(BaseModel):
    """One page of exports and its opaque continuation cursor."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    exports: tuple[ExportResponse, ...]
    next_cursor: str | None = Field(alias="nextCursor")


class ClipProjectResponse(BaseModel):
    """The Project one clip belongs to."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    name: str
    status: str


class ClipEditResponse(BaseModel):
    """One Edit opened from a clip, and the Revision it currently points at."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    current_revision: int = Field(alias="currentRevision")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    @field_serializer("created_at", "updated_at")
    def serialize_instants(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return _timestamp(value)


class ClipDetailResponse(BaseModel):
    """Everything a clip page resolves from the one identifier in its URL."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    candidate: CandidateResponse
    project: ClipProjectResponse
    edits: tuple[ClipEditResponse, ...]
    exports: tuple[ExportResponse, ...]


class AssetProvenanceResponse(BaseModel):
    """Where one retrieved or generated asset came from, and under what licence."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    provider: str
    author: str
    license_name: str = Field(alias="licenseName")
    license_url: str = Field(alias="licenseUrl")
    source_url: str = Field(alias="sourceUrl")
    attribution_text: str = Field(alias="attributionText")
    generated: bool


class LibraryAssetResponse(BaseModel):
    """One library asset, the Project that owns it, and its provenance when it has one."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    project_id: UUID = Field(alias="projectId")
    project_name: str = Field(alias="projectName")
    kind: AssetKind
    source_type: AssetSourceType = Field(alias="sourceType")
    content_type: str = Field(alias="contentType")
    size_bytes: int = Field(alias="sizeBytes")
    duration_ms: int | None = Field(alias="durationMs")
    width: int | None
    height: int | None
    created_at: datetime = Field(alias="createdAt")
    provenance: AssetProvenanceResponse | None

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return _timestamp(value)


class AssetPageResponse(BaseModel):
    """One page of library assets and its opaque continuation cursor."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    assets: tuple[LibraryAssetResponse, ...]
    next_cursor: str | None = Field(alias="nextCursor")


class MediaPreviewResponse(BaseModel):
    """One five-minute capability to look at stored media."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    url: str
    expires_at: datetime = Field(alias="expiresAt")
    content_type: str = Field(alias="contentType")

    @field_serializer("expires_at")
    def serialize_expires_at(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return _timestamp(value)


@router.get("/clips", response_model=ClipPageResponse)
def browse_clip_collection(
    session: DatabaseSession,
    workspace: ReadableWorkspace,
    project_id: Annotated[UUID | None, Query(alias="projectId")] = None,
    stage: ClipStage | None = None,
    limit: PageLimit = 24,
    cursor: str | None = None,
    order: ClipOrder = ClipOrder.CREATED,
) -> ClipPageResponse:
    """Browse every exposed moment in the Workspace before anything is searched for."""
    if order is ClipOrder.RECENT and cursor is not None:
        raise ApiError(status_code=422, code="VALIDATION_ERROR")
    page = browse_clips(
        session,
        access=workspace.access,
        project_id=project_id,
        stage=stage,
        limit=limit,
        after=None if cursor is None else _decode_clip_cursor(cursor),
        order=order,
    )
    boundary = page.next_boundary
    return ClipPageResponse(
        clips=tuple(_clip_body(clip) for clip in page.clips),
        nextCursor=(
            None
            if boundary is None
            else _encode(
                f"{boundary.created_at.isoformat()}|{boundary.rank}|{boundary.candidate_id}"
            )
        ),
    )


@router.get("/clips/{candidate_id}", response_model=ClipDetailResponse)
def show_clip(
    candidate_id: UUID, session: DatabaseSession, workspace: ReadableWorkspace
) -> ClipDetailResponse:
    """Resolve one clip's Project, Edits, and exports from the clip identifier alone."""
    try:
        detail = clip_detail(session, access=workspace.access, candidate_id=candidate_id)
    except StudioNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return ClipDetailResponse(
        candidate=candidate_body(detail.candidate),
        project=ClipProjectResponse(
            id=detail.project.project_id, name=detail.project.name, status=detail.project.status
        ),
        edits=tuple(
            ClipEditResponse(
                id=edit.edit_id,
                currentRevision=edit.current_revision,
                createdAt=edit.created_at,
                updatedAt=edit.updated_at,
            )
            for edit in detail.edits
        ),
        exports=tuple(_export_body(export) for export in detail.exports),
    )


@router.get("/exports", response_model=ExportPageResponse)
def export_collection(
    session: DatabaseSession,
    workspace: ReadableWorkspace,
    edit_id: Annotated[UUID | None, Query(alias="editId")] = None,
    project_id: Annotated[UUID | None, Query(alias="projectId")] = None,
    state: ExportListState | None = None,
    limit: PageLimit = 20,
    cursor: str | None = None,
) -> ExportPageResponse:
    """List exports newest first, including the ones still being encoded."""
    page = list_exports(
        session,
        access=workspace.access,
        edit_id=edit_id,
        project_id=project_id,
        state=state,
        limit=limit,
        after=None if cursor is None else _decode_time_cursor(cursor),
    )
    return ExportPageResponse(
        exports=tuple(_export_body(export) for export in page.exports),
        nextCursor=_encode_time_cursor(page.next_boundary),
    )


@router.get("/assets", response_model=AssetPageResponse)
def asset_collection(
    session: DatabaseSession,
    workspace: ReadableWorkspace,
    project_id: Annotated[UUID | None, Query(alias="projectId")] = None,
    kind: AssetKind | None = None,
    limit: PageLimit = 24,
    cursor: str | None = None,
) -> AssetPageResponse:
    """Browse member media across the Workspace, with where each file came from."""
    page = browse_assets(
        session,
        access=workspace.access,
        project_id=project_id,
        kind=kind,
        limit=limit,
        after=None if cursor is None else _decode_time_cursor(cursor),
    )
    return AssetPageResponse(
        assets=tuple(_asset_body(asset) for asset in page.assets),
        nextCursor=_encode_time_cursor(page.next_boundary),
    )


@router.get("/assets/{asset_id}/preview-url", response_model=MediaPreviewResponse)
def preview_asset(
    asset_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
    store: Annotated[ObjectStore, Depends(object_store_for)],
) -> MediaPreviewResponse:
    """Sign five minutes of access to one library asset this member may read."""
    try:
        preview = asset_preview(session, store, access=workspace.access, asset_id=asset_id)
    except StudioNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return _preview_body(preview)


@router.get("/projects/{project_id}/thumbnail", response_model=MediaPreviewResponse)
def thumbnail(
    project_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
    store: Annotated[ObjectStore, Depends(object_store_for)],
) -> MediaPreviewResponse:
    """Sign five minutes of access to the frame ingest captured for one Project."""
    try:
        preview = project_thumbnail(session, store, access=workspace.access, project_id=project_id)
    except StudioNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return _preview_body(preview)


def _clip_body(clip: ClipSummary) -> ClipSummaryResponse:
    """Render one browsed moment into its public camelCase representation."""
    return ClipSummaryResponse(
        id=clip.candidate_id,
        projectId=clip.project_id,
        projectName=clip.project_name,
        rank=clip.rank,
        score=clip.score,
        hook=clip.hook,
        reason=clip.reason,
        category=ClipCategory(clip.category),
        startMs=clip.start_ms,
        endMs=clip.end_ms,
        durationMs=clip.end_ms - clip.start_ms,
        stage=clip.stage,
        editId=clip.edit_id,
        currentRevision=clip.current_revision,
        exportCount=clip.export_count,
        createdAt=clip.created_at,
        editUpdatedAt=clip.edit_updated_at,
    )


def _export_body(export: ExportSummary) -> ExportResponse:
    """Render one export without its storage key or composition hash."""
    return ExportResponse(
        id=export.export_id,
        jobId=export.job_id,
        status=export.status,
        errorCode=export.error_code,
        preset=export.preset,
        projectId=export.project_id,
        projectName=export.project_name,
        candidateId=export.candidate_id,
        clipTitle=export.clip_title,
        editId=export.edit_id,
        revisionId=export.revision_id,
        revision=export.revision,
        renderId=export.render_id,
        durationMs=export.duration_ms,
        sizeBytes=export.size_bytes,
        createdAt=export.created_at,
        completedAt=export.completed_at,
    )


def _asset_body(asset: LibraryAssetSummary) -> LibraryAssetResponse:
    """Render one library asset without its storage key or checksum."""
    provenance = asset.provenance
    return LibraryAssetResponse(
        id=asset.asset_id,
        projectId=asset.project_id,
        projectName=asset.project_name,
        kind=asset.kind,
        sourceType=asset.source_type,
        contentType=asset.content_type,
        sizeBytes=asset.size_bytes,
        durationMs=asset.duration_ms,
        width=asset.width,
        height=asset.height,
        createdAt=asset.created_at,
        provenance=(
            None
            if provenance is None
            else AssetProvenanceResponse(
                provider=provenance.provider,
                author=provenance.author,
                licenseName=provenance.license_name,
                licenseUrl=provenance.license_url,
                sourceUrl=provenance.source_url,
                attributionText=provenance.attribution_text,
                generated=provenance.generated,
            )
        ),
    )


def _preview_body(preview: MediaPreview) -> MediaPreviewResponse:
    """Render one signed capability and the media type it will return."""
    return MediaPreviewResponse(
        url=preview.download.url,
        expiresAt=preview.download.expires_at,
        contentType=preview.content_type,
    )


def _encode(raw: str) -> str:
    """Wrap a boundary as an opaque URL-safe cursor."""
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode(cursor: str, parts: int) -> list[str]:
    """Unwrap one opaque cursor, or refuse it rather than restarting from the top."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        fields = base64.urlsafe_b64decode(padded).decode().split("|")
    except (ValueError, UnicodeDecodeError, binascii.Error) as error:
        raise ApiError(status_code=422, code="VALIDATION_ERROR") from error
    if len(fields) != parts:
        raise ApiError(status_code=422, code="VALIDATION_ERROR")
    return fields


def _decode_clip_cursor(cursor: str) -> ClipBoundary:
    """Decode one browsed-clip boundary."""
    created_at, rank, candidate_id = _decode(cursor, 3)
    try:
        return ClipBoundary(
            created_at=datetime.fromisoformat(created_at),
            rank=int(rank),
            candidate_id=UUID(candidate_id),
        )
    except ValueError as error:
        raise ApiError(status_code=422, code="VALIDATION_ERROR") from error


def _decode_time_cursor(cursor: str) -> TimeBoundary:
    """Decode one newest-first boundary."""
    created_at, row_id = _decode(cursor, 2)
    try:
        return TimeBoundary(created_at=datetime.fromisoformat(created_at), row_id=UUID(row_id))
    except ValueError as error:
        raise ApiError(status_code=422, code="VALIDATION_ERROR") from error


def _encode_time_cursor(boundary: TimeBoundary | None) -> str | None:
    """Encode one newest-first boundary, or say there is no further page."""
    if boundary is None:
        return None
    return _encode(f"{boundary.created_at.isoformat()}|{boundary.row_id}")


class StoryboardSheetResponse(BaseModel):
    """One signed sheet and the first moment it shows."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    index: int
    start_ms: int = Field(alias="startMs")
    tile_count: int = Field(alias="tileCount")
    url: str


class StoryboardResponse(BaseModel):
    """Sheet geometry and signed sheets for one Project."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: int
    interval_ms: int = Field(alias="intervalMs")
    tile_width: int = Field(alias="tileWidth")
    tile_height: int = Field(alias="tileHeight")
    columns: int
    rows: int
    duration_ms: int = Field(alias="durationMs")
    sheets: tuple[StoryboardSheetResponse, ...]
    expires_at: datetime = Field(alias="expiresAt")

    @field_serializer("expires_at")
    def serialize_expires_at(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return _timestamp(value)


class WaveformResponse(BaseModel):
    """A signed waveform and how many peaks make a second."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: int
    peaks_per_second: int = Field(alias="peaksPerSecond")
    duration_ms: int = Field(alias="durationMs")
    url: str
    expires_at: datetime = Field(alias="expiresAt")

    @field_serializer("expires_at")
    def serialize_expires_at(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return _timestamp(value)


class TranscriptWordResponse(BaseModel):
    """One transcribed word."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    text: str
    punctuation: str
    start_ms: int = Field(alias="startMs")
    end_ms: int = Field(alias="endMs")
    speaker: str


class TranscriptResponse(BaseModel):
    """One Project's transcript in spoken order."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    language: str
    duration_ms: int = Field(alias="durationMs")
    words: tuple[TranscriptWordResponse, ...]


@router.get("/projects/{project_id}/storyboard", response_model=StoryboardResponse)
def storyboard(
    project_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
    store: Annotated[ObjectStore, Depends(object_store_for)],
) -> StoryboardResponse:
    """Sign five minutes of access to every storyboard sheet of one Project."""
    try:
        view = project_storyboard(session, store, access=workspace.access, project_id=project_id)
    except StudioNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return StoryboardResponse(
        version=view.version,
        intervalMs=view.interval_ms,
        tileWidth=view.tile_width,
        tileHeight=view.tile_height,
        columns=view.columns,
        rows=view.rows,
        durationMs=view.duration_ms,
        sheets=tuple(
            StoryboardSheetResponse(
                index=sheet.index,
                startMs=sheet.start_ms,
                tileCount=sheet.tile_count,
                url=sheet.download.url,
            )
            for sheet in view.sheets
        ),
        expiresAt=view.expires_at,
    )


class ClipPosterResponse(BaseModel):
    """One moment's signed portrait poster."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    candidate_id: UUID = Field(alias="candidateId")
    width: int
    height: int
    url: str


class ClipPostersResponse(BaseModel):
    """Every poster drawn so far for one Project's exposed moments."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    posters: tuple[ClipPosterResponse, ...]
    expires_at: datetime | None = Field(alias="expiresAt")

    @field_serializer("expires_at")
    def serialize_expires_at(self, value: datetime | None) -> str | None:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return None if value is None else _timestamp(value)


@router.get("/projects/{project_id}/posters", response_model=ClipPostersResponse)
def posters(
    project_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
    store: Annotated[ObjectStore, Depends(object_store_for)],
) -> ClipPostersResponse:
    """Sign five minutes of access to every poster drawn for one Project's moments."""
    try:
        views = project_posters(session, store, access=workspace.access, project_id=project_id)
    except StudioNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return ClipPostersResponse(
        posters=tuple(
            ClipPosterResponse(
                candidateId=view.candidate_id,
                width=view.width,
                height=view.height,
                url=view.download.url,
            )
            for view in views
        ),
        expiresAt=min((view.download.expires_at for view in views), default=None),
    )


@router.get("/projects/{project_id}/waveform", response_model=WaveformResponse)
def waveform(
    project_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
    store: Annotated[ObjectStore, Depends(object_store_for)],
) -> WaveformResponse:
    """Sign five minutes of access to one Project's waveform peaks."""
    try:
        view = project_waveform(session, store, access=workspace.access, project_id=project_id)
    except StudioNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return WaveformResponse(
        version=view.version,
        peaksPerSecond=view.peaks_per_second,
        durationMs=view.duration_ms,
        url=view.download.url,
        expiresAt=view.download.expires_at,
    )


@router.get("/projects/{project_id}/transcript", response_model=TranscriptResponse)
def transcript(
    project_id: UUID, session: DatabaseSession, workspace: ReadableWorkspace
) -> TranscriptResponse:
    """Read one Project's transcript for review and the Project page."""
    try:
        view = project_transcript(session, access=workspace.access, project_id=project_id)
    except StudioNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return TranscriptResponse(
        language=view.language,
        durationMs=view.duration_ms,
        words=tuple(
            TranscriptWordResponse(
                id=word.word_id,
                text=word.text,
                punctuation=word.punctuation,
                startMs=word.start_ms,
                endMs=word.end_ms,
                speaker=word.speaker,
            )
            for word in view.words
        ),
    )
