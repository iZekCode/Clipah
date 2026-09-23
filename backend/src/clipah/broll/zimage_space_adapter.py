"""Raw-HTTP adapter for generated B-roll stills from a Z-Image Turbo Hugging Face Space.

A Space is a Gradio app, not a billing API: it is free, shared, and bounded by the caller's
ZeroGPU allowance (a token's account, or the anonymous allowance without one). It is meant
for development, where a paid provider is not wanted.

Gradio's call API has two steps. A POST queues the call and answers with an event id; a GET
on that id streams server-sent events until the result arrives. The result is delivered
exactly once, and a stream closed before it arrives forfeits it, so a poll holds the stream
open until the answer comes rather than peeking and hanging up.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote

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

ZIMAGE_SPACE_PROVIDER = "hf_space"
ZIMAGE_API_NAME = "generate"
ZIMAGE_STEPS = 8
ZIMAGE_TIME_SHIFT = 3.0
#: The portrait and landscape sizes the Space's resolution dropdown accepts, verbatim.
ZIMAGE_RESOLUTIONS = (
    (720, 1280, "720x1280 ( 9:16 )"),
    (864, 1536, "864x1536 ( 9:16 )"),
    (1152, 2048, "1152x2048 ( 9:16 )"),
    (1280, 720, "1280x720 ( 16:9 )"),
    (1536, 864, "1536x864 ( 16:9 )"),
    (2048, 1152, "2048x1152 ( 16:9 )"),
    (1024, 1024, "1024x1024 ( 1:1 )"),
    (1280, 1280, "1280x1280 ( 1:1 )"),
    (1536, 1536, "1536x1536 ( 1:1 )"),
)
_SPACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
_CONTENT_TYPES = {".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg"}


@dataclass(frozen=True, slots=True)
class ZImageSpaceConfig:
    """The Space to call, the alias that names it, and how long one answer may take."""

    space_id: str
    model_alias: str
    http_timeout_seconds: float
    stream_seconds: float
    token: SecretStr | None = None

    def __post_init__(self) -> None:
        """Reject a malformed Space or unbounded waits before any request can leave."""
        if _SPACE_ID_PATTERN.fullmatch(self.space_id) is None:
            raise ValueError("Hugging Face Space id must look like owner/name")
        if not self.model_alias or self.model_alias != self.model_alias.strip():
            raise ValueError("Space model alias must not be blank")
        for seconds in (self.http_timeout_seconds, self.stream_seconds):
            if not math.isfinite(seconds) or seconds <= 0:
                raise ValueError("Space timeouts must be positive and finite")

    @property
    def origin(self) -> str:
        """The Space's own HTTPS host, which Hugging Face derives from its id."""
        subdomain = re.sub(r"[^a-z0-9]+", "-", self.space_id.casefold()).strip("-")
        return f"https://{subdomain}.hf.space"


