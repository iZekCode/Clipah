"""Provider-neutral values and safety controls for generated B-roll.

This module describes generation without importing an SDK, making a network request, or
persisting provider data. Adapters translate their own payloads into these closed values;
callers receive stable usage, failure, moderation, and circuit-breaker semantics.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Protocol, Self, runtime_checkable
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from clipah.broll.models import VisualIntent

GENERATION_UNAVAILABLE_CODE = "GENERATION_PROVIDER_UNAVAILABLE"
GENERATION_RATE_LIMITED_CODE = "GENERATION_PROVIDER_RATE_LIMITED"
GENERATION_TIMEOUT_CODE = "GENERATION_PROVIDER_TIMEOUT"
GENERATION_REJECTED_CODE = "GENERATION_PROVIDER_REJECTED"
GENERATION_MODERATION_REJECTED_CODE = "GENERATION_MODERATION_REJECTED"
GENERATION_INVALID_RESPONSE_CODE = "GENERATION_PROVIDER_INVALID"
GENERATION_UNKNOWN_MODEL_CODE = "GENERATION_UNKNOWN_MODEL"
GENERATION_CIRCUIT_OPEN_CODE = "GENERATION_CIRCUIT_OPEN"
GENERATION_CONFIRMATION_INVALID_CODE = "GENERATION_CONFIRMATION_INVALID"

PROHIBITED_SAFETY_CLASSES = frozenset(
    {
        "impersonation",
        "sexual_content_involving_minors",
        "deceptive_real_person_claims",
    }
)


class GenerationMediaKind(StrEnum):
    """Media shapes generated-media providers may return."""

    IMAGE = "image"
    VIDEO = "video"


class GenerationLatencyClass(StrEnum):
    """Provider-neutral bands suitable for estimates shown before confirmation."""

    STANDARD = "standard"
    SLOW = "slow"


class GenerationStatus(StrEnum):
    """Normalized asynchronous states from submission through a terminal outcome."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    MODERATION_REJECTED = "moderation_rejected"
    CANCELED = "canceled"
    FAILED = "failed"


class GenerationModerationResult(StrEnum):
    """The safety evidence a provider supplied for one generation result."""

    PENDING = "pending"
    APPROVED = "approved"
    INPUT_REJECTED = "input_rejected"
    OUTPUT_REJECTED = "output_rejected"


class CircuitState(StrEnum):
    """Whether a provider/model circuit admits ordinary calls or one recovery probe."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class _GenerationValue(BaseModel):
    """Apply immutability and closed schemas to every provider-neutral value."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class GenerationRequest(_GenerationValue):
    """One complete server-owned request an adapter may estimate or submit."""

    prompt: str = Field(min_length=1, strict=True)
    media_kind: GenerationMediaKind
    output_count: int = Field(gt=0, strict=True)
    duration_ms: int | None = Field(default=None, gt=0, strict=True)
    width: int = Field(gt=0, strict=True)
    height: int = Field(gt=0, strict=True)
    model_alias: str = Field(min_length=1, strict=True)
    seed: int | None = Field(default=None, ge=0, strict=True)

    @field_validator("prompt", "model_alias")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        """Reject whitespace-only or ambiguously padded request text."""
        if not value.strip() or value != value.strip():
            raise ValueError("generation request text must be non-blank and unpadded")
        return value

    @model_validator(mode="after")
    def validate_duration_for_media_kind(self) -> Self:
        """Images have no duration while every video owns a positive duration."""
        if self.media_kind is GenerationMediaKind.IMAGE and self.duration_ms is not None:
            raise ValueError("generated images cannot carry a duration")
        if self.media_kind is GenerationMediaKind.VIDEO and self.duration_ms is None:
            raise ValueError("generated videos require a duration")
        return self


