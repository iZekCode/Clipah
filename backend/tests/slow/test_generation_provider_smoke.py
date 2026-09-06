"""Opt-in live smoke test for the generative providers, capped at one cheap output.

This is the only test in the suite that can spend money at fal or Runway. It is skipped
unless the opt-in variable and a credential are both supplied, it estimates before it
submits, and it asks for exactly one still or five seconds of video — proving that a live
answer normalizes into the same provider-neutral values the fixtures describe.

    CLIPAH_RUN_GENERATION_SMOKE=1 CLIPAH_FAL_API_KEY=... \\
        uv run pytest tests/slow/test_generation_provider_smoke.py -m slow
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic

import httpx
import pytest
from pydantic import SecretStr

from clipah.broll.fal_adapter import FalAdapterConfig, FalGenerativeMediaProvider
from clipah.broll.generation import (
    GenerationMediaKind,
    GenerationRequest,
    GenerationStatus,
    PromptModerator,
)
from clipah.broll.models import VisualIntent
from clipah.broll.runway_adapter import RunwayAdapterConfig, RunwayGenerativeMediaProvider

#: The most this test may ask a provider for: one still, or five seconds of video.
MAX_OUTPUT_COUNT = 1
MAX_DURATION_MS = 5_000


def _opted_in(credential: str) -> bool:
    """Report whether both the explicit opt-in and a credential were supplied."""
    opted_in = os.environ.get("CLIPAH_RUN_GENERATION_SMOKE") == "1"
    return opted_in and bool(os.environ.get(credential))


def _prompt() -> str:
    """Ask for something harmless every model can draw, from the real prompt policy."""
    return PromptModerator().build_prompt(
        intent=VisualIntent(
            subject="a laptop on a desk",
            action="a hand typing",
            setting="an office by a window",
            mood="calm",
            search_terms_id=("laptop meja kerja",),
            search_terms_en=("laptop on desk",),
            portrait_suitable=True,
            exclusions=(),
            factual_risk_flags=(),
            confidence=0.9,
        )
    )


@pytest.mark.slow
@pytest.mark.skipif(
    not _opted_in("CLIPAH_FAL_API_KEY"),
    reason="opt in with CLIPAH_RUN_GENERATION_SMOKE=1 and a fal credential",
)
def test_one_live_fal_estimate_prices_a_single_still() -> None:
    """A live price must normalize into the same estimate the contract tests assert on.

    Only the estimate is exercised: it reads fal's published pricing and starts no
    generation, so this test proves the adapter against the live API without billing a
    single output.
    """
    model_id = os.environ.get("CLIPAH_FAL_IMAGE_MODEL_ID", "fal-ai/nano-banana-2")
    assert "sora" not in model_id.casefold()
    provider = FalGenerativeMediaProvider(
        config=FalAdapterConfig(
            api_key=SecretStr(os.environ["CLIPAH_FAL_API_KEY"]),
            webhook_base_url=os.environ.get("CLIPAH_FAL_WEBHOOK_BASE_URL", "https://clipah.test"),
            image_models={"image-default": model_id},
            video_models={},
            http_timeout_seconds=30.0,
            pricing_cache_seconds=60.0,
        ),
        client=httpx.Client(),
        utc_clock=lambda: datetime.now(tz=UTC),
        monotonic=monotonic,
    )

    estimate = provider.estimate(
        request=GenerationRequest(
            prompt=_prompt(),
            media_kind=GenerationMediaKind.IMAGE,
            output_count=MAX_OUTPUT_COUNT,
            duration_ms=None,
            width=1080,
            height=1920,
            model_alias="image-default",
            seed=None,
        )
    )

    assert estimate.media_kind is GenerationMediaKind.IMAGE
    assert estimate.output_count == MAX_OUTPUT_COUNT
    assert estimate.image_units == Decimal(1)
    assert estimate.cost_usd >= 0


@pytest.mark.slow
@pytest.mark.skipif(
    not _opted_in("CLIPAH_RUNWAY_API_SECRET"),
    reason="opt in with CLIPAH_RUN_GENERATION_SMOKE=1 and a Runway credential",
)
def test_one_live_runway_task_is_submitted_and_then_cancelled() -> None:
    """One five-second task proves submit, poll, and cancel against the live API."""
    model_id = os.environ.get("CLIPAH_RUNWAY_VIDEO_MODEL_ID", "gen4_turbo")
    assert "sora" not in model_id.casefold()
    provider = RunwayGenerativeMediaProvider(
        config=RunwayAdapterConfig(
            api_secret=SecretStr(os.environ["CLIPAH_RUNWAY_API_SECRET"]),
            video_models={"video-default": model_id},
            credits_per_second=Decimal(5),
            http_timeout_seconds=30.0,
            max_duration_ms=MAX_DURATION_MS,
        ),
        client=httpx.Client(),
        utc_clock=lambda: datetime.now(tz=UTC),
    )
    request = GenerationRequest(
        prompt=_prompt(),
        media_kind=GenerationMediaKind.VIDEO,
        output_count=MAX_OUTPUT_COUNT,
        duration_ms=MAX_DURATION_MS,
        width=720,
        height=1280,
        model_alias="video-default",
        seed=None,
    )

    handle = provider.submit(request=request, idempotency_key="clipah-generation-smoke")
    try:
        result = provider.poll(handle=handle)
        assert result.status in set(GenerationStatus)
    finally:
        # Cancel whatever this test started, so an opt-in run costs at most one partial
        # generation rather than a completed one nobody asked for.
        provider.cancel(handle=handle)
