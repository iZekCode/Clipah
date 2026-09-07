"""Workspace-scoped use cases for Clip Variants and Claim Evidence.

Two rules shape everything here. A candidate this member has no standing on is absent, not
forbidden — the same 404 a missing one answers with. And nothing in this module decides
that a claim is true: a verification status is a person's assertion, recorded with the
person who made it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from clipah.models import ClaimVerificationStatus
from clipah.transcripts.models import TranscriptWord
from clipah.variants.assessor import (
    ContextProviderRetryableError,
    ContextProviderTerminalError,
    ContextSafetyAssessor,
)
from clipah.variants.claim_evidence import ClaimEvidenceInvalidError, normalize_evidence
from clipah.variants.generator import generate_variants
from clipah.variants.models import Platform
from clipah.variants.repository import (
    CandidateTarget,
    EvidenceSummary,
    VariantRepository,
    VariantSummary,
    summarize_evidence,
)
from clipah.workspaces.models import WorkspaceAccess


class VariantTargetNotFoundError(Exception):
    """The Project or Clip Candidate is not visible inside this Workspace."""


class VariantRequestInvalidError(Exception):
    """The requested durations or platforms are not ones Clipah can honour."""


def generate_clip_variants(
    session: Session,
    *,
    access: WorkspaceAccess,
    project_id: UUID,
    candidate_id: UUID,
    platforms: Sequence[Platform],
    durations_ms: Sequence[int],
    assessor: ContextSafetyAssessor | None = None,
) -> tuple[VariantSummary, ...]:
    """Offer every honest cut of one candidate, and remember the ones offered.

    The assessment is best-effort by design. A provider that is slow, throttled, or absent
    must not stop a member from seeing the variants the transcript alone can justify, so a
    provider failure degrades to the deterministic rules rather than failing the request.
    """
    repository = VariantRepository(session)
    target = repository.candidate_target(
        workspace_id=access.workspace_id, project_id=project_id, candidate_id=candidate_id
    )
    if target is None:
        raise VariantTargetNotFoundError(str(candidate_id))
    if not platforms or not durations_ms:
        raise VariantRequestInvalidError("a variant request names at least one of each")

    proposals = _proposals(
        assessor,
        words=target.words,
        start_word_id=target.start_word_id,
        end_word_id=target.end_word_id,
    )
    try:
        drafts = generate_variants(
            words=target.words,
            start_word_id=target.start_word_id,
            end_word_id=target.end_word_id,
            hook=target.hook,
            platforms=tuple(platforms),
            durations_ms=tuple(durations_ms),
            proposals=proposals,
        )
    except ValueError as error:
        raise VariantRequestInvalidError(str(error)) from None

    repository.record_variants(
        workspace_id=access.workspace_id,
        project_id=target.project_id,
        candidate_id=target.candidate_id,
        drafts=drafts,
    )
    return repository.variants_for_candidate(
        workspace_id=access.workspace_id, candidate_id=target.candidate_id
    )


def list_clip_variants(
    session: Session,
    *,
    access: WorkspaceAccess,
    project_id: UUID,
    candidate_id: UUID,
) -> tuple[VariantSummary, ...]:
    """Read one candidate's Variants, refusing a candidate nobody here may review."""
    repository = VariantRepository(session)
    target = repository.candidate_target(
        workspace_id=access.workspace_id, project_id=project_id, candidate_id=candidate_id
    )
    if target is None:
        raise VariantTargetNotFoundError(str(candidate_id))
    return repository.variants_for_candidate(
        workspace_id=access.workspace_id, candidate_id=target.candidate_id
    )


def attach_claim_evidence(
    session: Session,
    *,
    access: WorkspaceAccess,
    project_id: UUID,
    candidate_id: UUID,
    start_word_id: str,
    end_word_id: str,
    claim_text: str,
    source_url: str,
    source_title: str,
    publisher: str,
    retrieved_at: datetime,
) -> EvidenceSummary:
    """Record where a member says one claim came from, bound to exact words."""
    repository = VariantRepository(session)
    target = repository.candidate_target(
        workspace_id=access.workspace_id, project_id=project_id, candidate_id=candidate_id
    )
    if target is None:
        raise VariantTargetNotFoundError(str(candidate_id))
    _require_range_inside(target, start_word_id=start_word_id, end_word_id=end_word_id)

    normalized = normalize_evidence(
        source_url=source_url,
        source_title=source_title,
        publisher=publisher,
        claim_text=claim_text,
    )
    return repository.record_evidence(
        workspace_id=access.workspace_id,
        project_id=target.project_id,
        candidate_id=target.candidate_id,
        start_word_id=start_word_id,
        end_word_id=end_word_id,
        claim_text=normalized.claim_text,
        source_url=normalized.source_url,
        source_title=normalized.source_title,
        publisher=normalized.publisher,
        retrieved_at=retrieved_at,
        created_by_user_id=access.user_id,
    )


def list_claim_evidence(
    session: Session,
    *,
    access: WorkspaceAccess,
    project_id: UUID,
    candidate_id: UUID,
) -> tuple[EvidenceSummary, ...]:
    """Read every citation attached to one candidate a member may review."""
    repository = VariantRepository(session)
    target = repository.candidate_target(
        workspace_id=access.workspace_id, project_id=project_id, candidate_id=candidate_id
    )
    if target is None:
        raise VariantTargetNotFoundError(str(candidate_id))
    return repository.evidence_for_candidate(
        workspace_id=access.workspace_id, candidate_id=target.candidate_id
    )


def set_verification_status(
    session: Session,
    *,
    access: WorkspaceAccess,
    evidence_id: UUID,
    status: ClaimVerificationStatus,
) -> EvidenceSummary:
    """Record that a User — never Clipah — decided what this evidence establishes."""
    repository = VariantRepository(session)
    row = repository.lock_evidence(workspace_id=access.workspace_id, evidence_id=evidence_id)
    if row is None:
        raise VariantTargetNotFoundError(str(evidence_id))
    row.verification_status = status
    session.flush()
    return summarize_evidence(row)


def _proposals(
    assessor: ContextSafetyAssessor | None,
    *,
    words: Sequence[TranscriptWord],
    start_word_id: str,
    end_word_id: str,
) -> tuple[dict[str, object], ...]:
    """Ask the configured assessor for warnings, tolerating a provider that cannot answer."""
    if assessor is None:
        return ()
    try:
        result = assessor.assess(
            words=words,
            start_word_id=start_word_id,
            end_word_id=end_word_id,
        )
    except (ContextProviderRetryableError, ContextProviderTerminalError):
        return ()
    return tuple(dict(proposal) for proposal in result.proposals)


def _require_range_inside(target: CandidateTarget, *, start_word_id: str, end_word_id: str) -> None:
    """Refuse a citation whose words fall outside the candidate it claims to annotate.

    Evidence is shown beside a clip, so a range reaching past that clip would attach a
    citation to words nobody watching it will ever hear.
    """
    index = {word.word_id: position for position, word in enumerate(target.words)}
    bounds = (index.get(target.start_word_id), index.get(target.end_word_id))
    start = index.get(start_word_id)
    end = index.get(end_word_id)
    if start is None or end is None or end < start:
        raise ClaimEvidenceInvalidError("EVIDENCE_WORD_RANGE_INVALID")
    if bounds[0] is None or bounds[1] is None or start < bounds[0] or end > bounds[1]:
        raise ClaimEvidenceInvalidError("EVIDENCE_WORD_RANGE_OUTSIDE_CANDIDATE")
