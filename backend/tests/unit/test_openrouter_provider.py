"""Contracts for the OpenRouter highlight adapter's two-call span-selection flow.

The adapter must never let a model supply transcript text, a timestamp, or a boundary the
offer did not contain, and must classify provider failures into the same stable codes the
analyzer already understands.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from clipah.highlights.openrouter_adapter import (
    EXTRACTION_PROMPT_VERSION,
    PROVIDER,
    OpenRouterHighlightProvider,
)
from clipah.highlights.provider import (
    EXTRACT_OPERATION,
    RERANK_OPERATION,
    HighlightProviderRetryableError,
    HighlightProviderTerminalError,
)
from clipah.highlights.sentences import sentence_spans
from clipah.highlights.windowing import single_window
from clipah.transcripts.models import TranscriptResult, TranscriptWord

SENTENCE = ("Growth", "stalled", "until", "we", "cut", "the", "onboarding", "form")


def _transcript(count: int = 400) -> TranscriptResult:
    """Produce half-second words whose every eighth word closes a four-second sentence."""
    words = tuple(
        TranscriptWord(
            word_id=f"w{index + 1:06d}",
            text=SENTENCE[index % len(SENTENCE)],
            punctuation="." if index % len(SENTENCE) == len(SENTENCE) - 1 else "",
            start_ms=index * 500,
            end_ms=index * 500 + 500,
            confidence=0.9,
            speaker="A",
        )
        for index in range(count)
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
WINDOW = single_window(TRANSCRIPT)


def _metadata(position: int) -> dict[str, Any]:
    """Describe one clip exactly as the metadata call is asked to."""
    return {
        "position": position,
        "hook": "The onboarding form was the growth ceiling",
        "payoff": "Removing it doubled activation",
        "reason": "A complete problem, decision, and result",
        "category": "insight",
        "tags": ["growth"],
        "context_dependencies": [],
        "context_warnings": [],
        "visual_opportunities": ["Show the shortened form"],
        "score_breakdown": {
            "hook": 0.8,
            "payoff": 0.7,
            "narrative_completeness": 0.9,
            "context_safety": 0.85,
            "platform_fit": 0.75,
            "transcript_confidence": 0.9,
            "visual_opportunity": 0.6,
        },
    }


class _Recorder:
    """Return scripted replies while remembering every request the adapter sent."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        """Record one request and answer with the next scripted reply or failure."""
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _reply(payload: dict[str, Any], *, request_id: str = "gen-1") -> dict[str, Any]:
    """Shape one OpenRouter tool-call reply carrying the adapter's JSON payload."""
    return {
        "id": request_id,
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [{"function": {"arguments": json.dumps(payload)}}],
                }
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }


