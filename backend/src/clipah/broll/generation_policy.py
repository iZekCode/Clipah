"""Server-owned eligibility and request derivation for generated B-roll.

The browser names only a media kind. Everything a provider is asked for — the prompt,
the model, the geometry, the duration, and the output count — is derived here from the
stored suggestion and this deployment's configuration, so a client can neither widen a
request nor argue with the price it was shown.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from time import monotonic
from typing import Any

import httpx

from clipah.broll.fal_adapter import FalAdapterConfig, FalGenerativeMediaProvider
from clipah.broll.generation import (
    GenerationMediaKind,
    GenerationRequest,
    GenerativeMediaProvider,
    PromptModerator,
)
from clipah.broll.models import BrollSuggestionStatus, VisualIntent
from clipah.broll.runway_adapter import RunwayAdapterConfig, RunwayGenerativeMediaProvider
from clipah.config import Settings

GENERATED_IMAGE_WIDTH = 1080
GENERATED_IMAGE_HEIGHT = 1920
GENERATED_VIDEO_WIDTH = 720
GENERATED_VIDEO_HEIGHT = 1280
RUNWAY_CREDITS_PER_SECOND = Decimal(5)

GenerationProviders = Callable[[GenerationMediaKind], GenerativeMediaProvider | None]


class GenerationUnavailableReason(StrEnum):
    """Why a suggestion cannot be sent to a generative provider right now."""

    NOT_REVIEWABLE = "not_reviewable"
    STOCK_SUFFICIENT = "stock_sufficient"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    VIDEO_DISABLED = "video_disabled"


@dataclass(frozen=True, slots=True)
class GenerationEligibility:
    """Whether one suggestion may be generated, and the fixed reason when it may not."""

    available: bool
    reason: GenerationUnavailableReason | None = None


@dataclass(frozen=True, slots=True)
class GenerationTarget:
    """The stored suggestion facts eligibility and request derivation both read."""

    status: BrollSuggestionStatus
    asset_id: object | None
    relevance_score: float | None
    visual_intent: dict[str, Any]
    search_terms: dict[str, Any]
    exclusions: tuple[str, ...]


def suggestion_generation_refusal(
    target: GenerationTarget, *, settings: Settings
) -> GenerationUnavailableReason | None:
    """Refuse on the stored state alone, before any media kind or credential is read.

    Admission checks this first so a suggestion nobody may generate answers with why,
    rather than with a complaint about the confirmation that accompanied the request.
    """
    if target.status is not BrollSuggestionStatus.PROPOSED:
        return GenerationUnavailableReason.NOT_REVIEWABLE
    if (
        target.asset_id is not None
        and target.relevance_score is not None
        and target.relevance_score >= settings.broll_min_relevance
    ):
        return GenerationUnavailableReason.STOCK_SUFFICIENT
    return None


def generation_eligibility(
    target: GenerationTarget,
    *,
    media_kind: GenerationMediaKind,
    settings: Settings,
    providers: GenerationProviders,
) -> GenerationEligibility:
    """Apply the stock-first, review-first, feature-gated rules in one place."""
    stored_reason = suggestion_generation_refusal(target, settings=settings)
    if stored_reason is not None:
        return GenerationEligibility(False, stored_reason)
    if media_kind is GenerationMediaKind.VIDEO and not settings.generative_video_enabled:
        return GenerationEligibility(False, GenerationUnavailableReason.VIDEO_DISABLED)
    if providers(media_kind) is None:
        return GenerationEligibility(False, GenerationUnavailableReason.PROVIDER_UNAVAILABLE)
    return GenerationEligibility(True)


def generation_request_for(
    target: GenerationTarget,
    *,
    media_kind: GenerationMediaKind,
    settings: Settings,
) -> GenerationRequest:
    """Derive the one complete request this suggestion authorizes."""
    prompt = PromptModerator().build_prompt(intent=_intent(target))
    if media_kind is GenerationMediaKind.IMAGE:
        return GenerationRequest(
            prompt=prompt,
            media_kind=GenerationMediaKind.IMAGE,
            output_count=1,
            duration_ms=None,
            width=GENERATED_IMAGE_WIDTH,
            height=GENERATED_IMAGE_HEIGHT,
            model_alias=settings.fal_image_model_alias,
            seed=None,
        )
    return GenerationRequest(
        prompt=prompt,
        media_kind=GenerationMediaKind.VIDEO,
        output_count=1,
        duration_ms=settings.generation_max_duration_ms,
        width=GENERATED_VIDEO_WIDTH,
        height=GENERATED_VIDEO_HEIGHT,
        model_alias=_video_model_alias(settings),
        seed=None,
    )


def configured_generation_providers(settings: Settings) -> GenerationProviders:
    """Build the provider selection this deployment's credentials actually support."""
    cache: dict[GenerationMediaKind, GenerativeMediaProvider | None] = {}

    def select(media_kind: GenerationMediaKind) -> GenerativeMediaProvider | None:
        if media_kind not in cache:
            cache[media_kind] = _build_provider(settings, media_kind)
        return cache[media_kind]

    return select


