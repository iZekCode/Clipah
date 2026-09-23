"""Integration contracts for durable, retry-safe clip posters.

A poster is decoration, like a storyboard: a reviewer must never wait on it, a retry must
never draw it twice, and a moment the ranking hid must never get one.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from clipah.assets.clip_posters import PosterInputs, poster_asset_id, poster_name
from clipah.assets.ingest import IngestArtifact, IngestIntegrityError
from clipah.assets.storage import ObjectStoreUnavailableError
from clipah.jobs.clip_posters_task import ClipPostersStageRunner
from clipah.jobs.models import JobContext, RetryableJobError, TerminalJobError
from clipah.jobs.tasks import stage_runners
from clipah.models import Asset, AssetKind, AssetSourceType, ClipCandidate, JobKind, Transcript
from integration.test_preview_media_pipeline import _seed


class StaticMaker:
    """Describe one poster per requested moment without touching FFmpeg or storage."""

    def __init__(self) -> None:
        self.inputs: list[PosterInputs] = []

    def build(
        self, *, inputs: PosterInputs, workspace: Path, cancellation_check: Callable[[], None]
    ) -> tuple[IngestArtifact, ...]:
        cancellation_check()
        assert workspace.is_dir()
        self.inputs.append(inputs)
        prefix = (
            f"workspaces/{inputs.workspace_id}/projects/{inputs.project_id}/derived/"
            f"{inputs.source_asset_id}"
        )
        return tuple(
            IngestArtifact(
                asset_id=poster_asset_id(inputs.source_asset_id, moment.candidate_id),
                kind=AssetKind.POSTER,
                source_type=AssetSourceType.DERIVED,
                storage_key=f"{prefix}/{poster_name(moment.candidate_id)}",
                content_type="image/jpeg",
                size_bytes=90_000,
                sha256=hashlib.sha256(str(moment.candidate_id).encode()).digest(),
                duration_ms=None,
                width=404,
                height=720,
                video_codec=None,
                audio_codec=None,
            )
            for moment in inputs.moments
        )


class FailingMaker(StaticMaker):
    """Raise one boundary failure on build."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def build(self, **kwargs: object) -> tuple[IngestArtifact, ...]:  # type: ignore[override]
        del kwargs
        raise self.error


@pytest.mark.integration
def test_the_poster_runner_is_registered_for_its_job_kind() -> None:
    """A queued poster Job must never fall through to the unsupported-kind failure."""
    assert JobKind.CLIP_POSTERS in stage_runners()


@pytest.mark.integration
def test_each_exposed_moment_gets_one_poster_and_a_redelivery_draws_nothing(
    engine: Engine,
) -> None:
    """Two deliveries converge on one poster per shown moment, drawn at its poster instant."""
    context, source_id = _seed(engine, suffix=f"posters-{uuid4().hex[:8]}")
    best, second, hidden = _moments(engine, context, source_id)
    maker = StaticMaker()
    runner = ClipPostersStageRunner(maker_factory=lambda _settings: maker)

    runner(context)
    runner(context)

    assert len(maker.inputs) == 1
    recorded = maker.inputs[0]
    assert (recorded.proxy_width, recorded.proxy_height) == (1280, 720)
    assert [(moment.candidate_id, moment.at_ms) for moment in recorded.moments] == [
        (best, 11_000),
        (second, 40_250),
    ]
    with Session(engine) as session:
        drawn = set(
            session.scalars(
                select(Asset.id).where(
                    Asset.workspace_id == context.workspace_id, Asset.kind == AssetKind.POSTER
                )
            )
        )
    assert drawn == {poster_asset_id(source_id, best), poster_asset_id(source_id, second)}
    assert poster_asset_id(source_id, hidden) not in drawn


@pytest.mark.integration
def test_only_the_moments_still_missing_a_poster_are_drawn(engine: Engine) -> None:
    """A partly finished run, or a re-analysis, pays only for the posters it lacks."""
    context, source_id = _seed(engine, suffix=f"posters-partial-{uuid4().hex[:8]}")
    best, second, _ = _moments(engine, context, source_id)
    ClipPostersStageRunner(maker_factory=lambda _settings: StaticMaker())(context)
    with engine.begin() as connection:
        connection.execute(
            Asset.__table__.delete().where(Asset.id == poster_asset_id(source_id, second))
        )
    maker = StaticMaker()

    ClipPostersStageRunner(maker_factory=lambda _settings: maker)(context)

    assert [moment.candidate_id for moment in maker.inputs[0].moments] == [second]
    with Session(engine) as session:
        assert session.get(Asset, poster_asset_id(source_id, best)) is not None


