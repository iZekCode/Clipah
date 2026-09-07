"""Private persistence for Clip Variants and Claim Evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from clipah.models import (
    ClaimEvidence,
    ClaimVerificationStatus,
    ClipCandidate,
    ClipVariant,
    Project,
    ProjectStatus,
    Transcript,
)
from clipah.transcripts.models import TranscriptWord
from clipah.variants.generator import ClipVariantDraft


@dataclass(frozen=True, slots=True)
class CandidateTarget:
    """One reviewable candidate, and the transcript words its boundaries name."""

    project_id: UUID
    candidate_id: UUID
    hook: str
    start_word_id: str
    end_word_id: str
    words: tuple[TranscriptWord, ...]


@dataclass(frozen=True, slots=True)
class VariantSummary:
    """One stored Variant, detached from SQLAlchemy for the API to render."""

    variant_id: UUID
    candidate_id: UUID
    hook_strategy: str
    platform: str
    target_duration_ms: int
    start_word_id: str
    end_word_id: str
    start_ms: int
    end_ms: int
    title: str
    rationale: str
    warnings: tuple[dict[str, Any], ...]
    packaging: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class EvidenceSummary:
    """One stored citation, as a reviewer is allowed to read it."""

    evidence_id: UUID
    candidate_id: UUID
    start_word_id: str
    end_word_id: str
    claim_text: str
    source_url: str
    source_title: str
    publisher: str
    retrieved_at: datetime
    verification_status: ClaimVerificationStatus
    created_by_user_id: UUID
    created_at: datetime


class VariantRepository:
    """Keep Variant and evidence ORM details behind one tenant-scoped boundary."""

    def __init__(self, session: Session) -> None:
        """Bind persistence to the transaction holding verified RLS context."""
        self._session = session

    def candidate_target(
        self, *, workspace_id: UUID, project_id: UUID, candidate_id: UUID
    ) -> CandidateTarget | None:
        """Read one exposed candidate of a ready Project, with its transcript words.

        The candidate and its Transcript are read together because a boundary without the
        words it names cannot be assessed, cut, or cited — there is exactly one way to
        have no target, which is no row this Workspace can see.
        """
        row = self._session.execute(
            select(ClipCandidate, Transcript)
            .join(
                Project,
                (Project.workspace_id == ClipCandidate.workspace_id)
                & (Project.id == ClipCandidate.project_id),
            )
            .join(
                Transcript,
                (Transcript.workspace_id == ClipCandidate.workspace_id)
                & (Transcript.id == ClipCandidate.transcript_id),
            )
            .where(
                ClipCandidate.workspace_id == workspace_id,
                ClipCandidate.project_id == project_id,
                ClipCandidate.id == candidate_id,
                ClipCandidate.model_metadata["exposed"].as_boolean().is_(True),
                Project.status == ProjectStatus.READY,
                Project.archived_at.is_(None),
            )
        ).first()
        if row is None:
            return None
        candidate, transcript = row
        return CandidateTarget(
            project_id=candidate.project_id,
            candidate_id=candidate.id,
            hook=candidate.hook,
            start_word_id=candidate.start_word_id,
            end_word_id=candidate.end_word_id,
            words=tuple(_word(entry) for entry in transcript.words),
        )

    def record_variants(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        candidate_id: UUID,
        drafts: tuple[ClipVariantDraft, ...],
    ) -> None:
        """Store every accepted Variant once, converging on the rows already written.

        A repeated request for the same strategy, target, and platform reaches the row it
        already produced rather than a second copy: the boundary a member was shown is
        the boundary that stays stored.
        """
        if not drafts:
            return
        self._session.execute(
            insert(ClipVariant)
            .values(
                [
                    {
                        "id": uuid4(),
                        "workspace_id": workspace_id,
                        "project_id": project_id,
                        "candidate_id": candidate_id,
                        "hook_strategy": draft.hook_strategy,
                        "platform": draft.platform,
                        "target_duration_ms": draft.target_duration_ms,
                        "start_word_id": draft.start_word_id,
                        "end_word_id": draft.end_word_id,
                        "start_ms": draft.start_ms,
                        "end_ms": draft.end_ms,
                        "title": draft.title,
                        "rationale": draft.rationale,
                        "warnings": [warning.model_dump(mode="json") for warning in draft.warnings],
                        "packaging": _packaging(draft),
                    }
                    for draft in drafts
                ]
            )
            .on_conflict_do_nothing(
                constraint="uq_clip_variants_candidate_strategy_target_platform"
            )
        )
        self._session.flush()

    def variants_for_candidate(
        self, *, workspace_id: UUID, candidate_id: UUID
    ) -> tuple[VariantSummary, ...]:
        """Read one candidate's stored Variants in a stable, comparable order."""
        rows = self._session.scalars(
            select(ClipVariant)
            .where(
                ClipVariant.workspace_id == workspace_id,
                ClipVariant.candidate_id == candidate_id,
            )
            .order_by(
                ClipVariant.target_duration_ms,
                ClipVariant.hook_strategy,
                ClipVariant.platform,
                ClipVariant.id,
            )
        ).all()
        return tuple(_variant(row) for row in rows)

    def record_evidence(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        candidate_id: UUID,
        start_word_id: str,
        end_word_id: str,
        claim_text: str,
        source_url: str,
        source_title: str,
        publisher: str,
        retrieved_at: datetime,
        created_by_user_id: UUID,
    ) -> EvidenceSummary:
        """Store one citation exactly as its author wrote it, unverified by default."""
        row = ClaimEvidence(
            id=uuid4(),
            workspace_id=workspace_id,
            project_id=project_id,
            candidate_id=candidate_id,
            start_word_id=start_word_id,
            end_word_id=end_word_id,
            claim_text=claim_text,
            source_url=source_url,
            source_title=source_title,
            publisher=publisher,
            retrieved_at=retrieved_at,
            verification_status=ClaimVerificationStatus.UNVERIFIED,
            created_by_user_id=created_by_user_id,
        )
        self._session.add(row)
        self._session.flush()
        return _evidence(row)

    def evidence_for_candidate(
        self, *, workspace_id: UUID, candidate_id: UUID
    ) -> tuple[EvidenceSummary, ...]:
        """Read every citation attached to one candidate, earliest claim first."""
        rows = self._session.scalars(
            select(ClaimEvidence)
            .where(
                ClaimEvidence.workspace_id == workspace_id,
                ClaimEvidence.candidate_id == candidate_id,
            )
            .order_by(ClaimEvidence.start_word_id, ClaimEvidence.created_at, ClaimEvidence.id)
        ).all()
        return tuple(_evidence(row) for row in rows)

    def lock_evidence(self, *, workspace_id: UUID, evidence_id: UUID) -> ClaimEvidence | None:
        """Lock one citation of this Workspace before a member changes what it claims."""
        return self._session.scalar(
            select(ClaimEvidence)
            .where(
                ClaimEvidence.workspace_id == workspace_id,
                ClaimEvidence.id == evidence_id,
            )
            .with_for_update()
        )


