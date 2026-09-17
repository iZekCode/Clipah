"""Routing between the configured highlight provider and its deterministic fallback.

The router is also where a deployment learns, at startup, that a configured model alias is
unknown or already retired — long before an analysis Job would fail on it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date

from clipah.config import Settings
from clipah.highlights.groq_adapter import GroqHighlightProvider
from clipah.highlights.models import CandidatePolicy, ClipCandidateDraft, TranscriptWindow
from clipah.highlights.openrouter_adapter import (
    GEMINI_BASE_URL,
    GEMINI_PROVIDER,
    OpenRouterHighlightProvider,
)
from clipah.highlights.provider import (
    ExtractionResult,
    HighlightProvider,
    HighlightProviderRetryableError,
    RerankResult,
)
from clipah.observability.logging import get_logger

_logger = get_logger(__name__)
GEMINI_LAST_MODEL_ATTEMPTS = 3

SUPPORTED_HIGHLIGHT_MODELS: Mapping[str, date | None] = {
    "openai/gpt-oss-20b": None,
    "openai/gpt-oss-120b": None,
    "nvidia/nemotron-3-super-120b-a12b": None,
    "nvidia/nemotron-3-super-120b-a12b:free": None,
    "gemini-3.5-flash-lite": None,
    "gemini-3.5-flash": None,
    "gemini-3.6-flash": None,
    "gemini-3.7-flash": None,
    "gemini-3.8-flash": None,
}


class UnsupportedHighlightModelError(Exception):
    """Refuse a model alias this build does not know or the provider has retired."""


class HighlightProviderChain:
    """Serve one request from the first model in order that has capacity for it.

    Only a retryable failure — a rate limit or an outage — moves the request down the chain.
    A refusal or an invalid reply is about the request, so another model is not asked. A
    chain lives for one analysis and remembers how far down it has moved, so a later
    request never waits out a model an earlier one already gave up on.
    """

    def __init__(self, providers: Sequence[HighlightProvider]) -> None:
        """Bind the providers in the order they should be tried."""
        if not providers:
            raise ValueError("a provider chain needs at least one provider")
        self._providers = tuple(providers)
        self._start = 0

    def extract(self, *, window: TranscriptWindow, target_count: int) -> ExtractionResult:
        """Extract from the first provider that is available."""
        return self._first_available(
            lambda provider: provider.extract(window=window, target_count=target_count)
        )

    def rerank(self, *, candidates: Sequence[ClipCandidateDraft], limit: int) -> RerankResult:
        """Rerank with the first provider that is available."""
        return self._first_available(
            lambda provider: provider.rerank(candidates=candidates, limit=limit)
        )

    def _first_available[T](self, call: Callable[[HighlightProvider], T]) -> T:
        """Walk the chain, raising the last provider's failure when none could serve."""
        for position in range(self._start, len(self._providers)):
            try:
                return call(self._providers[position])
            except HighlightProviderRetryableError as error:
                if position == len(self._providers) - 1:
                    raise
                self._start = position + 1
                _logger.warning(
                    "highlight.provider_fallback", fromPosition=position, code=error.code
                )
        raise AssertionError("unreachable")  # pragma: no cover


class HighlightProviderRouter:
    """Serve extraction and reranking from the primary provider, falling back when needed."""

    def __init__(
        self,
        *,
        primary: HighlightProvider,
        fallback: HighlightProvider | None = None,
    ) -> None:
        """Bind the configured provider and the offline provider that covers an outage."""
        self._primary = primary
        self._fallback = fallback

    def extract(self, *, window: TranscriptWindow, target_count: int) -> ExtractionResult:
        """Extract from the primary provider, or from the fallback when it is unreachable."""
        try:
            return self._primary.extract(window=window, target_count=target_count)
        except HighlightProviderRetryableError:
            if self._fallback is None:
                raise
            return self._fallback.extract(window=window, target_count=target_count)

    def rerank(self, *, candidates: Sequence[ClipCandidateDraft], limit: int) -> RerankResult:
        """Rerank with the primary provider, or with the fallback when it is unreachable."""
        try:
            return self._primary.rerank(candidates=candidates, limit=limit)
        except HighlightProviderRetryableError:
            if self._fallback is None:
                raise
            return self._fallback.rerank(candidates=candidates, limit=limit)


