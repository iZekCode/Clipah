"""Integration contracts for licensed, provenance-complete B-roll retrieval.

Three promises are held here. An asset without complete provenance cannot exist, because
both rows are written in one transaction. Only the one candidate that was selected is ever
downloaded. And a Workspace pays for identical footage once, however many beats want it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from clipah.assets.probe import MediaValidationError, SourceMetadata
from clipah.assets.provider_fetch import validate_provider_media_url
from clipah.assets.storage import ObjectStoreUnavailableError, StoredObject
from clipah.broll.models import (
    PLANNER_VERSION,
    BrollCoverage,
    BrollSourceType,
    BrollSuggestionStatus,
)
from clipah.broll.reranker import DeterministicVisualReranker
from clipah.broll.retriever import (
    BrollRetrievalRetryableError,
    ExternalAssetCandidate,
    FakeBrollRetriever,
    LicenseTerms,
    MediaKind,
    RetrievalPolicy,
)
from clipah.db import RuntimeRole
from clipah.jobs.broll_retrieve_task import (
    INTEGRITY_CODE,
    MEDIA_INVALID_CODE,
    REQUEST_NOT_FOUND_CODE,
    STORAGE_UNAVAILABLE_CODE,
    BrollRetrievalDependenciesBuilder,
    BrollRetrieveStageRunner,
    RetrievalDependencies,
    stock_providers_for,
)
from clipah.jobs.models import (
    JobCancelledError,
    JobContext,
    RetryableJobError,
    TerminalJobError,
)
from clipah.jobs.tasks import stage_runners
from clipah.models import (
    Asset,
    AssetKind,
    AssetProvenance,
    AssetSourceType,
    BrollPlanRequest,
    BrollSuggestion,
    ClipCandidate,
    Job,
    JobEvent,
    JobKind,
    JobStatus,
    Project,
    ProjectStatus,
    SourceKind,
    Transcript,
)
from support import provision_identity, runtime_settings

NOW = datetime(2026, 9, 5, tzinfo=UTC)
MEDIA_BYTES = b"stock-footage-bytes"
MEDIA_DIGEST = sha256(MEDIA_BYTES).digest()


@dataclass(frozen=True, slots=True)
class _Seed:
    """One Workspace with a planned suggestion and a running retrieval Job."""

    context: JobContext
    workspace_id: UUID
    project_id: UUID
    candidate_id: UUID
    suggestion_id: UUID


class _Downloader:
    """Write fixed bytes and record every URL a retrieval actually fetched."""

    def __init__(self, *, payload: bytes = MEDIA_BYTES) -> None:
        """Bind the bytes every download produces."""
        self.payload = payload
        self.urls: list[str] = []

    def download(
        self,
        url: str,
        destination: BinaryIO,
        *,
        expected_size: int,
        max_bytes: int,
        cancellation_check: Any,
    ) -> Any:
        """Record the URL, write the bytes, and report the digest ingest would."""
        del expected_size, max_bytes, cancellation_check
        self.urls.append(url)
        destination.write(self.payload)

        @dataclass(frozen=True, slots=True)
        class _Downloaded:
            size_bytes: int
            sha256: bytes

        return _Downloaded(size_bytes=len(self.payload), sha256=sha256(self.payload).digest())


class _Media:
    """A media processor that reports fixed metadata and writes a fixed proxy."""

    def __init__(self, *, probe_error: Exception | None = None) -> None:
        """Bind the failure this processor raises instead of probing, if any."""
        self.probe_error = probe_error
        self.proxies: list[Path] = []

    def probe(self, source: Path, *, cancellation_check: Any) -> SourceMetadata:
        """Report one portrait clip, or refuse the media entirely."""
        del source, cancellation_check
        if self.probe_error is not None:
            raise self.probe_error
        return SourceMetadata(
            duration_ms=9_000,
            width=1080,
            height=1920,
            video_codecs=("h264",),
            audio_codecs=("aac",),
            variable_frame_rate=False,
        )

    def generate_proxy(
        self,
        source: Path,
        output: Path,
        *,
        duration_ms: int,
        cancellation_check: Any,
        progress: Any,
    ) -> None:
        """Write one small normalized rendition beside the original."""
        del source, duration_ms, cancellation_check, progress
        output.write_bytes(b"proxy-bytes")
        self.proxies.append(output)


class _Store:
    """An object store that records every key written, and can be made unavailable."""

    def __init__(self, *, unavailable: bool = False) -> None:
        """Bind whether this store refuses every write."""
        self.unavailable = unavailable
        self.keys: list[str] = []

    def put_file(
        self, *, key: str, content_type: str, file: BinaryIO, sha256: bytes | None = None
    ) -> StoredObject:
        """Record one stored object, or report the provider as unreachable."""
        del sha256
        if self.unavailable:
            raise ObjectStoreUnavailableError("object store is unreachable")
        body = file.read()
        self.keys.append(key)
        return StoredObject(key=key, content_type=content_type, content_length=len(body))


def _candidate(**overrides: Any) -> ExternalAssetCandidate:
    """Build one complete, highly relevant stock candidate."""
    values: dict[str, Any] = {
        "provider": "pexels",
        "provider_asset_id": "12345",
        "media_kind": MediaKind.VIDEO,
        "source_url": "https://www.pexels.com/video/12345/",
        "download_url": "https://videos/hd.mp4",
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
        "query": "formulir pendaftaran",
        "safe": True,
        "description": "a hand deleting fields from a signup form on a laptop screen",
        "tags": ("formulir", "pendaftaran", "signup", "form", "laptop"),
    }
    values.update(overrides)
    return ExternalAssetCandidate(**values)


def _offline_media_url_policy(url: str) -> str:
    """Run the real destination policy against an answer that never leaves the process.

    The policy itself is proven in `tests/security/test_provider_media_urls.py`; here it
    is wired in so these fixtures exercise the same code path a worker would, without a
    DNS lookup for a fixture hostname.
    """
    return validate_provider_media_url(url, resolver=lambda host: ("93.184.216.34",))


def _dependencies(
    *,
    stock: list[Any] | None = None,
    downloader: _Downloader | None = None,
    media: _Media | None = None,
    store: _Store | None = None,
) -> RetrievalDependencies:
    """Compose deterministic capabilities in place of every external service."""
    return RetrievalDependencies(
        stock=tuple(stock if stock is not None else [FakeBrollRetriever(results=[_candidate()])]),
        reranker=DeterministicVisualReranker(),
        object_store=store or _Store(),
        downloader=downloader or _Downloader(),
        media=media or _Media(),
        media_url_policy=_offline_media_url_policy,
        retrieval_policy=RetrievalPolicy(
            sufficient_local_results=4, max_provider_requests=2, min_relevance=0.5
        ),
    )


def _runner(dependencies: RetrievalDependencies) -> BrollRetrieveStageRunner:
    """Build the runner with a frozen clock so provenance timestamps are exact."""
    return BrollRetrieveStageRunner(dependencies_factory=lambda _: dependencies, clock=lambda: NOW)


@pytest.mark.integration
def test_the_retrieval_runner_is_registered_for_its_job_kind() -> None:
    """Queued BROLL_RETRIEVE Jobs must not fall through to the unsupported-kind failure."""
    assert JobKind.BROLL_RETRIEVE in stage_runners()


@pytest.mark.integration
def test_a_selected_clip_is_stored_with_complete_provenance(engine: Engine) -> None:
    """An asset and the record of why it may be used are written in one transaction."""
    seed = _seed(engine, suffix="broll-store")
    store = _Store()
    downloader = _Downloader()
    dependencies = _dependencies(store=store, downloader=downloader)

    _runner(dependencies)(seed.context)

    with Session(engine) as session:
        asset = session.scalar(
            select(Asset).where(
                Asset.workspace_id == seed.workspace_id, Asset.kind == AssetKind.BROLL
            )
        )
        provenance = session.scalar(
            select(AssetProvenance).where(AssetProvenance.workspace_id == seed.workspace_id)
        )
    assert asset is not None
    assert asset.source_type is AssetSourceType.STOCK
    assert asset.sha256 == MEDIA_DIGEST
    assert (asset.width, asset.height) == (1080, 1920)
    assert provenance is not None
    assert provenance.asset_id == asset.id
    assert provenance.provider == "pexels"
    assert provenance.provider_asset_id == "12345"
    assert provenance.source_url == "https://www.pexels.com/video/12345/"
    assert provenance.author == "Ana Rahma"
    assert provenance.license_name == "Pexels License"
    assert provenance.terms_snapshot
    assert provenance.retrieved_at == NOW
    assert provenance.query == "formulir pendaftaran"
    assert provenance.attribution_text == "Video by Ana Rahma on Pexels"
    assert provenance.moderation_result == "safe"
    assert provenance.checksum == MEDIA_DIGEST
    assert downloader.urls == ["https://videos/hd.mp4"]


@pytest.mark.integration
def test_only_the_selected_candidate_is_ever_downloaded(engine: Engine) -> None:
    """Systematically fetching search results is both a licence breach and a bill."""
    seed = _seed(engine, suffix="broll-one-download")
    downloader = _Downloader()
    offered = [
        _candidate(provider_asset_id="a", download_url="https://videos/a.mp4"),
        _candidate(provider_asset_id="b", download_url="https://videos/b.mp4", author="Budi"),
        _candidate(provider_asset_id="c", download_url="https://videos/c.mp4", author="Citra"),
    ]

    _runner(_dependencies(stock=[FakeBrollRetriever(results=offered)], downloader=downloader))(
        seed.context
    )

    assert len(downloader.urls) == 1


@pytest.mark.integration
def test_a_stored_asset_is_normalized_into_an_original_and_a_proxy(engine: Engine) -> None:
    """The editor plays a normalized rendition, not whatever the provider happened to send."""
    seed = _seed(engine, suffix="broll-proxy")
    store = _Store()

    _runner(_dependencies(store=store))(seed.context)

    with Session(engine) as session:
        kinds = set(
            session.scalars(select(Asset.kind).where(Asset.workspace_id == seed.workspace_id))
        )
    assert {AssetKind.BROLL, AssetKind.BROLL_PROXY} <= kinds
    assert len(store.keys) == 2
    assert all(f"/projects/{seed.project_id}/broll/" in key for key in store.keys)


@pytest.mark.integration
def test_the_suggestion_points_at_its_picture_and_explains_the_choice(
    engine: Engine,
) -> None:
    """A member reviewing a suggestion must be able to see what was weighed."""
    seed = _seed(engine, suffix="broll-attach")

    _runner(_dependencies())(seed.context)

    suggestion = _suggestion(engine, seed)
    assert suggestion.asset_id is not None
    assert suggestion.source_type is BrollSourceType.STOCK
    assert suggestion.relevance_score is not None and suggestion.relevance_score > 0.5
    retrieval = suggestion.provider_metadata["retrieval"]
    assert retrieval["provider"] == "pexels"
    assert retrieval["provider_asset_id"] == "12345"
    assert set(retrieval["breakdown"]) == {
        "semantic_relevance",
        "frame_relevance",
        "technical_quality",
        "crop_viability",
        "local_fit",
        "repetition_penalty",
    }


@pytest.mark.integration
def test_retrieval_never_decides_for_the_member(engine: Engine) -> None:
    """A picture is attached to a proposal; accepting it is somebody else's action."""
    seed = _seed(engine, suffix="broll-not-decided")

    _runner(_dependencies())(seed.context)

    suggestion = _suggestion(engine, seed)
    assert suggestion.status is BrollSuggestionStatus.PROPOSED
    assert suggestion.decided_at is None


