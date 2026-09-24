"""Workspace-scoped browsing reads the creator studio is built from.

A creator moves from a Project to a moment, to an Edit, to an export, and on to a
publication, and at every step they need to see what they already have without typing
an identifier. Everything here is derived from records the pipeline already keeps: no
read invents a workflow state, and none of them writes anything.

Every read is bounded by the Workspace the caller proved standing in and by Projects that
have not been deleted, so another Workspace's row and a deleted Project's row both end
the same way — absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.orm import Session

from clipah.assets.clip_posters import poster_candidate_id
from clipah.assets.preview_media import (
    STORYBOARD_V1,
    WAVEFORM_PEAKS_PER_SECOND,
    WAVEFORM_V1_NAME,
    storyboard_sheet_index,
)
from clipah.assets.storage import ObjectStore, SignedUrl
from clipah.assets.uploads import SIGNED_URL_TTL
from clipah.highlights.repository import CandidateSummary, HighlightRepository
from clipah.models import (
    Asset,
    AssetKind,
    AssetProvenance,
    AssetSourceType,
    ClipCandidate,
    ClipEdit,
    ClipEditRevision,
    Job,
    JobStatus,
    Project,
    RenderArtifact,
    RenderRequest,
    Transcript,
)
from clipah.renders.cover import cover_asset_id
from clipah.workspaces.models import WorkspaceAccess

# What a creator recognizes as their own media. Proxies, waveforms, transcription audio,
# and B-roll proxies exist for the pipeline, and a thumbnail is shown on its Project's
# card rather than listed as a file of its own.
BROWSABLE_ASSET_KINDS: frozenset[AssetKind] = frozenset(
    {AssetKind.SOURCE, AssetKind.BROLL, AssetKind.RENDER}
)

_IN_PROGRESS_JOB_STATUSES = (
    JobStatus.QUEUED,
    JobStatus.RUNNING,
    JobStatus.RETRYING,
    JobStatus.CANCEL_REQUESTED,
)


class StudioNotFoundError(Exception):
    """The row does not exist, or the caller may not learn that it does."""


class ClipOrder(StrEnum):
    """How a browsed clip list is ordered."""

    CREATED = "created"
    RECENT = "recent"


class ClipStage(StrEnum):
    """How far one moment has travelled from suggestion to finished file."""

    SUGGESTED = "suggested"
    EDITED = "edited"
    EXPORTED = "exported"


class ExportListState(StrEnum):
    """The piles an export list may be narrowed to."""

    READY = "ready"
    IN_PROGRESS = "in_progress"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ClipBoundary:
    """A stable place immediately after one browsed clip."""

    created_at: datetime
    rank: int
    candidate_id: UUID


@dataclass(frozen=True, slots=True)
class TimeBoundary:
    """A stable place immediately after one row ordered newest first."""

    created_at: datetime
    row_id: UUID


@dataclass(frozen=True, slots=True)
class ClipSummary:
    """One exposed moment, the Project it came from, and how far it has got."""

    candidate_id: UUID
    project_id: UUID
    project_name: str
    rank: int
    score: float
    hook: str
    reason: str
    category: str
    start_ms: int
    end_ms: int
    stage: ClipStage
    edit_id: UUID | None
    current_revision: int | None
    export_count: int
    created_at: datetime
    edit_updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class ClipPage:
    """One page of browsed clips and where the next page starts."""

    clips: tuple[ClipSummary, ...]
    next_boundary: ClipBoundary | None


@dataclass(frozen=True, slots=True)
class ExportSummary:
    """One requested export: the file when it exists, and the Job's account otherwise."""

    export_id: UUID
    job_id: UUID
    status: str
    error_code: str | None
    preset: str
    project_id: UUID
    project_name: str
    candidate_id: UUID
    clip_title: str
    edit_id: UUID
    revision_id: UUID
    revision: int
    render_id: UUID | None
    duration_ms: int | None
    size_bytes: int | None
    created_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ExportPage:
    """One page of exports and where the next page starts."""

    exports: tuple[ExportSummary, ...]
    next_boundary: TimeBoundary | None


