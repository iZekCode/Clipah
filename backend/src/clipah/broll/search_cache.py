"""Caching of provider search responses, for their terms and for the member's budget.

Both stock providers ask that identical searches be served from a cache rather than
re-requested, and a Workspace's monthly stock allowance is real money, so the same search
repeated across two beats of one clip must cost one request rather than two.

The cache stores normalized candidates, never raw provider payloads, so nothing
provider-specific and nothing resembling a credential survives in Redis. A cache that is
unreachable is a missed saving and never a failure: the search simply goes to the provider.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any, Protocol

from clipah.broll.retriever import (
    BrollRetriever,
    ExternalAssetCandidate,
    LicenseTerms,
    MediaKind,
    SearchRequest,
)

CACHE_VERSION = "broll-search/1"
DEFAULT_TTL_SECONDS = 24 * 60 * 60


class CacheBackend(Protocol):
    """The one cache capability a search needs, so tests never need a live Redis."""

    def get(self, key: str) -> bytes | str | None:
        """Read one cached value, or nothing."""

    def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        """Write one value that expires on its own."""


class RedisSearchCache:
    """The production cache, and the only place a Redis client type appears here."""

    def __init__(self, client: Any) -> None:
        """Bind the Redis client this cache reads and writes through."""
        self._client = client

    def get(self, key: str) -> bytes | str | None:
        """Read one cached value, treating an unreachable cache as a miss."""
        try:
            value = self._client.get(key)
        except Exception:
            return None
        return value if isinstance(value, bytes | str) else None

    def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        """Write one expiring value, treating an unreachable cache as a missed saving."""
        try:
            self._client.set(key, value, ex=ttl_seconds)
        except Exception:
            return


class InMemorySearchCache:
    """Deterministic cache used by tests and by any process without a configured Redis."""

    def __init__(self) -> None:
        """Start empty, recording every write so a test can prove the TTL that was asked for."""
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        """Read one cached value."""
        return self.values.get(key)

    def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        """Write one value and remember the expiry it was written with."""
        self.values[key] = value
        self.ttls[key] = ttl_seconds


@dataclass(frozen=True, slots=True)
class CachedBrollRetriever:
    """Serve one retriever's searches from a cache before paying its provider."""

    retriever: BrollRetriever
    cache: CacheBackend
    provider: str
    ttl_seconds: int = DEFAULT_TTL_SECONDS

    def search(self, *, request: SearchRequest) -> tuple[ExternalAssetCandidate, ...]:
        """Return the cached answer for this exact search, or perform and store one."""
        key = cache_key(provider=self.provider, request=request)
        cached = self.cache.get(key)
        if cached is not None:
            decoded = _decode(cached)
            if decoded is not None:
                return decoded
        results = tuple(self.retriever.search(request=request))
        self.cache.set(key, _encode(results), ttl_seconds=self.ttl_seconds)
        return results


def cache_key(*, provider: str, request: SearchRequest) -> str:
    """Name one exact search, so a different filter is never served a stale answer."""
    subject = json.dumps(
        {
            "version": CACHE_VERSION,
            "provider": provider,
            "queries": list(request.queries),
            "limit": request.limit,
            "portrait": request.intent.portrait_suitable,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return f"clipah:broll:search:{sha256(subject.encode()).hexdigest()}"


def _encode(candidates: tuple[ExternalAssetCandidate, ...]) -> str:
    """Store normalized candidates, never the provider payload they were read from."""
    return json.dumps(
        [
            {**asdict(candidate), "media_kind": candidate.media_kind.value}
            for candidate in candidates
        ],
        ensure_ascii=False,
    )


def _decode(value: bytes | str) -> tuple[ExternalAssetCandidate, ...] | None:
    """Rebuild cached candidates, treating anything unreadable as a plain cache miss."""
    try:
        entries = json.loads(value)
        if not isinstance(entries, list):
            return None
        return tuple(_candidate(entry) for entry in entries)
    except (ValueError, TypeError, KeyError):
        return None


def _candidate(entry: Any) -> ExternalAssetCandidate:
    """Rebuild one cached candidate exactly as it was normalized."""
    license_entry = entry["license"]
    return ExternalAssetCandidate(
        provider=entry["provider"],
        provider_asset_id=entry["provider_asset_id"],
        media_kind=MediaKind(entry["media_kind"]),
        source_url=entry["source_url"],
        download_url=entry["download_url"],
        author=entry["author"],
        author_url=entry["author_url"],
        license=LicenseTerms(
            name=license_entry["name"],
            url=license_entry["url"],
            attribution_required=bool(license_entry["attribution_required"]),
            snapshot=license_entry["snapshot"],
        ),
        width=int(entry["width"]),
        height=int(entry["height"]),
        duration_ms=None if entry["duration_ms"] is None else int(entry["duration_ms"]),
        attribution_text=entry["attribution_text"],
        query=entry["query"],
        safe=bool(entry["safe"]),
        description=entry.get("description", ""),
        tags=tuple(entry.get("tags", ())),
    )
