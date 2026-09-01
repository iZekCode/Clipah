"""Routing between the configured highlight provider and its deterministic fallback.

The router is also where a deployment learns, at startup, that a configured model alias is
unknown or already retired — long before an analysis Job would fail on it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from clipah.config import Settings
from clipah.highlights.groq_adapter import GroqHighlightProvider
from clipah.highlights.models import ClipCandidateDraft, TranscriptWindow
from clipah.highlights.provider import (
    ExtractionResult,
    HighlightProvider,
    HighlightProviderRetryableError,
    RerankResult,
)

SUPPORTED_HIGHLIGHT_MODELS: Mapping[str, date | None] = {
    "openai/gpt-oss-20b": None,
    "openai/gpt-oss-120b": None,
}


class UnsupportedHighlightModelError(Exception):
    """Refuse a model alias this build does not know or the provider has retired."""


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
    """Build the production router after proving both configured models are still usable."""
    if settings.groq_api_key is None:
        raise RuntimeError("highlight analysis requires a configured Groq API key")
    for alias in (settings.groq_extraction_model, settings.groq_reranking_model):
        assert_model_supported(alias, today=today, supported_models=supported_models)
    primary = GroqHighlightProvider(
        api_key=settings.groq_api_key.get_secret_value(),
        extraction_model=settings.groq_extraction_model,
        reranking_model=settings.groq_reranking_model,
    )
    return HighlightProviderRouter(primary=primary, fallback=fallback)


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
