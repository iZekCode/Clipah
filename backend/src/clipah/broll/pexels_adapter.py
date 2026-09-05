"""Pexels adapter: the only module that knows this provider's endpoint and payload shape.

Nothing Pexels-specific leaves here. A response becomes `ExternalAssetCandidate` values or
it becomes nothing: an entry whose author, source page, or media file is missing is skipped
rather than half-stored, because provenance that cannot be completed later cannot be
completed at all.
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

PROVIDER = "pexels"
SEARCH_URL = "https://api.pexels.com/videos/search"
LICENSE_NAME = "Pexels License"
LICENSE_URL = "https://www.pexels.com/license/"
LICENSE_SNAPSHOT = (
    "Pexels License, retrieved from https://www.pexels.com/license/. Photos and videos on "
    "Pexels are free to use for commercial and non-commercial purposes. Attribution is not "
    "required but is appreciated. Identifiable people may not appear in a bad light or in a "
    "way that is offensive, and content may not be sold unmodified or used to create a "
    "competing stock service."
)
#: Pexels asks that results be cached rather than re-requested for each identical search.
CACHE_TTL_SECONDS = 24 * 60 * 60


class PexelsBrollRetriever:
    """Search Pexels for portrait-suitable footage matching one visual intent."""

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
                "query": query,
                "orientation": "portrait" if request.intent.portrait_suitable else "landscape",
                "per_page": request.limit,
            },
            headers={"Authorization": self._api_key},
        )
        candidates = [
            candidate
            for entry in results_of(body, "videos")
            if (candidate := _candidate(entry, query=query)) is not None
        ]
        return tuple(candidates[: request.limit])


def _candidate(entry: dict[str, Any], *, query: str) -> ExternalAssetCandidate | None:
    """Normalize one Pexels video, or skip it when it cannot be fully described."""
    asset_id = positive_int(entry.get("id"))
    source_url = text_of(entry, "url")
    user = entry.get("user")
    author = text_of(user, "name") if isinstance(user, dict) else ""
    file = _largest_file(entry.get("video_files"))
    if not asset_id or not source_url or not author or file is None:
        return None
    download_url, width, height = file
    return ExternalAssetCandidate(
        provider=PROVIDER,
        provider_asset_id=str(asset_id),
        media_kind=MediaKind.VIDEO,
        source_url=source_url,
        download_url=download_url,
        author=author,
        author_url=text_of(user, "url") if isinstance(user, dict) else "",
        license=LicenseTerms(
            name=LICENSE_NAME,
            url=LICENSE_URL,
            attribution_required=False,
            snapshot=LICENSE_SNAPSHOT,
        ),
        width=width,
        height=height,
        duration_ms=positive_int(entry.get("duration")) * 1_000 or None,
        attribution_text=f"Video by {author} on Pexels",
        query=query,
        safe=True,
        description=" ".join(_tags(entry)),
        tags=_tags(entry),
    )


def _largest_file(files: Any) -> tuple[str, int, int] | None:
    """Choose the highest usable rendition once, so nothing is downloaded twice."""
    if not isinstance(files, list):
        return None
    usable = [
        (text_of(file, "link"), positive_int(file.get("width")), positive_int(file.get("height")))
        for file in files
        if isinstance(file, dict)
    ]
    complete = [item for item in usable if all(item)]
    if not complete:
        return None
    return max(complete, key=lambda item: (item[1] * item[2], item[0]))


def _tags(entry: dict[str, Any]) -> tuple[str, ...]:
    """Read the provider's own words about the footage, for the reranker to weigh."""
    tags = entry.get("tags")
    if not isinstance(tags, list):
        return ()
    return tuple(tag.strip() for tag in tags if isinstance(tag, str) and tag.strip())