@pytest.mark.integration
def test_a_replayed_retrieval_costs_the_workspace_nothing(engine: Engine) -> None:
    """A suggestion that already has a picture is skipped, not searched and bought again."""
    seed = _seed(engine, suffix="broll-replay")
    stock = FakeBrollRetriever(results=[_candidate()])
    downloader = _Downloader()
    runner = _runner(_dependencies(stock=[stock], downloader=downloader))

    runner(seed.context)
    first = _suggestion(engine, seed).asset_id
    runner(seed.context)
    second = _suggestion(engine, seed).asset_id

    assert first == second
    assert len(stock.searches) == 1
    assert len(downloader.urls) == 1
    assert _asset_count(engine, seed) == 2


@pytest.mark.integration
def test_a_second_beat_reuses_the_footage_the_first_beat_brought_in(engine: Engine) -> None:
    """Once a Workspace holds a good picture, later beats find it instead of buying one."""
    seed = _seed(engine, suffix="broll-reuse", suggestions=2)
    downloader = _Downloader()
    stock = FakeBrollRetriever(
        results=[_candidate(provider_asset_id="12345"), _candidate(provider_asset_id="67890")]
    )

    _runner(_dependencies(stock=[stock], downloader=downloader))(seed.context)

    with Session(engine) as session:
        assets = list(
            session.scalars(
                select(Asset).where(
                    Asset.workspace_id == seed.workspace_id, Asset.kind == AssetKind.BROLL
                )
            )
        )
        attached = set(
            session.scalars(
                select(BrollSuggestion.asset_id).where(
                    BrollSuggestion.workspace_id == seed.workspace_id
                )
            )
        )
    assert len(assets) == 1
    assert attached == {assets[0].id}
    assert len(downloader.urls) == 1


