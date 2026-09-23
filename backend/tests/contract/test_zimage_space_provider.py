"""Contracts for the Z-Image Turbo Hugging Face Space adapter, exercised without network."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from clipah.broll.generation import (
    GenerationHandle,
    GenerationInvalidResponseError,
    GenerationMediaKind,
    GenerationModerationResult,
    GenerationRateLimitedError,
    GenerationRejectedError,
    GenerationRequest,
    GenerationStatus,
    GenerationTimeoutError,
    GenerationUnavailableError,
    GenerationUnknownModelError,
    PromptRejectedError,
)
from clipah.broll.generation_policy import configured_generation_providers
from clipah.broll.zimage_space_adapter import ZImageSpaceConfig, ZImageSpaceProvider
from clipah.config import Environment, Settings

NOW = datetime(2026, 9, 24, 8, 0, tzinfo=UTC)
ORIGIN = "https://tongyi-mai-z-image-turbo.hf.space"
CALL = f"{ORIGIN}/gradio_api/call/generate"
FILE = f"{ORIGIN}/gradio_api/file=/tmp/gradio/abc/image.png"


def _request(**overrides: Any) -> GenerationRequest:
    """One portrait still, as generation policy derives it."""
    values: dict[str, Any] = {
        "prompt": "Subject: coffee cup.",
        "media_kind": GenerationMediaKind.IMAGE,
        "output_count": 1,
        "duration_ms": None,
        "width": 1080,
        "height": 1920,
        "model_alias": "image-default",
        "seed": None,
    }
    values.update(overrides)
    return GenerationRequest(**values)


def _provider(
    respond: Callable[[httpx.Request], httpx.Response],
    *,
    token: str | None = None,
    clock: Callable[[], float] = lambda: 0.0,
) -> ZImageSpaceProvider:
    """An adapter over a local transport with pinned clocks."""
    return ZImageSpaceProvider(
        config=ZImageSpaceConfig(
            space_id="Tongyi-MAI/Z-Image-Turbo",
            model_alias="image-default",
            http_timeout_seconds=10.0,
            stream_seconds=60.0,
            token=None if token is None else SecretStr(token),
        ),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        utc_clock=lambda: NOW,
        monotonic=clock,
    )


def _handle(**overrides: Any) -> GenerationHandle:
    """The handle the adapter issues for one queued call."""
    values: dict[str, Any] = {
        "provider": "hf_space",
        "provider_request_id": "event123",
        "model": "Tongyi-MAI/Z-Image-Turbo",
        "model_version": "steps-8",
        "submitted_at": NOW,
        "output_width": 1152,
        "output_height": 2048,
    }
    values.update(overrides)
    return GenerationHandle(**values)


def _stream(*events: tuple[str, object]) -> httpx.Response:
    """A server-sent event stream as Gradio writes it."""
    body = "".join(f"event: {event}\ndata: {json.dumps(data)}\n\n" for event, data in events)
    return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})


def _complete(url: str = FILE, seed: object = 42) -> tuple[str, object]:
    """The `complete` event of the Space's generate endpoint."""
    return ("complete", [[{"image": {"url": url}, "caption": None}], str(seed), seed])


@pytest.mark.unit
def test_estimate_is_free_and_makes_no_call() -> None:
    """A Space bills nothing, so the price shown before confirmation is zero."""
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    estimate = _provider(respond).estimate(request=_request())

    assert estimate.cost_usd == Decimal(0)
    assert estimate.provider_credits == Decimal(0)
    assert estimate.image_units == Decimal(1)
    assert calls == []


@pytest.mark.unit
def test_submit_queues_the_covering_portrait_size_and_keeps_only_the_event_id() -> None:
    """A 1080x1920 still is drawn at the Space's 1152x2048, the smallest size that covers it."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"event_id": "event123"})

    provider = _provider(respond)
    handle = provider.submit(request=_request(), idempotency_key="job-1")

    assert handle.provider == "hf_space"
    assert handle.provider_request_id == "event123"
    assert (handle.output_width, handle.output_height) == (1152, 2048)
    assert str(requests[0].url) == CALL
    assert "authorization" not in requests[0].headers
    assert json.loads(requests[0].content) == {
        "data": [_request().prompt, "1152x2048 ( 9:16 )", 0, 8, 3.0, True, []]
    }
    # Asking again for the same Job resumes the call instead of queueing another one.
    assert provider.submit(request=_request(), idempotency_key="job-1") == handle
    assert len(requests) == 1
    with pytest.raises(GenerationRejectedError):
        provider.submit(request=_request(prompt="Other."), idempotency_key="job-1")


@pytest.mark.unit
def test_submit_pins_a_requested_seed_and_spends_the_token_allowance() -> None:
    """A seed makes the picture reproducible; a token charges its own ZeroGPU allowance."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"event_id": "event123"})

    _provider(respond, token="hf_secret").submit(
        request=_request(seed=7, width=720, height=1280), idempotency_key="job-1"
    )

    assert requests[0].headers["authorization"] == "Bearer hf_secret"
    assert json.loads(requests[0].content)["data"][1:6] == ["720x1280 ( 9:16 )", 7, 8, 3.0, False]


