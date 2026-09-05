"""Contracts for the adapter-neutral visual reranker.

Retrieval finds pictures that mention the right words. Reranking decides which of them a
member would actually accept: whether it means the right thing, looks good enough, survives
a 9:16 crop, respects the brand's exclusions, fits locally, and is not the fourth clip by
the same author. No embedding or vision SDK type may appear in anything it returns.
"""

from __future__ import annotations

from typing import Any

import pytest

from clipah.broll.models import VisualIntent
from clipah.broll.reranker import (
    DEFAULT_RANKING_POLICY,
    DeterministicVisualReranker,
    FakeFrameRelevanceProvider,
    RerankingPolicy,
    ScoredCandidate,
    rerank_candidates,
)
from clipah.broll.retriever import ExternalAssetCandidate, LicenseTerms, MediaKind


def _intent(**overrides: Any) -> VisualIntent:
    """Build one schema-valid intent, varying only what a test is about."""
    values: dict[str, Any] = {
        "subject": "a shortened signup form",
        "action": "a hand deleting form fields",
        "setting": "a laptop screen on a desk",
        "mood": "focused",
        "search_terms_id": ("formulir pendaftaran", "layar laptop"),
        "search_terms_en": ("signup form", "laptop screen"),
        "portrait_suitable": True,
        "exclusions": ("handshake", "whiteboard"),
        "factual_risk_flags": (),
        "confidence": 0.8,
    }
    values.update(overrides)
    return VisualIntent(**values)


def _candidate(**overrides: Any) -> ExternalAssetCandidate:
    """Build one complete candidate that scores well on every dimension by default."""
    values: dict[str, Any] = {
        "provider": "pexels",
        "provider_asset_id": "12345",
        "media_kind": MediaKind.VIDEO,
        "source_url": "https://www.pexels.com/video/12345/",
        "download_url": "https://videos.pexels.com/video-files/12345/hd.mp4",
        "author": "Ana Rahma",
        "author_url": "https://www.pexels.com/@ana-rahma",
        "license": LicenseTerms(
            name="Pexels License",
            url="https://www.pexels.com/license/",
            attribution_required=False,
            snapshot="Free to use.",
        ),
        "width": 1080,
        "height": 1920,
        "duration_ms": 9_000,
        "attribution_text": "Video by Ana Rahma on Pexels",
        "query": "signup form",
        "safe": True,
        "description": "a hand deleting fields from a signup form on a laptop screen",
        "tags": ("signup", "form", "laptop"),
    }
    values.update(overrides)
    return ExternalAssetCandidate(**values)


def _rank(
    candidates: list[ExternalAssetCandidate],
    *,
    intent: VisualIntent | None = None,
    policy: RerankingPolicy = DEFAULT_RANKING_POLICY,
    frames: FakeFrameRelevanceProvider | None = None,
) -> tuple[ScoredCandidate, ...]:
    """Rank with the production reranker so tests measure the shipped weights."""
    return rerank_candidates(
        candidates=candidates,
        intent=intent or _intent(),
        reranker=DeterministicVisualReranker(frames=frames),
        policy=policy,
    )


@pytest.mark.unit
def test_a_candidate_matching_the_intent_outranks_one_that_does_not() -> None:
    """Semantic relevance is the dimension the whole feature rests on."""
    relevant = _candidate(provider_asset_id="relevant")
    irrelevant = _candidate(
        provider_asset_id="irrelevant",
        description="a sunset over an empty beach",
        tags=("sunset", "beach"),
        query="beach",
    )

    ranked = _rank([irrelevant, relevant])

    assert next(item.candidate.provider_asset_id for item in ranked) == "relevant"


@pytest.mark.unit
def test_an_irrelevant_candidate_below_the_threshold_is_dropped_rather_than_ranked_last() -> None:
    """Offering a member the least-bad wrong picture wastes their attention."""
    ranked = _rank(
        [
            _candidate(
                provider_asset_id="unrelated",
                description="a sunset over an empty beach",
                tags=("sunset",),
                query="beach",
            )
        ]
    )

    assert ranked == ()