@pytest.mark.integration
def test_identical_bytes_from_a_different_provider_asset_are_stored_once(
    engine: Engine,
) -> None:
    """Two provider entries of one clip must not become two copies in one Workspace."""
    first = _seed(engine, suffix="broll-checksum")
    downloader = _Downloader()
    _runner(
        _dependencies(
            stock=[FakeBrollRetriever(results=[_candidate(provider_asset_id="12345")])],
            downloader=downloader,
        )
    )(first.context)
    stored = _suggestion(engine, first).asset_id

    # A second beat whose intent shares no vocabulary with the first, so the Workspace's
    # own library cannot match it and the checksum is the only thing left to notice.
    second = _seed(
        engine,
        suffix="broll-checksum",
        reuse=first,
        beat_offset=1,
        intent_subject="a rising activation chart",
        intent_terms=("grafik aktivasi",),
    )
    _runner(
        _dependencies(
            stock=[
                FakeBrollRetriever(
                    results=[
                        _candidate(
                            provider_asset_id="99999",
                            provider="pixabay",
                            query="grafik aktivasi",
                            description="a rising activation chart on a laptop screen",
                            tags=("grafik", "aktivasi", "chart"),
                        )
                    ]
                )
            ],
            downloader=downloader,
        )
    )(second.context)

    assert _suggestion(engine, second).asset_id == stored
    assert len(downloader.urls) == 2
    with Session(engine) as session:
        assets = session.scalar(
            select(func.count())
            .select_from(Asset)
            .where(Asset.workspace_id == first.workspace_id, Asset.kind == AssetKind.BROLL)
        )
    assert assets == 1


