"""Groq adapter for strict-schema candidate extraction and global reranking.

Everything provider-specific stays here: the SDK client, the JSON Schema, the retry budget,
and the mapping from provider failures onto Clipah's stable retryable and terminal codes.
Nothing that leaves this module carries provider text.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, cast

from groq import Groq

from clipah.highlights.models import ClipCandidateDraft, ClipCategory, TranscriptWindow
from clipah.highlights.provider import (
    CANDIDATE_SCHEMA_VERSION,
    EXTRACT_OPERATION,
    RERANK_OPERATION,
    ExtractionResult,
    HighlightProviderRetryableError,
    HighlightProviderTerminalError,
    ProviderCall,
    RerankResult,
)

PROVIDER = "groq"
EXTRACTION_PROMPT_VERSION = "highlights/extract/1"
RERANK_PROMPT_VERSION = "highlights/rerank/1"
REQUEST_TIMEOUT_SECONDS = 120.0
RETRY_BASE_DELAY_SECONDS = 0.5

RATE_LIMITED_CODE = "HIGHLIGHT_PROVIDER_RATE_LIMITED"
UNAVAILABLE_CODE = "HIGHLIGHT_PROVIDER_UNAVAILABLE"
REJECTED_CODE = "HIGHLIGHT_PROVIDER_REJECTED"
INVALID_CODE = "HIGHLIGHT_PROVIDER_INVALID"
WINDOW_TOO_LARGE_CODE = "HIGHLIGHT_WINDOW_TOO_LARGE"
SCHEMA_REFUSED_CODE = "HIGHLIGHT_PROVIDER_SCHEMA_REFUSED"

# The configured models reason before they answer. Left unbounded they spend the whole
# response on reasoning and emit no JSON, which Groq refuses as `json_validate_failed` with
# an empty `failed_generation`. Against a real transcript window the unbounded request
# produced valid output on none of three attempts; these two parameters produced valid
# output on all three. Extraction reads supplied text rather than deducing anything, so the
# lowest reasoning setting is both the cheapest and, measured, the most reliable.
REASONING_EFFORT = "low"
MAX_COMPLETION_TOKENS = 8000

_OVERFLOW_MARKERS = ("context_length_exceeded", "too large", "maximum context")
_SCHEMA_REFUSAL_MARKERS = ("json_validate_failed", "failed to validate json")

_EXTRACTION_SYSTEM_PROMPT = (
    "You find complete, self-contained short-form moments in a transcript window. "
    "Refer to moments only by the word IDs you were given. Never invent a timestamp, "
    "never quote words outside the window, and report every context dependency and "
    "warning you notice."
)
_RERANK_SYSTEM_PROMPT = (
    "You order clip candidates from best to worst using narrative completeness, context "
    "safety, hook strength, payoff, clarity without external context, emotional or "
    "informational value, transcript confidence, platform fit, and visual opportunity. "
    "Return every candidate position exactly once."
)


class _Completions(Protocol):
    """Structural subset of the Groq chat completions client used by this adapter."""

    def create(self, **request: Any) -> Any:
        """Submit one chat completion request."""


class GroqHighlightProvider:
    """Translate Groq chat completions into Clipah's extraction and reranking contracts."""

    def __init__(
        self,
        *,
        extraction_model: str,
        reranking_model: str,
        api_key: str | None = None,
        completions: _Completions | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 3,
    ) -> None:
        """Bind the configured models and an injectable clock, sleep, and client."""
        self._extraction_model = extraction_model
        self._reranking_model = reranking_model
        self._completions = completions or cast(
            _Completions, Groq(api_key=api_key).chat.completions
        )
        self._clock = clock
        self._sleep = sleep
        self._max_attempts = max_attempts

    def extract(self, *, window: TranscriptWindow, target_count: int) -> ExtractionResult:
        """Ask the extraction model for strict-schema candidates inside one window."""
        response, latency_ms = self._call(
            model=self._extraction_model,
            messages=[
                {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": _extraction_prompt(window, target_count)},
            ],
            schema_name="clip_candidates",
            schema=candidate_json_schema(),
        )
        body = _json_body(response)
        proposals = body.get("candidates")
        if not isinstance(proposals, list) or not all(isinstance(item, dict) for item in proposals):
            raise HighlightProviderTerminalError(INVALID_CODE)
        return ExtractionResult(
            proposals=tuple(cast(list[Mapping[str, Any]], proposals)),
            call=self._call_record(
                response,
                model=self._extraction_model,
                latency_ms=latency_ms,
                prompt_version=EXTRACTION_PROMPT_VERSION,
                operation=EXTRACT_OPERATION,
            ),
        )

    def rerank(self, *, candidates: Sequence[ClipCandidateDraft], limit: int) -> RerankResult:
        """Ask the quality model to order the deduplicated candidates."""
        response, latency_ms = self._call(
            model=self._reranking_model,
            messages=[
                {"role": "system", "content": _RERANK_SYSTEM_PROMPT},
                {"role": "user", "content": _rerank_prompt(candidates, limit)},
            ],
            schema_name="clip_candidate_order",
            schema=order_json_schema(),
        )
        body = _json_body(response)
        order = body.get("order")
        if not isinstance(order, list) or not all(
            isinstance(item, int) and not isinstance(item, bool) for item in order
        ):
            raise HighlightProviderTerminalError(INVALID_CODE)
        return RerankResult(
            order=tuple(cast(list[int], order)),
            call=self._call_record(
                response,
                model=self._reranking_model,
                latency_ms=latency_ms,
                prompt_version=RERANK_PROMPT_VERSION,
                operation=RERANK_OPERATION,
            ),
        )

    def _call(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        schema_name: str,
        schema: dict[str, Any],
    ) -> tuple[Any, int]:
        """Spend the retry budget on transient failures only, and time the successful call."""
        attempt = 0
        while True:
            attempt += 1
            started = self._clock()
            try:
                response = self._completions.create(
                    model=model,
                    messages=messages,
                    temperature=0,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    reasoning_effort=REASONING_EFFORT,
                    max_completion_tokens=MAX_COMPLETION_TOKENS,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {"name": schema_name, "strict": True, "schema": schema},
                    },
                )
            except Exception as error:
                code = _failure_code(error)
                if code in {RATE_LIMITED_CODE, UNAVAILABLE_CODE, SCHEMA_REFUSED_CODE}:
                    if attempt >= self._max_attempts:
                        raise HighlightProviderRetryableError(code) from None
                    self._sleep(RETRY_BASE_DELAY_SECONDS * attempt)
                    continue
                raise HighlightProviderTerminalError(code) from None
            return response, round((self._clock() - started) * 1000)

    def _call_record(
        self,
        response: Any,
        *,
        model: str,
        latency_ms: int,
        prompt_version: str,
        operation: str,
    ) -> ProviderCall:
        """Describe the completed call without keeping any SDK object."""
        usage = getattr(response, "usage", None)
        return ProviderCall(
            provider=PROVIDER,
            operation=operation,
            model=model,
            request_id=_text(getattr(response, "id", "")),
            latency_ms=latency_ms,
            input_units=_units(getattr(usage, "prompt_tokens", 0)),
            output_units=_units(getattr(usage, "completion_tokens", 0)),
            prompt_version=prompt_version,
            schema_version=CANDIDATE_SCHEMA_VERSION,
        )


