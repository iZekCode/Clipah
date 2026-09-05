"""Integration contracts for durable, retry-safe B-roll planning.

Planning is the one AI stage that must be safe to run twice: a member can ask for balanced
coverage, look at the result, and ask again. These tests hold the two promises that makes
necessary — a replay writes no second row, and nothing planning does touches an Edit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clipah.broll.models import (
    PLANNER_VERSION,
    BrollCoverage,
    BrollSuggestionStatus,
    PlacementPolicy,
)
from clipah.broll.planner import (
    UNKNOWN_WORD_CODE,
    BrollProviderRetryableError,
    BrollProviderTerminalError,
    FakeBrollProvider,
    GroqBrollPlanner,
    ProposalResult,
)
from clipah.db import RuntimeRole
from clipah.highlights.provider import ProviderCall
from clipah.jobs.broll_plan_task import (
    INTEGRITY_CODE,
    REQUEST_NOT_FOUND_CODE,
    BrollPlanStageRunner,
    broll_plan_stage_runner,
    production_broll_provider,
    production_placement_policy,
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
    AssetSourceType,
    BrollPlanRequest,
    BrollSuggestion,
    ClipCandidate,
    ClipEdit,
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
from support import provision_identity, runtime_settings

WORD_MS = 500
WORD_COUNT = 240
CLIP_START_MS = 0
CLIP_END_MS = 60_000


@pytest.mark.integration
def test_the_planning_runner_is_registered_for_its_job_kind() -> None:
    """Queued BROLL_PLAN Jobs must not fall through to the unsupported-kind failure."""
    assert JobKind.BROLL_PLAN in stage_runners()


@pytest.mark.integration
def test_a_replayed_plan_persists_the_same_suggestions_without_duplicate_rows(
    engine: Engine,
) -> None:
    """A member who asks twice must get one plan, and the provider must be paid once."""
    seed = _seed(engine, suffix="broll-replay")
    provider = _provider([_result(_beats())])
    runner = BrollPlanStageRunner(provider_factory=lambda _: provider)

    runner(seed.context)
    first = _stored(engine, seed)
    runner(seed.context)
    second = _stored(engine, seed)

    assert [(row.beat_start_word_id, row.start_ms, row.end_ms) for row in first] == [
        (row.beat_start_word_id, row.start_ms, row.end_ms) for row in second
    ]
    assert [row.id for row in first] == [row.id for row in second]
    assert provider.calls == 1


@pytest.mark.integration
def test_a_stored_suggestion_carries_the_evidence_a_retriever_and_reviewer_need(
    engine: Engine,
) -> None:
    """Search intent, exclusions, and the placement reason must survive to the database."""
    seed = _seed(engine, suffix="broll-evidence")
    runner = BrollPlanStageRunner(provider_factory=lambda _: _provider([_result(_beats())]))

    runner(seed.context)

    stored = _stored(engine, seed)
    assert stored
    row = stored[0]
    assert row.status is BrollSuggestionStatus.PROPOSED
    assert row.planner_version == PLANNER_VERSION
    assert row.coverage is BrollCoverage.BALANCED
    assert row.search_terms == {
        "id": ["formulir pendaftaran"],
        "en": ["signup form"],
    }
    assert row.exclusions == ["stock office handshake"]
    assert row.visual_intent["subject"] == "a shortened signup form 0"
    assert row.placement_reason
    assert row.provider_metadata["provider"] == "fake"
    # Retrieval belongs to a later task, so nothing here has chosen a source yet.
    assert row.source_type is None
    assert row.asset_id is None
    assert row.decided_at is None


@pytest.mark.integration
def test_planning_creates_no_edit_and_leaves_the_project_ready(engine: Engine) -> None:
    """A proposal changes nothing until a member accepts it, which is a later task."""
    seed = _seed(engine, suffix="broll-no-edit")
    runner = BrollPlanStageRunner(provider_factory=lambda _: _provider([_result(_beats())]))

    runner(seed.context)

    with Session(engine) as session:
        edits = session.scalar(
            select(func.count())
            .select_from(ClipEdit)
            .where(ClipEdit.workspace_id == seed.workspace_id)
        )
        status = session.scalar(
            select(Project.status).where(
                Project.workspace_id == seed.workspace_id, Project.id == seed.project_id
            )
        )
    assert edits == 0
    assert status is ProjectStatus.READY


@pytest.mark.integration
def test_a_clip_with_no_visualizable_beat_succeeds_with_no_suggestions(
    engine: Engine,
) -> None:
    """ "Nothing to suggest" is an answer, so the Job must not fail to deliver it."""
    seed = _seed(engine, suffix="broll-empty")
    runner = BrollPlanStageRunner(provider_factory=lambda _: _provider([_result(())]))

    runner(seed.context)

    assert _stored(engine, seed) == []
    with Session(engine) as session:
        usage = session.scalar(
            select(func.count())
            .select_from(ProviderUsage)
            .where(
                ProviderUsage.workspace_id == seed.workspace_id,
                ProviderUsage.job_id == seed.context.job_id,
            )
        )
    # The call is still recorded, so a member can be told planning actually ran.
    assert usage == 1


@pytest.mark.integration
def test_a_protected_beat_reaches_the_database_as_no_suggestion_at_all(
    engine: Engine,
) -> None:
    """A punchline the model flagged must never become a durable cutaway."""
    seed = _seed(engine, suffix="broll-protected")
    beats = [_beat(0, protection="punchline"), _beat(1)]
    runner = BrollPlanStageRunner(provider_factory=lambda _: _provider([_result(tuple(beats))]))

    runner(seed.context)

    stored = _stored(engine, seed)
    assert [row.beat_start_word_id for row in stored] == [_word_id(_beat_start(1))]


@pytest.mark.integration
def test_one_malformed_beat_is_reported_and_costs_the_member_no_other_beat(
    engine: Engine,
) -> None:
    """A single bad proposal must not throw away the beats that were fine."""
    seed = _seed(engine, suffix="broll-partial")
    beats = (_beat(0), {**_beat(1), "start_word_id": "w999999"}, _beat(2))
    runner = BrollPlanStageRunner(provider_factory=lambda _: _provider([_result(beats)]))

    runner(seed.context)

    assert len(_stored(engine, seed)) == 2
    with Session(engine) as session:
        details = list(
            session.scalars(
                select(JobEvent.payload).where(
                    JobEvent.workspace_id == seed.workspace_id,
                    JobEvent.job_id == seed.context.job_id,
                )
            )
        )
    codes = [
        payload["error_code"]
        for payload in details
        if isinstance(payload, dict) and "error_code" in payload
    ]
    assert codes == [UNKNOWN_WORD_CODE]


@pytest.mark.integration
def test_a_provider_outage_is_retryable_rather_than_an_empty_plan(engine: Engine) -> None:
    """Telling a member their clip has no visual opportunities must require evidence."""
    seed = _seed(engine, suffix="broll-outage")
    runner = BrollPlanStageRunner(
        provider_factory=lambda _: _provider(
            [BrollProviderRetryableError("BROLL_PROVIDER_UNAVAILABLE")]
        )
    )

    with pytest.raises(RetryableJobError):
        runner(seed.context)

    assert _stored(engine, seed) == []


@pytest.mark.integration
def test_a_provider_refusal_is_terminal(engine: Engine) -> None:
    """Retrying a request the provider rejected would spend the budget on one answer."""
    seed = _seed(engine, suffix="broll-refusal")
    runner = BrollPlanStageRunner(
        provider_factory=lambda _: _provider(
            [BrollProviderTerminalError("BROLL_PROVIDER_REJECTED")]
        )
    )

    with pytest.raises(TerminalJobError):
        runner(seed.context)


@pytest.mark.integration
def test_a_job_with_no_recorded_target_fails_terminally(engine: Engine) -> None:
    """A planning Job that cannot say which clip it covers has nothing to retry."""
    seed = _seed(engine, suffix="broll-no-request", record_request=False)
    runner = BrollPlanStageRunner(provider_factory=lambda _: _provider([_result(_beats())]))

    with pytest.raises(TerminalJobError) as error:
        runner(seed.context)

    assert str(error.value) == REQUEST_NOT_FOUND_CODE


@pytest.mark.integration
def test_the_schema_refuses_to_point_a_planning_job_at_another_workspaces_clip(
    engine: Engine,
) -> None:
    """Cross-Workspace targeting is impossible to write, not merely refused when read."""
    seed = _seed(engine, suffix="broll-foreign")
    other = _seed(engine, suffix="broll-foreign-other")

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            BrollPlanRequest.__table__.update()
            .where(BrollPlanRequest.job_id == seed.context.job_id)
            .values(candidate_id=other.candidate_id)
        )


@pytest.mark.integration
def test_a_second_coverage_is_a_second_plan_rather_than_a_conflict(engine: Engine) -> None:
    """Asking for a busier cut must not be refused as a repeat of the balanced one."""
    seed = _seed(engine, suffix="broll-coverage")
    runner = BrollPlanStageRunner(provider_factory=lambda _: _provider([_result(_beats())]))
    runner(seed.context)

    dynamic = _second_plan(engine, seed, coverage=BrollCoverage.DYNAMIC)
    runner_two = BrollPlanStageRunner(provider_factory=lambda _: _provider([_result(_beats())]))
    runner_two(dynamic)

    with Session(engine) as session:
        coverages = sorted(
            {
                row
                for row in session.scalars(
                    select(BrollSuggestion.coverage).where(
                        BrollSuggestion.workspace_id == seed.workspace_id,
                        BrollSuggestion.candidate_id == seed.candidate_id,
                    )
                )
            }
        )
    assert coverages == [BrollCoverage.BALANCED, BrollCoverage.DYNAMIC]


class _Seed:
    """One provisioned Workspace, Project, Candidate, and running planning Job."""

    def __init__(
        self,
        *,
        context: JobContext,
        workspace_id: UUID,
        project_id: UUID,
        candidate_id: UUID,
    ) -> None:
        """Retain only what a test needs to read the rows the runner wrote."""
        self.context = context
        self.workspace_id = workspace_id
        self.project_id = project_id
        self.candidate_id = candidate_id


class _CountingProvider:
    """A fake that also reports how many times a provider was actually paid."""

    def __init__(self, outcomes: list[Any]) -> None:
        """Queue one outcome per expected planning call."""
        self._inner = FakeBrollProvider(results=outcomes)
        self.calls = 0

    def propose(self, **kwargs: Any) -> ProposalResult:
        """Count the call before replaying its queued outcome."""
        self.calls += 1
        return self._inner.propose(**kwargs)


def _provider(outcomes: list[Any]) -> _CountingProvider:
    """Build the counting fake this suite uses in place of a real planner."""
    return _CountingProvider(outcomes)


def _result(beats: tuple[dict[str, Any], ...]) -> ProposalResult:
    """Wrap raw beat payloads as one provider result with a recordable call."""
    return ProposalResult(
        proposals=beats,
        call=ProviderCall(
            provider="fake",
            operation="broll_plan",
            model="fake-planner",
            request_id="req-fake-1",
            latency_ms=5,
            input_units=100,
            output_units=20,
            prompt_version="broll/plan/1",
            schema_version="broll-beat/1",
        ),
    )


def _beat_start(index: int) -> int:
    """Space beats far enough apart that balanced density keeps every one of them."""
    return 10_000 + index * 10_000


def _word_id(start_ms: int) -> str:
    """Name the word one millisecond boundary falls on."""
    return f"w{start_ms // WORD_MS + 1:06d}"


def _beat(index: int, *, protection: str | None = None) -> dict[str, Any]:
    """Build one schema-valid beat payload with a subject unique to its index."""
    start = _beat_start(index)
    return {
        "start_word_id": _word_id(start),
        "end_word_id": _word_id(start + 2_000 - WORD_MS),
        "placement_reason": "The sentence names an object the viewer cannot see",
        "protection": protection,
        "intent": {
            "subject": f"a shortened signup form {index}",
            "action": "a hand deleting form fields",
            "setting": "a laptop screen on a desk",
            "mood": "focused",
            "search_terms_id": ["formulir pendaftaran"],
            "search_terms_en": ["signup form"],
            "portrait_suitable": True,
            "exclusions": ["stock office handshake"],
            "factual_risk_flags": [],
            "confidence": 0.8,
        },
    }


def _beats() -> tuple[dict[str, Any], ...]:
    """Offer three well-spaced beats that balanced coverage keeps in full."""
    return (_beat(0), _beat(1), _beat(2))


def _stored(engine: Engine, seed: _Seed) -> list[BrollSuggestion]:
    """Read this candidate's suggestions in the order they occur in the clip."""
    with Session(engine) as session:
        return list(
            session.scalars(
                select(BrollSuggestion)
                .where(
                    BrollSuggestion.workspace_id == seed.workspace_id,
                    BrollSuggestion.candidate_id == seed.candidate_id,
                )
                .order_by(BrollSuggestion.start_ms)
            )
        )