class ZImageSpaceProvider:
    """Translate Gradio call events into provider-neutral still results."""

    def __init__(
        self,
        *,
        config: ZImageSpaceConfig,
        client: httpx.Client,
        utc_clock: Callable[[], datetime],
        monotonic: Callable[[], float],
    ) -> None:
        """Bind explicit network and clock dependencies for deterministic operation."""
        self._config = config
        self._client = client
        self._utc_clock = utc_clock
        self._monotonic = monotonic
        self._submissions: dict[str, tuple[GenerationRequest, GenerationHandle]] = {}

    def estimate(self, *, request: GenerationRequest) -> GenerationEstimate:
        """A Space bills nothing; the Workspace's image allowance still counts the still."""
        self._validate_request(request)
        return GenerationEstimate.image(
            output_count=request.output_count,
            width=request.width,
            height=request.height,
            latency_class=GenerationLatencyClass.STANDARD,
            provider_credits=Decimal(0),
            cost_usd=Decimal(0),
        )

    def submit(self, *, request: GenerationRequest, idempotency_key: str) -> GenerationHandle:
        """Queue one call and retain only its event id and the size it will come back at."""
        width, height, resolution = self._validate_request(request)
        if not idempotency_key.strip():
            raise GenerationRejectedError
        existing = self._submissions.get(idempotency_key)
        if existing is not None:
            previous_request, handle = existing
            if previous_request != request:
                raise GenerationRejectedError
            return handle

        seed = request.seed
        payload = {
            "data": [
                request.prompt,
                resolution,
                seed if seed is not None else 0,
                ZIMAGE_STEPS,
                ZIMAGE_TIME_SHIFT,
                seed is None,
                [],
            ]
        }
        response = self._request("POST", self._call_url(), json=payload)
        event_id = _json_object(response).get("event_id")
        if not isinstance(event_id, str) or not _safe_identity(event_id):
            raise GenerationInvalidResponseError
        handle = GenerationHandle(
            provider=ZIMAGE_SPACE_PROVIDER,
            provider_request_id=event_id,
            model=self._config.space_id,
            model_version=f"steps-{ZIMAGE_STEPS}",
            submitted_at=self._utc_clock(),
            output_width=width,
            output_height=height,
        )
        self._submissions[idempotency_key] = (request, handle)
        return handle

    def poll(self, *, handle: GenerationHandle) -> GenerationResult:
        """Hold the event stream open until the call finishes, fails, or runs out of time.

        Every way a stream can end without a picture is final. Gradio hands a result over
        once and forfeits it when the stream closes early, and a stream on a call that
        already answered only ever sends heartbeats, so reading it again would wait for an
        answer that can no longer come.
        """
        self._validate_handle(handle)
        url = f"{self._call_url()}/{quote(handle.provider_request_id, safe='')}"
        deadline = self._monotonic() + self._config.stream_seconds
        try:
            with self._client.stream(
                "GET",
                url,
                headers=self._headers(),
                timeout=self._config.http_timeout_seconds,
            ) as response:
                _raise_for_status(response.status_code)
                for event, data in _events(response.iter_lines()):
                    if event == "complete":
                        return self._decode_success(handle=handle, data=data)
                    if event == "error" or self._monotonic() >= deadline:
                        return _ended()
        except httpx.TimeoutException:
            return _ended()
        except httpx.HTTPError:
            raise GenerationUnavailableError from None
        return _ended()

    def cancel(self, *, handle: GenerationHandle) -> None:
        """A queued Gradio call cannot be withdrawn over HTTP; letting it finish costs nothing."""
        self._validate_handle(handle)

    def _decode_success(self, *, handle: GenerationHandle, data: str) -> GenerationResult:
        """Accept one image file served by this Space itself, and nothing else."""
        try:
            payload = json.loads(data)
        except ValueError:
            raise GenerationInvalidResponseError from None
        if not isinstance(payload, list) or len(payload) < 3 or not isinstance(payload[0], list):
            raise GenerationInvalidResponseError
        gallery = payload[0]
        if not gallery or not isinstance(gallery[0], dict):
            raise GenerationInvalidResponseError
        image = gallery[0].get("image")
        url = image.get("url") if isinstance(image, dict) else None
        prefix = f"{self._config.origin}/gradio_api/file="
        if not isinstance(url, str) or not url.startswith(prefix):
            raise GenerationInvalidResponseError
        content_type = _CONTENT_TYPES.get(url[url.rfind(".") :].casefold())
        if content_type is None or handle.output_width is None or handle.output_height is None:
            raise GenerationInvalidResponseError
        seed = payload[2]
        return GenerationResult(
            status=GenerationStatus.SUCCEEDED,
            output=GenerationOutput(
                url=SecretStr(url),
                content_type=content_type,
                width=handle.output_width,
                height=handle.output_height,
            ),
            usage=GenerationUsage(
                generated_images=1,
                generated_videos=0,
                generated_seconds=Decimal(0),
                provider_credits=Decimal(0),
                cost_usd=Decimal(0),
            ),
            seed=seed
            if isinstance(seed, int) and not isinstance(seed, bool) and seed >= 0
            else None,
            # The Space screens both the prompt and the picture before answering, and Clipah's
            # own moderator refused prohibited prompts before the call was queued.
            moderation=GenerationModerationResult.APPROVED,
        )

    def _validate_request(self, request: GenerationRequest) -> tuple[int, int, str]:
        """Apply still-only, prompt, model, and size policy before any HTTP call."""
        if request.media_kind is not GenerationMediaKind.IMAGE or request.output_count != 1:
            raise GenerationRejectedError
        PromptModerator().ensure_allowed(text=request.prompt)
        if request.model_alias != self._config.model_alias:
            raise GenerationUnknownModelError
        return _resolution_for(request.width, request.height)

    def _validate_handle(self, handle: GenerationHandle) -> None:
        """Refuse handles another provider, another Space, or an unsafe id produced."""
        if (
            handle.provider != ZIMAGE_SPACE_PROVIDER
            or handle.model != self._config.space_id
            or not _safe_identity(handle.provider_request_id)
        ):
            raise GenerationInvalidResponseError

    def _call_url(self) -> str:
        """The Gradio call route of the configured Space's one generation endpoint."""
        return f"{self._config.origin}/gradio_api/call/{ZIMAGE_API_NAME}"

    def _headers(self) -> dict[str, str]:
        """Spend the token's ZeroGPU allowance when one is configured, else the anonymous one."""
        if self._config.token is None:
            return {}
        return {"Authorization": f"Bearer {self._config.token.get_secret_value()}"}

    def _request(self, method: str, url: str, *, json: dict[str, Any]) -> httpx.Response:
        """Bound one call and collapse Space failures into fixed local errors."""
        try:
            response = self._client.request(
                method,
                url,
                headers=self._headers(),
                json=json,
                timeout=self._config.http_timeout_seconds,
            )
        except httpx.TimeoutException:
            raise GenerationTimeoutError from None
        except httpx.HTTPError:
            raise GenerationUnavailableError from None
        _raise_for_status(response.status_code)
        return response


