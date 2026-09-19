"""Suggest a Project name from a YouTube video's public title.

YouTube's oEmbed endpoint describes a public video without an API key and without
downloading anything. The request always goes to YouTube's own host and names the
canonical watch address built from a validated video ID, so nothing a member types can
choose where the backend connects.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

OEMBED_ENDPOINT = "https://www.youtube.com/oembed"
# The New project dialog accepts at most this many characters for a name.
MAX_TITLE_LENGTH = 200
TIMEOUT_SECONDS = 3.0

YouTubeTitleLookup = Callable[[str], str | None]


def oembed_title(video_id: str, *, client: httpx.Client | None = None) -> str | None:
    """Return the video's public title, or None when YouTube will not say.

    Private, removed, and embedding-disabled videos, a slow or unreachable YouTube, and any
    answer without a usable title all mean "no suggestion": the member types a name.
    """
    owned = client is None
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False)
    try:
        response = http.get(
            OEMBED_ENDPOINT,
            params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
        )
        if response.status_code != 200:
            return None
        body = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        if owned:
            http.close()
    title = body.get("title") if isinstance(body, dict) else None
    if not isinstance(title, str) or not title.strip():
        return None
    return title.strip()[:MAX_TITLE_LENGTH]
