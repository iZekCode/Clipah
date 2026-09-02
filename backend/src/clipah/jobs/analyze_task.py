"""Durable ANALYZE runner turning one canonical Transcript into ranked clip candidates."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clipah.config import Settings
from clipah.db import RuntimeRole, session_scope
from clipah.highlights.analyzer import (
    DEFAULT_ANALYSIS_POLICY,
    AnalysisFailedError,
    AnalysisPolicy,
    AnalysisResult,
    HighlightAnalyzer,
)
from clipah.highlights.deduplicate import DeduplicationPolicy
from clipah.highlights.models import CandidatePolicy, WindowingPolicy
from clipah.highlights.provider import (
    DeterministicHighlightProvider,
    HighlightProvider,
    ProviderCall,
)
from clipah.highlights.provider_router import highlight_provider_router
from clipah.highlights.rerank import RankedCandidate, RankingPolicy
from clipah.jobs.models import JobCancelledError, JobContext, RetryableJobError, TerminalJobError
from clipah.jobs.use_cases import update_job_progress
from clipah.models import ClipCandidate, Project, ProjectStatus, ProviderUsage, Transcript
from clipah.transcripts.models import TranscriptResult, TranscriptWord

ANALYZE_STAGE = "analyze"
WINDOW_FAILED_STAGE = "analyze_window_failed"
ANALYZE_PROGRESS = 0.5
TRANSCRIPT_NOT_FOUND_CODE = "ANALYSIS_TRANSCRIPT_NOT_FOUND"
INTEGRITY_CODE = "ANALYSIS_INTEGRITY"

ProviderFactory = Callable[[TranscriptResult], HighlightProvider]


@dataclass(frozen=True, slots=True)
class AnalysisDependencies:
    """External capabilities the analysis needs once its transcript is loaded."""

    provider_factory: ProviderFactory


DependenciesFactory = Callable[[Settings], AnalysisDependencies]
PolicyFactory = Callable[[Settings], AnalysisPolicy]


class AnalysisIntegrityError(Exception):
    """Refuse persisted transcript evidence the analysis cannot trust."""


@dataclass(frozen=True, slots=True)
class _TranscriptSnapshot:
    """The transcript identity and value safe to carry outside a transaction."""

    transcript_id: UUID
    result: TranscriptResult


class AnalyzeStageRunner:
    """Analyze one transcript exactly once and converge on every later redelivery."""

    def __init__(
        self,
        *,
        dependencies_factory: DependenciesFactory,
        policy: AnalysisPolicy | None = None,
        policy_factory: PolicyFactory | None = None,
    ) -> None:
        """Bind production or deterministic capabilities and the analysis policy."""
        self._dependencies_factory = dependencies_factory
        self._policy = policy
        self._policy_factory = policy_factory

    def __call__(self, context: JobContext) -> None:
        """Run one analysis attempt through stable retryable and terminal codes."""
        try:
            context.raise_if_cancelled()
            snapshot = self._load_transcript(context)
            if self._already_complete(context):
                return
            dependencies = self._dependencies_factory(context.settings)
            policy = self._policy or (
                self._policy_factory(context.settings)
                if self._policy_factory is not None
                else DEFAULT_ANALYSIS_POLICY
            )
            analyzer = HighlightAnalyzer(
                provider=dependencies.provider_factory(snapshot.result),
                policy=policy,
                on_window_failure=lambda index, code: self._report_window_failure(
                    context, index=index, code=code
                ),
            )
            context.raise_if_cancelled()
            result = analyzer.analyze(transcript=snapshot.result)
            context.raise_if_cancelled()
            try:
                self._persist(context, snapshot=snapshot, result=result, policy=policy)
            except IntegrityError:
                if not self._already_complete(context):
                    raise AnalysisIntegrityError("concurrent candidate conflict") from None
        except JobCancelledError:
            raise
        except AnalysisFailedError as error:
            if error.retryable:
                raise RetryableJobError(error.code) from None
            raise TerminalJobError(error.code) from None
        except AnalysisIntegrityError:
            raise TerminalJobError(INTEGRITY_CODE) from None

    def _load_transcript(self, context: JobContext) -> _TranscriptSnapshot:
        """Read the sole canonical Transcript this Job's project owns."""
        with _transaction(context) as session:
            rows = session.scalars(
                select(Transcript)
                .where(
                    Transcript.workspace_id == context.workspace_id,
                    Transcript.project_id == context.project_id,
                )
                .order_by(Transcript.created_at, Transcript.id)
                .limit(2)
            ).all()
            if len(rows) != 1:
                raise TerminalJobError(TRANSCRIPT_NOT_FOUND_CODE)
            return _TranscriptSnapshot(
                transcript_id=rows[0].id,
                result=_transcript_result(rows[0]),
            )

    def _already_complete(self, context: JobContext) -> bool:
        """Treat any persisted candidate set as the answer this project already has."""
        with _transaction(context) as session:
            stored = session.scalar(
                select(func.count())
                .select_from(ClipCandidate)
                .where(
                    ClipCandidate.workspace_id == context.workspace_id,
                    ClipCandidate.project_id == context.project_id,
                )
            )
            if not stored:
                return False
            project = session.scalar(
                select(Project).where(
                    Project.workspace_id == context.workspace_id,
                    Project.id == context.project_id,
                )
            )
            if project is None:
                raise AnalysisIntegrityError("analysis Project disappeared")
            if project.status is ProjectStatus.ANALYZING:
                project.status = ProjectStatus.READY
            return True

    def _report_window_failure(self, context: JobContext, *, index: int, code: str) -> None:
        """Record one failed window durably so the remaining windows can continue."""
        with _transaction(context) as session:
            update_job_progress(
                session,
                workspace_id=context.workspace_id,
                job_id=context.job_id,
                stage=WINDOW_FAILED_STAGE,
                progress=ANALYZE_PROGRESS,
                now=datetime.now(tz=UTC),
                detail={"window_index": index, "error_code": code},
            )

    def _persist(
        self,
        context: JobContext,
        *,
        snapshot: _TranscriptSnapshot,
        result: AnalysisResult,
        policy: AnalysisPolicy,
    ) -> None:
        """Insert every ranked candidate and provider call in one atomic transaction."""
        metadata = _analysis_metadata(result.calls)
        with _transaction(context) as session:
            for ranked in result.ranked:
                session.add(
                    _candidate_row(
                        context,
                        transcript_id=snapshot.transcript_id,
                        ranked=ranked,
                        metadata=metadata,
                        exposed=ranked.rank <= policy.ranking.expose,
                    )
                )
            for call in result.calls:
                session.add(_usage_row(context, call))
            project = session.scalar(
                select(Project).where(
                    Project.workspace_id == context.workspace_id,
                    Project.id == context.project_id,
                    Project.status == ProjectStatus.ANALYZING,
                )
            )
            if project is None:
                raise AnalysisIntegrityError("analysis Project is not running")
            project.status = ProjectStatus.READY
            session.flush()


