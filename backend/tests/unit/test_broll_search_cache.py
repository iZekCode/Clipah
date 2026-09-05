"""Contracts for caching provider searches, which both terms and budgets require."""

from __future__ import annotations

from typing import Any

import pytest

from clipah.broll.models import VisualIntent
from clipah.broll.retriever import (
    ExternalAssetCandidate,
    FakeBrollRetriever,
    LicenseTerms,
    MediaKind,
    SearchRequest,
)
from clipah.broll.search_cache import (
    CachedBrollRetriever,
    InMemorySearchCache,
    RedisSearchCache,
    cache_key,
)


def _intent(**overrides: Any) -> VisualIntent:
    """Build one schema-valid intent."""
    values: dict[str, Any] = {
        "subject": "a shortened signup form",
        "action": "a hand deleting form fields",
        "setting": "a laptop screen on a desk",
        "mood": "focused",
        "search_terms_id": ("formulir pendaftaran",),
        "search_terms_en": ("signup form",),
        "portrait_suitable": True,
        "exclusions": (),
        "factual_risk_flags": (),
        "confidence": 0.8,
    }
    values.update(overrides)
    return VisualIntent(**values)


def _request(**overrides: Any) -> SearchRequest:
    """Build one search request, varying only what a test is about."""
    values: dict[str, Any] = {
        "intent": _intent(),
        "queries": ("formulir pendaftaran", "signup form"),
        "limit": 4,
    }
    values.update(overrides)
    return SearchRequest(**values)


def _candidate() -> ExternalAssetCandidate:
    """Build one complete candidate to be cached and read back."""
    return ExternalAssetCandidate(
        provider="pexels",
        provider_asset_id="12345",
        media_kind=MediaKind.VIDEO,
        source_url="https://www.pexels.com/video/12345/",
        download_url="https://videos/hd.mp4",
        author="Ana Rahma",
        author_url="https://www.pexels.com/@ana-rahma",
        license=LicenseTerms(
            name="Pexels License",
            url="https://www.pexels.com/license/",
            attribution_required=False,
            snapshot="Free to use.",
        ),
        width=1080,
        height=1920,
        duration_ms=9_000,
        attribution_text="Video by Ana Rahma on Pexels",
        query="formulir pendaftaran",
        safe=True,
        description="a signup form",
        tags=("signup", "form"),
    )


def _cached(
    results: list[ExternalAssetCandidate], cache: InMemorySearchCache, *, ttl: int = 3600
) -> tuple[CachedBrollRetriever, FakeBrollRetriever]:
    """Wrap a fake provider in the cache under test."""
    inner = FakeBrollRetriever(results=results)
    return (
        CachedBrollRetriever(retriever=inner, cache=cache, provider="pexels", ttl_seconds=ttl),
        inner,
    )


@pytest.mark.unit
def test_an_identical_search_is_served_from_the_cache_rather_than_the_provider() -> None:
    """A Workspace's monthly stock allowance is money; a repeated search must cost none."""
    cache = InMemorySearchCache()
    retriever, inner = _cached([_candidate()], cache)

    first = retriever.search(request=_request())
    second = retriever.search(request=_request())

    assert first == second
    assert len(inner.searches) == 1


@pytest.mark.unit
def test_a_cached_candidate_survives_the_round_trip_unchanged() -> None:
    """A cache that quietly loses a licence field would defeat the provenance gate."""
    cache = InMemorySearchCache()
    retriever, _ = _cached([_candidate()], cache)

    retriever.search(request=_request())
    replayed = retriever.search(request=_request())

    assert replayed == (_candidate(),)


@pytest.mark.unit
def test_the_cache_is_written_with_the_ttl_the_provider_terms_require() -> None:
    """Both providers ask that identical searches be cached rather than re-requested."""
    cache = InMemorySearchCache()
    retriever, _ = _cached([_candidate()], cache, ttl=86_400)

    retriever.search(request=_request())

    assert set(cache.ttls.values()) == {86_400}


@pytest.mark.unit
def test_a_different_query_is_a_different_cache_entry() -> None:
    """Serving one search's answer to another would be a silent relevance bug."""
    first = cache_key(provider="pexels", request=_request())
    second = cache_key(provider="pexels", request=_request(queries=("beach sunset",)))

    assert first != second


@pytest.mark.unit
def test_a_different_orientation_is_a_different_cache_entry() -> None:
    """A landscape answer served to a portrait search would fail the crop gate later."""
    first = cache_key(provider="pexels", request=_request())
    second = cache_key(provider="pexels", request=_request(intent=_intent(portrait_suitable=False)))

    assert first != second


