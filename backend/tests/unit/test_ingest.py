"""Unit contracts for isolated, deterministic media ingest orchestration."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

import httpx
import pytest

from clipah.assets.ingest import (
    AssetIngestor,
    DownloadedSource,
    HttpxSourceDownloader,
    IngestIntegrityError,
    SourceAsset,
    SourceDownloadError,
    write_download,
)
from clipah.assets.probe import MAX_MEDIA_BYTES, MediaValidationError, SourceMetadata
from clipah.assets.storage import FakeObjectStore, StoredObject
from clipah.models import AssetKind, AssetSourceType

WORKSPACE_ID = UUID("10000000-0000-0000-0000-000000000001")
PROJECT_ID = UUID("20000000-0000-0000-0000-000000000002")
SOURCE_ID = UUID("30000000-0000-0000-0000-000000000003")
NOW = datetime(2026, 9, 1, tzinfo=UTC)
SOURCE_BODY = b"source-media-body"


class ChunkDownloader:
    """Stream fixed source bytes through the real bounded hashing function."""

    def __init__(self, body: bytes = SOURCE_BODY) -> None:
        """Bind one complete fake private response body."""
        self.body = body
        self.urls: list[str] = []

    def download(
        self,
        url: str,
        destination: BinaryIO,
        *,
        expected_size: int,
        max_bytes: int,
        cancellation_check: Callable[[], None],
    ) -> DownloadedSource:
        """Record the ephemeral URL and delegate byte handling to production code."""
        self.urls.append(url)
        midpoint = len(self.body) // 2
        return write_download(
            (self.body[:midpoint], self.body[midpoint:]),
            destination,
            expected_size=expected_size,
            max_bytes=max_bytes,
            cancellation_check=cancellation_check,
        )


class DeterministicMediaProcessor:
    """Materialize complete derivatives while returning trusted source probe metadata."""

    def __init__(self) -> None:
        """Start with no stage calls."""
        self.calls: list[str] = []

    def probe(self, source: Path, *, cancellation_check: Callable[[], None]) -> SourceMetadata:
        """Observe the downloaded file and return supported landscape metadata."""
        assert source.read_bytes() == SOURCE_BODY
        cancellation_check()
        self.calls.append("probe")
        return SourceMetadata(
            duration_ms=42_000,
            width=1920,
            height=1080,
            video_codecs=("h264",),
            audio_codecs=("aac",),
            variable_frame_rate=False,
        )

    def generate_proxy(
        self,
        source: Path,
        output: Path,
        *,
        duration_ms: int,
        cancellation_check: Callable[[], None],
        progress: Callable[[float], None],
    ) -> None:
        """Create deterministic proxy bytes after checking the requested duration."""
        assert source.read_bytes() == SOURCE_BODY
        assert duration_ms == 42_000
        cancellation_check()
        self.calls.append("proxy")
        output.write_bytes(b"proxy-body")
        progress(1.0)

    def generate_thumbnail(
        self, source: Path, output: Path, *, cancellation_check: Callable[[], None]
    ) -> None:
        """Create deterministic thumbnail bytes from the same source."""
        assert source.read_bytes() == SOURCE_BODY
        cancellation_check()
        self.calls.append("thumbnail")
        output.write_bytes(b"jpeg-body")

    def generate_transcription_audio(
        self,
        source: Path,
        output: Path,
        *,
        duration_ms: int,
        cancellation_check: Callable[[], None],
        progress: Callable[[float], None],
    ) -> None:
        """Create deterministic mono-audio bytes after checking the source duration."""
        assert source.read_bytes() == SOURCE_BODY
        assert duration_ms == 42_000
        cancellation_check()
        self.calls.append("transcription_audio")
        output.write_bytes(b"wave-body")
        progress(1.0)


def _source(*, size_bytes: int = len(SOURCE_BODY), sha256: bytes | None = None) -> SourceAsset:
    """Build one complete detached source Asset value."""
    return SourceAsset(
        asset_id=SOURCE_ID,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        source_type=AssetSourceType.SOURCE_IMPORT,
        storage_key=f"workspaces/{WORKSPACE_ID}/projects/{PROJECT_ID}/source/original",
        content_type="video/mp4",
        size_bytes=size_bytes,
        sha256=sha256 or hashlib.sha256(SOURCE_BODY).digest(),
    )


def _store(source: SourceAsset) -> FakeObjectStore:
    """Make the source key signable by the deterministic object-store fake."""
    store = FakeObjectStore(now=lambda: NOW)
    store.objects[source.storage_key] = StoredObject(
        key=source.storage_key,
        content_type=source.content_type,
        content_length=source.size_bytes,
    )
    return store


@pytest.mark.unit
def test_write_download_hashes_chunks_and_enforces_the_observed_size(tmp_path: Path) -> None:
    """A changed or truncated private object must be caught while its bytes are streamed."""
    destination = tmp_path / "source"
    checks: list[None] = []

    with destination.open("wb") as target:
        downloaded = write_download(
            (b"source-", b"media-", b"body"),
            target,
            expected_size=len(SOURCE_BODY),
            max_bytes=MAX_MEDIA_BYTES,
            cancellation_check=lambda: checks.append(None),
        )

    assert destination.read_bytes() == SOURCE_BODY
    assert downloaded == DownloadedSource(
        size_bytes=len(SOURCE_BODY), sha256=hashlib.sha256(SOURCE_BODY).digest()
    )
    assert len(checks) == 3


@pytest.mark.unit
@pytest.mark.parametrize(
    ("chunks", "expected_size", "max_bytes", "code"),
    [
        ((b"short",), 100, MAX_MEDIA_BYTES, "ASSET_SOURCE_CHANGED"),
        ((b"too", b"large"), 8, 7, "ASSET_TOO_LARGE"),
        ((b"",), 0, MAX_MEDIA_BYTES, "ASSET_INVALID_MEDIA"),
    ],
)
def test_write_download_rejects_size_changes_empty_media_and_the_byte_limit(
    tmp_path: Path,
    chunks: Iterable[bytes],
    expected_size: int,
    max_bytes: int,
    code: str,
) -> None:
    """No partial or provider-swapped source may advance to MIME inspection."""
    with (
        (tmp_path / "source").open("wb") as target,
        pytest.raises(MediaValidationError) as captured,
    ):
        write_download(
            chunks,
            target,
            expected_size=expected_size,
            max_bytes=max_bytes,
            cancellation_check=lambda: None,
        )

    assert captured.value.code == code


@pytest.mark.unit
def test_write_download_rejects_non_byte_chunks_before_writing() -> None:
    """A malformed transport iterator must never smuggle an unexpected object into hashing."""
    with pytest.raises(MediaValidationError, match=r"^ASSET_INVALID_MEDIA$"):
        write_download(
            ["not-bytes"],  # type: ignore[list-item]
            BytesIO(),
            expected_size=9,
            max_bytes=MAX_MEDIA_BYTES,
            cancellation_check=lambda: None,
        )


@pytest.mark.unit
def test_httpx_downloader_streams_success_without_following_redirects() -> None:
    """The private download adapter must consume exact bytes without widening its capability."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Return one complete private object while recording the exact request."""
        requests.append(request)
        return httpx.Response(200, content=SOURCE_BODY)

    with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=False) as client:
        destination = BytesIO()
        downloaded = HttpxSourceDownloader(client=client).download(
            "https://storage.example/private?signature=secret",
            destination,
            expected_size=len(SOURCE_BODY),
            max_bytes=MAX_MEDIA_BYTES,
            cancellation_check=lambda: None,
        )

    assert destination.getvalue() == SOURCE_BODY
    assert downloaded.sha256 == hashlib.sha256(SOURCE_BODY).digest()
    assert len(requests) == 1


