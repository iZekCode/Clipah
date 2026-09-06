"""Raw-HTTP fal adapter for asynchronous generated B-roll requests."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from pydantic import SecretStr

from clipah.broll.generation import (
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
    PromptModerator,
)

FAL_QUEUE_ORIGIN = "https://queue.fal.run"
FAL_PRICING_URL = "https://api.fal.ai/v1/models/pricing"
FAL_WEBHOOK_PATH = "/api/v1/webhooks/generation/fal"
_ENDPOINT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


@dataclass(frozen=True, slots=True)
class FalAdapterConfig:
    """Validated fal credentials, allowlisted models, and bounded request policy."""

    api_key: SecretStr
    webhook_base_url: str
    image_models: Mapping[str, str]
    video_models: Mapping[str, str]
    http_timeout_seconds: float
    pricing_cache_seconds: float
    max_duration_ms: int = 5_000

    def __post_init__(self) -> None:
        """Reject unsafe endpoint configuration before any request can leave the process."""
        if not self.api_key.get_secret_value().strip():
            raise ValueError("fal API key must not be blank")
        origin = urlsplit(self.webhook_base_url)
        if (
            origin.scheme != "https"
            or not origin.netloc
            or origin.path not in {"", "/"}
            or origin.query
            or origin.fragment
        ):
            raise ValueError("fal webhook base URL must be a bare HTTPS origin")
        if not math.isfinite(self.http_timeout_seconds) or self.http_timeout_seconds <= 0:
            raise ValueError("fal HTTP timeout must be positive and finite")
        if (
            not math.isfinite(self.pricing_cache_seconds)
            or not 0 < self.pricing_cache_seconds <= 86_400
        ):
            raise ValueError("fal pricing cache must be between zero and 24 hours")
        if self.max_duration_ms <= 0:
            raise ValueError("fal maximum duration must be positive")

        image_models = _validated_models(self.image_models)
        video_models = _validated_models(self.video_models)
        overlap = image_models.keys() & video_models.keys()
        if overlap:
            raise ValueError("fal model aliases must identify exactly one media kind")
        object.__setattr__(self, "image_models", MappingProxyType(image_models))
        object.__setattr__(self, "video_models", MappingProxyType(video_models))


@dataclass(frozen=True, slots=True)
class _Price:
    """One validated fal USD price and its billing unit."""

    unit_price: Decimal
    unit: str


class FalGenerativeMediaProvider:
    """Translate fal queue and pricing HTTP payloads into provider-neutral values."""

    def __init__(
        self,
        *,
        config: FalAdapterConfig,
        client: httpx.Client,
        utc_clock: Callable[[], datetime],
        monotonic: Callable[[], float],
    ) -> None:
        """Bind explicit network and clock dependencies for deterministic operation."""
        self._config = config
        self._client = client
        self._utc_clock = utc_clock
        self._monotonic = monotonic
        self._price_cache: dict[str, tuple[float, _Price]] = {}
        self._submissions: dict[str, tuple[GenerationRequest, GenerationHandle]] = {}
        self._canceled: set[tuple[str, str]] = set()

    def estimate(self, *, request: GenerationRequest) -> GenerationEstimate:
        """Estimate normalized quota and USD cost from fal's current unit price."""
        endpoint_id = self._endpoint_for(request)
        price = self._price_for(endpoint_id)
        billing_units = _estimated_billing_units(request=request, unit=price.unit)
        cost_usd = price.unit_price * billing_units
        if request.media_kind is GenerationMediaKind.IMAGE:
            return GenerationEstimate.image(
                output_count=request.output_count,
                width=request.width,
                height=request.height,
                latency_class=GenerationLatencyClass.STANDARD,
                provider_credits=billing_units,
                cost_usd=cost_usd,
            )
        if request.duration_ms is None:
            raise GenerationInvalidResponseError
        return GenerationEstimate.video(
            output_count=request.output_count,
            duration_ms=request.duration_ms,
            width=request.width,
            height=request.height,
            latency_class=GenerationLatencyClass.SLOW,
            provider_credits=billing_units,
            cost_usd=cost_usd,
        )

    def submit(self, *, request: GenerationRequest, idempotency_key: str) -> GenerationHandle:
        """Submit one allowlisted queue request and retain no provider convenience URLs."""
        endpoint_id = self._endpoint_for(request)
        if not idempotency_key.strip():
            raise GenerationRejectedError
        existing = self._submissions.get(idempotency_key)
        if existing is not None:
            previous_request, handle = existing
            if previous_request != request:
                raise GenerationRejectedError
            return handle

        response = self._request(
            "POST",
            f"{FAL_QUEUE_ORIGIN}/{endpoint_id}",
            params={
                "fal_webhook": f"{self._config.webhook_base_url.rstrip('/')}{FAL_WEBHOOK_PATH}"
            },
            headers={
                **self._authorization_headers(),
                "X-Fal-No-Retry": "1",
                "X-Fal-Request-Timeout": _format_seconds(self._config.http_timeout_seconds),
                "Idempotency-Key": idempotency_key,
            },
            json=_submission_payload(request),
        )
        payload = _json_object(response)
        provider_request_id = payload.get("request_id")
        if not isinstance(provider_request_id, str) or not _safe_identity(provider_request_id):
            raise GenerationInvalidResponseError
        handle = GenerationHandle(
            provider="fal",
            provider_request_id=provider_request_id,
            model=endpoint_id,
            model_version="configured",
            submitted_at=self._utc_clock(),
        )
        self._submissions[idempotency_key] = (request, handle)
        return handle

    def poll(self, *, handle: GenerationHandle) -> GenerationResult:
        """Read one bounded queue status and retrieve a completed result without logs."""
        endpoint_id = self._validate_handle(handle)
        base_url = self._request_url(endpoint_id=endpoint_id, request_id=handle.provider_request_id)
        status_response = self._request(
            "GET",
            f"{base_url}/status",
            headers=self._authorization_headers(),
        )
        status_payload = _json_object(status_response)
        _require_matching_request_id(status_payload, handle.provider_request_id)
        status = status_payload.get("status")
        if status == "IN_QUEUE":
            return _pending_result(GenerationStatus.QUEUED)
        if status == "IN_PROGRESS":
            return _pending_result(GenerationStatus.RUNNING)
        if status != "COMPLETED":
            raise GenerationInvalidResponseError
        if "error" in status_payload or "error_type" in status_payload:
            return _error_result(status_payload)

        result_response = self._request(
            "GET",
            base_url,
            headers=self._authorization_headers(),
        )
        return self._decode_success(
            endpoint_id=endpoint_id,
            payload=_json_object(result_response),
            response=result_response,
        )

    def cancel(self, *, handle: GenerationHandle) -> None:
        """Request cancellation once while treating completed or missing work idempotently."""
        endpoint_id = self._validate_handle(handle)
        cancellation = (endpoint_id, handle.provider_request_id)
        if cancellation in self._canceled:
            return
        request_url = self._request_url(
            endpoint_id=endpoint_id,
            request_id=handle.provider_request_id,
        )
        url = f"{request_url}/cancel"
        self._request(
            "PUT",
            url,
            headers=self._authorization_headers(),
            idempotent_cancel=True,
        )
        self._canceled.add(cancellation)

    def _endpoint_for(self, request: GenerationRequest) -> str:
        """Resolve one server-owned alias and apply prompt and duration policy."""
        PromptModerator().ensure_allowed(text=request.prompt)
        models = (
            self._config.image_models
            if request.media_kind is GenerationMediaKind.IMAGE
            else self._config.video_models
        )
        endpoint_id = models.get(request.model_alias)
        if endpoint_id is None:
            raise GenerationUnknownModelError
        if request.duration_ms is not None and request.duration_ms > self._config.max_duration_ms:
            raise GenerationRejectedError
        return endpoint_id

    def _validate_handle(self, handle: GenerationHandle) -> str:
        """Refuse foreign providers, unknown models, or unsafe request identities."""
        allowed = set(self._config.image_models.values()) | set(self._config.video_models.values())
        if (
            handle.provider != "fal"
            or handle.model not in allowed
            or not _safe_identity(handle.provider_request_id)
        ):
            raise GenerationInvalidResponseError
        return handle.model

    def _price_for(self, endpoint_id: str) -> _Price:
        """Return a fresh cached price or fetch and strictly decode current pricing."""
        now = self._monotonic()
        cached = self._price_cache.get(endpoint_id)
        if cached is not None and now - cached[0] <= self._config.pricing_cache_seconds:
            return cached[1]
        response = self._request(
            "GET",
            FAL_PRICING_URL,
            params={"endpoint_id": endpoint_id},
            headers=self._authorization_headers(),
        )
        payload = _json_object(response)
        prices = payload.get("prices")
        if not isinstance(prices, list) or len(prices) != 1 or not isinstance(prices[0], dict):
            raise GenerationInvalidResponseError
        value = prices[0]
        if value.get("endpoint_id") != endpoint_id or value.get("currency") != "USD":
            raise GenerationInvalidResponseError
        unit = value.get("unit")
        if unit not in {"image", "megapixel", "second", "video"}:
            raise GenerationInvalidResponseError
        unit_price = _decimal(value.get("unit_price"))
        if unit_price < 0:
            raise GenerationInvalidResponseError
        price = _Price(unit_price=unit_price, unit=unit)
        self._price_cache[endpoint_id] = (now, price)
        return price

    def _decode_success(
        self,
        *,
        endpoint_id: str,
        payload: dict[str, Any],
        response: httpx.Response,
    ) -> GenerationResult:
        """Decode only documented media, usage, seed, and moderation fields."""
        safety = payload.get("has_nsfw_concepts")
        if (
            not isinstance(safety, list)
            or not safety
            or not all(isinstance(item, bool) for item in safety)
        ):
            raise GenerationInvalidResponseError
        if any(safety):
            return GenerationResult(
                status=GenerationStatus.MODERATION_REJECTED,
                output=None,
                usage=GenerationUsage.zero(),
                seed=_optional_nonnegative_int(payload.get("seed")),
                moderation=GenerationModerationResult.OUTPUT_REJECTED,
            )

        media_kind = self._media_kind_for_endpoint(endpoint_id)
        output, output_count, generated_seconds = _decode_output(payload, media_kind)
        billable_units = _decimal(response.headers.get("X-Fal-Billable-Units"))
        price = self._price_for(endpoint_id)
        usage = GenerationUsage(
            generated_images=output_count if media_kind is GenerationMediaKind.IMAGE else 0,
            generated_videos=output_count if media_kind is GenerationMediaKind.VIDEO else 0,
            generated_seconds=generated_seconds,
            provider_credits=billable_units,
            cost_usd=billable_units * price.unit_price,
        )
        return GenerationResult(
            status=GenerationStatus.SUCCEEDED,
            output=output,
            usage=usage,
            seed=_optional_nonnegative_int(payload.get("seed")),
            moderation=GenerationModerationResult.APPROVED,
        )

    def _media_kind_for_endpoint(self, endpoint_id: str) -> GenerationMediaKind:
        """Recover the configured media kind without retaining the submitted payload."""
        if endpoint_id in self._config.image_models.values():
            return GenerationMediaKind.IMAGE
        if endpoint_id in self._config.video_models.values():
            return GenerationMediaKind.VIDEO
        raise GenerationInvalidResponseError

    def _request_url(self, *, endpoint_id: str, request_id: str) -> str:
        """Reconstruct a queue URL exclusively from allowlisted and validated identities."""
        return f"{FAL_QUEUE_ORIGIN}/{endpoint_id}/requests/{quote(request_id, safe='')}"

    def _authorization_headers(self) -> dict[str, str]:
        """Build the secret-bearing header only at the HTTP boundary."""
        return {"Authorization": f"Key {self._config.api_key.get_secret_value()}"}

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None = None,
        json: Mapping[str, Any] | None = None,
        idempotent_cancel: bool = False,
    ) -> httpx.Response:
        """Bound HTTP calls and collapse provider failures into fixed local errors."""
        try:
            response = self._client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
                timeout=self._config.http_timeout_seconds,
            )
        except httpx.TimeoutException:
            raise GenerationTimeoutError from None
        except httpx.HTTPError:
            raise GenerationUnavailableError from None
        status = response.status_code
        if 200 <= status < 300:
            return response
        if idempotent_cancel and status in {400, 404}:
            return response
        if status == 429:
            raise GenerationRateLimitedError
        if status in {408, 504}:
            raise GenerationTimeoutError
        if status in {400, 404, 422}:
            raise GenerationRejectedError
        if status >= 500:
            raise GenerationUnavailableError
        raise GenerationInvalidResponseError


