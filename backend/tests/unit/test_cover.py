"""Contracts for a clip's cover: its design, the frame it shows, and how it is drawn."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from PIL import Image

from clipah.assets.clip_covers import (
    ClipCoverBuilder,
    CoverInputs,
    CoverRevision,
    StoredPicture,
    _pixels,
)
from clipah.assets.ffmpeg import FFmpegRunner
from clipah.assets.ingest import IngestIntegrityError
from clipah.assets.storage import FakeObjectStore
from clipah.editor.models import (
    Cover,
    CoverPreset,
    Watermark,
    WatermarkKind,
    WatermarkPosition,
    canonical_json,
    parse_composition,
)
from clipah.editor.use_cases import default_cover
from clipah.models import AssetKind
from clipah.renders.cover import (
    CoverUnavailableError,
    cover_asset_id,
    cover_frame,
    cover_name,
    draw_cover,
)
from clipah.renders.models import clipah_logo_path
from unit.test_composition import composition_document, rejects
from unit.test_ffmpeg import RecordingExecutor
from unit.test_preview_builder import BodyDownloader

NOW = datetime(2026, 9, 24, tzinfo=UTC)


def _covered(cover: dict[str, Any], **item: Any) -> dict[str, Any]:
    """The reference composition with one cover and, optionally, a changed base item."""
    document = composition_document()
    document["cover"] = cover
    document["tracks"][0]["items"][0].update(item)
    return document


@pytest.mark.unit
@pytest.mark.parametrize(
    "cover",
    [
        {"atMs": 0, "preset": "bold", "title": "   "},
        {"atMs": 0, "preset": "poster", "title": "Hook"},
        {"atMs": 0, "preset": "bold", "title": "x" * 121},
        {"atMs": 10_000_000, "preset": "bold", "title": "Hook"},
    ],
)
def test_a_cover_must_be_a_real_design_inside_the_clip(cover: dict[str, Any]) -> None:
    """A blank title, an unknown design, or a frame past the clip is refused at save."""
    document = composition_document()
    document["cover"] = cover
    assert rejects(document)


@pytest.mark.unit
def test_a_frame_at_or_past_the_end_of_the_clip_is_refused() -> None:
    """The last instant has no frame of its own to show."""
    document = composition_document()
    document["cover"] = {"atMs": document["durationMs"], "preset": "minimal", "title": None}
    assert rejects(document)


@pytest.mark.unit
def test_a_document_without_a_cover_keeps_its_stored_bytes() -> None:
    """Revisions written before covers existed keep their hash."""
    # "cover" is also an overlay placement, so it is the key that must be absent.
    assert b'"cover":' not in canonical_json(parse_composition(composition_document()))


@pytest.mark.unit
def test_a_first_cover_is_titled_with_the_hook_one_second_in() -> None:
    """The first cover matches the card poster's instant and carries the moment's headline."""
    assert default_cover("Kenapa  bisa\nbangkit?", 30_000) == Cover(
        at_ms=1_000, preset=CoverPreset.BOLD, title="Kenapa bisa bangkit?"
    )
    assert default_cover("", 1_200) == Cover(at_ms=600, preset=CoverPreset.MINIMAL, title=None)


@pytest.mark.unit
def test_a_long_hook_is_cut_at_a_word_boundary() -> None:
    """A cover title fits the schema without ending in half a word."""
    title = default_cover(("kata " * 40).strip(), 30_000).title

    assert title is not None
    assert len(title) <= 120
    assert title.endswith("kata…")


@pytest.mark.unit
def test_a_cover_is_identified_and_stored_by_its_revision() -> None:
    """Redrawing a Revision's cover converges on one identity and one key."""
    revision = uuid4()

    assert cover_asset_id(revision) == cover_asset_id(revision)
    assert cover_asset_id(revision) != cover_asset_id(uuid4())
    assert cover_name(revision) == f"covers-v1/{cover_asset_id(revision)}.jpg"


@pytest.mark.unit
def test_the_frame_is_the_source_moment_under_the_cover_instant() -> None:
    """Clip time maps through the base item to the source, as the export plays it."""
    document = _covered({"atMs": 2_000, "preset": "bold", "title": "Hook"})
    item = document["tracks"][0]["items"][0]

    frame = cover_frame(parse_composition(document))

    offset = 2_000 - item["timelineStartMs"]
    assert frame.source_ms == item["sourceInMs"] + offset


