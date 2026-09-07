"""Private persistence for Campaign Outputs and the Revision each one describes.

Copy is written once and never rewritten: a stored output is what a member actually read.
A repeated request therefore converges on the rows it already produced rather than quietly
replacing them with a second reading of the same clip.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from clipah.campaigns.generator import ClipSummary
from clipah.campaigns.models import CampaignLanguage, CampaignOutputDraft
from clipah.models import (
    CampaignOutput,
    ClipCandidate,
    ClipEdit,
    ClipEditRevision,
    Project,
    Transcript,
)
from clipah.variants.models import Platform


@dataclass(frozen=True, slots=True)
class RevisionTarget:
    """One immutable Revision and everything the copy about it is derived from."""

    edit_id: UUID
    project_id: UUID
    revision_id: UUID
    revision: int
    composition: dict[str, Any]
    clip: ClipSummary


@dataclass(frozen=True, slots=True)
class CampaignOutputSummary:
    """One stored piece of copy, detached from SQLAlchemy for the API to render."""

    output_id: UUID
    revision_id: UUID
    revision: int
    platform: Platform
    language: CampaignLanguage
    title: str
    post_copy: str
    cta: str
    hashtags: tuple[str, ...]
    thumbnail_brief: dict[str, Any]
    warnings: tuple[dict[str, Any], ...]
    model_metadata: dict[str, Any]
    created_at: datetime


class CampaignRepository:
    """Keep Campaign Output ORM details behind one tenant-scoped boundary."""

    def __init__(self, session: Session) -> None:
        """Bind persistence to the transaction holding verified RLS context."""
        self._session = session

    def revision_target(
        self, *, workspace_id: UUID, edit_id: UUID, revision: int
    ) -> RevisionTarget | None:
        """Read one exact Revision of an Edit, with the analysis behind its clip.

        The Revision, the candidate, and the transcript are read together because copy
        derived from a cut without the words it holds would be copy about nothing — there
        is exactly one way to have no target, which is no row this Workspace can see.
        """
        row = self._session.execute(
            select(ClipEdit, ClipEditRevision, ClipCandidate, Transcript)
            .join(
                ClipEditRevision,
                (ClipEditRevision.workspace_id == ClipEdit.workspace_id)
                & (ClipEditRevision.clip_edit_id == ClipEdit.id)
                & (ClipEditRevision.revision == revision),
            )
            .join(
                ClipCandidate,
                (ClipCandidate.workspace_id == ClipEdit.workspace_id)
                & (ClipCandidate.id == ClipEdit.candidate_id),
            )
            .join(
                Transcript,
                (Transcript.workspace_id == ClipCandidate.workspace_id)
                & (Transcript.id == ClipCandidate.transcript_id),
            )
            .join(
                Project,
                (Project.workspace_id == ClipCandidate.workspace_id)
                & (Project.id == ClipCandidate.project_id),
            )
            .where(
                ClipEdit.workspace_id == workspace_id,
                ClipEdit.id == edit_id,
                Project.archived_at.is_(None),
            )
        ).first()
        if row is None:
            return None
        edit, stored_revision, candidate, transcript = row
        return RevisionTarget(
            edit_id=edit.id,
            project_id=candidate.project_id,
            revision_id=stored_revision.id,
            revision=stored_revision.revision,
            composition=dict(stored_revision.composition),
            clip=ClipSummary(
                hook=candidate.hook,
                payoff=candidate.payoff,
                reason=candidate.reason,
                category=str(candidate.category),
                tags=tuple(str(tag) for tag in candidate.tags),
                quote=candidate.transcript_excerpt,
                quote_language=transcript.language,
                duration_ms=candidate.end_ms - candidate.start_ms,
                context_warnings=tuple(str(warning) for warning in candidate.context_warnings),
            ),
        )

    def record_outputs(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        revision_id: UUID,
        drafts: tuple[CampaignOutputDraft, ...],
        created_by_user_id: UUID,
    ) -> None:
        """Store every derived output once, converging on the rows already written."""
        if not drafts:  # pragma: no cover - a request always names one platform and language
            return
        self._session.execute(
            insert(CampaignOutput)
            .values(
                [
                    {
                        "id": uuid4(),
                        "workspace_id": workspace_id,
                        "project_id": project_id,
                        "clip_edit_revision_id": revision_id,
                        "platform": draft.platform,
                        "language": draft.language,
                        "title": draft.title,
                        "post_copy": draft.post_copy,
                        "cta": draft.cta,
                        "hashtags": list(draft.hashtags),
                        "thumbnail_brief": draft.thumbnail_brief.model_dump(mode="json"),
                        "warnings": [warning.model_dump(mode="json") for warning in draft.warnings],
                        "model_metadata": draft.model_metadata.model_dump(mode="json"),
                        "created_by_user_id": created_by_user_id,
                    }
                    for draft in drafts
                ]
            )
            .on_conflict_do_nothing(constraint="uq_campaign_outputs_revision_platform_language")
        )
        self._session.flush()

    def outputs_for_edit(
        self, *, workspace_id: UUID, edit_id: UUID
    ) -> tuple[CampaignOutputSummary, ...]:
        """Read every piece of copy derived from any Revision of one Edit."""
        rows = self._session.execute(
            select(CampaignOutput, ClipEditRevision.revision)
            .join(
                ClipEditRevision,
                (ClipEditRevision.workspace_id == CampaignOutput.workspace_id)
                & (ClipEditRevision.id == CampaignOutput.clip_edit_revision_id),
            )
            .where(
                CampaignOutput.workspace_id == workspace_id,
                ClipEditRevision.clip_edit_id == edit_id,
            )
            .order_by(
                ClipEditRevision.revision,
                CampaignOutput.platform,
                CampaignOutput.language,
                CampaignOutput.id,
            )
        ).all()
        return tuple(_summary(row, revision) for row, revision in rows)


def _summary(row: CampaignOutput, revision: int) -> CampaignOutputSummary:
    """Detach exactly the copy a member is allowed to read."""
    return CampaignOutputSummary(
        output_id=row.id,
        revision_id=row.clip_edit_revision_id,
        revision=revision,
        platform=row.platform,
        language=row.language,
        title=row.title,
        post_copy=row.post_copy,
        cta=row.cta,
        hashtags=tuple(str(hashtag) for hashtag in row.hashtags),
        thumbnail_brief=dict(row.thumbnail_brief),
        warnings=tuple(dict(warning) for warning in row.warnings),
        model_metadata=dict(row.model_metadata),
        created_at=row.created_at,
    )
