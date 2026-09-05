"""Shared plumbing for the stock-provider adapters: transport, failures, and safe reads.

Both stock adapters face the same three problems — an HTTP call that may fail in ways a
member must never see, a payload whose shape is the provider's business, and a credential
that must never leave this layer. Solving them once keeps each adapter to the part that is
genuinely provider-specific: which endpoint, which filter names, and which payload shape.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from clipah.broll.retriever import (
    RETRIEVAL_INVALID_CODE,
    RETRIEVAL_RATE_LIMITED_CODE,
    RETRIEVAL_REJECTED_CODE,
    RETRIEVAL_UNAVAILABLE_CODE,
    BrollRetrievalRetryableError,
    BrollRetrievalTerminalError,
)

REQUEST_TIMEOUT_SECONDS = 15.0


class HttpTransport(Protocol):
    """The one HTTP capability a stock adapter needs, so tests never touch a network."""

    def get(self, url: str, *, params: dict[str, Any], headers: dict[str, str]) -> Any:
        """Perform one GET and return a response exposing ``status_code`` and ``json()``."""


class HttpxTransport:
    """The production transport, and the only place an HTTP client type appears."""

    def __init__(self, *, timeout: float = REQUEST_TIMEOUT_SECONDS) -> None:
        """Bind the request timeout every provider call is bounded by."""
        self._timeout = timeout

    def get(  # pragma: no cover - one real HTTP request, exercised by the live smoke test
        self, url: str, *, params: dict[str, Any], headers: dict[str, str]
    ) -> Any:
        """Perform one bounded GET without following the provider anywhere else."""
        import httpx

        return httpx.get(
            url,
            params=params,
            headers=headers,
            timeout=self._timeout,
            follow_redirects=False,
        )


def request_json(
    transport: HttpTransport,
    *,
    url: str,
    params: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    """Perform one provider request and return its body, or a public-safe failure code.

    Failures are classified by status alone. Nothing the provider wrote, and nothing from
    the request that carried the credential, is allowed into the raised error: these codes
    reach logs, traces, and job events.
    """
    try:
        response = transport.get(url, params=params, headers=headers)
    except Exception:
        raise BrollRetrievalRetryableError(RETRIEVAL_UNAVAILABLE_CODE) from None

    status = getattr(response, "status_code", None)
    if not isinstance(status, int) or isinstance(status, bool):
        raise BrollRetrievalTerminalError(RETRIEVAL_INVALID_CODE)
    if status == 429:
        raise BrollRetrievalRetryableError(RETRIEVAL_RATE_LIMITED_CODE)
    if status >= 500:
        raise BrollRetrievalRetryableError(RETRIEVAL_UNAVAILABLE_CODE)
    if status >= 400:
        raise BrollRetrievalTerminalError(RETRIEVAL_REJECTED_CODE)

    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError, AttributeError):
        raise BrollRetrievalTerminalError(RETRIEVAL_INVALID_CODE) from None
    if not isinstance(body, dict):
        raise BrollRetrievalTerminalError(RETRIEVAL_INVALID_CODE)
    return body


def results_of(body: dict[str, Any], key: str) -> tuple[dict[str, Any], ...]:
    """Read one provider's result list, treating any other shape as no results at all."""
    entries = body.get(key)
    if not isinstance(entries, list):
        return ()
    return tuple(entry for entry in entries if isinstance(entry, dict))


def text_of(entry: dict[str, Any], key: str) -> str:
    """Read one string field, treating anything else as absent rather than coercing it."""
    value = entry.get(key)
    return value.strip() if isinstance(value, str) else ""


def positive_int(value: Any) -> int:
    """Read one positive integer, treating anything else as zero."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return 0
    return int(value)
