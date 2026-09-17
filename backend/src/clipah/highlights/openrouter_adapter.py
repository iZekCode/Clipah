"""OpenRouter adapter that lets a model choose clip boundaries only from offered spans.

Extraction is two requests. The first asks for span labels this deployment built from the
stored words; the second describes the spans that survived local resolution, seeing only
canonical transcript text. A model therefore never supplies a word ID, a timestamp, or an
excerpt, and a label the offer did not contain costs nothing beyond one refusal.

Gemini's OpenAI-compatible endpoint answers the same forced tool calls, so a Gemini
deployment is this adapter pointed at a different base URL under its own provider name.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, cast

import httpx

from clipah.highlights.extractor import CandidateValidationError
from clipah.highlights.models import (
    DEFAULT_CANDIDATE_POLICY,
    CandidatePolicy,
    ClipCandidateDraft,
    ClipCategory,
    TranscriptWindow,
)
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
from clipah.highlights.sentences import SentenceSpan, resolve_span, sentence_spans
from clipah.highlights.windowing import window_text
from clipah.observability.logging import get_logger

PROVIDER = "openrouter"
EXTRACTION_PROMPT_VERSION = "highlights/span-extract/2"
RERANK_PROMPT_VERSION = "highlights/rerank/1"
BASE_URL = "https://openrouter.ai/api/v1"
GEMINI_PROVIDER = "gemini"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
REQUEST_TIMEOUT_SECONDS = 300.0
_logger = get_logger(__name__)
RETRY_BASE_DELAY_SECONDS = 2.0
MAX_COMPLETION_TOKENS = 16_000
RATE_LIMITED_CODE = "HIGHLIGHT_PROVIDER_RATE_LIMITED"
UNAVAILABLE_CODE = "HIGHLIGHT_PROVIDER_UNAVAILABLE"
REJECTED_CODE = "HIGHLIGHT_PROVIDER_REJECTED"
INVALID_CODE = "HIGHLIGHT_PROVIDER_INVALID"

_SELECTION_SYSTEM_PROMPT = (
    "You find complete, self-contained short-form moments in a transcript. Choose each "
    "moment as one span label Sx-Sy, covering sentence Sx through sentence Sy, where Sy "
    "is not before Sx. A span lasts from the start of Sx to the end of Sy; use each "
    "sentence's seconds so it lasts between clip_seconds.min and clip_seconds.max. Start "
    "where the thought starts, not on a reference to something said earlier, and end where "
    "it finishes. Return distinct moments spread across the whole transcript, and return "
    "only a span label and a short reason."
)
_METADATA_SYSTEM_PROMPT = (
    "You describe short-form clips whose boundaries are already fixed. For every clip "
    "position, write metadata from that clip's transcript only, and report every context "
    "dependency or warning you notice, such as an opening that refers to something said "
    "earlier or an ending that stops before the thought is finished."
)
_RERANK_SYSTEM_PROMPT = (
    "You order clip candidates from best to worst using narrative completeness, context "
    "safety, hook strength, payoff, clarity without external context, emotional or "
    "informational value, transcript confidence, platform fit, and visual opportunity. "
    "Return every candidate position exactly once."
)


class _Completions(Protocol):
    """Structural subset of the chat-completions client this adapter drives."""

    def create(self, **request: Any) -> Any:
        """Submit one chat completion request and return its parsed body."""


class OpenRouterHighlightProvider:
    """Translate OpenRouter tool calls into Clipah's extraction and reranking contracts."""

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
        policy: CandidatePolicy = DEFAULT_CANDIDATE_POLICY,
        provider: str = PROVIDER,
        base_url: str = BASE_URL,
    ) -> None:
        """Bind the configured models and an injectable clock, sleep, and client."""
        self._extraction_model = extraction_model
        self._reranking_model = reranking_model
        self._provider = provider
        self._completions = completions or _HttpCompletions(
            api_key or "", base_url=base_url, provider=provider
        )
        self._clock = clock
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._policy = policy

    def extract(self, *, window: TranscriptWindow, target_count: int) -> ExtractionResult:
        """Select spans from this window's offer, then describe the ones that resolve."""
        spans = sentence_spans(window, policy=self._policy)
        selection, latency_ms = self._call(
            model=self._extraction_model,
            system=_SELECTION_SYSTEM_PROMPT,
            prompt=_selection_prompt(spans, target_count, self._policy),
            schema=_selection_schema(),
        )
        resolved = _resolved(_candidates(selection), spans)
        if not resolved:
            raise HighlightProviderTerminalError(INVALID_CODE)
        described, metadata_latency = self._call(
            model=self._extraction_model,
            system=_METADATA_SYSTEM_PROMPT,
            prompt=_metadata_prompt(resolved, window),
            schema=_metadata_schema(len(resolved)),
        )
        proposals = _merged(resolved, _candidates(described), window)
        if not proposals:
            raise HighlightProviderTerminalError(INVALID_CODE)
        return ExtractionResult(
            proposals=tuple(proposals),
            call=self._call_record(
                selection,
                model=self._extraction_model,
                latency_ms=latency_ms + metadata_latency,
                prompt_version=EXTRACTION_PROMPT_VERSION,
                operation=EXTRACT_OPERATION,
                extra=described,
            ),
        )

    def rerank(self, *, candidates: Sequence[ClipCandidateDraft], limit: int) -> RerankResult:
        """Ask the quality model to order the deduplicated candidates by position."""
        response, latency_ms = self._call(
            model=self._reranking_model,
            system=_RERANK_SYSTEM_PROMPT,
            prompt=_rerank_prompt(candidates, limit),
            schema=_order_schema(),
        )
        body = _arguments(response)
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
        self, *, model: str, system: str, prompt: str, schema: dict[str, Any]
    ) -> tuple[Any, int]:
        """Spend the retry budget on transient failures only, and time the successful call."""
        attempt = 0
        while True:
            attempt += 1
            started = self._clock()
            try:
                response = self._completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0,
                    max_tokens=MAX_COMPLETION_TOKENS,
                    tools=[
                        {
                            "type": "function",
                            "function": {"name": schema["name"], "parameters": schema["schema"]},
                        }
                    ],
                    tool_choice={"type": "function", "function": {"name": schema["name"]}},
                )
            except Exception as error:
                code = _failure_code(error)
                if code in {RATE_LIMITED_CODE, UNAVAILABLE_CODE}:
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
        extra: Any = None,
    ) -> ProviderCall:
        """Describe the completed operation, counting every request it needed."""
        usage = _usage(response)
        second = _usage(extra) if extra is not None else {}
        return ProviderCall(
            provider=self._provider,
            operation=operation,
            model=model,
            request_id=_text(response.get("id") if isinstance(response, Mapping) else ""),
            latency_ms=latency_ms,
            input_units=_units(usage.get("prompt_tokens")) + _units(second.get("prompt_tokens")),
            output_units=(
                _units(usage.get("completion_tokens")) + _units(second.get("completion_tokens"))
            ),
            prompt_version=prompt_version,
            schema_version=CANDIDATE_SCHEMA_VERSION,
        )