def _words() -> list[dict[str, Any]]:
    """Build a transcript whose first minute is the clip being planned for."""
    return [
        {
            "word_id": f"w{index + 1:06d}",
            "text": f"word{index}",
            "punctuation": "." if index % 40 == 39 else "",
            "start_ms": index * WORD_MS,
            "end_ms": index * WORD_MS + WORD_MS,
            "confidence": 0.9,
            "speaker": "A",
        }
        for index in range(WORD_COUNT)
    ]


def _second_plan(engine: Engine, seed: _Seed, *, coverage: BrollCoverage) -> JobContext:
    """Admit a second planning Job over the same clip at a different coverage."""
    job_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            Job.__table__.insert().values(
                id=job_id,
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                kind=JobKind.BROLL_PLAN,
                status=JobStatus.RUNNING,
                stage="queued",
                progress=0,
                attempt=1,
                idempotency_key=f"broll-plan-{job_id}",
            )
        )
        connection.execute(
            BrollPlanRequest.__table__.insert().values(
                id=uuid4(),
                workspace_id=seed.workspace_id,
                candidate_id=seed.candidate_id,
                job_id=job_id,
                coverage=coverage,
                requested_by_user_id=seed.context.user_id,
            )
        )
    return JobContext(
        job_id=job_id,
        workspace_id=seed.workspace_id,
        project_id=seed.project_id,
        user_id=seed.context.user_id,
        attempt=1,
        settings=runtime_settings(RuntimeRole.WORKER),
    )


