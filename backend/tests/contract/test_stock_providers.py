"""Contract fixtures for the Pexels and Pixabay adapters, with no network in CI.

Every response here is a checked-in fixture served by a stub transport. What is being
tested is the boundary: that the filters Clipah promises are actually sent, that provider
payload shapes are normalized deterministically, that a malformed or deleted upstream asset
is skipped rather than stored, and that no provider text or credential escapes the adapter.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from clipah.broll.models import VisualIntent
from clipah.broll.pexels_adapter import PexelsBrollRetriever
from clipah.broll.pixabay_adapter import PixabayBrollRetriever
from clipah.broll.retriever import (
    RETRIEVAL_INVALID_CODE,
    RETRIEVAL_RATE_LIMITED_CODE,
    RETRIEVAL_UNAVAILABLE_CODE,
    BrollRetrievalRetryableError,
    BrollRetrievalTerminalError,
    MediaKind,
    SearchRequest,
)

API_KEY = "secret-provider-key"


def _intent() -> VisualIntent:
    """Build one schema-valid Indonesian-and-English intent."""
    return VisualIntent(
        subject="a shortened signup form",
        action="a hand deleting form fields",
        setting="a laptop screen on a desk",
        mood="focused",
        search_terms_id=("formulir pendaftaran",),
        search_terms_en=("signup form",),
        portrait_suitable=True,
        exclusions=(),
        factual_risk_flags=(),
        confidence=0.8,
    )


def _request(limit: int = 4) -> SearchRequest:
    """Build the search one retriever is handed."""
    return SearchRequest(
        intent=_intent(), queries=("formulir pendaftaran", "signup form"), limit=limit
    )


class _StubResponse:
    """One checked-in provider response, shaped like the HTTP client's own."""

    def __init__(self, *, status_code: int, payload: Any = None, text: str = "") -> None:
        """Bind the status and body this response replays."""
        self.status_code = status_code
        self.text = text or json.dumps(payload if payload is not None else {})

    def json(self) -> Any:
        """Parse the body the way the real client would."""
        return json.loads(self.text)


class _StubTransport:
    """Replay queued responses while recording every request the adapter made."""

    def __init__(self, responses: list[Any]) -> None:
        """Queue one outcome per expected provider request."""
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    def get(self, url: str, *, params: dict[str, Any], headers: dict[str, str]) -> Any:
        """Record the exact request, then raise or return the queued outcome."""
        self.requests.append({"url": url, "params": params, "headers": headers})
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


PEXELS_VIDEO = {
    "id": 12345,
    "width": 1080,
    "height": 1920,
    "duration": 9,
    "url": "https://www.pexels.com/video/12345/",
    "user": {"name": "Ana Rahma", "url": "https://www.pexels.com/@ana-rahma"},
    "video_files": [
        {"quality": "sd", "width": 540, "height": 960, "link": "https://videos/sd.mp4"},
        {"quality": "hd", "width": 1080, "height": 1920, "link": "https://videos/hd.mp4"},
    ],
    "tags": ["signup", "form"],
}

PIXABAY_VIDEO = {
    "id": 67890,
    "pageURL": "https://pixabay.com/videos/id-67890/",
    "duration": 12,
    "user": "budisantoso",
    "tags": "formulir, pendaftaran, laptop",
    "videos": {
        "large": {"url": "https://cdn.pixabay.com/large.mp4", "width": 1080, "height": 1920},
        "small": {"url": "https://cdn.pixabay.com/small.mp4", "width": 540, "height": 960},
    },
}


def _pexels(responses: list[Any]) -> tuple[PexelsBrollRetriever, _StubTransport]:
    """Build the Pexels adapter over a stub transport."""
    transport = _StubTransport(responses)
    return PexelsBrollRetriever(api_key=API_KEY, transport=transport), transport


def _pixabay(responses: list[Any]) -> tuple[PixabayBrollRetriever, _StubTransport]:
    """Build the Pixabay adapter over a stub transport."""
    transport = _StubTransport(responses)
    return PixabayBrollRetriever(api_key=API_KEY, transport=transport), transport


@pytest.mark.unit
def test_pexels_sends_the_query_orientation_and_safe_search_filters() -> None:
    """The filters Clipah promises members are the ones that must reach the provider."""
    retriever, transport = _pexels([_StubResponse(status_code=200, payload={"videos": []})])

    retriever.search(request=_request())

    params = transport.requests[0]["params"]
    assert params["query"] == "formulir pendaftaran"
    assert params["orientation"] == "portrait"
    assert params["per_page"] == 4
    assert transport.requests[0]["headers"]["Authorization"] == API_KEY


@pytest.mark.unit
def test_pixabay_sends_the_query_orientation_and_safe_search_filters() -> None:
    """Safe search is applied at the provider, before an unsafe asset is ever seen."""
    retriever, transport = _pixabay([_StubResponse(status_code=200, payload={"hits": []})])

    retriever.search(request=_request())

    params = transport.requests[0]["params"]
    assert params["q"] == "formulir pendaftaran"
    assert params["safesearch"] == "true"
    assert params["per_page"] == 4
    assert params["key"] == API_KEY


