"""Pixabay adapter: the only module that knows this provider's endpoint and payload shape.

Pixabay's payload shares nothing with Pexels' — results are `hits`, renditions are a
dictionary rather than a list, tags are one comma-separated string, and the credential
travels as a query parameter. All of that stops here; what leaves is the same
`ExternalAssetCandidate` the rest of the product reasons about.
"""

from __future__ import annotations

from typing import Any

from clipah.broll.retriever import (
    ExternalAssetCandidate,
    LicenseTerms,
    MediaKind,
    SearchRequest,
)
from clipah.broll.stock import (
    HttpTransport,
    HttpxTransport,
    positive_int,
    request_json,
    results_of,
    text_of,
)

PROVIDER = "pixabay"
SEARCH_URL = "https://pixabay.com/api/videos/"
LICENSE_NAME = "Pixabay Content License"
LICENSE_URL = "https://pixabay.com/service/license-summary/"
LICENSE_SNAPSHOT = (
    "Pixabay Content License, retrieved from https://pixabay.com/service/license-summary/. "
    "Content is free to use for commercial and non-commercial purposes without attribution, "
    "but may not be redistributed on a competing platform, sold unaltered, or used to depict "
    "identifiable people or brands in a way that implies endorsement or is offensive."
)
#: Pixabay's terms require search results to be cached for at least 24 hours.
CACHE_TTL_SECONDS = 24 * 60 * 60

_RENDITION_ORDER = ("large", "medium", "small", "tiny")


class PixabayBrollRetriever:
    """Search Pixabay for safe, portrait-suitable footage matching one visual intent."""

    def __init__(self, *, api_key: str, transport: HttpTransport | None = None) -> None:
        """Bind the credential and the transport, which tests replace with a stub."""
        self._api_key = api_key
        self._transport = transport or HttpxTransport()

    def search(self, *, request: SearchRequest) -> tuple[ExternalAssetCandidate, ...]:
        """Perform exactly one search, so a request budget means what it says."""
        query = request.queries[0] if request.queries else ""
        body = request_json(
            self._transport,
            url=SEARCH_URL,
            params={
                "key": self._api_key,
                "q": query,
                "safesearch": "true",
                "orientation": "vertical" if request.intent.portrait_suitable else "horizontal",
                "per_page": request.limit,
            },
            headers={},
        )
        candidates = [
            candidate
            for entry in results_of(body, "hits")
            if (candidate := _candidate(entry, query=query)) is not None
        ]
        return tuple(candidates[: request.limit])


def _candidate(entry: dict[str, Any], *, query: str) -> ExternalAssetCandidate | None:
    """Normalize one Pixabay video, or skip it when it cannot be fully described."""
    asset_id = positive_int(entry.get("id"))
    source_url = text_of(entry, "pageURL")
    author = text_of(entry, "user")
    file = _largest_file(entry.get("videos"))
    if not asset_id or not source_url or not author or file is None:
        return None
    download_url, width, height = file
    tags = _tags(entry)
    return ExternalAssetCandidate(
        provider=PROVIDER,
        provider_asset_id=str(asset_id),
        media_kind=MediaKind.VIDEO,
        source_url=source_url,
        download_url=download_url,
        author=author,
        author_url=f"https://pixabay.com/users/{author}/",
        license=LicenseTerms(
            name=LICENSE_NAME,
            url=LICENSE_URL,
            attribution_required=False,
            snapshot=LICENSE_SNAPSHOT,
        ),
        width=width,
        height=height,
        duration_ms=positive_int(entry.get("duration")) * 1_000 or None,
        attribution_text=f"Video by {author} on Pixabay",
        query=query,
        safe=True,
        description=" ".join(tags),
        tags=tags,
    )


def _largest_file(renditions: Any) -> tuple[str, int, int] | None:
    """Choose the highest usable rendition once, so nothing is downloaded twice."""
    if not isinstance(renditions, dict):
        return None
    for name in _RENDITION_ORDER:
        rendition = renditions.get(name)
        if not isinstance(rendition, dict):
            continue
        url = text_of(rendition, "url")
        width = positive_int(rendition.get("width"))
        height = positive_int(rendition.get("height"))
        if url and width and height:
            return url, width, height
    return None


def _tags(entry: dict[str, Any]) -> tuple[str, ...]:
    """Split Pixabay's one comma-separated tag string into the words it meant."""
    return tuple(tag.strip() for tag in text_of(entry, "tags").split(",") if tag.strip())