@pytest.mark.unit
def test_a_candidate_naming_a_brand_exclusion_is_refused_outright() -> None:
    """An exclusion is a rule the member set, not a preference to be outweighed."""
    ranked = _rank(
        [
            _candidate(
                provider_asset_id="excluded",
                description="a signup form beside a corporate handshake",
                tags=("signup", "handshake"),
            )
        ]
    )

    assert ranked == ()


@pytest.mark.unit
def test_an_exclusion_is_matched_ignoring_case() -> None:
    """A model that capitalized its own exclusion has not withdrawn it."""
    ranked = _rank([_candidate(provider_asset_id="excluded", tags=("signup", "Handshake"))])

    assert ranked == ()


@pytest.mark.unit
def test_a_portrait_candidate_outranks_a_landscape_one_for_a_vertical_clip() -> None:
    """Everything Clipah exports is 9:16, so crop viability is not a nicety."""
    portrait = _candidate(provider_asset_id="portrait", width=1080, height=1920)
    landscape = _candidate(provider_asset_id="landscape", width=1920, height=1080)

    ranked = _rank([landscape, portrait])

    assert next(item.candidate.provider_asset_id for item in ranked) == "portrait"


@pytest.mark.unit
def test_an_extremely_wide_candidate_cannot_be_cropped_to_vertical_and_is_refused() -> None:
    """A panorama cropped to 9:16 keeps a sliver of itself and none of its meaning."""
    ranked = _rank([_candidate(provider_asset_id="panorama", width=5120, height=1080)])

    assert ranked == ()


@pytest.mark.unit
def test_a_higher_resolution_candidate_outranks_a_low_resolution_one() -> None:
    """Technical quality is what separates two clips that mean the same thing."""
    sharp = _candidate(provider_asset_id="sharp", width=1080, height=1920)
    soft = _candidate(provider_asset_id="soft", width=270, height=480)

    ranked = _rank([soft, sharp])

    assert next(item.candidate.provider_asset_id for item in ranked) == "sharp"


@pytest.mark.unit
def test_a_candidate_below_the_minimum_resolution_is_refused() -> None:
    """Upscaling a thumbnail into a vertical export produces a visible mistake."""
    ranked = _rank([_candidate(provider_asset_id="tiny", width=160, height=284)])

    assert ranked == ()


@pytest.mark.unit
def test_a_locally_relevant_candidate_outranks_a_generic_one_for_indonesian_intent() -> None:
    """An Indonesian concept illustrated with Indonesian footage is the point."""
    shared = "a hand deleting fields from a signup form on a laptop screen"
    local = _candidate(
        provider_asset_id="local",
        description=shared,
        tags=("formulir", "pendaftaran", "layar"),
        query="formulir pendaftaran",
    )
    generic = _candidate(
        provider_asset_id="generic",
        description=shared,
        tags=("business", "office", "corporate"),
        query="signup form",
    )

    ranked = _rank([generic, local])

    assert next(item.candidate.provider_asset_id for item in ranked) == "local"


@pytest.mark.unit
def test_a_third_clip_from_one_author_is_penalized_against_a_new_author() -> None:
    """A montage by one photographer reads as a stock-library dump, not as editing."""
    ranked = _rank(
        [
            _candidate(provider_asset_id="a1", author="Ana Rahma"),
            _candidate(provider_asset_id="a2", author="Ana Rahma"),
            _candidate(provider_asset_id="a3", author="Ana Rahma"),
            _candidate(provider_asset_id="b1", author="Budi Santoso"),
        ]
    )

    ordering = [item.candidate.provider_asset_id for item in ranked]
    assert ordering.index("b1") < ordering.index("a3")


@pytest.mark.unit
def test_a_third_clip_from_one_provider_is_penalized_against_a_new_provider() -> None:
    """Spreading across sources is how a cut stops looking like one search result page."""
    ranked = _rank(
        [
            _candidate(provider_asset_id="p1", provider="pexels", author="A"),
            _candidate(provider_asset_id="p2", provider="pexels", author="B"),
            _candidate(provider_asset_id="p3", provider="pexels", author="C"),
            _candidate(provider_asset_id="x1", provider="pixabay", author="D"),
        ]
    )

    ordering = [item.candidate.provider_asset_id for item in ranked]
    assert ordering.index("x1") < ordering.index("p3")