@pytest.mark.integration
def test_another_workspaces_footage_is_never_reused(engine: Engine) -> None:
    """Reuse by checksum is scoped to one tenant, or an asset library would leak across."""
    first = _seed(engine, suffix="broll-tenant-a")
    second = _seed(engine, suffix="broll-tenant-b")

    _runner(_dependencies())(first.context)
    _runner(_dependencies())(second.context)

    with Session(engine) as session:
        owners = list(
            session.scalars(select(Asset.workspace_id).where(Asset.kind == AssetKind.BROLL))
        )
    assert sorted(owners) == sorted([first.workspace_id, second.workspace_id])


@pytest.mark.integration
def test_the_workspaces_own_footage_is_preferred_over_paying_a_provider(
    engine: Engine,
) -> None:
    """Accepted footage is free, already licensed, and already normalized."""
    seed = _seed(engine, suffix="broll-local-first")
    stock = FakeBrollRetriever(results=[_candidate()])
    downloader = _Downloader()
    _runner(_dependencies(stock=[stock], downloader=downloader))(seed.context)
    existing = _suggestion(engine, seed).asset_id

    second = _seed(engine, suffix="broll-local-first", reuse=seed, beat_offset=1)
    reuse_stock = FakeBrollRetriever(results=[_candidate(provider_asset_id="99999")])
    reuse_downloader = _Downloader()
    _runner(
        _dependencies(
            stock=[reuse_stock],
            downloader=reuse_downloader,
            store=_Store(),
        )
    )(second.context)

    assert _suggestion(engine, second).asset_id == existing
    assert reuse_downloader.urls == []