@dataclass(frozen=True, slots=True)
class ProjectContext:
    """The Project one clip belongs to, as much of it as a clip page shows."""

    project_id: UUID
    name: str
    status: str


@dataclass(frozen=True, slots=True)
class EditSummary:
    """One Edit of a clip and the Revision it currently points at."""

    edit_id: UUID
    current_revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ClipDetail:
    """Everything a clip page resolves from the one identifier in its URL."""

    candidate: CandidateSummary
    project: ProjectContext
    edits: tuple[EditSummary, ...]
    exports: tuple[ExportSummary, ...]


@dataclass(frozen=True, slots=True)
class AssetProvenanceSummary:
    """Where a retrieved or generated asset came from, without anything secret."""

    provider: str
    author: str
    license_name: str
    license_url: str
    source_url: str
    attribution_text: str
    generated: bool


@dataclass(frozen=True, slots=True)
class LibraryAssetSummary:
    """One member-recognizable asset, the Project that owns it, and its provenance."""

    asset_id: UUID
    project_id: UUID
    project_name: str
    kind: AssetKind
    source_type: AssetSourceType
    content_type: str
    size_bytes: int
    duration_ms: int | None
    width: int | None
    height: int | None
    created_at: datetime
    provenance: AssetProvenanceSummary | None


@dataclass(frozen=True, slots=True)
class AssetPage:
    """One page of library assets and where the next page starts."""

    assets: tuple[LibraryAssetSummary, ...]
    next_boundary: TimeBoundary | None


@dataclass(frozen=True, slots=True)
class MediaPreview:
    """One five-minute capability to look at stored media, and what it will be."""

    download: SignedUrl
    content_type: str


def browse_clips(
    session: Session,
    *,
    access: WorkspaceAccess,
    project_id: UUID | None,
    stage: ClipStage | None,
    limit: int,
    after: ClipBoundary | None,
    order: ClipOrder = ClipOrder.CREATED,
) -> ClipPage:
    """List exposed moments across the Workspace, newest analysis first, best rank first.

    `RECENT` instead returns one top-N page, most recently saved Edit first; it has no
    continuation, because saving an Edit reshuffles the order a cursor would promise.
    """
    edit_id = _first_edit_column(ClipEdit.id)
    current_revision = _first_edit_column(ClipEdit.current_revision)
    edit_updated_at = _first_edit_column(ClipEdit.updated_at)
    export_count = _export_count()
    conditions: list[ColumnElement[bool]] = [
        ClipCandidate.workspace_id == access.workspace_id,
        _exposed(),
    ]
    if project_id is not None:
        conditions.append(ClipCandidate.project_id == project_id)
    if stage is ClipStage.SUGGESTED:
        conditions.append(edit_id.is_(None))
    elif stage is ClipStage.EDITED:
        conditions.extend([edit_id.is_not(None), export_count == 0])
    elif stage is ClipStage.EXPORTED:
        conditions.append(export_count > 0)
    if order is ClipOrder.RECENT:
        recent_rows = session.execute(
            select(
                ClipCandidate,
                Project.name,
                edit_id,
                current_revision,
                export_count,
                edit_updated_at,
            )
            .join(Project, _active_project(ClipCandidate.workspace_id, ClipCandidate.project_id))
            .where(*conditions)
            .order_by(edit_updated_at.desc().nulls_last(), ClipCandidate.id)
            .limit(limit)
        ).all()
        return ClipPage(
            clips=tuple(
                _clip_summary(candidate, project_name, found_edit, revision, count, updated)
                for candidate, project_name, found_edit, revision, count, updated in recent_rows
            ),
            next_boundary=None,
        )
    if after is not None:
        conditions.append(
            or_(
                ClipCandidate.created_at < after.created_at,
                and_(
                    ClipCandidate.created_at == after.created_at,
                    or_(
                        ClipCandidate.rank > after.rank,
                        and_(
                            ClipCandidate.rank == after.rank,
                            ClipCandidate.id > after.candidate_id,
                        ),
                    ),
                ),
            )
        )
    rows = session.execute(
        select(
            ClipCandidate, Project.name, edit_id, current_revision, export_count, edit_updated_at
        )
        .join(Project, _active_project(ClipCandidate.workspace_id, ClipCandidate.project_id))
        .where(*conditions)
        .order_by(ClipCandidate.created_at.desc(), ClipCandidate.rank, ClipCandidate.id)
        .limit(limit + 1)
    ).all()
    clips = tuple(
        _clip_summary(candidate, project_name, found_edit, revision, count, updated)
        for candidate, project_name, found_edit, revision, count, updated in rows[:limit]
    )
    next_boundary = None
    if len(rows) > limit:
        last = clips[-1]
        next_boundary = ClipBoundary(
            created_at=last.created_at, rank=last.rank, candidate_id=last.candidate_id
        )
    return ClipPage(clips=clips, next_boundary=next_boundary)