def candidate_json_schema() -> dict[str, Any]:
    """Describe exactly the candidate list a provider may return, and nothing else."""
    return _object(
        {
            "candidates": {
                "type": "array",
                "items": _object(
                    {
                        "hook": {"type": "string"},
                        "payoff": {"type": "string"},
                        "reason": {"type": "string"},
                        "category": {
                            "type": "string",
                            "enum": [category.value for category in ClipCategory],
                        },
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "start_word_id": {"type": "string"},
                        "end_word_id": {"type": "string"},
                        "transcript_excerpt": {"type": "string"},
                        "context_dependencies": {"type": "array", "items": {"type": "string"}},
                        "context_warnings": {"type": "array", "items": {"type": "string"}},
                        "visual_opportunities": {"type": "array", "items": {"type": "string"}},
                        "score_breakdown": _object(
                            {
                                name: {"type": "number", "minimum": 0, "maximum": 1}
                                for name in (
                                    "hook",
                                    "payoff",
                                    "narrative_completeness",
                                    "context_safety",
                                    "platform_fit",
                                    "transcript_confidence",
                                    "visual_opportunity",
                                )
                            }
                        ),
                    }
                ),
            }
        }
    )


def order_json_schema() -> dict[str, Any]:
    """Describe the reranking answer as positions only, never as new candidate text."""
    return _object({"order": {"type": "array", "items": {"type": "integer", "minimum": 0}}})


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    """Build one strict object schema in which every property is required."""
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(properties),
        "additionalProperties": False,
    }


