"""Opt-in live smoke test for the stock providers, limited to one search each.

This is the only test in the suite that contacts Pexels or Pixabay. It is skipped unless a
credential is explicitly supplied, it performs exactly one search per provider, and it
downloads nothing — proving that a live answer normalizes into the same candidate shape the
checked-in fixtures describe, and that nothing unselected is ever fetched.

    CLIPAH_STOCK_SMOKE=1 CLIPAH_PEXELS_API_KEY=... uv run pytest tests/slow -m slow
"""

from __future__ import annotations

import os

import pytest

from clipah.broll.models import VisualIntent
from clipah.broll.pexels_adapter import PexelsBrollRetriever
from clipah.broll.pixabay_adapter import PixabayBrollRetriever
from clipah.broll.retriever import SearchRequest, provenance_of
from clipah.broll.stock import HttpTransport, HttpxTransport

RETRIEVED_AT = "2026-09-05T00:00:00+00:00"


class _CountingTransport:
    """Wrap the production transport so a test can prove one search is one request."""

    def __init__(self) -> None:
        """Start with no requests recorded."""
        self.inner: HttpTransport = HttpxTransport()
        self.requests = 0

    def get(self, url: str, *, params: dict[str, object], headers: dict[str, str]) -> object:
        """Count the request before letting the real transport perform it."""
        self.requests += 1
        return self.inner.get(url, params=params, headers=headers)  # type: ignore[arg-type]


def _intent() -> VisualIntent:
    """Ask for something every stock library has, so a miss means a real problem."""
    return VisualIntent(
        subject="a laptop on a desk",
        action="a hand typing",
        setting="an office by a window",
        mood="calm",
        search_terms_id=("laptop meja kerja",),
        search_terms_en=("laptop on desk",),
        portrait_suitable=True,
        exclusions=(),
        factual_risk_flags=(),
        confidence=0.9,
    )


def _request() -> SearchRequest:
    """Ask for the smallest page a provider will serve."""
    return SearchRequest(intent=_intent(), queries=("laptop on desk",), limit=3)


@pytest.mark.slow
@pytest.mark.skipif(
    os.environ.get("CLIPAH_STOCK_SMOKE") != "1" or not os.environ.get("CLIPAH_PEXELS_API_KEY"),
    reason="opt in with CLIPAH_STOCK_SMOKE=1 and a Pexels credential",
)
def test_one_live_pexels_search_normalizes_and_downloads_nothing() -> None:
    """A live answer must satisfy the same provenance gate the fixtures do."""
    transport = _CountingTransport()
    retriever = PexelsBrollRetriever(
        api_key=os.environ["CLIPAH_PEXELS_API_KEY"], transport=transport
    )

    results = retriever.search(request=_request())

    assert transport.requests == 1
    assert results
    for candidate in results:
        provenance_of(candidate, retrieved_at_iso=RETRIEVED_AT)
        assert candidate.download_url.startswith("https://")


@pytest.mark.slow
@pytest.mark.skipif(
    os.environ.get("CLIPAH_STOCK_SMOKE") != "1" or not os.environ.get("CLIPAH_PIXABAY_API_KEY"),
    reason="opt in with CLIPAH_STOCK_SMOKE=1 and a Pixabay credential",
)
def test_one_live_pixabay_search_normalizes_and_downloads_nothing() -> None:
    """The second provider's payload shape must normalize to the same candidate."""
    transport = _CountingTransport()
    retriever = PixabayBrollRetriever(
        api_key=os.environ["CLIPAH_PIXABAY_API_KEY"], transport=transport
    )

    results = retriever.search(request=_request())

    assert transport.requests == 1
    assert results
    for candidate in results:
        provenance_of(candidate, retrieved_at_iso=RETRIEVED_AT)
        assert candidate.download_url.startswith("https://")
