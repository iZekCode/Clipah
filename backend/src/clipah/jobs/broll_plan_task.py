"""Durable BROLL_PLAN runner turning one Clip Candidate into proposed B-roll placements.

Planning proposes and never edits. This runner creates no asset, touches no composition,
and moves no Project between states: everything it writes is a `proposed` suggestion a
member is free to ignore. A redelivered Job converges on the suggestions already stored,
because a plan is identified by its candidate, planner version, and coverage.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clipah.broll.models import (
    DEFAULT_PLACEMENT_POLICY,
    PLANNER_VERSION,
    BrollCoverage,
    BrollSuggestionStatus,
    CandidateSpan,
    PlacedSuggestion,
    PlacementPolicy,
)
from clipah.broll.placement import place_suggestions, scene_boundaries
from clipah.broll.planner import (
    BrollBeatProvider,
    BrollPlanner,
    BrollProviderRetryableError,
    BrollProviderTerminalError,
    GroqBrollPlanner,
)
from clipah.broll.repository import BrollRepository
from clipah.config import Settings
from clipah.db import RuntimeRole, session_scope
from clipah.highlights.provider import ProviderCall
from clipah.jobs.models import JobCancelledError, JobContext, RetryableJobError, TerminalJobError
from clipah.jobs.use_cases import update_job_progress
from clipah.models import BrollSuggestion, Transcript
from clipah.observability.metrics import count
from clipah.observability.usage import ProviderCallRecord, record_provider_usage
from clipah.transcripts.models import TranscriptResult, TranscriptWord

BROLL_PLAN_STAGE = "broll_plan"
BEAT_REJECTED_STAGE = "broll_plan_beat_rejected"
BROLL_PLAN_PROGRESS = 0.5
REQUEST_NOT_FOUND_CODE = "BROLL_PLAN_REQUEST_NOT_FOUND"
INTEGRITY_CODE = "BROLL_PLAN_INTEGRITY"

ProviderFactory = Callable[[Settings], BrollBeatProvider]
PolicyFactory = Callable[[Settings], PlacementPolicy]


class BrollPlanIntegrityError(Exception):
    """Refuse persisted evidence the planning stage cannot trust."""


@dataclass(frozen=True, slots=True)
class _PlanSnapshot:
    """Everything one planning attempt needs, carried outside any transaction."""

    candidate_id: UUID
    project_id: UUID
    coverage: BrollCoverage
    span: CandidateSpan
    transcript: TranscriptResult


class BrollPlanStageRunner:
    """Plan one clip exactly once and converge on every later redelivery."""

    def __init__(
        self,
        *,
        provider_factory: ProviderFactory,
        policy: PlacementPolicy | None = None,
        policy_factory: PolicyFactory | None = None,
    ) -> None:
        """Bind production or deterministic capabilities and the placement policy."""
        self._provider_factory = provider_factory
        self._policy = policy
        self._policy_factory = policy_factory

    def __call__(self, context: JobContext) -> None:
        """Run one planning attempt through stable retryable and terminal codes."""
        try:
            context.raise_if_cancelled()
            snapshot = self._load(context)
            policy = self._placement_policy(context.settings)
            if self._already_planned(context, snapshot):
                return

            planner = BrollPlanner(
                provider=self._provider_factory(context.settings),
                on_beat_rejected=lambda code: self._report_beat_rejected(context, code=code),
            )
            boundaries = scene_boundaries(
                _clip_words(snapshot.transcript.words, snapshot.span),
                silence_gap_ms=policy.silence_gap_ms,
            )
            context.raise_if_cancelled()
            plan = planner.plan(
                transcript=snapshot.transcript,
                candidate=snapshot.span,
                coverage=snapshot.coverage,
                boundaries=boundaries,
            )
            placed = place_suggestions(
                beats=plan.beats,
                candidate=snapshot.span,
                coverage=snapshot.coverage,
                boundaries=boundaries,
                policy=policy,
            )
            context.raise_if_cancelled()
            try:
                self._persist(context, snapshot=snapshot, placed=placed, calls=plan.calls)
            except IntegrityError:
                if not self._already_planned(context, snapshot):
                    raise BrollPlanIntegrityError("concurrent suggestion conflict") from None
        except JobCancelledError:
            raise
        except BrollProviderRetryableError as error:
            raise RetryableJobError(error.code) from None
        except BrollProviderTerminalError as error:
            raise TerminalJobError(error.code) from None
        except BrollPlanIntegrityError:
            raise TerminalJobError(INTEGRITY_CODE) from None

    def _placement_policy(self, settings: Settings) -> PlacementPolicy:
        """Prefer an injected policy, then a configured one, then the shipped default."""
        if self._policy is not None:
            return self._policy
        if self._policy_factory is not None:
            return self._policy_factory(settings)
        return DEFAULT_PLACEMENT_POLICY

    def _load(self, context: JobContext) -> _PlanSnapshot:
        """Read the clip this Job was admitted for and the transcript it is keyed to."""
        with _transaction(context) as session:
            found = BrollRepository(session).plan_target_for_job(
                workspace_id=context.workspace_id, job_id=context.job_id
            )
            if found is None:
                raise TerminalJobError(REQUEST_NOT_FOUND_CODE)
            request, project_id, span, transcript = found
            return _PlanSnapshot(
                candidate_id=request.candidate_id,
                project_id=project_id,
                coverage=request.coverage,
                span=span,
                transcript=_transcript_result(transcript),
            )

    def _already_planned(self, context: JobContext, snapshot: _PlanSnapshot) -> bool:
        """Treat this plan's stored suggestions as the answer the clip already has."""
        with _transaction(context) as session:
            return BrollRepository(session).plan_is_stored(
                workspace_id=context.workspace_id,
                candidate_id=snapshot.candidate_id,
                planner_version=PLANNER_VERSION,
                coverage=snapshot.coverage,
            )

    def _report_beat_rejected(self, context: JobContext, *, code: str) -> None:
        """Record one refused beat durably so the remaining beats can still be placed."""
        with _transaction(context) as session:
            update_job_progress(
                session,
                workspace_id=context.workspace_id,
                job_id=context.job_id,
                stage=BEAT_REJECTED_STAGE,
                progress=BROLL_PLAN_PROGRESS,
                now=datetime.now(tz=UTC),
                detail={"error_code": code},
            )

    def _persist(
        self,
        context: JobContext,
        *,
        snapshot: _PlanSnapshot,
        placed: tuple[PlacedSuggestion, ...],
        calls: tuple[ProviderCall, ...],
    ) -> None:
        """Insert every proposal and provider call in one atomic transaction.

        A clip that earned no suggestion still records its provider calls, so a member who
        asks why nothing was proposed can be told the planning actually ran.
        """
        metadata = _plan_metadata(calls)
        with _transaction(context) as session:
            for suggestion in placed:
                session.add(
                    _suggestion_row(
                        context, snapshot=snapshot, placed=suggestion, metadata=metadata
                    )
                )
            for call in calls:
                record_provider_usage(
                    session,
                    workspace_id=context.workspace_id,
                    job_id=context.job_id,
                    call=ProviderCallRecord(
                        provider=call.provider,
                        operation=call.operation,
                        model_or_api_version=call.model,
                        request_id=call.request_id,
                        input_units=call.input_units,
                        output_units=call.output_units,
                    ),
                )
            for suggestion in placed:
                count(
                    "clipah.broll.decision",
                    decision=str(suggestion.placement_reason),
                    provider=calls[0].provider if calls else "none",
                )
            session.flush()


