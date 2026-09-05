"""Contracts for provenance completeness and user-asset-first retrieval order.

Two rules decide whether a picture may ever reach a member's timeline. It must carry
complete provenance — who made it, under what licence, retrieved when, for which query —
and it must be looked for in the Workspace's own accepted assets before anybody pays a
stock provider for it.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from clipah.broll.models import BrollCoverage, VisualIntent
from clipah.broll.retriever import (
    DEFAULT_RETRIEVAL_POLICY,
    INCOMPLETE_PROVENANCE_CODE,
    UNSAFE_CANDIDATE_CODE,
    ExternalAssetCandidate,
    FakeBrollRetriever,
    LicenseTerms,
    MediaKind,
    ProvenanceError,
    RetrievalPolicy,
    RetrievalRequest,
    SearchRequest,
    provenance_of,
    retrieve_candidates,
)

del BrollCoverage


def _intent(**overrides: Any) -> VisualIntent:
    """Build one schema-valid intent, varying only what a test is about."""
    values: dict[str, Any] = {
        "subject": "a shortened signup form",
        "action": "a hand deleting form fields",
        "setting": "a laptop screen on a desk",
        "mood": "focused",
        "search_terms_id": ("formulir pendaftaran",),
        "search_terms_en": ("signup form",),
        "portrait_suitable": True,
        "exclusions": ("handshake",),
        "factual_risk_flags": (),
        "confidence": 0.8,
    }
    values.update(overrides)
    return VisualIntent(**values)


def _license(**overrides: Any) -> LicenseTerms:
    """Build one complete licence snapshot."""
    values: dict[str, Any] = {
        "name": "Pexels License",
        "url": "https://www.pexels.com/license/",
        "attribution_required": False,
        "snapshot": "Free to use. Attribution is not required but appreciated.",
    }
    values.update(overrides)
    return LicenseTerms(**values)


def _candidate(**overrides: Any) -> ExternalAssetCandidate:
    """Build one complete stock candidate, overriding only the field under test."""
    values: dict[str, Any] = {
        "provider": "pexels",
        "provider_asset_id": "12345",
        "media_kind": MediaKind.VIDEO,
        "source_url": "https://www.pexels.com/video/12345/",
        "download_url": "https://videos.pexels.com/video-files/12345/hd.mp4",
        "author": "Ana Rahma",
        "author_url": "https://www.pexels.com/@ana-rahma",
        "license": _license(),
        "width": 1080,
        "height": 1920,
        "duration_ms": 9_000,
        "attribution_text": "Video by Ana Rahma on Pexels",
        "query": "signup form",
        "safe": True,
    }
    values.update(overrides)
    return ExternalAssetCandidate(**values)


@pytest.mark.unit
def test_complete_provenance_records_everything_a_licence_review_would_ask_for() -> None:
    """A year from now, the only record of why this footage was usable is this row."""
    provenance = provenance_of(_candidate(), retrieved_at_iso="2026-09-05T00:00:00+00:00")

    assert provenance.provider == "pexels"
    assert provenance.provider_asset_id == "12345"
    assert provenance.source_url == "https://www.pexels.com/video/12345/"
    assert provenance.author == "Ana Rahma"
    assert provenance.license_name == "Pexels License"
    assert provenance.license_url == "https://www.pexels.com/license/"
    assert provenance.terms_snapshot.startswith("Free to use")
    assert provenance.retrieved_at == "2026-09-05T00:00:00+00:00"
    assert provenance.query == "signup form"
    assert provenance.attribution_text == "Video by Ana Rahma on Pexels"
    assert provenance.moderation_result == "safe"


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"provider": ""}, id="no provider"),
        pytest.param({"provider_asset_id": ""}, id="no provider asset id"),
        pytest.param({"source_url": ""}, id="no source URL"),
        pytest.param({"author": ""}, id="no author"),
        pytest.param({"attribution_text": ""}, id="no attribution text"),
        pytest.param({"query": ""}, id="no query"),
        pytest.param({"license": _license(name="")}, id="no licence name"),
        pytest.param({"license": _license(url="")}, id="no licence URL"),
        pytest.param({"license": _license(snapshot="")}, id="no terms snapshot"),
    ],
)
@pytest.mark.unit
def test_incomplete_provenance_is_refused_before_an_asset_becomes_selectable(
    overrides: dict[str, Any],
) -> None:
    """A picture nobody can trace is a legal liability, not an asset."""
    with pytest.raises(ProvenanceError) as error:
        provenance_of(_candidate(**overrides), retrieved_at_iso="2026-09-05T00:00:00+00:00")

    assert error.value.code == INCOMPLETE_PROVENANCE_CODE


@pytest.mark.unit
def test_an_unsafe_candidate_is_refused_even_with_complete_provenance() -> None:
    """Safe-search is a filter applied before storage, not a note kept beside it."""
    with pytest.raises(ProvenanceError) as error:
        provenance_of(_candidate(safe=False), retrieved_at_iso="2026-09-05T00:00:00+00:00")

    assert error.value.code == UNSAFE_CANDIDATE_CODE


@pytest.mark.unit
def test_a_provenance_refusal_carries_no_provider_payload() -> None:
    """The code is public; whatever the provider sent about the asset is not."""
    with pytest.raises(ProvenanceError) as error:
        provenance_of(_candidate(author=""), retrieved_at_iso="2026-09-05T00:00:00+00:00")

    assert str(error.value) == INCOMPLETE_PROVENANCE_CODE


@pytest.mark.unit
def test_sufficient_local_results_mean_no_provider_is_paid_at_all() -> None:
    """The Workspace's own accepted footage is free, immediate, and already licensed."""
    local = FakeBrollRetriever(
        results=[
            _candidate(provider="workspace", provider_asset_id=str(index)) for index in range(4)
        ]
    )
    stock = FakeBrollRetriever(results=[_candidate()])

    result = retrieve_candidates(
        RetrievalRequest(intent=_intent(), limit=4),
        local=local,
        stock=(stock,),
        policy=DEFAULT_RETRIEVAL_POLICY,
    )

    assert stock.searches == []
    assert {candidate.provider for candidate in result.candidates} == {"workspace"}
    assert result.provider_requests == 0