def _validated_models(models: Mapping[str, str]) -> dict[str, str]:
    """Copy and validate aliases and endpoint IDs as a closed allowlist."""
    validated: dict[str, str] = {}
    for alias, endpoint_id in models.items():
        if (
            not alias
            or alias != alias.strip()
            or not endpoint_id
            or endpoint_id != endpoint_id.strip()
            or _ENDPOINT_PATTERN.fullmatch(endpoint_id) is None
            or "sora" in alias.casefold()
            or "sora" in endpoint_id.casefold()
        ):
            raise ValueError("fal model allowlist contains an unsafe value")
        validated[alias] = endpoint_id
    return validated


def _submission_payload(request: GenerationRequest) -> dict[str, Any]:
    """Encode the minimal shared fal input fields for an allowlisted endpoint."""
    aspect_ratio = _aspect_ratio(
        width=request.width,
        height=request.height,
        media_kind=request.media_kind,
    )
    if request.media_kind is GenerationMediaKind.VIDEO:
        if request.output_count != 1 or request.duration_ms not in {5_000, 10_000}:
            raise GenerationRejectedError
        return {
            "prompt": request.prompt,
            "duration": str(request.duration_ms // 1_000),
            "aspect_ratio": aspect_ratio,
            "generate_audio": False,
        }

    if request.output_count > 4:
        raise GenerationRejectedError
    payload: dict[str, Any] = {
        "prompt": request.prompt,
        "num_images": request.output_count,
        "aspect_ratio": aspect_ratio,
        "output_format": "png",
        "safety_tolerance": "1",
        "limit_generations": True,
        "enable_web_search": False,
    }
    if request.seed is not None:
        payload["seed"] = request.seed
    return payload


def _aspect_ratio(*, width: int, height: int, media_kind: GenerationMediaKind) -> str:
    """Map exact provider-neutral geometry onto one model-supported aspect ratio."""
    ratios: tuple[tuple[tuple[int, int], str], ...] = (
        ((16, 9), "16:9"),
        ((9, 16), "9:16"),
        ((1, 1), "1:1"),
    )
    if media_kind is GenerationMediaKind.IMAGE:
        ratios += (
            ((21, 9), "21:9"),
            ((3, 2), "3:2"),
            ((2, 3), "2:3"),
            ((4, 3), "4:3"),
            ((3, 4), "3:4"),
            ((5, 4), "5:4"),
            ((4, 5), "4:5"),
            ((4, 1), "4:1"),
            ((1, 4), "1:4"),
            ((8, 1), "8:1"),
            ((1, 8), "1:8"),
        )
    for (ratio_width, ratio_height), label in ratios:
        if width * ratio_height == height * ratio_width:
            return label
    raise GenerationRejectedError


def _estimated_billing_units(*, request: GenerationRequest, unit: str) -> Decimal:
    """Convert one documented fal billing unit into a deterministic request quantity."""
    count = Decimal(request.output_count)
    if unit in {"image", "video"}:
        return count
    if unit == "megapixel" and request.media_kind is GenerationMediaKind.IMAGE:
        return Decimal(request.width * request.height) / Decimal(1_000_000) * count
    if unit == "second" and request.duration_ms is not None:
        return Decimal(request.duration_ms) / Decimal(1_000) * count
    raise GenerationInvalidResponseError


def _decode_output(
    payload: dict[str, Any], media_kind: GenerationMediaKind
) -> tuple[GenerationOutput, int, Decimal]:
    """Decode one supported image or video output and reject provider-specific extras."""
    if media_kind is GenerationMediaKind.IMAGE:
        images = payload.get("images")
        if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict):
            raise GenerationInvalidResponseError
        return _media_output(images[0], duration_ms=None), 1, Decimal(0)

    video = payload.get("video")
    if not isinstance(video, dict):
        raise GenerationInvalidResponseError
    duration_ms = video.get("duration_ms")
    if not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms <= 0:
        raise GenerationInvalidResponseError
    return (
        _media_output(video, duration_ms=duration_ms),
        1,
        Decimal(duration_ms) / Decimal(1_000),
    )


