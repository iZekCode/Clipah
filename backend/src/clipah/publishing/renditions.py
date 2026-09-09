"""Immutable persistence and cache lookup for provider-specific renditions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.assets.ingest import DownloadedSource
from clipah.assets.storage import ObjectStore
from clipah.models import RenderArtifact, SocialRendition
from clipah.publishing.preflight import MediaFacts, PublicationEvidence, preflight
from clipah.publishing.profiles import ProviderProfile
from clipah.publishing.render_tasks import RenderedRendition
from clipah.social_accounts.models import SocialProvider

SIGNED_DOWNLOAD_TTL = timedelta(minutes=5)
_RENDITION_FIXABLE_CODES = frozenset(
    {
        "file_size",
        "container",
        "video_codec",
        "audio_codec",
        "audio_required",
        "resolution",
        "aspect_ratio",
        "frame_rate",
    }
)


@dataclass(frozen=True, slots=True)
class RenditionProvenance:
    """Secret-free evidence sufficient to reproduce one encoded output."""

    ffmpeg_arguments: tuple[str, ...]
    ffmpeg_config_version: str
    source_checksums: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible immutable-evidence document."""
        return {
            "ffmpegArguments": list(self.ffmpeg_arguments),
            "ffmpegConfigVersion": self.ffmpeg_config_version,
            "sourceChecksums": list(self.source_checksums),
        }


@dataclass(frozen=True, slots=True)
class RenditionResult:
    """Safe rendition identity that deliberately omits its private object key."""

    id: UUID
    render_artifact_id: UUID
    provider: SocialProvider
    profile_version: str
    sha256: bytes
    size_bytes: int
    duration_ms: int
    reused_master: bool


class RenditionIntegrityError(Exception):
    """Frozen source, rendered output, or stored object bytes did not match."""


class RenditionPreflightError(Exception):
    """A rendition cannot safely remediate the blocking preflight evidence."""


class RenditionDownloader(Protocol):
    """Stream one signed source capability into a Job-local file."""

    def download(
        self,
        url: str,
        destination: BinaryIO,
        *,
        expected_size: int,
        max_bytes: int,
        cancellation_check: Callable[[], None],
    ) -> DownloadedSource:
        """Return observed byte identity without retaining the capability URL."""
        ...


class RenditionRenderer(Protocol):
    """Create one provider-shaped file from immutable source bytes."""

    def render(
        self,
        *,
        profile: ProviderProfile,
        source: Path,
        workspace: Path,
        cancellation_check: Callable[[], None],
    ) -> RenderedRendition:
        """Return measured output and reproducibility evidence."""
        ...


def ensure_social_rendition(
    session: Session,
    *,
    artifact: RenderArtifact,
    provider: SocialProvider,
    profile: ProviderProfile,
    master_media: MediaFacts,
    evidence: PublicationEvidence,
    store: ObjectStore,
    downloader: RenditionDownloader,
    renderer: RenditionRenderer,
    workspace: Path,
    cancellation_check: Callable[[], None],
    now: datetime,
) -> RenditionResult:
    """Reuse or derive provider bytes exclusively from one frozen Render Artifact."""
    cancellation_check()
    if artifact.sha256 is None:
        raise RenditionIntegrityError("approved master has no checksum")
    source_sha256 = bytes(artifact.sha256)
    cached = find_cached_rendition(
        session,
        workspace_id=artifact.workspace_id,
        source_sha256=source_sha256,
        provider=provider,
        profile_version=profile.version,
    )
    if cached is not None:
        return cached

    master_report = preflight(profile=profile, media=master_media, evidence=evidence)
    if master_report.passed:
        return record_rendition(
            session,
            workspace_id=artifact.workspace_id,
            render_artifact_id=artifact.id,
            source_sha256=source_sha256,
            provider=provider,
            profile_version=profile.version,
            output_sha256=source_sha256,
            storage_key=artifact.storage_key,
            size_bytes=artifact.size_bytes,
            duration_ms=artifact.duration_ms,
            provenance=RenditionProvenance(
                ffmpeg_arguments=(),
                ffmpeg_config_version="master-reuse-v1",
                source_checksums=(source_sha256.hex(),),
            ),
            validation_report=master_report.as_dict(),
            reused_master=True,
            now=now,
        )
    if any(
        violation.code not in _RENDITION_FIXABLE_CODES for violation in master_report.violations
    ):
        raise RenditionPreflightError("publication evidence requires user remediation")

    cancellation_check()
    signed = store.sign_download(key=artifact.storage_key, expires_in=SIGNED_DOWNLOAD_TTL)
    source = workspace / "social-master.mp4"
    with source.open("wb") as destination:
        downloaded = downloader.download(
            signed.url,
            destination,
            expected_size=artifact.size_bytes,
            max_bytes=artifact.size_bytes,
            cancellation_check=cancellation_check,
        )
    if downloaded.size_bytes != artifact.size_bytes or downloaded.sha256 != source_sha256:
        raise RenditionIntegrityError("downloaded master does not match its frozen identity")

    cancellation_check()
    rendered = renderer.render(
        profile=profile,
        source=source,
        workspace=workspace,
        cancellation_check=cancellation_check,
    )
    output_report = preflight(profile=profile, media=rendered.media, evidence=evidence)
    if not output_report.passed:
        raise RenditionPreflightError("rendered media does not satisfy the provider profile")
    cancellation_check()
    key = _rendition_key(
        workspace_id=artifact.workspace_id,
        source_sha256=source_sha256,
        provider=provider,
        profile_version=profile.version,
    )
    with rendered.path.open("rb") as output:
        uploaded = store.put_file(
            key=key,
            content_type="video/mp4",
            file=output,
            sha256=rendered.sha256,
        )
    observed = store.head_object(key=key)
    if (
        uploaded.content_length != rendered.media.size_bytes
        or observed.content_length != rendered.media.size_bytes
        or uploaded.sha256 != rendered.sha256
        or observed.sha256 != rendered.sha256
    ):
        store.delete_object(key=key)
        raise RenditionIntegrityError("stored rendition does not match the rendered bytes")
    cancellation_check()
    return record_rendition(
        session,
        workspace_id=artifact.workspace_id,
        render_artifact_id=artifact.id,
        source_sha256=source_sha256,
        provider=provider,
        profile_version=profile.version,
        output_sha256=rendered.sha256,
        storage_key=key,
        size_bytes=rendered.media.size_bytes,
        duration_ms=rendered.media.duration_ms,
        provenance=RenditionProvenance(
            ffmpeg_arguments=_safe_arguments(
                rendered.ffmpeg_arguments, source=source, output=rendered.path
            ),
            ffmpeg_config_version=rendered.config_version,
            source_checksums=(source_sha256.hex(),),
        ),
        validation_report=output_report.as_dict(),
        reused_master=False,
        now=now,
    )