@pytest.mark.unit
def test_one_query_is_cached_separately_for_each_provider() -> None:
    """Two providers answering one query are two answers, not one shared entry."""
    assert cache_key(provider="pexels", request=_request()) != cache_key(
        provider="pixabay", request=_request()
    )


@pytest.mark.unit
def test_a_cache_entry_never_carries_a_credential_or_a_provider_payload() -> None:
    """Everything stored is a normalized candidate, so nothing provider-shaped survives."""
    cache = InMemorySearchCache()
    retriever, _ = _cached([_candidate()], cache)

    retriever.search(request=_request())

    stored = " ".join(cache.values.values())
    assert "Authorization" not in stored
    assert "api.pexels.com" not in stored


@pytest.mark.unit
def test_an_unreadable_cache_entry_is_a_miss_rather_than_a_failure() -> None:
    """Corrupt cached bytes must send the search to the provider, not raise at a member."""
    cache = InMemorySearchCache()
    retriever, inner = _cached([_candidate()], cache)
    cache.values[cache_key(provider="pexels", request=_request())] = "not json"

    assert retriever.search(request=_request()) == (_candidate(),)
    assert len(inner.searches) == 1


@pytest.mark.unit
def test_a_cache_entry_missing_a_field_is_a_miss_rather_than_a_crash() -> None:
    """A cache written by an older release must degrade to a live search."""
    cache = InMemorySearchCache()
    retriever, inner = _cached([_candidate()], cache)
    cache.values[cache_key(provider="pexels", request=_request())] = '[{"provider": "pexels"}]'

    assert retriever.search(request=_request()) == (_candidate(),)
    assert len(inner.searches) == 1


@pytest.mark.unit
def test_an_empty_provider_answer_is_cached_so_it_is_not_paid_for_twice() -> None:
    """A search that found nothing is exactly the one worth not repeating."""
    cache = InMemorySearchCache()
    retriever, inner = _cached([], cache)

    assert retriever.search(request=_request()) == ()
    assert retriever.search(request=_request()) == ()
    assert len(inner.searches) == 1


class _BrokenRedis:
    """A Redis client that fails every call, as an unreachable one would."""

    def get(self, key: str) -> bytes | None:
        """Fail the read."""
        raise ConnectionError("redis is unreachable")

    def set(self, key: str, value: str, *, ex: int) -> None:
        """Fail the write."""
        raise ConnectionError("redis is unreachable")


class _RecordingRedis:
    """A minimal Redis stand-in that records what it was asked to store."""

    def __init__(self) -> None:
        """Start empty."""
        self.values: dict[str, str] = {}
        self.expiries: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        """Read one value."""
        return self.values.get(key)

    def set(self, key: str, value: str, *, ex: int) -> None:
        """Write one value and its expiry."""
        self.values[key] = value
        self.expiries[key] = ex


@pytest.mark.unit
def test_an_unreachable_cache_is_a_missed_saving_rather_than_a_failed_search() -> None:
    """Redis being down must not stop a member from being offered a picture."""
    cache = RedisSearchCache(_BrokenRedis())
    retriever = CachedBrollRetriever(
        retriever=FakeBrollRetriever(results=[_candidate()]),
        cache=cache,
        provider="pexels",
    )

    assert retriever.search(request=_request()) == (_candidate(),)


@pytest.mark.unit
def test_the_redis_cache_stores_and_returns_one_search() -> None:
    """The production backend must behave like the in-memory one it is tested against."""
    client = _RecordingRedis()
    retriever = CachedBrollRetriever(
        retriever=FakeBrollRetriever(results=[_candidate()]),
        cache=RedisSearchCache(client),
        provider="pexels",
        ttl_seconds=600,
    )

    first = retriever.search(request=_request())
    second = retriever.search(request=_request())

    assert first == second == (_candidate(),)
    assert set(client.expiries.values()) == {600}


@pytest.mark.unit
def test_a_redis_value_of_an_unexpected_type_is_a_miss() -> None:
    """A key written by something else entirely must not be parsed as a search answer."""

    class _WrongType:
        def get(self, key: str) -> int:
            return 7

        def set(self, key: str, value: str, *, ex: int) -> None:
            return None

    assert RedisSearchCache(_WrongType()).get("any") is None


@pytest.mark.unit
def test_a_cached_value_that_is_not_a_list_is_a_miss() -> None:
    """A key holding something else entirely must send the search to the provider."""
    cache = InMemorySearchCache()
    retriever, inner = _cached([_candidate()], cache)
    cache.values[cache_key(provider="pexels", request=_request())] = "{}"

    assert retriever.search(request=_request()) == (_candidate(),)
    assert len(inner.searches) == 1
