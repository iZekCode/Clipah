"""A minimal client for OpenAI-compatible chat completion endpoints, such as Gemini's.

B-roll planning and the context-safety check were written against the Groq SDK. This
client answers the same `create(**request)` call and returns an object read the same way
(`.id`, `.choices[0].message.content`, `.usage`), so either task can run on Gemini without
changing how it builds requests or validates answers. Failures carry the HTTP status and
a class name the callers already classify by.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

import httpx

GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
DEFAULT_TIMEOUT_SECONDS = 60.0


class ChatCompletionError(Exception):
    """A provider refused or never answered; the status is kept, the provider text is not."""

    def __init__(self, status_code: int | None) -> None:
        """Retain only the HTTP status, never the provider's message."""
        super().__init__("chat completion failed" if status_code is None else f"HTTP {status_code}")
        self.status_code = status_code


class ChatCompletionRateLimitError(ChatCompletionError):
    """HTTP 429: worth another attempt later."""


class ChatCompletionAPIStatusError(ChatCompletionError):
    """HTTP 5xx: the provider is unavailable, worth another attempt."""


class ChatCompletionBadRequestError(ChatCompletionError):
    """Any other 4xx: the request itself was refused, so repeating it cannot help."""


class ChatCompletionConnectionError(ChatCompletionError):
    """The provider could not be reached or did not answer in time."""


@dataclass(frozen=True, slots=True)
class Message:
    """The text of one choice."""

    content: str | None


@dataclass(frozen=True, slots=True)
class Choice:
    """One answer the provider offered."""

    message: Message


@dataclass(frozen=True, slots=True)
class Usage:
    """Token counts the provider reported, zero when it reported none."""

    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ChatCompletion:
    """The parts of a completion the callers read."""

    id: str
    choices: tuple[Choice, ...]
    usage: Usage
    # The model that answered, when a fallback chain chose it; empty otherwise.
    model: str = ""


class ChatCompletionsClient:
    """Post chat completion requests to one OpenAI-compatible base URL."""

    def __init__(self, *, base_url: str, api_key: str, http: httpx.Client | None = None) -> None:
        """Bind the endpoint and credential; tests pass their own transport."""
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._http = http or httpx.Client(follow_redirects=False)

    def create(self, **request: Any) -> ChatCompletion:
        """Submit one request; `timeout` bounds the HTTP call and is not sent to the provider."""
        timeout = request.pop("timeout", DEFAULT_TIMEOUT_SECONDS)
        try:
            response = self._http.post(
                self._url,
                json=request,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=timeout,
            )
        except httpx.HTTPError as error:
            raise ChatCompletionConnectionError(None) from error
        if response.status_code == 429:
            raise ChatCompletionRateLimitError(429)
        if response.status_code >= 500:
            raise ChatCompletionAPIStatusError(response.status_code)
        if response.status_code >= 400:
            raise ChatCompletionBadRequestError(response.status_code)
        return _completion(response)


class ModelFallbackCompletions:
    """Ask each model in order until one answers, stepping past busy ones only.

    A rate limit, an overload, or no answer moves on to the next model; a refused request
    does not, because another model would be asked the same wrong thing. The configured
    model comes first, and the answer records which model gave it.
    """

    def __init__(self, client: ChatCompletionsClient, *, models: Sequence[str]) -> None:
        """Bind the client and the models to try, first to last."""
        if not models:
            raise ValueError("a fallback chain needs at least one model")
        self._client = client
        self._models = tuple(models)

    def create(self, **request: Any) -> ChatCompletion:
        """Submit the request to each model in turn; `model` in the request is replaced."""
        for position, model in enumerate(self._models):
            try:
                response = self._client.create(**{**request, "model": model})
            except (
                ChatCompletionRateLimitError,
                ChatCompletionAPIStatusError,
                ChatCompletionConnectionError,
            ):
                if position == len(self._models) - 1:
                    raise
                continue
            return replace(response, model=model)
        raise AssertionError("unreachable")  # pragma: no cover


def _completion(response: httpx.Response) -> ChatCompletion:
    """Read what the callers need, leaving anything unusable empty for them to refuse."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return ChatCompletion(id="", choices=(), usage=Usage())
    choices = tuple(
        Choice(message=Message(content=_content(choice)))
        for choice in body.get("choices") or ()
        if isinstance(choice, dict)
    )
    reported = body.get("usage")
    usage: dict[str, Any] = reported if isinstance(reported, dict) else {}
    return ChatCompletion(
        id=body["id"] if isinstance(body.get("id"), str) else "",
        choices=choices,
        usage=Usage(
            prompt_tokens=_count(usage.get("prompt_tokens")),
            completion_tokens=_count(usage.get("completion_tokens")),
        ),
    )


def _content(choice: dict[str, Any]) -> str | None:
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, str) else None


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
