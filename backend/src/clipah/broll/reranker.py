"""Adapter-neutral reranking of retrieved B-roll candidates.

Retrieval finds pictures whose metadata mentions the right words. This module decides which
of them a member would actually accept, and says why across every dimension it weighed. No
embedding or vision SDK type appears in anything it returns: a vision model, where one is
configured at all, is reached through the `FrameRelevanceProvider` port and is recorded by
name and version beside every score it influenced.

Some rules are refusals rather than weights. A candidate naming one of the brand's
exclusions, one too small to survive a vertical export, or one too wide to crop to 9:16 at
all is dropped — a member is better served by no picture than by the least-bad wrong one.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from clipah.broll.models import VisualIntent
from clipah.broll.retriever import ExternalAssetCandidate

_TARGET_ASPECT = 9 / 16
_QUALITY_REFERENCE_PIXELS = 1080 * 1920
_TOKEN = re.compile(r"[^\w]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class RerankingPolicy:
    """Every number reranking may use, kept as data so a deployment configures it."""

    min_relevance: float
    min_width: int
    min_height: int
    max_aspect_ratio: float
    repetition_penalty: float


DEFAULT_RANKING_POLICY = RerankingPolicy(
    min_relevance=0.5,
    min_width=320,
    min_height=320,
    max_aspect_ratio=2.0,
    repetition_penalty=0.15,
)


@dataclass(frozen=True, slots=True)
class RelevanceBreakdown:
    """The dimensions behind one candidate's score, each within 0-1.

    ``frame_relevance`` is ``None`` rather than zero when no vision provider was
    configured: a dimension nobody measured must not be reported as one that scored badly.
    """

    semantic_relevance: float
    frame_relevance: float | None
    technical_quality: float
    crop_viability: float
    local_fit: float
    repetition_penalty: float


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    """One candidate, its overall relevance, and the evidence behind that number.

    ``relevance`` says how well this picture matches the intent and is the number stored
    beside the suggestion. ``variety_adjusted`` is the same number after repetition is
    charged, and decides only the order candidates are offered in — a third clip by one
    photographer is a variety problem, never evidence that it stopped being relevant.
    """

    candidate: ExternalAssetCandidate
    relevance: float
    variety_adjusted: float
    breakdown: RelevanceBreakdown
    reranker_model: str
    reranker_model_version: str


class FrameRelevanceProvider(Protocol):
    """Provider-independent capability to judge what a candidate's frames actually show."""

    @property
    def model(self) -> str:
        """Name the model whose judgement is being recorded."""

    @property
    def model_version(self) -> str:
        """Name the version of that model."""

    def score(self, *, intent: VisualIntent, candidate: ExternalAssetCandidate) -> float:
        """Return how well sampled frames match the intent, within 0-1."""


class VisualReranker(Protocol):
    """Provider-independent capability to score one candidate against one intent."""

    def score(
        self,
        *,
        intent: VisualIntent,
        candidate: ExternalAssetCandidate,
        policy: RerankingPolicy,
    ) -> ScoredCandidate | None:
        """Score one candidate on its own merits, or refuse it by returning nothing."""


class DeterministicVisualReranker:
    """Score candidates from their own metadata, and from frames where a provider exists."""

    def __init__(self, *, frames: FrameRelevanceProvider | None = None) -> None:
        """Bind the optional vision provider whose judgement outranks uploader text."""
        self._frames = frames

    def score(
        self,
        *,
        intent: VisualIntent,
        candidate: ExternalAssetCandidate,
        policy: RerankingPolicy,
    ) -> ScoredCandidate | None:
        """Weigh one candidate, refusing the cases no weighting should be able to rescue."""
        if _names_an_exclusion(intent, candidate):
            return None
        if candidate.width < policy.min_width or candidate.height < policy.min_height:
            return None
        crop = _crop_viability(candidate, policy)
        if crop is None:
            return None

        semantic = _semantic_relevance(intent, candidate)
        frame = (
            None
            if self._frames is None
            else _clamp(self._frames.score(intent=intent, candidate=candidate))
        )
        quality = _technical_quality(candidate)
        local = _local_fit(intent, candidate)

        meaning = semantic if frame is None else (semantic + frame * 2) / 3
        relevance = _clamp(meaning * 0.5 + quality * 0.15 + crop * 0.2 + local * 0.15)
        if relevance < policy.min_relevance:
            return None
        return ScoredCandidate(
            candidate=candidate,
            relevance=relevance,
            variety_adjusted=relevance,
            breakdown=RelevanceBreakdown(
                semantic_relevance=semantic,
                frame_relevance=frame,
                technical_quality=quality,
                crop_viability=crop,
                local_fit=local,
                repetition_penalty=0.0,
            ),
            reranker_model="" if self._frames is None else self._frames.model,
            reranker_model_version="" if self._frames is None else self._frames.model_version,
        )