@pytest.mark.unit
def test_insufficient_local_results_fall_through_to_the_stock_providers() -> None:
    """A Workspace with nothing relevant must still be offered a picture."""
    local = FakeBrollRetriever(results=[])
    stock = FakeBrollRetriever(results=[_candidate()])

    result = retrieve_candidates(
        RetrievalRequest(intent=_intent(), limit=4),
        local=local,
        stock=(stock,),
        policy=DEFAULT_RETRIEVAL_POLICY,
    )

    assert len(stock.searches) == 1
    assert [candidate.provider for candidate in result.candidates] == ["pexels"]
    assert result.provider_requests == 1


@pytest.mark.unit
def test_local_results_are_kept_and_ranked_ahead_of_stock_ones() -> None:
    """Falling through to a provider must not throw away what the Workspace already owns."""
    local = FakeBrollRetriever(results=[_candidate(provider="workspace")])
    stock = FakeBrollRetriever(results=[_candidate()])

    result = retrieve_candidates(
        RetrievalRequest(intent=_intent(), limit=4),
        local=local,
        stock=(stock,),
        policy=DEFAULT_RETRIEVAL_POLICY,
    )

    assert [candidate.provider for candidate in result.candidates] == ["workspace", "pexels"]


@pytest.mark.unit
def test_both_indonesian_and_english_terms_reach_the_provider() -> None:
    """An Indonesian concept searched only in English has already lost its meaning."""
    stock = FakeBrollRetriever(results=[_candidate()])

    retrieve_candidates(
        RetrievalRequest(intent=_intent(), limit=4),
        local=FakeBrollRetriever(results=[]),
        stock=(stock,),
        policy=DEFAULT_RETRIEVAL_POLICY,
    )

    queries = stock.searches[0].queries
    assert "formulir pendaftaran" in queries
    assert "signup form" in queries


