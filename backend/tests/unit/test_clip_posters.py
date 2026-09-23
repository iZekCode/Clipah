"""Unit contracts for drawing one sharp portrait poster per ranked moment."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from clipah.assets.clip_posters import (
    ClipPosterBuilder,
    PosterCrop,
    PosterInputs,
    PosterMoment,
    poster_asset_id,
    poster_candidate_id,
    poster_crop,
    poster_name,
    poster_time_ms,
)
from clipah.assets.ingest import IngestIntegrityError
from clipah.assets.keys import preview_media_key
from clipah.assets.storage import FakeObjectStore
from clipah.models import AssetKind, AssetSourceType
from unit.test_preview_builder import BodyDownloader

NOW = datetime(2026, 9, 23, tzinfo=UTC)


class FrameRenderer:
    """Write one fake JPEG per request, the way real FFmpeg would."""

    def __init__(self, *, write: bool = True) -> None:
        self.write = write
        self.calls: list[tuple[int, int, int, int, int]] = []

    def extract_poster(
        self,
        source: Path,
        output: Path,
        *,
        at_ms: int,
        crop_width: int,
        crop_height: int,
        crop_x: int,
        crop_y: int,
        cancellation_check: Callable[[], None],
    ) -> None:
        cancellation_check()
        assert source.is_file()
        self.calls.append((at_ms, crop_width, crop_height, crop_x, crop_y))
        if self.write:
            output.write_bytes(f"jpeg-{at_ms}".encode())


@pytest.mark.unit
@pytest.mark.parametrize(
    ("start_ms", "end_ms", "expected"),
    [(10_000, 40_000, 11_000), (40_000, 40_500, 40_250), (0, 1, 0)],
)
def test_a_poster_shows_one_second_in_or_the_middle_of_a_short_moment(
    start_ms: int, end_ms: int, expected: int
) -> None:
    """The sharp poster shows the instant the storyboard poster did, so nothing jumps."""
    assert poster_time_ms(start_ms, end_ms) == expected


@pytest.mark.unit
@pytest.mark.parametrize(("start_ms", "end_ms"), [(5_000, 5_000), (-1, 10)])
def test_a_moment_without_a_forward_range_has_no_poster_instant(start_ms: int, end_ms: int) -> None:
    """A range that goes nowhere is a bug upstream, not a frame to guess at."""
    with pytest.raises(ValueError, match="time range"):
        poster_time_ms(start_ms, end_ms)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (1280, 720, PosterCrop(width=404, height=720, x=438, y=0)),
        (720, 1280, PosterCrop(width=720, height=1280, x=0, y=0)),
        (720, 720, PosterCrop(width=404, height=720, x=158, y=0)),
        (720, 1600, PosterCrop(width=720, height=1280, x=0, y=160)),
    ],
)
def test_the_crop_is_the_largest_centred_portrait_box_with_even_sides(
    width: int, height: int, expected: PosterCrop
) -> None:
    """Landscape loses its sides, a taller-than-9:16 frame its top and bottom, never scaled."""
    assert poster_crop(width=width, height=height) == expected


@pytest.mark.unit
def test_a_frame_with_no_size_has_no_crop() -> None:
    """A proxy without dimensions cannot be cropped honestly."""
    with pytest.raises(ValueError, match="frame size"):
        poster_crop(width=0, height=720)


@pytest.mark.unit
def test_a_poster_key_names_its_moment_and_nothing_else_is_read_as_one() -> None:
    """The API finds each poster's moment from its key, so the round trip must be exact."""
    candidate_id, source_id = uuid4(), uuid4()
    key = preview_media_key(
        workspace_id=uuid4(),
        project_id=uuid4(),
        source_asset_id=source_id,
        name=poster_name(candidate_id),
    )

    assert poster_candidate_id(key) == candidate_id
    assert poster_candidate_id(key.replace("posters-v1", "posters-v2")) is None
    assert poster_candidate_id("storyboard-v1/sheet-0000.jpg") is None
    assert poster_asset_id(source_id, candidate_id) == poster_asset_id(source_id, candidate_id)
    assert poster_asset_id(source_id, candidate_id) != poster_asset_id(source_id, uuid4())


