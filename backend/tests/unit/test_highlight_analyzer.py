"""Contracts for the analyzer that turns one transcript into ranked candidates."""

from __future__ import annotations

from typing import Any

import pytest

from clipah.highlights.analyzer import (
    AnalysisFailedError,
    AnalysisPolicy,
    HighlightAnalyzer,
)
from clipah.highlights.provider import (
    CANDIDATE_SCHEMA_VERSION,
    ExtractionResult,
    FakeHighlightProvider,
    HighlightProviderRetryableError,
    HighlightProviderTerminalError,
    ProviderCall,
    RerankResult,
)
from clipah.transcripts.models import TranscriptResult, TranscriptWord

WORDS_PER_SECOND = 2


def _transcript(seconds: int = 400) -> TranscriptResult:
    """Build a transcript long enough to produce several windows."""
    words = tuple(
        TranscriptWord(
            word_id=f"w{index + 1:06d}",
            text=f"word{index}",
            punctuation="." if index % 40 == 39 else "",
            start_ms=index * 500,
            end_ms=index * 500 + 500,
            confidence=0.9,
            speaker="A",
        )
        for index in range(seconds * WORDS_PER_SECOND)
    )
    return TranscriptResult(
        provider="assemblyai",
        provider_version="1.0.0",
        model="universal-3-pro",
        language="en",
        full_text=" ".join(word.text for word in words),
        words=words,
        speaker_segments=(),
        utterances=(),
        duration_ms=words[-1].end_ms,
        raw_result={},
    )


TRANSCRIPT = _transcript()


def _call(provider: str = "groq") -> ProviderCall:
    """Describe one provider call the analyzer should carry into usage recording."""
    return ProviderCall(
        provider=provider,
        operation="highlight_extract",
        model="openai/gpt-oss-20b",
        request_id="req_1",
        latency_ms=250,
        input_units=1_000,
        output_units=200,
        prompt_version="highlights/extract/1",
        schema_version=CANDIDATE_SCHEMA_VERSION,
    )


def _proposal(start_index: int, *, duration_words: int = 60, hook: str = "hook") -> dict[str, Any]:
    """Propose one valid candidate over the authoritative words at ``start_index``."""
    words = TRANSCRIPT.words[start_index : start_index + duration_words]
    excerpt = " ".join(f"{word.text}{word.punctuation}" for word in words)
    return {
        "hook": hook,
        "payoff": "payoff",
        "reason": "reason",
        "category": "insight",
        "tags": [],
        "start_word_id": words[0].word_id,
        "end_word_id": words[-1].word_id,
        "transcript_excerpt": excerpt,
        "context_dependencies": [],
        "context_warnings": [],
        "visual_opportunities": [],
        "score_breakdown": {
            "hook": 0.5,
            "payoff": 0.5,
            "narrative_completeness": 0.5,
            "context_safety": 0.5,
            "platform_fit": 0.5,
            "transcript_confidence": 0.5,
            "visual_opportunity": 0.5,
        },
    }


def _extraction(*proposals: dict[str, Any]) -> ExtractionResult:
    """Wrap proposals as one provider extraction result."""
    return ExtractionResult(proposals=tuple(proposals), call=_call())


def _analyzer(
    provider: FakeHighlightProvider,
    *,
    failures: list[tuple[int, str]] | None = None,
    policy: AnalysisPolicy | None = None,
) -> HighlightAnalyzer:
    """Build one analyzer recording each window failure it reports."""
    return HighlightAnalyzer(
        provider=provider,
        policy=policy or AnalysisPolicy(),
        on_window_failure=(lambda index, code: failures.append((index, code)))
        if failures is not None
        else None,
    )


@pytest.mark.unit
def test_ranks_candidates_extracted_from_every_window() -> None:
    """The analysis must offer one ranked list built from the whole transcript."""
    provider = FakeHighlightProvider(
        results=[
            _extraction(_proposal(0, hook="a")),
            _extraction(_proposal(400, hook="b")),
            _extraction(_proposal(700, hook="c")),
        ],
        rerank_result=RerankResult(order=(), call=_call()),
    )

    result = _analyzer(provider).analyze(transcript=TRANSCRIPT)

    assert len(provider.windows) == 3
    assert [item.rank for item in result.ranked] == [1, 2, 3]
    assert {item.draft.hook for item in result.ranked} == {"a", "b", "c"}
    assert len(result.calls) == 4


@pytest.mark.unit
def test_drops_a_proposal_that_fails_transcript_validation() -> None:
    """One hallucinated word ID must not cost the window its valid candidates."""
    invalid = _proposal(0, hook="invalid") | {"start_word_id": "w999999"}
    provider = FakeHighlightProvider(
        results=[
            _extraction(invalid, _proposal(0, hook="valid")),
            _extraction(_proposal(400, hook="b")),
            _extraction(_proposal(700, hook="c")),
        ],
        rerank_result=RerankResult(order=(), call=_call()),
    )

    result = _analyzer(provider).analyze(transcript=TRANSCRIPT)

    assert "invalid" not in {item.draft.hook for item in result.ranked}
    assert "valid" in {item.draft.hook for item in result.ranked}


