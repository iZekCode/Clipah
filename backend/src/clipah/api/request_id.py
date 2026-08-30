"""Request correlation identifiers for HTTP responses."""

from __future__ import annotations

import re
from uuid import uuid4

from starlette.requests import Request

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def assign_request_id(request: Request) -> str:
    """Store and return a safe request ID for one HTTP request."""
    request_id = _safe_request_id(request.headers.get(REQUEST_ID_HEADER))
    request.state.request_id = request_id
    return request_id


def request_id_for(request: Request) -> str:
    """Return the request's correlation ID, creating one for direct handler use."""
    request_id = getattr(request.state, "request_id", None)
    if isinstance(request_id, str):
        return request_id
    return assign_request_id(request)


def _safe_request_id(value: str | None) -> str:
    """Accept only bounded token-like IDs that are safe to reflect in a header."""
    if value is not None and _SAFE_REQUEST_ID.fullmatch(value):
        return value
    return uuid4().hex