def _suggestion_row(
    context: JobContext,
    *,
    snapshot: _PlanSnapshot,
    placed: PlacedSuggestion,
    metadata: dict[str, Any],
) -> BrollSuggestion:
    """Turn one placed beat into the durable proposal a reviewer will read."""
    intent = placed.beat.intent
    return BrollSuggestion(
        workspace_id=context.workspace_id,
        project_id=snapshot.project_id,
        candidate_id=snapshot.candidate_id,
        planner_version=PLANNER_VERSION,
        coverage=snapshot.coverage,
        beat_start_word_id=placed.beat.start_word_id,
        beat_end_word_id=placed.beat.end_word_id,
        start_ms=placed.start_ms,
        end_ms=placed.end_ms,
        visual_intent={
            "subject": intent.subject,
            "action": intent.action,
            "setting": intent.setting,
            "mood": intent.mood,
            "portrait_suitable": intent.portrait_suitable,
            "factual_risk_flags": list(intent.factual_risk_flags),
            "confidence": intent.confidence,
        },
        search_terms={
            "id": list(intent.search_terms_id),
            "en": list(intent.search_terms_en),
        },
        exclusions=list(intent.exclusions),
        status=BrollSuggestionStatus.PROPOSED,
        placement_reason=placed.placement_reason,
        provider_metadata=metadata,
    )


def _plan_metadata(calls: tuple[ProviderCall, ...]) -> dict[str, Any]:
    """Describe the model and prompt every suggestion of this plan came from."""
    return {
        "provider": calls[0].provider if calls else "",
        "model": calls[0].model if calls else "",
        "prompt_version": calls[0].prompt_version if calls else "",
        "schema_version": calls[0].schema_version if calls else "",
        "request_ids": [call.request_id for call in calls],
        "latency_ms": sum(call.latency_ms for call in calls),
    }


def _clip_words(
    words: tuple[TranscriptWord, ...], span: CandidateSpan
) -> tuple[TranscriptWord, ...]:
    """Keep the words of one clip, which are the only ones a scene change can lie between."""
    return tuple(
        word for word in words if word.start_ms >= span.start_ms and word.end_ms <= span.end_ms
    )


def _transcript_result(row: Transcript) -> TranscriptResult:
    """Rebuild the transcript value planning reads, which is its words and nothing else."""
    if not isinstance(row.words, list) or not row.words:
        raise BrollPlanIntegrityError("persisted Transcript carries no words")
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
    """Read one persisted word, refusing any row a beat could not be keyed to."""
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
        raise BrollPlanIntegrityError("persisted Transcript word is unreadable") from None


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


def production_broll_provider(settings: Settings) -> BrollBeatProvider:
    """Compose the configured Groq planner, which has no offline substitute by design."""
    return GroqBrollPlanner(
        model=settings.groq_reranking_model,
        api_key=(
            settings.groq_api_key.get_secret_value() if settings.groq_api_key is not None else None
        ),
    )


def production_placement_policy(settings: Settings) -> PlacementPolicy:
    """Build the placement policy from validated deployment settings."""
    return PlacementPolicy(
        min_shot_ms=settings.broll_min_shot_ms,
        max_shot_ms=settings.broll_max_shot_ms,
        hook_guard_ms=settings.broll_hook_guard_ms,
        min_confidence=settings.broll_min_confidence,
        silence_gap_ms=settings.analysis_window_silence_gap_ms,
    )


broll_plan_stage_runner = BrollPlanStageRunner(
    provider_factory=production_broll_provider,
    policy_factory=production_placement_policy,
)
