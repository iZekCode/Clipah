"""Contracts for the Groq extraction adapter and the provider router around it."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from pydantic import SecretStr

from clipah.config import Settings
from clipah.highlights.groq_adapter import (
    EXTRACTION_PROMPT_VERSION,
    GroqHighlightProvider,
    candidate_json_schema,
)
from clipah.highlights.models import (
    ClipCandidateDraft,
    ClipCategory,
    ScoreBreakdown,
    TranscriptWindow,
)
from clipah.highlights.provider import (
    EXTRACT_OPERATION,
    RERANK_OPERATION,
    DeterministicHighlightProvider,
    FakeHighlightProvider,
    HighlightProviderRetryableError,
    HighlightProviderTerminalError,
    ProviderCall,
    RerankResult,
)
from clipah.highlights.provider_router import (
    SUPPORTED_HIGHLIGHT_MODELS,
    HighlightProviderRouter,
    UnsupportedHighlightModelError,
    highlight_provider_router,
)
from clipah.jobs.analyze_task import production_analysis_dependencies, production_analysis_policy
from clipah.transcripts.models import TranscriptResult, TranscriptWord

WINDOW = TranscriptWindow(
    index=0,
    start_ms=0,
    end_ms=120_000,
    word_ids=("w000001", "w000002"),
    text="Growth stalled, until we cut the form!",
)

PROPOSAL: dict[str, Any] = {
    "hook": "The form was the ceiling",
    "payoff": "Removing it doubled activation",
    "reason": "One complete decision",
    "category": "insight",
    "tags": ["growth"],
    "start_word_id": "w000001",
    "end_word_id": "w000002",
    "transcript_excerpt": "Growth stalled, until we cut the form!",
    "context_dependencies": [],
    "context_warnings": [],
    "visual_opportunities": ["Show the form"],
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


class _Response:
    """The minimal completion shape the adapter is allowed to read."""

    def __init__(self, content: str, *, request_id: str = "req_1") -> None:
        """Bind one body and the identifiers usage recording needs."""
        self.id = request_id
        self.choices = [type("Choice", (), {"message": type("Message", (), {"content": content})})]
        self.usage = type("Usage", (), {"prompt_tokens": 1_200, "completion_tokens": 300})


_FALLBACK_CALL = ProviderCall(
    provider="deterministic",
    operation=RERANK_OPERATION,
    model="deterministic",
    request_id="",
    latency_ms=0,
    input_units=0,
    output_units=0,
    prompt_version="deterministic/1",
    schema_version="clip-candidate/1",
)


class _Usage:
    """The usage counters a provider may report, including unusable ones."""

    def __init__(self, *, prompt_tokens: object, completion_tokens: object) -> None:
        """Bind exactly what the SDK would have carried."""
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeCompletions:
    """Record every request and replay queued responses or provider failures."""

    def __init__(self, *outcomes: object) -> None:
        """Queue one outcome per expected call."""
        self.outcomes = list(outcomes)
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        """Return the next queued response or raise the next queued provider failure."""
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _ProviderError(Exception):
    """A provider failure carrying only the status the adapter may classify on."""

    def __init__(self, status_code: int | None, message: str = "provider said no") -> None:
        """Bind the HTTP status the SDK would have reported."""
        self.status_code = status_code
        super().__init__(message)


def _provider(
    *outcomes: object,
    max_attempts: int = 3,
) -> tuple[GroqHighlightProvider, _FakeCompletions, list[float]]:
    """Build one adapter over a fake completions client with a pinned clock."""
    completions = _FakeCompletions(*outcomes)
    ticks = iter([0.0, 0.25] * 20)
    slept: list[float] = []
    provider = GroqHighlightProvider(
        completions=completions,
        extraction_model="openai/gpt-oss-20b",
        reranking_model="openai/gpt-oss-120b",
        clock=lambda: next(ticks),
        sleep=slept.append,
        max_attempts=max_attempts,
    )
    return provider, completions, slept


def _body(proposals: list[dict[str, Any]]) -> str:
    """Render one schema-shaped provider body."""
    return json.dumps({"candidates": proposals})


def _draft(hook: str) -> ClipCandidateDraft:
    """Build one deduplicated candidate for a reranking request."""
    return ClipCandidateDraft(
        hook=hook,
        payoff="payoff",
        reason="reason",
        category=ClipCategory.INSIGHT,
        tags=(),
        start_word_id="w000001",
        end_word_id="w000060",
        start_ms=0,
        end_ms=30_000,
        duration_ms=30_000,
        transcript_excerpt="excerpt",
        context_dependencies=(),
        context_warnings=(),
        visual_opportunities=(),
        score_breakdown=ScoreBreakdown(
            hook=0.5,
            payoff=0.5,
            narrative_completeness=0.5,
            context_safety=0.5,
            platform_fit=0.5,
            transcript_confidence=0.5,
            visual_opportunity=0.5,
        ),
    )


@pytest.mark.unit
def test_requests_a_strict_schema_with_every_field_required() -> None:
    """A permissive schema lets a model return fields Clipah never validates."""
    schema = candidate_json_schema()

    def assert_strict(node: dict[str, Any]) -> None:
        if node.get("type") != "object":
            return
        assert node["additionalProperties"] is False
        assert sorted(node["required"]) == sorted(node["properties"])
        for child in node["properties"].values():
            assert_strict(child)
            if child.get("type") == "array":
                assert_strict(child["items"])

    assert_strict(schema)


@pytest.mark.unit
def test_extracts_proposals_and_reports_the_call_it_made() -> None:
    """Usage recording needs the provider, model, request ID, latency, and tokens."""
    provider, completions, _ = _provider(_Response(_body([PROPOSAL])))

    result = provider.extract(window=WINDOW, target_count=5)

    assert result.proposals == (PROPOSAL,)
    assert result.call.provider == "groq"
    assert result.call.model == "openai/gpt-oss-20b"
    assert result.call.request_id == "req_1"
    assert result.call.latency_ms == 250
    assert (result.call.input_units, result.call.output_units) == (1_200, 300)
    assert result.call.prompt_version == EXTRACTION_PROMPT_VERSION
    assert result.call.operation == EXTRACT_OPERATION
    assert completions.requests[0]["model"] == "openai/gpt-oss-20b"
    assert completions.requests[0]["temperature"] == 0
    assert completions.requests[0]["response_format"]["type"] == "json_schema"


@pytest.mark.unit
def test_sends_the_window_text_and_its_word_ids() -> None:
    """A model can only key a candidate to words it was shown."""
    provider, completions, _ = _provider(_Response(_body([PROPOSAL])))

    provider.extract(window=WINDOW, target_count=5)

    prompt = json.dumps(completions.requests[0]["messages"])
    assert WINDOW.text in prompt
    assert "w000001" in prompt


@pytest.mark.unit
def test_rejects_a_body_that_is_not_valid_json() -> None:
    """Unparseable provider output is a permanent failure for that window."""
    provider, _, _ = _provider(_Response("not json at all"))

    with pytest.raises(HighlightProviderTerminalError) as raised:
        provider.extract(window=WINDOW, target_count=5)

    assert raised.value.code == "HIGHLIGHT_PROVIDER_INVALID"


@pytest.mark.unit
def test_rejects_a_body_without_a_candidate_list() -> None:
    """A body that ignores the schema cannot be validated against the transcript."""
    provider, _, _ = _provider(_Response(json.dumps({"candidates": {"hook": "no"}})))

    with pytest.raises(HighlightProviderTerminalError) as raised:
        provider.extract(window=WINDOW, target_count=5)

    assert raised.value.code == "HIGHLIGHT_PROVIDER_INVALID"


@pytest.mark.unit
def test_retries_a_rate_limited_request_and_succeeds() -> None:
    """A single 429 is normal under load and must not fail the window."""
    provider, completions, slept = _provider(
        _ProviderError(429), _Response(_body([PROPOSAL]), request_id="req_2")
    )

    result = provider.extract(window=WINDOW, target_count=5)

    assert result.call.request_id == "req_2"
    assert len(completions.requests) == 2
    assert slept and all(delay > 0 for delay in slept)


@pytest.mark.unit
def test_reports_an_exhausted_retry_budget_as_retryable() -> None:
    """The Job, not the adapter, decides how long to keep trying a throttled provider."""
    provider, completions, _ = _provider(_ProviderError(429), _ProviderError(429), max_attempts=2)

    with pytest.raises(HighlightProviderRetryableError) as raised:
        provider.extract(window=WINDOW, target_count=5)

    assert raised.value.code == "HIGHLIGHT_PROVIDER_RATE_LIMITED"
    assert len(completions.requests) == 2


@pytest.mark.unit
def test_retries_a_server_side_failure() -> None:
    """A 5xx is transient, so the adapter spends its budget before giving up."""
    provider, _, _ = _provider(_ProviderError(503), _ProviderError(503), max_attempts=2)

    with pytest.raises(HighlightProviderRetryableError) as raised:
        provider.extract(window=WINDOW, target_count=5)

    assert raised.value.code == "HIGHLIGHT_PROVIDER_UNAVAILABLE"


@pytest.mark.unit
def test_treats_a_context_window_overflow_as_terminal() -> None:
    """Resending an oversized window would fail identically, so it is never retried."""
    provider, completions, _ = _provider(
        _ProviderError(400, "context_length_exceeded: too many tokens")
    )

    with pytest.raises(HighlightProviderTerminalError) as raised:
        provider.extract(window=WINDOW, target_count=5)

    assert raised.value.code == "HIGHLIGHT_WINDOW_TOO_LARGE"
    assert len(completions.requests) == 1


@pytest.mark.unit
def test_treats_another_rejected_request_as_terminal() -> None:
    """A refused request is a defect in what Clipah sent, not a transient outage."""
    provider, _, _ = _provider(_ProviderError(400, "invalid api key"))

    with pytest.raises(HighlightProviderTerminalError) as raised:
        provider.extract(window=WINDOW, target_count=5)

    assert raised.value.code == "HIGHLIGHT_PROVIDER_REJECTED"


@pytest.mark.unit
def test_treats_a_transport_failure_without_a_status_as_retryable() -> None:
    """A dropped connection says nothing about the request itself."""
    provider, _, _ = _provider(_ProviderError(None), _ProviderError(None), max_attempts=2)

    with pytest.raises(HighlightProviderRetryableError) as raised:
        provider.extract(window=WINDOW, target_count=5)

    assert raised.value.code == "HIGHLIGHT_PROVIDER_UNAVAILABLE"


@pytest.mark.unit
def test_reranks_with_the_quality_model_and_returns_an_order() -> None:
    """Reranking uses the larger configured model, not the extraction one."""
    provider, completions, _ = _provider(_Response(json.dumps({"order": [1, 0]})))

    result = provider.rerank(candidates=[_draft("first"), _draft("second")], limit=10)

    assert result.order == (1, 0)
    assert result.call.operation == RERANK_OPERATION
    assert completions.requests[0]["model"] == "openai/gpt-oss-120b"


@pytest.mark.unit
def test_rejects_a_reranking_body_that_is_not_a_list_of_positions() -> None:
    """An unusable order must be refused here so ranking can fall back locally."""
    provider, _, _ = _provider(_Response(json.dumps({"order": "best first"})))

    with pytest.raises(HighlightProviderTerminalError) as raised:
        provider.rerank(candidates=[_draft("first")], limit=10)

    assert raised.value.code == "HIGHLIGHT_PROVIDER_INVALID"


@pytest.mark.unit
def test_deterministic_fallback_proposes_candidates_without_a_provider() -> None:
    """An outage must not stop analysis; the fallback proposes from the words alone."""
    words = tuple(
        TranscriptWord(
            word_id=f"w{index + 1:06d}",
            text="word",
            punctuation="." if index % 40 == 39 else "",
            start_ms=index * 500,
            end_ms=index * 500 + 500,
            confidence=0.9,
            speaker="A",
        )
        for index in range(240)
    )
    window = TranscriptWindow(
        index=0,
        start_ms=0,
        end_ms=120_000,
        word_ids=tuple(word.word_id for word in words),
        text=" ".join(f"{word.text}{word.punctuation}" for word in words),
    )
    transcript = TranscriptResult(
        provider="assemblyai",
        provider_version="1.0.0",
        model="universal-3-pro",
        language="en",
        full_text=window.text,
        words=words,
        speaker_segments=(),
        utterances=(),
        duration_ms=120_000,
        raw_result={},
    )
    fallback = DeterministicHighlightProvider(transcript=transcript)

    result = fallback.extract(window=window, target_count=3)

    assert result.call.provider == "deterministic"
    assert result.proposals
    for proposal in result.proposals:
        assert proposal["start_word_id"] in window.word_ids
        assert proposal["end_word_id"] in window.word_ids


@pytest.mark.unit
def test_router_falls_back_when_the_primary_provider_is_unavailable() -> None:
    """A configured fallback is what keeps a throttled analysis moving."""
    primary, _, _ = _provider(_ProviderError(503), _ProviderError(503), max_attempts=2)
    fallback, _, _ = _provider(_Response(_body([PROPOSAL]), request_id="req_fallback"))
    router = HighlightProviderRouter(primary=primary, fallback=fallback)

    result = router.extract(window=WINDOW, target_count=5)

    assert result.call.request_id == "req_fallback"


@pytest.mark.unit
def test_router_does_not_retry_a_terminal_failure_on_the_fallback() -> None:
    """A window the primary refused permanently would be refused again."""
    primary, _, _ = _provider(_ProviderError(400, "context_length_exceeded"))
    fallback, fallback_calls, _ = _provider(_Response(_body([PROPOSAL])))
    router = HighlightProviderRouter(primary=primary, fallback=fallback)

    with pytest.raises(HighlightProviderTerminalError):
        router.extract(window=WINDOW, target_count=5)

    assert fallback_calls.requests == []


@pytest.mark.unit
def test_router_raises_the_primary_failure_when_no_fallback_is_configured() -> None:
    """Without a fallback the Job must see the real retryable code."""
    primary, _, _ = _provider(_ProviderError(503), _ProviderError(503), max_attempts=2)
    router = HighlightProviderRouter(primary=primary, fallback=None)

    with pytest.raises(HighlightProviderRetryableError):
        router.extract(window=WINDOW, target_count=5)


def _settings(**overrides: Any) -> Settings:
    """Build settings carrying only the highlight provider fields under test."""
    values: dict[str, Any] = {
        "groq_api_key": SecretStr("test-key"),
        "groq_extraction_model": "openai/gpt-oss-20b",
        "groq_reranking_model": "openai/gpt-oss-120b",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.unit
def test_rejects_an_unknown_model_alias_at_startup() -> None:
    """A typo in a model ID must stop the process, not surface as a runtime failure."""
    with pytest.raises(UnsupportedHighlightModelError):
        highlight_provider_router(
            _settings(groq_extraction_model="openai/gpt-oss-19b"), today=date(2026, 9, 1)
        )


@pytest.mark.unit
def test_rejects_a_model_past_its_provider_shutdown_date() -> None:
    """A retired model would fail every analysis once the provider removes it."""
    alias = next(iter(SUPPORTED_HIGHLIGHT_MODELS))
    shutdown = date(2026, 1, 1)

    with pytest.raises(UnsupportedHighlightModelError):
        highlight_provider_router(
            _settings(),
            today=date(2026, 9, 1),
            supported_models={alias: shutdown, "openai/gpt-oss-120b": shutdown},
        )


@pytest.mark.unit
def test_refuses_to_build_a_router_without_provider_credentials() -> None:
    """Fail-closed configuration applies to the analysis worker like every other process."""
    with pytest.raises(RuntimeError):
        highlight_provider_router(_settings(groq_api_key=None), today=date(2026, 9, 1))


@pytest.mark.unit
def test_rejects_a_response_without_a_message_body() -> None:
    """A malformed completion must be refused instead of read as an empty answer."""
    provider, _, _ = _provider(object())

    with pytest.raises(HighlightProviderTerminalError) as raised:
        provider.extract(window=WINDOW, target_count=5)

    assert raised.value.code == "HIGHLIGHT_PROVIDER_INVALID"


@pytest.mark.unit
def test_rejects_a_body_that_is_not_a_json_object() -> None:
    """A JSON array is not the schema the adapter demanded."""
    provider, _, _ = _provider(_Response("[]"))

    with pytest.raises(HighlightProviderTerminalError) as raised:
        provider.extract(window=WINDOW, target_count=5)

    assert raised.value.code == "HIGHLIGHT_PROVIDER_INVALID"


@pytest.mark.unit
def test_ignores_unusable_usage_counters() -> None:
    """Usage recording must never store a negative or non-numeric provider count."""
    response = _Response(_body([PROPOSAL]))
    response.usage = _Usage(prompt_tokens=-5, completion_tokens=True)  # type: ignore[assignment]

    provider, _, _ = _provider(response)
    result = provider.extract(window=WINDOW, target_count=5)

    assert (result.call.input_units, result.call.output_units) == (0, 0)


@pytest.mark.unit
def test_router_falls_back_for_reranking_too() -> None:
    """An outage during reranking must not throw away the candidates already found."""
    primary = FakeHighlightProvider(
        rerank_result=HighlightProviderRetryableError("HIGHLIGHT_PROVIDER_UNAVAILABLE")
    )
    fallback = FakeHighlightProvider(rerank_result=RerankResult(order=(0,), call=_FALLBACK_CALL))
    router = HighlightProviderRouter(primary=primary, fallback=fallback)

    result = router.rerank(candidates=[_draft("first")], limit=10)

    assert result.order == (0,)
    assert fallback.rerank_calls


@pytest.mark.unit
def test_router_raises_a_reranking_failure_when_no_fallback_is_configured() -> None:
    """Without a fallback the outage must reach the analyzer's own local ranking."""
    primary = FakeHighlightProvider(
        rerank_result=HighlightProviderRetryableError("HIGHLIGHT_PROVIDER_UNAVAILABLE")
    )
    router = HighlightProviderRouter(primary=primary, fallback=None)

    with pytest.raises(HighlightProviderRetryableError):
        router.rerank(candidates=[_draft("first")], limit=10)


