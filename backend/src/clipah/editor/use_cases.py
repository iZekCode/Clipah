"""Editing decisions: opening an Edit, reading it, and saving the next Revision.

An Edit is created from a reviewed Clip Candidate and never rewritten. Every save
appends a Revision, and which save wins is decided in Postgres rather than in a
browser, because two members editing one clip is the ordinary case, not the rare one.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from clipah.editor.models import (
    SCHEMA_VERSION,
    AudioMix,
    BlendMode,
    Canvas,
    CaptionMode,
    Captions,
    CaptionStyle,
    CaptionWord,
    CompositionV1,
    FontFamily,
    MotionPreset,
    Origin,
    OriginType,
    SourceRange,
    TextAlign,
    TextDecoration,
    Track,
    TrackItem,
    TrackType,
    Transform,
    canonical_json,
    collect_asset_ids,
    composition_hash,
    parse_composition,
)
from clipah.editor.repository import CandidateSeed, EditDetail, EditRepository, RevisionSummary
from clipah.workspaces.models import WorkspaceAccess

DEFAULT_CANVAS = Canvas(width=1080, height=1920, background="#000000")
MAIN_VIDEO_TRACK_ID = "main-video"
FIRST_SCENE_ITEM_ID = "scene-1"
MAX_REVISION_HISTORY = 100


class EditNotFoundError(Exception):
    """The Edit does not exist, or the caller may not know that it does."""


class CandidateNotEditableError(Exception):
    """The Clip Candidate cannot be opened as an Edit, for reasons a caller may not learn."""


class CompositionAssetError(Exception):
    """The composition names media this Project does not own."""

    def __init__(self, unauthorized: frozenset[UUID]) -> None:
        """Carry the offending asset identity for the audit trail, never for the client."""
        super().__init__("composition names unauthorized assets")
        self.unauthorized = unauthorized


class EditRevisionConflictError(Exception):
    """Another save already became the Revision this one expected to write."""

    def __init__(self, current_revision: int) -> None:
        """Carry the Revision the caller must reconcile against before saving again."""
        super().__init__(f"edit is at revision {current_revision}")
        self.current_revision = current_revision


def create_edit_from_candidate(
    repository: EditRepository,
    *,
    access: WorkspaceAccess,
    project_id: UUID,
    candidate_id: UUID,
    now: datetime,
) -> tuple[EditDetail, bool]:
    """Open the one Edit belonging to a reviewed candidate, or reach the existing one.

    The second return value says whether this call created the Edit, so the HTTP layer
    can answer a repeated click as a replay rather than as a second creation.
    """
    repository.lock_candidate(workspace_id=access.workspace_id, candidate_id=candidate_id)
    existing = repository.edit_for_candidate(
        workspace_id=access.workspace_id, candidate_id=candidate_id
    )
    if existing is not None:
        return existing, False
    seed = repository.candidate_seed(
        workspace_id=access.workspace_id, project_id=project_id, candidate_id=candidate_id
    )
    if seed is None:
        raise CandidateNotEditableError(str(candidate_id))
    composition = initial_composition(seed)
    edit_id = repository.create(
        workspace_id=access.workspace_id,
        candidate_id=candidate_id,
        created_by_user_id=access.user_id,
        composition=_stored_document(composition),
        composition_hash=composition_hash(composition),
        now=now,
    )
    created = repository.detail(workspace_id=access.workspace_id, edit_id=edit_id)
    if created is None:  # pragma: no cover - the row was written in this transaction
        raise EditNotFoundError(str(edit_id))
    return created, True


def get_edit(repository: EditRepository, *, access: WorkspaceAccess, edit_id: UUID) -> EditDetail:
    """Read one Edit, hiding another Workspace's Edit behind the same absence."""
    detail = repository.detail(workspace_id=access.workspace_id, edit_id=edit_id)
    if detail is None:
        raise EditNotFoundError(str(edit_id))
    return detail


def list_revisions(
    repository: EditRepository,
    *,
    access: WorkspaceAccess,
    edit_id: UUID,
    limit: int = MAX_REVISION_HISTORY,
) -> tuple[RevisionSummary, ...]:
    """Read the history of one Edit, newest Revision first."""
    if repository.detail(workspace_id=access.workspace_id, edit_id=edit_id) is None:
        raise EditNotFoundError(str(edit_id))
    return repository.revisions(workspace_id=access.workspace_id, edit_id=edit_id, limit=limit)


