"""Contracts for the member's watermark: how it is stored, and how an export draws it."""

from __future__ import annotations

import copy
from typing import Any
from uuid import UUID

import pytest

from clipah.editor.models import (
    WatermarkKind,
    WatermarkPosition,
    canonical_json,
    collect_asset_ids,
    composition_hash,
    parse_composition,
)
from clipah.editor.repository import CandidateSeed
from clipah.editor.use_cases import initial_composition
from clipah.renders.models import (
    ASSET_MISSING,
    CLIPAH_LOGO_ASSET_ID,
    FEATURE_UNSUPPORTED,
    RenderAsset,
    clipah_logo_asset,
    clipah_logo_path,
)
from unit.test_composition import composition_document as full_document
from unit.test_composition import rejects
from unit.test_render_compiler import (
    BROLL_IMAGE_ID,
    BROLL_VIDEO_ID,
    assets,
    broll_image,
    broll_video,
    composition_document,
    plan_for,
    refusal,
)


def _mark(**overrides: Any) -> dict[str, Any]:
    """Clipah's own mark, bottom right, as a first Edit starts with."""
    values: dict[str, Any] = {
        "kind": "clipah",
        "position": "bottomRight",
        "size": 0.12,
        "opacity": 0.9,
    }
    values.update(overrides)
    return values


@pytest.mark.unit
@pytest.mark.parametrize(
    "mark",
    [
        _mark(kind="image"),
        _mark(assetId=str(BROLL_IMAGE_ID)),
        _mark(kind="text"),
        _mark(kind="text", text="   "),
        _mark(text="clipah.com"),
        _mark(kind="image", assetId=str(BROLL_IMAGE_ID), text="both"),
        _mark(size=0.9),
        _mark(opacity=0.0),
        _mark(position="somewhere"),
    ],
)
def test_a_watermark_must_carry_exactly_what_its_kind_draws(mark: dict[str, Any]) -> None:
    """A picture needs its asset and text its words; nothing else may ride along."""
    document = full_document()
    document["watermark"] = mark
    assert rejects(document)


@pytest.mark.unit
def test_a_document_without_a_watermark_keeps_the_bytes_it_was_stored_with() -> None:
    """Revisions written before watermarks existed must keep their hash and their render."""
    document = full_document()
    composition = parse_composition(document)

    assert composition.watermark is None
    assert b"watermark" not in canonical_json(composition)

    marked = copy.deepcopy(document)
    marked["watermark"] = _mark()
    assert composition_hash(parse_composition(marked)) != composition_hash(composition)


@pytest.mark.unit
def test_an_image_watermark_is_an_asset_the_caller_must_authorize() -> None:
    """A Workspace picture used as a mark is checked like any other media the clip uses."""
    document = full_document()
    document["watermark"] = _mark(kind="image", assetId=str(BROLL_IMAGE_ID))

    assert BROLL_IMAGE_ID in collect_asset_ids(parse_composition(document))


@pytest.mark.unit
def test_a_first_edit_starts_with_clipahs_mark_bottom_right() -> None:
    """Every new clip carries Clipah's mark until the member moves, swaps, or removes it."""
    seed = CandidateSeed(
        candidate_id=UUID("99999999-9999-4999-8999-999999999999"),
        project_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        source_asset_id=UUID("11111111-1111-4111-8111-111111111111"),
        start_ms=1_000,
        end_ms=31_000,
        words=(),
    )

    mark = initial_composition(seed).watermark

    assert mark is not None
    assert mark.kind is WatermarkKind.CLIPAH
    assert mark.position is WatermarkPosition.BOTTOM_RIGHT


@pytest.mark.unit
def test_clipahs_mark_ships_with_the_renderer() -> None:
    """The packaged mark exists, so staging it can never depend on a tenant's storage."""
    assert clipah_logo_path().is_file()
    assert clipah_logo_asset().is_image


def _with_logo(*extra: RenderAsset) -> dict[UUID, RenderAsset]:
    """The asset table a worker hands over once it has staged Clipah's mark."""
    return {**assets(*extra), CLIPAH_LOGO_ASSET_ID: clipah_logo_asset()}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("position", "x", "y"),
    [
        ("topLeft", "43", "43"),
        ("center", "(W-w)/2", "(H-h)/2"),
        ("bottomRight", "W-w-43", "H-h-43"),
        ("middleLeft", "43", "(H-h)/2"),
        ("topCenter", "(W-w)/2", "43"),
    ],
)
def test_clipahs_mark_is_overlaid_in_its_grid_cell(position: str, x: str, y: str) -> None:
    """Each of the nine cells anchors the mark to its edges with the same margin."""
    plan = plan_for(composition_document(watermark=_mark(position=position)), table=_with_logo())

    assert any(entry.asset_id == CLIPAH_LOGO_ASSET_ID and entry.loop_image for entry in plan.inputs)
    assert "scale=130:-2,format=rgba,colorchannelmixer=aa=0.900[wmimg]" in plan.filter_script
    assert f"[wmimg]overlay=x={x}:y={y}:" in plan.filter_script


@pytest.mark.unit
def test_a_workspace_picture_is_drawn_like_clipahs_mark() -> None:
    """A member's own logo is scaled and placed by the same rules."""
    mark = _mark(kind="image", assetId=str(BROLL_IMAGE_ID), position="topRight", size=0.2)

    plan = plan_for(composition_document(watermark=mark), table=assets(broll_image()))

    assert any(entry.asset_id == BROLL_IMAGE_ID for entry in plan.inputs)
    assert "scale=216:-2" in plan.filter_script
    assert "overlay=x=W-w-43:y=43:" in plan.filter_script


@pytest.mark.unit
def test_a_text_watermark_is_drawn_from_a_file() -> None:
    """Watermark words are content, never filter syntax, like every other text."""
    mark = _mark(kind="text", text="@clipah :drop [0:v]", position="bottomCenter", size=0.04)

    plan = plan_for(composition_document(watermark=mark))

    written = next(entry for entry in plan.files if entry.path.name == "composition-watermark.txt")
    assert written.contents == "@clipah :drop [0:v]"
    assert "@clipah" not in plan.filter_script
    assert "fontsize=43" in plan.filter_script
    assert "x=(w-tw)/2:y=h-th-43" in plan.filter_script


@pytest.mark.unit
def test_a_watermark_that_is_not_a_still_or_was_not_staged_is_refused() -> None:
    """A moving mark is an effect the preview cannot show, and a missing one is no export."""
    moving = _mark(kind="image", assetId=str(BROLL_VIDEO_ID))

    assert refusal(composition_document(watermark=moving), table=assets(broll_video())) == (
        FEATURE_UNSUPPORTED
    )
    assert refusal(composition_document(watermark=_mark())) == ASSET_MISSING


@pytest.mark.unit
def test_no_watermark_draws_nothing() -> None:
    """A clip whose member removed the mark exports without one."""
    plan = plan_for(composition_document())

    assert "wmimg" not in plan.filter_script
    assert "composition-watermark" not in plan.filter_script