def _candidate_row(
    context: JobContext,
    *,
    transcript_id: UUID,
    ranked: RankedCandidate,
    metadata: dict[str, Any],
    exposed: bool,
) -> ClipCandidate:
    """Turn one ranked draft into the durable row reviewers will read."""
    draft = ranked.draft
    return ClipCandidate(
        workspace_id=context.workspace_id,
        project_id=context.project_id,
        transcript_id=transcript_id,
        rank=ranked.rank,
        score=ranked.score,
        hook=draft.hook,
        payoff=draft.payoff,
        reason=draft.reason,
        category=draft.category.value,
        tags=list(draft.tags),
        start_ms=draft.start_ms,
        end_ms=draft.end_ms,
        start_word_id=draft.start_word_id,
        end_word_id=draft.end_word_id,
        transcript_excerpt=draft.transcript_excerpt,
        context_dependencies=list(draft.context_dependencies),
        score_breakdown=draft.score_breakdown.model_dump(),
        context_warnings=list(draft.context_warnings),
        visual_opportunities=list(draft.visual_opportunities),
        model_metadata={**metadata, "exposed": exposed},
    )


def _usage_row(context: JobContext, call: ProviderCall) -> ProviderUsage:
    """Record exactly what one provider call cost this workspace."""
    return ProviderUsage(
        workspace_id=context.workspace_id,
        provider=call.provider,
        operation=call.operation,
        model_or_api_version=call.model,
        request_id=call.request_id,
        input_units=call.input_units,
        output_units=call.output_units,
        job_id=context.job_id,
    )