@pytest.mark.unit
def test_a_moving_crop_is_framed_where_it_is_at_that_instant() -> None:
    """A smart crop's window travels linearly between keyframes and holds outside them."""
    crop = {"x": 0.0, "y": 0.0, "width": 0.3, "height": 1.0}
    frames = [
        {
            "atMs": 0,
            "easing": "linear",
            "transform": {"x": 0.2, "y": 0.5, "scale": 1, "rotation": 0},
            "opacity": None,
            "style": None,
        },
        {
            "atMs": 4_000,
            "easing": "linear",
            "transform": {"x": 0.6, "y": 0.5, "scale": 1, "rotation": 0},
            "opacity": None,
            "style": None,
        },
    ]
    document = _covered(
        {"atMs": 2_000, "preset": "bold", "title": "Hook"},
        crop=crop,
        keyframes=frames,
        timelineStartMs=0,
    )

    window = cover_frame(parse_composition(document)).window

    assert window is not None
    assert window[0] == pytest.approx(0.4 - 0.15)
    assert window[2:] == (0.3, 1.0)


@pytest.mark.unit
def test_a_still_crop_is_used_as_it_is() -> None:
    """A fixed window needs no interpolation."""
    crop = {"x": 0.25, "y": 0.0, "width": 0.5, "height": 1.0}
    document = _covered({"atMs": 0, "preset": "bold", "title": "Hook"}, crop=crop, keyframes=[])

    assert cover_frame(parse_composition(document)).window == (0.25, 0.0, 0.5, 1.0)


@pytest.mark.unit
def test_there_is_no_frame_without_a_cover() -> None:
    """A cover of nothing is refused rather than drawn black."""
    with pytest.raises(CoverUnavailableError):
        cover_frame(parse_composition(composition_document()))


@pytest.mark.unit
@pytest.mark.parametrize("preset", list(CoverPreset))
def test_every_design_draws_at_the_canvas_size(preset: CoverPreset) -> None:
    """A cover is exactly as large as the frame it was designed on."""
    frame = Image.new("RGB", (540, 960), (40, 90, 160))

    drawn = draw_cover(
        frame,
        Cover(at_ms=0, preset=preset, title="Terus titik baliknya tuh kapan"),
        watermark=None,
        watermark_image=None,
    )

    assert drawn.size == (540, 960)
    assert drawn.mode == "RGB"


@pytest.mark.unit
def test_a_title_is_drawn_where_its_design_puts_it_and_minimal_draws_none() -> None:
    """Bold writes low, top title writes high, and minimal leaves the frame alone."""
    frame = Image.new("RGB", (540, 960), (40, 90, 160))

    def changed(preset: CoverPreset, box: tuple[int, int, int, int]) -> bool:
        drawn = draw_cover(
            frame, Cover(at_ms=0, preset=preset, title="Hook"), watermark=None, watermark_image=None
        )
        return drawn.crop(box).tobytes() != frame.crop(box).tobytes()

    bottom = (0, 700, 540, 830)
    top = (0, 90, 540, 220)
    assert changed(CoverPreset.BOLD, bottom)
    assert not changed(CoverPreset.BOLD, top)
    assert changed(CoverPreset.TOP_TITLE, top)
    assert changed(CoverPreset.CLEAN, (0, 600, 540, 780))
    assert not changed(CoverPreset.MINIMAL, (0, 0, 540, 960))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mark", "box"),
    [
        (
            Watermark(
                kind=WatermarkKind.CLIPAH, position=WatermarkPosition.TOP_LEFT, size=0.2, opacity=1
            ),
            (21, 21, 120, 120),
        ),
        (
            Watermark(
                kind=WatermarkKind.TEXT,
                position=WatermarkPosition.BOTTOM_RIGHT,
                size=0.05,
                opacity=1,
                text="@clipah",
            ),
            (380, 880, 520, 940),
        ),
    ],
)
def test_the_clips_watermark_is_drawn_in_its_cell(
    mark: Watermark, box: tuple[int, int, int, int]
) -> None:
    """The mark on a cover sits where the export burns it."""
    frame = Image.new("RGB", (540, 960), (0, 0, 0))
    with Image.open(clipah_logo_path()) as logo:
        drawn = draw_cover(
            frame,
            Cover(at_ms=0, preset=CoverPreset.MINIMAL),
            watermark=mark,
            watermark_image=logo.copy(),
        )

    assert drawn.crop(box).getextrema() != frame.crop(box).getextrema()