def _extraction_prompt(window: TranscriptWindow, target_count: int) -> str:
    """Show the model the window text and the word IDs it must key candidates to."""
    return json.dumps(
        {
            "target_count": target_count,
            "window_index": window.index,
            "word_ids": list(window.word_ids),
            "transcript": window.text,
        },
        ensure_ascii=False,
    )


def _rerank_prompt(candidates: Sequence[ClipCandidateDraft], limit: int) -> str:
    """Show the quality model each candidate by position, with its own evidence."""
    return json.dumps(
        {
            "limit": limit,
            "candidates": [
                {
                    "position": position,
                    "hook": candidate.hook,
                    "payoff": candidate.payoff,
                    "category": candidate.category.value,
                    "duration_ms": candidate.duration_ms,
                    "transcript_excerpt": candidate.transcript_excerpt,
                    "context_warnings": list(candidate.context_warnings),
                    "score_breakdown": candidate.score_breakdown.model_dump(),
                }
                for position, candidate in enumerate(candidates)
            ],
        },
        ensure_ascii=False,
    )


def _json_body(response: Any) -> dict[str, Any]:
    """Parse the single completion body, refusing anything that is not a JSON object."""
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        raise HighlightProviderTerminalError(INVALID_CODE) from None
    if not isinstance(content, str):
        raise HighlightProviderTerminalError(INVALID_CODE)
    try:
        body = json.loads(content)
    except ValueError:
        raise HighlightProviderTerminalError(INVALID_CODE) from None
    if not isinstance(body, dict):
        raise HighlightProviderTerminalError(INVALID_CODE)
    return cast(dict[str, Any], body)


def _failure_code(error: Exception) -> str:
    """Classify one provider failure by status alone, never by its message alone."""
    status = getattr(error, "status_code", None)
    if not isinstance(status, int) or isinstance(status, bool):
        return UNAVAILABLE_CODE
    if status == 429:
        return RATE_LIMITED_CODE
    if status >= 500:
        return UNAVAILABLE_CODE
    if status == 400 and any(marker in str(error).casefold() for marker in _OVERFLOW_MARKERS):
        return WINDOW_TOO_LARGE_CODE
    # Groq validates strict-schema output on its own side and refuses a reply that does not
    # conform. That is a property of one generation, not of the request, so the same window
    # is worth asking for again rather than being dropped on the first unlucky roll.
    if status == 400 and any(marker in str(error).casefold() for marker in _SCHEMA_REFUSAL_MARKERS):
        return SCHEMA_REFUSED_CODE
    return REJECTED_CODE


def _text(value: object) -> str:
    """Keep only a string identifier, never an SDK object."""
    return value if isinstance(value, str) else ""


def _units(value: object) -> int:
    """Keep only a non-negative integer count of provider units."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value
