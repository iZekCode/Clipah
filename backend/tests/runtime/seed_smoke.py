"""Fixed fixture identity and reusable workflow proofs for the runtime smoke."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import UUID

SMOKE_IDENTITY = UUID("46000000-0000-4000-8000-000000000046")
FIXTURE_SHA256 = "7cad1bd14c51c71a47da88f3cb16a9a443e37f9e67a9783e2162a21e50cc8551"
FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "media" / "landscape.mp4"


@dataclass(frozen=True, slots=True)
class SmokeScenario:
    """One existing durable contract reused by the image-level workflow proof."""

    phase: str
    node_id: str


SCENARIOS = (
    SmokeScenario(
        "candidate-and-broll-plan",
        "tests/integration/test_broll_plans.py::"
        "test_a_replayed_plan_persists_the_same_suggestions_without_duplicate_rows",
    ),
    SmokeScenario(
        "broll-retrieval",
        "tests/integration/test_broll_retrieval.py::"
        "test_a_replayed_retrieval_costs_the_workspace_nothing",
    ),
    SmokeScenario(
        "acceptance-and-edit-revision",
        "tests/integration/test_edit_revisions.py::"
        "test_accepting_one_suggestion_twice_places_it_once",
    ),
    SmokeScenario(
        "render-artifact-idempotency",
        "tests/integration/test_render_pipeline.py::"
        "test_the_render_stage_stores_one_artifact_and_a_redelivery_stores_no_second_one",
    ),
    SmokeScenario(
        "playable-mp4-and-dialogue",
        "tests/integration/test_render_pipeline.py::"
        "test_real_ffmpeg_renders_each_fixture_composition_into_a_playable_export",
    ),
    SmokeScenario(
        "independent-publications-and-partial-success",
        "tests/e2e/test_multi_destination_publish.py::"
        "test_a_permanently_refused_destination_leaves_the_batch_partially_failed",
    ),
)


def validate_fixture() -> None:
    """Refuse a smoke run whose checked-in source video drifted unexpectedly."""
    if sha256(FIXTURE_PATH.read_bytes()).hexdigest() != FIXTURE_SHA256:
        raise RuntimeError("runtime smoke fixture checksum unavailable")