def rerank_candidates(
    *,
    candidates: Sequence[ExternalAssetCandidate],
    intent: VisualIntent,
    reranker: VisualReranker,
    policy: RerankingPolicy = DEFAULT_RANKING_POLICY,
) -> tuple[ScoredCandidate, ...]:
    """Order candidates best first, charging repetition as each author and source recurs.

    Each candidate is first scored entirely on its own merits, so which provider answered
    first is never mistaken for evidence about which picture is better. Repetition is then
    charged walking down that merit order, which means the best clip by a photographer is
    the one that survives and the fourth is the one that sinks — the opposite of charging
    in whatever order the identifiers happened to sort in.
    """
    scored = [
        result
        for candidate in sorted(candidates, key=lambda item: item.identity)
        if (result := reranker.score(intent=intent, candidate=candidate, policy=policy)) is not None
    ]
    scored.sort(key=lambda item: (-item.relevance, item.candidate.identity))

    seen_authors: dict[str, int] = {}
    seen_providers: dict[str, int] = {}
    adjusted: list[ScoredCandidate] = []
    for item in scored:
        candidate = item.candidate
        repetition = _repetition(candidate, seen_authors, seen_providers, policy)
        seen_authors[candidate.author] = seen_authors.get(candidate.author, 0) + 1
        seen_providers[candidate.provider] = seen_providers.get(candidate.provider, 0) + 1
        adjusted.append(
            ScoredCandidate(
                candidate=candidate,
                relevance=item.relevance,
                variety_adjusted=_clamp(item.relevance * (1 - repetition)),
                breakdown=RelevanceBreakdown(
                    semantic_relevance=item.breakdown.semantic_relevance,
                    frame_relevance=item.breakdown.frame_relevance,
                    technical_quality=item.breakdown.technical_quality,
                    crop_viability=item.breakdown.crop_viability,
                    local_fit=item.breakdown.local_fit,
                    repetition_penalty=repetition,
                ),
                reranker_model=item.reranker_model,
                reranker_model_version=item.reranker_model_version,
            )
        )
    adjusted.sort(key=lambda item: (-item.variety_adjusted, item.candidate.identity))
    return tuple(adjusted)


class FakeFrameRelevanceProvider:
    """Deterministic frame judgement used where real vision work would be inappropriate."""

    def __init__(self, *, scores: Mapping[str, float], model: str, model_version: str) -> None:
        """Bind one score per provider asset id, and the model identity to record."""
        self._scores = dict(scores)
        self._model = model
        self._model_version = model_version
        self.requests: list[str] = []

    @property
    def model(self) -> str:
        """Name the model whose judgement is being recorded."""
        return self._model

    @property
    def model_version(self) -> str:
        """Name the version of that model."""
        return self._model_version

    def score(self, *, intent: VisualIntent, candidate: ExternalAssetCandidate) -> float:
        """Return the bound score, treating an unlisted candidate as neutral."""
        del intent
        self.requests.append(candidate.provider_asset_id)
        return self._scores.get(candidate.provider_asset_id, 0.5)


def _tokens(*values: str) -> set[str]:
    """Reduce text to comparable lowercase tokens without inventing stems."""
    joined = " ".join(values).casefold()
    return {token for token in _TOKEN.split(joined) if token}


def _names_an_exclusion(intent: VisualIntent, candidate: ExternalAssetCandidate) -> bool:
    """Report whether a candidate names something the member ruled out."""
    described = _tokens(candidate.description, " ".join(candidate.tags), candidate.query)
    return any(_tokens(exclusion) & described for exclusion in intent.exclusions)


def _semantic_relevance(intent: VisualIntent, candidate: ExternalAssetCandidate) -> float:
    """Measure how much of the intent's own vocabulary the candidate actually carries."""
    wanted = _tokens(
        intent.subject,
        intent.action,
        intent.setting,
        " ".join(intent.search_terms_en),
        " ".join(intent.search_terms_id),
    )
    described = _tokens(candidate.description, " ".join(candidate.tags), candidate.query)
    return _clamp(len(wanted & described) / len(wanted) * 2)


def _local_fit(intent: VisualIntent, candidate: ExternalAssetCandidate) -> float:
    """Measure whether the candidate answers the Indonesian half of the intent."""
    local_terms = _tokens(" ".join(intent.search_terms_id))
    if not local_terms:
        return 0.5
    described = _tokens(candidate.description, " ".join(candidate.tags), candidate.query)
    return _clamp(len(local_terms & described) / len(local_terms))


def _technical_quality(candidate: ExternalAssetCandidate) -> float:
    """Measure resolution against one vertical export, which is what Clipah produces."""
    return _clamp((candidate.width * candidate.height) / _QUALITY_REFERENCE_PIXELS)


def _crop_viability(candidate: ExternalAssetCandidate, policy: RerankingPolicy) -> float | None:
    """Measure how much of the frame survives a 9:16 crop, or refuse an impossible one."""
    ratio = candidate.width / candidate.height
    if ratio > policy.max_aspect_ratio:
        return None
    if ratio <= _TARGET_ASPECT:
        return 1.0
    return _clamp(_TARGET_ASPECT / ratio)


def _repetition(
    candidate: ExternalAssetCandidate,
    seen_authors: Mapping[str, int],
    seen_providers: Mapping[str, int],
    policy: RerankingPolicy,
) -> float:
    """Charge a candidate for every earlier clip sharing its author or its source."""
    repeats = seen_authors.get(candidate.author, 0) + seen_providers.get(candidate.provider, 0)
    return _clamp(repeats * policy.repetition_penalty)


def _clamp(value: float) -> float:
    """Keep every reported dimension inside the 0-1 range it promises."""
    return max(0.0, min(1.0, value))