def _media_output(payload: dict[str, Any], *, duration_ms: int | None) -> GenerationOutput:
    """Decode one ephemeral HTTPS capability and its validation metadata."""
    url = payload.get("url")
    content_type = payload.get("content_type")
    width = payload.get("width")
    height = payload.get("height")
    if (
        not isinstance(url, str)
        or urlsplit(url).scheme != "https"
        or not urlsplit(url).netloc
        or not isinstance(content_type, str)
        or not isinstance(width, int)
        or isinstance(width, bool)
        or not isinstance(height, int)
        or isinstance(height, bool)
    ):
        raise GenerationInvalidResponseError
    try:
        return GenerationOutput(
            url=SecretStr(url),
            content_type=content_type,
            width=width,
            height=height,
            duration_ms=duration_ms,
        )
    except ValueError:
        raise GenerationInvalidResponseError from None


def _pending_result(status: GenerationStatus) -> GenerationResult:
    """Construct a normalized nonterminal result with no fabricated evidence."""
    return GenerationResult(
        status=status,
        output=None,
        usage=GenerationUsage.zero(),
        seed=None,
        moderation=GenerationModerationResult.PENDING,
    )


def _error_result(payload: dict[str, Any]) -> GenerationResult:
    """Map fal's machine error category while discarding all provider text."""
    error_type = payload.get("error_type")
    if error_type in {"input_moderation", "content_policy_input"}:
        status = GenerationStatus.MODERATION_REJECTED
        moderation = GenerationModerationResult.INPUT_REJECTED
    elif error_type in {"output_moderation", "content_policy_output"}:
        status = GenerationStatus.MODERATION_REJECTED
        moderation = GenerationModerationResult.OUTPUT_REJECTED
    elif error_type in {"validation_error", "invalid_request"}:
        status = GenerationStatus.REJECTED
        moderation = GenerationModerationResult.PENDING
    elif error_type in {"canceled", "cancelled"}:
        status = GenerationStatus.CANCELED
        moderation = GenerationModerationResult.PENDING
    else:
        status = GenerationStatus.FAILED
        moderation = GenerationModerationResult.PENDING
    return GenerationResult(
        status=status,
        output=None,
        usage=GenerationUsage.zero(),
        seed=None,
        moderation=moderation,
    )


