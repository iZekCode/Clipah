"""Real-Postgres contracts for one safely checkpointed YouTube Publication."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from clipah.models import Publication, PublicationAttempt
from clipah.publishing.models import PublicationStatus
from clipah.publishing.providers.youtube.adapter import YouTubePrivacy
from clipah.publishing.providers.youtube.publications import YouTubePublicationCoordinator
from clipah.publishing.providers.youtube.resumable import UploadCheckpoint
from clipah.publishing.providers.youtube.status import (
    AttachmentResult,
    AttachmentState,
    YouTubeOutcome,
    YouTubePermanentError,
    YouTubeReconnectRequiredError,
    YouTubeStatus,
    YouTubeUnavailableError,
)
from integration.test_publications import _seed_publication

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 10, 5, 0, tzinfo=UTC)
RENDITION_DIGEST = bytes.fromhex("ab" * 32)


def _seed_youtube_publication(
    engine: Engine, *, suffix: str, scheduled_for: datetime | None = None
) -> dict[str, Any]:
    """Create one bound preflighting Publication using the shared durable graph."""
    seed = _seed_publication(engine, suffix=suffix)
    rendition_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO social_renditions
                    (id, workspace_id, render_artifact_id, source_sha256, provider,
                     profile_version, output_sha256, storage_key, size_bytes, duration_ms,
                     provenance, validation_report, reused_master, created_at)
                VALUES (:id, :workspace, :render, :source, 'youtube', '2026-09-09', :output,
                        'social/youtube.mp4', 600000, 1000, '{}', '{}', false, :now)
                """
            ),
            {
                "id": rendition_id,
                "workspace": seed["workspace"],
                "render": seed["render"],
                "source": seed["digest"],
                "output": RENDITION_DIGEST,
                "now": NOW,
            },
        )
        connection.execute(
            text(
                """
                UPDATE publications
                SET status = 'preflighting', social_rendition_id = :rendition,
                    approved_by_user_id = :user, approved_at = :now, attempt_count = 0,
                    scheduled_for = :scheduled_for
                WHERE id = :publication
                """
            ),
            {
                **seed,
                "rendition": rendition_id,
                "now": NOW,
                "scheduled_for": scheduled_for,
            },
        )
    return {**seed, "rendition": rendition_id}


def _checkpoint(*, acknowledged_bytes: int) -> UploadCheckpoint:
    """Build safe progress tied to the immutable provider rendition checksum."""
    return UploadCheckpoint(
        secret_reference="vault://opaque/session-1",
        total_bytes=600_000,
        acknowledged_bytes=acknowledged_bytes,
        source_sha256=RENDITION_DIGEST,
        generation=1,
        final_request_ambiguous=False,
    )