def _resolution_for(width: int, height: int) -> tuple[int, int, str]:
    """The smallest size of the same shape that covers the request, else the largest one."""
    same_shape = [entry for entry in ZIMAGE_RESOLUTIONS if entry[0] * height == entry[1] * width]
    if not same_shape:
        raise GenerationRejectedError
    covering = [entry for entry in same_shape if entry[0] >= width]
    if covering:
        return min(covering, key=lambda entry: entry[0])
    return max(same_shape, key=lambda entry: entry[0])


def _raise_for_status(status: int) -> None:
    """Map a non-success HTTP status onto the fixed retryable and terminal errors."""
    if 200 <= status < 300:
        return
    if status == 429:
        raise GenerationRateLimitedError
    if status in {408, 504}:
        raise GenerationTimeoutError
    if status in {400, 404, 422}:
        raise GenerationRejectedError
    if status >= 500:
        raise GenerationUnavailableError
    raise GenerationInvalidResponseError


def _events(lines: Iterator[str]) -> Iterator[tuple[str, str]]:
    """Pair each server-sent `event:` with the `data:` line that follows it."""
    event = ""
    for line in lines:
        if line.startswith("event:"):
            event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            yield event, line.removeprefix("data:").strip()
            event = ""


def _ended() -> GenerationResult:
    """A call that ended without a picture: the Space errored, or the answer is gone.

    It is reported as a refusal rather than a failure, because a failure is retried by
    polling the same call again, and this call has nothing left to give.
    """
    return GenerationResult(
        status=GenerationStatus.REJECTED,
        output=None,
        usage=GenerationUsage.zero(),
        seed=None,
        moderation=GenerationModerationResult.PENDING,
    )


def _json_object(response: httpx.Response) -> dict[str, Any]:
    """Decode one JSON object without keeping the response text."""
    try:
        payload = response.json()
    except ValueError:
        raise GenerationInvalidResponseError from None
    if not isinstance(payload, dict):
        raise GenerationInvalidResponseError
    return payload


def _safe_identity(value: str) -> bool:
    """Permit opaque event ids without allowing URL path control characters."""
    return (
        bool(value)
        and len(value) <= 256
        and all(character.isalnum() or character in "-_." for character in value)
    )