def _inputs(
    store: FakeObjectStore, bodies: dict[str, bytes], moments: tuple[PosterMoment, ...]
) -> PosterInputs:
    workspace_id, project_id, source_id = uuid4(), uuid4(), uuid4()
    key = f"workspaces/{workspace_id}/projects/{project_id}/derived/{source_id}/proxy"
    proxy = b"proxy-bytes"
    bodies[key] = proxy
    store.put_file(key=key, content_type="video/mp4", file=io.BytesIO(proxy))
    return PosterInputs(
        source_asset_id=source_id,
        workspace_id=workspace_id,
        project_id=project_id,
        proxy_key=key,
        proxy_size=len(proxy),
        proxy_sha256=hashlib.sha256(proxy).digest(),
        proxy_width=1280,
        proxy_height=720,
        moments=moments,
    )


@pytest.mark.unit
def test_the_builder_downloads_once_and_uploads_one_verified_poster_per_moment(
    tmp_path: Path,
) -> None:
    """Every poster carries its deterministic identity, key, crop size, and digest."""
    store = FakeObjectStore(now=lambda: NOW)
    bodies: dict[str, bytes] = {}
    first, second = uuid4(), uuid4()
    inputs = _inputs(
        store,
        bodies,
        (
            PosterMoment(candidate_id=first, at_ms=11_000),
            PosterMoment(candidate_id=second, at_ms=40_250),
        ),
    )
    media = FrameRenderer()

    artifacts = ClipPosterBuilder(
        store=store, downloader=BodyDownloader(bodies), media=media
    ).build(inputs=inputs, workspace=tmp_path, cancellation_check=lambda: None)

    assert media.calls == [(11_000, 404, 720, 438, 0), (40_250, 404, 720, 438, 0)]
    assert [artifact.asset_id for artifact in artifacts] == [
        poster_asset_id(inputs.source_asset_id, first),
        poster_asset_id(inputs.source_asset_id, second),
    ]
    poster = artifacts[0]
    assert poster.kind is AssetKind.POSTER
    assert poster.source_type is AssetSourceType.DERIVED
    assert poster.storage_key.endswith(f"/derived/{inputs.source_asset_id}/posters-v1/{first}.jpg")
    assert (poster.width, poster.height, poster.duration_ms) == (404, 720, None)
    assert poster.sha256 == hashlib.sha256(b"jpeg-11000").digest()
    assert poster.storage_key in store.objects


@pytest.mark.unit
def test_the_builder_draws_nothing_when_no_moment_is_missing_a_poster(tmp_path: Path) -> None:
    """A run with nothing to draw must not even download the proxy."""
    store = FakeObjectStore(now=lambda: NOW)
    media = FrameRenderer()

    artifacts = ClipPosterBuilder(store=store, downloader=BodyDownloader({}), media=media).build(
        inputs=_inputs(store, {}, ()), workspace=tmp_path, cancellation_check=lambda: None
    )

    assert artifacts == ()
    assert media.calls == []


@pytest.mark.unit
def test_a_frame_ffmpeg_did_not_write_is_an_integrity_failure(tmp_path: Path) -> None:
    """Uploading nothing under a poster's key would promise a picture that does not exist."""
    store = FakeObjectStore(now=lambda: NOW)
    bodies: dict[str, bytes] = {}
    inputs = _inputs(store, bodies, (PosterMoment(candidate_id=uuid4(), at_ms=0),))

    with pytest.raises(IngestIntegrityError, match="not written"):
        ClipPosterBuilder(
            store=store, downloader=BodyDownloader(bodies), media=FrameRenderer(write=False)
        ).build(inputs=inputs, workspace=tmp_path, cancellation_check=lambda: None)


@pytest.mark.unit
def test_a_proxy_whose_bytes_changed_is_refused_before_any_frame_is_drawn(
    tmp_path: Path,
) -> None:
    """A poster of other bytes than the ones ingest recorded would be a poster of a stranger."""
    store = FakeObjectStore(now=lambda: NOW)
    bodies: dict[str, bytes] = {}
    inputs = _inputs(store, bodies, (PosterMoment(candidate_id=uuid4(), at_ms=0),))
    bodies[inputs.proxy_key] = b"other-bytes"
    media = FrameRenderer()

    with pytest.raises(IngestIntegrityError):
        ClipPosterBuilder(store=store, downloader=BodyDownloader(bodies), media=media).build(
            inputs=inputs, workspace=tmp_path, cancellation_check=lambda: None
        )
    assert media.calls == []