@pytest.mark.unit
def test_httpx_downloader_sanitizes_redirect_responses_and_signed_urls() -> None:
    """A storage redirect must fail closed without leaking either capability URL."""
    signed_url = "https://storage.example/private?signature=secret"

    def redirect(request: httpx.Request) -> httpx.Response:
        """Attempt to move the private capability to an unapproved host."""
        return httpx.Response(302, headers={"Location": "https://other.example/leak"})

    with (
        httpx.Client(transport=httpx.MockTransport(redirect), follow_redirects=False) as client,
        pytest.raises(SourceDownloadError) as captured,
    ):
        HttpxSourceDownloader(client=client).download(
            signed_url,
            BytesIO(),
            expected_size=len(SOURCE_BODY),
            max_bytes=MAX_MEDIA_BYTES,
            cancellation_check=lambda: None,
        )

    assert str(captured.value) == "ASSET_SOURCE_UNAVAILABLE"
    assert signed_url not in str(captured.value)


@pytest.mark.unit
def test_ingest_returns_complete_metadata_and_uploads_deterministic_artifacts(
    tmp_path: Path,
) -> None:
    """One valid source must converge on the same complete Asset identities and private keys."""
    source = _source()
    store = _store(source)
    downloader = ChunkDownloader()
    media = DeterministicMediaProcessor()
    progress: list[tuple[str, float]] = []
    checks: list[None] = []
    ingestor = AssetIngestor(
        store=store,
        downloader=downloader,
        media=media,
        mime_detector=lambda _: "video/mp4",
    )

    result = ingestor.ingest(
        source=source,
        workspace=tmp_path,
        cancellation_check=lambda: checks.append(None),
        progress=lambda stage, ratio: progress.append((stage, ratio)),
    )

    assert downloader.urls == [f"fake://download/{source.storage_key}"]
    assert media.calls == ["probe", "proxy", "thumbnail", "transcription_audio"]
    assert result.source.sha256 == hashlib.sha256(SOURCE_BODY).digest()
    assert result.source.duration_ms == 42_000
    assert (result.source.width, result.source.height) == (1920, 1080)
    assert (result.proxy.width, result.proxy.height) == (1280, 720)
    assert result.proxy.video_codec == "h264"
    assert result.proxy.audio_codec == "aac"
    assert result.thumbnail.content_type == "image/jpeg"
    assert result.thumbnail.duration_ms is None
    assert result.transcription_audio.content_type == "audio/wav"
    assert result.transcription_audio.audio_codec == "pcm_s16le"
    artifacts = (result.proxy, result.thumbnail, result.transcription_audio)
    assert [artifact.kind for artifact in artifacts] == [
        AssetKind.PROXY,
        AssetKind.THUMBNAIL,
        AssetKind.TRANSCRIPTION_AUDIO,
    ]
    assert len({artifact.asset_id for artifact in artifacts}) == 3
    assert all(str(SOURCE_ID) in artifact.storage_key for artifact in artifacts)
    assert all(artifact.storage_key in store.object_bodies for artifact in artifacts)
    assert progress[0] == ("download", 0.0)
    assert progress[-1] == ("upload", 1.0)
    assert len(checks) >= 6