def _seed(engine: Engine, *, suffix: str, record_request: bool = True) -> _Seed:
    """Create one running BROLL_PLAN Job over a ready Project's exposed candidate."""
    user_id, workspace_id = provision_identity(engine, suffix=suffix)
    project_id = uuid4()
    source_id = uuid4()
    transcript_id = uuid4()
    candidate_id = uuid4()
    job_id = uuid4()
    now = datetime.now(tz=UTC)
    words = _words()
    with engine.begin() as connection:
        connection.execute(
            Project.__table__.insert().values(
                id=project_id,
                workspace_id=workspace_id,
                created_by_user_id=user_id,
                name=f"Planning {suffix}",
                status=ProjectStatus.READY,
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
                duration_ms=WORD_COUNT * WORD_MS,
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
                language="en",
                full_text=" ".join(word["text"] for word in words),
                words=words,
                speaker_segments=[],
                utterances=[],
                duration_ms=WORD_COUNT * WORD_MS,
                raw_result_storage_key=(
                    f"workspaces/{workspace_id}/projects/{project_id}/"
                    f"transcripts/{source_id}/assemblyai.json"
                ),
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
                hook="The onboarding form was the growth ceiling",
                payoff="Removing it doubled activation",
                reason="A complete problem and result inside one minute",
                category="insight",
                tags=["growth"],
                start_ms=CLIP_START_MS,
                end_ms=CLIP_END_MS,
                start_word_id=_word_id(CLIP_START_MS),
                end_word_id=_word_id(CLIP_END_MS - WORD_MS),
                transcript_excerpt="word0 word1",
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
                kind=JobKind.BROLL_PLAN,
                status=JobStatus.RUNNING,
                stage="queued",
                progress=0,
                attempt=1,
                idempotency_key=f"broll-plan-{suffix}",
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
    )


@pytest.mark.integration
def test_a_cancelled_job_stops_before_it_pays_a_provider(engine: Engine) -> None:
    """Cancelling planning must stop the work, not merely discard its result."""
    seed = _seed(engine, suffix="broll-cancel")
    with engine.begin() as connection:
        connection.execute(
            Job.__table__.update()
            .where(Job.id == seed.context.job_id)
            .values(cancel_requested_at=datetime.now(tz=UTC))
        )
    provider = _provider([_result(_beats())])

    with pytest.raises(JobCancelledError):
        BrollPlanStageRunner(provider_factory=lambda _: provider)(seed.context)

    assert provider.calls == 0
    assert _stored(engine, seed) == []


@pytest.mark.integration
def test_a_plan_won_by_a_racing_worker_converges_instead_of_failing(
    engine: Engine,
) -> None:
    """The loser of a race must adopt the winner's plan, not report a failure for it."""
    seed = _seed(engine, suffix="broll-race")

    class _RacingProvider:
        """Write the plan from another connection while this worker is still planning."""

        def propose(self, **kwargs: Any) -> ProposalResult:
            """Let a second writer win the plan between the check and the insert."""
            del kwargs
            _store_conflicting_suggestion(engine, seed)
            return _result(_beats())

    BrollPlanStageRunner(provider_factory=lambda _: _RacingProvider())(seed.context)

    stored = _stored(engine, seed)
    assert [row.placement_reason for row in stored] == ["Written by the worker that won the race"]


@pytest.mark.integration
def test_the_schema_refuses_to_point_a_clip_at_a_transcript_that_does_not_exist(
    engine: Engine,
) -> None:
    """A clip's authoritative words cannot go missing, so planning need not handle it."""
    seed = _seed(engine, suffix="broll-no-transcript")

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            ClipCandidate.__table__.update()
            .where(ClipCandidate.id == seed.candidate_id)
            .values(transcript_id=uuid4())
        )


