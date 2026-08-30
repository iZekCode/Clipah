"""Stable, sanitized API errors."""

from __future__ import annotations

from typing import Any

from starlette.responses import JSONResponse

from clipah.api.request_id import REQUEST_ID_HEADER


class ApiError(Exception):
    """A safe error explicitly intended for an API response."""

    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message


def error_response(*, status_code: int, code: str, message: str, request_id: str) -> JSONResponse:
    """Build the one public error-envelope shape used by the HTTP application."""
    content: dict[str, Any] = {"error": {"code": code, "message": message, "requestId": request_id}}
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers={REQUEST_ID_HEADER: request_id},
    )