class _FailureError(Exception):
    """One provider failure carrying only the status the adapter classifies on."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def _provider(client: _Recorder, **overrides: Any) -> OpenRouterHighlightProvider:
    """Build the adapter with injected transport, clock, and sleep."""
    return OpenRouterHighlightProvider(
        extraction_model="nvidia/nemotron-3-super-120b-a12b",
        reranking_model="nvidia/nemotron-3-super-120b-a12b",
        completions=client,
        clock=iter([0.0, 0.25, 0.25, 0.5, 0.5, 0.75, 0.75, 1.0]).__next__,
        sleep=lambda _: None,
        **overrides,
    )


@pytest.mark.unit
def test_extraction_offers_only_spans_the_transcript_authorizes() -> None:
    """The selection request must list exactly the duration-valid pairs and nothing else."""
    client = _Recorder(
        _reply({"candidates": [{"span": "S0-S7", "reason": "one complete moment"}]}),
        _reply({"candidates": [_metadata(0)]}),
    )
    _provider(client).extract(window=WINDOW, target_count=1)
    selection = client.requests[0]
    offered = json.loads(selection["messages"][1]["content"])
    expected = {f"{span.label}-{end}" for span in sentence_spans(WINDOW) for end in span.valid_ends}
    assert set(offered["allowed_spans"]) == expected
    assert "S0-S3" not in offered["allowed_spans"]
    assert offered["target_count"] == 1
    properties = selection["tools"][0]["function"]["parameters"]["properties"]["candidates"][
        "items"
    ]["properties"]
    assert sorted(properties) == ["reason", "span"]


@pytest.mark.unit
def test_metadata_is_written_after_the_boundaries_are_resolved_locally() -> None:
    """The second call sees stored text for accepted spans, and cannot move a boundary."""
    client = _Recorder(
        _reply({"candidates": [{"span": "S0-S7", "reason": "one complete moment"}]}),
        _reply({"candidates": [_metadata(0) | {"span": "S20-S27"}]}),
    )
    result = _provider(client).extract(window=WINDOW, target_count=1)
    described = json.loads(client.requests[1]["messages"][1]["content"])
    assert described["clips"][0]["position"] == 0
    assert described["clips"][0]["transcript"].startswith("Growth stalled until we cut")
    proposal = result.proposals[0]
    assert (proposal["start_word_id"], proposal["end_word_id"]) == ("w000001", "w000064")
    assert proposal["transcript_excerpt"].startswith("Growth stalled until we cut")
    assert "span" not in proposal


@pytest.mark.unit
def test_a_span_outside_the_offer_never_reaches_the_metadata_call() -> None:
    """An unoffered or out-of-range label is dropped before it can cost a second request."""
    client = _Recorder(
        _reply(
            {
                "candidates": [
                    {"span": "S0-S2", "reason": "too short"},
                    {"span": "S0-S99", "reason": "invented"},
                    {"span": "S8-S15", "reason": "valid"},
                ]
            }
        ),
        _reply({"candidates": [_metadata(0)]}),
    )
    result = _provider(client).extract(window=WINDOW, target_count=3)
    described = json.loads(client.requests[1]["messages"][1]["content"])
    assert len(described["clips"]) == 1
    assert len(result.proposals) == 1
    assert result.proposals[0]["start_word_id"] == "w000065"


@pytest.mark.unit
def test_metadata_missing_for_a_span_drops_that_candidate() -> None:
    """A span the metadata call ignored must not reach review without its description."""
    client = _Recorder(
        _reply(
            {
                "candidates": [
                    {"span": "S0-S7", "reason": "first"},
                    {"span": "S8-S15", "reason": "second"},
                ]
            }
        ),
        _reply({"candidates": [_metadata(1)]}),
    )
    result = _provider(client).extract(window=WINDOW, target_count=2)
    assert len(result.proposals) == 1
    assert result.proposals[0]["start_word_id"] == "w000065"


@pytest.mark.unit
def test_an_extraction_without_a_usable_span_fails_the_window() -> None:
    """A window that yields nothing must say so rather than return empty proposals."""
    client = _Recorder(_reply({"candidates": [{"span": "S0-S2", "reason": "too short"}]}))
    with pytest.raises(HighlightProviderTerminalError) as caught:
        _provider(client).extract(window=WINDOW, target_count=1)
    assert caught.value.code == "HIGHLIGHT_PROVIDER_INVALID"


@pytest.mark.unit
def test_extraction_records_both_requests_as_one_usage_record() -> None:
    """Usage recording must account for the metadata call, not only the selection call."""
    client = _Recorder(
        _reply({"candidates": [{"span": "S0-S7", "reason": "one"}]}, request_id="gen-7"),
        _reply({"candidates": [_metadata(0)]}),
    )
    call = _provider(client).extract(window=WINDOW, target_count=1).call
    assert call.provider == PROVIDER
    assert (call.operation, call.request_id) == (EXTRACT_OPERATION, "gen-7")
    assert call.prompt_version == EXTRACTION_PROMPT_VERSION
    assert (call.input_units, call.output_units) == (22, 14)
    assert call.latency_ms > 0


@pytest.mark.unit
def test_reranking_returns_positions_only() -> None:
    """The quality model may reorder candidates but never rewrite one."""
    client = _Recorder(_reply({"order": [2, 0, 1]}))
    result = _provider(client).rerank(candidates=(), limit=3)
    assert result.order == (2, 0, 1)
    assert result.call.operation == RERANK_OPERATION


@pytest.mark.unit
@pytest.mark.parametrize(
    "status,code",
    [
        (429, "HIGHLIGHT_PROVIDER_RATE_LIMITED"),
        (503, "HIGHLIGHT_PROVIDER_UNAVAILABLE"),
    ],
)
def test_transient_failures_are_retried_then_reported_as_retryable(status, code) -> None:
    """A temporary refusal must be retried and, once exhausted, stay retryable."""
    client = _Recorder(*[_FailureError(status)] * 3)
    with pytest.raises(HighlightProviderRetryableError) as caught:
        _provider(client).extract(window=WINDOW, target_count=1)
    assert caught.value.code == code
    assert len(client.requests) == 3


@pytest.mark.unit
def test_a_rejected_request_is_terminal() -> None:
    """A refusal the same request would earn again must not burn the retry budget."""
    client = _Recorder(_FailureError(400))
    with pytest.raises(HighlightProviderTerminalError) as caught:
        _provider(client).extract(window=WINDOW, target_count=1)
    assert caught.value.code == "HIGHLIGHT_PROVIDER_REJECTED"
    assert len(client.requests) == 1


@pytest.mark.unit
def test_a_reply_without_a_tool_call_is_invalid() -> None:
    """A model that answers in prose has not answered the contract at all."""
    client = _Recorder({"id": "gen-1", "choices": [{"message": {"content": "sure!"}}], "usage": {}})
    with pytest.raises(HighlightProviderTerminalError) as caught:
        _provider(client).extract(window=WINDOW, target_count=1)
    assert caught.value.code == "HIGHLIGHT_PROVIDER_INVALID"
