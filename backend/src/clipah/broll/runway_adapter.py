"""Raw-HTTP Runway adapter for asynchronous generated B-roll video.

Runway is the optional second video provider. Its task payload reports neither
geometry nor billed credits, so the adapter records the requested geometry on the
handle and derives credits from the configured per-second rate at Runway's fixed
one-cent credit price.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
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

RUNWAY_API_ORIGIN = "https://api.dev.runwayml.com"
RUNWAY_API_VERSION = "2024-11-06"
RUNWAY_CREDIT_USD = Decimal("0.01")
RUNWAY_VIDEO_CONTENT_TYPE = "video/mp4"
RUNWAY_SUPPORTED_DURATIONS_MS = frozenset({5_000, 10_000})
RUNWAY_SUPPORTED_RATIOS = frozenset(
    {
        (1280, 720),
        (720, 1280),
        (1104, 832),
        (832, 1104),
        (960, 960),
        (1584, 672),
    }
)
_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True, slots=True)
class RunwayAdapterConfig:
    """Validated Runway credentials, allowlisted models, and bounded request policy."""

    api_secret: SecretStr
    video_models: Mapping[str, str]
    credits_per_second: Decimal
    http_timeout_seconds: float
    max_duration_ms: int = 5_000

    def __post_init__(self) -> None:
        """Reject unsafe model or pricing configuration before any request can leave."""
        if not self.api_secret.get_secret_value().strip():
            raise ValueError("Runway API secret must not be blank")
        if not math.isfinite(self.http_timeout_seconds) or self.http_timeout_seconds <= 0:
            raise ValueError("Runway HTTP timeout must be positive and finite")
        if not self.credits_per_second.is_finite() or self.credits_per_second <= 0:
            raise ValueError("Runway credit rate must be positive and finite")
        if self.max_duration_ms <= 0:
            raise ValueError("Runway maximum duration must be positive")

        validated: dict[str, str] = {}
        for alias, model in self.video_models.items():
            if (
                not alias
                or alias != alias.strip()
                or not model
                or model != model.strip()
                or _MODEL_PATTERN.fullmatch(model) is None
                or "sora" in alias.casefold()
                or "sora" in model.casefold()
            ):
                raise ValueError("Runway model allowlist contains an unsafe value")
            validated[alias] = model
        object.__setattr__(self, "video_models", MappingProxyType(validated))


class RunwayGenerativeMediaProvider:
    """Translate Runway task payloads into provider-neutral video values."""

    def __init__(
        self,
        *,
        config: RunwayAdapterConfig,
        client: httpx.Client,
        utc_clock: Callable[[], datetime],
    ) -> None:
        """Bind explicit network and clock dependencies for deterministic operation."""
        self._config = config
        self._client = client
        self._utc_clock = utc_clock
        self._submissions: dict[str, tuple[GenerationRequest, GenerationHandle]] = {}
        self._canceled: set[str] = set()

    def estimate(self, *, request: GenerationRequest) -> GenerationEstimate:
        """Estimate credits and USD from the configured rate without a provider call."""
        self._validate_request(request)
        duration_ms = _required_duration(request)
        credits = self._credits_for(duration_ms=duration_ms, output_count=request.output_count)
        return GenerationEstimate.video(
            output_count=request.output_count,
            duration_ms=duration_ms,
            width=request.width,
            height=request.height,
            latency_class=GenerationLatencyClass.SLOW,
            provider_credits=credits,
            cost_usd=credits * RUNWAY_CREDIT_USD,
        )

    def submit(self, *, request: GenerationRequest, idempotency_key: str) -> GenerationHandle:
        """Start one allowlisted task and retain only its opaque identity and geometry."""
        model = self._validate_request(request)
        duration_ms = _required_duration(request)
        if not idempotency_key.strip():
            raise GenerationRejectedError
        existing = self._submissions.get(idempotency_key)
        if existing is not None:
            previous_request, handle = existing
            if previous_request != request:
                raise GenerationRejectedError
            return handle

        payload: dict[str, Any] = {
            "model": model,
            "promptText": request.prompt,
            "ratio": f"{request.width}:{request.height}",
            "duration": duration_ms // 1_000,
        }
        if request.seed is not None:
            payload["seed"] = request.seed
        response = self._request(
            "POST",
            f"{RUNWAY_API_ORIGIN}/v1/image_to_video",
            json=payload,
        )
        task_id = _json_object(response).get("id")
        if not isinstance(task_id, str) or not _safe_identity(task_id):
            raise GenerationInvalidResponseError
        handle = GenerationHandle(
            provider="runway",
            provider_request_id=task_id,
            model=model,
            model_version=RUNWAY_API_VERSION,
            submitted_at=self._utc_clock(),
            output_width=request.width,
            output_height=request.height,
            output_duration_ms=duration_ms,
        )
        self._submissions[idempotency_key] = (request, handle)
        return handle

    def poll(self, *, handle: GenerationHandle) -> GenerationResult:
        """Read one task state and normalize it without retaining Runway's own text."""
        self._validate_handle(handle)
        payload = _json_object(self._request("GET", self._task_url(handle)))
        if payload.get("id") != handle.provider_request_id:
            raise GenerationInvalidResponseError

        status = payload.get("status")
        if status in {"PENDING", "THROTTLED"}:
            return _pending_result(GenerationStatus.QUEUED)
        if status == "RUNNING":
            return _pending_result(GenerationStatus.RUNNING)
        if status in {"CANCELLED", "CANCELED"}:
            return _terminal_result(GenerationStatus.CANCELED, GenerationModerationResult.PENDING)
        if status == "FAILED":
            return _failure_result(payload.get("failureCode"))
        if status != "SUCCEEDED":
            raise GenerationInvalidResponseError
        return self._decode_success(handle=handle, payload=payload)

    def cancel(self, *, handle: GenerationHandle) -> None:
        """Delete one task once, treating a finished or unknown task as already stopped."""
        self._validate_handle(handle)
        if handle.provider_request_id in self._canceled:
            return
        self._request("DELETE", self._task_url(handle), idempotent_cancel=True)
        self._canceled.add(handle.provider_request_id)

    def _decode_success(
        self, *, handle: GenerationHandle, payload: dict[str, Any]
    ) -> GenerationResult:
        """Decode one secure output capability and derive its billed credits."""
        outputs = payload.get("output")
        if not isinstance(outputs, list) or len(outputs) != 1:
            raise GenerationInvalidResponseError
        url = outputs[0]
        if not isinstance(url, str) or urlsplit(url).scheme != "https" or not urlsplit(url).netloc:
            raise GenerationInvalidResponseError
        width, height, duration_ms = _required_geometry(handle)
        credits = self._credits_for(duration_ms=duration_ms, output_count=1)
        try:
            output = GenerationOutput(
                url=SecretStr(url),
                content_type=RUNWAY_VIDEO_CONTENT_TYPE,
                width=width,
                height=height,
                duration_ms=duration_ms,
            )
        except ValueError:
            raise GenerationInvalidResponseError from None
        return GenerationResult(
            status=GenerationStatus.SUCCEEDED,
            output=output,
            usage=GenerationUsage(
                generated_images=0,
                generated_videos=1,
                generated_seconds=Decimal(duration_ms) / Decimal(1_000),
                provider_credits=credits,
                cost_usd=credits * RUNWAY_CREDIT_USD,
            ),
            seed=None,
            moderation=GenerationModerationResult.APPROVED,
        )

    def _credits_for(self, *, duration_ms: int, output_count: int) -> Decimal:
        """Derive billed credits from the configured per-second rate."""
        seconds = Decimal(duration_ms) / Decimal(1_000)
        return seconds * self._config.credits_per_second * Decimal(output_count)

    def _validate_request(self, request: GenerationRequest) -> str:
        """Apply video-only, prompt, model, geometry, and duration policy before HTTP."""
        if request.media_kind is not GenerationMediaKind.VIDEO:
            raise GenerationRejectedError
        PromptModerator().ensure_allowed(text=request.prompt)
        model = self._config.video_models.get(request.model_alias)
        if model is None:
            raise GenerationUnknownModelError
        duration_ms = _required_duration(request)
        if (
            request.output_count != 1
            or duration_ms > self._config.max_duration_ms
            or duration_ms not in RUNWAY_SUPPORTED_DURATIONS_MS
            or (request.width, request.height) not in RUNWAY_SUPPORTED_RATIOS
        ):
            raise GenerationRejectedError
        return model

    def _validate_handle(self, handle: GenerationHandle) -> None:
        """Refuse foreign providers, unknown models, or unsafe task identities."""
        if (
            handle.provider != "runway"
            or handle.model not in set(self._config.video_models.values())
            or not _safe_identity(handle.provider_request_id)
        ):
            raise GenerationInvalidResponseError

    def _task_url(self, handle: GenerationHandle) -> str:
        """Reconstruct a task URL exclusively from validated identities."""
        return f"{RUNWAY_API_ORIGIN}/v1/tasks/{quote(handle.provider_request_id, safe='')}"

    def _request(
        self,
        method: str,
        url: str,
        *,
        json: Mapping[str, Any] | None = None,
        idempotent_cancel: bool = False,
    ) -> httpx.Response:
        """Bound HTTP calls and collapse provider failures into fixed local errors."""
        headers = {
            "Authorization": f"Bearer {self._config.api_secret.get_secret_value()}",
            "X-Runway-Version": RUNWAY_API_VERSION,
        }
        try:
            response = self._client.request(
                method,
                url,
                headers=headers,
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
        if idempotent_cancel and status in {400, 404, 409}:
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


def _required_duration(request: GenerationRequest) -> int:
    """Read the duration every Runway request owns, refusing a still by construction."""
    if request.duration_ms is None:
        raise GenerationRejectedError
    return request.duration_ms


def _required_geometry(handle: GenerationHandle) -> tuple[int, int, int]:
    """Recover the geometry Runway's task payload never reports."""
    if (
        handle.output_width is None
        or handle.output_height is None
        or handle.output_duration_ms is None
    ):
        raise GenerationInvalidResponseError
    return handle.output_width, handle.output_height, handle.output_duration_ms


def _failure_result(failure_code: object) -> GenerationResult:
    """Map Runway's machine failure category while discarding all provider text."""
    if isinstance(failure_code, str):
        if failure_code.startswith("SAFETY.INPUT"):
            return _terminal_result(
                GenerationStatus.MODERATION_REJECTED,
                GenerationModerationResult.INPUT_REJECTED,
            )
        if failure_code.startswith("SAFETY.OUTPUT"):
            return _terminal_result(
                GenerationStatus.MODERATION_REJECTED,
                GenerationModerationResult.OUTPUT_REJECTED,
            )
    return _terminal_result(GenerationStatus.FAILED, GenerationModerationResult.PENDING)


def _pending_result(status: GenerationStatus) -> GenerationResult:
    """Construct a normalized nonterminal result with no fabricated evidence."""
    return _terminal_result(status, GenerationModerationResult.PENDING)


def _terminal_result(
    status: GenerationStatus, moderation: GenerationModerationResult
) -> GenerationResult:
    """Construct one outcome that carries neither media nor reported usage."""
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


def _safe_identity(value: str) -> bool:
    """Permit opaque task identities without allowing URL path control characters."""
    return (
        bool(value)
        and len(value) <= 256
        and all(character.isalnum() or character in "-_." for character in value)
    )
