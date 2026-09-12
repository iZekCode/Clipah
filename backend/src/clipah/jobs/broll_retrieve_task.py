"""Durable BROLL_RETRIEVE runner: find, license, and store a picture for each suggestion.

Retrieval is the stage that turns a proposal into something a member can actually see. It
searches the Workspace's own accepted footage before paying a provider, refuses anything it
cannot fully trace, downloads only the one candidate it selected, and writes the asset and
its provenance in a single transaction — so an asset without provenance cannot exist.

A suggestion that finds nothing good enough keeps its beat and gets no picture. That is a
truthful outcome: Task 31 owns offering to generate one, and a member is better served by a
suggestion with no footage than by footage that does not mean what the beat says.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.assets.ingest import MediaProcessor, SourceDownloader
from clipah.assets.keys import broll_asset_key
from clipah.assets.probe import MediaValidationError
from clipah.assets.provider_fetch import UnsafeProviderUrlError, validate_provider_media_url
from clipah.assets.storage import ObjectStore, ObjectStoreUnavailableError
from clipah.broll.models import (
    PLANNER_VERSION,
    BrollCoverage,
    BrollSourceType,
    BrollSuggestionStatus,
    VisualIntent,
)
from clipah.broll.reranker import (
    DEFAULT_RANKING_POLICY,
    DeterministicVisualReranker,
    RerankingPolicy,
    ScoredCandidate,
    VisualReranker,
    rerank_candidates,
)
from clipah.broll.retriever import (
    DEFAULT_RETRIEVAL_POLICY,
    RETRIEVAL_UNAVAILABLE_CODE,
    BrollRetrievalRetryableError,
    BrollRetriever,
    ExternalAssetCandidate,
    RetrievalPolicy,
    RetrievalRequest,
    provenance_of,
    retrieve_candidates,
)
from clipah.broll.user_asset_retriever import (
    WORKSPACE_PROVIDER,
    UserAssetRetriever,
    find_by_checksum,
)
from clipah.config import Settings
from clipah.db import RuntimeRole, session_scope
from clipah.jobs.models import JobCancelledError, JobContext, RetryableJobError, TerminalJobError
from clipah.jobs.use_cases import update_job_progress
from clipah.jobs.workspace import job_workspace
from clipah.models import (
    Asset,
    AssetKind,
    AssetProvenance,
    AssetSourceType,
    BrollPlanRequest,
    BrollSuggestion,
)
from clipah.observability.metrics import count

BROLL_RETRIEVE_STAGE = "broll_retrieve"
SUGGESTION_STAGE = "broll_retrieve_suggestion"
REQUEST_NOT_FOUND_CODE = "BROLL_RETRIEVE_REQUEST_NOT_FOUND"
INTEGRITY_CODE = "BROLL_RETRIEVE_INTEGRITY"
MEDIA_INVALID_CODE = "BROLL_ASSET_INVALID_MEDIA"
STORAGE_UNAVAILABLE_CODE = "BROLL_ASSET_STORAGE_UNAVAILABLE"
MEDIA_URL_UNSAFE_CODE = "BROLL_ASSET_SOURCE_UNSAFE"

#: A retrieved clip is a few seconds of B-roll, never a feature film.
MAX_ASSET_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RetrievalDependencies:
    """Every external capability retrieval needs, injected so tests can pin all of them."""

    stock: tuple[BrollRetriever, ...]
    reranker: VisualReranker
    object_store: ObjectStore
    downloader: SourceDownloader
    media: MediaProcessor
    retrieval_policy: RetrievalPolicy = DEFAULT_RETRIEVAL_POLICY
    ranking_policy: RerankingPolicy = DEFAULT_RANKING_POLICY
    # A stock catalogue names where its media lives, and a worker fetches that from inside
    # the deployment's own network, so the destination is proven before anything is read.
    media_url_policy: Callable[[str], str] = validate_provider_media_url


DependenciesFactory = Callable[[Settings], RetrievalDependencies]


class BrollRetrievalIntegrityError(Exception):
    """Refuse persisted evidence the retrieval stage cannot trust."""


@dataclass(frozen=True, slots=True)
class _PendingSuggestion:
    """One proposed suggestion still waiting for a picture, outside any transaction."""

    suggestion_id: UUID
    project_id: UUID
    intent: VisualIntent


@dataclass(frozen=True, slots=True)
class _Selected:
    """The asset one suggestion ended up pointing at, and how it was chosen."""

    asset_id: UUID
    source_type: BrollSourceType
    relevance: float
    metadata: dict[str, Any]


class BrollRetrieveStageRunner:
    """Give each proposed suggestion of one plan a licensed picture, exactly once."""

    def __init__(
        self,
        *,
        dependencies_factory: DependenciesFactory,
        clock: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        """Bind production or deterministic capabilities and an injectable clock."""
        self._dependencies_factory = dependencies_factory
        self._clock = clock

    def __call__(self, context: JobContext) -> None:
        """Run one retrieval attempt through stable retryable and terminal codes."""
        try:
            context.raise_if_cancelled()
            coverage, candidate_id = self._target(context)
            dependencies = self._dependencies_factory(context.settings)
            for pending in self._pending(context, candidate_id=candidate_id, coverage=coverage):
                context.raise_if_cancelled()
                self._fill(context, pending=pending, dependencies=dependencies)
        except JobCancelledError:
            raise
        except BrollRetrievalRetryableError as error:
            raise RetryableJobError(error.code) from None
        except ObjectStoreUnavailableError:
            raise RetryableJobError(STORAGE_UNAVAILABLE_CODE) from None
        except MediaValidationError:
            raise TerminalJobError(MEDIA_INVALID_CODE) from None
        except BrollRetrievalIntegrityError:
            raise TerminalJobError(INTEGRITY_CODE) from None

    def _target(self, context: JobContext) -> tuple[BrollCoverage, UUID]:
        """Read which plan this Job was admitted to illustrate."""
        with _transaction(context) as session:
            row = session.scalar(
                select(BrollPlanRequest).where(
                    BrollPlanRequest.workspace_id == context.workspace_id,
                    BrollPlanRequest.job_id == context.job_id,
                )
            )
            if row is None:
                raise TerminalJobError(REQUEST_NOT_FOUND_CODE)
            return row.coverage, row.candidate_id

    def _pending(
        self, context: JobContext, *, candidate_id: UUID, coverage: BrollCoverage
    ) -> tuple[_PendingSuggestion, ...]:
        """Read the suggestions of this plan that still have no picture.

        A suggestion that already points at an asset is skipped rather than searched
        again, which is what makes a redelivered Job cost the Workspace nothing.
        """
        with _transaction(context) as session:
            rows = session.scalars(
                select(BrollSuggestion)
                .where(
                    BrollSuggestion.workspace_id == context.workspace_id,
                    BrollSuggestion.candidate_id == candidate_id,
                    BrollSuggestion.planner_version == PLANNER_VERSION,
                    BrollSuggestion.coverage == coverage,
                    BrollSuggestion.status == BrollSuggestionStatus.PROPOSED,
                    BrollSuggestion.asset_id.is_(None),
                )
                .order_by(BrollSuggestion.start_ms, BrollSuggestion.id)
            ).all()
            return tuple(
                _PendingSuggestion(
                    suggestion_id=row.id,
                    project_id=row.project_id,
                    intent=_intent_of(row),
                )
                for row in rows
            )

    def _fill(
        self,
        context: JobContext,
        *,
        pending: _PendingSuggestion,
        dependencies: RetrievalDependencies,
    ) -> None:
        """Search, rank, store, and attach one picture for one suggestion."""
        retrieved_at = self._clock()
        with _transaction(context) as session:
            local = UserAssetRetriever(session, workspace_id=context.workspace_id)
            result = retrieve_candidates(
                RetrievalRequest(
                    intent=pending.intent,
                    limit=dependencies.retrieval_policy.sufficient_local_results,
                ),
                local=local,
                stock=dependencies.stock,
                policy=dependencies.retrieval_policy,
                retrieved_at_iso=retrieved_at.isoformat(),
                sufficient=_ranks_well_enough(pending.intent, dependencies),
            )
        ranked = rerank_candidates(
            candidates=result.candidates,
            intent=pending.intent,
            reranker=dependencies.reranker,
            policy=dependencies.ranking_policy,
        )
        if not ranked:
            if result.failed_sources and not result.candidates:
                # Every source this search could reach failed. Telling a member their beat
                # has no picture would be a lie about the world rather than a fact about it.
                raise BrollRetrievalRetryableError(RETRIEVAL_UNAVAILABLE_CODE)
            self._report(context, suggestion_id=pending.suggestion_id, selected=None)
            return
        best = ranked[0]
        selected = self._store(
            context,
            pending=pending,
            best=best,
            dependencies=dependencies,
            retrieved_at=retrieved_at,
        )
        self._attach(context, suggestion_id=pending.suggestion_id, selected=selected)
        self._report(context, suggestion_id=pending.suggestion_id, selected=selected)

    def _store(
        self,
        context: JobContext,
        *,
        pending: _PendingSuggestion,
        best: ScoredCandidate,
        dependencies: RetrievalDependencies,
        retrieved_at: datetime,
    ) -> _Selected:
        """Reuse footage this Workspace already holds, or fetch exactly the one selected."""
        candidate = best.candidate
        metadata = _selection_metadata(best)
        count("clipah.broll.decision", decision="selected", provider=candidate.provider)
        if candidate.provider == WORKSPACE_PROVIDER:
            return _Selected(
                asset_id=UUID(candidate.provider_asset_id),
                source_type=BrollSourceType.USER_ASSET,
                relevance=best.relevance,
                metadata=metadata,
            )
        asset_id = self._download_and_store(
            context,
            pending=pending,
            candidate=candidate,
            dependencies=dependencies,
            retrieved_at=retrieved_at,
        )
        return _Selected(
            asset_id=asset_id,
            source_type=BrollSourceType.STOCK,
            relevance=best.relevance,
            metadata=metadata,
        )

    def _download_and_store(
        self,
        context: JobContext,
        *,
        pending: _PendingSuggestion,
        candidate: ExternalAssetCandidate,
        dependencies: RetrievalDependencies,
        retrieved_at: datetime,
    ) -> UUID:
        """Fetch one selected clip, validate it, and store it with its provenance.

        Only the selected candidate is ever downloaded. Systematically fetching every
        search result would be both a licence violation and a bill nobody agreed to.
        """
        provenance = provenance_of(candidate, retrieved_at_iso=retrieved_at.isoformat())
        with job_workspace(context.job_id) as directory:
            original = directory / "original"
            download_url = _safe_download_url(candidate, dependencies=dependencies)
            with original.open("wb") as handle:
                downloaded = dependencies.downloader.download(
                    download_url,
                    handle,
                    expected_size=0,
                    max_bytes=MAX_ASSET_BYTES,
                    cancellation_check=context.raise_if_cancelled,
                )
            with _transaction(context) as session:
                existing = find_by_checksum(
                    session,
                    workspace_id=context.workspace_id,
                    sha256=downloaded.sha256,
                )
            if existing is not None:
                return existing.asset_id

            metadata = dependencies.media.probe(
                original, cancellation_check=context.raise_if_cancelled
            )
            proxy = directory / "proxy.mp4"
            dependencies.media.generate_proxy(
                original,
                proxy,
                duration_ms=metadata.duration_ms,
                cancellation_check=context.raise_if_cancelled,
                progress=lambda _: None,
            )
            asset_id = uuid4()
            original_key = broll_asset_key(
                workspace_id=context.workspace_id,
                project_id=pending.project_id,
                asset_id=asset_id,
                kind=AssetKind.BROLL,
            )
            proxy_key = broll_asset_key(
                workspace_id=context.workspace_id,
                project_id=pending.project_id,
                asset_id=asset_id,
                kind=AssetKind.BROLL_PROXY,
            )
            stored = _put(dependencies.object_store, original, original_key, "video/mp4")
            proxy_stored = _put(dependencies.object_store, proxy, proxy_key, "video/mp4")

        with _transaction(context) as session:
            session.add(
                Asset(
                    id=asset_id,
                    workspace_id=context.workspace_id,
                    project_id=pending.project_id,
                    kind=AssetKind.BROLL,
                    source_type=AssetSourceType.STOCK,
                    storage_key=original_key,
                    content_type="video/mp4",
                    size_bytes=stored.content_length,
                    duration_ms=metadata.duration_ms,
                    width=metadata.width,
                    height=metadata.height,
                    video_codec=metadata.video_codec,
                    audio_codec=metadata.audio_codec,
                    sha256=downloaded.sha256,
                )
            )
            proxy_id = uuid4()
            session.add(
                Asset(
                    id=proxy_id,
                    workspace_id=context.workspace_id,
                    project_id=pending.project_id,
                    kind=AssetKind.BROLL_PROXY,
                    source_type=AssetSourceType.DERIVED,
                    storage_key=proxy_key,
                    content_type="video/mp4",
                    size_bytes=proxy_stored.content_length,
                    duration_ms=metadata.duration_ms,
                    width=metadata.width,
                    height=metadata.height,
                    video_codec=metadata.video_codec,
                    audio_codec=metadata.audio_codec,
                    sha256=downloaded.sha256,
                )
            )
            session.flush()
            # Provenance is written in the same transaction as the asset, so an asset
            # nobody can trace cannot come into existence even for an instant.
            session.add(
                AssetProvenance(
                    workspace_id=context.workspace_id,
                    asset_id=asset_id,
                    provider=provenance.provider,
                    provider_asset_id=provenance.provider_asset_id,
                    source_url=provenance.source_url,
                    author=provenance.author,
                    author_url=provenance.author_url,
                    license_name=provenance.license_name,
                    license_url=provenance.license_url,
                    terms_snapshot=provenance.terms_snapshot,
                    retrieved_at=retrieved_at,
                    query=provenance.query,
                    moderation_result=provenance.moderation_result,
                    attribution_text=provenance.attribution_text,
                    checksum=downloaded.sha256,
                )
            )
            session.flush()
        return asset_id

    def _attach(self, context: JobContext, *, suggestion_id: UUID, selected: _Selected) -> None:
        """Point one suggestion at its picture without touching the member's decision."""
        with _transaction(context) as session:
            row = session.scalar(
                select(BrollSuggestion).where(
                    BrollSuggestion.workspace_id == context.workspace_id,
                    BrollSuggestion.id == suggestion_id,
                )
            )
            if row is None:
                raise BrollRetrievalIntegrityError("suggestion disappeared during retrieval")
            row.source_type = selected.source_type
            row.asset_id = selected.asset_id
            row.relevance_score = selected.relevance
            row.provider_metadata = {**dict(row.provider_metadata), **selected.metadata}
            session.flush()

    def _report(
        self, context: JobContext, *, suggestion_id: UUID, selected: _Selected | None
    ) -> None:
        """Record durably what happened to one suggestion, including finding nothing."""
        with _transaction(context) as session:
            update_job_progress(
                session,
                workspace_id=context.workspace_id,
                job_id=context.job_id,
                stage=SUGGESTION_STAGE,
                progress=0.5,
                now=self._clock(),
                detail={
                    "suggestion_id": str(suggestion_id),
                    "selected": selected is not None,
                    "source_type": None if selected is None else selected.source_type.value,
                },
            )


