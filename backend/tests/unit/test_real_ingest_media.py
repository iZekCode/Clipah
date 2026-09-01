"""Native integration coverage for complete media ingest artifacts."""

from __future__ import annotations

import hashlib
import shutil
import wave
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

import pytest

from clipah.assets.ffmpeg import FFmpegRunner
from clipah.assets.ingest import (
    AssetIngestor,
    DownloadedSource,
    SourceAsset,
    write_download,
)
from clipah.assets.probe import MediaValidationError, sniff_mime
from clipah.assets.storage import FakeObjectStore, StoredObject
from clipah.models import AssetSourceType

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "media"


class FixtureDownloader:
    """Stream one checked-in fixture through the production bounded hashing path."""

    def __init__(self, fixture: Path) -> None:
        """Bind one immutable local source fixture."""
        self._fixture = fixture

    def download(
        self,
        url: str,
        destination: BinaryIO,
        *,
        expected_size: int,
        max_bytes: int,
        cancellation_check: Callable[[], None],
    ) -> DownloadedSource:
        """Ignore the fake capability URL and copy fixed-size fixture chunks."""
        assert url.startswith("fake://download/")
        with self._fixture.open("rb") as source:
            return write_download(
                iter(lambda: source.read(64 * 1024), b""),
                destination,
                expected_size=expected_size,
                max_bytes=max_bytes,
                cancellation_check=cancellation_check,
            )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("fixture_name", "expected_dimensions", "expect_vfr"),
    [
        ("landscape.mp4", (640, 360), False),
        ("portrait.mp4", (360, 640), False),
        ("variable-frame-rate.mp4", (320, 180), True),
    ],
)
def test_real_media_ingest_generates_inspectable_bounded_artifacts(
    tmp_path: Path,
    fixture_name: str,
    expected_dimensions: tuple[int, int],
    expect_vfr: bool,
) -> None:
    """Pinned-capable hosts exercise real ffprobe, FFmpeg, libmagic, and upload bytes."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("real media tools are unavailable")
    fixture = FIXTURE_ROOT / fixture_name
    try:
        fixture_mime = sniff_mime(fixture)
    except MediaValidationError:
        pytest.skip("native libmagic is unavailable")
    source_body = fixture.read_bytes()
    source = SourceAsset(
        asset_id=uuid4(),
        workspace_id=uuid4(),
        project_id=uuid4(),
        source_type=AssetSourceType.SOURCE_IMPORT,
        storage_key="workspaces/test/projects/test/source/original",
        content_type=fixture_mime,
        size_bytes=len(source_body),
        sha256=hashlib.sha256(source_body).digest(),
    )
    store = FakeObjectStore(now=lambda: datetime(2026, 9, 1, tzinfo=UTC))
    store.objects[source.storage_key] = StoredObject(
        key=source.storage_key,
        content_type=source.content_type,
        content_length=source.size_bytes,
    )

    result = AssetIngestor(
        store=store,
        downloader=FixtureDownloader(fixture),
        media=FFmpegRunner(),
    ).ingest(
        source=source,
        workspace=tmp_path,
        cancellation_check=lambda: None,
        progress=lambda _stage, _ratio: None,
    )

    assert (result.source.width, result.source.height) == expected_dimensions
    assert result.source.duration_ms is not None and result.source.duration_ms > 0
    assert result.proxy.width is not None and result.proxy.width <= 1280
    assert result.proxy.height is not None and result.proxy.height <= 1280
    assert result.proxy.video_codec == "h264"
    assert result.proxy.audio_codec == "aac"
    assert result.source.asset_id != result.proxy.asset_id
    assert (
        FFmpegRunner().probe(fixture, cancellation_check=lambda: None).variable_frame_rate
        is expect_vfr
    )

    proxy_path = tmp_path / "uploaded-proxy.mp4"
    thumbnail_path = tmp_path / "uploaded-thumbnail.jpg"
    audio_path = tmp_path / "uploaded-transcription.wav"
    proxy_path.write_bytes(store.object_bodies[result.proxy.storage_key])
    thumbnail_path.write_bytes(store.object_bodies[result.thumbnail.storage_key])
    audio_path.write_bytes(store.object_bodies[result.transcription_audio.storage_key])
    proxy_metadata = FFmpegRunner().probe(proxy_path, cancellation_check=lambda: None)
    assert proxy_metadata.video_codec == "h264"
    assert proxy_metadata.audio_codec == "aac"
    assert sniff_mime(thumbnail_path) == "image/jpeg"
    with wave.open(str(audio_path), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getframerate() == 16_000