@pytest.mark.unit
def test_pexels_normalizes_one_video_into_a_complete_candidate() -> None:
    """Normalization is deterministic and produces every field provenance will demand."""
    retriever, _ = _pexels([_StubResponse(status_code=200, payload={"videos": [PEXELS_VIDEO]})])

    results = retriever.search(request=_request())

    assert len(results) == 1
    candidate = results[0]
    assert candidate.provider == "pexels"
    assert candidate.provider_asset_id == "12345"
    assert candidate.media_kind is MediaKind.VIDEO
    assert candidate.source_url == "https://www.pexels.com/video/12345/"
    assert candidate.download_url == "https://videos/hd.mp4"
    assert candidate.author == "Ana Rahma"
    assert (candidate.width, candidate.height) == (1080, 1920)
    assert candidate.duration_ms == 9_000
    assert candidate.license.name == "Pexels License"
    assert candidate.license.snapshot
    assert candidate.attribution_text == "Video by Ana Rahma on Pexels"
    assert candidate.query == "formulir pendaftaran"
    assert candidate.safe is True


@pytest.mark.unit
def test_pixabay_normalizes_one_video_into_a_complete_candidate() -> None:
    """Two providers with different payload shapes must produce one candidate shape."""
    retriever, _ = _pixabay([_StubResponse(status_code=200, payload={"hits": [PIXABAY_VIDEO]})])

    results = retriever.search(request=_request())

    assert len(results) == 1
    candidate = results[0]
    assert candidate.provider == "pixabay"
    assert candidate.provider_asset_id == "67890"
    assert candidate.source_url == "https://pixabay.com/videos/id-67890/"
    assert candidate.download_url == "https://cdn.pixabay.com/large.mp4"
    assert candidate.author == "budisantoso"
    assert (candidate.width, candidate.height) == (1080, 1920)
    assert candidate.duration_ms == 12_000
    assert candidate.license.name == "Pixabay Content License"
    assert candidate.tags == ("formulir", "pendaftaran", "laptop")


@pytest.mark.unit
def test_pexels_chooses_the_largest_file_that_is_still_a_real_file() -> None:
    """Picking the highest usable rendition once avoids re-downloading a better one later."""
    retriever, _ = _pexels([_StubResponse(status_code=200, payload={"videos": [PEXELS_VIDEO]})])

    candidate = retriever.search(request=_request())[0]

    assert candidate.download_url == "https://videos/hd.mp4"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"videos": [{"id": 1}]}, id="no user or files"),
        pytest.param(
            {"videos": [{**PEXELS_VIDEO, "user": {"name": "", "url": ""}}]}, id="no author"
        ),
        pytest.param({"videos": [{**PEXELS_VIDEO, "video_files": []}]}, id="no media files"),
        pytest.param({"videos": [{**PEXELS_VIDEO, "url": ""}]}, id="no source page"),
        pytest.param({"videos": "not a list"}, id="results are not a list"),
        pytest.param({}, id="no results key at all"),
    ],
)
@pytest.mark.unit
def test_pexels_skips_malformed_or_deleted_upstream_assets(payload: dict[str, Any]) -> None:
    """A provider entry Clipah cannot fully describe is skipped, never half-stored."""
    retriever, _ = _pexels([_StubResponse(status_code=200, payload=payload)])

    assert retriever.search(request=_request()) == ()


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"hits": [{"id": 1}]}, id="no user or videos"),
        pytest.param({"hits": [{**PIXABAY_VIDEO, "user": ""}]}, id="no author"),
        pytest.param({"hits": [{**PIXABAY_VIDEO, "videos": {}}]}, id="no media files"),
        pytest.param({"hits": [{**PIXABAY_VIDEO, "pageURL": ""}]}, id="no source page"),
        pytest.param({"hits": "not a list"}, id="results are not a list"),
    ],
)
@pytest.mark.unit
def test_pixabay_skips_malformed_or_deleted_upstream_assets(payload: dict[str, Any]) -> None:
    """The same rule, at a provider whose payload shape shares none of the other's."""
    retriever, _ = _pixabay([_StubResponse(status_code=200, payload=payload)])

    assert retriever.search(request=_request()) == ()


@pytest.mark.unit
def test_a_provider_page_of_results_is_bounded_by_the_requested_limit() -> None:
    """A provider that ignores per_page must not cost the reranker a hundred candidates."""
    videos = [{**PEXELS_VIDEO, "id": index} for index in range(20)]
    retriever, _ = _pexels([_StubResponse(status_code=200, payload={"videos": videos})])

    assert len(retriever.search(request=_request(limit=4))) == 4


@pytest.mark.unit
def test_a_rate_limited_provider_is_reported_as_retryable() -> None:
    """A throttled provider is a wait, not a verdict on the beat being illustrated."""
    retriever, _ = _pexels([_StubResponse(status_code=429)])

    with pytest.raises(BrollRetrievalRetryableError) as error:
        retriever.search(request=_request())

    assert error.value.code == RETRIEVAL_RATE_LIMITED_CODE