def _safe_download_url(
    candidate: ExternalAssetCandidate, *, dependencies: RetrievalDependencies
) -> str:
    """Prove the catalogue's chosen destination before this worker connects to it.

    The URL came from a provider's search response, so it is the one value in a retrieval
    that somebody outside this deployment gets to choose. A destination inside our own
    network is refused terminally: retrying it would only repeat the request the attacker
    wanted.
    """
    try:
        return dependencies.media_url_policy(candidate.download_url)
    except UnsafeProviderUrlError as error:
        raise TerminalJobError(MEDIA_URL_UNSAFE_CODE) from error


def _put(store: ObjectStore, path: Path, key: str, content_type: str) -> Any:
    """Upload one exact local file under one server-chosen key."""
    with path.open("rb") as handle:
        return store.put_file(key=key, content_type=content_type, file=handle)


def _selection_metadata(best: ScoredCandidate) -> dict[str, Any]:
    """Describe why this picture was chosen, without any provider payload."""
    return {
        "retrieval": {
            "provider": best.candidate.provider,
            "provider_asset_id": best.candidate.provider_asset_id,
            "relevance": best.relevance,
            "variety_adjusted": best.variety_adjusted,
            "reranker_model": best.reranker_model,
            "reranker_model_version": best.reranker_model_version,
            "breakdown": {
                "semantic_relevance": best.breakdown.semantic_relevance,
                "frame_relevance": best.breakdown.frame_relevance,
                "technical_quality": best.breakdown.technical_quality,
                "crop_viability": best.breakdown.crop_viability,
                "local_fit": best.breakdown.local_fit,
                "repetition_penalty": best.breakdown.repetition_penalty,
            },
        }
    }