@pytest.mark.unit
def test_a_request_larger_than_every_size_uses_the_largest_of_its_shape() -> None:
    """A still bigger than the Space draws is drawn at its largest size, never refused."""

    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"event_id": "event123"})

    handle = _provider(respond).submit(
        request=_request(width=2160, height=3840), idempotency_key="job-1"
    )

    assert (handle.output_width, handle.output_height) == (1152, 2048)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"media_kind": GenerationMediaKind.VIDEO, "duration_ms": 5_000}, GenerationRejectedError),
        ({"output_count": 2}, GenerationRejectedError),
        ({"width": 1000, "height": 1000 + 1}, GenerationRejectedError),
        ({"model_alias": "other"}, GenerationUnknownModelError),
        ({"prompt": "impersonation of a mayor"}, PromptRejectedError),
    ],
)
def test_requests_the_space_cannot_serve_are_refused_before_any_call(
    overrides: dict[str, Any], error: type[Exception]
) -> None:
    """Video, several outputs, odd shapes, unknown models, and prohibited prompts stay home."""
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"event_id": "event123"})

    with pytest.raises(error):
        _provider(respond).submit(request=_request(**overrides), idempotency_key="job-1")
    assert calls == []


@pytest.mark.unit
def test_submit_refuses_a_blank_key_and_an_unusable_event_id() -> None:
    """Without an idempotency key or a safe event id there is nothing to resume."""

    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"event_id": "../escape"})

    provider = _provider(respond)
    with pytest.raises(GenerationRejectedError):
        provider.submit(request=_request(), idempotency_key=" ")
    with pytest.raises(GenerationInvalidResponseError):
        provider.submit(request=_request(), idempotency_key="job-1")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "error"),
    [
        (429, GenerationRateLimitedError),
        (504, GenerationTimeoutError),
        (422, GenerationRejectedError),
        (503, GenerationUnavailableError),
        (302, GenerationInvalidResponseError),
    ],
)
def test_http_failures_map_to_fixed_errors(status: int, error: type[Exception]) -> None:
    """A Space's own failure text never leaves the adapter; only a fixed code does."""

    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="internal detail")

    with pytest.raises(error):
        _provider(respond).submit(request=_request(), idempotency_key="job-1")


@pytest.mark.unit
def test_transport_failures_map_to_retryable_errors() -> None:
    """A slow or unreachable Space is worth trying again later."""

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(GenerationTimeoutError):
        _provider(slow).submit(request=_request(), idempotency_key="job-1")
    with pytest.raises(GenerationUnavailableError):
        _provider(down).submit(request=_request(), idempotency_key="job-1")
    with pytest.raises(GenerationTimeoutError):
        _provider(slow).poll(handle=_handle())
    with pytest.raises(GenerationUnavailableError):
        _provider(down).poll(handle=_handle())


@pytest.mark.unit
def test_submit_refuses_a_body_that_is_not_an_object() -> None:
    """Only a JSON object can carry an event id."""

    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    def array(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["event123"])

    with pytest.raises(GenerationInvalidResponseError):
        _provider(respond).submit(request=_request(), idempotency_key="job-1")
    with pytest.raises(GenerationInvalidResponseError):
        _provider(array).submit(request=_request(), idempotency_key="job-1")


