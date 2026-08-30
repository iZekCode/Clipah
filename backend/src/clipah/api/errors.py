"""Stable, sanitized API errors."""

from __future__ import annotations

from types import MappingProxyType

from starlette.responses import JSONResponse

from clipah.api.request_id import REQUEST_ID_HEADER


class ApiError(Exception):
    """A coded error explicitly intended for an API response."""

    def __init__(self, *, status_code: int, code: str, message: str | None = None) -> None:
        """Preserve a compatibility-only message argument without retaining it publicly."""
        del message
        super().__init__(code)
        self.status_code = status_code
        self.code = code


_PUBLIC_MESSAGES = MappingProxyType(
    {
        "NOT_FOUND": "The requested resource was not found.",
        "HTTP_ERROR": "The request could not be completed.",
        "VALIDATION_ERROR": "Request validation failed.",
        "SERVICE_UNAVAILABLE": "A required service is unavailable.",
        "INTERNAL_ERROR": "An unexpected error occurred.",
        "CONFLICT": "The resource changed.",
    }
)


def error_response(*, status_code: int, code: str, request_id: str) -> JSONResponse:
    """Build the one public error-envelope shape used by the HTTP application."""
    content = {
        "error": {
            "code": code,
            "message": _PUBLIC_MESSAGES.get(code, _PUBLIC_MESSAGES["HTTP_ERROR"]),
            "requestId": request_id,
        }
    }
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers={REQUEST_ID_HEADER: request_id},
    )
