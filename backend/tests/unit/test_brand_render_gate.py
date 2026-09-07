"""The render compiler judges a composition by the Brand Kit version it declares.

The editor and the exporter read the same published rules, so a clip that a member was
told complies is a clip that exports. An export that quietly dropped a brand's required
attribution, or burned in a colour the brand never published, would be worse than one that
never started: nobody would know it happened.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

import pytest

from brand_fixtures import (
    BROLL_ASSET_ID,
    SOURCE_ASSET_ID,
    compliant_definition,
    composition_document,
    text_overlay,
    text_style,
    video_overlay,
)
from clipah.brands.models import BrandKitDefinition, ClaimRules
from clipah.editor.models import parse_composition
from clipah.models import AssetKind
from clipah.renders.compiler import compile_render_plan
from clipah.renders.models import RenderAsset, RenderCompilationError, RenderPlan, RenderPreset

WORKSPACE = Path("/tmp/render-workspace")


def _assets(descriptions: Mapping[str, str]) -> dict[UUID, RenderAsset]:
    """The assets this suite places, described the way retrieval described them."""
    return {
        SOURCE_ASSET_ID: RenderAsset(
            asset_id=SOURCE_ASSET_ID,
            kind=AssetKind.PROXY,
            content_type="video/mp4",
            duration_ms=60_000,
            width=1920,
            height=1080,
            description=descriptions.get("source", ""),
        ),
        BROLL_ASSET_ID: RenderAsset(
            asset_id=BROLL_ASSET_ID,
            kind=AssetKind.BROLL,
            content_type="video/mp4",
            duration_ms=10_000,
            width=1920,
            height=1080,
            description=descriptions.get("broll", ""),
        ),
    }


def _compile(
    document: dict[str, object],
    *,
    brand: BrandKitDefinition | None = None,
    descriptions: Mapping[str, str] | None = None,
) -> RenderPlan:
    """Compile one composition at the vertical preset this product exports."""
    return compile_render_plan(
        parse_composition(document),
        assets=_assets(descriptions or {}),
        preset=RenderPreset.PORTRAIT,
        workspace=WORKSPACE,
        brand=brand,
    )


@pytest.mark.unit
def test_a_composition_with_no_brand_kit_compiles_exactly_as_before() -> None:
    """Most clips have no brand to answer to, and the gate may not change their export."""
    assert _compile(composition_document())


@pytest.mark.unit
def test_a_composition_that_honours_its_declared_kit_compiles() -> None:
    """A gate that refused a compliant clip would stop every export this product makes."""
    assert _compile(composition_document(), brand=compliant_definition())


@pytest.mark.unit
def test_an_export_that_breaks_its_declared_kit_is_refused_rather_than_corrected() -> None:
    """The alternative is a file that no longer matches the preview somebody approved."""
    document = composition_document(overlays=[text_overlay(style=text_style(color="#FF00FF"))])

    with pytest.raises(RenderCompilationError) as refusal:
        _compile(document, brand=compliant_definition())

    assert refusal.value.code == "RENDER_BRAND_VIOLATION"
    assert "color_not_in_kit" in refusal.value.detail


@pytest.mark.unit
def test_an_export_missing_a_required_attribution_is_refused() -> None:
    """Attribution a brand owes somebody else is not something an export may drop."""
    definition = compliant_definition()
    definition = definition.model_copy(
        update={
            "claim_rules": ClaimRules(
                required_attribution="Sumber: BPS", forbidden_claim_phrases=()
            )
        }
    )

    with pytest.raises(RenderCompilationError) as refusal:
        _compile(composition_document(), brand=definition)

    assert refusal.value.code == "RENDER_BRAND_VIOLATION"
    assert "attribution_missing" in refusal.value.detail


@pytest.mark.unit
def test_the_exporter_judges_placed_media_by_what_retrieval_said_it_shows() -> None:
    """The compiler cannot watch a stock clip, so provenance is the evidence it has."""
    document = composition_document(overlays=[text_overlay(), video_overlay()])

    with pytest.raises(RenderCompilationError) as refusal:
        _compile(
            document,
            descriptions={"broll": "Friends toasting with alcohol at a bar"},
            brand=compliant_definition(),
        )

    assert refusal.value.code == "RENDER_BRAND_VIOLATION"
    assert "visual_excluded" in refusal.value.detail