class GenerationEstimate(_GenerationValue):
    """A provider estimate normalized into quota units and decimal money."""

    media_kind: GenerationMediaKind
    output_count: int = Field(gt=0, strict=True)
    duration_ms: int | None = Field(default=None, gt=0, strict=True)
    width: int = Field(gt=0, strict=True)
    height: int = Field(gt=0, strict=True)
    latency_class: GenerationLatencyClass
    image_units: Decimal = Field(ge=0, allow_inf_nan=False, strict=True)
    video_units: Decimal = Field(ge=0, allow_inf_nan=False, strict=True)
    generated_seconds: Decimal = Field(ge=0, allow_inf_nan=False, strict=True)
    provider_credits: Decimal = Field(ge=0, allow_inf_nan=False, strict=True)
    cost_usd: Decimal = Field(ge=0, allow_inf_nan=False, strict=True)

    @classmethod
    def image(
        cls,
        *,
        output_count: int,
        width: int,
        height: int,
        latency_class: GenerationLatencyClass,
        provider_credits: Decimal,
        cost_usd: Decimal,
    ) -> GenerationEstimate:
        """Construct a still estimate with its image quota units derived once."""
        return cls(
            media_kind=GenerationMediaKind.IMAGE,
            output_count=output_count,
            duration_ms=None,
            width=width,
            height=height,
            latency_class=latency_class,
            image_units=Decimal(output_count),
            video_units=Decimal(0),
            generated_seconds=Decimal(0),
            provider_credits=provider_credits,
            cost_usd=cost_usd,
        )

    @classmethod
    def video(
        cls,
        *,
        output_count: int,
        duration_ms: int,
        width: int,
        height: int,
        latency_class: GenerationLatencyClass,
        provider_credits: Decimal,
        cost_usd: Decimal,
    ) -> GenerationEstimate:
        """Construct a video estimate with output and duration quota units derived once."""
        seconds = Decimal(duration_ms) / Decimal(1_000)
        return cls(
            media_kind=GenerationMediaKind.VIDEO,
            output_count=output_count,
            duration_ms=duration_ms,
            width=width,
            height=height,
            latency_class=latency_class,
            image_units=Decimal(0),
            video_units=Decimal(output_count),
            generated_seconds=seconds * Decimal(output_count),
            provider_credits=provider_credits,
            cost_usd=cost_usd,
        )

    @model_validator(mode="after")
    def validate_normalized_units(self) -> Self:
        """Refuse estimates whose quota counters disagree with their requested media."""
        if self.media_kind is GenerationMediaKind.IMAGE:
            expected = (Decimal(self.output_count), Decimal(0), Decimal(0))
            if self.duration_ms is not None:
                raise ValueError("generated image estimates cannot carry a duration")
        else:
            if self.duration_ms is None:
                raise ValueError("generated video estimates require a duration")
            expected = (
                Decimal(0),
                Decimal(self.output_count),
                Decimal(self.duration_ms) * Decimal(self.output_count) / Decimal(1_000),
            )
        actual = (self.image_units, self.video_units, self.generated_seconds)
        if actual != expected:
            raise ValueError("generation estimate quota units are inconsistent")
        return self


class GenerationHandle(_GenerationValue):
    """The opaque provider identity sufficient to resume or cancel one request.

    A provider whose own status payload omits output geometry records the requested
    geometry here, so a worker resuming in a later process can still normalize the
    result without holding the original request in memory.
    """

    provider: str = Field(min_length=1, strict=True)
    provider_request_id: str = Field(min_length=1, strict=True)
    model: str = Field(min_length=1, strict=True)
    model_version: str = Field(min_length=1, strict=True)
    submitted_at: datetime
    output_width: int | None = Field(default=None, gt=0, strict=True)
    output_height: int | None = Field(default=None, gt=0, strict=True)
    output_duration_ms: int | None = Field(default=None, gt=0, strict=True)

    @field_validator("submitted_at")
    @classmethod
    def require_utc_instant(cls, value: datetime) -> datetime:
        """Keep persisted submission instants aware and normalized to UTC."""
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("generation submission time must be timezone-aware UTC")
        return value


class GenerationUsage(_GenerationValue):
    """Actual provider usage normalized for exact quota settlement and reporting."""

    generated_images: int = Field(ge=0, strict=True)
    generated_videos: int = Field(ge=0, strict=True)
    generated_seconds: Decimal = Field(ge=0, allow_inf_nan=False, strict=True)
    provider_credits: Decimal = Field(ge=0, allow_inf_nan=False, strict=True)
    cost_usd: Decimal = Field(ge=0, allow_inf_nan=False, strict=True)

    @classmethod
    def zero(cls) -> GenerationUsage:
        """Represent a provider outcome that consumed no reported billable usage."""
        return cls(
            generated_images=0,
            generated_videos=0,
            generated_seconds=Decimal(0),
            provider_credits=Decimal(0),
            cost_usd=Decimal(0),
        )


class GenerationOutput(_GenerationValue):
    """One ephemeral output capability plus the metadata required to validate it."""

    url: SecretStr
    content_type: str = Field(min_length=1, strict=True)
    width: int = Field(gt=0, strict=True)
    height: int = Field(gt=0, strict=True)
    duration_ms: int | None = Field(default=None, gt=0, strict=True)

    @field_validator("url")
    @classmethod
    def reject_blank_capability(cls, value: SecretStr) -> SecretStr:
        """An absent output capability cannot masquerade as a successful result."""
        if not value.get_secret_value().strip():
            raise ValueError("generation output capability cannot be blank")
        return value