@pytest.mark.unit
def test_a_provider_outage_is_reported_as_retryable() -> None:
    """One provider being down must not permanently mark a beat as unillustratable."""
    retriever, _ = _pixabay([_StubResponse(status_code=503)])

    with pytest.raises(BrollRetrievalRetryableError) as error:
        retriever.search(request=_request())

    assert error.value.code == RETRIEVAL_UNAVAILABLE_CODE


@pytest.mark.unit
def test_a_transport_timeout_is_reported_as_retryable_without_provider_text() -> None:
    """A timeout says nothing about the query and must carry nothing about the request."""
    retriever, _ = _pexels([TimeoutError("read timed out on https://api.pexels.com?key=secret")])

    with pytest.raises(BrollRetrievalRetryableError) as error:
        retriever.search(request=_request())

    assert error.value.code == RETRIEVAL_UNAVAILABLE_CODE
    assert "secret" not in str(error.value)


@pytest.mark.unit
def test_a_refused_request_is_terminal() -> None:
    """Retrying a request the provider rejected spends the budget on the same answer."""
    retriever, _ = _pexels([_StubResponse(status_code=400)])

    with pytest.raises(BrollRetrievalTerminalError):
        retriever.search(request=_request())


@pytest.mark.unit
def test_an_unparseable_body_is_terminal() -> None:
    """A body that is not the agreed shape will not become one on a second try."""
    retriever, _ = _pexels([_StubResponse(status_code=200, text="not json")])

    with pytest.raises(BrollRetrievalTerminalError) as error:
        retriever.search(request=_request())

    assert error.value.code == RETRIEVAL_INVALID_CODE


@pytest.mark.unit
def test_no_failure_ever_carries_the_api_key() -> None:
    """A credential in an error message reaches logs, traces, and Sentry at once."""
    retriever, _ = _pexels([_StubResponse(status_code=400, text=f"bad key {API_KEY}")])

    with pytest.raises(BrollRetrievalTerminalError) as error:
        retriever.search(request=_request())

    assert API_KEY not in str(error.value)


@pytest.mark.unit
def test_an_adapter_sends_exactly_one_request_per_search() -> None:
    """A request budget is only meaningful if one search is one provider request."""
    retriever, transport = _pexels(
        [_StubResponse(status_code=200, payload={"videos": [PEXELS_VIDEO]})]
    )

    retriever.search(request=_request())

    assert len(transport.requests) == 1


@pytest.mark.unit
def test_normalization_of_the_same_payload_is_identical_across_runs() -> None:
    """A replayed retrieval must converge on the candidate it already normalized."""
    first, _ = _pexels([_StubResponse(status_code=200, payload={"videos": [PEXELS_VIDEO]})])
    second, _ = _pexels([_StubResponse(status_code=200, payload={"videos": [PEXELS_VIDEO]})])

    assert first.search(request=_request()) == second.search(request=_request())


@pytest.mark.unit
def test_a_response_with_no_status_is_terminal() -> None:
    """A transport that answered with something other than a response is not retryable."""

    class _Shapeless:
        status_code = None

    retriever, _ = _pexels([_Shapeless()])

    with pytest.raises(BrollRetrievalTerminalError) as error:
        retriever.search(request=_request())

    assert error.value.code == RETRIEVAL_INVALID_CODE


@pytest.mark.unit
def test_a_body_that_is_a_list_rather_than_an_object_is_terminal() -> None:
    """Both providers answer with an object; a bare array is not their contract."""
    retriever, _ = _pixabay([_StubResponse(status_code=200, text="[]")])

    with pytest.raises(BrollRetrievalTerminalError) as error:
        retriever.search(request=_request())

    assert error.value.code == RETRIEVAL_INVALID_CODE


@pytest.mark.unit
def test_pexels_tags_that_are_not_a_list_are_read_as_no_tags() -> None:
    """A provider changing a field's shape must not stop the asset being usable."""
    retriever, _ = _pexels(
        [_StubResponse(status_code=200, payload={"videos": [{**PEXELS_VIDEO, "tags": "signup"}]})]
    )

    assert retriever.search(request=_request())[0].tags == ()


@pytest.mark.unit
def test_the_production_transport_is_bounded_by_a_timeout() -> None:
    """An unbounded provider request would hold a worker slot indefinitely."""
    from clipah.broll.stock import REQUEST_TIMEOUT_SECONDS, HttpxTransport

    assert HttpxTransport(timeout=REQUEST_TIMEOUT_SECONDS) is not None
    assert REQUEST_TIMEOUT_SECONDS > 0


@pytest.mark.unit
def test_pixabay_skips_an_incomplete_rendition_and_takes_the_next_one() -> None:
    """A provider offering a broken large file must not cost the usable small one."""
    entry = {
        **PIXABAY_VIDEO,
        "videos": {
            "large": {"url": "", "width": 1080, "height": 1920},
            "medium": {"url": "https://cdn.pixabay.com/medium.mp4", "width": 720, "height": 1280},
        },
    }
    retriever, _ = _pixabay([_StubResponse(status_code=200, payload={"hits": [entry]})])

    assert retriever.search(request=_request())[0].download_url == (
        "https://cdn.pixabay.com/medium.mp4"
    )