def _json_object(response: httpx.Response) -> dict[str, Any]:
    """Decode one JSON object without preserving response text or unknown top-level types."""
    try:
        payload = response.json()
    except ValueError:
        raise GenerationInvalidResponseError from None
    if not isinstance(payload, dict):
        raise GenerationInvalidResponseError
    return payload


def _decimal(value: object) -> Decimal:
    """Decode a finite decimal without binary-float artifacts or coercing booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise GenerationInvalidResponseError
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise GenerationInvalidResponseError from None
    if not result.is_finite() or result < 0:
        raise GenerationInvalidResponseError
    return result


def _optional_nonnegative_int(value: object) -> int | None:
    """Decode an optional non-negative seed without accepting boolean integers."""
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise GenerationInvalidResponseError
    return value


def _require_matching_request_id(payload: dict[str, Any], expected: str) -> None:
    """Reject a queue response that names another provider request."""
    request_id = payload.get("request_id")
    if request_id is not None and request_id != expected:
        raise GenerationInvalidResponseError


def _safe_identity(value: str) -> bool:
    """Permit opaque request identities without allowing URL path control characters."""
    return (
        bool(value)
        and len(value) <= 256
        and all(character.isalnum() or character in "-_." for character in value)
    )


def _format_seconds(value: float) -> str:
    """Render integral timeout seconds without a provider-visible decimal suffix."""
    return str(int(value)) if value.is_integer() else str(value)