class GenerationResult(_GenerationValue):
    """One normalized poll result with mandatory terminal safety evidence."""

    status: GenerationStatus
    output: GenerationOutput | None = None
    usage: GenerationUsage
    seed: int | None = Field(default=None, ge=0, strict=True)
    moderation: GenerationModerationResult

    @model_validator(mode="after")
    def validate_terminal_evidence(self) -> Self:
        """Require an output and affirmative moderation for every reported success."""
        if self.status is GenerationStatus.SUCCEEDED:
            if self.output is None:
                raise ValueError("successful generation requires an output")
            if self.moderation is not GenerationModerationResult.APPROVED:
                raise ValueError("successful generation requires approved moderation evidence")
        elif self.output is not None:
            raise ValueError("only successful generation may carry an output")

        rejected = {
            GenerationModerationResult.INPUT_REJECTED,
            GenerationModerationResult.OUTPUT_REJECTED,
        }
        if self.status is GenerationStatus.MODERATION_REJECTED and self.moderation not in rejected:
            raise ValueError("moderation rejection requires input or output rejection evidence")
        return self


@runtime_checkable
class GenerativeMediaProvider(Protocol):
    """The complete provider-neutral asynchronous generation boundary."""

    def estimate(self, *, request: GenerationRequest) -> GenerationEstimate:
        """Return normalized expected cost and usage without starting generation."""

    def submit(self, *, request: GenerationRequest, idempotency_key: str) -> GenerationHandle:
        """Start or idempotently resume one provider request."""

    def poll(self, *, handle: GenerationHandle) -> GenerationResult:
        """Read the current normalized state of one submitted request."""

    def cancel(self, *, handle: GenerationHandle) -> None:
        """Best-effort cancel a provider request, idempotently."""


class GenerationProviderError(Exception):
    """A provider failure carrying only a fixed public-safe code."""

    def __init__(self, code: str) -> None:
        """Keep raw provider messages, payloads, and URLs out of the failure."""
        super().__init__(code)
        self.code = code


class GenerationProviderRetryableError(GenerationProviderError):
    """A temporary provider condition a durable Job may retry later."""


class GenerationProviderTerminalError(GenerationProviderError):
    """A provider or request condition that retrying cannot repair."""


class GenerationUnavailableError(GenerationProviderRetryableError):
    """The configured provider cannot currently accept work."""

    def __init__(self) -> None:
        """Use the fixed unavailable code."""
        super().__init__(GENERATION_UNAVAILABLE_CODE)


class GenerationRateLimitedError(GenerationProviderRetryableError):
    """The configured provider has temporarily throttled work."""

    def __init__(self) -> None:
        """Use the fixed rate-limit code."""
        super().__init__(GENERATION_RATE_LIMITED_CODE)


class GenerationTimeoutError(GenerationProviderRetryableError):
    """The configured provider did not answer inside the bounded attempt."""

    def __init__(self) -> None:
        """Use the fixed timeout code."""
        super().__init__(GENERATION_TIMEOUT_CODE)


class GenerationRejectedError(GenerationProviderTerminalError):
    """The provider permanently rejected an otherwise local-safe request."""

    def __init__(self) -> None:
        """Use the fixed provider-rejection code."""
        super().__init__(GENERATION_REJECTED_CODE)


class PromptRejectedError(GenerationProviderTerminalError):
    """Clipah moderation rejected a prohibited request before submission."""

    def __init__(self) -> None:
        """Use the fixed moderation-rejection code."""
        super().__init__(GENERATION_MODERATION_REJECTED_CODE)


class GenerationInvalidResponseError(GenerationProviderTerminalError):
    """The provider returned data that cannot satisfy the generation contract."""

    def __init__(self) -> None:
        """Use the fixed invalid-response code."""
        super().__init__(GENERATION_INVALID_RESPONSE_CODE)


class GenerationUnknownModelError(GenerationProviderTerminalError):
    """A request named no server-configured allowlisted model alias."""

    def __init__(self) -> None:
        """Use the fixed unknown-model code."""
        super().__init__(GENERATION_UNKNOWN_MODEL_CODE)


class GenerationCircuitOpenError(GenerationProviderRetryableError):
    """The local provider/model circuit is cooling down after failures."""

    def __init__(self) -> None:
        """Use the fixed open-circuit code."""
        super().__init__(GENERATION_CIRCUIT_OPEN_CODE)