def _intent_of(row: BrollSuggestion) -> VisualIntent:
    """Rebuild the intent one suggestion was planned with, refusing an unreadable row."""
    try:
        intent = dict(row.visual_intent)
        terms = dict(row.search_terms)
        return VisualIntent(
            subject=str(intent["subject"]),
            action=str(intent["action"]),
            setting=str(intent["setting"]),
            mood=str(intent["mood"]),
            search_terms_id=tuple(str(term) for term in terms["id"]),
            search_terms_en=tuple(str(term) for term in terms["en"]),
            portrait_suitable=bool(intent["portrait_suitable"]),
            exclusions=tuple(str(item) for item in row.exclusions),
            factual_risk_flags=tuple(str(flag) for flag in intent["factual_risk_flags"]),
            confidence=float(intent["confidence"]),
        )
    except (KeyError, TypeError, ValueError):
        raise BrollRetrievalIntegrityError("persisted visual intent is unreadable") from None


def _ranks_well_enough(
    intent: VisualIntent, dependencies: RetrievalDependencies
) -> Callable[[Sequence[ExternalAssetCandidate]], bool]:
    """Stop searching as soon as one gathered candidate would actually be offered.

    This is what makes "search accepted user assets first" mean something: a Workspace
    that already holds a picture good enough for this beat never pays a provider for a
    second one, and a Workspace whose own footage merely mentions the right words still
    gets a real search.
    """

    def decide(candidates: Sequence[ExternalAssetCandidate]) -> bool:
        return bool(
            rerank_candidates(
                candidates=candidates,
                intent=intent,
                reranker=dependencies.reranker,
                policy=dependencies.ranking_policy,
            )
        )

    return decide


