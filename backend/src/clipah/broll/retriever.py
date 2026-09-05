"""Provider-neutral B-roll retrieval: the port, the candidate shape, and the search order.

Two rules live here and nowhere else. A picture may only become selectable when it carries
complete provenance — provider, asset identity, source URL, author, licence, terms snapshot,
retrieval date, query, moderation, and the attribution a member may have to display — and
the Workspace's own accepted footage is always searched before anybody pays a stock
provider. Nothing in this module knows that Pexels or Pixabay exist.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from clipah.broll.models import VisualIntent

INCOMPLETE_PROVENANCE_CODE = "BROLL_ASSET_PROVENANCE_INCOMPLETE"
UNSAFE_CANDIDATE_CODE = "BROLL_ASSET_UNSAFE"

RETRIEVAL_RATE_LIMITED_CODE = "BROLL_RETRIEVAL_RATE_LIMITED"
RETRIEVAL_UNAVAILABLE_CODE = "BROLL_RETRIEVAL_UNAVAILABLE"
RETRIEVAL_REJECTED_CODE = "BROLL_RETRIEVAL_REJECTED"
RETRIEVAL_INVALID_CODE = "BROLL_RETRIEVAL_INVALID"

MODERATION_SAFE = "safe"

#: Whether the candidates gathered so far are enough to stop searching.
SufficiencyTest = Callable[["Sequence[ExternalAssetCandidate]"], bool]


class MediaKind(StrEnum):
    """What kind of media one candidate is, which decides how it is normalized."""

    IMAGE = "image"
    VIDEO = "video"


class BrollRetrievalRetryableError(Exception):
    """Represent a temporary retrieval failure through a stable code."""

    def __init__(self, code: str) -> None:
        """Retain only public-safe retry information."""
        self.code = code
        super().__init__(code)


class BrollRetrievalTerminalError(Exception):
    """Represent a permanent retrieval failure through a stable code."""

    def __init__(self, code: str) -> None:
        """Retain only public-safe terminal information."""
        self.code = code
        super().__init__(code)


class ProvenanceError(Exception):
    """Refuse one candidate that may not be stored, through a stable public code."""

    def __init__(self, code: str) -> None:
        """Retain only the public-safe reason this candidate was refused."""
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class LicenseTerms:
    """The licence one candidate is offered under, as it read when it was retrieved."""

    name: str
    url: str
    attribution_required: bool
    snapshot: str


@dataclass(frozen=True, slots=True)
class ExternalAssetCandidate:
    """One normalized candidate from any source, carrying no provider payload type.

    ``download_url`` is deliberately transient. It is used once, for the one candidate a
    member's plan actually selects, and is never persisted: Clipah stores the media it
    selected rather than linking to somebody else's server forever.
    """

    provider: str
    provider_asset_id: str
    media_kind: MediaKind
    source_url: str
    download_url: str
    author: str
    author_url: str
    license: LicenseTerms
    width: int
    height: int
    duration_ms: int | None
    attribution_text: str
    query: str
    safe: bool
    description: str = ""
    tags: tuple[str, ...] = ()

    @property
    def identity(self) -> tuple[str, str]:
        """Name this asset independently of which search turned it up."""
        return (self.provider, self.provider_asset_id)


@dataclass(frozen=True, slots=True)
class AssetProvenanceRecord:
    """Everything a later licence review would ask about one stored asset."""

    provider: str
    provider_asset_id: str
    source_url: str
    author: str
    author_url: str
    license_name: str
    license_url: str
    terms_snapshot: str
    retrieved_at: str
    query: str
    attribution_text: str
    moderation_result: str


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """One search a retriever is asked to perform, in every language of the intent."""

    intent: VisualIntent
    queries: tuple[str, ...]
    limit: int


class BrollRetriever(Protocol):
    """Provider-independent capability to find candidates for one visual intent."""

    def search(self, *, request: SearchRequest) -> Sequence[ExternalAssetCandidate]:
        """Return normalized candidates, or nothing when the source has none."""


@dataclass(frozen=True, slots=True)
class RetrievalPolicy:
    """How hard Clipah tries locally before it starts paying a provider."""

    sufficient_local_results: int
    max_provider_requests: int
    min_relevance: float


DEFAULT_RETRIEVAL_POLICY = RetrievalPolicy(
    sufficient_local_results=4,
    max_provider_requests=2,
    min_relevance=0.5,
)


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    """One intent to illustrate and how many candidates a reviewer should be offered."""

    intent: VisualIntent
    limit: int


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """The candidates one search produced, and what it cost and refused on the way."""

    candidates: tuple[ExternalAssetCandidate, ...]
    provider_requests: int
    refused: tuple[str, ...] = field(default=())
    #: Sources that failed outright, so a caller can tell "nothing exists" from "nobody
    #: answered". Finding nothing is an ordinary result; finding nothing because every
    #: source was unreachable is not, and must not be reported to a member as the former.
    failed_sources: int = 0


def provenance_of(
    candidate: ExternalAssetCandidate, *, retrieved_at_iso: str
) -> AssetProvenanceRecord:
    """Build the provenance one candidate must carry, or refuse it entirely.

    This is the gate the plan names: an asset becomes selectable only after every field is
    present. A missing author or terms snapshot is not a cosmetic gap — it is the reason
    nobody could later answer whether the footage was ever licensed for this use.
    """
    if not candidate.safe:
        raise ProvenanceError(UNSAFE_CANDIDATE_CODE)
    required = (
        candidate.provider,
        candidate.provider_asset_id,
        candidate.source_url,
        candidate.author,
        candidate.license.name,
        candidate.license.url,
        candidate.license.snapshot,
        candidate.query,
        candidate.attribution_text,
        retrieved_at_iso,
    )
    if not all(value.strip() for value in required):
        raise ProvenanceError(INCOMPLETE_PROVENANCE_CODE)
    return AssetProvenanceRecord(
        provider=candidate.provider,
        provider_asset_id=candidate.provider_asset_id,
        source_url=candidate.source_url,
        author=candidate.author,
        author_url=candidate.author_url,
        license_name=candidate.license.name,
        license_url=candidate.license.url,
        terms_snapshot=candidate.license.snapshot,
        retrieved_at=retrieved_at_iso,
        query=candidate.query,
        attribution_text=candidate.attribution_text,
        moderation_result=MODERATION_SAFE,
    )


def intent_queries(intent: VisualIntent) -> tuple[str, ...]:
    """Offer a retriever both languages of one intent, Indonesian first.

    Searching an Indonesian concept only in English has already translated away the thing
    that made it local, so both sets of terms are always sent and the original order is
    preserved.
    """
    seen: list[str] = []
    for term in (*intent.search_terms_id, *intent.search_terms_en):
        cleaned = " ".join(term.split())
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return tuple(seen)


def retrieve_candidates(
    request: RetrievalRequest,
    *,
    local: BrollRetriever,
    stock: Sequence[BrollRetriever],
    policy: RetrievalPolicy = DEFAULT_RETRIEVAL_POLICY,
    retrieved_at_iso: str = "1970-01-01T00:00:00+00:00",
    sufficient: SufficiencyTest | None = None,
) -> RetrievalResult:
    """Search the Workspace's own footage first, then stock, inside one request budget.

    What counts as "enough" is injected rather than fixed, because counting results is a
    poor test of it: one clip that actually illustrates the beat is enough, and four that
    merely mention the right words are not. Production passes a test that reranks, so a
    provider is paid only when the Workspace's own footage genuinely cannot answer.
    """
    decide = sufficient or _count_is_enough(policy)
    search = SearchRequest(
        intent=request.intent,
        queries=intent_queries(request.intent),
        limit=request.limit,
    )
    refused: list[str] = []
    failures: list[str] = []
    seen: set[tuple[str, str]] = set()
    accepted: list[ExternalAssetCandidate] = []

    _collect(local, search, accepted, seen, refused, failures, retrieved_at_iso=retrieved_at_iso)
    if decide(tuple(accepted)):
        return RetrievalResult(
            candidates=tuple(accepted[: request.limit]),
            provider_requests=0,
            refused=tuple(refused),
            failed_sources=len(failures),
        )

    provider_requests = 0
    for retriever in stock:
        if provider_requests >= policy.max_provider_requests:
            break
        provider_requests += 1
        _collect(
            retriever, search, accepted, seen, refused, failures, retrieved_at_iso=retrieved_at_iso
        )
        if decide(tuple(accepted)):
            break
    return RetrievalResult(
        candidates=tuple(accepted[: request.limit]),
        provider_requests=provider_requests,
        refused=tuple(refused),
        failed_sources=len(failures),
    )


def _count_is_enough(policy: RetrievalPolicy) -> SufficiencyTest:
    """Judge sufficiency by count alone, for callers with no reranker to hand."""

    def decide(candidates: Sequence[ExternalAssetCandidate]) -> bool:
        return len(candidates) >= policy.sufficient_local_results

    return decide


def _collect(
    retriever: BrollRetriever,
    search: SearchRequest,
    accepted: list[ExternalAssetCandidate],
    seen: set[tuple[str, str]],
    refused: list[str],
    failures: list[str],
    *,
    retrieved_at_iso: str,
) -> None:
    """Take one source's answer, keeping only candidates that may actually be stored.

    A source that fails costs the search only its own results: another provider may still
    be able to illustrate the beat, and one outage is not evidence that nothing exists.
    The failure is counted rather than swallowed, so a caller can still tell the two apart
    when every source failed.
    """
    try:
        results = retriever.search(request=search)
    except Exception as error:
        failures.append(getattr(error, "code", RETRIEVAL_UNAVAILABLE_CODE))
        return
    for candidate in results:
        if candidate.identity in seen:
            continue
        try:
            provenance_of(candidate, retrieved_at_iso=retrieved_at_iso)
        except ProvenanceError as error:
            refused.append(error.code)
            continue
        seen.add(candidate.identity)
        accepted.append(candidate)


class FakeBrollRetriever:
    """Deterministic retriever used where real provider work would be inappropriate."""

    def __init__(self, *, results: Sequence[ExternalAssetCandidate] | Exception) -> None:
        """Bind one fixed answer, or the failure this source always raises."""
        self._results = results
        self.searches: list[SearchRequest] = []

    def search(self, *, request: SearchRequest) -> Sequence[ExternalAssetCandidate]:
        """Record the exact search before replaying the bound outcome."""
        self.searches.append(request)
        if isinstance(self._results, Exception):
            raise self._results
        return self._results