class GenerationConfirmationInvalidError(GenerationProviderTerminalError):
    """A confirmation was unsealed, expired, or agreed by another User or Workspace."""

    def __init__(self) -> None:
        """Use the fixed confirmation-invalid code."""
        super().__init__(GENERATION_CONFIRMATION_INVALID_CODE)


class GenerationConfirmation(_GenerationValue):
    """One User's sealed agreement to a complete request at a shown price."""

    version: Literal[1] = 1
    workspace_id: UUID
    user_id: UUID
    suggestion_id: UUID
    request: GenerationRequest
    estimate: GenerationEstimate
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def require_utc_expiry(cls, value: datetime) -> datetime:
        """Keep the agreed lifetime unambiguous across processes and time zones."""
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("generation confirmation expiry must be timezone-aware UTC")
        return value

    @model_validator(mode="after")
    def validate_estimate_matches_request(self) -> Self:
        """A price is only an agreement while it prices the request it was sealed with."""
        if (
            self.estimate.media_kind is not self.request.media_kind
            or self.estimate.output_count != self.request.output_count
            or self.estimate.duration_ms != self.request.duration_ms
            or self.estimate.width != self.request.width
            or self.estimate.height != self.request.height
        ):
            raise ValueError("generation confirmation estimate does not price its request")
        return self


def seal_generation_confirmation(confirmation: GenerationConfirmation, *, secret: str) -> str:
    """Seal one confirmation so only this deployment can have issued it."""
    payload = confirmation.model_dump_json().encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return f"{_b64url(payload)}.{_b64url(signature)}"


def open_generation_confirmation(
    token: str,
    *,
    secret: str,
    now: datetime,
) -> GenerationConfirmation:
    """Return the sealed confirmation, refusing anything tampered with or expired."""
    encoded_payload, _, encoded_signature = token.partition(".")
    if not encoded_payload or not encoded_signature:
        raise GenerationConfirmationInvalidError
    try:
        payload = _b64url_decode(encoded_payload)
        signature = _b64url_decode(encoded_signature)
    except ValueError:
        raise GenerationConfirmationInvalidError from None
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise GenerationConfirmationInvalidError
    try:
        confirmation = GenerationConfirmation.model_validate_json(payload)
    except ValidationError:
        raise GenerationConfirmationInvalidError from None
    if now >= confirmation.expires_at:
        raise GenerationConfirmationInvalidError
    return confirmation


def _b64url(value: bytes) -> str:
    """Encode signed bytes without padding a reader would have to reproduce."""
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    """Decode exactly one canonical unpadded base64url segment."""
    if not value.isascii() or any(character in value for character in "+/="):
        raise ValueError("confirmation segment is not canonical base64url")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class PromptModerator:
    """Construct deterministic prompts and reject Clipah's prohibited safety classes."""

    def build_prompt(self, *, intent: VisualIntent) -> str:
        """Return the canonical prompt for one safe Visual Intent."""
        values = intent.model_dump(mode="python")
        for value in values.values():
            if isinstance(value, tuple):
                for item in value:
                    self.ensure_allowed(text=item)
            elif isinstance(value, str):
                self.ensure_allowed(text=value)

        composition = "portrait-suitable" if intent.portrait_suitable else "landscape-suitable"
        exclusions = "; ".join(intent.exclusions) or "none"
        risks = "; ".join(intent.factual_risk_flags) or "none"
        return "\n".join(
            (
                f"Subject: {intent.subject}.",
                f"Action: {intent.action}.",
                f"Setting: {intent.setting}.",
                f"Mood: {intent.mood}.",
                f"Composition: {composition}.",
                f"Indonesian context: {'; '.join(intent.search_terms_id)}.",
                f"English context: {'; '.join(intent.search_terms_en)}.",
                f"Exclude: {exclusions}.",
                f"Factual-risk constraints: {risks}.",
            )
        )

    def ensure_allowed(self, *, text: str) -> None:
        """Reject text containing any normalized prohibited safety class."""
        normalized = re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")
        if any(safety_class in normalized for safety_class in PROHIBITED_SAFETY_CLASSES):
            raise PromptRejectedError