@pytest.mark.unit
def test_sampled_frame_relevance_moves_the_order_when_a_provider_is_configured() -> None:
    """What the footage actually shows outranks what its uploader typed about it."""
    honest = _candidate(provider_asset_id="honest")
    mislabelled = _candidate(provider_asset_id="mislabelled")
    frames = FakeFrameRelevanceProvider(
        scores={"honest": 1.0, "mislabelled": 0.0},
        model="fake-vision",
        model_version="1",
    )

    ranked = _rank([mislabelled, honest], frames=frames)

    assert next(item.candidate.provider_asset_id for item in ranked) == "honest"


@pytest.mark.unit
def test_frame_relevance_is_recorded_as_unmeasured_when_no_provider_is_configured() -> None:
    """A dimension nobody measured must not be reported as one that scored well."""
    ranked = _rank([_candidate()])

    assert ranked[0].breakdown.frame_relevance is None
    assert ranked[0].reranker_model == ""
    assert ranked[0].reranker_model_version == ""


@pytest.mark.unit
def test_the_reranker_model_and_version_are_recorded_when_one_is_used() -> None:
    """A score is only attributable if the thing that produced it is named beside it."""
    frames = FakeFrameRelevanceProvider(
        scores={"12345": 0.9}, model="fake-vision", model_version="2026-09"
    )

    ranked = _rank([_candidate()], frames=frames)

    assert ranked[0].reranker_model == "fake-vision"
    assert ranked[0].reranker_model_version == "2026-09"


@pytest.mark.unit
def test_every_scored_candidate_explains_itself_across_all_dimensions() -> None:
    """A member who disagrees with a ranking must be able to see what it weighed."""
    ranked = _rank([_candidate()])

    breakdown = ranked[0].breakdown
    assert 0.0 <= breakdown.semantic_relevance <= 1.0
    assert 0.0 <= breakdown.technical_quality <= 1.0
    assert 0.0 <= breakdown.crop_viability <= 1.0
    assert 0.0 <= breakdown.local_fit <= 1.0
    assert 0.0 <= breakdown.repetition_penalty <= 1.0
    assert 0.0 <= ranked[0].relevance <= 1.0


@pytest.mark.unit
def test_ranking_is_identical_across_repeated_runs() -> None:
    """A replayed retrieval Job must converge on the picture it already chose."""
    candidates = [
        _candidate(provider_asset_id=str(index), author=f"Author {index}") for index in range(6)
    ]

    assert _rank(list(candidates)) == _rank(list(candidates))


@pytest.mark.unit
def test_ranking_does_not_depend_on_the_order_the_providers_answered_in() -> None:
    """Which provider replied first is not evidence about which picture is better."""
    candidates = [
        _candidate(provider_asset_id="a", author="A"),
        _candidate(provider_asset_id="b", author="B"),
        _candidate(provider_asset_id="c", author="C"),
    ]

    forward = [item.candidate.provider_asset_id for item in _rank(list(candidates))]
    backward = [item.candidate.provider_asset_id for item in _rank(list(reversed(candidates)))]

    assert forward == backward


@pytest.mark.unit
def test_a_policy_may_raise_the_relevance_floor_without_touching_the_weights() -> None:
    """Deployments tune how sure Clipah must be, not what it means to be sure."""
    strict = RerankingPolicy(
        min_relevance=0.99,
        min_width=320,
        min_height=320,
        max_aspect_ratio=2.0,
        repetition_penalty=0.15,
    )

    assert _rank([_candidate()], policy=strict) == ()


@pytest.mark.unit
def test_no_candidates_at_all_ranks_to_nothing_rather_than_failing() -> None:
    """An empty search is an ordinary outcome for a concept nothing illustrates."""
    assert _rank([]) == ()


@pytest.mark.unit
def test_an_intent_whose_local_terms_are_punctuation_scores_local_fit_neutrally() -> None:
    """A planner term with no words in it is not evidence for or against a candidate."""
    ranked = _rank([_candidate()], intent=_intent(search_terms_id=("---",)))

    assert ranked
    assert ranked[0].breakdown.local_fit == pytest.approx(0.5)