@pytest.mark.unit
def test_fake_provider_refuses_to_invent_an_outcome_it_was_never_given() -> None:
    """A test double must fail loudly rather than silently pass an unplanned call."""
    provider = FakeHighlightProvider()

    with pytest.raises(RuntimeError):
        provider.extract(window=WINDOW, target_count=5)
    with pytest.raises(RuntimeError):
        provider.rerank(candidates=[], limit=10)


@pytest.mark.unit
def test_production_router_uses_the_configured_models() -> None:
    """A worker must build its provider from settings alone, before any Job runs."""
    settings = _settings(
        groq_api_key="secret",
        groq_extraction_model="openai/gpt-oss-20b",
        groq_reranking_model="openai/gpt-oss-120b",
    )

    router = highlight_provider_router(settings, today=date(2026, 9, 1))

    assert isinstance(router, HighlightProviderRouter)


@pytest.mark.unit
def test_rejects_a_message_whose_content_is_not_text() -> None:
    """A completion carrying no text cannot be validated against any schema."""
    response = _Response("ignored")
    response.choices = [type("Choice", (), {"message": type("Message", (), {"content": None})})]

    provider, _, _ = _provider(response)

    with pytest.raises(HighlightProviderTerminalError) as raised:
        provider.extract(window=WINDOW, target_count=5)

    assert raised.value.code == "HIGHLIGHT_PROVIDER_INVALID"