@pytest.mark.unit
def test_reports_a_failed_window_and_completes_the_remaining_ones() -> None:
    """A single bad window is not an analysis failure while other moments survive."""
    failures: list[tuple[int, str]] = []
    provider = FakeHighlightProvider(
        results=[
            HighlightProviderTerminalError("HIGHLIGHT_WINDOW_TOO_LARGE"),
            _extraction(_proposal(400, hook="b"), _proposal(500, hook="c")),
            _extraction(_proposal(700, hook="d")),
        ],
        rerank_result=RerankResult(order=(), call=_call()),
    )

    result = _analyzer(provider, failures=failures).analyze(transcript=TRANSCRIPT)

    assert failures == [(0, "HIGHLIGHT_WINDOW_TOO_LARGE")]
    assert len(result.ranked) == 3
    assert result.failed_windows == ((0, "HIGHLIGHT_WINDOW_TOO_LARGE"),)


@pytest.mark.unit
def test_fails_the_analysis_when_too_few_candidates_survive() -> None:
    """Two moments are not a review-worthy result, so the Job must not report success."""
    provider = FakeHighlightProvider(
        results=[
            _extraction(_proposal(0, hook="a")),
            _extraction(_proposal(400, hook="b")),
            _extraction(),
        ],
        rerank_result=RerankResult(order=(), call=_call()),
    )

    with pytest.raises(AnalysisFailedError) as raised:
        _analyzer(provider).analyze(transcript=TRANSCRIPT)

    assert raised.value.code == "ANALYSIS_INSUFFICIENT_CANDIDATES"
    assert raised.value.retryable is False


@pytest.mark.unit
def test_reports_a_provider_outage_as_retryable_when_nothing_survives() -> None:
    """An outage must leave the Job recoverable instead of failing it permanently."""
    provider = FakeHighlightProvider(
        results=[HighlightProviderRetryableError("HIGHLIGHT_PROVIDER_UNAVAILABLE")] * 3,
        rerank_result=RerankResult(order=(), call=_call()),
    )

    with pytest.raises(AnalysisFailedError) as raised:
        _analyzer(provider).analyze(transcript=TRANSCRIPT)

    assert raised.value.retryable is True


@pytest.mark.unit
def test_deduplicates_the_same_moment_found_in_two_windows() -> None:
    """Overlap exists to avoid losing a moment, not to show it twice."""
    provider = FakeHighlightProvider(
        results=[
            _extraction(_proposal(220, hook="first"), _proposal(0, hook="opening")),
            _extraction(_proposal(222, hook="second")),
            _extraction(_proposal(700, hook="third")),
        ],
        rerank_result=RerankResult(order=(), call=_call()),
    )

    result = _analyzer(provider).analyze(transcript=TRANSCRIPT)

    assert {item.draft.hook for item in result.ranked} == {"opening", "first", "third"}


@pytest.mark.unit
def test_applies_a_valid_provider_order() -> None:
    """The quality model decides the final order when its answer is usable."""
    provider = FakeHighlightProvider(
        results=[
            _extraction(_proposal(0, hook="a")),
            _extraction(_proposal(400, hook="b")),
            _extraction(_proposal(700, hook="c")),
        ],
        rerank_result=RerankResult(order=(2, 1, 0), call=_call()),
    )

    result = _analyzer(provider).analyze(transcript=TRANSCRIPT)

    assert [item.draft.hook for item in result.ranked] == ["c", "b", "a"]


@pytest.mark.unit
def test_falls_back_to_local_ranking_when_reranking_fails() -> None:
    """A reranking outage must not throw away candidates that already validated."""
    provider = FakeHighlightProvider(
        results=[
            _extraction(_proposal(0, hook="a")),
            _extraction(_proposal(400, hook="b")),
            _extraction(_proposal(700, hook="c")),
        ],
        rerank_result=HighlightProviderRetryableError("HIGHLIGHT_PROVIDER_UNAVAILABLE"),
    )

    result = _analyzer(provider).analyze(transcript=TRANSCRIPT)

    assert len(result.ranked) == 3


@pytest.mark.unit
def test_fails_an_empty_transcript_without_calling_the_provider() -> None:
    """A transcript with no windows can never produce a candidate."""
    provider = FakeHighlightProvider(results=[], rerank_result=RerankResult(order=(), call=_call()))
    empty = _transcript(seconds=1)

    with pytest.raises(AnalysisFailedError):
        _analyzer(provider).analyze(transcript=empty)

    assert provider.windows == []