class _HttpCompletions:
    """The production transport, kept minimal so no SDK type escapes this module."""

    def __init__(self, api_key: str, *, base_url: str, provider: str) -> None:
        """Bind one keyed client whose key never travels in a URL."""
        self._provider = provider
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

    def create(self, **request: Any) -> Any:
        """Submit one request and raise a status-bearing failure for any refusal."""
        response = self._http.post("/chat/completions", json=request)
        body = response.json() if response.content else {}
        if response.status_code != 200 or "error" in body:
            status = refusal_status(response.status_code, body)
            # The status alone is logged: provider text never leaves this module.
            _logger.warning(
                "highlight.provider_refused",
                provider=self._provider,
                status=response.status_code,
                statusCode=status,
            )
            raise _ProviderRefusalError(status)
        return body


def refusal_status(http_status: int, body: Mapping[str, Any]) -> int:
    """Name the status one refusal should be classified by.

    OpenRouter reports an upstream model failure, a rate limit, or a timeout inside an
    otherwise successful response, carrying the real status as the error's own code. Reading
    only the HTTP status would call every one of those a permanent refusal.
    """
    error = body.get("error") if isinstance(body, Mapping) else None
    code = error.get("code") if isinstance(error, Mapping) else None
    if isinstance(code, int) and not isinstance(code, bool) and 400 <= code <= 599:
        return code
    if http_status == 200:
        return 502
    return http_status


class _ProviderRefusalError(Exception):
    """One refused request, carrying only the status the adapter classifies on."""

    def __init__(self, status_code: int) -> None:
        """Retain the status without any provider text."""
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def _selection_prompt(
    spans: tuple[SentenceSpan, ...], target_count: int, policy: CandidatePolicy
) -> str:
    """Show every sentence with its time and the duration rule a span must satisfy.

    Listing every valid pair instead grows with the square of the sentence count, which put
    a 41-minute source at 871k tokens; a pair outside the rule is refused locally anyway.
    """
    return json.dumps(
        {
            "target_count": target_count,
            "clip_seconds": {
                "min": policy.min_duration_ms // 1000,
                "max": policy.max_duration_ms // 1000,
            },
            "sentences": [
                {
                    "id": span.label,
                    "seconds": f"{span.start_ms / 1000:.1f}-{span.end_ms / 1000:.1f}",
                    "text": span.text,
                }
                for span in spans
            ],
        },
        ensure_ascii=False,
    )