@pytest.mark.unit
def test_ingest_rejects_a_source_digest_changed_after_signing(tmp_path: Path) -> None:
    """An object replaced after database persistence must stop before probe or FFmpeg work."""
    source = _source(sha256=b"x" * 32)
    store = _store(source)
    media = DeterministicMediaProcessor()

    with pytest.raises(MediaValidationError, match=r"^ASSET_SOURCE_CHANGED$"):
        AssetIngestor(
            store=store,
            downloader=ChunkDownloader(),
            media=media,
            mime_detector=lambda _: "video/mp4",
        ).ingest(
            source=source,
            workspace=tmp_path,
            cancellation_check=lambda: None,
            progress=lambda _stage, _ratio: None,
        )

    assert media.calls == []


@pytest.mark.unit
def test_ingest_rejects_a_mime_mismatch_before_ffprobe(tmp_path: Path) -> None:
    """A byte-sniff mismatch must stop before any untrusted media parser runs."""
    source = _source()
    media = DeterministicMediaProcessor()

    with pytest.raises(MediaValidationError, match=r"^ASSET_MIME_MISMATCH$"):
        AssetIngestor(
            store=_store(source),
            downloader=ChunkDownloader(),
            media=media,
            mime_detector=lambda _: "audio/mpeg",
        ).ingest(
            source=source,
            workspace=tmp_path,
            cancellation_check=lambda: None,
            progress=lambda _stage, _ratio: None,
        )

    assert media.calls == []