@pytest.mark.integration
def test_an_unreadable_persisted_word_is_refused_rather_than_planned_around(
    engine: Engine,
) -> None:
    """A transcript this stage cannot key a beat to must stop the plan, not shorten it."""
    seed = _seed(engine, suffix="broll-bad-words")
    with engine.begin() as connection:
        connection.execute(
            Transcript.__table__.update()
            .where(Transcript.project_id == seed.project_id)
            .values(words=[{"word_id": "w000001"}])
        )
    runner = BrollPlanStageRunner(provider_factory=lambda _: _provider([_result(_beats())]))

    with pytest.raises(TerminalJobError) as error:
        runner(seed.context)

    assert str(error.value) == INTEGRITY_CODE


@pytest.mark.integration
def test_an_injected_policy_overrides_the_configured_one(engine: Engine) -> None:
    """A caller that supplies a policy must not silently get the deployment's instead."""
    seed = _seed(engine, suffix="broll-policy")
    tight = PlacementPolicy(
        min_shot_ms=2_000,
        max_shot_ms=3_000,
        hook_guard_ms=3_000,
        min_confidence=0.5,
        silence_gap_ms=1_200,
    )
    runner = BrollPlanStageRunner(
        provider_factory=lambda _: _provider([_result(_beats())]), policy=tight
    )

    runner(seed.context)

    assert all(row.end_ms - row.start_ms <= 3_000 for row in _stored(engine, seed))