def test_checkpoint_persists_only_an_opaque_reference_and_safe_progress(
    engine: Engine, clean_database: None
) -> None:
    """Postgres must never contain the provider's secret resumable-session URI."""
    seed = _seed_youtube_publication(engine, suffix="youtube-checkpoint")

    with Session(engine) as session, session.begin():
        YouTubePublicationCoordinator(session).save_upload_checkpoint(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            secret_reference="vault://opaque/session-1",
            checkpoint=_checkpoint(acknowledged_bytes=262_144),
            now=NOW,
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.status is PublicationStatus.TRANSFERRING
        assert publication.encrypted_checkpoint_reference == "vault://opaque/session-1"
        assert publication.checkpoint_metadata is not None
        assert publication.checkpoint_metadata["youtubeUpload"]["acknowledgedBytes"] == 262_144
        assert "session-secret" not in json.dumps(publication.checkpoint_metadata).lower()
        attempts = tuple(
            session.scalars(
                select(PublicationAttempt).where(
                    PublicationAttempt.publication_id == seed["publication"]
                )
            )
        )
        assert [attempt.stage for attempt in attempts] == ["youtube_chunk_262144"]
        assert attempts[0].byte_checkpoint == 262_144


def test_video_status_and_attachment_results_preserve_one_authoritative_video(
    engine: Engine, clean_database: None
) -> None:
    """Attachment retries must not erase, replace, or replay a published base video."""
    seed = _seed_youtube_publication(engine, suffix="youtube-video-state")
    with Session(engine) as session, session.begin():
        coordinator = YouTubePublicationCoordinator(session)
        coordinator.save_upload_checkpoint(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            secret_reference="vault://opaque/session-1",
            checkpoint=_checkpoint(acknowledged_bytes=600_000),
            now=NOW,
        )
        coordinator.record_video_created(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            video_id="video-1",
            provider_request_id="request-1",
            now=NOW,
        )
        coordinator.apply_status(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            status=YouTubeStatus(
                video_id="video-1",
                outcome=YouTubeOutcome.SUCCEEDED,
                privacy=YouTubePrivacy.PUBLIC,
                scheduled_for=None,
                failure_code=None,
            ),
            poll_sequence=1,
            now=NOW,
        )
        coordinator.record_attachment(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            kind="caption",
            result=AttachmentResult(
                video_id="video-1",
                state=AttachmentState.RETRYABLE_FAILED,
            ),
            attachment_attempt=1,
            now=NOW,
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.provider_publication_id == "video-1"
        assert publication.provider_permalink == "https://www.youtube.com/watch?v=video-1"
        assert publication.status is PublicationStatus.PUBLISHED
        assert publication.checkpoint_metadata is not None
        assert publication.checkpoint_metadata["youtubeCaption"] == {
            "state": "retryable_failed",
            "resourceId": None,
        }


@pytest.mark.parametrize(
    ("error", "expected_status"),
    (
        (
            YouTubeUnavailableError("YouTube publishing is temporarily unavailable"),
            PublicationStatus.RETRYABLE_FAILED,
        ),
        (
            YouTubeReconnectRequiredError("YouTube authorization must be reconnected"),
            PublicationStatus.RECONNECT_REQUIRED,
        ),
        (
            YouTubePermanentError("YouTube rejected the publishing operation"),
            PublicationStatus.PERMANENT_FAILED,
        ),
    ),
)
def test_provider_error_mapping_is_sanitized_and_durable(
    engine: Engine,
    clean_database: None,
    error: Exception,
    expected_status: PublicationStatus,
) -> None:
    """Provider failures must enter the truthful local state without raw response evidence."""
    seed = _seed_youtube_publication(engine, suffix=f"youtube-error-{expected_status.value}")
    with Session(engine) as session, session.begin():
        coordinator = YouTubePublicationCoordinator(session)
        coordinator.save_upload_checkpoint(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            secret_reference="vault://opaque/session-1",
            checkpoint=_checkpoint(acknowledged_bytes=262_144),
            now=NOW,
        )
        coordinator.apply_error(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            stage="youtube_transfer",
            error=error,
            now=NOW,
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.status is expected_status
        assert publication.normalized_error_code
        assert "secret" not in (publication.sanitized_error_message or "")


def test_processed_native_schedule_stays_scheduled_until_provider_visibility_changes(
    engine: Engine, clean_database: None
) -> None:
    """Successful processing must not claim publication before YouTube's native publishAt."""
    scheduled_for = NOW + timedelta(days=1)
    seed = _seed_youtube_publication(
        engine,
        suffix="youtube-native-schedule",
        scheduled_for=scheduled_for,
    )
    with Session(engine) as session, session.begin():
        coordinator = YouTubePublicationCoordinator(session)
        coordinator.save_upload_checkpoint(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            secret_reference="vault://opaque/session-1",
            checkpoint=_checkpoint(acknowledged_bytes=600_000),
            now=NOW,
        )
        coordinator.record_video_created(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            video_id="video-scheduled",
            provider_request_id=None,
            now=NOW,
        )
        coordinator.apply_status(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            status=YouTubeStatus(
                video_id="video-scheduled",
                outcome=YouTubeOutcome.SUCCEEDED,
                privacy=YouTubePrivacy.PRIVATE,
                scheduled_for=scheduled_for,
                failure_code=None,
            ),
            poll_sequence=1,
            now=NOW,
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.status is PublicationStatus.SCHEDULED
        assert publication.published_at is None

    published_at = scheduled_for + timedelta(seconds=1)
    with Session(engine) as session, session.begin():
        YouTubePublicationCoordinator(session).apply_status(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            status=YouTubeStatus(
                video_id="video-scheduled",
                outcome=YouTubeOutcome.SUCCEEDED,
                privacy=YouTubePrivacy.PUBLIC,
                scheduled_for=scheduled_for,
                failure_code=None,
            ),
            poll_sequence=2,
            now=published_at,
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.status is PublicationStatus.PUBLISHED
        assert publication.published_at == published_at