@pytest.mark.integration
def test_a_search_that_finds_nothing_leaves_the_suggestion_without_a_picture(
    engine: Engine,
) -> None:
    """A beat nothing illustrates keeps its place; generating one is a later task."""
    seed = _seed(engine, suffix="broll-nothing")

    _runner(_dependencies(stock=[FakeBrollRetriever(results=[])]))(seed.context)

    suggestion = _suggestion(engine, seed)
    assert suggestion.asset_id is None
    assert suggestion.status is BrollSuggestionStatus.PROPOSED
    assert _asset_count(engine, seed) == 0


@pytest.mark.integration
def test_nothing_relevant_enough_is_the_same_as_nothing_found(engine: Engine) -> None:
    """Below the threshold, a picture is worse than no picture."""
    seed = _seed(engine, suffix="broll-irrelevant")
    unrelated = _candidate(
        description="a sunset over an empty beach", tags=("sunset",), query="beach"
    )

    _runner(_dependencies(stock=[FakeBrollRetriever(results=[unrelated])]))(seed.context)

    assert _suggestion(engine, seed).asset_id is None


@pytest.mark.integration
def test_an_untraceable_candidate_never_becomes_an_asset(engine: Engine) -> None:
    """The provenance gate runs before anything is downloaded, let alone stored."""
    seed = _seed(engine, suffix="broll-untraceable")
    downloader = _Downloader()

    _runner(
        _dependencies(
            stock=[FakeBrollRetriever(results=[_candidate(author="")])], downloader=downloader
        )
    )(seed.context)

    assert _suggestion(engine, seed).asset_id is None
    assert downloader.urls == []
    assert _asset_count(engine, seed) == 0


@pytest.mark.integration
def test_an_unsafe_candidate_never_becomes_an_asset(engine: Engine) -> None:
    """Safe search is enforced again on this side, not trusted to the provider alone."""
    seed = _seed(engine, suffix="broll-unsafe")

    _runner(_dependencies(stock=[FakeBrollRetriever(results=[_candidate(safe=False)])]))(
        seed.context
    )

    assert _suggestion(engine, seed).asset_id is None
    assert _asset_count(engine, seed) == 0


@pytest.mark.integration
def test_every_source_failing_is_retryable_rather_than_reported_as_nothing_found(
    engine: Engine,
) -> None:
    """Telling a member their beat has no picture must be a fact, not an outage."""
    seed = _seed(engine, suffix="broll-outage")
    broken = FakeBrollRetriever(results=BrollRetrievalRetryableError("BROLL_RETRIEVAL_UNAVAILABLE"))

    with pytest.raises(RetryableJobError):
        _runner(_dependencies(stock=[broken]))(seed.context)

    assert _suggestion(engine, seed).asset_id is None


@pytest.mark.integration
def test_one_source_failing_still_lets_another_illustrate_the_beat(engine: Engine) -> None:
    """One provider being down must not cost a member the provider that was up."""
    seed = _seed(engine, suffix="broll-partial-outage")
    broken = FakeBrollRetriever(results=BrollRetrievalRetryableError("BROLL_RETRIEVAL_UNAVAILABLE"))
    working = FakeBrollRetriever(results=[_candidate(provider="pixabay")])

    _runner(_dependencies(stock=[broken, working]))(seed.context)

    assert _suggestion(engine, seed).asset_id is not None