@pytest.mark.integration
def test_a_project_without_a_proxy_fails_terminally(engine: Engine) -> None:
    """A poster of a source ingest never finished would be a poster of nothing."""
    context, _ = _seed(engine, suffix=f"posters-missing-{uuid4().hex[:8]}", derivatives=False)

    with pytest.raises(TerminalJobError, match=r"^CLIP_POSTERS_INPUT_MISSING$"):
        ClipPostersStageRunner(maker_factory=lambda _settings: StaticMaker())(context)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("error", "expected", "code"),
    [
        (
            ObjectStoreUnavailableError("secret detail"),
            RetryableJobError,
            "ASSET_STORAGE_UNAVAILABLE",
        ),
        (IngestIntegrityError("detail"), TerminalJobError, "CLIP_POSTERS_INTEGRITY"),
    ],
)
def test_boundary_failures_map_to_stable_codes(
    engine: Engine, error: Exception, expected: type[Exception], code: str
) -> None:
    """No provider or file detail may leave the runner as a Job error code."""
    context, source_id = _seed(engine, suffix=f"posters-fail-{uuid4().hex[:8]}")
    _moments(engine, context, source_id)

    with pytest.raises(expected, match=rf"^{code}$"):
        ClipPostersStageRunner(maker_factory=lambda _settings: FailingMaker(error))(context)


@pytest.mark.integration
def test_a_conflicting_existing_poster_rolls_back_the_whole_set(engine: Engine) -> None:
    """A poster another delivery recorded with other bytes stops every insert of this one."""
    context, source_id = _seed(engine, suffix=f"posters-conflict-{uuid4().hex[:8]}")
    best, second, _ = _moments(engine, context, source_id)

    class Racing(StaticMaker):
        """Draw both moments, then find the second recorded meanwhile with other bytes."""

        def build(
            self, *, inputs: PosterInputs, workspace: Path, cancellation_check: Callable[[], None]
        ) -> tuple[IngestArtifact, ...]:
            artifacts = super().build(
                inputs=inputs, workspace=workspace, cancellation_check=cancellation_check
            )
            with engine.begin() as connection:
                connection.execute(
                    Asset.__table__.insert().values(
                        id=poster_asset_id(source_id, second),
                        workspace_id=context.workspace_id,
                        project_id=context.project_id,
                        kind=AssetKind.POSTER,
                        source_type=AssetSourceType.DERIVED,
                        storage_key="conflicting-key",
                        content_type="image/jpeg",
                        size_bytes=1,
                        sha256=b"z" * 32,
                    )
                )
            return artifacts

    with pytest.raises(TerminalJobError, match=r"^CLIP_POSTERS_INTEGRITY$"):
        ClipPostersStageRunner(maker_factory=lambda _settings: Racing())(context)

    with Session(engine) as session:
        assert session.get(Asset, poster_asset_id(source_id, best)) is None


def _moments(engine: Engine, context: JobContext, source_id: UUID) -> tuple[UUID, UUID, UUID]:
    """Rank two shown moments and one the ranking hid, over one transcript."""
    transcript_id = uuid4()
    best, second, hidden = uuid4(), uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(
            Transcript.__table__.insert().values(
                id=transcript_id,
                workspace_id=context.workspace_id,
                project_id=context.project_id,
                asset_id=source_id,
                provider="assemblyai",
                provider_version="1.0.0",
                model="universal-2",
                language="id",
                full_text="Satu",
                words=[],
                speaker_segments=[],
                utterances=[],
                duration_ms=205_000,
                raw_result_storage_key=(
                    f"workspaces/{context.workspace_id}/projects/{context.project_id}"
                    "/transcripts/raw.json"
                ),
            )
        )
        for candidate_id, rank, start_ms, end_ms, exposed in (
            (second, 2, 40_000, 40_500, True),
            (best, 1, 10_000, 40_000, True),
            (hidden, 3, 90_000, 120_000, False),
        ):
            connection.execute(
                ClipCandidate.__table__.insert().values(
                    id=candidate_id,
                    workspace_id=context.workspace_id,
                    project_id=context.project_id,
                    transcript_id=transcript_id,
                    rank=rank,
                    score=0.9,
                    hook="Hook",
                    reason="Reason",
                    category="insight",
                    start_ms=start_ms,
                    end_ms=end_ms,
                    transcript_excerpt="Satu",
                    score_breakdown={},
                    visual_opportunities=[],
                    model_metadata={"exposed": exposed},
                )
            )
    return best, second, hidden
