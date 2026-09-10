"""Real-Postgres contracts for one safely reconciled Instagram Reels Publication."""

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
from clipah.publishing.providers.instagram.containers import (
    ContainerCheckpoint,
    ContainerState,
    InstagramContainerStatus,
    InstagramPermanentError,
    InstagramPublicationCoordinator,
    InstagramPublicationInvalidError,
    InstagramReconnectRequiredError,
    InstagramUnavailableError,
)
from integration.test_publications import _seed_publication

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 10, 5, 0, tzinfo=UTC)
RENDITION_DIGEST = bytes.fromhex("ab" * 32)
PULL_URL = "https://media.clipah.test/social/instagram.mp4?X-Amz-Signature=deadbeef"


def _seed_instagram_publication(
    engine: Engine, *, suffix: str, provider: str = "instagram"
) -> dict[str, Any]:
    """Create one bound preflighting Instagram Publication on the shared durable graph."""
    seed = _seed_publication(engine, suffix=suffix)
    rendition_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE social_accounts
                SET provider = :provider, api_version = 'v22.0'
                WHERE id = :account
                """
            ),
            {"account": seed["account"], "provider": provider},
        )
        connection.execute(
            text(
                """
                INSERT INTO social_renditions
                    (id, workspace_id, render_artifact_id, source_sha256, provider,
                     profile_version, output_sha256, storage_key, size_bytes, duration_ms,
                     provenance, validation_report, reused_master, created_at)
                VALUES (:id, :workspace, :render, :source, :provider, '2026-09-09', :output,
                        'social/instagram.mp4', 600000, 5000, '{}', '{}', false, :now)
                """
            ),
            {
                "id": rendition_id,
                "workspace": seed["workspace"],
                "render": seed["render"],
                "source": seed["digest"],
                "output": RENDITION_DIGEST,
                "provider": provider,
                "now": NOW,
            },
        )
        connection.execute(
            text(
                """
                UPDATE publications
                SET status = 'preflighting', social_rendition_id = :rendition,
                    approved_by_user_id = :user, approved_at = :now, attempt_count = 0
                WHERE id = :publication
                """
            ),
            {**seed, "rendition": rendition_id, "now": NOW},
        )
    return {**seed, "rendition": rendition_id}


def _created(coordinator: InstagramPublicationCoordinator, seed: dict[str, Any]) -> None:
    """Move one seeded Publication to a durable created-container checkpoint."""
    coordinator.save_container(
        workspace_id=seed["workspace"],
        publication_id=seed["publication"],
        checkpoint=ContainerCheckpoint(container_id="container-1", created_at=NOW),
        now=NOW,
    )


def _status(state: ContainerState, *, failure_code: str | None = None) -> InstagramContainerStatus:
    """Build one authoritative container status for the seeded container."""
    return InstagramContainerStatus(
        container_id="container-1", state=state, failure_code=failure_code
    )


def test_container_checkpoint_persists_only_safe_progress(
    engine: Engine, clean_database: None
) -> None:
    """Postgres must never contain the short-lived capability Instagram pulled from."""
    seed = _seed_instagram_publication(engine, suffix="instagram-container")

    with Session(engine) as session, session.begin():
        _created(InstagramPublicationCoordinator(session), seed)

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.status is PublicationStatus.TRANSFERRING
        assert publication.dispatched_at == NOW
        assert publication.checkpoint_metadata is not None
        container = publication.checkpoint_metadata["instagramContainer"]
        assert container["containerId"] == "container-1"
        assert container["creationAmbiguous"] is False
        assert PULL_URL not in json.dumps(publication.checkpoint_metadata)
        assert "x-amz-signature" not in json.dumps(publication.checkpoint_metadata).lower()
        attempts = tuple(
            session.scalars(
                select(PublicationAttempt).where(
                    PublicationAttempt.publication_id == seed["publication"]
                )
            )
        )
        assert [attempt.stage for attempt in attempts] == ["instagram_container_created"]


def test_an_unacknowledged_creation_is_visible_before_another_container_is_made(
    engine: Engine, clean_database: None
) -> None:
    """A lost creation response must leave durable evidence rather than silent retry."""
    seed = _seed_instagram_publication(engine, suffix="instagram-ambiguous")

    with Session(engine) as session, session.begin():
        InstagramPublicationCoordinator(session).save_container(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            checkpoint=ContainerCheckpoint(creation_ambiguous=True),
            now=NOW,
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.checkpoint_metadata is not None
        container = publication.checkpoint_metadata["instagramContainer"]
        assert container["containerId"] is None
        assert container["creationAmbiguous"] is True
        attempts = tuple(
            session.scalars(
                select(PublicationAttempt).where(
                    PublicationAttempt.publication_id == seed["publication"]
                )
            )
        )
        assert [attempt.stage for attempt in attempts] == ["instagram_container_ambiguous"]


def test_publishing_one_media_reaches_published_only_on_provider_confirmation(
    engine: Engine, clean_database: None
) -> None:
    """Clipah must not claim a Reel is live before Instagram reports it published."""
    seed = _seed_instagram_publication(engine, suffix="instagram-publish")

    with Session(engine) as session, session.begin():
        coordinator = InstagramPublicationCoordinator(session)
        _created(coordinator, seed)
        coordinator.record_media_published(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            media_id="media-1",
            permalink="https://www.instagram.com/reel/abc/",
            provider_request_id="request-1",
            now=NOW,
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.status is PublicationStatus.PROCESSING
        assert publication.provider_publication_id == "media-1"
        assert publication.provider_permalink == "https://www.instagram.com/reel/abc/"
        assert publication.published_at is None

    published_at = NOW + timedelta(minutes=1)
    with Session(engine) as session, session.begin():
        InstagramPublicationCoordinator(session).apply_status(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            status=_status(ContainerState.PUBLISHED),
            poll_sequence=1,
            now=published_at,
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.status is PublicationStatus.PUBLISHED
        assert publication.published_at == published_at


def test_a_permalink_from_another_host_is_never_persisted(
    engine: Engine, clean_database: None
) -> None:
    """A provider-controlled link must not become a Clipah-rendered redirect."""
    seed = _seed_instagram_publication(engine, suffix="instagram-permalink")

    with Session(engine) as session, session.begin():
        coordinator = InstagramPublicationCoordinator(session)
        _created(coordinator, seed)
        coordinator.record_media_published(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            media_id="media-1",
            permalink="https://evil.test/reel/abc/",
            provider_request_id=None,
            now=NOW,
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.provider_permalink is None


def test_a_repeated_publish_result_never_names_a_second_media(
    engine: Engine, clean_database: None
) -> None:
    """A redelivered publish must converge on the media the first attempt created."""
    seed = _seed_instagram_publication(engine, suffix="instagram-idempotent")

    with Session(engine) as session, session.begin():
        coordinator = InstagramPublicationCoordinator(session)
        _created(coordinator, seed)
        coordinator.record_media_published(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            media_id="media-1",
            permalink=None,
            provider_request_id=None,
            now=NOW,
        )
        coordinator.record_media_published(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            media_id="media-1",
            permalink=None,
            provider_request_id=None,
            now=NOW + timedelta(seconds=5),
        )
        with pytest.raises(InstagramPublicationInvalidError):
            coordinator.record_media_published(
                workspace_id=seed["workspace"],
                publication_id=seed["publication"],
                media_id="media-2",
                permalink=None,
                provider_request_id=None,
                now=NOW + timedelta(seconds=10),
            )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.provider_publication_id == "media-1"


def test_late_duplicated_or_out_of_order_status_never_regresses_a_publication(
    engine: Engine, clean_database: None
) -> None:
    """Reconciliation must be monotonic whether events arrive twice, late, or reordered."""
    seed = _seed_instagram_publication(engine, suffix="instagram-ordering")

    with Session(engine) as session, session.begin():
        coordinator = InstagramPublicationCoordinator(session)
        _created(coordinator, seed)
        coordinator.record_media_published(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            media_id="media-1",
            permalink=None,
            provider_request_id=None,
            now=NOW,
        )
        coordinator.apply_status(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            status=_status(ContainerState.PUBLISHED),
            poll_sequence=2,
            now=NOW + timedelta(minutes=1),
        )
        coordinator.apply_status(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            status=_status(ContainerState.PUBLISHED),
            poll_sequence=2,
            now=NOW + timedelta(minutes=2),
        )
        coordinator.apply_status(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            status=_status(ContainerState.IN_PROGRESS),
            poll_sequence=1,
            now=NOW + timedelta(minutes=3),
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.status is PublicationStatus.PUBLISHED
        assert publication.published_at == NOW + timedelta(minutes=1)
        assert publication.checkpoint_metadata is not None
        assert publication.checkpoint_metadata["instagramContainer"]["lastPollSequence"] == 2
        stages = sorted(
            attempt.stage
            for attempt in session.scalars(
                select(PublicationAttempt).where(
                    PublicationAttempt.publication_id == seed["publication"]
                )
            )
        )
        assert stages == [
            "instagram_container_created",
            "instagram_media_published",
            "instagram_status_2",
        ]


@pytest.mark.parametrize(
    ("state", "failure_code", "expected_status", "expected_code"),
    (
        (
            ContainerState.ERROR,
            "2207026",
            PublicationStatus.PERMANENT_FAILED,
            "2207026",
        ),
        (
            ContainerState.EXPIRED,
            None,
            PublicationStatus.RETRYABLE_FAILED,
            "instagram_container_expired",
        ),
    ),
)
def test_a_failed_or_expired_container_reaches_the_truthful_terminal_state(
    engine: Engine,
    clean_database: None,
    state: ContainerState,
    failure_code: str | None,
    expected_status: PublicationStatus,
    expected_code: str,
) -> None:
    """A container that cannot be published must not leave a Publication transferring."""
    seed = _seed_instagram_publication(engine, suffix=f"instagram-{state.value}")

    with Session(engine) as session, session.begin():
        coordinator = InstagramPublicationCoordinator(session)
        _created(coordinator, seed)
        coordinator.apply_status(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            status=_status(state, failure_code=failure_code),
            poll_sequence=1,
            now=NOW,
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.status is expected_status
        assert publication.normalized_error_code == expected_code
        assert "fbtrace" not in (publication.sanitized_error_message or "")


@pytest.mark.parametrize(
    ("error", "expected_status"),
    (
        (
            InstagramUnavailableError("Instagram publishing is temporarily unavailable"),
            PublicationStatus.RETRYABLE_FAILED,
        ),
        (
            InstagramReconnectRequiredError("Instagram authorization must be reconnected"),
            PublicationStatus.RECONNECT_REQUIRED,
        ),
        (
            InstagramPermanentError("Instagram rejected the publishing operation"),
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
    seed = _seed_instagram_publication(engine, suffix=f"instagram-error-{expected_status.value}")

    with Session(engine) as session, session.begin():
        coordinator = InstagramPublicationCoordinator(session)
        _created(coordinator, seed)
        coordinator.apply_error(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            stage="instagram_publish",
            error=error,
            now=NOW,
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.status is expected_status
        assert publication.normalized_error_code
        assert "secret" not in (publication.sanitized_error_message or "")


def test_status_naming_another_container_cannot_advance_a_publication(
    engine: Engine, clean_database: None
) -> None:
    """Evidence about a different container must never move this destination's truth."""
    seed = _seed_instagram_publication(engine, suffix="instagram-other-container")

    with Session(engine) as session, session.begin():
        coordinator = InstagramPublicationCoordinator(session)
        _created(coordinator, seed)
        with pytest.raises(InstagramPublicationInvalidError):
            coordinator.apply_status(
                workspace_id=seed["workspace"],
                publication_id=seed["publication"],
                status=InstagramContainerStatus(
                    container_id="container-other",
                    state=ContainerState.PUBLISHED,
                    failure_code=None,
                ),
                poll_sequence=1,
                now=NOW,
            )


def test_a_publication_bound_to_another_provider_is_refused(
    engine: Engine, clean_database: None
) -> None:
    """The Instagram coordinator must not be able to write YouTube destination state."""
    seed = _seed_instagram_publication(
        engine, suffix="instagram-wrong-provider", provider="youtube"
    )

    with (
        Session(engine) as session,
        session.begin(),
        pytest.raises(InstagramPublicationInvalidError),
    ):
        _created(InstagramPublicationCoordinator(session), seed)


def test_another_workspace_cannot_reach_this_publication(
    engine: Engine, clean_database: None
) -> None:
    """A guessed Publication identifier must be indistinguishable from a missing one."""
    first = _seed_instagram_publication(engine, suffix="instagram-tenant-a")
    second = _seed_instagram_publication(engine, suffix="instagram-tenant-b")

    with (
        Session(engine) as session,
        session.begin(),
        pytest.raises(InstagramPublicationInvalidError),
    ):
        InstagramPublicationCoordinator(session).save_container(
            workspace_id=second["workspace"],
            publication_id=first["publication"],
            checkpoint=ContainerCheckpoint(container_id="container-1", created_at=NOW),
            now=NOW,
        )
