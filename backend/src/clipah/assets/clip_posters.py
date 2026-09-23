"""One sharp portrait poster per ranked moment, drawn from the proxy after analysis.

A storyboard tile is 160 pixels on its long side: enough for a filmstrip, not for a card a
reviewer decides on. A poster is one full-resolution frame of the moment, cropped to 9:16
around the centre, taken at the same instant the storyboard poster shows, so the picture
does not jump when the sharper one arrives. The geometry is versioned like the storyboard's:
it lives here, is named in every storage key, and changes only by adding a new version.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid5

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
from clipah.models import AssetKind, AssetSourceType

POSTER_ASPECT_WIDTH = 9
POSTER_ASPECT_HEIGHT = 16
_POSTER_KEY = re.compile(
    r"posters-v1/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jpg$"
)


@dataclass(frozen=True, slots=True)
class PosterCrop:
    """The 9:16 box cut from one proxy frame; the poster keeps its pixels unscaled."""

    width: int
    height: int
    x: int
    y: int


def poster_time_ms(start_ms: int, end_ms: int) -> int:
    """The instant a moment's poster shows: one second in, or its middle when shorter.

    It matches the storyboard poster the browser draws until this one exists.
    """
    if end_ms <= start_ms or start_ms < 0:
        raise ValueError("a moment needs a positive time range")
    return min(start_ms + 1_000, start_ms + (end_ms - start_ms) // 2)


def poster_crop(*, width: int, height: int) -> PosterCrop:
    """The largest centred 9:16 box inside a frame, with even sides."""
    if width <= 0 or height <= 0:
        raise ValueError("a poster needs a positive frame size")
    if width * POSTER_ASPECT_HEIGHT >= height * POSTER_ASPECT_WIDTH:
        crop_height = _even_floor(height)
        crop_width = _even_floor(crop_height * POSTER_ASPECT_WIDTH // POSTER_ASPECT_HEIGHT)
    else:
        crop_width = _even_floor(width)
        crop_height = _even_floor(crop_width * POSTER_ASPECT_HEIGHT // POSTER_ASPECT_WIDTH)
    return PosterCrop(
        width=crop_width,
        height=crop_height,
        x=(width - crop_width) // 2,
        y=(height - crop_height) // 2,
    )


def poster_name(candidate_id: UUID) -> str:
    """The version-one storage name of one moment's poster."""
    return f"posters-v1/{candidate_id}.jpg"


def poster_candidate_id(storage_key: str) -> UUID | None:
    """Recover which moment a poster shows from its storage key, or nothing for any other."""
    match = _POSTER_KEY.search(storage_key)
    return None if match is None else UUID(match.group(1))


def poster_asset_id(source_asset_id: UUID, candidate_id: UUID) -> UUID:
    """The deterministic Asset identity of one version-one poster."""
    return uuid5(source_asset_id, f"poster-v1:{candidate_id}")


@dataclass(frozen=True, slots=True)
class PosterMoment:
    """One moment to draw, by its candidate and the instant its poster shows."""

    candidate_id: UUID
    at_ms: int


@dataclass(frozen=True, slots=True)
class PosterInputs:
    """The recorded proxy and the moments whose posters are still missing."""

    source_asset_id: UUID
    workspace_id: UUID
    project_id: UUID
    proxy_key: str
    proxy_size: int
    proxy_sha256: bytes
    proxy_width: int
    proxy_height: int
    moments: tuple[PosterMoment, ...]


class PosterRenderer(Protocol):
    """The one media operation posters need."""

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
        cancellation_check: CancellationCheck,
    ) -> None:
        """Write one cropped JPEG frame."""
        ...


class ClipPosterMaker(Protocol):
    """What the stage runner asks of whatever draws posters."""

    def build(
        self, *, inputs: PosterInputs, workspace: Path, cancellation_check: CancellationCheck
    ) -> tuple[IngestArtifact, ...]:
        """Draw, upload, and describe one poster per moment, in the order given."""
        ...


class ClipPosterBuilder:
    """Download the verified proxy once, then draw and upload every moment's poster."""

    def __init__(
        self, *, store: ObjectStore, downloader: SourceDownloader, media: PosterRenderer
    ) -> None:
        """Bind storage, private download, and media rendering boundaries."""
        self._store = store
        self._downloader = downloader
        self._media = media

    def build(
        self, *, inputs: PosterInputs, workspace: Path, cancellation_check: CancellationCheck
    ) -> tuple[IngestArtifact, ...]:
        """Produce one uploaded poster per moment."""
        if not inputs.moments:
            return ()
        crop = poster_crop(width=inputs.proxy_width, height=inputs.proxy_height)
        proxy_path = workspace / "poster-proxy.mp4"
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
        for moment in inputs.moments:
            cancellation_check()
            output = workspace / f"poster-{moment.candidate_id}.jpg"
            self._media.extract_poster(
                proxy_path,
                output,
                at_ms=moment.at_ms,
                crop_width=crop.width,
                crop_height=crop.height,
                crop_x=crop.x,
                crop_y=crop.y,
                cancellation_check=cancellation_check,
            )
            if not output.is_file():
                raise IngestIntegrityError("a poster frame was not written")
            artifacts.append(self._upload(inputs, workspace, output, moment, crop))
        return tuple(artifacts)

    def _upload(
        self,
        inputs: PosterInputs,
        workspace: Path,
        path: Path,
        moment: PosterMoment,
        crop: PosterCrop,
    ) -> IngestArtifact:
        """Upload one poster under its deterministic key and describe it."""
        key = preview_media_key(
            workspace_id=inputs.workspace_id,
            project_id=inputs.project_id,
            source_asset_id=inputs.source_asset_id,
            name=poster_name(moment.candidate_id),
        )
        size_bytes, digest = upload_verified_artifact(
            self._store, path=path, workspace=workspace, key=key, content_type="image/jpeg"
        )
        return IngestArtifact(
            asset_id=poster_asset_id(inputs.source_asset_id, moment.candidate_id),
            kind=AssetKind.POSTER,
            source_type=AssetSourceType.DERIVED,
            storage_key=key,
            content_type="image/jpeg",
            size_bytes=size_bytes,
            sha256=digest,
            duration_ms=None,
            width=crop.width,
            height=crop.height,
            video_codec=None,
            audio_codec=None,
        )


def _even_floor(value: int) -> int:
    """Round down to an even integer, never below two."""
    return max(2, value - value % 2)