@pytest.mark.unit
def test_the_request_budget_stops_a_search_from_paying_every_provider_forever() -> None:
    """One suggestion must not be able to spend a Workspace's whole monthly allowance."""
    first = FakeBrollRetriever(results=[_candidate(provider="pexels")])
    second = FakeBrollRetriever(results=[_candidate(provider="pixabay")])
    policy = RetrievalPolicy(
        sufficient_local_results=4,
        max_provider_requests=1,
        min_relevance=0.5,
    )

    result = retrieve_candidates(
        RetrievalRequest(intent=_intent(), limit=4),
        local=FakeBrollRetriever(results=[]),
        stock=(first, second),
        policy=policy,
    )

    assert len(first.searches) == 1
    assert second.searches == []
    assert result.provider_requests == 1


@pytest.mark.unit
def test_an_unsafe_or_untraceable_provider_result_never_reaches_the_caller() -> None:
    """A provider that returns an unusable asset must cost the member nothing."""
    stock = FakeBrollRetriever(
        results=[_candidate(safe=False), _candidate(author=""), _candidate()]
    )

    result = retrieve_candidates(
        RetrievalRequest(intent=_intent(), limit=4),
        local=FakeBrollRetriever(results=[]),
        stock=(stock,),
        policy=DEFAULT_RETRIEVAL_POLICY,
    )

    assert len(result.candidates) == 1
    assert result.refused == (UNSAFE_CANDIDATE_CODE, INCOMPLETE_PROVENANCE_CODE)


@pytest.mark.unit
def test_the_same_provider_asset_is_never_offered_twice() -> None:
    """Two providers indexing one clip must not become two suggestions of it."""
    first = FakeBrollRetriever(results=[_candidate()])
    second = FakeBrollRetriever(results=[_candidate()])

    result = retrieve_candidates(
        RetrievalRequest(intent=_intent(), limit=4),
        local=FakeBrollRetriever(results=[]),
        stock=(first, second),
        policy=RetrievalPolicy(
            sufficient_local_results=4, max_provider_requests=4, min_relevance=0.5
        ),
    )

    assert len(result.candidates) == 1


@pytest.mark.unit
def test_a_provider_that_fails_does_not_cost_the_member_the_other_providers() -> None:
    """One provider outage must not empty a search that another could have answered."""
    broken = FakeBrollRetriever(results=RuntimeError("provider exploded"))
    working = FakeBrollRetriever(results=[_candidate(provider="pixabay")])

    result = retrieve_candidates(
        RetrievalRequest(intent=_intent(), limit=4),
        local=FakeBrollRetriever(results=[]),
        stock=(broken, working),
        policy=RetrievalPolicy(
            sufficient_local_results=4, max_provider_requests=4, min_relevance=0.5
        ),
    )

    assert [candidate.provider for candidate in result.candidates] == ["pixabay"]


@pytest.mark.unit
def test_no_results_anywhere_is_an_empty_answer_rather_than_a_failure() -> None:
    """A concept nothing in the world illustrates is not an error to report."""
    result = retrieve_candidates(
        RetrievalRequest(intent=_intent(), limit=4),
        local=FakeBrollRetriever(results=[]),
        stock=(FakeBrollRetriever(results=[]),),
        policy=DEFAULT_RETRIEVAL_POLICY,
    )

    assert result.candidates == ()


@pytest.mark.unit
def test_an_intent_with_no_usable_words_matches_no_stored_asset() -> None:
    """A local search with nothing to match on must return nothing, not everything."""
    from clipah.broll.user_asset_retriever import UserAssetRetriever

    retriever = UserAssetRetriever(None, workspace_id=uuid4())  # type: ignore[arg-type]

    assert (
        retriever.search(
            request=SearchRequest(
                intent=_intent(
                    subject="a",
                    action="of",
                    setting="on",
                    search_terms_id=("di",),
                    search_terms_en=("to",),
                ),
                queries=("a",),
                limit=4,
            )
        )
        == ()
    )


@pytest.mark.unit
def test_a_term_repeated_across_both_languages_is_searched_once() -> None:
    """Sending one provider the same query twice spends a request budget for nothing."""
    from clipah.broll.retriever import intent_queries

    queries = intent_queries(
        _intent(search_terms_id=("signup form", "  "), search_terms_en=("signup form",))
    )

    assert queries == ("signup form",)