def _analysis_metadata(calls: tuple[ProviderCall, ...]) -> dict[str, Any]:
    """Describe the models and prompts every candidate of this analysis came from."""
    return {
        "provider": calls[0].provider if calls else "",
        "model": calls[0].model if calls else "",
        "prompt_version": calls[0].prompt_version if calls else "",
        "schema_version": calls[0].schema_version if calls else "",
        "request_ids": [call.request_id for call in calls],
        "latency_ms": sum(call.latency_ms for call in calls),
    }


def _transcript_result(row: Transcript) -> TranscriptResult:
    """Rebuild the transcript value analysis reads, which is its words and nothing else.

    Speaker segments and utterances stay in the row: candidates are keyed to word IDs, so
    carrying a second copy of the same evidence would only invite the two to disagree.
    """
    if not isinstance(row.words, list) or not row.words:
        raise AnalysisIntegrityError("persisted Transcript carries no words")
    return TranscriptResult(
        provider=row.provider,
        provider_version=row.provider_version,
        model=row.model,
        language=row.language,
        full_text=row.full_text,
        words=tuple(_word(word) for word in row.words),
        speaker_segments=(),
        utterances=(),
        duration_ms=row.duration_ms,
        raw_result={},
    )


def _word(word: Any) -> TranscriptWord:
    """Read one persisted word, refusing any row the analysis cannot key candidates to."""
    try:
        return TranscriptWord(
            word_id=str(word["word_id"]),
            text=str(word["text"]),
            punctuation=str(word["punctuation"]),
            start_ms=int(word["start_ms"]),
            end_ms=int(word["end_ms"]),
            confidence=float(word["confidence"]),
            speaker=str(word["speaker"]),
        )
    except (KeyError, TypeError, ValueError):
        raise AnalysisIntegrityError("persisted Transcript word is unreadable") from None


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


def production_analysis_dependencies(settings: Settings) -> AnalysisDependencies:
    """Compose the configured Groq provider with its deterministic offline fallback."""

    def factory(transcript: TranscriptResult) -> HighlightProvider:
        return highlight_provider_router(
            settings,
            today=datetime.now(tz=UTC).date(),
            fallback=DeterministicHighlightProvider(transcript=transcript),
        )

    return AnalysisDependencies(provider_factory=factory)


def production_analysis_policy(settings: Settings) -> AnalysisPolicy:
    """Build every analysis tuning policy from validated deployment settings."""
    return AnalysisPolicy(
        windowing=WindowingPolicy(
            target_min_ms=settings.analysis_window_target_min_ms,
            target_max_ms=settings.analysis_window_target_max_ms,
            overlap_ms=settings.analysis_window_overlap_ms,
            silence_gap_ms=settings.analysis_window_silence_gap_ms,
            min_words=settings.analysis_window_min_words,
        ),
        candidate=CandidatePolicy(
            min_duration_ms=settings.analysis_candidate_min_duration_ms,
            max_duration_ms=settings.analysis_candidate_max_duration_ms,
        ),
        deduplication=DeduplicationPolicy(
            min_temporal_iou=settings.analysis_deduplication_temporal_iou,
            min_excerpt_cosine=settings.analysis_deduplication_excerpt_cosine,
        ),
        ranking=RankingPolicy(
            keep=settings.analysis_candidates_kept,
            expose=settings.analysis_candidates_exposed,
        ),
    )


analyze_stage_runner = AnalyzeStageRunner(
    dependencies_factory=production_analysis_dependencies,
    policy_factory=production_analysis_policy,
)
