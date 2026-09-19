"""The shared client for OpenAI-compatible chat completion endpoints such as Gemini's."""

from __future__ import annotations

import json

import httpx
import pytest

from clipah.language_models.chat_completions import (
    ChatCompletionAPIStatusError,
    ChatCompletionBadRequestError,
    ChatCompletionConnectionError,
    ChatCompletionRateLimitError,
    ChatCompletionsClient,
    ModelFallbackCompletions,
)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


def _client(handler: httpx.MockTransport) -> ChatCompletionsClient:
    return ChatCompletionsClient(
        base_url=BASE_URL, api_key="test-key", http=httpx.Client(transport=handler)
    )


@pytest.mark.unit
def test_a_completion_is_read_the_way_the_callers_already_read_the_sdk() -> None:
    """Callers read `.id`, `.choices[0].message.content`, and `.usage` token counts."""
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": "resp-1",
                "choices": [{"message": {"content": '{"beats": []}'}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 8},
            },
        )

    response = _client(httpx.MockTransport(answer)).create(
        model="gemini-3.8-flash",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0,
        timeout=30,
        response_format={"type": "json_object"},
    )

    assert response.id == "resp-1"
    assert response.choices[0].message.content == '{"beats": []}'
    assert (response.usage.prompt_tokens, response.usage.completion_tokens) == (120, 8)
    request = seen[0]
    assert str(request.url) == f"{BASE_URL}/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    # The timeout governs the HTTP call; it is not part of the request the provider sees.
    assert body == {
        "model": "gemini-3.8-flash",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "error"),
    [
        (429, ChatCompletionRateLimitError),
        (500, ChatCompletionAPIStatusError),
        (503, ChatCompletionAPIStatusError),
        (400, ChatCompletionBadRequestError),
        (401, ChatCompletionBadRequestError),
    ],
)
def test_a_refusal_carries_its_status_and_a_name_callers_classify_by(
    status: int, error: type[Exception]
) -> None:
    """Callers classify by `status_code` or by the error's class name, as they did with the SDK."""
    client = _client(httpx.MockTransport(lambda _: httpx.Response(status, text="no")))

    with pytest.raises(error) as raised:
        client.create(model="m", messages=[])

    assert raised.value.status_code == status


@pytest.mark.unit
def test_an_unreachable_provider_is_a_connection_error_without_a_status() -> None:
    """A transport failure is retryable, and has no status to classify by."""

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ChatCompletionConnectionError) as raised:
        _client(httpx.MockTransport(fail)).create(model="m", messages=[])

    assert raised.value.status_code is None


@pytest.mark.unit
def test_an_answer_that_is_not_a_completion_reads_as_empty() -> None:
    """A garbled body leaves the caller's own validation to refuse it."""
    client = _client(httpx.MockTransport(lambda _: httpx.Response(200, content=b"<html>")))

    response = client.create(model="m", messages=[])

    assert response.choices == ()
    assert response.id == ""


def _answering(outcomes: dict[str, int]) -> tuple[ChatCompletionsClient, list[str]]:
    """A client whose status depends on the model asked; 200 answers with an empty object."""
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        asked.append(model)
        status = outcomes.get(model, 200)
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, json={"id": "r", "choices": [{"message": {"content": "{}"}}]})

    return _client(httpx.MockTransport(answer)), asked


@pytest.mark.unit
def test_an_overloaded_model_falls_through_to_the_next_one() -> None:
    """Gemini's newest model is often busy; an older one answering is better than none."""
    client, asked = _answering({"gemini-3.8-flash": 503, "gemini-3.7-flash": 429})
    chain = ModelFallbackCompletions(
        client, models=("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash")
    )

    response = chain.create(model="ignored", messages=[])

    assert asked == ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash"]
    # The caller records the model that actually answered.
    assert response.model == "gemini-3.6-flash"


@pytest.mark.unit
def test_a_refused_request_does_not_fall_through() -> None:
    """A 4xx means the request is wrong; asking another model the same thing cannot help."""
    client, asked = _answering({"gemini-3.8-flash": 400})
    chain = ModelFallbackCompletions(client, models=("gemini-3.8-flash", "gemini-3.7-flash"))

    with pytest.raises(ChatCompletionBadRequestError):
        chain.create(model="ignored", messages=[])

    assert asked == ["gemini-3.8-flash"]


@pytest.mark.unit
def test_when_every_model_is_busy_the_last_failure_is_raised() -> None:
    """The caller's own retry budget then decides whether to try the chain again."""
    client, _ = _answering({"a": 503, "b": 503})

    with pytest.raises(ChatCompletionAPIStatusError):
        ModelFallbackCompletions(client, models=("a", "b")).create(model="a", messages=[])