def _metadata_prompt(resolved: list[tuple[str, str]], window: TranscriptWindow) -> str:
    """Show each accepted clip by position, with only the words the transcript authorizes."""
    return json.dumps(
        {
            "clips": [
                {
                    "position": position,
                    "duration_seconds": round(_span_words(window, pair)[-1].end_ms / 1000, 1)
                    - round(_span_words(window, pair)[0].start_ms / 1000, 1),
                    "transcript": window_text(_span_words(window, pair)),
                }
                for position, pair in enumerate(resolved)
            ]
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


def _selection_schema() -> dict[str, Any]:
    """Describe a reply that may carry span labels and reasons, and nothing else."""
    return {
        "name": "clip_spans",
        "schema": _object(
            {
                "candidates": {
                    "type": "array",
                    "items": _object({"span": {"type": "string"}, "reason": {"type": "string"}}),
                }
            }
        ),
    }


def _metadata_schema(count: int) -> dict[str, Any]:
    """Describe metadata for fixed positions, with no field that could move a boundary."""
    return {
        "name": "clip_metadata",
        "schema": _object(
            {
                "candidates": {
                    "type": "array",
                    "items": _object(
                        {
                            "position": {"type": "integer", "minimum": 0, "maximum": count - 1},
                            "hook": {"type": "string"},
                            "payoff": {"type": "string"},
                            "reason": {"type": "string"},
                            "category": {
                                "type": "string",
                                "enum": [category.value for category in ClipCategory],
                            },
                            "tags": {"type": "array", "items": {"type": "string"}},
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
        ),
    }


def _order_schema() -> dict[str, Any]:
    """Describe the reranking answer as positions only, never as new candidate text."""
    return {
        "name": "clip_candidate_order",
        "schema": _object({"order": {"type": "array", "items": {"type": "integer", "minimum": 0}}}),
    }


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    """Build one strict object schema in which every property is required."""
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(properties),
        "additionalProperties": False,
    }


def _resolved(
    proposals: Sequence[Mapping[str, Any]], spans: tuple[SentenceSpan, ...]
) -> list[tuple[str, str]]:
    """Keep the boundaries the offer authorizes, in the order they were proposed."""
    resolved: list[tuple[str, str]] = []
    for proposal in proposals:
        label = proposal.get("span")
        if not isinstance(label, str):
            continue
        try:
            pair = resolve_span(label, spans)
        except CandidateValidationError:
            continue
        if pair not in resolved:
            resolved.append(pair)
    return resolved


def _merged(
    resolved: list[tuple[str, str]],
    metadata: Sequence[Mapping[str, Any]],
    window: TranscriptWindow,
) -> list[Mapping[str, Any]]:
    """Attach metadata to fixed boundaries by position, discarding anything undescribed."""
    by_position: dict[int, Mapping[str, Any]] = {}
    for item in metadata:
        position = item.get("position")
        if isinstance(position, int) and not isinstance(position, bool):
            by_position.setdefault(position, item)
    proposals: list[Mapping[str, Any]] = []
    for position, pair in enumerate(resolved):
        described = by_position.get(position)
        if described is None:
            continue
        words = _span_words(window, pair)
        proposals.append(
            {
                key: value
                for key, value in described.items()
                if key not in {"position", "span", "start_word_id", "end_word_id"}
            }
            | {
                "start_word_id": pair[0],
                "end_word_id": pair[1],
                "transcript_excerpt": window_text(words),
            }
        )
    return proposals


def _span_words(window: TranscriptWindow, pair: tuple[str, str]) -> tuple[Any, ...]:
    """Return the authoritative words one resolved span covers."""
    index = {word.word_id: position for position, word in enumerate(window.words)}
    return window.words[index[pair[0]] : index[pair[1]] + 1]


def _candidates(response: Any) -> Sequence[Mapping[str, Any]]:
    """Read the one candidate list a tool-call reply may carry."""
    body = _arguments(response)
    proposals = body.get("candidates")
    if not isinstance(proposals, list) or not all(isinstance(item, dict) for item in proposals):
        raise HighlightProviderTerminalError(INVALID_CODE)
    return cast(list[Mapping[str, Any]], proposals)


def _arguments(response: Any) -> dict[str, Any]:
    """Parse the forced tool call, refusing a reply that answered in any other shape."""
    try:
        message = response["choices"][0]["message"]
        arguments = message["tool_calls"][0]["function"]["arguments"]
        body = json.loads(arguments)
    except (KeyError, IndexError, TypeError, ValueError):
        raise HighlightProviderTerminalError(INVALID_CODE) from None
    if not isinstance(body, dict):
        raise HighlightProviderTerminalError(INVALID_CODE)
    return cast(dict[str, Any], body)


def _failure_code(error: Exception) -> str:
    """Classify one provider failure by status alone, never by its message alone."""
    if isinstance(error, HighlightProviderTerminalError):
        raise error
    status = getattr(error, "status_code", None)
    if not isinstance(status, int) or isinstance(status, bool):
        return UNAVAILABLE_CODE
    if status == 429:
        return RATE_LIMITED_CODE
    if status >= 500:
        return UNAVAILABLE_CODE
    return REJECTED_CODE


def _usage(response: Any) -> Mapping[str, Any]:
    """Read the usage mapping of one reply, tolerating a provider that omits it."""
    usage = response.get("usage") if isinstance(response, Mapping) else None
    return usage if isinstance(usage, Mapping) else {}


def _text(value: object) -> str:
    """Keep only a string identifier, never a provider object."""
    return value if isinstance(value, str) else ""


def _units(value: object) -> int:
    """Keep only a non-negative integer count of provider units."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value
