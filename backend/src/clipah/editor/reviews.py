"""Immutable Edit Revision comments, decisions, and derived review state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from clipah.editor.models import parse_composition
from clipah.models import (
    ClipEdit,
    ClipEditRevision,
    EditReviewComment,
    EditReviewCommentResolution,
    EditReviewDecision,
    EditReviewDecisionKind,
    ReviewAnchorKind,
    User,
)
from clipah.workspaces.models import WorkspaceAccess


class ReviewNotFoundError(Exception):
    """The Edit, Revision, or comment is unavailable to this Workspace."""


class ReviewInvalidError(Exception):
    """A review anchor or comment does not describe its immutable Revision."""


@dataclass(frozen=True, slots=True)
class ReviewAnchor:
    """One timestamp or composition item identified by a review comment."""

    kind: ReviewAnchorKind
    timestamp_ms: int | None = None
    item_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewComment:
    """One comment with its current resolution state."""

    comment_id: UUID
    revision_id: UUID
    revision: int
    actor_user_id: UUID
    actor_display_name: str
    text: str
    anchor: ReviewAnchor
    resolved: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    """One historical decision and whether its Revision is still current."""

    decision_id: UUID
    revision_id: UUID
    revision: int
    actor_user_id: UUID
    actor_display_name: str
    decision: EditReviewDecisionKind
    sequence: int
    current: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewSummary:
    """The current approval answer and complete ordered review evidence."""

    approved: bool
    comments: tuple[ReviewComment, ...]
    decisions: tuple[ReviewDecision, ...]


def add_comment(
    session: Session,
    *,
    access: WorkspaceAccess,
    edit_id: UUID,
    revision_id: UUID,
    text: str,
    anchor: ReviewAnchor,
    now: datetime,
) -> ReviewComment:
    """Append one plain-text comment after validating its immutable anchor."""
    _, revision = _target(
        session,
        workspace_id=access.workspace_id,
        edit_id=edit_id,
        revision_id=revision_id,
        lock_edit=False,
    )
    normalized = text.strip()
    if not normalized or len(normalized) > 4_000:
        raise ReviewInvalidError("comment text is outside the supported bounds")
    _validate_anchor(revision, anchor)
    comment = EditReviewComment(
        id=uuid4(),
        workspace_id=access.workspace_id,
        clip_edit_id=edit_id,
        clip_edit_revision_id=revision_id,
        created_by_user_id=access.user_id,
        text=normalized,
        anchor_kind=anchor.kind,
        anchor_ms=anchor.timestamp_ms,
        item_id=anchor.item_id,
        created_at=now,
    )
    session.add(comment)
    session.flush()
    return _comment(session, comment, resolved=False)


def record_decision(
    session: Session,
    *,
    access: WorkspaceAccess,
    edit_id: UUID,
    revision_id: UUID,
    decision: EditReviewDecisionKind,
    now: datetime,
) -> ReviewDecision:
    """Append one sequenced decision against an exact immutable Revision."""
    edit, _ = _target(
        session,
        workspace_id=access.workspace_id,
        edit_id=edit_id,
        revision_id=revision_id,
        lock_edit=True,
    )
    sequence = (
        session.scalar(
            select(func.max(EditReviewDecision.sequence)).where(
                EditReviewDecision.workspace_id == access.workspace_id,
                EditReviewDecision.clip_edit_id == edit_id,
            )
        )
        or 0
    ) + 1
    row = EditReviewDecision(
        id=uuid4(),
        workspace_id=access.workspace_id,
        clip_edit_id=edit_id,
        clip_edit_revision_id=revision_id,
        actor_user_id=access.user_id,
        sequence=sequence,
        decision=decision,
        created_at=now,
    )
    session.add(row)
    session.flush()
    return _decision(session, row, current=_revision_is_current(session, edit, revision_id))


def resolve_comment(
    session: Session,
    *,
    access: WorkspaceAccess,
    comment_id: UUID,
    resolved: bool,
    now: datetime,
) -> ReviewComment:
    """Append one sequenced resolution or reopening event for a comment."""
    session.execute(
        sql_text("SELECT pg_advisory_xact_lock(hashtext(:subject))"),
        {"subject": f"edit-review-comment:{comment_id}"},
    )
    comment = session.execute(
        select(EditReviewComment).where(
            EditReviewComment.workspace_id == access.workspace_id,
            EditReviewComment.id == comment_id,
        )
    ).scalar_one_or_none()
    if comment is None:
        raise ReviewNotFoundError("no such review comment")
    sequence = (
        session.scalar(
            select(func.max(EditReviewCommentResolution.sequence)).where(
                EditReviewCommentResolution.workspace_id == access.workspace_id,
                EditReviewCommentResolution.comment_id == comment_id,
            )
        )
        or 0
    ) + 1
    session.add(
        EditReviewCommentResolution(
            id=uuid4(),
            workspace_id=access.workspace_id,
            comment_id=comment_id,
            actor_user_id=access.user_id,
            sequence=sequence,
            resolved=resolved,
            created_at=now,
        )
    )
    session.flush()
    return _comment(session, comment, resolved=resolved)


def review_summary(session: Session, *, access: WorkspaceAccess, edit_id: UUID) -> ReviewSummary:
    """Read comments and decisions newest-first with current approval derived live."""
    edit = session.execute(
        select(ClipEdit).where(
            ClipEdit.workspace_id == access.workspace_id,
            ClipEdit.id == edit_id,
        )
    ).scalar_one_or_none()
    if edit is None:
        raise ReviewNotFoundError("no such Edit")
    current_revision_id = session.scalar(
        select(ClipEditRevision.id).where(
            ClipEditRevision.workspace_id == access.workspace_id,
            ClipEditRevision.clip_edit_id == edit_id,
            ClipEditRevision.revision == edit.current_revision,
        )
    )
    decisions = tuple(
        _decision(session, row, current=row.clip_edit_revision_id == current_revision_id)
        for row in session.scalars(
            select(EditReviewDecision)
            .where(
                EditReviewDecision.workspace_id == access.workspace_id,
                EditReviewDecision.clip_edit_id == edit_id,
            )
            .order_by(EditReviewDecision.sequence.desc())
        )
    )
    resolution_rows = session.execute(
        select(
            EditReviewCommentResolution.comment_id,
            EditReviewCommentResolution.resolved,
        )
        .join(EditReviewComment, EditReviewComment.id == EditReviewCommentResolution.comment_id)
        .where(EditReviewComment.workspace_id == access.workspace_id)
        .order_by(
            EditReviewCommentResolution.comment_id,
            EditReviewCommentResolution.sequence.desc(),
        )
    ).all()
    resolved_by_comment: dict[UUID, bool] = {}
    for comment_id, resolved in resolution_rows:
        resolved_by_comment.setdefault(comment_id, resolved)
    comments = tuple(
        _comment(session, row, resolved=resolved_by_comment.get(row.id, False))
        for row in session.scalars(
            select(EditReviewComment)
            .where(
                EditReviewComment.workspace_id == access.workspace_id,
                EditReviewComment.clip_edit_id == edit_id,
            )
            .order_by(EditReviewComment.created_at.desc(), EditReviewComment.id.desc())
        )
    )
    approved = bool(
        decisions
        and decisions[0].current
        and decisions[0].decision is EditReviewDecisionKind.APPROVE
    )
    return ReviewSummary(approved=approved, comments=comments, decisions=decisions)


def has_current_approval(
    session: Session, *, workspace_id: UUID, edit_id: UUID, revision_id: UUID
) -> bool:
    """Report whether the named Revision is current and its latest decision approves it."""
    current_revision_id = session.scalar(
        select(ClipEditRevision.id)
        .join(
            ClipEdit,
            (ClipEdit.workspace_id == ClipEditRevision.workspace_id)
            & (ClipEdit.id == ClipEditRevision.clip_edit_id)
            & (ClipEdit.current_revision == ClipEditRevision.revision),
        )
        .where(
            ClipEdit.workspace_id == workspace_id,
            ClipEdit.id == edit_id,
        )
    )
    if current_revision_id != revision_id:
        return False
    latest = session.execute(
        select(
            EditReviewDecision.clip_edit_revision_id,
            EditReviewDecision.decision,
        )
        .where(
            EditReviewDecision.workspace_id == workspace_id,
            EditReviewDecision.clip_edit_id == edit_id,
        )
        .order_by(EditReviewDecision.sequence.desc())
        .limit(1)
    ).one_or_none()
    return bool(
        latest is not None
        and latest.clip_edit_revision_id == revision_id
        and latest.decision is EditReviewDecisionKind.APPROVE
    )


def revision_document(
    session: Session, *, workspace_id: UUID, edit_id: UUID, revision_id: UUID
) -> dict[str, Any]:
    """Read one immutable composition inside its Workspace boundary."""
    _, revision = _target(
        session,
        workspace_id=workspace_id,
        edit_id=edit_id,
        revision_id=revision_id,
        lock_edit=False,
    )
    return dict(revision.composition)


def _target(
    session: Session,
    *,
    workspace_id: UUID,
    edit_id: UUID,
    revision_id: UUID,
    lock_edit: bool,
) -> tuple[ClipEdit, ClipEditRevision]:
    """Resolve an Edit and one of its Revisions inside one Workspace."""
    edit_query = select(ClipEdit).where(
        ClipEdit.workspace_id == workspace_id,
        ClipEdit.id == edit_id,
    )
    if lock_edit:
        edit_query = edit_query.with_for_update()
    edit = session.execute(edit_query).scalar_one_or_none()
    revision = session.execute(
        select(ClipEditRevision).where(
            ClipEditRevision.workspace_id == workspace_id,
            ClipEditRevision.id == revision_id,
            ClipEditRevision.clip_edit_id == edit_id,
        )
    ).scalar_one_or_none()
    if edit is None or revision is None:
        raise ReviewNotFoundError("no such Edit Revision")
    return edit, revision


def _validate_anchor(revision: ClipEditRevision, anchor: ReviewAnchor) -> None:
    """Prove one anchor exists inside the stored composition."""
    composition = parse_composition(dict(revision.composition))
    if anchor.kind is ReviewAnchorKind.TIMESTAMP:
        if anchor.timestamp_ms is None or anchor.item_id is not None:
            raise ReviewInvalidError("timestamp anchor is incomplete")
        if not 0 <= anchor.timestamp_ms <= composition.duration_ms:
            raise ReviewInvalidError("timestamp anchor is outside the Revision")
        return
    if anchor.kind is ReviewAnchorKind.ITEM:
        if anchor.item_id is None or anchor.timestamp_ms is not None:
            raise ReviewInvalidError("item anchor is incomplete")
        item_ids = {
            *(item.id for track in composition.tracks for item in track.items),
            *(overlay.id for overlay in composition.overlays),
        }
        if anchor.item_id not in item_ids:
            raise ReviewInvalidError("item anchor is outside the Revision")
        return
    raise ReviewInvalidError("unknown anchor kind")


def _revision_is_current(session: Session, edit: ClipEdit, revision_id: UUID) -> bool:
    """Compare an immutable Revision identity with the Edit's current sequence."""
    return bool(
        session.scalar(
            select(ClipEditRevision.id).where(
                ClipEditRevision.workspace_id == edit.workspace_id,
                ClipEditRevision.clip_edit_id == edit.id,
                ClipEditRevision.revision == edit.current_revision,
                ClipEditRevision.id == revision_id,
            )
        )
    )