def find_cached_rendition(
    session: Session,
    *,
    workspace_id: UUID,
    source_sha256: bytes,
    provider: SocialProvider,
    profile_version: str,
) -> RenditionResult | None:
    """Find the immutable winner for one exact source/provider/profile identity."""
    rendition = session.scalar(
        select(SocialRendition).where(
            SocialRendition.workspace_id == workspace_id,
            SocialRendition.source_sha256 == source_sha256,
            SocialRendition.provider == provider,
            SocialRendition.profile_version == profile_version,
        )
    )
    return None if rendition is None else _result(rendition)


def record_rendition(
    session: Session,
    *,
    workspace_id: UUID,
    render_artifact_id: UUID,
    source_sha256: bytes,
    provider: SocialProvider,
    profile_version: str,
    output_sha256: bytes,
    storage_key: str,
    size_bytes: int,
    duration_ms: int,
    provenance: RenditionProvenance,
    validation_report: Mapping[str, object],
    reused_master: bool,
    now: datetime,
) -> RenditionResult:
    """Return an existing cache row or insert the first immutable rendition evidence."""
    existing = find_cached_rendition(
        session,
        workspace_id=workspace_id,
        source_sha256=source_sha256,
        provider=provider,
        profile_version=profile_version,
    )
    if existing is not None:
        return existing
    rendition = SocialRendition(
        id=uuid4(),
        workspace_id=workspace_id,
        render_artifact_id=render_artifact_id,
        source_sha256=source_sha256,
        provider=provider,
        profile_version=profile_version,
        output_sha256=output_sha256,
        storage_key=storage_key,
        size_bytes=size_bytes,
        duration_ms=duration_ms,
        provenance=provenance.as_dict(),
        validation_report=dict(validation_report),
        reused_master=reused_master,
        created_at=now,
    )
    session.add(rendition)
    session.flush()
    return _result(rendition)


def _result(rendition: SocialRendition) -> RenditionResult:
    """Detach the safe subset of one internal persistence row."""
    return RenditionResult(
        id=rendition.id,
        render_artifact_id=rendition.render_artifact_id,
        provider=rendition.provider,
        profile_version=rendition.profile_version,
        sha256=bytes(rendition.output_sha256),
        size_bytes=rendition.size_bytes,
        duration_ms=rendition.duration_ms,
        reused_master=rendition.reused_master,
    )


def _rendition_key(
    *,
    workspace_id: UUID,
    source_sha256: bytes,
    provider: SocialProvider,
    profile_version: str,
) -> str:
    """Derive one exact tenant key from server-controlled immutable identifiers."""
    return (
        f"workspaces/{workspace_id}/social-renditions/{source_sha256.hex()}/"
        f"{provider.value}/{profile_version}.mp4"
    )


def _safe_arguments(arguments: tuple[str, ...], *, source: Path, output: Path) -> tuple[str, ...]:
    """Retain reproducible configuration without persisting local filesystem paths."""
    replacements = {str(source): "$SOURCE", str(output): "$OUTPUT"}
    return tuple(replacements.get(argument, argument) for argument in arguments)
