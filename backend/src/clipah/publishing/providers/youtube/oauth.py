"""Least-privilege OAuth scope policy for YouTube publishing."""

from __future__ import annotations

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_READ_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
YOUTUBE_CAPTION_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"


class YouTubeScopeMissingError(Exception):
    """The OAuth Grant lacks permission for the requested YouTube operation."""


def require_youtube_publish_scopes(scopes: frozenset[str], *, captions: bool) -> None:
    """Reject a grant missing base upload or explicitly requested caption access."""
    required = {YOUTUBE_UPLOAD_SCOPE}
    if captions:
        required.add(YOUTUBE_CAPTION_SCOPE)
    if not required.issubset(scopes):
        raise YouTubeScopeMissingError("required YouTube publishing scope is unavailable")