@contextmanager
def _transaction(context: JobContext) -> Iterator[Session]:
    """Open one short least-privilege worker transaction for this Job's tenant."""
    with session_scope(
        settings=context.settings,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        runtime_role=RuntimeRole.WORKER,
    ) as session:
        yield session


def stock_providers_for(settings: Settings) -> tuple[BrollRetriever, ...]:
    """Compose the stock providers a deployment configured, each behind its own cache.

    A provider with no configured credential is simply absent rather than a startup
    failure: a deployment may run with one stock source, or with none at all, because
    B-roll is optional and a Workspace's own footage may be all it wants.
    """
    from clipah.broll import pexels_adapter, pixabay_adapter
    from clipah.broll.search_cache import CachedBrollRetriever, RedisSearchCache

    cache = RedisSearchCache(_redis_client(settings))
    stock: list[BrollRetriever] = []
    if settings.pexels_api_key is not None:
        stock.append(
            CachedBrollRetriever(
                retriever=pexels_adapter.PexelsBrollRetriever(
                    api_key=settings.pexels_api_key.get_secret_value()
                ),
                cache=cache,
                provider=pexels_adapter.PROVIDER,
                ttl_seconds=pexels_adapter.CACHE_TTL_SECONDS,
            )
        )
    if settings.pixabay_api_key is not None:
        stock.append(
            CachedBrollRetriever(
                retriever=pixabay_adapter.PixabayBrollRetriever(
                    api_key=settings.pixabay_api_key.get_secret_value()
                ),
                cache=cache,
                provider=pixabay_adapter.PROVIDER,
                ttl_seconds=pixabay_adapter.CACHE_TTL_SECONDS,
            )
        )
    return tuple(stock)


