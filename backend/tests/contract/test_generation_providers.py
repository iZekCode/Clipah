"""Provider-neutral generated-media contracts exercised without network or billing."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from clipah.broll.fal_adapter import FalAdapterConfig, FalGenerativeMediaProvider
from clipah.broll.generation import (
    GENERATION_CIRCUIT_OPEN_CODE,
    GENERATION_MODERATION_REJECTED_CODE,
    GENERATION_UNKNOWN_MODEL_CODE,
    CircuitBreaker,
    CircuitState,
    FakeGenerativeMediaProvider,
    GenerationCircuitOpenError,
    GenerationEstimate,
    GenerationHandle,
    GenerationInvalidResponseError,
    GenerationLatencyClass,
    GenerationMediaKind,
    GenerationModerationResult,
    GenerationOutput,
    GenerationRateLimitedError,
    GenerationRejectedError,
    GenerationRequest,
    GenerationResult,
    GenerationStatus,
    GenerationTimeoutError,
    GenerationUnavailableError,
    GenerationUnknownModelError,
    GenerationUsage,
    GenerativeMediaProvider,
    PromptModerator,
    PromptRejectedError,
)
from clipah.broll.models import VisualIntent
from clipah.broll.runway_adapter import RunwayAdapterConfig, RunwayGenerativeMediaProvider

NOW = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)


def _fal_config() -> FalAdapterConfig:
    """Build a complete fal boundary configuration without environment access."""
    return FalAdapterConfig(
        api_key=SecretStr("fal-secret-key"),
        webhook_base_url="https://clipah.example",
        image_models={"image-default": "fal-ai/nano-banana-2"},
        video_models={"video-default": "fal-ai/kling-video/v2.6/pro/text-to-video"},
        http_timeout_seconds=12.0,
        pricing_cache_seconds=60.0,
    )


def _fal_handle(**overrides: Any) -> GenerationHandle:
    """Build a sanitized handle whose URLs must be reconstructed by the adapter."""
    values: dict[str, Any] = {
        "provider": "fal",
        "provider_request_id": "fal-request",
        "model": "fal-ai/nano-banana-2",
        "model_version": "configured",
        "submitted_at": NOW,
    }
    values.update(overrides)
    return GenerationHandle(**values)


def _intent(**overrides: Any) -> VisualIntent:
    """Build one complete visual intent with independently chosen fixture values."""
    values: dict[str, Any] = {
        "subject": "a shortened signup form",
        "action": "a hand deleting form fields",
        "setting": "a laptop screen on a desk",
        "mood": "focused",
        "search_terms_id": ("formulir singkat", "hapus kolom"),
        "search_terms_en": ("short signup form", "remove fields"),
        "portrait_suitable": True,
        "exclusions": ("logos", "readable personal data"),
        "factual_risk_flags": ("avoid claims about a named company",),
        "confidence": 0.9,
    }
    values.update(overrides)
    return VisualIntent(**values)


def _image_request(**overrides: Any) -> GenerationRequest:
    """Build one valid server-owned still request."""
    values: dict[str, Any] = {
        "prompt": PromptModerator().build_prompt(intent=_intent()),
        "media_kind": GenerationMediaKind.IMAGE,
        "output_count": 1,
        "duration_ms": None,
        "width": 1080,
        "height": 1920,
        "model_alias": "image-default",
        "seed": 7,
    }
    values.update(overrides)
    return GenerationRequest(**values)


def _successful_image_result(*, cost_usd: Decimal) -> GenerationResult:
    """Build one complete successful result with affirmative safety evidence."""
    return GenerationResult(
        status=GenerationStatus.SUCCEEDED,
        output=GenerationOutput(
            url=SecretStr("https://provider.invalid/ephemeral-output"),
            content_type="image/png",
            width=1080,
            height=1920,
            duration_ms=None,
        ),
        usage=GenerationUsage(
            generated_images=1,
            generated_videos=0,
            generated_seconds=Decimal(0),
            provider_credits=Decimal("8"),
            cost_usd=cost_usd,
        ),
        seed=7,
        moderation=GenerationModerationResult.APPROVED,
    )


@pytest.mark.unit
def test_provider_contract_normalizes_estimate_and_actual_usage() -> None:
    """Provider-specific prices must become the one quota and USD vocabulary callers use."""
    request = _image_request()
    provider = FakeGenerativeMediaProvider(
        estimate=GenerationEstimate.image(
            output_count=1,
            width=1080,
            height=1920,
            latency_class=GenerationLatencyClass.STANDARD,
            provider_credits=Decimal("8"),
            cost_usd=Decimal("0.08"),
        ),
        result=_successful_image_result(cost_usd=Decimal("0.08")),
        utc_clock=lambda: NOW,
    )

    assert provider.estimate(request=request).image_units == Decimal(1)
    handle = provider.submit(request=request, idempotency_key="job:1")
    assert provider.poll(handle=handle).usage.cost_usd == Decimal("0.08")
    assert isinstance(provider, GenerativeMediaProvider)


@pytest.mark.unit
def test_generation_values_are_closed_and_frozen() -> None:
    """Unknown input and post-validation mutation must never alter a billable request."""
    with pytest.raises(ValidationError):
        GenerationRequest(**{**_image_request().model_dump(), "unexpected": "x"})

    request = _image_request()
    with pytest.raises(ValidationError):
        request.width = 720  # type: ignore[misc]


@pytest.mark.unit
def test_generation_values_reject_enum_and_datetime_string_coercion() -> None:
    """Provider payload strings must be decoded explicitly instead of silently coerced."""
    request_values = _image_request().model_dump(mode="python")
    request_values["media_kind"] = "image"
    with pytest.raises(ValidationError):
        GenerationRequest.model_validate(request_values)

    with pytest.raises(ValidationError):
        GenerationHandle.model_validate(
            {
                "provider": "fake",
                "provider_request_id": "request-1",
                "model": "fake/image",
                "model_version": "1",
                "submitted_at": "2026-09-06T08:00:00Z",
            }
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides",
    (
        {"width": 0},
        {"height": -1},
        {"output_count": 0},
        {"media_kind": GenerationMediaKind.IMAGE, "duration_ms": 5_000},
        {"media_kind": GenerationMediaKind.VIDEO, "duration_ms": None},
        {"media_kind": GenerationMediaKind.VIDEO, "duration_ms": 0},
    ),
)
def test_generation_request_rejects_invalid_dimensions_counts_and_durations(
    overrides: dict[str, Any],
) -> None:
    """Malformed geometry must fail before an adapter can create a billable request."""
    with pytest.raises(ValidationError):
        GenerationRequest(**{**_image_request().model_dump(), **overrides})


@pytest.mark.unit
@pytest.mark.parametrize("value", (Decimal("-0.01"), Decimal("NaN"), Decimal("Infinity")))
def test_estimates_reject_negative_or_non_finite_cost(value: Decimal) -> None:
    """An unusable estimate cannot be displayed or reserved as if it were real money."""
    with pytest.raises(ValidationError):
        GenerationEstimate.image(
            output_count=1,
            width=1080,
            height=1920,
            latency_class=GenerationLatencyClass.STANDARD,
            provider_credits=Decimal("8"),
            cost_usd=value,
        )


@pytest.mark.unit
@pytest.mark.parametrize("field", ("generated_seconds", "provider_credits", "cost_usd"))
@pytest.mark.parametrize("value", (Decimal("-1"), Decimal("NaN"), Decimal("Infinity")))
def test_usage_rejects_negative_or_non_finite_amounts(field: str, value: Decimal) -> None:
    """A malformed provider counter cannot corrupt quota settlement or reporting."""
    values: dict[str, Any] = {
        "generated_images": 1,
        "generated_videos": 0,
        "generated_seconds": Decimal(0),
        "provider_credits": Decimal("8"),
        "cost_usd": Decimal("0.08"),
    }
    values[field] = value

    with pytest.raises(ValidationError):
        GenerationUsage(**values)


@pytest.mark.unit
def test_video_estimate_normalizes_output_and_duration_units() -> None:
    """Video admission must reserve both one output and every generated second."""
    estimate = GenerationEstimate.video(
        output_count=2,
        duration_ms=5_000,
        width=1080,
        height=1920,
        latency_class=GenerationLatencyClass.SLOW,
        provider_credits=Decimal("250"),
        cost_usd=Decimal("2.50"),
    )

    assert estimate.image_units == Decimal(0)
    assert estimate.video_units == Decimal(2)
    assert estimate.generated_seconds == Decimal(10)


@pytest.mark.unit
@pytest.mark.parametrize(
    "submitted_at",
    (
        datetime(2026, 9, 6, 8, 0),
        datetime(2026, 9, 6, 8, 0, tzinfo=timezone(timedelta(hours=7))),
    ),
)
def test_generation_handle_accepts_only_timezone_aware_utc_instants(
    submitted_at: datetime,
) -> None:
    """Persisted provider handles need one unambiguous UTC replay instant."""
    values = {
        "provider": "fake",
        "provider_request_id": "request-1",
        "model": "fake/image",
        "model_version": "1",
        "submitted_at": submitted_at,
    }
    with pytest.raises(ValidationError):
        GenerationHandle(**values)


@pytest.mark.unit
def test_fake_provider_rejects_an_unknown_model_before_submission() -> None:
    """A typo or arbitrary provider ID must never become a provider call."""
    provider = FakeGenerativeMediaProvider(
        estimate=GenerationEstimate.image(
            output_count=1,
            width=1080,
            height=1920,
            latency_class=GenerationLatencyClass.STANDARD,
            provider_credits=Decimal(1),
            cost_usd=Decimal("0.01"),
        ),
        result=_successful_image_result(cost_usd=Decimal("0.01")),
        utc_clock=lambda: NOW,
    )

    with pytest.raises(GenerationUnknownModelError) as raised:
        provider.submit(
            request=_image_request(model_alias="provider/arbitrary-model"),
            idempotency_key="job:1",
        )

    assert raised.value.code == GENERATION_UNKNOWN_MODEL_CODE
    assert provider.submit_count == 0


@pytest.mark.unit
def test_fake_provider_reuses_the_handle_for_an_idempotent_retry() -> None:
    """A redelivered Job must not create a second billable provider request."""
    provider = FakeGenerativeMediaProvider(
        estimate=GenerationEstimate.image(
            output_count=1,
            width=1080,
            height=1920,
            latency_class=GenerationLatencyClass.STANDARD,
            provider_credits=Decimal(1),
            cost_usd=Decimal("0.01"),
        ),
        result=_successful_image_result(cost_usd=Decimal("0.01")),
        utc_clock=lambda: NOW,
    )
    request = _image_request()

    first = provider.submit(request=request, idempotency_key="job:1")
    second = provider.submit(request=request, idempotency_key="job:1")

    assert second == first
    assert provider.submit_count == 1


@pytest.mark.unit
def test_fake_provider_cancellation_is_idempotent_and_terminal() -> None:
    """Repeated cancellation must neither fail nor allow a late success to escape."""
    provider = FakeGenerativeMediaProvider(
        estimate=GenerationEstimate.image(
            output_count=1,
            width=1080,
            height=1920,
            latency_class=GenerationLatencyClass.STANDARD,
            provider_credits=Decimal(1),
            cost_usd=Decimal("0.01"),
        ),
        result=_successful_image_result(cost_usd=Decimal("0.01")),
        utc_clock=lambda: NOW,
    )
    handle = provider.submit(request=_image_request(), idempotency_key="job:1")

    provider.cancel(handle=handle)
    provider.cancel(handle=handle)

    assert provider.cancel_count == 1
    assert provider.poll(handle=handle).status is GenerationStatus.CANCELED


@pytest.mark.unit
def test_prompt_is_deterministic_and_carries_exclusions_and_factual_risks() -> None:
    """Replays need the same safe prompt, including constraints the image must not erase."""
    moderator = PromptModerator()

    prompt = moderator.build_prompt(intent=_intent())

    assert prompt == (
        "Subject: a shortened signup form.\n"
        "Action: a hand deleting form fields.\n"
        "Setting: a laptop screen on a desk.\n"
        "Mood: focused.\n"
        "Composition: portrait-suitable.\n"
        "Indonesian context: formulir singkat; hapus kolom.\n"
        "English context: short signup form; remove fields.\n"
        "Exclude: logos; readable personal data.\n"
        "Factual-risk constraints: avoid claims about a named company."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "safety_class",
    (
        "impersonation",
        "sexual_content_involving_minors",
        "deceptive_real_person_claims",
    ),
)
def test_prompt_moderator_rejects_each_prohibited_safety_class(safety_class: str) -> None:
    """A prohibited intent must stop locally instead of reaching a generation provider."""
    with pytest.raises(PromptRejectedError) as raised:
        PromptModerator().build_prompt(intent=_intent(factual_risk_flags=(safety_class,)))

    assert raised.value.code == GENERATION_MODERATION_REJECTED_CODE


@pytest.mark.unit
def test_circuit_opens_at_threshold_and_refuses_calls_during_cooldown() -> None:
    """Consecutive provider outages must stop a worker from amplifying the outage."""
    tick = [10.0]
    circuit = CircuitBreaker(failure_threshold=2, cooldown_seconds=30, monotonic=lambda: tick[0])

    circuit.before_call()
    circuit.record_failure()
    circuit.before_call()
    circuit.record_failure()

    assert circuit.state is CircuitState.OPEN
    with pytest.raises(GenerationCircuitOpenError) as raised:
        circuit.before_call()
    assert raised.value.code == GENERATION_CIRCUIT_OPEN_CODE


@pytest.mark.unit
def test_circuit_allows_one_half_open_probe_and_reopens_after_probe_failure() -> None:
    """A provider in recovery must receive one trial, not a fresh burst of requests."""
    tick = [10.0]
    circuit = CircuitBreaker(failure_threshold=1, cooldown_seconds=30, monotonic=lambda: tick[0])
    circuit.before_call()
    circuit.record_failure()
    tick[0] = 40.0

    circuit.before_call()
    assert circuit.state is CircuitState.HALF_OPEN
    with pytest.raises(GenerationCircuitOpenError):
        circuit.before_call()

    circuit.record_failure()
    assert circuit.state is CircuitState.OPEN
    tick[0] = 69.9
    with pytest.raises(GenerationCircuitOpenError):
        circuit.before_call()


@pytest.mark.unit
def test_successful_half_open_probe_closes_and_resets_the_circuit() -> None:
    """A recovered provider must resume normal traffic without stale failure counts."""
    tick = [10.0]
    circuit = CircuitBreaker(failure_threshold=1, cooldown_seconds=30, monotonic=lambda: tick[0])
    circuit.before_call()
    circuit.record_failure()
    tick[0] = 40.0
    circuit.before_call()

    circuit.record_success()

    assert circuit.state is CircuitState.CLOSED
    circuit.before_call()


@pytest.mark.unit
def test_fal_estimate_uses_current_unit_price_and_a_bounded_cache() -> None:
    """Displayed cost must use current fal pricing without fetching it for every estimate."""
    tick = [10.0]
    pricing_calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        pricing_calls.append(request)
        return httpx.Response(
            200,
            json={
                "prices": [
                    {
                        "endpoint_id": "fal-ai/nano-banana-2",
                        "unit_price": 0.08,
                        "unit": "image",
                        "currency": "USD",
                    }
                ],
                "next_cursor": None,
                "has_more": False,
            },
        )

    provider = FalGenerativeMediaProvider(
        config=_fal_config(),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        utc_clock=lambda: NOW,
        monotonic=lambda: tick[0],
    )

    first = provider.estimate(request=_image_request())
    second = provider.estimate(request=_image_request())
    tick[0] = 71.0
    third = provider.estimate(request=_image_request())

    assert first.cost_usd == second.cost_usd == third.cost_usd == Decimal("0.08")
    assert first.provider_credits == Decimal(1)
    assert len(pricing_calls) == 2
    assert pricing_calls[0].url == (
        "https://api.fal.ai/v1/models/pricing?endpoint_id=fal-ai%2Fnano-banana-2"
    )
    assert pricing_calls[0].headers["Authorization"] == "Key fal-secret-key"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("unit", "expected_cost"),
    (("second", Decimal("0.50")), ("video", Decimal("0.10"))),
)
def test_fal_video_estimate_normalizes_supported_billing_units(
    unit: str, expected_cost: Decimal
) -> None:
    """fal per-second and per-video prices must become the same visible estimate shape."""

    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "prices": [
                    {
                        "endpoint_id": "fal-ai/kling-video/v2.6/pro/text-to-video",
                        "unit_price": 0.1,
                        "unit": unit,
                        "currency": "USD",
                    }
                ],
                "next_cursor": None,
                "has_more": False,
            },
        )

    provider = FalGenerativeMediaProvider(
        config=_fal_config(),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        utc_clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )
    request = _image_request(
        media_kind=GenerationMediaKind.VIDEO,
        duration_ms=5_000,
        model_alias="video-default",
    )

    estimate = provider.estimate(request=request)

    assert estimate.cost_usd == expected_cost
    assert estimate.video_units == Decimal(1)
    assert estimate.generated_seconds == Decimal(5)


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    (
        {"prices": []},
        {
            "prices": [
                {
                    "endpoint_id": "fal-ai/nano-banana-2",
                    "unit_price": -1,
                    "unit": "image",
                    "currency": "USD",
                }
            ]
        },
        {
            "prices": [
                {
                    "endpoint_id": "fal-ai/nano-banana-2",
                    "unit_price": 0.08,
                    "unit": "request",
                    "currency": "USD",
                }
            ]
        },
    ),
)
def test_fal_estimate_fails_closed_for_unusable_pricing(payload: dict[str, Any]) -> None:
    """A missing, negative, or unsupported price must never become a guessed estimate."""
    provider = FalGenerativeMediaProvider(
        config=_fal_config(),
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ),
        utc_clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    with pytest.raises(GenerationInvalidResponseError):
        provider.estimate(request=_image_request())


@pytest.mark.unit
def test_fal_submit_uses_queue_headers_and_returns_only_an_opaque_identity() -> None:
    """Queue convenience URLs and credentials must not escape the adapter boundary."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "request_id": "fal-request",
                "response_url": "https://secret.example/response?token=provider-secret",
                "status_url": "https://secret.example/status?token=provider-secret",
                "cancel_url": "https://secret.example/cancel?token=provider-secret",
            },
        )

    provider = FalGenerativeMediaProvider(
        config=_fal_config(),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        utc_clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    handle = provider.submit(request=_image_request(), idempotency_key="job-id")

    assert handle == _fal_handle()
    assert requests[0].method == "POST"
    assert requests[0].url == (
        "https://queue.fal.run/fal-ai/nano-banana-2"
        "?fal_webhook=https%3A%2F%2Fclipah.example%2Fapi%2Fv1%2Fwebhooks%2Fgeneration%2Ffal"
    )
    assert requests[0].headers["Authorization"] == "Key fal-secret-key"
    assert requests[0].headers["X-Fal-No-Retry"] == "1"
    assert requests[0].headers["X-Fal-Request-Timeout"] == "12"
    assert requests[0].headers["Idempotency-Key"] == "job-id"
    assert json.loads(requests[0].content) == {
        "prompt": _image_request().prompt,
        "num_images": 1,
        "aspect_ratio": "9:16",
        "output_format": "png",
        "safety_tolerance": "1",
        "limit_generations": True,
        "enable_web_search": False,
        "seed": 7,
    }
    exposed = repr(handle)
    assert "secret.example" not in exposed
    assert "provider-secret" not in exposed
    assert "fal-secret-key" not in exposed


@pytest.mark.unit
def test_fal_video_submit_uses_supported_geometry_and_disables_generated_audio() -> None:
    """The configured Kling request must express portrait geometry and never request audio."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"request_id": "fal-video-request"})

    provider = FalGenerativeMediaProvider(
        config=_fal_config(),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        utc_clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )
    request = _image_request(
        media_kind=GenerationMediaKind.VIDEO,
        duration_ms=5_000,
        model_alias="video-default",
        seed=None,
    )

    provider.submit(request=request, idempotency_key="video-job")

    assert json.loads(requests[0].content) == {
        "prompt": request.prompt,
        "duration": "5",
        "aspect_ratio": "9:16",
        "generate_audio": False,
    }


@pytest.mark.unit
def test_fal_submit_is_idempotent_for_the_same_key_and_request() -> None:
    """A local retry must reuse its handle rather than issue a second billable submission."""
    calls = [0]

    def respond(_: httpx.Request) -> httpx.Response:
        calls[0] += 1
        return httpx.Response(200, json={"request_id": "fal-request"})

    provider = FalGenerativeMediaProvider(
        config=_fal_config(),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        utc_clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )
    request = _image_request()

    first = provider.submit(request=request, idempotency_key="job-id")
    second = provider.submit(request=request, idempotency_key="job-id")

    assert second == first
    assert calls[0] == 1
    with pytest.raises(GenerationRejectedError):
        provider.submit(request=_image_request(seed=8), idempotency_key="job-id")
    assert calls[0] == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    "generation_request",
    (
        _image_request(model_alias="unknown"),
        _image_request(prompt="deceptive_real_person_claims"),
    ),
)
def test_fal_rejects_unknown_models_and_unsafe_prompts_before_http(
    generation_request: GenerationRequest,
) -> None:
    """Neither arbitrary endpoints nor prohibited prompts may reach fal."""
    calls = [0]

    def respond(_: httpx.Request) -> httpx.Response:
        calls[0] += 1
        return httpx.Response(500)

    provider = FalGenerativeMediaProvider(
        config=_fal_config(),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        utc_clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    with pytest.raises((GenerationUnknownModelError, PromptRejectedError)):
        provider.submit(request=generation_request, idempotency_key="job-id")
    assert calls[0] == 0


@pytest.mark.unit
def test_fal_configuration_rejects_sora_aliases_and_model_ids() -> None:
    """No configuration path may make a retired Sora endpoint billable."""
    with pytest.raises(ValueError):
        FalAdapterConfig(
            api_key=SecretStr("key"),
            webhook_base_url="https://clipah.example",
            image_models={"Sora-image": "fal-ai/safe-image"},
            video_models={},
            http_timeout_seconds=10,
            pricing_cache_seconds=60,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("fal_status", "expected"),
    (("IN_QUEUE", GenerationStatus.QUEUED), ("IN_PROGRESS", GenerationStatus.RUNNING)),
)
def test_fal_poll_normalizes_nonterminal_queue_statuses(
    fal_status: str, expected: GenerationStatus
) -> None:
    """Workers need stable queued and running states without receiving fal logs or URLs."""
    provider = FalGenerativeMediaProvider(
        config=_fal_config(),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "status": fal_status,
                        "request_id": "fal-request",
                        "response_url": "https://secret.example/result",
                        "logs": [{"message": "credential=provider-secret"}],
                    },
                )
            )
        ),
        utc_clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    result = provider.poll(handle=_fal_handle())

    assert result.status is expected
    assert result.usage == GenerationUsage.zero()
    assert "secret.example" not in repr(result)
    assert "provider-secret" not in repr(result)


@pytest.mark.unit
def test_fal_poll_decodes_success_usage_and_moderation_without_exposing_urls() -> None:
    """Only the secret output capability and normalized usage may cross the adapter boundary."""
    responses = iter(
        (
            httpx.Response(200, json={"status": "COMPLETED", "request_id": "fal-request"}),
            httpx.Response(
                200,
                headers={"X-Fal-Billable-Units": "1"},
                json={
                    "images": [
                        {
                            "url": "https://secret.example/output.png?token=provider-secret",
                            "width": 1080,
                            "height": 1920,
                            "content_type": "image/png",
                        }
                    ],
                    "seed": 7,
                    "has_nsfw_concepts": [False],
                },
            ),
            httpx.Response(
                200,
                json={
                    "prices": [
                        {
                            "endpoint_id": "fal-ai/nano-banana-2",
                            "unit_price": 0.08,
                            "unit": "image",
                            "currency": "USD",
                        }
                    ],
                    "next_cursor": None,
                    "has_more": False,
                },
            ),
        )
    )
    provider = FalGenerativeMediaProvider(
        config=_fal_config(),
        client=httpx.Client(transport=httpx.MockTransport(lambda _: next(responses))),
        utc_clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    result = provider.poll(handle=_fal_handle())

    assert result.status is GenerationStatus.SUCCEEDED
    assert result.moderation is GenerationModerationResult.APPROVED
    assert result.usage.provider_credits == Decimal(1)
    assert result.usage.cost_usd == Decimal("0.08")
    assert result.output is not None
    assert result.output.url.get_secret_value().startswith("https://secret.example/")
    assert "secret.example" not in repr(result)
    assert "provider-secret" not in repr(result)


@pytest.mark.unit
def test_fal_poll_maps_provider_rejection_without_returning_error_text() -> None:
    """Provider error details must become a fixed terminal state, never durable text."""
    provider = FalGenerativeMediaProvider(
        config=_fal_config(),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "status": "COMPLETED",
                        "request_id": "fal-request",
                        "error_type": "validation_error",
                        "error": "provider key secret and private URL https://secret.example",
                    },
                )
            )
        ),
        utc_clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    result = provider.poll(handle=_fal_handle())

    assert result.status is GenerationStatus.REJECTED
    assert result.output is None
    assert "secret.example" not in repr(result)
    assert "provider key secret" not in repr(result)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("safety", "expected"),
    (([True], GenerationModerationResult.OUTPUT_REJECTED), (None, None)),
)
def test_fal_poll_rejects_unsafe_or_missing_moderation_evidence(
    safety: list[bool] | None, expected: GenerationModerationResult | None
) -> None:
    """A success cannot create media unless fal supplied affirmative moderation evidence."""
    payload: dict[str, Any] = {
        "images": [
            {
                "url": "https://secret.example/output.png",
                "width": 1080,
                "height": 1920,
                "content_type": "image/png",
            }
        ],
        "seed": 7,
    }
    if safety is not None:
        payload["has_nsfw_concepts"] = safety
    responses = iter(
        (
            httpx.Response(200, json={"status": "COMPLETED", "request_id": "fal-request"}),
            httpx.Response(200, json=payload),
        )
    )
    provider = FalGenerativeMediaProvider(
        config=_fal_config(),
        client=httpx.Client(transport=httpx.MockTransport(lambda _: next(responses))),
        utc_clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    if expected is None:
        with pytest.raises(GenerationInvalidResponseError):
            provider.poll(handle=_fal_handle())
    else:
        result = provider.poll(handle=_fal_handle())
        assert result.status is GenerationStatus.MODERATION_REJECTED
        assert result.moderation is expected
        assert result.output is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("outcome", "error_type"),
    (
        (httpx.ReadTimeout("slow"), GenerationTimeoutError),
        (httpx.Response(429), GenerationRateLimitedError),
        (httpx.Response(504), GenerationTimeoutError),
    ),
)
def test_fal_http_timeouts_and_throttling_use_retryable_fixed_errors(
    outcome: httpx.Response | Exception, error_type: type[Exception]
) -> None:
    """Workers must retry transient fal failures without persisting provider messages."""

    def respond(_: httpx.Request) -> httpx.Response:
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    provider = FalGenerativeMediaProvider(
        config=_fal_config(),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        utc_clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    with pytest.raises(error_type) as raised:
        provider.poll(handle=_fal_handle())
    assert "slow" not in str(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.unit
def test_fal_cancel_reconstructs_the_url_and_is_idempotent() -> None:
    """Repeated cancellation must not repeat provider traffic or require stored cancel URLs."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(202, json={"status": "CANCELLATION_REQUESTED"})

    provider = FalGenerativeMediaProvider(
        config=_fal_config(),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        utc_clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )
    handle = _fal_handle()

    provider.cancel(handle=handle)
    provider.cancel(handle=handle)

    assert len(requests) == 1
    assert requests[0].method == "PUT"
    assert requests[0].url == (
        "https://queue.fal.run/fal-ai/nano-banana-2/requests/fal-request/cancel"
    )


def _runway_config(**overrides: Any) -> RunwayAdapterConfig:
    """Build a complete Runway boundary configuration without environment access."""
    values: dict[str, Any] = {
        "api_secret": SecretStr("runway-secret-key"),
        "video_models": {"video-default": "gen4_turbo"},
        "credits_per_second": Decimal(5),
        "http_timeout_seconds": 12.0,
        "max_duration_ms": 5_000,
    }
    values.update(overrides)
    return RunwayAdapterConfig(**values)


def _video_request(**overrides: Any) -> GenerationRequest:
    """Build one valid server-owned five-second portrait video request."""
    values: dict[str, Any] = {
        "media_kind": GenerationMediaKind.VIDEO,
        "duration_ms": 5_000,
        "width": 720,
        "height": 1280,
        "model_alias": "video-default",
        "seed": None,
    }
    values.update(overrides)
    return _image_request(**values)


def _runway_provider(
    respond: Callable[[httpx.Request], httpx.Response],
    **overrides: Any,
) -> RunwayGenerativeMediaProvider:
    """Build a Runway adapter over a local transport and pinned clock."""
    return RunwayGenerativeMediaProvider(
        config=_runway_config(**overrides),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        utc_clock=lambda: NOW,
    )


def _runway_handle(**overrides: Any) -> GenerationHandle:
    """Build the handle Runway issues, carrying the geometry its task payload omits."""
    values: dict[str, Any] = {
        "provider": "runway",
        "provider_request_id": "runway-task",
        "model": "gen4_turbo",
        "model_version": "2024-11-06",
        "submitted_at": NOW,
        "output_width": 720,
        "output_height": 1280,
        "output_duration_ms": 5_000,
    }
    values.update(overrides)
    return GenerationHandle(**values)


@pytest.mark.unit
def test_runway_refuses_still_requests_before_any_provider_call() -> None:
    """The Runway adapter exists for video only; a still must never reach its endpoint."""
    calls = [0]

    def respond(_: httpx.Request) -> httpx.Response:
        calls[0] += 1
        return httpx.Response(200, json={"id": "runway-task"})

    provider = _runway_provider(respond)

    with pytest.raises(GenerationRejectedError):
        provider.submit(request=_image_request(), idempotency_key="job-id")
    with pytest.raises(GenerationRejectedError):
        provider.estimate(request=_image_request())
    assert calls[0] == 0


@pytest.mark.unit
def test_runway_estimate_normalizes_credits_at_one_cent_without_a_provider_call() -> None:
    """A shown price must come from the configured credit rate, not a billable probe."""
    calls = [0]

    def respond(_: httpx.Request) -> httpx.Response:
        calls[0] += 1
        return httpx.Response(500)

    estimate = _runway_provider(respond).estimate(request=_video_request())

    assert estimate.media_kind is GenerationMediaKind.VIDEO
    assert estimate.generated_seconds == Decimal(5)
    assert estimate.provider_credits == Decimal(25)
    assert estimate.cost_usd == Decimal("0.25")
    assert estimate.latency_class is GenerationLatencyClass.SLOW
    assert calls[0] == 0


@pytest.mark.unit
def test_runway_submit_sends_the_pinned_api_version_and_retains_only_the_task_id() -> None:
    """Runway pins behavior to a dated version, and only its task identity may persist."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json={"id": "runway-task", "href": "https://secret.example/tasks/runway-task"}
        )

    handle = _runway_provider(respond).submit(request=_video_request(), idempotency_key="job-id")

    assert handle.provider == "runway"
    assert handle.provider_request_id == "runway-task"
    assert handle.model_version == "2024-11-06"
    assert handle.output_duration_ms == 5_000
    assert "secret.example" not in repr(handle)
    assert requests[0].method == "POST"
    assert requests[0].url == "https://api.dev.runwayml.com/v1/image_to_video"
    assert requests[0].headers["X-Runway-Version"] == "2024-11-06"
    assert json.loads(requests[0].content) == {
        "model": "gen4_turbo",
        "promptText": _video_request().prompt,
        "ratio": "720:1280",
        "duration": 5,
    }


@pytest.mark.unit
def test_runway_submit_is_idempotent_for_the_same_key_and_request() -> None:
    """A local retry must reuse its task rather than start a second billable generation."""
    calls = [0]

    def respond(_: httpx.Request) -> httpx.Response:
        calls[0] += 1
        return httpx.Response(200, json={"id": "runway-task"})

    provider = _runway_provider(respond)
    request = _video_request()

    first = provider.submit(request=request, idempotency_key="job-id")
    second = provider.submit(request=request, idempotency_key="job-id")

    assert second == first
    assert calls[0] == 1
    with pytest.raises(GenerationRejectedError):
        provider.submit(request=_video_request(duration_ms=10_000), idempotency_key="job-id")
    assert calls[0] == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    "generation_request",
    (
        _video_request(model_alias="unknown"),
        _video_request(prompt="impersonation of a public figure"),
        _video_request(duration_ms=10_000),
        _video_request(width=999, height=1280),
    ),
)
def test_runway_rejects_unsafe_models_prompts_geometry_and_duration_before_http(
    generation_request: GenerationRequest,
) -> None:
    """Nothing outside the configured allowlist and bounds may become a Runway task."""
    calls = [0]

    def respond(_: httpx.Request) -> httpx.Response:
        calls[0] += 1
        return httpx.Response(200, json={"id": "runway-task"})

    provider = _runway_provider(respond)

    refusals = (GenerationUnknownModelError, PromptRejectedError, GenerationRejectedError)
    with pytest.raises(refusals):
        provider.submit(request=generation_request, idempotency_key="job-id")
    assert calls[0] == 0


@pytest.mark.unit
def test_runway_configuration_rejects_sora_aliases_and_model_ids() -> None:
    """No configuration path may make a retired Sora model billable through Runway."""
    with pytest.raises(ValueError):
        _runway_config(video_models={"video-default": "sora-2"})


@pytest.mark.unit
@pytest.mark.parametrize(
    ("runway_status", "expected"),
    (
        ("PENDING", GenerationStatus.QUEUED),
        ("THROTTLED", GenerationStatus.QUEUED),
        ("RUNNING", GenerationStatus.RUNNING),
    ),
)
def test_runway_poll_normalizes_nonterminal_task_statuses(
    runway_status: str, expected: GenerationStatus
) -> None:
    """A waiting task must report a stable state and no fabricated usage."""
    provider = _runway_provider(
        lambda _: httpx.Response(
            200,
            json={
                "id": "runway-task",
                "status": runway_status,
                "progress": 0.4,
                "failure": "credential=provider-secret",
            },
        )
    )

    result = provider.poll(handle=_runway_handle())

    assert result.status is expected
    assert result.usage == GenerationUsage.zero()
    assert result.output is None
    assert "provider-secret" not in repr(result)


@pytest.mark.unit
def test_runway_poll_decodes_success_usage_without_exposing_the_output_url() -> None:
    """Only the secret output capability and credit-derived cost may cross the boundary."""
    provider = _runway_provider(
        lambda _: httpx.Response(
            200,
            json={
                "id": "runway-task",
                "status": "SUCCEEDED",
                "output": ["https://secret.example/output.mp4?token=provider-secret"],
            },
        )
    )

    result = provider.poll(handle=_runway_handle())

    assert result.status is GenerationStatus.SUCCEEDED
    assert result.moderation is GenerationModerationResult.APPROVED
    assert result.usage.generated_videos == 1
    assert result.usage.generated_seconds == Decimal(5)
    assert result.usage.provider_credits == Decimal(25)
    assert result.usage.cost_usd == Decimal("0.25")
    assert result.output is not None
    assert result.output.url.get_secret_value().startswith("https://secret.example/")
    assert result.output.duration_ms == 5_000
    assert "secret.example" not in repr(result)
    assert "provider-secret" not in repr(result)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("failure_code", "expected_status", "expected_moderation"),
    (
        (
            "SAFETY.INPUT.TEXT",
            GenerationStatus.MODERATION_REJECTED,
            GenerationModerationResult.INPUT_REJECTED,
        ),
        (
            "SAFETY.OUTPUT.VIDEO",
            GenerationStatus.MODERATION_REJECTED,
            GenerationModerationResult.OUTPUT_REJECTED,
        ),
        (
            "INTERNAL.BAD_OUTPUT",
            GenerationStatus.FAILED,
            GenerationModerationResult.PENDING,
        ),
    ),
)
def test_runway_poll_normalizes_failure_codes_without_returning_failure_text(
    failure_code: str,
    expected_status: GenerationStatus,
    expected_moderation: GenerationModerationResult,
) -> None:
    """Safety refusals must be distinguishable from faults while text stays with Runway."""
    provider = _runway_provider(
        lambda _: httpx.Response(
            200,
            json={
                "id": "runway-task",
                "status": "FAILED",
                "failureCode": failure_code,
                "failure": "provider key secret and private URL https://secret.example",
            },
        )
    )

    result = provider.poll(handle=_runway_handle())

    assert result.status is expected_status
    assert result.moderation is expected_moderation
    assert result.output is None
    assert "secret.example" not in repr(result)
    assert "provider key secret" not in repr(result)


@pytest.mark.unit
def test_runway_poll_reports_a_canceled_task_as_canceled() -> None:
    """A task Clipah or Runway stopped is terminal without being a failure to retry."""
    provider = _runway_provider(
        lambda _: httpx.Response(200, json={"id": "runway-task", "status": "CANCELLED"})
    )

    result = provider.poll(handle=_runway_handle())

    assert result.status is GenerationStatus.CANCELED
    assert result.usage == GenerationUsage.zero()


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    (
        {"id": "runway-task", "status": "SUCCEEDED", "output": []},
        {"id": "runway-task", "status": "SUCCEEDED", "output": ["http://insecure.example/out.mp4"]},
        {"id": "other-task", "status": "SUCCEEDED", "output": ["https://secret.example/out.mp4"]},
        {"id": "runway-task", "status": "INVENTED"},
    ),
)
def test_runway_poll_refuses_task_payloads_that_cannot_satisfy_the_contract(
    payload: dict[str, Any],
) -> None:
    """A success without one secure output for this task is not evidence of media."""
    provider = _runway_provider(lambda _: httpx.Response(200, json=payload))

    with pytest.raises(GenerationInvalidResponseError):
        provider.poll(handle=_runway_handle())


@pytest.mark.unit
@pytest.mark.parametrize(
    ("outcome", "error_type"),
    (
        (httpx.ReadTimeout("slow"), GenerationTimeoutError),
        (httpx.Response(429), GenerationRateLimitedError),
        (httpx.Response(504), GenerationTimeoutError),
        (httpx.Response(503), GenerationUnavailableError),
    ),
)
def test_runway_http_timeouts_and_throttling_use_retryable_fixed_errors(
    outcome: httpx.Response | Exception, error_type: type[Exception]
) -> None:
    """Workers must retry transient Runway failures without persisting provider messages."""

    def respond(_: httpx.Request) -> httpx.Response:
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    with pytest.raises(error_type) as raised:
        _runway_provider(respond).poll(handle=_runway_handle())
    assert "slow" not in str(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.unit
def test_runway_cancel_deletes_the_task_once_and_tolerates_a_missing_task() -> None:
    """Repeated cancellation must not repeat provider traffic or fail on a finished task."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404)

    provider = _runway_provider(respond)
    handle = _runway_handle()

    provider.cancel(handle=handle)
    provider.cancel(handle=handle)

    assert len(requests) == 1
    assert requests[0].method == "DELETE"
    assert requests[0].url == "https://api.dev.runwayml.com/v1/tasks/runway-task"
    assert requests[0].headers["X-Runway-Version"] == "2024-11-06"


@pytest.mark.unit
def test_runway_refuses_a_handle_issued_by_another_provider() -> None:
    """A foreign handle must never be turned into a Runway URL."""
    provider = _runway_provider(lambda _: httpx.Response(200, json={"id": "runway-task"}))

    with pytest.raises(GenerationInvalidResponseError):
        provider.poll(handle=_fal_handle())
