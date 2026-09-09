"""Real-Postgres contracts for immutable provider rendition caching."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clipah.assets.ffmpeg import SubprocessExecutor
from clipah.assets.ingest import DownloadedSource
from clipah.assets.storage import FakeObjectStore, StoredObject
from clipah.db import RuntimeRole, session_scope
from clipah.models import RenderArtifact, SocialRendition
from clipah.publishing.preflight import MediaFacts, PublicationEvidence
from clipah.publishing.profiles import profile_for
from clipah.publishing.render_tasks import (
    RenderedRendition,
    SocialRenditionRenderer,
    build_rendition_arguments,
)
from clipah.publishing.renditions import (
    RenditionIntegrityError,
    RenditionProvenance,
    ensure_social_rendition,
    record_rendition,
)
from clipah.social_accounts.models import SocialProvider
from support import provision_identity, runtime_settings

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 9, 8, 0, tzinfo=UTC)
SOURCE_SHA256 = bytes.fromhex("42" * 32)
OUTPUT_SHA256 = bytes.fromhex("0e4d51ac3800e560ca6be6577fd3378f6840412b94a2be0efc8195817e853847")
MEDIA_FIXTURES = Path(__file__).parents[1] / "fixtures" / "media"


def _seed_render_artifact(
    engine: Engine, *, suffix: str, workspace_id: UUID | None = None, user_id: UUID | None = None
) -> dict[str, UUID]:
    """Create the smallest valid tenant graph ending in one portrait Render Artifact."""
    if workspace_id is None or user_id is None:
        user_id, workspace_id = provision_identity(engine, suffix=suffix)
    ids = {
        name: uuid4()
        for name in ("project", "asset", "transcript", "candidate", "edit", "revision", "render")
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects
                    (id, workspace_id, created_by_user_id, name, status, source_kind)
                VALUES (:project, :workspace, :user, 'Rendition', 'ready', 'upload')
                """
            ),
            {**ids, "workspace": workspace_id, "user": user_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO assets
                    (id, workspace_id, project_id, kind, source_type, storage_key,
                     content_type, size_bytes, duration_ms, sha256)
                VALUES (:asset, :workspace, :project, 'source', 'user_upload',
                        'source.mp4', 'video/mp4', 100, 30000, :digest)
                """
            ),
            {**ids, "workspace": workspace_id, "digest": SOURCE_SHA256},
        )
        connection.execute(
            text(
                """
                INSERT INTO transcripts
                    (id, workspace_id, project_id, asset_id, provider, provider_version, model,
                     language, full_text, words, speaker_segments, utterances, duration_ms,
                     raw_result_storage_key)
                VALUES (:transcript, :workspace, :project, :asset, 'fake', '1', 'fake',
                        'en', 'hello', '[]', '[]', '[]', 30000, 'transcript.json')
                """
            ),
            {**ids, "workspace": workspace_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO clip_candidates
                    (id, workspace_id, project_id, transcript_id, rank, score, hook, reason,
                     category, tags, start_ms, end_ms, transcript_excerpt, score_breakdown,
                     context_warnings, visual_opportunities, model_metadata)
                VALUES (:candidate, :workspace, :project, :transcript, 1, 0.9, 'Hook', 'Reason',
                        'story', '{}', 0, 30000, 'hello', '{}', '{}', '[]', '{}')
                """
            ),
            {**ids, "workspace": workspace_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO clip_edits
                    (id, workspace_id, candidate_id, created_by_user_id, current_revision)
                VALUES (:edit, :workspace, :candidate, :user, 1)
                """
            ),
            {**ids, "workspace": workspace_id, "user": user_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO clip_edit_revisions
                    (id, workspace_id, clip_edit_id, revision, composition,
                     composition_hash, created_by_user_id)
                VALUES (:revision, :workspace, :edit, 1, '{}', :digest, :user)
                """
            ),
            {**ids, "workspace": workspace_id, "user": user_id, "digest": SOURCE_SHA256},
        )
        connection.execute(
            text(
                """
                INSERT INTO render_artifacts
                    (id, workspace_id, clip_edit_revision_id, preset, composition_hash, sha256,
                     storage_key, size_bytes, duration_ms)
                VALUES (:render, :workspace, :revision, '1080x1920', :digest, :digest,
                        'render.mp4', 999, 30000)
                """
            ),
            {**ids, "workspace": workspace_id, "digest": SOURCE_SHA256},
        )
    return {**ids, "workspace": workspace_id, "user": user_id}