def BrollRetrievalDependenciesBuilder(  # noqa: N802 - a factory named for what it returns
    settings: Settings,
) -> RetrievalDependencies:
    """Compose the configured stock providers, each behind its own terms-length cache.

    A provider with no configured credential is simply absent rather than a startup
    failure: a deployment may run with one stock source, or with none at all, and B-roll
    is optional to the product. Object storage is not optional, because a selected clip
    that cannot be stored would have to be hotlinked, which the licences forbid.

    Constructing this needs the pinned media toolchain, because `_validated_media_runner`
    proves the FFmpeg version at worker start rather than at the first encode. The parts
    that make a decision live in `stock_providers_for`, which is testable on its own.
    """
    from clipah.assets.ingest import HttpxSourceDownloader
    from clipah.assets.storage import observed_s3_store
    from clipah.jobs.ingest_task import _validated_media_runner

    if (
        settings.object_store_bucket is None
        or settings.object_store_access_key_id is None
        or settings.object_store_secret_access_key is None
    ):
        raise RuntimeError("B-roll retrieval requires configured object storage")

    return RetrievalDependencies(  # pragma: no cover - needs the pinned media toolchain
        stock=stock_providers_for(settings),
        reranker=DeterministicVisualReranker(),
        object_store=observed_s3_store(
            bucket=settings.object_store_bucket,
            endpoint_url=settings.object_store_endpoint,
            access_key_id=settings.object_store_access_key_id.get_secret_value(),
            secret_access_key=settings.object_store_secret_access_key.get_secret_value(),
        ),
        downloader=HttpxSourceDownloader(),
        media=_validated_media_runner(),
        retrieval_policy=RetrievalPolicy(
            sufficient_local_results=settings.broll_sufficient_local_results,
            max_provider_requests=settings.broll_max_provider_requests,
            min_relevance=settings.broll_min_relevance,
        ),
        ranking_policy=RerankingPolicy(
            min_relevance=settings.broll_min_relevance,
            min_width=settings.broll_min_asset_width,
            min_height=settings.broll_min_asset_height,
            max_aspect_ratio=settings.broll_max_aspect_ratio,
            repetition_penalty=settings.broll_repetition_penalty,
        ),
    )


def _redis_client(settings: Settings) -> Any:
    """Build the Redis client the search cache reads through."""
    from redis import Redis

    return Redis.from_url(str(settings.redis_url))


broll_retrieve_stage_runner = BrollRetrieveStageRunner(
    dependencies_factory=BrollRetrievalDependenciesBuilder
)
