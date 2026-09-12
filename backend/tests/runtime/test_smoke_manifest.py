"""Static contract for the image-level workflow smoke manifest."""

from __future__ import annotations

from .seed_smoke import SCENARIOS, SMOKE_IDENTITY, validate_fixture


def test_smoke_uses_one_fixed_identity_and_every_required_phase() -> None:
    validate_fixture()

    assert str(SMOKE_IDENTITY) == "46000000-0000-4000-8000-000000000046"
    assert {scenario.phase for scenario in SCENARIOS} == {
        "candidate-and-broll-plan",
        "broll-retrieval",
        "acceptance-and-edit-revision",
        "render-artifact-idempotency",
        "playable-mp4-and-dialogue",
        "independent-publications-and-partial-success",
    }