def _build_provider(
    settings: Settings, media_kind: GenerationMediaKind
) -> GenerativeMediaProvider | None:
    """Construct one configured adapter, or report the capability as absent."""
    if media_kind is GenerationMediaKind.VIDEO and settings.generated_video_provider == "runway":
        if (
            settings.runway_api_secret is None
            or settings.runway_video_model_alias is None
            or settings.runway_video_model_id is None
        ):
            return None
        return RunwayGenerativeMediaProvider(
            config=RunwayAdapterConfig(
                api_secret=settings.runway_api_secret,
                video_models={settings.runway_video_model_alias: settings.runway_video_model_id},
                credits_per_second=RUNWAY_CREDITS_PER_SECOND,
                http_timeout_seconds=settings.generation_http_timeout_seconds,
                max_duration_ms=settings.generation_max_duration_ms,
            ),
            client=httpx.Client(),
            utc_clock=_utc_clock,
        )

    if settings.fal_api_key is None or settings.fal_webhook_base_url is None:
        return None
    return FalGenerativeMediaProvider(
        config=FalAdapterConfig(
            api_key=settings.fal_api_key,
            webhook_base_url=settings.fal_webhook_base_url,
            image_models={settings.fal_image_model_alias: settings.fal_image_model_id},
            video_models={settings.fal_video_model_alias: settings.fal_video_model_id},
            http_timeout_seconds=settings.generation_http_timeout_seconds,
            pricing_cache_seconds=settings.generation_estimate_token_ttl_seconds,
            max_duration_ms=settings.generation_max_duration_ms,
        ),
        client=httpx.Client(),
        utc_clock=_utc_clock,
        monotonic=monotonic,
    )


def _utc_clock() -> datetime:
    """Read the wall clock only at the process boundary an adapter is built for."""
    return datetime.now(tz=UTC)


def _video_model_alias(settings: Settings) -> str:
    """Name the video alias whichever configured provider will answer the request."""
    if settings.generated_video_provider == "runway" and settings.runway_video_model_alias:
        return settings.runway_video_model_alias
    return settings.fal_video_model_alias


def _intent(target: GenerationTarget) -> VisualIntent:
    """Rebuild the planner's Visual Intent from the columns that persist it."""
    intent = target.visual_intent
    terms = target.search_terms
    return VisualIntent(
        subject=str(intent["subject"]),
        action=str(intent["action"]),
        setting=str(intent["setting"]),
        mood=str(intent["mood"]),
        search_terms_id=tuple(str(term) for term in terms["id"]),
        search_terms_en=tuple(str(term) for term in terms["en"]),
        portrait_suitable=bool(intent["portrait_suitable"]),
        exclusions=target.exclusions,
        factual_risk_flags=tuple(str(flag) for flag in intent["factual_risk_flags"]),
        confidence=float(intent["confidence"]),
    )
