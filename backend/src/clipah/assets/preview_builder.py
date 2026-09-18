"""Turn one source's proxy and transcription audio into uploaded preview artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid5

from clipah.assets.ffmpeg import CancellationCheck
from clipah.assets.ingest import (
    SIGNED_DOWNLOAD_TTL,
    IngestArtifact,
    IngestIntegrityError,
    SourceDownloader,
    upload_verified_artifact,
)
from clipah.assets.keys import preview_media_key
from clipah.assets.preview_media import (
    WAVEFORM_V1_NAME,
    storyboard_layout,
    storyboard_sheet_name,
    waveform_peaks,
)
from clipah.assets.probe import MAX_MEDIA_BYTES
from clipah.assets.storage import ObjectStore
from clipah.models import AssetKind, AssetSourceType


@dataclass(frozen=True, slots=True)
class PreviewInputs:
    """The recorded identity of the proxy and audio one preview is drawn from."""

    source_asset_id: UUID
    workspace_id: UUID
    project_id: UUID
    proxy_key: str
    proxy_size: int
    proxy_sha256: bytes
    proxy_width: int
    proxy_height: int
    duration_ms: int
    audio_key: str
    audio_size: int
    audio_sha256: bytes


@dataclass(frozen=True, slots=True)
class PreviewMediaResult:
    """Every uploaded sheet in order, and the waveform."""

    sheets: tuple[IngestArtifact, ...]
    waveform: IngestArtifact


class StoryboardRenderer(Protocol):
    """The one media operation previews need."""

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
        cancellation_check: CancellationCheck,
    ) -> None:
        """Write numbered JPEG sheets into the workspace."""
        ...


class PreviewMediaMaker(Protocol):
    """What the stage runner asks of whatever builds previews."""

    def build(
        self, *, inputs: PreviewInputs, workspace: Path, cancellation_check: CancellationCheck
    ) -> PreviewMediaResult:
        """Build, upload, and describe every preview artifact."""
        ...


def storyboard_asset_id(source_asset_id: UUID, index: int) -> UUID:
    """The deterministic Asset identity of one version-one sheet."""
    return uuid5(source_asset_id, f"storyboard-v1:{index}")


def waveform_asset_id(source_asset_id: UUID) -> UUID:
    """The deterministic Asset identity of the version-one waveform."""
    return uuid5(source_asset_id, "waveform-v1")


class PreviewMediaBuilder:
    """Download verified inputs, render sheets and peaks, and upload them verified."""

    def __init__(
        self, *, store: ObjectStore, downloader: SourceDownloader, media: StoryboardRenderer
    ) -> None:
        """Bind storage, private download, and media rendering boundaries."""
        self._store = store
        self._downloader = downloader
        self._media = media

    def build(
        self, *, inputs: PreviewInputs, workspace: Path, cancellation_check: CancellationCheck
    ) -> PreviewMediaResult:
        """Produce the storyboard sheets and waveform for one source."""
        layout = storyboard_layout(
            duration_ms=inputs.duration_ms, width=inputs.proxy_width, height=inputs.proxy_height
        )
        proxy_path = workspace / "preview-proxy.mp4"
        self._download(
            inputs.proxy_key, inputs.proxy_size, inputs.proxy_sha256, proxy_path, cancellation_check
        )
        cancellation_check()
        self._media.generate_storyboard(
            proxy_path,
            workspace,
            tile_width=layout.tile_width,
            tile_height=layout.tile_height,
            interval_ms=layout.policy.interval_ms,
            columns=layout.policy.columns,
            rows=layout.policy.rows,
            cancellation_check=cancellation_check,
        )
        rendered = [
            index
            for index in range(layout.sheet_count)
            if (workspace / f"sheet-{index:04d}.jpg").is_file()
        ]
        if not rendered or rendered != list(range(len(rendered))):
            raise IngestIntegrityError("storyboard sheets are missing or out of order")

        cancellation_check()
        audio_path = workspace / "preview-audio.wav"
        self._download(
            inputs.audio_key, inputs.audio_size, inputs.audio_sha256, audio_path, cancellation_check
        )
        peaks_path = workspace / "waveform.bin"
        with audio_path.open("rb") as audio:
            peaks_path.write_bytes(waveform_peaks(audio))

        sheets = tuple(
            self._upload_sheet(
                inputs,
                workspace,
                index,
                layout.tile_width * layout.policy.columns,
                layout.tile_height * layout.policy.rows,
                layout.sheet_duration_ms(index),
            )
            for index in rendered
        )
        cancellation_check()
        return PreviewMediaResult(
            sheets=sheets, waveform=self._upload_waveform(inputs, workspace, peaks_path)
        )

    def _download(
        self,
        key: str,
        size: int,
        sha256: bytes,
        destination: Path,
        cancellation_check: CancellationCheck,
    ) -> None:
        """Stream one recorded object into the workspace and refuse it if its bytes changed."""
        signed = self._store.sign_download(key=key, expires_in=SIGNED_DOWNLOAD_TTL)
        with destination.open("wb") as output:
            downloaded = self._downloader.download(
                signed.url,
                output,
                expected_size=size,
                max_bytes=MAX_MEDIA_BYTES,
                cancellation_check=cancellation_check,
            )
        if downloaded.sha256 != sha256:
            raise IngestIntegrityError("a preview input no longer matches its recorded digest")

    def _upload_sheet(
        self,
        inputs: PreviewInputs,
        workspace: Path,
        index: int,
        width: int,
        height: int,
        duration_ms: int,
    ) -> IngestArtifact:
        """Upload one sheet under its deterministic key and describe it."""
        key = preview_media_key(
            workspace_id=inputs.workspace_id,
            project_id=inputs.project_id,
            source_asset_id=inputs.source_asset_id,
            name=storyboard_sheet_name(index),
        )
        size_bytes, digest = upload_verified_artifact(
            self._store,
            path=workspace / f"sheet-{index:04d}.jpg",
            workspace=workspace,
            key=key,
            content_type="image/jpeg",
        )
        return IngestArtifact(
            asset_id=storyboard_asset_id(inputs.source_asset_id, index),
            kind=AssetKind.STORYBOARD,
            source_type=AssetSourceType.DERIVED,
            storage_key=key,
            content_type="image/jpeg",
            size_bytes=size_bytes,
            sha256=digest,
            duration_ms=duration_ms,
            width=width,
            height=height,
            video_codec=None,
            audio_codec=None,
        )

    def _upload_waveform(
        self, inputs: PreviewInputs, workspace: Path, path: Path
    ) -> IngestArtifact:
        """Upload the waveform peaks under their deterministic key and describe them."""
        key = preview_media_key(
            workspace_id=inputs.workspace_id,
            project_id=inputs.project_id,
            source_asset_id=inputs.source_asset_id,
            name=WAVEFORM_V1_NAME,
        )
        size_bytes, digest = upload_verified_artifact(
            self._store,
            path=path,
            workspace=workspace,
            key=key,
            content_type="application/octet-stream",
        )
        return IngestArtifact(
            asset_id=waveform_asset_id(inputs.source_asset_id),
            kind=AssetKind.WAVEFORM,
            source_type=AssetSourceType.DERIVED,
            storage_key=key,
            content_type="application/octet-stream",
            size_bytes=size_bytes,
            sha256=digest,
            duration_ms=inputs.duration_ms,
            width=None,
            height=None,
            video_codec=None,
            audio_codec=None,
        )