def clip_detail(session: Session, *, access: WorkspaceAccess, candidate_id: UUID) -> ClipDetail:
    """Resolve one clip's Project, its Edits, and its exports from the clip alone."""
    candidate = HighlightRepository(session).candidate_in_workspace(
        workspace_id=access.workspace_id, candidate_id=candidate_id
    )
    if candidate is None:
        raise StudioNotFoundError(str(candidate_id))
    project = session.execute(
        select(Project.id, Project.name, Project.status).where(
            Project.workspace_id == access.workspace_id,
            Project.id == candidate.project_id,
            Project.archived_at.is_(None),
        )
    ).first()
    if project is None:
        raise StudioNotFoundError(str(candidate_id))
    edits = session.execute(
        select(ClipEdit.id, ClipEdit.current_revision, ClipEdit.created_at, ClipEdit.updated_at)
        .where(
            ClipEdit.workspace_id == access.workspace_id,
            ClipEdit.candidate_id == candidate_id,
        )
        .order_by(ClipEdit.created_at, ClipEdit.id)
    ).all()
    exports = list_exports(
        session,
        access=access,
        edit_id=None,
        project_id=None,
        candidate_id=candidate_id,
        state=None,
        limit=50,
        after=None,
    )
    project_id, name, status = project
    return ClipDetail(
        candidate=candidate,
        project=ProjectContext(project_id=project_id, name=name, status=str(status.value)),
        edits=tuple(
            EditSummary(
                edit_id=edit_id, current_revision=revision, created_at=created, updated_at=updated
            )
            for edit_id, revision, created, updated in edits
        ),
        exports=exports.exports,
    )


