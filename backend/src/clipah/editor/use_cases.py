"""Editing decisions: opening an Edit, reading it, and saving the next Revision.

An Edit is created from a reviewed Clip Candidate and never rewritten. Every save
appends a Revision, and which save wins is decided in Postgres rather than in a
browser, because two members editing one clip is the ordinary case, not the rare one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from clipah.brands.models import (
    WorkspaceTemplateDefinition,
    apply_brand_kit,
    apply_workspace_template,
)
from clipah.broll.models import BrollSuggestionStatus
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
    ImageOverlay,
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
    VideoOverlay,
    canonical_json,
    collect_asset_ids,
    composition_hash,
    parse_composition,
)
from clipah.editor.repository import (
    CandidateSeed,
    DecidableSuggestion,
    EditDetail,
    EditRepository,
    RevisionSummary,
)
from clipah.workspaces.models import WorkspaceAccess

DEFAULT_CANVAS = Canvas(width=1080, height=1920, background="#000000")
MAIN_VIDEO_TRACK_ID = "main-video"
FIRST_SCENE_ITEM_ID = "scene-1"
MAX_REVISION_HISTORY = 100


@dataclass(frozen=True, slots=True)
class TemplateSelection:
    """One published look a member chose, resolved by the caller before it is applied."""

    template_id: UUID
    version: int
    definition: WorkspaceTemplateDefinition


@dataclass(frozen=True, slots=True)
class BrandKitSelection:
    """One published Brand Kit version a member chose to be judged against."""

    brand_kit_id: UUID
    version: int
    logo_asset_id: UUID | None


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


class BrollSuggestionNotFoundError(Exception):
    """The suggestion does not exist, or belongs to a clip this Edit does not cover."""


class BrollDecisionError(Exception):
    """The decision contradicts the composition the caller sent with it."""


class BrollDecision(StrEnum):
    """What a member may decide about one proposed picture."""

    ACCEPT = "accept"
    REPLACE = "replace"
    REMOVE = "remove"
    REJECT = "reject"


# Accepting places a picture, so there is no moment in which a suggestion is agreed to
# but not on the timeline: the composition that carries it is saved in the same call.
_DECIDED_STATUS: dict[BrollDecision, BrollSuggestionStatus] = {
    BrollDecision.ACCEPT: BrollSuggestionStatus.PLACED,
    BrollDecision.REPLACE: BrollSuggestionStatus.REPLACED,
    BrollDecision.REMOVE: BrollSuggestionStatus.REMOVED,
    BrollDecision.REJECT: BrollSuggestionStatus.REJECTED,
}


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
    template: TemplateSelection | None = None,
    brand_kit: BrandKitSelection | None = None,
) -> tuple[EditDetail, bool]:
    """Open the one Edit belonging to a reviewed candidate, or reach the existing one.

    The second return value says whether this call created the Edit, so the HTTP layer
    can answer a repeated click as a replay rather than as a second creation. A look and
    a Brand Kit are applied only to an Edit this call creates: reaching an Edit somebody
    already opened must not restyle the work they have done in it.
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
    if template is not None:
        composition = apply_workspace_template(
            composition,
            template_id=template.template_id,
            version=template.version,
            definition=template.definition,
        )
    if brand_kit is not None:
        composition = apply_brand_kit(
            composition,
            brand_kit_id=brand_kit.brand_kit_id,
            version=brand_kit.version,
            logo_asset_id=brand_kit.logo_asset_id,
        )
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


def decide_on_suggestion(
    repository: EditRepository,
    *,
    access: WorkspaceAccess,
    edit_id: UUID,
    suggestion_id: UUID,
    action: BrollDecision,
    replacement_asset_id: UUID | None,
    expected_revision: int,
    document: dict[str, Any],
    now: datetime,
) -> EditDetail:
    """Record one B-roll decision and the composition it produced, together or not at all.

    A decision and its Revision are the same event seen twice: the member's answer to a
    proposal, and the document that answer produced. Writing one without the other would
    leave a suggestion the timeline contradicts, so both happen in this transaction and
    the same optimistic concurrency that guards every save guards this one — which is
    what stops two tabs from placing one picture twice.
    """
    locked = repository.lock(workspace_id=access.workspace_id, edit_id=edit_id)
    if locked is None:
        raise EditNotFoundError(str(edit_id))
    suggestion = repository.suggestion_for_edit(
        workspace_id=access.workspace_id, edit_id=edit_id, suggestion_id=suggestion_id
    )
    if suggestion is None:
        raise BrollSuggestionNotFoundError(str(suggestion_id))
    composition = parse_composition(document)
    _reject_inconsistent_decision(
        composition,
        suggestion=suggestion,
        action=action,
        replacement_asset_id=replacement_asset_id,
    )
    detail = save_revision(
        repository,
        access=access,
        edit_id=edit_id,
        expected_revision=expected_revision,
        document=document,
        now=now,
    )
    repository.record_decision(
        workspace_id=access.workspace_id,
        suggestion_id=suggestion_id,
        status=_DECIDED_STATUS[action],
        # Only a replacement changes which picture a suggestion holds. Every other
        # decision is about the picture already chosen, so it leaves that column alone.
        asset_id=replacement_asset_id if action is BrollDecision.REPLACE else None,
        edit_id=None if action is BrollDecision.REJECT else edit_id,
        now=now,
    )
    return detail


def _reject_inconsistent_decision(
    composition: CompositionV1,
    *,
    suggestion: DecidableSuggestion,
    action: BrollDecision,
    replacement_asset_id: UUID | None,
) -> None:
    """Prove the decision and the composition describe the same clip.

    The document is the evidence. A member who says they accepted a suggestion must have
    sent a composition that draws it, and a member who says they removed one must have
    sent a composition that does not — otherwise the stored decision would describe a
    clip nobody would ever see.
    """
    drawn = tuple(
        overlay
        for overlay in composition.overlays
        if isinstance(overlay, VideoOverlay | ImageOverlay)
        and overlay.origin.suggestion_id == suggestion.suggestion_id
    )
    if action in {BrollDecision.REMOVE, BrollDecision.REJECT}:
        if drawn:
            raise BrollDecisionError("the composition still draws this suggestion")
        return
    if len(drawn) != 1:
        raise BrollDecisionError("an accepted suggestion is drawn exactly once")
    expected = suggestion.asset_id if action is BrollDecision.ACCEPT else replacement_asset_id
    if expected is None:
        raise BrollDecisionError("this decision names no media")
    if drawn[0].asset_id != expected:
        raise BrollDecisionError("the overlay draws media this decision did not choose")


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