@pytest.mark.integration
def test_unreachable_storage_is_retryable_and_stores_no_half_asset(engine: Engine) -> None:
    """A stored asset without provenance must be impossible, including under failure."""
    seed = _seed(engine, suffix="broll-storage-down")

    with pytest.raises(RetryableJobError) as error:
        _runner(_dependencies(store=_Store(unavailable=True)))(seed.context)

    assert str(error.value) == STORAGE_UNAVAILABLE_CODE
    assert _asset_count(engine, seed) == 0
    assert _provenance_count(engine, seed) == 0


@pytest.mark.integration
def test_media_that_ffprobe_refuses_is_terminal_and_stores_nothing(engine: Engine) -> None:
    """A provider that served something that is not media will serve it again on a retry."""
    seed = _seed(engine, suffix="broll-bad-media")
    media = _Media(probe_error=MediaValidationError("ASSET_INVALID_MEDIA"))

    with pytest.raises(TerminalJobError) as error:
        _runner(_dependencies(media=media))(seed.context)

    assert str(error.value) == MEDIA_INVALID_CODE
    assert _asset_count(engine, seed) == 0


@pytest.mark.integration
def test_a_job_with_no_recorded_target_fails_terminally(engine: Engine) -> None:
    """A retrieval Job that cannot say which plan it serves has nothing to retry."""
    seed = _seed(engine, suffix="broll-no-request", record_request=False)

    with pytest.raises(TerminalJobError) as error:
        _runner(_dependencies())(seed.context)

    assert str(error.value) == REQUEST_NOT_FOUND_CODE


@pytest.mark.integration
def test_each_suggestion_is_reported_durably_including_the_ones_that_found_nothing(
    engine: Engine,
) -> None:
    """A member asking why a beat has no picture must find the answer in the Job's history."""
    seed = _seed(engine, suffix="broll-events")

    _runner(_dependencies(stock=[FakeBrollRetriever(results=[])]))(seed.context)

    with Session(engine) as session:
        payloads = list(
            session.scalars(
                select(JobEvent.payload).where(
                    JobEvent.workspace_id == seed.workspace_id,
                    JobEvent.job_id == seed.context.job_id,
                )
            )
        )
    reported = [
        payload for payload in payloads if isinstance(payload, dict) and "selected" in payload
    ]
    assert [entry["selected"] for entry in reported] == [False]


@pytest.mark.integration
def test_the_production_dependencies_refuse_to_run_without_object_storage() -> None:
    """A clip that cannot be stored would have to be hotlinked, which licences forbid."""
    settings = runtime_settings(RuntimeRole.WORKER)

    with pytest.raises(RuntimeError):
        BrollRetrievalDependenciesBuilder(settings)


def _suggestion(engine: Engine, seed: _Seed) -> BrollSuggestion:
    """Read back the one suggestion a test seeded."""
    with Session(engine) as session:
        row = session.scalar(
            select(BrollSuggestion).where(BrollSuggestion.id == seed.suggestion_id)
        )
    assert row is not None
    return row


def _asset_count(engine: Engine, seed: _Seed) -> int:
    """Count the B-roll assets stored for one Workspace."""
    with Session(engine) as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(Asset)
                .where(
                    Asset.workspace_id == seed.workspace_id,
                    Asset.kind.in_((AssetKind.BROLL, AssetKind.BROLL_PROXY)),
                )
            )
            or 0
        )


def _provenance_count(engine: Engine, seed: _Seed) -> int:
    """Count the provenance rows stored for one Workspace."""
    with Session(engine) as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(AssetProvenance)
                .where(AssetProvenance.workspace_id == seed.workspace_id)
            )
            or 0
        )