def _record(session: Session, seed: dict[str, UUID], **changes: Any) -> Any:
    """Record one rendition with literal provenance suitable for cache assertions."""
    values: dict[str, Any] = {
        "workspace_id": seed["workspace"],
        "render_artifact_id": seed["render"],
        "source_sha256": SOURCE_SHA256,
        "provider": SocialProvider.INSTAGRAM,
        "profile_version": "2026-09-09",
        "output_sha256": OUTPUT_SHA256,
        "storage_key": "workspaces/private/social/output.mp4",
        "size_bytes": 800,
        "duration_ms": 30_000,
        "provenance": RenditionProvenance(
            ffmpeg_arguments=("ffmpeg", "-nostdin", "output.mp4"),
            ffmpeg_config_version="social-rendition-v1",
            source_checksums=(SOURCE_SHA256.hex(),),
        ),
        "validation_report": {"passed": True, "violations": []},
        "reused_master": False,
        "now": NOW,
    }
    values.update(changes)
    return record_rendition(session, **values)


def test_same_cache_key_reuses_the_first_immutable_rendition(
    engine: Engine, clean_database: None
) -> None:
    """A retry must converge on existing bytes instead of rewriting provenance."""
    seed = _seed_render_artifact(engine, suffix="rendition-cache")

    with session_scope(
        settings=runtime_settings(RuntimeRole.WORKER),
        runtime_role=RuntimeRole.WORKER,
        workspace_id=seed["workspace"],
        user_id=seed["user"],
    ) as session:
        first = _record(session, seed)
        second = _record(
            session,
            seed,
            output_sha256=bytes.fromhex("99" * 32),
            storage_key="must-not-replace-first.mp4",
        )

        assert second == first
        assert first.sha256 == OUTPUT_SHA256

    with Session(engine) as session:
        stored = session.scalars(select(SocialRendition)).all()
        assert len(stored) == 1
        assert stored[0].storage_key == "workspaces/private/social/output.mp4"


def test_safe_rendition_result_omits_the_private_storage_key(
    engine: Engine, clean_database: None
) -> None:
    """Callers may identify immutable bytes without receiving an internal object key."""
    seed = _seed_render_artifact(engine, suffix="rendition-safe")

    with Session(engine) as session, session.begin():
        result = _record(session, seed)

    assert result.render_artifact_id == seed["render"]
    assert result.provider is SocialProvider.INSTAGRAM
    assert not hasattr(result, "storage_key")


@pytest.mark.parametrize(
    ("runtime_role", "statement"),
    [
        (RuntimeRole.API, "UPDATE social_renditions SET size_bytes = 1"),
        (RuntimeRole.WORKER, "DELETE FROM social_renditions"),
    ],
)
def test_runtime_roles_cannot_rewrite_or_delete_rendition_evidence(
    engine: Engine,
    clean_database: None,
    runtime_role: RuntimeRole,
    statement: str,
) -> None:
    """Immutability must be a database privilege, not a repository convention."""
    seed = _seed_render_artifact(engine, suffix=f"immutable-{runtime_role.value}")
    with Session(engine) as session, session.begin():
        _record(session, seed)

    with (
        pytest.raises(Exception) as refused,
        session_scope(
            settings=runtime_settings(runtime_role),
            runtime_role=runtime_role,
            workspace_id=seed["workspace"],
            user_id=seed["user"],
        ) as session,
    ):
        session.execute(text(statement))

    assert "permission denied" in str(refused.value).lower()


def test_rls_hides_a_rendition_from_another_workspace(engine: Engine, clean_database: None) -> None:
    """Removing tenant context must not reveal cached publication media."""
    first = _seed_render_artifact(engine, suffix="rendition-one")
    second_user, second_workspace = provision_identity(engine, suffix="rendition-two")
    with Session(engine) as session, session.begin():
        rendition = _record(session, first)

    with session_scope(
        settings=runtime_settings(RuntimeRole.WORKER),
        runtime_role=RuntimeRole.WORKER,
        workspace_id=second_workspace,
        user_id=second_user,
    ) as session:
        hidden = session.scalar(select(SocialRendition).where(SocialRendition.id == rendition.id))

    assert hidden is None


def test_database_rejects_a_cross_workspace_source_artifact_link(
    engine: Engine, clean_database: None
) -> None:
    """A rendition can never be re-parented away from its source Render Artifact."""
    first = _seed_render_artifact(engine, suffix="rendition-fk-one")
    second_user, second_workspace = provision_identity(engine, suffix="rendition-fk-two")

    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        _record(
            session,
            {**first, "workspace": second_workspace, "user": second_user},
        )


def _master_media(**changes: Any) -> MediaFacts:
    """Describe the canonical portrait master with literal measured facts."""
    base = MediaFacts(
        size_bytes=999,
        duration_ms=30_000,
        container="mp4",
        video_codec="h264",
        audio_codec="aac",
        width=1_080,
        height=1_920,
        frame_rate=Fraction(30, 1),
    )
    values = {field: getattr(base, field) for field in base.__dataclass_fields__}
    values.update(changes)
    return MediaFacts(**values)