@pytest.mark.unit
def test_poll_waits_through_heartbeats_for_the_picture() -> None:
    """The answer arrives once, so the stream is read past heartbeats until it does."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _stream(("heartbeat", None), _complete())

    result = _provider(respond, token="hf_secret").poll(handle=_handle())

    assert str(requests[0].url) == f"{CALL}/event123"
    assert requests[0].headers["authorization"] == "Bearer hf_secret"
    assert result.status is GenerationStatus.SUCCEEDED
    assert result.moderation is GenerationModerationResult.APPROVED
    assert result.seed == 42
    assert result.usage.generated_images == 1
    assert result.usage.cost_usd == Decimal(0)
    assert result.output is not None
    assert result.output.url.get_secret_value() == FILE
    assert result.output.content_type == "image/png"
    assert (result.output.width, result.output.height) == (1152, 2048)


@pytest.mark.unit
def test_a_nonnumeric_seed_is_not_reported() -> None:
    """A seed the Space did not report as a number is not invented."""

    def respond(_: httpx.Request) -> httpx.Response:
        return _stream(_complete(seed="n/a"))

    assert _provider(respond).poll(handle=_handle()).seed is None


@pytest.mark.unit
def test_an_error_event_or_a_stream_with_no_answer_fails_the_call() -> None:
    """A Space error, exhausted GPU allowance, or an answer already taken is a failed call."""

    def errored(_: httpx.Request) -> httpx.Response:
        return _stream(("error", "You have exceeded your GPU quota"))

    def empty(_: httpx.Request) -> httpx.Response:
        return _stream(("heartbeat", None))

    for respond in (errored, empty):
        result = _provider(respond).poll(handle=_handle())
        assert result.status is GenerationStatus.FAILED
        assert result.output is None


@pytest.mark.unit
def test_poll_gives_up_once_the_stream_deadline_passes() -> None:
    """A Space that never answers cannot hold a worker forever."""
    ticks = iter([0.0, 61.0])

    def respond(_: httpx.Request) -> httpx.Response:
        return _stream(("heartbeat", None), _complete())

    with pytest.raises(GenerationTimeoutError):
        _provider(respond, clock=lambda: next(ticks)).poll(handle=_handle())


@pytest.mark.unit
def test_poll_maps_a_failed_stream_status() -> None:
    """A stream the Space refuses to open is mapped like any other HTTP failure."""

    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    with pytest.raises(GenerationRateLimitedError):
        _provider(respond).poll(handle=_handle())


@pytest.mark.unit
@pytest.mark.parametrize(
    "data",
    [
        "not json",
        json.dumps({"image": FILE}),
        json.dumps([[], "1", 1]),
        json.dumps([["x"], "1", 1]),
        json.dumps([[{"image": {"url": "https://attacker.example/gradio_api/file=a.png"}}], "", 1]),
        json.dumps([[{"image": {"url": f"{ORIGIN}/gradio_api/file=/tmp/a.gif"}}], "", 1]),
        json.dumps([[{"image": "flat"}], "", 1]),
    ],
)
def test_a_result_that_is_not_one_image_from_this_space_is_refused(data: str) -> None:
    """Only a picture served by the configured Space may be fetched by the worker."""

    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=f"event: complete\ndata: {data}\n\n")

    with pytest.raises(GenerationInvalidResponseError):
        _provider(respond).poll(handle=_handle())


@pytest.mark.unit
def test_a_handle_without_its_size_is_refused() -> None:
    """The Space never reports geometry, so a handle that lost it cannot be completed."""

    def respond(_: httpx.Request) -> httpx.Response:
        return _stream(_complete())

    with pytest.raises(GenerationInvalidResponseError):
        _provider(respond).poll(handle=_handle(output_width=None))


@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides",
    [{"provider": "fal"}, {"model": "Other/Space"}, {"provider_request_id": "a/b"}],
)
def test_foreign_handles_are_refused_without_a_call(overrides: dict[str, Any]) -> None:
    """A handle this adapter did not issue cannot steer its requests."""
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _stream(_complete())

    provider = _provider(respond)
    with pytest.raises(GenerationInvalidResponseError):
        provider.poll(handle=_handle(**overrides))
    with pytest.raises(GenerationInvalidResponseError):
        provider.cancel(handle=_handle(**overrides))
    assert calls == []


@pytest.mark.unit
def test_cancel_is_a_harmless_no_op() -> None:
    """A queued Gradio call cannot be withdrawn, and letting it finish costs nothing."""
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    _provider(respond).cancel(handle=_handle())
    assert calls == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides",
    [
        {"space_id": "no-owner"},
        {"model_alias": " "},
        {"http_timeout_seconds": 0.0},
        {"stream_seconds": float("inf")},
    ],
)
def test_config_rejects_unsafe_values(overrides: dict[str, Any]) -> None:
    """A malformed Space or an unbounded wait is refused before any request."""
    values: dict[str, Any] = {
        "space_id": "Tongyi-MAI/Z-Image-Turbo",
        "model_alias": "image-default",
        "http_timeout_seconds": 10.0,
        "stream_seconds": 60.0,
    }
    values.update(overrides)
    with pytest.raises(ValueError):
        ZImageSpaceConfig(**values)


@pytest.mark.unit
def test_the_space_origin_follows_hugging_face_naming() -> None:
    """The host is the lowercased id with every separator turned into a dash."""
    config = ZImageSpaceConfig(
        space_id="Tongyi-MAI/Z-Image_Turbo.v2",
        model_alias="image-default",
        http_timeout_seconds=1.0,
        stream_seconds=1.0,
    )
    assert config.origin == "https://tongyi-mai-z-image-turbo-v2.hf.space"


@pytest.mark.unit
def test_selecting_the_space_builds_it_for_stills_only() -> None:
    """The Space answers stills; video still comes from the video provider, if any."""
    settings = Settings(environment=Environment.TEST, generated_image_provider="hf_space")

    providers = configured_generation_providers(settings)

    assert isinstance(providers(GenerationMediaKind.IMAGE), ZImageSpaceProvider)
    assert not isinstance(providers(GenerationMediaKind.VIDEO), ZImageSpaceProvider)


@pytest.mark.unit
def test_settings_refuse_a_malformed_space_id() -> None:
    """A Space id that is not owner/name fails at startup, not on the first request."""
    with pytest.raises(ValidationError):
        Settings(
            environment=Environment.TEST,
            generated_image_provider="hf_space",
            hf_space_image_id="not a space",
        )