def list_exports(
    session: Session,
    *,
    access: WorkspaceAccess,
    edit_id: UUID | None,
    project_id: UUID | None,
    candidate_id: UUID | None = None,
    state: ExportListState | None,
    limit: int,
    after: TimeBoundary | None,
) -> ExportPage:
    """List requested exports newest first, each with its file or its Job's status."""
    conditions: list[ColumnElement[bool]] = [RenderRequest.workspace_id == access.workspace_id]
    if edit_id is not None:
        conditions.append(ClipEdit.id == edit_id)
    if project_id is not None:
        conditions.append(Project.id == project_id)
    if candidate_id is not None:
        conditions.append(ClipEdit.candidate_id == candidate_id)
    if state is ExportListState.READY:
        conditions.append(RenderArtifact.id.is_not(None))
    elif state is ExportListState.IN_PROGRESS:
        conditions.extend([RenderArtifact.id.is_(None), Job.status.in_(_IN_PROGRESS_JOB_STATUSES)])
    elif state is ExportListState.FAILED:
        conditions.extend(
            [RenderArtifact.id.is_(None), Job.status.not_in(_IN_PROGRESS_JOB_STATUSES)]
        )
    if after is not None:
        conditions.append(
            or_(
                RenderRequest.created_at < after.created_at,
                and_(
                    RenderRequest.created_at == after.created_at,
                    RenderRequest.id < after.row_id,
                ),
            )
        )
    rows = session.execute(
        select(
            RenderRequest,
            Job.status,
            Job.error_code,
            Job.finished_at,
            ClipEditRevision.revision,
            ClipEdit.id,
            ClipEdit.candidate_id,
            ClipCandidate.hook,
            Project.id,
            Project.name,
            RenderArtifact,
        )
        .join(
            Job,
            (Job.workspace_id == RenderRequest.workspace_id) & (Job.id == RenderRequest.job_id),
        )
        .join(
            ClipEditRevision,
            (ClipEditRevision.workspace_id == RenderRequest.workspace_id)
            & (ClipEditRevision.id == RenderRequest.clip_edit_revision_id),
        )
        .join(
            ClipEdit,
            (ClipEdit.workspace_id == ClipEditRevision.workspace_id)
            & (ClipEdit.id == ClipEditRevision.clip_edit_id),
        )
        .join(
            ClipCandidate,
            (ClipCandidate.workspace_id == ClipEdit.workspace_id)
            & (ClipCandidate.id == ClipEdit.candidate_id),
        )
        .join(Project, _active_project(ClipCandidate.workspace_id, ClipCandidate.project_id))
        .outerjoin(
            RenderArtifact,
            (RenderArtifact.workspace_id == RenderRequest.workspace_id)
            & (RenderArtifact.composition_hash == RenderRequest.composition_hash)
            & (RenderArtifact.preset == RenderRequest.preset)
            & (RenderArtifact.size_bytes > 0),
        )
        .where(*conditions)
        .order_by(RenderRequest.created_at.desc(), RenderRequest.id.desc())
        .limit(limit + 1)
    ).all()
    exports = tuple(_export_summary(*row) for row in rows[:limit])
    next_boundary = None
    if len(rows) > limit:
        last = exports[-1]
        next_boundary = TimeBoundary(created_at=last.created_at, row_id=last.export_id)
    return ExportPage(exports=exports, next_boundary=next_boundary)


def browse_assets(
    session: Session,
    *,
    access: WorkspaceAccess,
    project_id: UUID | None,
    kind: AssetKind | None,
    limit: int,
    after: TimeBoundary | None,
) -> AssetPage:
    """List member media across the Workspace, newest first, with where each came from."""
    kinds = BROWSABLE_ASSET_KINDS if kind is None else BROWSABLE_ASSET_KINDS & {kind}
    conditions: list[ColumnElement[bool]] = [
        Asset.workspace_id == access.workspace_id,
        Asset.kind.in_(kinds),
    ]
    if project_id is not None:
        conditions.append(Asset.project_id == project_id)
    if after is not None:
        conditions.append(
            or_(
                Asset.created_at < after.created_at,
                and_(Asset.created_at == after.created_at, Asset.id < after.row_id),
            )
        )
    rows = session.execute(
        select(Asset, Project.name, AssetProvenance)
        .join(Project, _active_project(Asset.workspace_id, Asset.project_id))
        .outerjoin(
            AssetProvenance,
            (AssetProvenance.workspace_id == Asset.workspace_id)
            & (AssetProvenance.asset_id == Asset.id),
        )
        .where(*conditions)
        .order_by(Asset.created_at.desc(), Asset.id.desc())
        .limit(limit + 1)
    ).all()
    assets = tuple(
        _asset_summary(asset, project_name, provenance)
        for asset, project_name, provenance in rows[:limit]
    )
    next_boundary = None
    if len(rows) > limit:
        last = assets[-1]
        next_boundary = TimeBoundary(created_at=last.created_at, row_id=last.asset_id)
    return AssetPage(assets=assets, next_boundary=next_boundary)