@pytest.mark.integration
def test_the_shipped_default_policy_applies_when_no_policy_is_configured(
    engine: Engine,
) -> None:
    """A runner built with neither policy must still place within the documented band."""
    seed = _seed(engine, suffix="broll-default-policy")
    runner = BrollPlanStageRunner(provider_factory=lambda _: _provider([_result(_beats())]))

    runner(seed.context)

    assert all(2_000 <= row.end_ms - row.start_ms <= 5_000 for row in _stored(engine, seed))


@pytest.mark.integration
def test_the_production_runner_reads_its_policy_and_provider_from_settings() -> None:
    """The shipped runner must be wired to configuration rather than to test defaults."""
    settings = runtime_settings(RuntimeRole.WORKER, groq_api_key="test-key")

    policy = production_placement_policy(settings)
    provider = production_broll_provider(settings)

    assert policy.min_shot_ms == settings.broll_min_shot_ms
    assert policy.max_shot_ms == settings.broll_max_shot_ms
    assert policy.hook_guard_ms == settings.broll_hook_guard_ms
    assert policy.min_confidence == settings.broll_min_confidence
    assert isinstance(provider, GroqBrollPlanner)
    assert broll_plan_stage_runner is stage_runners()[JobKind.BROLL_PLAN]


def _store_conflicting_suggestion(engine: Engine, seed: _Seed) -> None:
    """Write one row of this exact plan, as a second worker committing first would."""
    with engine.begin() as connection:
        connection.execute(
            BrollSuggestion.__table__.insert().values(
                id=uuid4(),
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                candidate_id=seed.candidate_id,
                planner_version=PLANNER_VERSION,
                coverage=BrollCoverage.BALANCED,
                beat_start_word_id=_word_id(_beat_start(0)),
                beat_end_word_id=_word_id(_beat_start(0) + 1_500),
                start_ms=_beat_start(0),
                end_ms=_beat_start(0) + 2_000,
                visual_intent={},
                search_terms={"id": [], "en": []},
                exclusions=[],
                status="proposed",
                placement_reason="Written by the worker that won the race",
                provider_metadata={},
            )
        )


