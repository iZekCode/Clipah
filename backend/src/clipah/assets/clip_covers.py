"""Draw one clip Revision's designed cover from the Project's proxy.

A cover is one frame of the clip, framed the way the export frames it, with the chosen
preset's title and the clip's watermark drawn on. Each Revision has at most one, under a
deterministic identity and key, so a redrawn cover is recognized rather than duplicated.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from PIL import Image

from clipah.assets.ffmpeg import CancellationCheck
from clipah.assets.ingest import (
    IngestArtifact,
    IngestIntegrityError,
    SourceDownloader,
    upload_verified_artifact,
)
from clipah.assets.keys import preview_media_key
from clipah.assets.preview_builder import download_verified
from clipah.assets.storage import ObjectStore
from clipah.editor.models import CompositionV1, WatermarkKind
from clipah.models import AssetKind, AssetSourceType
from clipah.renders.cover import cover_asset_id, cover_frame, cover_name, draw_cover
from clipah.renders.models import clipah_logo_path

COVER_JPEG_QUALITY = 90


@dataclass(frozen=True, slots=True)
class StoredPicture:
    """One recorded Workspace picture a cover draws as its watermark."""

    key: str
    size: int
    sha256: bytes


@dataclass(frozen=True, slots=True)
class CoverRevision:
    """One Revision whose cover is still missing, and the picture its mark needs."""

    revision_id: UUID
    composition: CompositionV1
    watermark_picture: StoredPicture | None


@dataclass(frozen=True, slots=True)
class CoverInputs:
    """The recorded proxy and the Revisions whose covers are still missing."""

    source_asset_id: UUID
    workspace_id: UUID
    project_id: UUID
    proxy_key: str
    proxy_size: int
    proxy_sha256: bytes
    proxy_width: int
    proxy_height: int
    revisions: tuple[CoverRevision, ...]


class CoverFrameRenderer(Protocol):
    """The one media operation covers need."""

    def extract_cover_frame(
        self,
        source: Path,
        output: Path,
        *,
        at_ms: int,
        window: tuple[int, int, int, int] | None,
        width: int,
        height: int,
        cancellation_check: CancellationCheck,
    ) -> None:
        """Write one framed PNG at the canvas size."""
        ...


class ClipCoverMaker(Protocol):
    """What the stage runner asks of whatever draws covers."""

    def build(
        self, *, inputs: CoverInputs, workspace: Path, cancellation_check: CancellationCheck
    ) -> tuple[IngestArtifact, ...]:
        """Draw, upload, and describe one cover per Revision, in the order given."""
        ...


class ClipCoverBuilder:
    """Download the verified proxy once, then draw and upload every missing cover."""

    def __init__(
        self, *, store: ObjectStore, downloader: SourceDownloader, media: CoverFrameRenderer
    ) -> None:
        """Bind storage, private download, and media rendering boundaries."""
        self._store = store
        self._downloader = downloader
        self._media = media

    def build(
        self, *, inputs: CoverInputs, workspace: Path, cancellation_check: CancellationCheck
    ) -> tuple[IngestArtifact, ...]:
        """Produce one uploaded cover per Revision."""
        if not inputs.revisions:
            return ()
        proxy_path = workspace / "cover-proxy.mp4"
        download_verified(
            self._store,
            self._downloader,
            key=inputs.proxy_key,
            size=inputs.proxy_size,
            sha256=inputs.proxy_sha256,
            destination=proxy_path,
            cancellation_check=cancellation_check,
        )
        artifacts: list[IngestArtifact] = []
        for revision in inputs.revisions:
            cancellation_check()
            artifacts.append(
                self._draw(inputs, revision, proxy_path, workspace, cancellation_check)
            )
        return tuple(artifacts)

    def _draw(
        self,
        inputs: CoverInputs,
        revision: CoverRevision,
        proxy_path: Path,
        workspace: Path,
        cancellation_check: CancellationCheck,
    ) -> IngestArtifact:
        """Frame, design, and upload one Revision's cover."""
        composition = revision.composition
        cover = composition.cover
        if cover is None:  # pragma: no cover - the runner only hands over designed covers
            raise IngestIntegrityError("a Revision without a cover was handed to the builder")
        frame = cover_frame(composition)
        width, height = composition.canvas.width, composition.canvas.height
        still = workspace / f"cover-{revision.revision_id}.png"
        self._media.extract_cover_frame(
            proxy_path,
            still,
            at_ms=frame.source_ms,
            window=_pixels(frame.window, inputs.proxy_width, inputs.proxy_height),
            width=width,
            height=height,
            cancellation_check=cancellation_check,
        )
        if not still.is_file():
            raise IngestIntegrityError("a cover frame was not written")
        picture = self._watermark_picture(revision, workspace, cancellation_check)
        output = workspace / f"cover-{revision.revision_id}.jpg"
        with Image.open(still) as opened:
            drawn = draw_cover(
                opened,
                cover,
                watermark=composition.watermark,
                watermark_image=picture,
            )
        drawn.save(output, "JPEG", quality=COVER_JPEG_QUALITY)
        key = preview_media_key(
            workspace_id=inputs.workspace_id,
            project_id=inputs.project_id,
            source_asset_id=inputs.source_asset_id,
            name=cover_name(revision.revision_id),
        )
        size_bytes, digest = upload_verified_artifact(
            self._store, path=output, workspace=workspace, key=key, content_type="image/jpeg"
        )
        return IngestArtifact(
            asset_id=cover_asset_id(revision.revision_id),
            kind=AssetKind.COVER,
            source_type=AssetSourceType.DERIVED,
            storage_key=key,
            content_type="image/jpeg",
            size_bytes=size_bytes,
            sha256=digest,
            duration_ms=None,
            width=width,
            height=height,
            video_codec=None,
            audio_codec=None,
        )

    def _watermark_picture(
        self, revision: CoverRevision, workspace: Path, cancellation_check: CancellationCheck
    ) -> Image.Image | None:
        """Open the picture the clip's mark draws: Clipah's own, or a verified download."""
        watermark = revision.composition.watermark
        if watermark is None or watermark.kind is WatermarkKind.TEXT:
            return None
        if watermark.kind is WatermarkKind.CLIPAH:
            with Image.open(clipah_logo_path()) as logo:
                mark: Image.Image = logo.copy()
                return mark
        stored = revision.watermark_picture
        if stored is None:
            raise IngestIntegrityError("a cover's watermark picture is not recorded")
        destination = workspace / f"mark-{revision.revision_id}"
        download_verified(
            self._store,
            self._downloader,
            key=stored.key,
            size=stored.size,
            sha256=stored.sha256,
            destination=destination,
            cancellation_check=cancellation_check,
        )
        with Image.open(destination) as picture:
            copied: Image.Image = picture.copy()
            return copied


def _pixels(
    window: tuple[float, float, float, float] | None, width: int, height: int
) -> tuple[int, int, int, int] | None:
    """Turn a window's shares of the frame into an even pixel box inside the proxy."""
    if window is None:
        return None
    x, y, share_width, share_height = window
    box_width = max(round(share_width * width) // 2 * 2, 2)
    box_height = max(round(share_height * height) // 2 * 2, 2)
    return (
        box_width,
        box_height,
        min(round(x * width), width - box_width),
        min(round(y * height), height - box_height),
    )