class WrongLengthStore(FakeObjectStore):
    """Return mismatched provider metadata after accepting one upload."""

    def put_file(
        self,
        *,
        key: str,
        content_type: str,
        file: BinaryIO,
        sha256: bytes | None = None,
    ) -> StoredObject:
        """Store the real body but lie about its observed length."""
        stored = super().put_file(key=key, content_type=content_type, file=file, sha256=sha256)
        return StoredObject(
            key=stored.key,
            content_type=stored.content_type,
            content_length=stored.content_length + 1,
            sha256=stored.sha256,
        )


@pytest.mark.unit
def test_ingest_deletes_the_exact_uploaded_object_when_provider_metadata_mismatches(
    tmp_path: Path,
) -> None:
    """A partial or altered upload must not survive as a plausible derived Asset."""
    source = _source()
    store = WrongLengthStore(now=lambda: NOW)
    store.objects[source.storage_key] = StoredObject(
        key=source.storage_key,
        content_type=source.content_type,
        content_length=source.size_bytes,
    )
    ingestor = AssetIngestor(
        store=store,
        downloader=ChunkDownloader(),
        media=DeterministicMediaProcessor(),
        mime_detector=lambda _: "video/mp4",
    )

    with pytest.raises(IngestIntegrityError):
        ingestor.ingest(
            source=source,
            workspace=tmp_path,
            cancellation_check=lambda: None,
            progress=lambda _stage, _ratio: None,
        )

    assert store.deleted == [
        f"workspaces/{WORKSPACE_ID}/projects/{PROJECT_ID}/derived/{SOURCE_ID}/proxy"
    ]


class WrongDigestStore(FakeObjectStore):
    """Return provider metadata for bytes whose stored SHA-256 does not match."""

    def head_object(self, *, key: str) -> StoredObject:
        """Corrupt only derivative checksum observations, leaving source signing intact."""
        stored = super().head_object(key=key)
        if stored.sha256 is None:
            return stored
        return StoredObject(
            key=stored.key,
            content_type=stored.content_type,
            content_length=stored.content_length,
            sha256=b"x" * 32,
        )


@pytest.mark.unit
def test_ingest_deletes_an_uploaded_object_when_its_stored_sha256_mismatches(
    tmp_path: Path,
) -> None:
    """Durable metadata may only reference a derivative whose stored digest was verified."""
    source = _source()
    store = WrongDigestStore(now=lambda: NOW)
    store.objects[source.storage_key] = StoredObject(
        key=source.storage_key,
        content_type=source.content_type,
        content_length=source.size_bytes,
    )

    with pytest.raises(IngestIntegrityError):
        AssetIngestor(
            store=store,
            downloader=ChunkDownloader(),
            media=DeterministicMediaProcessor(),
            mime_detector=lambda _: "video/mp4",
        ).ingest(
            source=source,
            workspace=tmp_path,
            cancellation_check=lambda: None,
            progress=lambda _stage, _ratio: None,
        )

    assert store.deleted == [
        f"workspaces/{WORKSPACE_ID}/projects/{PROJECT_ID}/derived/{SOURCE_ID}/proxy"
    ]


class MissingProxyProcessor(DeterministicMediaProcessor):
    """Return from proxy generation without creating the promised regular file."""

    def generate_proxy(self, *args: object, **kwargs: object) -> None:
        """Model a media tool that exits successfully before materializing output."""
        del args, kwargs


@pytest.mark.unit
def test_ingest_refuses_a_missing_completed_derivative(tmp_path: Path) -> None:
    """Only a complete regular Job-workspace file may reach private object storage."""
    source = _source()
    store = _store(source)

    with pytest.raises(IngestIntegrityError):
        AssetIngestor(
            store=store,
            downloader=ChunkDownloader(),
            media=MissingProxyProcessor(),
            mime_detector=lambda _: "video/mp4",
        ).ingest(
            source=source,
            workspace=tmp_path,
            cancellation_check=lambda: None,
            progress=lambda _stage, _ratio: None,
        )

    assert store.object_bodies == {}