@pytest.mark.integration
def test_a_configured_policy_is_read_from_settings_when_no_policy_is_injected(
    engine: Engine,
) -> None:
    """The shipped runner takes its numbers from the deployment, not from a default."""
    seed = _seed(engine, suffix="broll-configured")
    runner = BrollPlanStageRunner(
        provider_factory=lambda _: _provider([_result(_beats())]),
        policy_factory=production_placement_policy,
    )

    runner(seed.context)

    settings = seed.context.settings
    assert all(
        settings.broll_min_shot_ms <= row.end_ms - row.start_ms <= settings.broll_max_shot_ms
        for row in _stored(engine, seed)
    )


@pytest.mark.integration
def test_a_transcript_stored_without_words_is_refused_rather_than_planned_around(
    engine: Engine,
) -> None:
    """Beats are keyed to words, so a wordless transcript cannot be planned against."""
    seed = _seed(engine, suffix="broll-wordless")
    with engine.begin() as connection:
        connection.execute(
            Transcript.__table__.update()
            .where(Transcript.project_id == seed.project_id)
            .values(words=[])
        )
    runner = BrollPlanStageRunner(provider_factory=lambda _: _provider([_result(_beats())]))

    with pytest.raises(TerminalJobError) as error:
        runner(seed.context)

    assert str(error.value) == INTEGRITY_CODE


@pytest.mark.integration
def test_a_write_that_conflicts_on_something_other_than_the_plan_is_reported(
    engine: Engine,
) -> None:
    """A conflict the plan's own key cannot explain must fail rather than look convergent."""
    seed = _seed(engine, suffix="broll-foreign-conflict")

    class _JobDeletingProvider:
        """Remove the Job row this plan's usage rows reference, mid-attempt."""

        def propose(self, **kwargs: Any) -> ProposalResult:
            """Let retention take the Job away between the check and the insert."""
            del kwargs
            with engine.begin() as connection:
                connection.execute(
                    JobEvent.__table__.delete().where(JobEvent.job_id == seed.context.job_id)
                )
                connection.execute(
                    BrollPlanRequest.__table__.delete().where(
                        BrollPlanRequest.job_id == seed.context.job_id
                    )
                )
                connection.execute(Job.__table__.delete().where(Job.id == seed.context.job_id))
            return _result(_beats())

    with pytest.raises(TerminalJobError) as error:
        BrollPlanStageRunner(provider_factory=lambda _: _JobDeletingProvider())(seed.context)

    assert str(error.value) == INTEGRITY_CODE
    assert _stored(engine, seed) == []