def _seed(
    engine: Engine,
    *,
    suffix: str,
    suggestions: int = 1,
    record_request: bool = True,
    reuse: _Seed | None = None,
    beat_offset: int = 0,
    intent_subject: str = "a shortened signup form",
    intent_terms: tuple[str, ...] = ("formulir pendaftaran",),
) -> _Seed:
    """Create one running BROLL_RETRIEVE Job over a planned, proposed suggestion."""
    if reuse is not None:
        user_id, workspace_id = reuse.context.user_id, reuse.workspace_id
        project_id, candidate_id = reuse.project_id, reuse.candidate_id
    else:
        user_id, workspace_id = provision_identity(engine, suffix=suffix)
        project_id = uuid4()
        candidate_id = uuid4()
    job_id = uuid4()
    suggestion_ids = [uuid4() for _ in range(suggestions)]
    with engine.begin() as connection:
        if reuse is None:
            source_id = uuid4()
            transcript_id = uuid4()
            connection.execute(
                Project.__table__.insert().values(
                    id=project_id,
                    workspace_id=workspace_id,
                    created_by_user_id=user_id,
                    name=f"Retrieval {suffix}",
                    status=ProjectStatus.READY,
                    source_kind=SourceKind.UPLOAD,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            connection.execute(
                Asset.__table__.insert().values(
                    id=source_id,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    kind=AssetKind.SOURCE,
                    source_type=AssetSourceType.USER_UPLOAD,
                    storage_key=f"workspaces/{workspace_id}/projects/{project_id}/source/x",
                    content_type="video/mp4",
                    size_bytes=100,
                    duration_ms=120_000,
                    sha256=b"s" * 32,
                )
            )
            connection.execute(
                Transcript.__table__.insert().values(
                    id=transcript_id,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    asset_id=source_id,
                    provider="assemblyai",
                    provider_version="1.0.0",
                    model="universal-3-pro",
                    language="id",
                    full_text="",
                    words=[],
                    speaker_segments=[],
                    utterances=[],
                    duration_ms=120_000,
                    raw_result_storage_key="raw.json",
                )
            )
            connection.execute(
                ClipCandidate.__table__.insert().values(
                    id=candidate_id,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    transcript_id=transcript_id,
                    rank=1,
                    score=0.9,
                    hook="hook",
                    payoff="payoff",
                    reason="reason",
                    category="insight",
                    tags=[],
                    start_ms=0,
                    end_ms=60_000,
                    start_word_id="w000001",
                    end_word_id="w000120",
                    transcript_excerpt="",
                    context_dependencies=[],
                    score_breakdown={},
                    context_warnings=[],
                    visual_opportunities=[],
                    model_metadata={"exposed": True},
                )
            )
        connection.execute(
            Job.__table__.insert().values(
                id=job_id,
                workspace_id=workspace_id,
                project_id=project_id,
                kind=JobKind.BROLL_RETRIEVE,
                status=JobStatus.RUNNING,
                stage="queued",
                progress=0,
                attempt=1,
                idempotency_key=f"broll-retrieve-{job_id}",
            )
        )
        if record_request:
            connection.execute(
                BrollPlanRequest.__table__.insert().values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    candidate_id=candidate_id,
                    job_id=job_id,
                    coverage=BrollCoverage.BALANCED,
                    requested_by_user_id=user_id,
                )
            )
        for index, suggestion_id in enumerate(suggestion_ids):
            connection.execute(
                BrollSuggestion.__table__.insert().values(
                    id=suggestion_id,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    candidate_id=candidate_id,
                    planner_version=PLANNER_VERSION,
                    coverage=BrollCoverage.BALANCED,
                    beat_start_word_id=f"w{index + beat_offset + 21:06d}",
                    beat_end_word_id=f"w{index + beat_offset + 24:06d}",
                    start_ms=10_000 + (index + beat_offset) * 10_000,
                    end_ms=12_000 + (index + beat_offset) * 10_000,
                    visual_intent={
                        "subject": intent_subject,
                        "action": "a hand deleting form fields",
                        "setting": "a laptop screen on a desk",
                        "mood": "focused",
                        "portrait_suitable": True,
                        "factual_risk_flags": [],
                        "confidence": 0.8,
                    },
                    search_terms={
                        "id": list(intent_terms),
                        "en": ["signup form"],
                    },
                    exclusions=["handshake"],
                    status=BrollSuggestionStatus.PROPOSED,
                    placement_reason="The sentence names an object the viewer cannot see",
                    provider_metadata={},
                    created_at=NOW,
                )
            )
    return _Seed(
        context=JobContext(
            job_id=job_id,
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
            attempt=1,
            settings=runtime_settings(RuntimeRole.WORKER),
        ),
        workspace_id=workspace_id,
        project_id=project_id,
        candidate_id=candidate_id,
        suggestion_id=suggestion_ids[0],
    )


@pytest.mark.integration
def test_the_production_dependencies_compose_every_configured_stock_provider() -> None:
    """A deployment with both credentials must reach both providers, each behind a cache."""
    settings = runtime_settings(
        RuntimeRole.WORKER,
        object_store_bucket="clipah",
        object_store_access_key_id="key",
        object_store_secret_access_key="secret",
        object_store_endpoint="http://127.0.0.1:59001",
        redis_url="redis://127.0.0.1:56380/0",
        pexels_api_key="pexels-key",
        pixabay_api_key="pixabay-key",
    )

    assert len(stock_providers_for(settings)) == 2


@pytest.mark.integration
def test_a_deployment_with_no_stock_credentials_reaches_no_provider() -> None:
    """B-roll is optional, so a Workspace may run on its own footage alone."""
    settings = runtime_settings(
        RuntimeRole.WORKER,
        object_store_bucket="clipah",
        object_store_access_key_id="key",
        object_store_secret_access_key="secret",
        redis_url="redis://127.0.0.1:56380/0",
    )

    assert stock_providers_for(settings) == ()


@pytest.mark.integration
def test_a_suggestion_whose_intent_cannot_be_read_fails_terminally(engine: Engine) -> None:
    """A stored intent this stage cannot rebuild must stop the Job, not be searched blind."""
    seed = _seed(engine, suffix="broll-bad-intent")
    with engine.begin() as connection:
        connection.execute(
            BrollSuggestion.__table__.update()
            .where(BrollSuggestion.id == seed.suggestion_id)
            .values(visual_intent={"subject": "only this"})
        )

    with pytest.raises(TerminalJobError) as error:
        _runner(_dependencies())(seed.context)

    assert str(error.value) == INTEGRITY_CODE


@pytest.mark.integration
def test_a_suggestion_deleted_mid_retrieval_is_reported_rather_than_ignored(
    engine: Engine,
) -> None:
    """A picture attached to a row that no longer exists would be silently lost."""
    seed = _seed(engine, suffix="broll-vanishing")

    class _DeletingStore(_Store):
        def put_file(self, **kwargs: Any) -> StoredObject:
            stored = super().put_file(**kwargs)
            with engine.begin() as connection:
                connection.execute(
                    BrollSuggestion.__table__.delete().where(
                        BrollSuggestion.id == seed.suggestion_id
                    )
                )
            return stored

    with pytest.raises(TerminalJobError) as error:
        _runner(_dependencies(store=_DeletingStore()))(seed.context)

    assert str(error.value) == INTEGRITY_CODE


@pytest.mark.integration
def test_a_cancelled_retrieval_stops_before_it_pays_a_provider(engine: Engine) -> None:
    """Cancelling retrieval must stop the work, not merely discard what it bought."""
    seed = _seed(engine, suffix="broll-cancelled")
    with engine.begin() as connection:
        connection.execute(
            Job.__table__.update()
            .where(Job.id == seed.context.job_id)
            .values(cancel_requested_at=NOW)
        )
    downloader = _Downloader()

    with pytest.raises(JobCancelledError):
        _runner(_dependencies(downloader=downloader))(seed.context)

    assert downloader.urls == []
    assert _asset_count(engine, seed) == 0
