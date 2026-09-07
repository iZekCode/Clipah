"""Provider-neutral context-safety assessment, and the Groq adapter behind it.

An assessor may only propose warnings keyed to the word IDs it was shown. It never decides
whether a proposal is valid: `context_safety.assess_context_with_proposals` does that
against the authoritative transcript, and discards whatever does not resolve.

A deployment with no credential configured has no assessor at all, which is an ordinary
state rather than a failure — the deterministic rules still run, exactly as B-roll falls
back to a Workspace's own footage when no stock provider is configured.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from clipah.config import Settings
from clipah.highlights.provider import ProviderCall
from clipah.transcripts.models import TranscriptWord
from clipah.variants.models import ContextWarningType

PROVIDER = "groq"
CONTEXT_ASSESS_OPERATION = "context_assess"
CONTEXT_PROMPT_VERSION = "variants/context/1"
CONTEXT_SCHEMA_VERSION = "context-warning/1"
REQUEST_TIMEOUT_SECONDS = 60.0
RETRY_BASE_DELAY_SECONDS = 0.5

RATE_LIMITED_CODE = "CONTEXT_PROVIDER_RATE_LIMITED"
UNAVAILABLE_CODE = "CONTEXT_PROVIDER_UNAVAILABLE"
REJECTED_CODE = "CONTEXT_PROVIDER_REJECTED"
INVALID_CODE = "CONTEXT_PROVIDER_INVALID"

_SYSTEM_PROMPT = (
    "You judge whether cutting a transcript at the given boundary would misrepresent the "
    "speaker. Refer to words only by the word IDs you were given. Never invent a word ID, "
    "never quote outside the span, and report only the warning types you were offered."
)


class ContextProviderRetryableError(Exception):
    """A temporary assessment failure a caller may retry through a stable code."""

    def __init__(self, code: str) -> None:
        """Retain only public-safe retry information."""
        self.code = code
        super().__init__(code)


class ContextProviderTerminalError(Exception):
    """A permanent assessment failure carrying only a stable code."""

    def __init__(self, code: str) -> None:
        """Retain only public-safe terminal information."""
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AssessmentResult:
    """Unvalidated warning proposals, with the provider call that produced them."""

    proposals: tuple[Mapping[str, Any], ...]
    call: ProviderCall


class ContextSafetyAssessor(Protocol):
    """Provider-independent capability to propose warnings about one boundary."""

    def assess(
        self,
        *,
        words: Sequence[TranscriptWord],
        start_word_id: str,
        end_word_id: str,
    ) -> AssessmentResult:
        """Propose warnings about one span, keyed to that transcript's word IDs."""


class _Completions(Protocol):
    """Structural subset of the Groq chat completions client used by this adapter."""

    def create(self, **request: Any) -> Any:
        """Submit one chat completion request."""


