"""Stable, sanitized API errors."""

from __future__ import annotations

from types import MappingProxyType

from starlette.responses import JSONResponse

from clipah.api.request_id import REQUEST_ID_HEADER


class ApiError(Exception):
    """A coded error explicitly intended for an API response."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        """Preserve a compatibility-only message argument without retaining it publicly."""
        del message
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.retry_after_seconds = retry_after_seconds


_PUBLIC_MESSAGES = MappingProxyType(
    {
        "NOT_FOUND": "The requested resource was not found.",
        "HTTP_ERROR": "The request could not be completed.",
        "VALIDATION_ERROR": "Request validation failed.",
        "SERVICE_UNAVAILABLE": "A required service is unavailable.",
        "INTERNAL_ERROR": "An unexpected error occurred.",
        "CONFLICT": "The resource changed.",
        "UNAUTHENTICATED": "Authentication is required.",
        "AUTHENTICATION_FAILED": "Authentication could not be completed.",
        "IDENTITY_CONFLICT": "This email address already belongs to another sign-in method.",
        "ACCOUNT_DISABLED": "This account can no longer sign in.",
        "CSRF_FAILED": "The request could not be verified.",
        "FORBIDDEN": "This action is not permitted for your role.",
        "RECENT_AUTHENTICATION_REQUIRED": "Sign in again to complete this action.",
        "RATE_LIMITED": "Too many requests. Try again shortly.",
        "QUOTA_EXCEEDED": "This workspace has used its plan allowance for this period.",
        "CONCURRENCY_LIMIT": "This workspace already has the maximum number of jobs running.",
        "SOURCE_UNSUPPORTED": "This public video source is not supported.",
        "SOURCE_PRIVATE": "This video is not publicly accessible.",
        "SOURCE_TOO_LONG": "This video exceeds the supported duration.",
        "SOURCE_TLS_FAILED": "The video source could not be verified securely.",
        "SOURCE_UNAVAILABLE": "The video source is temporarily unavailable.",
    }
)


def error_response(
    *, status_code: int, code: str, request_id: str, retry_after_seconds: int | None = None
) -> JSONResponse:
    """Build the one public error-envelope shape used by the HTTP application."""
    content = {
        "error": {
            "code": code,
            "message": _PUBLIC_MESSAGES.get(code, _PUBLIC_MESSAGES["HTTP_ERROR"]),
            "requestId": request_id,
        }
    }
    headers = {REQUEST_ID_HEADER: request_id}
    if retry_after_seconds is not None:
        headers["Retry-After"] = str(retry_after_seconds)
    return JSONResponse(status_code=status_code, content=content, headers=headers)