def _publication_evidence() -> PublicationEvidence:
    """Return provider-safe frozen evidence for a social rendition."""
    return PublicationEvidence(
        captions_attached=True,
        thumbnail_attached=True,
        promotional_watermarks=(),
        overlays=(),
        metadata={"caption": ""},
        disclosures={},
    )


class _Downloader:
    """Write known source bytes and report their observed integrity."""

    def __init__(self, *, sha256: bytes = SOURCE_SHA256) -> None:
        """Bind the digest this controlled boundary reports."""
        self.sha256 = sha256
        self.calls = 0

    def download(
        self,
        url: str,
        destination: BinaryIO,
        *,
        expected_size: int,
        max_bytes: int,
        cancellation_check: Callable[[], None],
    ) -> DownloadedSource:
        """Exercise cancellation and write exactly the expected placeholder length."""
        self.calls += 1
        cancellation_check()
        assert url == "fake://download/render.mp4"
        assert max_bytes == expected_size == 999
        destination.write(b"s" * expected_size)
        return DownloadedSource(size_bytes=expected_size, sha256=self.sha256)


class _Renderer:
    """Produce deterministic provider-ready bytes without invoking native FFmpeg."""

    def __init__(self) -> None:
        """Expose calls so cache reuse is observable."""
        self.calls = 0

    def render(
        self,
        *,
        profile: Any,
        source: Path,
        workspace: Path,
        cancellation_check: Callable[[], None],
    ) -> RenderedRendition:
        """Write a fixed result and return independently specified media facts."""
        self.calls += 1
        cancellation_check()
        assert source.read_bytes() == b"s" * 999
        output = workspace / "social-rendition.mp4"
        output.write_bytes(b"rendition")
        return RenderedRendition(
            path=output,
            media=_master_media(size_bytes=9),
            sha256=OUTPUT_SHA256,
            ffmpeg_arguments=("ffmpeg", "-nostdin", str(source), str(output)),
            config_version="social-rendition-v1",
        )


def _store_with_master() -> FakeObjectStore:
    """Return a fake private store containing the seeded Render Artifact."""
    store = FakeObjectStore(now=lambda: NOW)
    store.objects["render.mp4"] = StoredObject(
        key="render.mp4",
        content_type="video/mp4",
        content_length=999,
        sha256=SOURCE_SHA256,
    )
    return store


def test_fixed_ffmpeg_arguments_create_the_profile_output_without_a_shell(tmp_path: Path) -> None:
    """User evidence must never become executable FFmpeg or shell syntax."""
    arguments = build_rendition_arguments(
        profile_for(SocialProvider.INSTAGRAM),
        source=tmp_path / "source.mp4",
        output=tmp_path / "output.mp4",
    )
    assert arguments == (
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(tmp_path / "source.mp4"),
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "30",
        "-b:v",
        "8M",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        "-y",
        str(tmp_path / "output.mp4"),
    )