def asset_preview(
    session: Session, store: ObjectStore, *, access: WorkspaceAccess, asset_id: UUID
) -> MediaPreview:
    """Sign five minutes of access to one library asset of an active Project.

    B-roll is previewed from the proxy retrieval made of it when there is one: a stock
    original can be a 4K file of hundreds of megabytes, and the editor only plays it.
    """
    row = session.execute(
        select(Asset.storage_key, Asset.content_type, Asset.kind, Asset.project_id, Asset.sha256)
        .join(Project, _active_project(Asset.workspace_id, Asset.project_id))
        .where(
            Asset.workspace_id == access.workspace_id,
            Asset.id == asset_id,
            Asset.kind.in_(BROWSABLE_ASSET_KINDS),
        )
    ).first()
    if row is None:
        raise StudioNotFoundError(str(asset_id))
    key, content_type, kind, project_id, sha256 = row
    if kind is AssetKind.BROLL:
        proxy = session.execute(
            select(Asset.storage_key, Asset.content_type)
            .where(
                Asset.workspace_id == access.workspace_id,
                Asset.project_id == project_id,
                Asset.kind == AssetKind.BROLL_PROXY,
                Asset.sha256 == sha256,
            )
            .limit(1)
        ).first()
        if proxy is not None:
            key, content_type = proxy
    return MediaPreview(
        download=store.sign_download(key=key, expires_in=SIGNED_URL_TTL),
        content_type=content_type,
    )


def project_thumbnail(
    session: Session, store: ObjectStore, *, access: WorkspaceAccess, project_id: UUID
) -> MediaPreview:
    """Sign five minutes of access to the newest frame ingest captured for one Project."""
    row = session.execute(
        select(Asset.storage_key, Asset.content_type)
        .join(Project, _active_project(Asset.workspace_id, Asset.project_id))
        .where(
            Asset.workspace_id == access.workspace_id,
            Asset.project_id == project_id,
            Asset.kind == AssetKind.THUMBNAIL,
        )
        .order_by(Asset.created_at.desc(), Asset.id.desc())
        .limit(1)
    ).first()
    if row is None:
        raise StudioNotFoundError(str(project_id))
    key, content_type = row
    return MediaPreview(
        download=store.sign_download(key=key, expires_in=SIGNED_URL_TTL),
        content_type=content_type,
    )


@dataclass(frozen=True, slots=True)
class StoryboardSheet:
    """One signed sheet and the frames it holds."""

    index: int
    start_ms: int
    tile_count: int
    download: SignedUrl


@dataclass(frozen=True, slots=True)
class StoryboardView:
    """Everything a browser needs to draw any moment of a source from its sheets."""

    version: int
    interval_ms: int
    tile_width: int
    tile_height: int
    columns: int
    rows: int
    duration_ms: int
    sheets: tuple[StoryboardSheet, ...]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class WaveformView:
    """A signed waveform and how to read it."""

    version: int
    peaks_per_second: int
    duration_ms: int
    download: SignedUrl


@dataclass(frozen=True, slots=True)
class TranscriptWordView:
    """One transcribed word with its timing and speaker."""

    word_id: str
    text: str
    punctuation: str
    start_ms: int
    end_ms: int
    speaker: str


@dataclass(frozen=True, slots=True)
class TranscriptView:
    """One Project's canonical transcript, in spoken order."""

    language: str
    duration_ms: int
    words: tuple[TranscriptWordView, ...]