@pytest.mark.unit
def test_deterministic_fallback_keeps_the_final_partial_span() -> None:
    """The tail of a window is still speech, so asking for more must not discard it."""
    words = tuple(
        TranscriptWord(
            word_id=f"w{index + 1:06d}",
            text="word",
            punctuation="." if index % 100 == 99 else "",
            start_ms=index * 500,
            end_ms=index * 500 + 500,
            confidence=0.9,
            speaker="A",
        )
        for index in range(240)
    )
    window = TranscriptWindow(
        index=0,
        start_ms=0,
        end_ms=words[-1].end_ms,
        word_ids=tuple(word.word_id for word in words),
        text=" ".join(word.text for word in words),
    )
    transcript = TranscriptResult(
        provider="assemblyai",
        provider_version="1.0.0",
        model="universal-3-pro",
        language="en",
        full_text=window.text,
        words=words,
        speaker_segments=(),
        utterances=(),
        duration_ms=words[-1].end_ms,
        raw_result={},
    )

    result = DeterministicHighlightProvider(transcript=transcript).extract(
        window=window, target_count=100
    )

    assert result.proposals[-1]["end_word_id"] == words[-1].word_id


@pytest.mark.unit
def test_deterministic_fallback_stops_at_the_requested_candidate_count() -> None:
    """The offline provider must respect the budget the analysis gave it."""
    words = tuple(
        TranscriptWord(
            word_id=f"w{index + 1:06d}",
            text="word",
            punctuation="." if index % 100 == 99 else "",
            start_ms=index * 500,
            end_ms=index * 500 + 500,
            confidence=0.9,
            speaker="A",
        )
        for index in range(400)
    )
    window = TranscriptWindow(
        index=0,
        start_ms=0,
        end_ms=words[-1].end_ms,
        word_ids=tuple(word.word_id for word in words),
        text=" ".join(word.text for word in words),
    )
    transcript = TranscriptResult(
        provider="assemblyai",
        provider_version="1.0.0",
        model="universal-3-pro",
        language="en",
        full_text=window.text,
        words=words,
        speaker_segments=(),
        utterances=(),
        duration_ms=words[-1].end_ms,
        raw_result={},
    )

    result = DeterministicHighlightProvider(transcript=transcript).extract(
        window=window, target_count=2
    )

    assert len(result.proposals) == 2