def test_golden_landscape_media_becomes_a_measured_provider_rendition(tmp_path: Path) -> None:
    """Checked-in real media must produce the exact profile shape before persistence."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("real media tools are unavailable")

    def probe(path: Path, cancellation_check: Callable[[], None]) -> MediaFacts:
        output = SubprocessExecutor().run(
            (
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration,format_name:stream=codec_type,codec_name,width,height,avg_frame_rate",
                "-of",
                "json",
                str(path),
            ),
            timeout_seconds=30,
            cancellation_check=cancellation_check,
        )
        payload = json.loads(output)
        streams = payload["streams"]
        video = next(stream for stream in streams if stream["codec_type"] == "video")
        audio = next(stream for stream in streams if stream["codec_type"] == "audio")
        numerator, denominator = video["avg_frame_rate"].split("/", maxsplit=1)
        return MediaFacts(
            size_bytes=path.stat().st_size,
            duration_ms=round(float(payload["format"]["duration"]) * 1_000),
            container="mp4",
            video_codec=video["codec_name"],
            audio_codec=audio["codec_name"],
            width=video["width"],
            height=video["height"],
            frame_rate=Fraction(int(numerator), int(denominator)),
        )

    profile = profile_for(SocialProvider.INSTAGRAM)
    rendered = SocialRenditionRenderer(probe=probe).render(
        profile=profile,
        source=MEDIA_FIXTURES / "landscape.mp4",
        workspace=tmp_path,
        cancellation_check=lambda: None,
    )

    assert rendered.media.width == profile.output_width
    assert rendered.media.height == profile.output_height
    assert rendered.media.frame_rate == Fraction(profile.output_frame_rate, 1)
    assert rendered.media.video_codec == "h264"
    assert rendered.media.audio_codec == "aac"
    assert rendered.media.size_bytes == rendered.path.stat().st_size
    assert rendered.media.duration_ms > 0
    assert rendered.sha256 != SOURCE_SHA256


def test_compliant_master_is_reused_without_download_render_or_upload(
    engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    """A provider-compatible immutable master must remain byte-for-byte identical."""
    seed = _seed_render_artifact(engine, suffix="master-reuse")
    store = _store_with_master()
    downloader = _Downloader()
    renderer = _Renderer()
    with Session(engine) as session, session.begin():
        artifact = session.get(RenderArtifact, seed["render"])
        assert artifact is not None
        result = ensure_social_rendition(
            session,
            artifact=artifact,
            provider=SocialProvider.INSTAGRAM,
            profile=profile_for(SocialProvider.INSTAGRAM),
            master_media=_master_media(),
            evidence=_publication_evidence(),
            store=store,
            downloader=downloader,
            renderer=renderer,
            workspace=tmp_path,
            cancellation_check=lambda: None,
            now=NOW,
        )

    assert result.reused_master is True
    assert result.sha256 == SOURCE_SHA256
    assert downloader.calls == 0
    assert renderer.calls == 0
    assert set(store.objects) == {"render.mp4"}


def test_incompatible_master_renders_once_and_retries_reuse_the_cached_checksum(
    engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    """Mutable Edit state and repeated encoding must not influence a retry."""
    seed = _seed_render_artifact(engine, suffix="render-cache")
    store = _store_with_master()
    downloader = _Downloader()
    renderer = _Renderer()
    with Session(engine) as session, session.begin():
        artifact = session.get(RenderArtifact, seed["render"])
        assert artifact is not None
        first = ensure_social_rendition(
            session,
            artifact=artifact,
            provider=SocialProvider.INSTAGRAM,
            profile=profile_for(SocialProvider.INSTAGRAM),
            master_media=_master_media(container="mkv"),
            evidence=_publication_evidence(),
            store=store,
            downloader=downloader,
            renderer=renderer,
            workspace=tmp_path,
            cancellation_check=lambda: None,
            now=NOW,
        )
        second = ensure_social_rendition(
            session,
            artifact=artifact,
            provider=SocialProvider.INSTAGRAM,
            profile=profile_for(SocialProvider.INSTAGRAM),
            master_media=_master_media(container="mkv"),
            evidence=_publication_evidence(),
            store=store,
            downloader=downloader,
            renderer=renderer,
            workspace=tmp_path,
            cancellation_check=lambda: None,
            now=NOW,
        )

    assert second == first
    assert first.sha256 == OUTPUT_SHA256
    assert first.reused_master is False
    assert downloader.calls == 1
    assert renderer.calls == 1


def test_source_checksum_mismatch_stops_before_render_and_upload(
    engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    """Rendition provenance is invalid unless downloaded bytes match the frozen master."""
    seed = _seed_render_artifact(engine, suffix="source-integrity")
    store = _store_with_master()
    renderer = _Renderer()
    with Session(engine) as session, session.begin(), pytest.raises(RenditionIntegrityError):
        artifact = session.get(RenderArtifact, seed["render"])
        assert artifact is not None
        ensure_social_rendition(
            session,
            artifact=artifact,
            provider=SocialProvider.INSTAGRAM,
            profile=profile_for(SocialProvider.INSTAGRAM),
            master_media=_master_media(container="mkv"),
            evidence=_publication_evidence(),
            store=store,
            downloader=_Downloader(sha256=bytes.fromhex("ff" * 32)),
            renderer=renderer,
            workspace=tmp_path,
            cancellation_check=lambda: None,
            now=NOW,
        )

    assert renderer.calls == 0
    assert set(store.objects) == {"render.mp4"}


def test_cancellation_before_work_leaves_no_rendition_or_object(
    engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    """A cancelled social-rendition Job must stop before its first external side effect."""
    seed = _seed_render_artifact(engine, suffix="rendition-cancel")
    store = _store_with_master()

    def cancelled() -> None:
        """Represent the Job cancellation boundary with one stable test exception."""
        raise RuntimeError("cancelled")

    with (
        Session(engine) as session,
        session.begin(),
        pytest.raises(RuntimeError, match="cancelled"),
    ):
        artifact = session.get(RenderArtifact, seed["render"])
        assert artifact is not None
        ensure_social_rendition(
            session,
            artifact=artifact,
            provider=SocialProvider.INSTAGRAM,
            profile=profile_for(SocialProvider.INSTAGRAM),
            master_media=_master_media(container="mkv"),
            evidence=_publication_evidence(),
            store=store,
            downloader=_Downloader(),
            renderer=_Renderer(),
            workspace=tmp_path,
            cancellation_check=cancelled,
            now=NOW,
        )

    with Session(engine) as session:
        assert session.scalars(select(SocialRendition)).all() == []
    assert set(store.objects) == {"render.mp4"}
