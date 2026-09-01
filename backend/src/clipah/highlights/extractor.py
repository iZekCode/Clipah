"""Validation of provider-proposed clip candidates against the authoritative transcript.

A provider may only point at words. Everything a clip is later rendered from — its start,
its end, and the text it claims to contain — is resolved here from the transcript, so a
model that invents a word ID, a range, or an excerpt cannot reach durable state.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from clipah.highlights.models import (
    DEFAULT_CANDIDATE_POLICY,
    CandidatePolicy,
    ClipCandidateDraft,
    ClipCandidateProposal,
)
from clipah.highlights.windowing import window_text
from clipah.transcripts.models import TranscriptResult, TranscriptWord

_NON_COMPARABLE = re.compile(r"[^\w\s]", flags=re.UNICODE)


class CandidateValidationError(Exception):
    """Reject one candidate through a stable code that carries no provider text."""

    def __init__(self, code: str) -> None:
        """Retain only the public-safe rejection code."""
        self.code = code
        super().__init__(code)


def validate_candidate(
    payload: Mapping[str, Any],
    *,
    transcript: TranscriptResult,
    policy: CandidatePolicy = DEFAULT_CANDIDATE_POLICY,
) -> ClipCandidateDraft:
    """Accept one candidate only when every claim it makes matches the transcript."""
    proposal = _parse(payload)
    index = {word.word_id: position for position, word in enumerate(transcript.words)}
    start_position = index.get(proposal.start_word_id)
    end_position = index.get(proposal.end_word_id)
    if start_position is None or end_position is None:
        raise CandidateValidationError("CANDIDATE_UNKNOWN_WORD_ID")
    if end_position < start_position:
        raise CandidateValidationError("CANDIDATE_RANGE_REVERSED")

    included = transcript.words[start_position : end_position + 1]
    duration_ms = included[-1].end_ms - included[0].start_ms
    if not policy.min_duration_ms <= duration_ms <= policy.max_duration_ms:
        raise CandidateValidationError("CANDIDATE_DURATION_OUT_OF_RANGE")

    excerpt = window_text(included)
    if _comparable(proposal.transcript_excerpt) != _comparable(excerpt):
        raise CandidateValidationError("CANDIDATE_EXCERPT_MISMATCH")

    return _draft(proposal, included=included, duration_ms=duration_ms, excerpt=excerpt)


def _parse(payload: Mapping[str, Any]) -> ClipCandidateProposal:
    """Enforce the strict proposal schema without leaking provider values into the failure."""
    try:
        return ClipCandidateProposal.model_validate(dict(payload))
    except ValidationError as error:
        raise CandidateValidationError("CANDIDATE_SCHEMA_INVALID") from error


def _draft(
    proposal: ClipCandidateProposal,
    *,
    included: tuple[TranscriptWord, ...],
    duration_ms: int,
    excerpt: str,
) -> ClipCandidateDraft:
    """Bind the accepted proposal to the timestamps and text the transcript authorizes."""
    return ClipCandidateDraft(
        hook=proposal.hook,
        payoff=proposal.payoff,
        reason=proposal.reason,
        category=proposal.category,
        tags=proposal.tags,
        start_word_id=included[0].word_id,
        end_word_id=included[-1].word_id,
        start_ms=included[0].start_ms,
        end_ms=included[-1].end_ms,
        duration_ms=duration_ms,
        transcript_excerpt=excerpt,
        context_dependencies=proposal.context_dependencies,
        context_warnings=proposal.context_warnings,
        visual_opportunities=proposal.visual_opportunities,
        score_breakdown=proposal.score_breakdown,
    )


def _comparable(text: str) -> list[str]:
    """Reduce text to the tokens that must match, ignoring case, spacing, and punctuation."""
    return _NON_COMPARABLE.sub(" ", text).casefold().split()