@pytest.mark.unit
def test_production_dependencies_build_a_router_with_the_offline_fallback() -> None:
    """A worker must compose its provider from settings alone, before any Job runs."""
    settings = _settings(
        groq_api_key="secret",
        groq_extraction_model="openai/gpt-oss-20b",
        groq_reranking_model="openai/gpt-oss-120b",
    )
    transcript = TranscriptResult(
        provider="assemblyai",
        provider_version="1.0.0",
        model="universal-3-pro",
        language="en",
        full_text="word",
        words=(TranscriptWord("w000001", "word", "", 0, 500, 0.9, "A"),),
        speaker_segments=(),
        utterances=(),
        duration_ms=500,
        raw_result={},
    )

    dependencies = production_analysis_dependencies(settings)

    assert isinstance(dependencies.provider_factory(transcript), HighlightProviderRouter)


@pytest.mark.unit
def test_production_analysis_policy_reads_every_deployment_tuning_setting() -> None:
    """Changing a deployment policy must affect workers without editing analysis modules."""
    settings = _settings(
        analysis_window_target_min_ms=100_000,
        analysis_window_target_max_ms=160_000,
        analysis_window_overlap_ms=15_000,
        analysis_window_silence_gap_ms=900,
        analysis_window_min_words=20,
        analysis_candidate_min_duration_ms=15_000,
        analysis_candidate_max_duration_ms=75_000,
        analysis_deduplication_temporal_iou=0.7,
        analysis_deduplication_excerpt_cosine=0.95,
        analysis_candidates_kept=24,
        analysis_candidates_exposed=8,
    )

    policy = production_analysis_policy(settings)

    assert policy.windowing.target_min_ms == 100_000
    assert policy.windowing.target_max_ms == 160_000
    assert policy.windowing.overlap_ms == 15_000
    assert policy.windowing.silence_gap_ms == 900
    assert policy.windowing.min_words == 20
    assert policy.candidate.min_duration_ms == 15_000
    assert policy.candidate.max_duration_ms == 75_000
    assert policy.deduplication.min_temporal_iou == 0.7
    assert policy.deduplication.min_excerpt_cosine == 0.95
    assert policy.ranking.keep == 24
    assert policy.ranking.expose == 8