class CircuitBreaker:
    """Bound consecutive retryable failures for one provider/model pair."""

    def __init__(
        self,
        *,
        failure_threshold: int,
        cooldown_seconds: float,
        monotonic: Callable[[], float],
    ) -> None:
        """Bind positive policy values and the injected monotonic clock."""
        if failure_threshold <= 0:
            raise ValueError("circuit failure threshold must be positive")
        if cooldown_seconds <= 0:
            raise ValueError("circuit cooldown must be positive")
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._monotonic = monotonic
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._half_open_probe_active = False

    @property
    def state(self) -> CircuitState:
        """Expose the stable state for health metrics and deterministic tests."""
        return self._state

    def before_call(self) -> None:
        """Admit an ordinary call, one due recovery probe, or raise open circuit."""
        if self._state is CircuitState.CLOSED:
            return
        if self._state is CircuitState.HALF_OPEN:
            raise GenerationCircuitOpenError
        if self._opened_at is None:
            raise GenerationCircuitOpenError
        if self._monotonic() - self._opened_at < self._cooldown_seconds:
            raise GenerationCircuitOpenError
        self._state = CircuitState.HALF_OPEN
        self._half_open_probe_active = True

    def record_failure(self) -> None:
        """Record one retryable failure and open or re-open at the threshold."""
        if self._state is CircuitState.HALF_OPEN:
            self._open()
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._failure_threshold:
            self._open()

    def record_success(self) -> None:
        """Close the circuit and forget failures after any successful provider call."""
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = None
        self._half_open_probe_active = False

    def _open(self) -> None:
        """Start a fresh cooldown from the injected monotonic instant."""
        self._state = CircuitState.OPEN
        self._opened_at = self._monotonic()
        self._half_open_probe_active = False


class FakeGenerativeMediaProvider:
    """Deterministic adapter for contract, use-case, and worker tests."""

    def __init__(
        self,
        *,
        estimate: GenerationEstimate,
        result: GenerationResult,
        utc_clock: Callable[[], datetime],
        models: Mapping[str, str] | None = None,
    ) -> None:
        """Bind fixed outcomes, an injected UTC clock, and allowlisted aliases."""
        self._estimate = estimate
        self._result = result
        self._utc_clock = utc_clock
        self._models = dict(
            models
            or {
                "image-default": "fake/image",
                "video-default": "fake/video",
            }
        )
        self._requests_by_key: dict[str, tuple[GenerationRequest, GenerationHandle]] = {}
        self._handles: dict[str, GenerationHandle] = {}
        self._canceled: set[str] = set()
        self.submit_count = 0
        self.cancel_count = 0

    def estimate(self, *, request: GenerationRequest) -> GenerationEstimate:
        """Return the fixed estimate after applying the real pre-submit guards."""
        self._validate_request(request)
        if self._estimate.media_kind is not request.media_kind:
            raise GenerationInvalidResponseError
        return self._estimate

    def submit(self, *, request: GenerationRequest, idempotency_key: str) -> GenerationHandle:
        """Create exactly one handle for each non-blank idempotency key and request."""
        self._validate_request(request)
        if not idempotency_key.strip():
            raise GenerationRejectedError
        existing = self._requests_by_key.get(idempotency_key)
        if existing is not None:
            previous_request, handle = existing
            if previous_request != request:
                raise GenerationRejectedError
            return handle

        submitted_at = self._utc_clock()
        model = self._models[request.model_alias]
        self.submit_count += 1
        handle = GenerationHandle(
            provider="fake",
            provider_request_id=f"fake-request-{self.submit_count}",
            model=model,
            model_version="test-1",
            submitted_at=submitted_at,
        )
        self._requests_by_key[idempotency_key] = (request, handle)
        self._handles[handle.provider_request_id] = handle
        return handle

    def poll(self, *, handle: GenerationHandle) -> GenerationResult:
        """Return a canceled terminal result or the configured normalized result."""
        self._require_handle(handle)
        if handle.provider_request_id in self._canceled:
            return GenerationResult(
                status=GenerationStatus.CANCELED,
                output=None,
                usage=GenerationUsage.zero(),
                seed=None,
                moderation=GenerationModerationResult.PENDING,
            )
        return self._result

    def cancel(self, *, handle: GenerationHandle) -> None:
        """Mark one known request canceled once, making repeated calls harmless."""
        self._require_handle(handle)
        if handle.provider_request_id not in self._canceled:
            self._canceled.add(handle.provider_request_id)
            self.cancel_count += 1

    def _validate_request(self, request: GenerationRequest) -> None:
        """Apply model and prompt safety gates before any fake provider operation."""
        if request.model_alias not in self._models or "sora" in request.model_alias.casefold():
            raise GenerationUnknownModelError
        model = self._models[request.model_alias]
        if "sora" in model.casefold():
            raise GenerationUnknownModelError
        PromptModerator().ensure_allowed(text=request.prompt)

    def _require_handle(self, handle: GenerationHandle) -> None:
        """Reject handles this fake did not issue instead of fabricating state."""
        if self._handles.get(handle.provider_request_id) != handle:
            raise GenerationInvalidResponseError