def highlight_provider_router(
    settings: Settings,
    *,
    today: date,
    supported_models: Mapping[str, date | None] = SUPPORTED_HIGHLIGHT_MODELS,
    fallback: HighlightProvider | None = None,
) -> HighlightProviderRouter:
    """Build the configured provider after proving both of its models are still usable."""
    builders = {
        "openrouter": _openrouter_provider,
        "gemini": _gemini_provider,
        "groq": _groq_provider,
    }
    primary = builders[settings.highlight_provider](
        settings, today=today, supported_models=supported_models
    )
    return HighlightProviderRouter(primary=primary, fallback=fallback)


def _groq_provider(
    settings: Settings, *, today: date, supported_models: Mapping[str, date | None]
) -> HighlightProvider:
    """Build the Groq adapter, refusing a deployment without its credential."""
    if settings.groq_api_key is None:
        raise RuntimeError("highlight analysis requires a configured Groq API key")
    for alias in (settings.groq_extraction_model, settings.groq_reranking_model):
        assert_model_supported(alias, today=today, supported_models=supported_models)
    return GroqHighlightProvider(
        api_key=settings.groq_api_key.get_secret_value(),
        extraction_model=settings.groq_extraction_model,
        reranking_model=settings.groq_reranking_model,
    )


def _openrouter_provider(
    settings: Settings, *, today: date, supported_models: Mapping[str, date | None]
) -> HighlightProvider:
    """Build the OpenRouter adapter, refusing a deployment without its credential."""
    if settings.openrouter_api_key is None:
        raise RuntimeError("highlight analysis requires a configured OpenRouter API key")
    for alias in (settings.openrouter_extraction_model, settings.openrouter_reranking_model):
        assert_model_supported(alias, today=today, supported_models=supported_models)
    return OpenRouterHighlightProvider(
        api_key=settings.openrouter_api_key.get_secret_value(),
        extraction_model=settings.openrouter_extraction_model,
        reranking_model=settings.openrouter_reranking_model,
        policy=CandidatePolicy(
            min_duration_ms=settings.analysis_candidate_min_duration_ms,
            max_duration_ms=settings.analysis_candidate_max_duration_ms,
        ),
    )


def _gemini_provider(
    settings: Settings, *, today: date, supported_models: Mapping[str, date | None]
) -> HighlightProvider:
    """Build the Gemini model chain, refusing a deployment without its key.

    Every alias in the chain is proven usable at startup, so a typo in a fallback surfaces
    before the outage that would have needed it.
    """
    if settings.gemini_api_key is None:
        raise RuntimeError("highlight analysis requires a configured Gemini API key")
    models = [(settings.gemini_extraction_model, settings.gemini_reranking_model)] + [
        (alias, alias) for alias in settings.gemini_fallback_models
    ]
    for extraction, reranking in models:
        for alias in (extraction, reranking):
            assert_model_supported(alias, today=today, supported_models=supported_models)
    policy = CandidatePolicy(
        min_duration_ms=settings.analysis_candidate_min_duration_ms,
        max_duration_ms=settings.analysis_candidate_max_duration_ms,
    )
    api_key = settings.gemini_api_key.get_secret_value()
    last = len(models) - 1
    return HighlightProviderChain(
        [
            OpenRouterHighlightProvider(
                api_key=api_key,
                extraction_model=extraction,
                reranking_model=reranking,
                policy=policy,
                provider=GEMINI_PROVIDER,
                base_url=GEMINI_BASE_URL,
                # Another model is waiting, so an unavailable one is not retried; only the
                # last model spends a retry budget before the analysis gives up on Gemini.
                max_attempts=GEMINI_LAST_MODEL_ATTEMPTS if position == last else 1,
            )
            for position, (extraction, reranking) in enumerate(models)
        ]
    )


def assert_model_supported(
    alias: str,
    *,
    today: date,
    supported_models: Mapping[str, date | None] = SUPPORTED_HIGHLIGHT_MODELS,
) -> None:
    """Refuse an alias this build does not know, or one past its provider shutdown date."""
    if alias not in supported_models:
        raise UnsupportedHighlightModelError(f"unknown highlight model alias: {alias}")
    shutdown = supported_models[alias]
    if shutdown is not None and today >= shutdown:
        raise UnsupportedHighlightModelError(f"retired highlight model alias: {alias}")
