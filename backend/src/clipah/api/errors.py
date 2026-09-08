"""Stable, sanitized API errors."""

from __future__ import annotations

from collections.abc import Mapping
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
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Preserve a compatibility-only message argument without retaining it publicly."""
        del message
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        # Only values the application computed itself belong here; never provider text.
        self.headers = dict(headers or {})


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
        "EDIT_REVISION_CONFLICT": "This clip changed since you opened it.",
        "REVIEW_INVALID": "This review could not be attached to that revision.",
        "REVIEW_APPROVAL_REQUIRED": "Approve the current revision before continuing.",
        "COMPOSITION_INVALID": "This edit could not be saved as a valid composition.",
        "COMPOSITION_ASSET_FORBIDDEN": "This edit uses media that is not available here.",
        "GENERATION_WEBHOOK_INVALID": "This delivery could not be verified.",
        "GENERATION_CONFIRMATION_INVALID": "This estimate is no longer valid. Ask for a new one.",
        "GENERATION_CONFIRMATION_REQUIRED": "Generating video needs a separate confirmation.",
        "GENERATION_NOT_ELIGIBLE": "This suggestion cannot be generated right now.",
        "GENERATION_REQUEST_REFUSED": "This suggestion cannot be sent to a generative model.",
        "VARIANT_REQUEST_INVALID": "That combination of lengths and platforms is not supported.",
        "EVIDENCE_INVALID": "That source could not be accepted. Check the link and the text.",
        "BRAND_DEFINITION_INVALID": "This brand definition could not be saved.",
        "BRAND_ASSET_FORBIDDEN": "This brand uses media that is not available here.",
        "BRAND_KIT_ARCHIVED": "This brand kit has been archived.",
        "TEMPLATE_ARCHIVED": "This template has been archived.",
        "SOCIAL_OAUTH_INVALID": "This social authorization could not be completed.",
        "SOCIAL_SCOPE_MISSING": "The required provider permissions were not granted.",
        "SOCIAL_ACCOUNT_RECONNECT_REQUIRED": "Reconnect this social account to continue.",
    }
)


def error_response(
    *,
    status_code: int,
    code: str,
    request_id: str,
    retry_after_seconds: int | None = None,
    extra_headers: Mapping[str, str] | None = None,
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
    headers.update(extra_headers or {})
    return JSONResponse(status_code=status_code, content=content, headers=headers)
