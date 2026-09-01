"""Turn one transcript into a ranked, deduplicated list of clip candidates.

The analyzer owns the whole read-only half of the analysis: window the transcript, ask a
provider for proposals per window, validate every proposal against the authoritative words,
deduplicate, rerank globally, and rank. A single failed window is recorded and survived; the
analysis only fails when too few candidates remain to be worth review.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from clipah.highlights.deduplicate import (
    DEFAULT_DEDUPLICATION_POLICY,
    DeduplicationPolicy,
    deduplicate,
)
from clipah.highlights.extractor import CandidateValidationError, validate_candidate
from clipah.highlights.models import (
    DEFAULT_CANDIDATE_POLICY,
    DEFAULT_WINDOWING_POLICY,
    CandidatePolicy,
    ClipCandidateDraft,
    WindowingPolicy,
)
from clipah.highlights.provider import (
    HighlightProvider,
    HighlightProviderRetryableError,
    HighlightProviderTerminalError,
    ProviderCall,
)
from clipah.highlights.rerank import (
    DEFAULT_RANKING_POLICY,
    RankedCandidate,
    RankingPolicy,
    rank_candidates,
)
from clipah.highlights.windowing import build_windows
from clipah.transcripts.models import TranscriptResult

INSUFFICIENT_CANDIDATES_CODE = "ANALYSIS_INSUFFICIENT_CANDIDATES"


@dataclass(frozen=True, slots=True)
class AnalysisPolicy:
    """Every knob the analysis uses, so a caller can tune it without editing the code."""

    windowing: WindowingPolicy = DEFAULT_WINDOWING_POLICY
    candidate: CandidatePolicy = DEFAULT_CANDIDATE_POLICY
    deduplication: DeduplicationPolicy = DEFAULT_DEDUPLICATION_POLICY
    ranking: RankingPolicy = DEFAULT_RANKING_POLICY
    target_per_window: int = 8
    min_candidates: int = 3


DEFAULT_ANALYSIS_POLICY = AnalysisPolicy()


class AnalysisFailedError(Exception):
    """Report an analysis that cannot produce a review-worthy result."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        """Retain only the stable code and whether the Job may be retried."""
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """Everything the Job needs to persist after one successful analysis."""

    ranked: tuple[RankedCandidate, ...]
    calls: tuple[ProviderCall, ...]
    failed_windows: tuple[tuple[int, str], ...] = field(default=())


class HighlightAnalyzer:
    """Drive one transcript through extraction, deduplication, reranking, and ranking."""

    def __init__(
        self,
        *,
        provider: HighlightProvider,
        policy: AnalysisPolicy = DEFAULT_ANALYSIS_POLICY,
        on_window_failure: Callable[[int, str], None] | None = None,
    ) -> None:
        """Bind the provider, the tuning policy, and the per-window failure observer."""
        self._provider = provider
        self._policy = policy
        self._on_window_failure = on_window_failure

    def analyze(self, *, transcript: TranscriptResult) -> AnalysisResult:
        """Produce the ranked candidates for one transcript, or explain why it cannot."""
        calls: list[ProviderCall] = []
        failures: list[tuple[int, str]] = []
        drafts: list[ClipCandidateDraft] = []
        retryable = False
        for window in build_windows(transcript, policy=self._policy.windowing):
            try:
                extraction = self._provider.extract(
                    window=window, target_count=self._policy.target_per_window
                )
            except (HighlightProviderRetryableError, HighlightProviderTerminalError) as error:
                retryable = retryable or isinstance(error, HighlightProviderRetryableError)
                self._record_failure(failures, window.index, error.code)
                continue
            calls.append(extraction.call)
            drafts.extend(self._validated(extraction.proposals, transcript=transcript))
        survivors = deduplicate(drafts, policy=self._policy.deduplication)
        if len(survivors) < self._policy.min_candidates:
            raise AnalysisFailedError(INSUFFICIENT_CANDIDATES_CODE, retryable=retryable)
        order = self._order(survivors, calls)
        ranked = rank_candidates(survivors, order=order, policy=self._policy.ranking)
        return AnalysisResult(
            ranked=tuple(ranked),
            calls=tuple(calls),
            failed_windows=tuple(failures),
        )

    def _validated(
        self, proposals: Sequence[Mapping[str, Any]], *, transcript: TranscriptResult
    ) -> list[ClipCandidateDraft]:
        """Keep only the proposals the authoritative transcript can support."""
        drafts: list[ClipCandidateDraft] = []
        for proposal in proposals:
            try:
                drafts.append(
                    validate_candidate(
                        proposal, transcript=transcript, policy=self._policy.candidate
                    )
                )
            except CandidateValidationError:
                continue
        return drafts

    def _order(
        self, survivors: Sequence[ClipCandidateDraft], calls: list[ProviderCall]
    ) -> tuple[int, ...] | None:
        """Ask the reranker for a global order, falling back to the local weights."""
        try:
            result = self._provider.rerank(candidates=survivors, limit=self._policy.ranking.expose)
        except (HighlightProviderRetryableError, HighlightProviderTerminalError):
            return None
        calls.append(result.call)
        return result.order

    def _record_failure(self, failures: list[tuple[int, str]], index: int, code: str) -> None:
        """Remember one failed window and tell the observer while others keep running."""
        failures.append((index, code))
        if self._on_window_failure is not None:
            self._on_window_failure(index, code)
