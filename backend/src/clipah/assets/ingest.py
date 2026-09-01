"""Orchestrate bounded source download, validation, derivatives, and private uploads."""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import UUID, uuid5

import httpx

from clipah.assets.ffmpeg import FFmpegRunner
from clipah.assets.keys import derived_asset_key
from clipah.assets.probe import (
    MAX_MEDIA_BYTES,
    MediaValidationError,
    MimeDetector,
    SourceMetadata,
    sniff_mime,
    validate_mime_types,
    validate_source,
)
from clipah.assets.storage import ObjectStore
from clipah.models import AssetKind, AssetSourceType

SIGNED_DOWNLOAD_TTL = timedelta(minutes=5)
DOWNLOAD_CHUNK_BYTES = 1024 * 1024

CancellationCheck = Callable[[], None]
IngestProgress = Callable[[str, float], None]


class IngestIntegrityError(Exception):
    """A deterministic local or object-store artifact failed immutable verification."""


class SourceDownloadError(Exception):
    """A private object could not be downloaded without exposing its signed URL."""


@dataclass(frozen=True, slots=True)
class DownloadedSource:
    """Observed source identity calculated during its one bounded download."""

    size_bytes: int
    sha256: bytes


@dataclass(frozen=True, slots=True)
class SourceAsset:
    """Detached immutable source fields needed outside a database transaction."""

    asset_id: UUID
    workspace_id: UUID
    project_id: UUID
    source_type: AssetSourceType
    storage_key: str
    content_type: str
    size_bytes: int
    sha256: bytes


@dataclass(frozen=True, slots=True)
class IngestArtifact:
    """Complete durable metadata for one validated source or ingest derivative."""

    asset_id: UUID
    kind: AssetKind
    source_type: AssetSourceType
    storage_key: str
    content_type: str
    size_bytes: int
    sha256: bytes
    duration_ms: int | None
    width: int | None
    height: int | None
    video_codec: str | None
    audio_codec: str | None


@dataclass(frozen=True, slots=True)
class IngestResult:
    """The validated source and exactly three artifacts produced by one ingest run."""

    source: IngestArtifact
    proxy: IngestArtifact
    thumbnail: IngestArtifact
    transcription_audio: IngestArtifact


class SourceDownloader(Protocol):
    """Stream one ephemeral private URL into a caller-owned destination."""

    def download(
        self,
        url: str,
        destination: BinaryIO,
        *,
        expected_size: int,
        max_bytes: int,
        cancellation_check: CancellationCheck,
    ) -> DownloadedSource:
        """Return size and digest without retaining the URL or response body."""
        ...


class MediaProcessor(Protocol):
    """Media operations required by ingest without exposing FFmpeg implementation details."""

    def probe(self, source: Path, *, cancellation_check: CancellationCheck) -> SourceMetadata:
        """Return parsed source metadata."""
        ...

    def generate_proxy(
        self,
        source: Path,
        output: Path,
        *,
        duration_ms: int,
        cancellation_check: CancellationCheck,
        progress: Callable[[float], None],
    ) -> None:
        """Create the bounded playback proxy."""
        ...

    def generate_thumbnail(
        self, source: Path, output: Path, *, cancellation_check: CancellationCheck
    ) -> None:
        """Create the JPEG thumbnail."""
        ...

    def generate_transcription_audio(
        self,
        source: Path,
        output: Path,
        *,
        duration_ms: int,
        cancellation_check: CancellationCheck,
        progress: Callable[[float], None],
    ) -> None:
        """Create the mono 16 kHz transcription audio."""
        ...


class HttpxSourceDownloader:
    """Download one private signed URL with fixed timeouts and no redirects."""

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        """Bind an optional test client or create a production client per download."""
        self._client = client

    def download(
        self,
        url: str,
        destination: BinaryIO,
        *,
        expected_size: int,
        max_bytes: int,
        cancellation_check: CancellationCheck,
    ) -> DownloadedSource:
        """Stream one successful response without retaining or redirecting its capability URL."""
        owns_client = self._client is None
        client = self._client or httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(connect=10, read=60, write=30, pool=10),
        )
        try:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                return write_download(
                    response.iter_bytes(DOWNLOAD_CHUNK_BYTES),
                    destination,
                    expected_size=expected_size,
                    max_bytes=max_bytes,
                    cancellation_check=cancellation_check,
                )
        except (httpx.HTTPError, OSError) as error:
            raise SourceDownloadError("ASSET_SOURCE_UNAVAILABLE") from error
        finally:
            if owns_client:
                client.close()


