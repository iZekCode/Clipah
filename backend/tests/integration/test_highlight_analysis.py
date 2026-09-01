"""Integration contracts for durable, retry-safe highlight analysis."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from clipah.db import RuntimeRole
from clipah.highlights.analyzer import AnalysisPolicy
from clipah.highlights.provider import (
    DETERMINISTIC_PROVIDER,
    DeterministicHighlightProvider,
    ExtractionResult,
    FakeHighlightProvider,
    HighlightProvider,
    HighlightProviderRetryableError,
    HighlightProviderTerminalError,
    RerankResult,
)
from clipah.highlights.rerank import RankingPolicy
from clipah.jobs.analyze_task import AnalysisDependencies, AnalyzeStageRunner
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
    AssetSourceType,
    ClipCandidate,
    Job,
    JobEvent,
    JobKind,
    JobStatus,
    Project,
    ProjectStatus,
    ProviderUsage,
    SourceKind,
    Transcript,
)
from clipah.transcripts.models import TranscriptResult
from support import provision_identity, runtime_settings

WORDS_PER_SECOND = 2
DURATION_SECONDS = 400


@pytest.mark.integration
def test_analysis_runner_is_registered_for_the_existing_job_kind() -> None:
    """Queued ANALYZE Jobs must not fall through to the unsupported-kind failure."""
    assert JobKind.ANALYZE in stage_runners()


@pytest.mark.integration
def test_worker_persists_ranked_candidates_and_usage_then_converges_on_redelivery(
    engine: Engine,
) -> None:
    """One analysis must persist reviewable candidates and never pay the provider twice."""
    context, transcript_id = _seed_analysis(engine, suffix="analysis-success")
    calls: list[str] = []

    def factory(transcript: TranscriptResult) -> HighlightProvider:
        calls.append(transcript.provider)
        return DeterministicHighlightProvider(transcript=transcript)

    runner = AnalyzeStageRunner(
        dependencies_factory=lambda _: AnalysisDependencies(provider_factory=factory)
    )

    runner(context)
    runner(context)

    with Session(engine) as session:
        candidates = list(
            session.scalars(
                select(ClipCandidate)
                .where(
                    ClipCandidate.workspace_id == context.workspace_id,
                    ClipCandidate.project_id == context.project_id,
                )
                .order_by(ClipCandidate.rank)
            )
        )
        usage = list(
            session.scalars(
                select(ProviderUsage).where(
                    ProviderUsage.workspace_id == context.workspace_id,
                    ProviderUsage.job_id == context.job_id,
                )
            )
        )
    assert calls == ["assemblyai"]
    assert 3 <= len(candidates) <= 30
    assert [candidate.rank for candidate in candidates] == list(range(1, len(candidates) + 1))
    first = candidates[0]
    assert first.transcript_id == transcript_id
    assert 20_000 <= first.end_ms - first.start_ms <= 90_000
    assert first.transcript_excerpt
    assert first.start_word_id.startswith("w")
    assert first.end_word_id > first.start_word_id
    assert first.payoff
    assert set(first.score_breakdown) >= {"hook", "payoff", "narrative_completeness"}
    assert first.model_metadata["provider"] == DETERMINISTIC_PROVIDER
    assert first.model_metadata["prompt_version"]
    assert first.model_metadata["schema_version"]
    assert first.model_metadata["request_ids"] is not None
    assert first.model_metadata["latency_ms"] >= 0
    assert first.model_metadata["exposed"] is True
    assert sum(1 for candidate in candidates if candidate.model_metadata["exposed"]) == min(
        10, len(candidates)
    )
    assert float(candidates[0].score) >= float(candidates[-1].score)
    assert usage
    assert {row.operation for row in usage} == {"highlight_extract", "highlight_rerank"}
    assert all(row.provider == DETERMINISTIC_PROVIDER for row in usage)


@pytest.mark.integration
def test_only_the_exposed_ranks_are_marked_for_review(engine: Engine) -> None:
    """Persisting more candidates than reviewers see must still expose the best ones only."""
    context, _ = _seed_analysis(engine, suffix="analysis-exposure")
    runner = AnalyzeStageRunner(
        dependencies_factory=lambda _: AnalysisDependencies(
            provider_factory=lambda transcript: DeterministicHighlightProvider(
                transcript=transcript
            )
        ),
        policy=AnalysisPolicy(ranking=RankingPolicy(keep=30, expose=2)),
    )

    runner(context)

    with Session(engine) as session:
        exposed_ranks = sorted(
            candidate.rank
            for candidate in session.scalars(
                select(ClipCandidate).where(ClipCandidate.workspace_id == context.workspace_id)
            )
            if candidate.model_metadata["exposed"]
        )
    assert exposed_ranks == [1, 2]


@pytest.mark.integration
def test_one_failed_window_is_recorded_and_the_remaining_windows_complete(
    engine: Engine,
) -> None:
    """A window the provider refuses must cost that window only, not the whole analysis."""
    context, _ = _seed_analysis(engine, suffix="analysis-window")

    def factory(transcript: TranscriptResult) -> HighlightProvider:
        deterministic = DeterministicHighlightProvider(transcript=transcript)
        results: list[ExtractionResult | Exception] = [
            HighlightProviderTerminalError("HIGHLIGHT_WINDOW_TOO_LARGE")
        ]
        return _replaying_provider(deterministic, transcript=transcript, results=results)

    runner = AnalyzeStageRunner(
        dependencies_factory=lambda _: AnalysisDependencies(provider_factory=factory)
    )

    runner(context)

    with Session(engine) as session:
        stages = list(
            session.scalars(
                select(JobEvent.payload)
                .where(
                    JobEvent.workspace_id == context.workspace_id,
                    JobEvent.job_id == context.job_id,
                )
                .order_by(JobEvent.sequence)
            )
        )
        candidates = session.scalar(
            select(func.count())
            .select_from(ClipCandidate)
            .where(ClipCandidate.workspace_id == context.workspace_id)
        )
    assert candidates is not None and candidates >= 3
    assert any("HIGHLIGHT_WINDOW_TOO_LARGE" in str(payload) for payload in stages)


@pytest.mark.integration
def test_too_few_surviving_candidates_fail_the_job_terminally(engine: Engine) -> None:
    """A Job that cannot offer three moments must fail instead of reporting success."""
    context, _ = _seed_analysis(engine, suffix="analysis-empty")
    provider = FakeHighlightProvider(
        results=[ExtractionResult(proposals=(), call=_deterministic_call())] * 8,
        rerank_result=RerankResult(order=(), call=_deterministic_call()),
    )
    runner = AnalyzeStageRunner(
        dependencies_factory=lambda _: AnalysisDependencies(provider_factory=lambda _t: provider)
    )

    with pytest.raises(TerminalJobError) as raised:
        runner(context)

    assert str(raised.value) == "ANALYSIS_INSUFFICIENT_CANDIDATES"


@pytest.mark.integration
def test_a_provider_outage_leaves_the_job_retryable(engine: Engine) -> None:
    """An outage is transient, so the Job must stay recoverable and store nothing."""
    context, _ = _seed_analysis(engine, suffix="analysis-outage")
    provider = FakeHighlightProvider(
        results=[HighlightProviderRetryableError("HIGHLIGHT_PROVIDER_UNAVAILABLE")] * 8,
        rerank_result=RerankResult(order=(), call=_deterministic_call()),
    )
    runner = AnalyzeStageRunner(
        dependencies_factory=lambda _: AnalysisDependencies(provider_factory=lambda _t: provider)
    )

    with pytest.raises(RetryableJobError) as raised:
        runner(context)

    assert str(raised.value) == "ANALYSIS_INSUFFICIENT_CANDIDATES"
    with Session(engine) as session:
        stored = session.scalar(
            select(func.count())
            .select_from(ClipCandidate)
            .where(ClipCandidate.workspace_id == context.workspace_id)
        )
    assert stored == 0


@pytest.mark.integration
def test_a_missing_transcript_fails_terminally_without_provider_work(engine: Engine) -> None:
    """Analysis cannot invent a transcript, so it must refuse before spending anything."""
    context, _ = _seed_analysis(engine, suffix="analysis-missing", include_transcript=False)
    provider = FakeHighlightProvider(
        rerank_result=RerankResult(order=(), call=_deterministic_call())
    )
    runner = AnalyzeStageRunner(
        dependencies_factory=lambda _: AnalysisDependencies(provider_factory=lambda _t: provider)
    )

    with pytest.raises(TerminalJobError) as raised:
        runner(context)

    assert str(raised.value) == "ANALYSIS_TRANSCRIPT_NOT_FOUND"
    assert provider.windows == []


@pytest.mark.integration
def test_a_cancelled_job_stops_before_any_provider_work(engine: Engine) -> None:
    """A workspace that cancels must not keep paying for analysis it no longer wants."""
    context, _ = _seed_analysis(engine, suffix="analysis-cancel")
    with engine.begin() as connection:
        connection.execute(
            Job.__table__.update()
            .where(Job.__table__.c.id == context.job_id)
            .values(cancel_requested_at=datetime.now(tz=UTC))
        )
    provider = FakeHighlightProvider(
        rerank_result=RerankResult(order=(), call=_deterministic_call())
    )
    runner = AnalyzeStageRunner(
        dependencies_factory=lambda _: AnalysisDependencies(provider_factory=lambda _t: provider)
    )

    with pytest.raises(JobCancelledError):
        runner(context)

    assert provider.windows == []


@pytest.mark.integration
def test_an_unreadable_transcript_word_fails_terminally(engine: Engine) -> None:
    """Analysis must refuse evidence it cannot key a timestamp to, not guess around it."""
    context, _ = _seed_analysis(engine, suffix="analysis-corrupt")
    with engine.begin() as connection:
        connection.execute(
            Transcript.__table__.update()
            .where(Transcript.__table__.c.workspace_id == context.workspace_id)
            .values(words=[{"word_id": "w000001"}])
        )
    runner = AnalyzeStageRunner(
        dependencies_factory=lambda _: AnalysisDependencies(
            provider_factory=lambda transcript: DeterministicHighlightProvider(
                transcript=transcript
            )
        )
    )

    with pytest.raises(TerminalJobError) as raised:
        runner(context)

    assert str(raised.value) == "ANALYSIS_INTEGRITY"


@pytest.mark.integration
def test_a_transcript_without_words_fails_terminally(engine: Engine) -> None:
    """An empty word list can never support a candidate, however the row was written."""
    context, _ = _seed_analysis(engine, suffix="analysis-wordless")
    with engine.begin() as connection:
        connection.execute(
            Transcript.__table__.update()
            .where(Transcript.__table__.c.workspace_id == context.workspace_id)
            .values(words=[])
        )
    runner = AnalyzeStageRunner(
        dependencies_factory=lambda _: AnalysisDependencies(
            provider_factory=lambda transcript: DeterministicHighlightProvider(
                transcript=transcript
            )
        )
    )

    with pytest.raises(TerminalJobError) as raised:
        runner(context)

    assert str(raised.value) == "ANALYSIS_INTEGRITY"


@pytest.mark.integration
def test_a_concurrent_analysis_converges_instead_of_failing(engine: Engine) -> None:
    """Two workers racing on the same project must end with one candidate set, not an error."""
    context, _ = _seed_analysis(engine, suffix="analysis-race")
    dependencies = AnalysisDependencies(
        provider_factory=lambda transcript: DeterministicHighlightProvider(transcript=transcript)
    )
    AnalyzeStageRunner(dependencies_factory=lambda _: dependencies)(context)
    racing = _BlindRunner(dependencies_factory=lambda _: dependencies)

    racing(context)

    with Session(engine) as session:
        ranks = sorted(
            candidate.rank
            for candidate in session.scalars(
                select(ClipCandidate).where(ClipCandidate.workspace_id == context.workspace_id)
            )
        )
    assert ranks == sorted(set(ranks))


@pytest.mark.integration
def test_a_conflicting_candidate_set_fails_terminally(engine: Engine) -> None:
    """A conflict that convergence cannot explain must surface, not be written twice."""
    context, _ = _seed_analysis(engine, suffix="analysis-conflict")
    dependencies = AnalysisDependencies(
        provider_factory=lambda transcript: DeterministicHighlightProvider(transcript=transcript)
    )
    AnalyzeStageRunner(dependencies_factory=lambda _: dependencies)(context)
    blind = _AlwaysEmptyRunner(dependencies_factory=lambda _: dependencies)

    with pytest.raises(TerminalJobError) as raised:
        blind(context)

    assert str(raised.value) == "ANALYSIS_INTEGRITY"


class _AlwaysEmptyRunner(AnalyzeStageRunner):
    """Represent a worker whose view of the project never catches up."""

    def _already_complete(self, context: JobContext) -> bool:
        """Insist the project holds no candidates, however often it is asked."""
        del context
        return False


class _BlindRunner(AnalyzeStageRunner):
    """Represent the worker that read the project before the winning worker committed."""

    def __init__(self, **kwargs: Any) -> None:
        """Start with the stale read the losing worker began its attempt from."""
        super().__init__(**kwargs)
        self._read_before_the_race = False

    def _already_complete(self, context: JobContext) -> bool:
        """Report the project as empty once, then read what the winner actually stored."""
        if not self._read_before_the_race:
            self._read_before_the_race = True
            return False
        return super()._already_complete(context)


class _ReplayingProvider:
    """Replay queued outcomes for the first windows, then defer to the offline provider."""

    def __init__(
        self,
        deterministic: DeterministicHighlightProvider,
        *,
        results: list[ExtractionResult | Exception],
    ) -> None:
        """Bind the queued outcomes and the provider covering every later window."""
        self._deterministic = deterministic
        self._results = results

    def extract(self, *, window: Any, target_count: int) -> ExtractionResult:
        """Replay one queued outcome, or extract offline once the queue is empty."""
        if self._results:
            outcome = self._results.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return self._deterministic.extract(window=window, target_count=target_count)

    def rerank(self, *, candidates: Any, limit: int) -> RerankResult:
        """Leave ordering to the local weights."""
        return self._deterministic.rerank(candidates=candidates, limit=limit)


def _replaying_provider(
    deterministic: DeterministicHighlightProvider,
    *,
    transcript: TranscriptResult,
    results: list[ExtractionResult | Exception],
) -> HighlightProvider:
    """Build one provider that fails the given windows and answers the rest offline."""
    del transcript
    return _ReplayingProvider(deterministic, results=results)


def _deterministic_call() -> Any:
    """Reuse the offline provider's own call record for fake results."""
    return (
        DeterministicHighlightProvider(
            transcript=TranscriptResult(
                provider=DETERMINISTIC_PROVIDER,
                provider_version="1",
                model=DETERMINISTIC_PROVIDER,
                language="en",
                full_text="",
                words=(),
                speaker_segments=(),
                utterances=(),
                duration_ms=1,
                raw_result={},
            )
        )
        .rerank(candidates=[], limit=10)
        .call
    )