@pytest.mark.unit
def test_every_request_bounds_its_completion_and_reasoning_budget() -> None:
    """A reasoning model with no budget reasons instead of answering.

    The configured Groq models reason before they reply. Asked for strict-schema JSON with
    no completion budget and no reasoning setting, they spend the whole response on
    reasoning and emit no JSON at all, and Groq refuses the call with `json_validate_failed`
    and an empty `failed_generation`. Measured against a real transcript window, the request
    this adapter used to send produced valid output on none of three attempts; with these two
    parameters it produced valid output on all three.
    """
    provider, completions, _ = _provider(_Response(json.dumps({"candidates": []})))

    provider.extract(window=WINDOW, target_count=5)

    request = completions.requests[0]
    assert request["reasoning_effort"] == "low"
    assert request["max_completion_tokens"] > 0


@pytest.mark.unit
def test_reranking_bounds_its_budget_the_same_way() -> None:
    """Reranking runs on the larger model, which reasons at least as eagerly."""
    provider, completions, _ = _provider(_Response(json.dumps({"order": [1, 0]})))

    provider.rerank(candidates=[_draft("first"), _draft("second")], limit=2)

    request = completions.requests[0]
    assert request["reasoning_effort"] == "low"
    assert request["max_completion_tokens"] > 0


@pytest.mark.unit
def test_retries_a_schema_validation_refusal() -> None:
    """A model that failed to satisfy the schema once may satisfy it next time.

    Groq validates strict-schema output on its own side and returns 400
    `json_validate_failed` when the model's reply does not conform. That is a property of
    one generation rather than of the request, so the window is worth asking for again;
    treating it as terminal dropped the window on the first unlucky roll.
    """
    provider, completions, _ = _provider(
        _ProviderError(400, "Failed to validate JSON. json_validate_failed"),
        _Response(json.dumps({"candidates": []})),
    )

    result = provider.extract(window=WINDOW, target_count=5)

    assert result.proposals == ()
    assert len(completions.requests) == 2


@pytest.mark.unit
def test_reports_an_exhausted_schema_validation_budget_as_retryable() -> None:
    """A model that never conforms is a provider problem, not a caller defect."""
    provider, _, _ = _provider(
        _ProviderError(400, "json_validate_failed"),
        _ProviderError(400, "json_validate_failed"),
        max_attempts=2,
    )

    with pytest.raises(HighlightProviderRetryableError) as raised:
        provider.extract(window=WINDOW, target_count=5)

    assert raised.value.code == "HIGHLIGHT_PROVIDER_SCHEMA_REFUSED"