class AssetIngestor:
    """Turn one private source Asset into trusted metadata and deterministic derivatives."""

    def __init__(
        self,
        *,
        store: ObjectStore,
        downloader: SourceDownloader,
        media: MediaProcessor | None = None,
        mime_detector: MimeDetector | None = None,
    ) -> None:
        """Bind external boundaries for deterministic, offline-testable orchestration."""
        self._store = store
        self._downloader = downloader
        self._media = media or FFmpegRunner()
        self._mime_detector = mime_detector

    def ingest(
        self,
        *,
        source: SourceAsset,
        workspace: Path,
        cancellation_check: CancellationCheck,
        progress: IngestProgress,
    ) -> IngestResult:
        """Validate one source, upload complete derivatives, and return durable metadata."""
        source_path = _workspace_file(workspace, "source-media")
        progress("download", 0.0)
        cancellation_check()
        signed = self._store.sign_download(key=source.storage_key, expires_in=SIGNED_DOWNLOAD_TTL)
        with source_path.open("wb") as destination:
            downloaded = self._downloader.download(
                signed.url,
                destination,
                expected_size=source.size_bytes,
                max_bytes=MAX_MEDIA_BYTES,
                cancellation_check=cancellation_check,
            )
        if downloaded.sha256 != source.sha256:
            raise MediaValidationError("ASSET_SOURCE_CHANGED")
        progress("download", 1.0)

        cancellation_check()
        progress("probe", 0.0)
        sniffed_mime = sniff_mime(source_path, detector=self._mime_detector)
        validate_mime_types(declared_mime=source.content_type, sniffed_mime=sniffed_mime)
        metadata = self._media.probe(source_path, cancellation_check=cancellation_check)
        validate_source(
            declared_mime=source.content_type,
            sniffed_mime=sniffed_mime,
            metadata=metadata,
            size_bytes=downloaded.size_bytes,
        )
        progress("probe", 1.0)

        proxy_path = _workspace_file(workspace, "proxy.mp4")
        thumbnail_path = _workspace_file(workspace, "thumbnail.jpg")
        audio_path = _workspace_file(workspace, "transcription.wav")
        cancellation_check()
        progress("proxy", 0.0)
        self._media.generate_proxy(
            source_path,
            proxy_path,
            duration_ms=metadata.duration_ms,
            cancellation_check=cancellation_check,
            progress=lambda ratio: progress("proxy", ratio),
        )
        cancellation_check()
        progress("thumbnail", 0.0)
        self._media.generate_thumbnail(
            source_path, thumbnail_path, cancellation_check=cancellation_check
        )
        progress("thumbnail", 1.0)
        cancellation_check()
        progress("transcription_audio", 0.0)
        self._media.generate_transcription_audio(
            source_path,
            audio_path,
            duration_ms=metadata.duration_ms,
            cancellation_check=cancellation_check,
            progress=lambda ratio: progress("transcription_audio", ratio),
        )

        proxy_width, proxy_height = _proxy_dimensions(metadata.width, metadata.height)
        source_artifact = IngestArtifact(
            asset_id=source.asset_id,
            kind=AssetKind.SOURCE,
            source_type=source.source_type,
            storage_key=source.storage_key,
            content_type=sniffed_mime,
            size_bytes=downloaded.size_bytes,
            sha256=downloaded.sha256,
            duration_ms=metadata.duration_ms,
            width=metadata.width,
            height=metadata.height,
            video_codec=metadata.video_codec,
            audio_codec=metadata.audio_codec,
        )
        derivative_specs = (
            (
                AssetKind.PROXY,
                proxy_path,
                "video/mp4",
                metadata.duration_ms,
                proxy_width,
                proxy_height,
                "h264",
                "aac",
            ),
            (
                AssetKind.THUMBNAIL,
                thumbnail_path,
                "image/jpeg",
                None,
                proxy_width,
                proxy_height,
                None,
                None,
            ),
            (
                AssetKind.TRANSCRIPTION_AUDIO,
                audio_path,
                "audio/wav",
                metadata.duration_ms,
                None,
                None,
                None,
                "pcm_s16le",
            ),
        )
        uploaded: list[IngestArtifact] = []
        progress("upload", 0.0)
        for index, specification in enumerate(derivative_specs, start=1):
            cancellation_check()
            uploaded.append(self._upload(source, workspace=workspace, specification=specification))
            progress("upload", index / len(derivative_specs))
        return IngestResult(
            source=source_artifact,
            proxy=uploaded[0],
            thumbnail=uploaded[1],
            transcription_audio=uploaded[2],
        )

    def _upload(
        self,
        source: SourceAsset,
        *,
        workspace: Path,
        specification: tuple[
            AssetKind,
            Path,
            str,
            int | None,
            int | None,
            int | None,
            str | None,
            str | None,
        ],
    ) -> IngestArtifact:
        """Verify and upload one complete direct-child artifact under its deterministic key."""
        kind, path, content_type, duration, width, height, video_codec, audio_codec = specification
        _require_regular_workspace_file(path, workspace)
        size_bytes, digest = _hash_file(path)
        key = derived_asset_key(
            workspace_id=source.workspace_id,
            project_id=source.project_id,
            source_asset_id=source.asset_id,
            kind=kind,
        )
        with path.open("rb") as artifact_file:
            stored = self._store.put_file(
                key=key,
                content_type=content_type,
                file=artifact_file,
                sha256=digest,
            )
        observed = self._store.head_object(key=key)
        if any(
            candidate.key != key
            or candidate.content_length != size_bytes
            or candidate.sha256 != digest
            for candidate in (stored, observed)
        ):
            self._store.delete_object(key=key)
            raise IngestIntegrityError("derived Asset upload metadata mismatch")
        return IngestArtifact(
            asset_id=uuid5(source.asset_id, kind.value),
            kind=kind,
            source_type=AssetSourceType.DERIVED,
            storage_key=key,
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=digest,
            duration_ms=duration,
            width=width,
            height=height,
            video_codec=video_codec,
            audio_codec=audio_codec,
        )