def _words() -> list[dict[str, Any]]:
    """Build a transcript long enough to window into several minutes of speech."""
    return [
        {
            "word_id": f"w{index + 1:06d}",
            "text": f"word{index}",
            "punctuation": "." if index % 40 == 39 else "",
            "start_ms": index * 500,
            "end_ms": index * 500 + 500,
            "confidence": 0.9,
            "speaker": "A",
        }
        for index in range(DURATION_SECONDS * WORDS_PER_SECOND)
    ]


def _seed_analysis(
    engine: Engine, *, suffix: str, include_transcript: bool = True
) -> tuple[JobContext, UUID]:
    """Create one running ANALYZE Job over a persisted canonical Transcript."""
    user_id, workspace_id = provision_identity(engine, suffix=suffix)
    project_id = uuid4()
    source_id = uuid4()
    transcript_id = uuid4()
    job_id = uuid4()
    now = datetime.now(tz=UTC)
    words = _words()
    with engine.begin() as connection:
        connection.execute(
            Project.__table__.insert().values(
                id=project_id,
                workspace_id=workspace_id,
                created_by_user_id=user_id,
                name=f"Analysis {suffix}",
                status=ProjectStatus.ANALYZING,
                source_kind=SourceKind.UPLOAD,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            Asset.__table__.insert().values(
                id=source_id,
                workspace_id=workspace_id,
                project_id=project_id,
                kind=AssetKind.SOURCE,
                source_type=AssetSourceType.USER_UPLOAD,
                storage_key=f"workspaces/{workspace_id}/projects/{project_id}/source/original",
                content_type="video/mp4",
                size_bytes=100,
                duration_ms=DURATION_SECONDS * 1_000,
                sha256=b"s" * 32,
            )
        )
        if include_transcript:
            connection.execute(
                Transcript.__table__.insert().values(
                    id=transcript_id,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    asset_id=source_id,
                    provider="assemblyai",
                    provider_version="1.0.0",
                    model="universal-3-pro",
                    language="en",
                    full_text=" ".join(word["text"] for word in words),
                    words=words,
                    speaker_segments=[
                        {
                            "segment_id": "s000001",
                            "speaker": "A",
                            "start_ms": 0,
                            "end_ms": words[-1]["end_ms"],
                            "word_ids": [word["word_id"] for word in words],
                        }
                    ],
                    utterances=[
                        {
                            "utterance_id": "u000001",
                            "text": " ".join(word["text"] for word in words),
                            "start_ms": 0,
                            "end_ms": words[-1]["end_ms"],
                            "speaker": "A",
                            "word_ids": [word["word_id"] for word in words],
                        }
                    ],
                    duration_ms=DURATION_SECONDS * 1_000,
                    raw_result_storage_key=(
                        f"workspaces/{workspace_id}/projects/{project_id}/"
                        f"transcripts/{source_id}/assemblyai.json"
                    ),
                )
            )
        connection.execute(
            Job.__table__.insert().values(
                id=job_id,
                workspace_id=workspace_id,
                project_id=project_id,
                kind=JobKind.ANALYZE,
                status=JobStatus.RUNNING,
                stage="queued",
                progress=0,
                attempt=1,
                idempotency_key=f"analyze-{suffix}",
            )
        )
    return (
        JobContext(
            job_id=job_id,
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
            attempt=1,
            settings=runtime_settings(RuntimeRole.WORKER),
        ),
        transcript_id,
    )