def _comment(session: Session, row: EditReviewComment, *, resolved: bool) -> ReviewComment:
    """Convert one persistence row into a public domain value."""
    return ReviewComment(
        comment_id=row.id,
        revision_id=row.clip_edit_revision_id,
        revision=_revision_number(session, row.clip_edit_revision_id),
        actor_user_id=row.created_by_user_id,
        actor_display_name=session.get_one(User, row.created_by_user_id).display_name,
        text=row.text,
        anchor=ReviewAnchor(kind=row.anchor_kind, timestamp_ms=row.anchor_ms, item_id=row.item_id),
        resolved=resolved,
        created_at=row.created_at,
    )


def _decision(session: Session, row: EditReviewDecision, *, current: bool) -> ReviewDecision:
    """Convert one persistence row into a public domain value."""
    return ReviewDecision(
        decision_id=row.id,
        revision_id=row.clip_edit_revision_id,
        revision=_revision_number(session, row.clip_edit_revision_id),
        actor_user_id=row.actor_user_id,
        actor_display_name=session.get_one(User, row.actor_user_id).display_name,
        decision=row.decision,
        sequence=row.sequence,
        current=current,
        created_at=row.created_at,
    )


def _revision_number(session: Session, revision_id: UUID) -> int:
    """Resolve the stable display sequence for one already-authorized Revision."""
    return session.execute(
        select(ClipEditRevision.revision).where(ClipEditRevision.id == revision_id)
    ).scalar_one()