def project_storyboard(
    session: Session, store: ObjectStore, *, access: WorkspaceAccess, project_id: UUID
) -> StoryboardView:
    """Sign every version-one sheet of one active Project, in order."""
    rows = session.execute(
        select(Asset.storage_key, Asset.width, Asset.height, Asset.duration_ms)
        .join(Project, _active_project(Asset.workspace_id, Asset.project_id))
        .where(
            Asset.workspace_id == access.workspace_id,
            Asset.project_id == project_id,
            Asset.kind == AssetKind.STORYBOARD,
        )
        .order_by(Asset.storage_key)
    ).all()
    policy = STORYBOARD_V1
    sheets: list[StoryboardSheet] = []
    tile_width = tile_height = 0
    duration_ms = 0
    for key, width, height, sheet_duration in rows:
        index = storyboard_sheet_index(key)
        if index is None or width is None or height is None or sheet_duration is None:
            continue
        tile_width, tile_height = width // policy.columns, height // policy.rows
        start_ms = index * policy.frames_per_sheet * policy.interval_ms
        duration_ms = max(duration_ms, start_ms + sheet_duration)
        sheets.append(
            StoryboardSheet(
                index=index,
                start_ms=start_ms,
                tile_count=max(1, -(-sheet_duration // policy.interval_ms)),
                download=store.sign_download(key=key, expires_in=SIGNED_URL_TTL),
            )
        )
    if not sheets:
        raise StudioNotFoundError(str(project_id))
    return StoryboardView(
        version=policy.version,
        interval_ms=policy.interval_ms,
        tile_width=tile_width,
        tile_height=tile_height,
        columns=policy.columns,
        rows=policy.rows,
        duration_ms=duration_ms,
        sheets=tuple(sheets),
        expires_at=min(sheet.download.expires_at for sheet in sheets),
    )


@dataclass(frozen=True, slots=True)
class ClipPosterView:
    """One moment's signed poster and its pixel size."""

    candidate_id: UUID
    width: int
    height: int
    download: SignedUrl


def project_posters(
    session: Session, store: ObjectStore, *, access: WorkspaceAccess, project_id: UUID
) -> tuple[ClipPosterView, ...]:
    """Sign every drawn poster of one active Project's exposed moments.

    A Project whose posters are still being drawn answers with fewer, or none: each card
    keeps its storyboard picture until its own poster exists.
    """
    if (
        session.scalar(
            select(Project.id).where(
                Project.workspace_id == access.workspace_id,
                Project.id == project_id,
                Project.archived_at.is_(None),
            )
        )
        is None
    ):
        raise StudioNotFoundError(str(project_id))
    exposed = set(
        session.scalars(
            select(ClipCandidate.id).where(
                ClipCandidate.workspace_id == access.workspace_id,
                ClipCandidate.project_id == project_id,
                _exposed(),
            )
        )
    )
    rows = session.execute(
        select(Asset.storage_key, Asset.width, Asset.height)
        .where(
            Asset.workspace_id == access.workspace_id,
            Asset.project_id == project_id,
            Asset.kind == AssetKind.POSTER,
        )
        .order_by(Asset.storage_key)
    ).all()
    posters: list[ClipPosterView] = []
    for key, width, height in rows:
        candidate_id = poster_candidate_id(key)
        if candidate_id is None or candidate_id not in exposed or width is None or height is None:
            continue
        posters.append(
            ClipPosterView(
                candidate_id=candidate_id,
                width=width,
                height=height,
                download=store.sign_download(key=key, expires_in=SIGNED_URL_TTL),
            )
        )
    covers = _designed_covers(session, access=access, project_id=project_id, exposed=exposed)
    for candidate_id, (key, width, height) in covers.items():
        posters = [poster for poster in posters if poster.candidate_id != candidate_id]
        posters.append(
            ClipPosterView(
                candidate_id=candidate_id,
                width=width,
                height=height,
                download=store.sign_download(key=key, expires_in=SIGNED_URL_TTL),
            )
        )
    return tuple(sorted(posters, key=lambda poster: str(poster.candidate_id)))


def _designed_covers(
    session: Session, *, access: WorkspaceAccess, project_id: UUID, exposed: set[UUID]
) -> dict[UUID, tuple[str, int, int]]:
    """The drawn cover of each exposed clip's current Revision, which its card shows first.

    A member who designed a cover chose the picture their clip is known by, so it takes
    the place of the frame analysis picked. A cover of an older Revision is not shown.
    """
    current = session.execute(
        select(ClipEdit.candidate_id, ClipEditRevision.id)
        .join(
            ClipEditRevision,
            (ClipEditRevision.workspace_id == ClipEdit.workspace_id)
            & (ClipEditRevision.clip_edit_id == ClipEdit.id)
            & (ClipEditRevision.revision == ClipEdit.current_revision),
        )
        .where(ClipEdit.workspace_id == access.workspace_id, ClipEdit.candidate_id.in_(exposed))
    ).all()
    wanted = {cover_asset_id(revision_id): candidate_id for candidate_id, revision_id in current}
    if not wanted:
        return {}
    rows = session.execute(
        select(Asset.id, Asset.storage_key, Asset.width, Asset.height).where(
            Asset.workspace_id == access.workspace_id,
            Asset.project_id == project_id,
            Asset.kind == AssetKind.COVER,
            Asset.id.in_(list(wanted)),
        )
    ).all()
    return {
        wanted[row.id]: (row.storage_key, row.width, row.height)
        for row in rows
        if row.width is not None and row.height is not None
    }


def project_waveform(
    session: Session, store: ObjectStore, *, access: WorkspaceAccess, project_id: UUID
) -> WaveformView:
    """Sign the version-one waveform of one active Project."""
    row = session.execute(
        select(Asset.storage_key, Asset.duration_ms)
        .join(Project, _active_project(Asset.workspace_id, Asset.project_id))
        .where(
            Asset.workspace_id == access.workspace_id,
            Asset.project_id == project_id,
            Asset.kind == AssetKind.WAVEFORM,
            Asset.storage_key.endswith(f"/{WAVEFORM_V1_NAME}"),
        )
        .order_by(Asset.created_at.desc(), Asset.id.desc())
        .limit(1)
    ).first()
    if row is None or row.duration_ms is None:
        raise StudioNotFoundError(str(project_id))
    return WaveformView(
        version=1,
        peaks_per_second=WAVEFORM_PEAKS_PER_SECOND,
        duration_ms=row.duration_ms,
        download=store.sign_download(key=row.storage_key, expires_in=SIGNED_URL_TTL),
    )


def project_transcript(
    session: Session, *, access: WorkspaceAccess, project_id: UUID
) -> TranscriptView:
    """Read the newest canonical transcript of one active Project."""
    row = session.execute(
        select(Transcript.language, Transcript.duration_ms, Transcript.words)
        .join(Project, _active_project(Transcript.workspace_id, Transcript.project_id))
        .where(Transcript.workspace_id == access.workspace_id, Transcript.project_id == project_id)
        .order_by(Transcript.created_at.desc(), Transcript.id.desc())
        .limit(1)
    ).first()
    if row is None:
        raise StudioNotFoundError(str(project_id))
    return TranscriptView(
        language=row.language,
        duration_ms=row.duration_ms,
        words=tuple(
            TranscriptWordView(
                word_id=str(word["word_id"]),
                text=str(word["text"]),
                punctuation=str(word.get("punctuation") or ""),
                start_ms=int(word["start_ms"]),
                end_ms=int(word["end_ms"]),
                speaker=str(word.get("speaker") or ""),
            )
            for word in row.words
        ),
    )


def _active_project(workspace_id: Any, project_id: Any) -> ColumnElement[bool]:
    """Join to the owning Project only while it has not been deleted."""
    return and_(
        Project.workspace_id == workspace_id,
        Project.id == project_id,
        Project.archived_at.is_(None),
    )


def _exposed() -> ColumnElement[bool]:
    """Only candidates the ranking policy chose to show a reviewer."""
    exposed: ColumnElement[bool] = ClipCandidate.model_metadata["exposed"].as_boolean().is_(True)
    return exposed


def _first_edit_column(column: Any) -> Any:
    """Read one column of the first Edit opened from the candidate in the outer query."""
    return (
        select(column)
        .where(
            ClipEdit.workspace_id == ClipCandidate.workspace_id,
            ClipEdit.candidate_id == ClipCandidate.id,
        )
        .order_by(ClipEdit.created_at, ClipEdit.id)
        .limit(1)
        .correlate(ClipCandidate)
        .scalar_subquery()
    )


def _export_count() -> Any:
    """Count the finished files any Edit of the outer query's candidate produced."""
    return (
        select(func.count(RenderArtifact.id))
        .join(
            ClipEditRevision,
            (ClipEditRevision.workspace_id == RenderArtifact.workspace_id)
            & (ClipEditRevision.id == RenderArtifact.clip_edit_revision_id),
        )
        .join(
            ClipEdit,
            (ClipEdit.workspace_id == ClipEditRevision.workspace_id)
            & (ClipEdit.id == ClipEditRevision.clip_edit_id),
        )
        .where(
            ClipEdit.workspace_id == ClipCandidate.workspace_id,
            ClipEdit.candidate_id == ClipCandidate.id,
            RenderArtifact.size_bytes > 0,
        )
        .correlate(ClipCandidate)
        .scalar_subquery()
    )


def _clip_summary(
    candidate: ClipCandidate,
    project_name: str,
    edit_id: UUID | None,
    current_revision: int | None,
    export_count: int,
    edit_updated_at: datetime | None,
) -> ClipSummary:
    """Name how far one moment has got from the rows that already record it."""
    if export_count > 0:
        stage = ClipStage.EXPORTED
    elif edit_id is not None:
        stage = ClipStage.EDITED
    else:
        stage = ClipStage.SUGGESTED
    return ClipSummary(
        candidate_id=candidate.id,
        project_id=candidate.project_id,
        project_name=project_name,
        rank=candidate.rank,
        score=float(candidate.score),
        hook=candidate.hook,
        reason=candidate.reason,
        category=candidate.category,
        start_ms=candidate.start_ms,
        end_ms=candidate.end_ms,
        stage=stage,
        edit_id=edit_id,
        current_revision=current_revision,
        export_count=export_count,
        created_at=candidate.created_at,
        edit_updated_at=edit_updated_at,
    )


def _export_summary(
    request: RenderRequest,
    job_status: JobStatus,
    error_code: str | None,
    finished_at: datetime | None,
    revision: int,
    edit_id: UUID,
    candidate_id: UUID,
    clip_title: str,
    project_id: UUID,
    project_name: str,
    artifact: RenderArtifact | None,
) -> ExportSummary:
    """Describe one export by its file when there is one, and by its Job when there is not."""
    return ExportSummary(
        export_id=request.id,
        job_id=request.job_id,
        status=_export_status(job_status, artifact),
        error_code=None if artifact is not None else error_code,
        preset=request.preset,
        project_id=project_id,
        project_name=project_name,
        candidate_id=candidate_id,
        clip_title=clip_title,
        edit_id=edit_id,
        revision_id=request.clip_edit_revision_id,
        revision=revision,
        render_id=None if artifact is None else artifact.id,
        duration_ms=None if artifact is None else artifact.duration_ms,
        size_bytes=None if artifact is None else artifact.size_bytes,
        created_at=request.created_at,
        completed_at=artifact.created_at if artifact is not None else finished_at,
    )


def _export_status(job_status: JobStatus, artifact: RenderArtifact | None) -> str:
    """Collapse the Job lifecycle into the four states a creator acts on."""
    if artifact is not None:
        return "ready"
    if job_status is JobStatus.QUEUED:
        return "queued"
    if job_status in _IN_PROGRESS_JOB_STATUSES:
        return "rendering"
    if job_status is JobStatus.CANCELED:
        return "canceled"
    # A Job that finished without a healthy file produced nothing a creator can use.
    return "failed"


def _asset_summary(
    asset: Asset, project_name: str, provenance: AssetProvenance | None
) -> LibraryAssetSummary:
    """Describe one library asset without its private storage key."""
    return LibraryAssetSummary(
        asset_id=asset.id,
        project_id=asset.project_id,
        project_name=project_name,
        kind=asset.kind,
        source_type=asset.source_type,
        content_type=asset.content_type,
        size_bytes=asset.size_bytes,
        duration_ms=asset.duration_ms,
        width=asset.width,
        height=asset.height,
        created_at=asset.created_at,
        provenance=(
            None
            if provenance is None
            else AssetProvenanceSummary(
                provider=provenance.provider,
                author=provenance.author,
                license_name=provenance.license_name,
                license_url=provenance.license_url,
                source_url=provenance.source_url,
                attribution_text=provenance.attribution_text,
                generated=asset.source_type is AssetSourceType.GENERATED,
            )
        ),
    )
