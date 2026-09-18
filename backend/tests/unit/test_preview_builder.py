"""Unit contracts for turning a proxy and its audio into uploaded preview artifacts."""

from __future__ import annotations

import hashlib
import io
import struct
import wave
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

import pytest

from clipah.assets.ingest import DownloadedSource, IngestIntegrityError
from clipah.assets.preview_builder import (
    PreviewInputs,
    PreviewMediaBuilder,
    storyboard_asset_id,
    waveform_asset_id,
)
from clipah.assets.storage import FakeObjectStore
from clipah.models import AssetKind, AssetSourceType

NOW = datetime(2026, 9, 17, tzinfo=UTC)


def _wav_bytes(seconds: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(struct.pack("<h", 1_000) * 16_000 * seconds)
    return buffer.getvalue()


class BodyDownloader:
    """Serve fixed bodies by signed URL, the way a private download would."""

    def __init__(self, bodies: dict[str, bytes]) -> None:
        self.bodies = bodies

    def download(
        self,
        url: str,
        destination: BinaryIO,
        *,
        expected_size: int,
        max_bytes: int,
        cancellation_check: Callable[[], None],
    ) -> DownloadedSource:
        cancellation_check()
        key = url.removeprefix("fake://download/")
        body = self.bodies[key]
        destination.write(body)
        return DownloadedSource(size_bytes=len(body), sha256=hashlib.sha256(body).digest())


class SheetRenderer:
    """Write the number of sheets real FFmpeg would for the requested geometry."""

    def __init__(self, sheets: int) -> None:
        self.sheets = sheets
        self.calls: list[tuple[int, int, int]] = []

    def generate_storyboard(
        self,
        source: Path,
        workspace: Path,
        *,
        tile_width: int,
        tile_height: int,
        interval_ms: int,
        columns: int,
        rows: int,
        cancellation_check: Callable[[], None],
    ) -> None:
        cancellation_check()
        assert source.is_file()
        self.calls.append((tile_width, tile_height, interval_ms))
        for index in range(self.sheets):
            (workspace / f"sheet-{index:04d}.jpg").write_bytes(f"jpeg-{index}".encode())


def _inputs(store: FakeObjectStore, bodies: dict[str, bytes], *, duration_ms: int) -> PreviewInputs:
    workspace_id, project_id, source_id = uuid4(), uuid4(), uuid4()
    prefix = f"workspaces/{workspace_id}/projects/{project_id}/derived/{source_id}"
    proxy, audio = b"proxy-bytes", _wav_bytes(3)
    bodies[f"{prefix}/proxy"] = proxy
    bodies[f"{prefix}/transcription_audio"] = audio
    # The fake store only signs objects it holds, exactly like a real bucket.
    store.put_file(key=f"{prefix}/proxy", content_type="video/mp4", file=io.BytesIO(proxy))
    store.put_file(
        key=f"{prefix}/transcription_audio", content_type="audio/wav", file=io.BytesIO(audio)
    )
    return PreviewInputs(
        source_asset_id=source_id,
        workspace_id=workspace_id,
        project_id=project_id,
        proxy_key=f"{prefix}/proxy",
        proxy_size=len(proxy),
        proxy_sha256=hashlib.sha256(proxy).digest(),
        proxy_width=1280,
        proxy_height=720,
        duration_ms=duration_ms,
        audio_key=f"{prefix}/transcription_audio",
        audio_size=len(audio),
        audio_sha256=hashlib.sha256(audio).digest(),
    )


@pytest.mark.unit
def test_the_builder_uploads_every_expected_sheet_and_one_waveform(tmp_path: Path) -> None:
    """Each artifact carries its deterministic identity, key, geometry, and verified digest."""
    store = FakeObjectStore(now=lambda: NOW)
    bodies: dict[str, bytes] = {}
    inputs = _inputs(store, bodies, duration_ms=205_000)
    renderer = SheetRenderer(sheets=2)
    builder = PreviewMediaBuilder(store=store, downloader=BodyDownloader(bodies), media=renderer)

    result = builder.build(inputs=inputs, workspace=tmp_path, cancellation_check=lambda: None)

    assert renderer.calls == [(160, 90, 2_000)]
    assert [sheet.asset_id for sheet in result.sheets] == [
        storyboard_asset_id(inputs.source_asset_id, 0),
        storyboard_asset_id(inputs.source_asset_id, 1),
    ]
    first, last = result.sheets
    assert first.kind is AssetKind.STORYBOARD
    assert first.source_type is AssetSourceType.DERIVED
    assert first.storage_key.endswith("/storyboard-v1/sheet-0000.jpg")
    assert (first.content_type, first.width, first.height) == ("image/jpeg", 1600, 900)
    assert (first.duration_ms, last.duration_ms) == (200_000, 5_000)
    assert result.waveform.asset_id == waveform_asset_id(inputs.source_asset_id)
    assert result.waveform.storage_key.endswith("/waveform-v1.bin")
    assert result.waveform.content_type == "application/octet-stream"
    assert result.waveform.duration_ms == 205_000
    assert store.object_bodies[result.waveform.storage_key] == bytes([8]) * 60
    assert hashlib.sha256(b"jpeg-1").digest() == last.sha256


@pytest.mark.unit
def test_extra_sheets_are_ignored_and_a_short_render_keeps_what_exists(tmp_path: Path) -> None:
    """FFmpeg's frame rounding may add or drop a final partial sheet; neither is fatal."""
    store = FakeObjectStore(now=lambda: NOW)
    bodies: dict[str, bytes] = {}
    inputs = _inputs(store, bodies, duration_ms=205_000)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()

    extra = PreviewMediaBuilder(
        store=store, downloader=BodyDownloader(bodies), media=SheetRenderer(3)
    )
    assert (
        len(
            extra.build(
                inputs=inputs, workspace=tmp_path / "a", cancellation_check=lambda: None
            ).sheets
        )
        == 2
    )

    short = PreviewMediaBuilder(
        store=store, downloader=BodyDownloader(bodies), media=SheetRenderer(1)
    )
    assert (
        len(
            short.build(
                inputs=inputs, workspace=tmp_path / "b", cancellation_check=lambda: None
            ).sheets
        )
        == 1
    )


@pytest.mark.unit
def test_no_sheet_at_all_is_an_integrity_failure(tmp_path: Path) -> None:
    """A proxy FFmpeg could not tile must not become a storyboard with nothing in it."""
    store = FakeObjectStore(now=lambda: NOW)
    bodies: dict[str, bytes] = {}
    inputs = _inputs(store, bodies, duration_ms=205_000)
    builder = PreviewMediaBuilder(
        store=store, downloader=BodyDownloader(bodies), media=SheetRenderer(0)
    )

    with pytest.raises(IngestIntegrityError):
        builder.build(inputs=inputs, workspace=tmp_path, cancellation_check=lambda: None)


@pytest.mark.unit
def test_an_input_whose_bytes_changed_is_refused_before_any_render(tmp_path: Path) -> None:
    """Previews drawn from a different proxy than the one recorded would lie about the source."""
    store = FakeObjectStore(now=lambda: NOW)
    bodies: dict[str, bytes] = {}
    inputs = _inputs(store, bodies, duration_ms=205_000)
    bodies[inputs.proxy_key] = b"tampered"
    renderer = SheetRenderer(2)
    builder = PreviewMediaBuilder(store=store, downloader=BodyDownloader(bodies), media=renderer)

    with pytest.raises(IngestIntegrityError):
        builder.build(inputs=inputs, workspace=tmp_path, cancellation_check=lambda: None)
    assert renderer.calls == []