@pytest.mark.unit
def test_a_picture_watermark_that_is_missing_draws_nothing() -> None:
    """An image mark without its picture leaves the frame as it was."""
    frame = Image.new("RGB", (540, 960), (0, 0, 0))
    mark = Watermark(
        kind=WatermarkKind.IMAGE,
        position=WatermarkPosition.CENTER,
        size=0.2,
        opacity=0.5,
        asset_id=uuid4(),
    )

    drawn = draw_cover(
        frame, Cover(at_ms=0, preset=CoverPreset.MINIMAL), watermark=mark, watermark_image=None
    )

    assert drawn.tobytes() == frame.tobytes()


@pytest.mark.unit
def test_a_window_becomes_an_even_box_inside_the_proxy() -> None:
    """FFmpeg crops in whole even pixels, never past the frame edge."""
    assert _pixels(None, 1280, 720) is None
    assert _pixels((0.9, 0.0, 0.3, 1.0), 1280, 720) == (384, 720, 896, 0)


class FrameRenderer:
    """Write one solid frame per request, the way real FFmpeg would."""

    def __init__(self, *, write: bool = True) -> None:
        self.write = write
        self.calls: list[tuple[int, tuple[int, int, int, int] | None, int, int]] = []

    def extract_cover_frame(
        self,
        source: Path,
        output: Path,
        *,
        at_ms: int,
        window: tuple[int, int, int, int] | None,
        width: int,
        height: int,
        cancellation_check: Callable[[], None],
    ) -> None:
        cancellation_check()
        assert source.is_file()
        self.calls.append((at_ms, window, width, height))
        if self.write:
            Image.new("RGB", (width, height), (10, 20, 30)).save(output, "PNG")


def _inputs(
    store: FakeObjectStore, bodies: dict[str, bytes], revisions: tuple[CoverRevision, ...]
) -> CoverInputs:
    workspace_id, project_id, source_id = uuid4(), uuid4(), uuid4()
    key = f"workspaces/{workspace_id}/projects/{project_id}/derived/{source_id}/proxy"
    proxy = b"proxy-bytes"
    bodies[key] = proxy
    store.put_file(key=key, content_type="video/mp4", file=io.BytesIO(proxy))
    return CoverInputs(
        source_asset_id=source_id,
        workspace_id=workspace_id,
        project_id=project_id,
        proxy_key=key,
        proxy_size=len(proxy),
        proxy_sha256=hashlib.sha256(proxy).digest(),
        proxy_width=1280,
        proxy_height=720,
        revisions=revisions,
    )


def _revision(**overrides: Any) -> CoverRevision:
    """One Revision designing a bold cover over the reference composition."""
    document = _covered({"atMs": 2_000, "preset": "bold", "title": "Hook"})
    document.update(overrides)
    return CoverRevision(
        revision_id=uuid4(), composition=parse_composition(document), watermark_picture=None
    )


@pytest.mark.unit
def test_the_builder_draws_and_uploads_one_verified_cover_per_revision(tmp_path: Path) -> None:
    """Every cover carries its Revision's identity, key, canvas size, and digest."""
    store = FakeObjectStore(now=lambda: NOW)
    bodies: dict[str, bytes] = {}
    revision = _revision(
        watermark={"kind": "clipah", "position": "bottomRight", "size": 0.12, "opacity": 0.9}
    )
    inputs = _inputs(store, bodies, (revision,))
    media = FrameRenderer()

    artifacts = ClipCoverBuilder(store=store, downloader=BodyDownloader(bodies), media=media).build(
        inputs=inputs, workspace=tmp_path, cancellation_check=lambda: None
    )

    assert len(media.calls) == 1
    assert media.calls[0][2:] == (1080, 1920)
    cover = artifacts[0]
    assert cover.asset_id == cover_asset_id(revision.revision_id)
    assert cover.kind is AssetKind.COVER
    assert cover.storage_key.endswith(cover_name(revision.revision_id))
    assert (cover.width, cover.height, cover.content_type) == (1080, 1920, "image/jpeg")
    assert cover.storage_key in store.objects