def context_warning_json_schema() -> dict[str, Any]:
    """Describe the only answer shape an assessment provider may return."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["warnings"],
        "properties": {
            "warnings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["type", "evidence_word_ids"],
                    "properties": {
                        "type": {"type": "string", "enum": [value for value in ContextWarningType]},
                        "evidence_word_ids": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "string"},
                        },
                        "suggested_start_word_id": {"type": ["string", "null"]},
                        "suggested_end_word_id": {"type": ["string", "null"]},
                    },
                },
            }
        },
    }


class GroqContextSafetyAssessor:
    """Translate Groq chat completions into unvalidated warning proposals."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        completions: _Completions | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 3,
    ) -> None:
        """Bind the configured model and an injectable clock, sleep, and client."""
        self._model = model
        self._completions = completions or _default_completions(api_key)
        self._clock = clock
        self._sleep = sleep
        self._max_attempts = max_attempts

    def assess(
        self,
        *,
        words: Sequence[TranscriptWord],
        start_word_id: str,
        end_word_id: str,
    ) -> AssessmentResult:
        """Ask the model which warnings this boundary deserves, under a closed schema."""
        prompt = _prompt(words=words, start_word_id=start_word_id, end_word_id=end_word_id)
        response, latency_ms = self._call(prompt)
        payload = _decoded(response)
        proposals = payload.get("warnings")
        if not isinstance(proposals, list):
            raise ContextProviderTerminalError(INVALID_CODE)
        return AssessmentResult(
            proposals=tuple(item for item in proposals if isinstance(item, dict)),
            call=ProviderCall(
                provider=PROVIDER,
                operation=CONTEXT_ASSESS_OPERATION,
                model=self._model,
                request_id=_request_id(response),
                latency_ms=latency_ms,
                input_units=len(words),
                output_units=len(proposals),
                prompt_version=CONTEXT_PROMPT_VERSION,
                schema_version=CONTEXT_SCHEMA_VERSION,
            ),
        )

    def _call(self, prompt: str) -> tuple[Any, int]:
        """Spend a bounded retry budget, mapping provider failures onto stable codes."""
        started = self._clock()
        last: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                response = self._completions.create(
                    model=self._model,
                    messages=(
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ),
                    response_format={"type": "json_object"},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            except Exception as error:
                last = error
                if not _is_retryable(error) or attempt == self._max_attempts - 1:
                    raise _mapped(error) from None
                self._sleep(RETRY_BASE_DELAY_SECONDS * (2**attempt))
                continue
            return response, int((self._clock() - started) * 1_000)
        raise _mapped(last) from None  # pragma: no cover - the loop always returns or raises


def _default_completions(api_key: str | None) -> _Completions:
    """Build the real client only when this deployment actually configured one."""
    from groq import Groq

    return cast(_Completions, Groq(api_key=api_key).chat.completions)


def _prompt(*, words: Sequence[TranscriptWord], start_word_id: str, end_word_id: str) -> str:
    """Show the model the span and its surroundings, addressed only by word ID."""
    lines = [f"{word.word_id}\t{word.text}{word.punctuation}" for word in words]
    return "\n".join(
        (
            f"Proposed cut: {start_word_id} through {end_word_id}.",
            "Transcript:",
            *lines,
            "Report every warning that applies, and nothing that does not.",
        )
    )


def _decoded(response: Any) -> dict[str, Any]:
    """Read one JSON object from the completion without retaining provider text."""
    try:
        content = response.choices[0].message.content
        payload = json.loads(content)
    except (AttributeError, IndexError, TypeError, ValueError):
        raise ContextProviderTerminalError(INVALID_CODE) from None
    if not isinstance(payload, dict):
        raise ContextProviderTerminalError(INVALID_CODE)
    return payload


def _request_id(response: Any) -> str:
    """Read the provider's own request identity, or record that it gave none."""
    identity = getattr(response, "id", None)
    return identity if isinstance(identity, str) else ""


def _is_retryable(error: Exception) -> bool:
    """Report whether one provider failure is worth another attempt."""
    name = type(error).__name__
    return "RateLimit" in name or "Connection" in name or "Timeout" in name or "APIStatus" in name


def _mapped(error: Exception | None) -> Exception:
    """Collapse a provider exception onto one of this module's stable codes."""
    name = type(error).__name__ if error is not None else ""
    if "RateLimit" in name:
        return ContextProviderRetryableError(RATE_LIMITED_CODE)
    if "Connection" in name or "Timeout" in name or "APIStatus" in name:
        return ContextProviderRetryableError(UNAVAILABLE_CODE)
    if "BadRequest" in name or "Permission" in name or "Authentication" in name:
        return ContextProviderTerminalError(REJECTED_CODE)
    return ContextProviderTerminalError(INVALID_CODE)


def configured_context_assessor(settings: Settings) -> ContextSafetyAssessor | None:
    """Build the assessor this deployment's credentials support, or report none.

    An absent credential is an ordinary state: the deterministic rules still run, and a
    member still sees every warning the transcript alone can establish.
    """
    api_key = settings.groq_api_key
    if api_key is None:
        return None
    return GroqContextSafetyAssessor(
        model=settings.groq_extraction_model,
        api_key=api_key.get_secret_value(),
    )