def write_download(
    chunks: Iterable[bytes],
    destination: BinaryIO,
    *,
    expected_size: int,
    max_bytes: int,
    cancellation_check: CancellationCheck,
) -> DownloadedSource:
    """Write, count, and hash one response stream under fixed observed-size boundaries."""
    digest = hashlib.sha256()
    size_bytes = 0
    for chunk in chunks:
        cancellation_check()
        if not isinstance(chunk, bytes):
            raise MediaValidationError("ASSET_INVALID_MEDIA")
        size_bytes += len(chunk)
        if size_bytes > max_bytes:
            raise MediaValidationError("ASSET_TOO_LARGE")
        destination.write(chunk)
        digest.update(chunk)
    if size_bytes == 0:
        raise MediaValidationError("ASSET_INVALID_MEDIA")
    if size_bytes != expected_size:
        raise MediaValidationError("ASSET_SOURCE_CHANGED")
    return DownloadedSource(size_bytes=size_bytes, sha256=digest.digest())


def _workspace_file(workspace: Path, name: str) -> Path:
    """Construct one fixed direct-child path beneath the caller's Job workspace."""
    if not name or Path(name).name != name:
        raise ValueError("workspace filename must be one safe path component")
    return workspace / name


def _require_regular_workspace_file(path: Path, workspace: Path) -> None:
    """Refuse missing, symlinked, empty, or escaped derivative outputs."""
    try:
        metadata = path.lstat()
    except OSError as error:
        raise IngestIntegrityError("derived Asset output is unavailable") from error
    if (
        path.parent != workspace
        or not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or metadata.st_size <= 0
    ):
        raise IngestIntegrityError("derived Asset output is not a regular workspace file")


def _hash_file(path: Path) -> tuple[int, bytes]:
    """Calculate immutable size and SHA-256 from one completed local artifact."""
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as artifact:
        while chunk := artifact.read(DOWNLOAD_CHUNK_BYTES):
            size_bytes += len(chunk)
            digest.update(chunk)
    return size_bytes, digest.digest()


def _proxy_dimensions(width: int, height: int) -> tuple[int, int]:
    """Mirror the no-upscale orientation-aware FFmpeg bounds for persisted proxy metadata."""
    maximum_width, maximum_height = (1280, 720) if width >= height else (720, 1280)
    ratio = min(1.0, maximum_width / width, maximum_height / height)
    scaled_width = max(2, int(width * ratio) // 2 * 2)
    scaled_height = max(2, int(height * ratio) // 2 * 2)
    return scaled_width, scaled_height