@pytest.mark.unit
def test_the_builder_draws_a_workspace_picture_mark_from_its_verified_bytes(
    tmp_path: Path,
) -> None:
    """A member's own logo is downloaded like the proxy, and refused if its bytes changed."""
    store = FakeObjectStore(now=lambda: NOW)
    bodies: dict[str, bytes] = {}
    buffer = io.BytesIO()
    Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(buffer, "PNG")
    logo = buffer.getvalue()
    logo_key = "workspaces/w/projects/p/uploads/logo.png"
    bodies[logo_key] = logo
    store.put_file(key=logo_key, content_type="image/png", file=io.BytesIO(logo))
    mark = {
        "kind": "image",
        "position": "topRight",
        "size": 0.2,
        "opacity": 1.0,
        "assetId": str(uuid4()),
    }
    revision = _revision(watermark=mark)
    stored = CoverRevision(
        revision_id=revision.revision_id,
        composition=revision.composition,
        watermark_picture=StoredPicture(
            key=logo_key, size=len(logo), sha256=hashlib.sha256(logo).digest()
        ),
    )

    artifacts = ClipCoverBuilder(
        store=store, downloader=BodyDownloader(bodies), media=FrameRenderer()
    ).build(
        inputs=_inputs(store, bodies, (stored,)),
        workspace=tmp_path,
        cancellation_check=lambda: None,
    )

    assert len(artifacts) == 1
    missing = CoverRevision(
        revision_id=revision.revision_id, composition=revision.composition, watermark_picture=None
    )
    (tmp_path / "again").mkdir()
    with pytest.raises(IngestIntegrityError):
        ClipCoverBuilder(
            store=store, downloader=BodyDownloader(bodies), media=FrameRenderer()
        ).build(
            inputs=_inputs(store, bodies, (missing,)),
            workspace=tmp_path / "again",
            cancellation_check=lambda: None,
        )


@pytest.mark.unit
def test_the_builder_draws_nothing_when_no_cover_is_missing(tmp_path: Path) -> None:
    """A Project whose covers all exist downloads nothing."""
    store = FakeObjectStore(now=lambda: NOW)

    assert (
        ClipCoverBuilder(store=store, downloader=BodyDownloader({}), media=FrameRenderer()).build(
            inputs=_inputs(store, {}, ()), workspace=tmp_path, cancellation_check=lambda: None
        )
        == ()
    )


@pytest.mark.unit
def test_a_frame_ffmpeg_did_not_write_is_an_integrity_failure(tmp_path: Path) -> None:
    """A cover of a frame that never arrived is refused before anything is uploaded."""
    store = FakeObjectStore(now=lambda: NOW)
    bodies: dict[str, bytes] = {}

    with pytest.raises(IngestIntegrityError):
        ClipCoverBuilder(
            store=store, downloader=BodyDownloader(bodies), media=FrameRenderer(write=False)
        ).build(
            inputs=_inputs(store, bodies, (_revision(),)),
            workspace=tmp_path,
            cancellation_check=lambda: None,
        )


@pytest.mark.unit
def test_runner_frames_one_frame_to_the_canvas(tmp_path: Path) -> None:
    """The window is cut first, then the frame fills the canvas the way the export does."""
    executor = RecordingExecutor()
    runner = FFmpegRunner(executor=executor, ffmpeg_timeout_seconds=12.0)

    runner.extract_cover_frame(
        tmp_path / "proxy.mp4",
        tmp_path / "cover.png",
        at_ms=2_500,
        window=(384, 720, 448, 0),
        width=1080,
        height=1920,
        cancellation_check=lambda: None,
    )
    runner.extract_cover_frame(
        tmp_path / "proxy.mp4",
        tmp_path / "cover.png",
        at_ms=0,
        window=None,
        width=1080,
        height=1080,
        cancellation_check=lambda: None,
    )

    moved, _, _ = executor.calls[-2]
    whole, _, _ = executor.calls[-1]
    assert moved[moved.index("-ss") + 1] == "2.500"
    assert moved[moved.index("-vf") + 1] == (
        "crop=384:720:448:0,scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,setsar=1"
    )
    assert whole[whole.index("-vf") + 1].startswith("scale=1080:1080:")


@pytest.mark.unit
@pytest.mark.parametrize(
    "geometry",
    [
        {"at_ms": -1, "window": None, "width": 1080, "height": 1920},
        {"at_ms": 0, "window": None, "width": 0, "height": 1920},
        {"at_ms": 0, "window": (0, 720, 0, 0), "width": 1080, "height": 1920},
    ],
)
def test_runner_refuses_impossible_cover_geometry(tmp_path: Path, geometry: dict[str, Any]) -> None:
    """No command is built for a frame that cannot exist."""
    runner = FFmpegRunner(executor=RecordingExecutor(), ffmpeg_timeout_seconds=12.0)
    with pytest.raises(ValueError):
        runner.extract_cover_frame(
            tmp_path / "proxy.mp4",
            tmp_path / "cover.png",
            cancellation_check=lambda: None,
            **geometry,
        )