def save_revision(
    repository: EditRepository,
    *,
    access: WorkspaceAccess,
    edit_id: UUID,
    expected_revision: int,
    document: dict[str, Any],
    now: datetime,
) -> EditDetail:
    """Append one composition as the next Revision of an Edit.

    The Edit row is locked first, so a second save waits for the first to commit and
    then finds a Revision it did not expect, rather than overwriting it. A save that
    changes nothing is answered with the Revision that already holds those bytes.
    """
    locked = repository.lock(workspace_id=access.workspace_id, edit_id=edit_id)
    if locked is None:
        raise EditNotFoundError(str(edit_id))
    composition = parse_composition(document)
    authorized = repository.project_asset_ids(
        workspace_id=access.workspace_id, project_id=locked.project_id
    )
    unauthorized = collect_asset_ids(composition) - authorized
    if unauthorized:
        raise CompositionAssetError(unauthorized)
    if locked.current_revision != expected_revision:
        raise EditRevisionConflictError(locked.current_revision)
    digest = composition_hash(composition)
    if digest == locked.composition_hash:
        return get_edit(repository, access=access, edit_id=edit_id)
    repository.append_revision(
        workspace_id=access.workspace_id,
        edit_id=edit_id,
        revision=expected_revision + 1,
        composition=_stored_document(composition),
        composition_hash=digest,
        created_by_user_id=access.user_id,
        now=now,
    )
    if not repository.advance_current_revision(
        workspace_id=access.workspace_id,
        edit_id=edit_id,
        expected_revision=expected_revision,
        now=now,
    ):  # pragma: no cover - the row lock above already decided this
        raise EditRevisionConflictError(locked.current_revision)
    return get_edit(repository, access=access, edit_id=edit_id)


def initial_composition(seed: CandidateSeed) -> CompositionV1:
    """Build the first composition of a reviewed candidate from the transcript alone.

    The clip is the candidate's own span of the source, its captions are the words the
    analysis keyed the candidate to, and nothing else is invented on the member's behalf.
    """
    duration_ms = seed.end_ms - seed.start_ms
    origin = Origin(type=OriginType.SOURCE, suggestion_id=None, provenance_id=None)
    item = TrackItem(
        id=FIRST_SCENE_ITEM_ID,
        source_asset_id=seed.source_asset_id,
        timeline_start_ms=0,
        source_in_ms=seed.start_ms,
        source_out_ms=seed.end_ms,
        transform=Transform(x=0.5, y=0.5, scale=1.0, rotation=0.0),
        crop=None,
        opacity=1.0,
        blend_mode=BlendMode.NORMAL,
        motion=MotionPreset.NONE,
        origin=origin,
        keyframes=(),
    )
    return CompositionV1(
        schema_version=SCHEMA_VERSION,
        source_asset_id=seed.source_asset_id,
        duration_ms=duration_ms,
        canvas=DEFAULT_CANVAS,
        source_range=SourceRange(in_ms=seed.start_ms, out_ms=seed.end_ms),
        template=None,
        brand_kit=None,
        tracks=(Track(id=MAIN_VIDEO_TRACK_ID, type=TrackType.VIDEO, items=(item,)),),
        captions=Captions(
            mode=CaptionMode.KARAOKE,
            words=tuple(
                CaptionWord(
                    id=word.word_id,
                    start_ms=word.start_ms - seed.start_ms,
                    end_ms=word.end_ms - seed.start_ms,
                    text=word.text,
                    speaker=word.speaker,
                )
                for word in seed.words
            ),
            style=default_caption_style(),
        ),
        overlays=(),
        audio=AudioMix(gain_db=0.0, music_gain_db=-18.0),
        bookmarks=(),
    )


def default_caption_style() -> CaptionStyle:
    """Return the caption type a first Edit starts from."""
    return CaptionStyle(
        font_family=FontFamily.MONTSERRAT,
        font_size=64,
        color="#FFFFFF",
        highlight_color="#FFD166",
        align=TextAlign.CENTER,
        weight=700,
        italic=False,
        decoration=TextDecoration.NONE,
        letter_spacing=0.0,
        line_height=1.2,
        background_enabled=False,
        background_color="#000000",
    )


def _stored_document(composition: CompositionV1) -> dict[str, Any]:
    """Store exactly the bytes the composition hash was taken over."""
    stored: dict[str, Any] = json.loads(canonical_json(composition))
    return stored
