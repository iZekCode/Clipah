"""Provider-neutral highlight extraction and reranking ports, errors, and offline providers.

An extraction provider may only propose candidates keyed to the word IDs it was shown. It
never resolves a timestamp and never decides whether a proposal is valid; the extractor and
the analyzer do that against the transcript.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from clipah.highlights.models import ClipCandidateDraft, TranscriptWindow
from clipah.transcripts.models import TranscriptResult, TranscriptWord

DETERMINISTIC_PROVIDER = "deterministic"
DETERMINISTIC_PROMPT_VERSION = "deterministic/1"
CANDIDATE_SCHEMA_VERSION = "clip-candidate/1"
EXTRACT_OPERATION = "highlight_extract"
RERANK_OPERATION = "highlight_rerank"

_FALLBACK_SCORE = 0.5
_FALLBACK_TARGET_MS = 45_000


class HighlightProviderRetryableError(Exception):
    """Represent a temporary highlight-provider failure through a stable code."""

    def __init__(self, code: str) -> None:
        """Retain only public-safe retry information."""
        self.code = code
        super().__init__(code)


class HighlightProviderTerminalError(Exception):
    """Represent a permanent highlight-provider failure through a stable code."""

    def __init__(self, code: str) -> None:
        """Retain only public-safe terminal information."""
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ProviderCall:
    """Everything usage recording must remember about one provider request."""

    provider: str
    operation: str
    model: str
    request_id: str
    latency_ms: int
    input_units: int
    output_units: int
    prompt_version: str
    schema_version: str


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Unvalidated proposals from one window, with the call that produced them."""

    proposals: tuple[Mapping[str, Any], ...]
    call: ProviderCall


@dataclass(frozen=True, slots=True)
class RerankResult:
    """One proposed global order over the candidates a reranker was given."""

    order: tuple[int, ...]
    call: ProviderCall


class HighlightExtractor(Protocol):
    """Provider-independent capability to propose candidates for one window."""

    def extract(self, *, window: TranscriptWindow, target_count: int) -> ExtractionResult:
        """Propose at most ``target_count`` candidates keyed to the window's word IDs."""


class HighlightReranker(Protocol):
    """Provider-independent capability to order deduplicated candidates."""

    def rerank(self, *, candidates: Sequence[ClipCandidateDraft], limit: int) -> RerankResult:
        """Return positions of the given candidates, best first."""


class HighlightProvider(HighlightExtractor, HighlightReranker, Protocol):
    """A provider offering both halves of the analysis."""


class DeterministicHighlightProvider:
    """Offline provider that proposes sentence-aligned candidates from the words alone."""

    def __init__(self, *, transcript: TranscriptResult) -> None:
        """Bind the authoritative transcript the proposals must be keyed to."""
        self._words = {word.word_id: word for word in transcript.words}

    def extract(self, *, window: TranscriptWindow, target_count: int) -> ExtractionResult:
        """Cut the window into roughly equal candidates ending at sentence boundaries."""
        words = [self._words[word_id] for word_id in window.word_ids if word_id in self._words]
        proposals = [
            _fallback_proposal(span) for span in _spans(words, target_count) if len(span) > 1
        ]
        return ExtractionResult(
            proposals=tuple(proposals),
            call=_deterministic_call(len(words), len(proposals), operation=EXTRACT_OPERATION),
        )

    def rerank(self, *, candidates: Sequence[ClipCandidateDraft], limit: int) -> RerankResult:
        """Decline to reorder, leaving the local weights in charge."""
        del limit
        return RerankResult(
            order=(), call=_deterministic_call(len(candidates), 0, operation=RERANK_OPERATION)
        )


class FakeHighlightProvider:
    """Deterministic provider used where real provider work would be inappropriate."""

    def __init__(
        self,
        *,
        results: Sequence[ExtractionResult | Exception] = (),
        rerank_result: RerankResult | Exception | None = None,
    ) -> None:
        """Queue one outcome per expected window and one reranking outcome."""
        self.results = list(results)
        self.rerank_result = rerank_result
        self.windows: list[TranscriptWindow] = []
        self.rerank_calls: list[tuple[ClipCandidateDraft, ...]] = []

    def extract(self, *, window: TranscriptWindow, target_count: int) -> ExtractionResult:
        """Record the exact window before replaying its queued outcome."""
        del target_count
        self.windows.append(window)
        if not self.results:
            raise RuntimeError("FakeHighlightProvider ran out of extraction outcomes")
        outcome = self.results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def rerank(self, *, candidates: Sequence[ClipCandidateDraft], limit: int) -> RerankResult:
        """Record the exact candidates before replaying the queued reranking outcome."""
        del limit
        self.rerank_calls.append(tuple(candidates))
        if self.rerank_result is None:
            raise RuntimeError("FakeHighlightProvider has no reranking outcome")
        if isinstance(self.rerank_result, Exception):
            raise self.rerank_result
        return self.rerank_result


def _deterministic_call(input_units: int, output_units: int, *, operation: str) -> ProviderCall:
    """Describe one offline call so usage recording stays uniform."""
    return ProviderCall(
        provider=DETERMINISTIC_PROVIDER,
        operation=operation,
        model=DETERMINISTIC_PROVIDER,
        request_id="",
        latency_ms=0,
        input_units=input_units,
        output_units=output_units,
        prompt_version=DETERMINISTIC_PROMPT_VERSION,
        schema_version=CANDIDATE_SCHEMA_VERSION,
    )


def _spans(words: Sequence[TranscriptWord], target_count: int) -> list[list[TranscriptWord]]:
    """Split words into candidate-sized runs that end at a sentence where one exists."""
    spans: list[list[TranscriptWord]] = []
    current: list[TranscriptWord] = []
    for word in words:
        current.append(word)
        elapsed = word.end_ms - current[0].start_ms
        if elapsed >= _FALLBACK_TARGET_MS and word.punctuation in {".", "!", "?"}:
            spans.append(current)
            current = []
        if len(spans) == target_count:
            return spans
    if current:
        spans.append(current)
    return spans


def _fallback_proposal(span: Sequence[TranscriptWord]) -> dict[str, Any]:
    """Describe one offline candidate in exactly the shape a provider would return."""
    excerpt = " ".join(f"{word.text}{word.punctuation}" for word in span)
    return {
        "hook": excerpt[:120],
        "payoff": excerpt[-120:],
        "reason": "Selected offline from sentence boundaries because no provider answered",
        "category": "insight",
        "tags": [],
        "start_word_id": span[0].word_id,
        "end_word_id": span[-1].word_id,
        "transcript_excerpt": excerpt,
        "context_dependencies": [],
        "context_warnings": ["selected without a language model"],
        "visual_opportunities": [],
        "score_breakdown": {
            "hook": _FALLBACK_SCORE,
            "payoff": _FALLBACK_SCORE,
            "narrative_completeness": _FALLBACK_SCORE,
            "context_safety": _FALLBACK_SCORE,
            "platform_fit": _FALLBACK_SCORE,
            "transcript_confidence": _FALLBACK_SCORE,
            "visual_opportunity": _FALLBACK_SCORE,
        },
    }