def summarize_evidence(row: ClaimEvidence) -> EvidenceSummary:
    """Detach one citation from SQLAlchemy after a use case has changed it."""
    return _evidence(row)


def _packaging(draft: ClipVariantDraft) -> dict[str, Any]:
    """Record what the destination asked of this cut, beside the cut itself."""
    packaging = draft.packaging
    return {
        "platform": packaging.platform.value,
        "aspect_ratio": packaging.aspect_ratio,
        "safe_area_top_percent": packaging.safe_area_top_percent,
        "safe_area_bottom_percent": packaging.safe_area_bottom_percent,
        "safe_area_horizontal_percent": packaging.safe_area_horizontal_percent,
        "max_title_characters": packaging.max_title_characters,
        "caption_style": packaging.caption_style,
        "export_preset": packaging.export_preset,
    }


def _variant(row: ClipVariant) -> VariantSummary:
    """Detach only the Variant evidence a member is allowed to read."""
    return VariantSummary(
        variant_id=row.id,
        candidate_id=row.candidate_id,
        hook_strategy=row.hook_strategy.value,
        platform=row.platform.value,
        target_duration_ms=row.target_duration_ms,
        start_word_id=row.start_word_id,
        end_word_id=row.end_word_id,
        start_ms=row.start_ms,
        end_ms=row.end_ms,
        title=row.title,
        rationale=row.rationale,
        warnings=tuple(dict(warning) for warning in row.warnings),
        packaging=dict(row.packaging),
        created_at=row.created_at,
    )


def _evidence(row: ClaimEvidence) -> EvidenceSummary:
    """Detach one citation exactly as it was written."""
    return EvidenceSummary(
        evidence_id=row.id,
        candidate_id=row.candidate_id,
        start_word_id=row.start_word_id,
        end_word_id=row.end_word_id,
        claim_text=row.claim_text,
        source_url=row.source_url,
        source_title=row.source_title,
        publisher=row.publisher,
        retrieved_at=row.retrieved_at,
        verification_status=row.verification_status,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
    )


def _word(entry: Any) -> TranscriptWord:
    """Rebuild one canonical word from the transcript's stored representation."""
    return TranscriptWord(
        word_id=str(entry["word_id"]),
        text=str(entry["text"]),
        punctuation=str(entry.get("punctuation", "")),
        start_ms=int(entry["start_ms"]),
        end_ms=int(entry["end_ms"]),
        confidence=float(entry.get("confidence", 0.0)),
        speaker=str(entry.get("speaker", "")),
    )
