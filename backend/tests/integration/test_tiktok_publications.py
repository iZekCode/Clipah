"""Real-Postgres contracts for TikTok draft fallback and audited Direct Post."""

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
from clipah.publishing.providers.tiktok.transfers import (
    DeliveryMode,
    PublishState,
    TikTokPermanentError,
    TikTokPublicationCoordinator,
    TikTokPublicationInvalidError,
    TikTokPublishStatus,
    TikTokRateLimitedError,
    TikTokReconnectRequiredError,
    TransferCheckpoint,
)
from clipah.publishing.use_cases import (
    PublicationInvalidError,
    confirm_publication_draft,
    preflight_publication_draft,
    tiktok_policy_evidence,
)
from clipah.workspaces.models import (
    PublishingRolePolicy,
    WorkspaceAccess,
    WorkspaceRole,
)
from integration.test_publications import _seed_publication

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 10, 5, 0, tzinfo=UTC)
RENDITION_DIGEST = bytes.fromhex("ab" * 32)
PULL_URL = "https://media.clipah.test/social-renditions/abc/tiktok.mp4?X-Amz-Signature=deadbeef"


def _seed_tiktok_publication(
    engine: Engine,
    *,
    suffix: str,
    provider: str = "tiktok",
    status: str = "preflighting",
) -> dict[str, Any]:
    """Create one bound TikTok Publication on the shared durable graph."""
    seed = _seed_publication(engine, suffix=suffix)
    rendition_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE social_accounts
                SET provider = :provider, api_version = 'v2'
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
                        'social/tiktok.mp4', 600000, 5000, '{}', '{}', false, :now)
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
                SET status = :status, social_rendition_id = :rendition,
                    approved_by_user_id = :approver, approved_at = :approved_at,
                    attempt_count = 0,
                    provider_options = '{"creatorUsername": "clipah"}',
                    consent_snapshot = '{"confirmed": true}'
                WHERE id = :publication
                """
            ),
            {
                **seed,
                "rendition": rendition_id,
                "status": status,
                "approver": None if status == "draft" else seed["user"],
                "approved_at": None if status == "draft" else NOW,
            },
        )
    return {**seed, "rendition": rendition_id}


def _submitted(
    coordinator: TikTokPublicationCoordinator,
    seed: dict[str, Any],
    *,
    mode: DeliveryMode = DeliveryMode.DRAFT_INBOX,
) -> None:
    """Move one seeded Publication to a durable submitted-delivery checkpoint."""
    coordinator.save_transfer(
        workspace_id=seed["workspace"],
        publication_id=seed["publication"],
        checkpoint=TransferCheckpoint(publish_id="publish-1", mode=mode, created_at=NOW),
        now=NOW,
    )


def _status(
    state: PublishState,
    *,
    failure_code: str | None = None,
    public_post_id: str | None = None,
) -> TikTokPublishStatus:
    """Build one authoritative publish status for the seeded delivery."""
    return TikTokPublishStatus(
        publish_id="publish-1",
        state=state,
        failure_code=failure_code,
        public_post_id=public_post_id,
    )


def test_a_submitted_delivery_persists_only_safe_progress(
    engine: Engine, clean_database: None
) -> None:
    """Postgres must never contain the capability TikTok pulled the rendition from."""
    seed = _seed_tiktok_publication(engine, suffix="tiktok-submit")

    with Session(engine) as session, session.begin():
        _submitted(TikTokPublicationCoordinator(session), seed)

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.status is PublicationStatus.TRANSFERRING
        assert publication.provider_publication_id == "publish-1"
        assert publication.checkpoint_metadata is not None
        transfer = publication.checkpoint_metadata["tiktokTransfer"]
        assert transfer["publishId"] == "publish-1"
        assert transfer["mode"] == "draft_inbox"
        assert PULL_URL not in json.dumps(publication.checkpoint_metadata)
        stages = [
            attempt.stage
            for attempt in session.scalars(
                select(PublicationAttempt).where(
                    PublicationAttempt.publication_id == seed["publication"]
                )
            )
        ]
        assert stages == ["tiktok_draft_inbox_submitted"]


def test_an_unacknowledged_submission_is_visible_before_another_is_attempted(
    engine: Engine, clean_database: None
) -> None:
    """A lost init response must leave durable evidence rather than silent retry."""
    seed = _seed_tiktok_publication(engine, suffix="tiktok-ambiguous")

    with Session(engine) as session, session.begin():
        TikTokPublicationCoordinator(session).save_transfer(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            checkpoint=TransferCheckpoint(mode=DeliveryMode.DIRECT_POST, init_ambiguous=True),
            now=NOW,
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.provider_publication_id is None
        assert publication.checkpoint_metadata is not None
        assert publication.checkpoint_metadata["tiktokTransfer"]["initAmbiguous"] is True


def test_a_draft_delivery_stops_at_processing_and_states_the_remaining_app_action(
    engine: Engine, clean_database: None
) -> None:
    """Clipah must not claim a draft is published while the member must finish in-app."""
    seed = _seed_tiktok_publication(engine, suffix="tiktok-draft")

    with Session(engine) as session, session.begin():
        coordinator = TikTokPublicationCoordinator(session)
        _submitted(coordinator, seed)
        coordinator.apply_status(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            status=_status(PublishState.DELIVERED_TO_INBOX),
            poll_sequence=1,
            now=NOW,
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.status is PublicationStatus.PROCESSING
        assert publication.published_at is None
        assert "TikTok app" in (publication.sanitized_error_message or "")


def test_an_audited_direct_post_reaches_published_with_a_tiktok_permalink(
    engine: Engine, clean_database: None
) -> None:
    """A completed Direct Post must record the public post TikTok made available."""
    seed = _seed_tiktok_publication(engine, suffix="tiktok-direct")

    with Session(engine) as session, session.begin():
        coordinator = TikTokPublicationCoordinator(session)
        _submitted(coordinator, seed, mode=DeliveryMode.DIRECT_POST)
        coordinator.apply_status(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            status=_status(PublishState.PUBLISHED, public_post_id="7000000000"),
            poll_sequence=1,
            now=NOW,
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.status is PublicationStatus.PUBLISHED
        assert publication.published_at == NOW
        assert publication.provider_permalink == ("https://www.tiktok.com/@clipah/video/7000000000")
        assert publication.sanitized_error_message is None


def test_late_duplicated_or_out_of_order_status_never_regresses_a_publication(
    engine: Engine, clean_database: None
) -> None:
    """Reconciliation must be monotonic whether events arrive twice, late, or reordered."""
    seed = _seed_tiktok_publication(engine, suffix="tiktok-ordering")

    with Session(engine) as session, session.begin():
        coordinator = TikTokPublicationCoordinator(session)
        _submitted(coordinator, seed, mode=DeliveryMode.DIRECT_POST)
        coordinator.apply_status(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            status=_status(PublishState.PUBLISHED, public_post_id="7000000000"),
            poll_sequence=3,
            now=NOW,
        )
        coordinator.apply_status(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            status=_status(PublishState.PUBLISHED, public_post_id="7000000000"),
            poll_sequence=3,
            now=NOW + timedelta(minutes=1),
        )
        coordinator.apply_status(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            status=_status(PublishState.PROCESSING),
            poll_sequence=2,
            now=NOW + timedelta(minutes=2),
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.status is PublicationStatus.PUBLISHED
        assert publication.published_at == NOW
        assert publication.checkpoint_metadata is not None
        assert publication.checkpoint_metadata["tiktokTransfer"]["lastPollSequence"] == 3


def test_a_failed_publish_reaches_a_terminal_state_with_a_bounded_reason(
    engine: Engine, clean_database: None
) -> None:
    """A refused upload must not leave a Publication transferring forever."""
    seed = _seed_tiktok_publication(engine, suffix="tiktok-failed")

    with Session(engine) as session, session.begin():
        coordinator = TikTokPublicationCoordinator(session)
        _submitted(coordinator, seed)
        coordinator.apply_status(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            status=_status(PublishState.FAILED, failure_code="duration_check_failed"),
            poll_sequence=1,
            now=NOW,
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.status is PublicationStatus.PERMANENT_FAILED
        assert publication.normalized_error_code == "duration_check_failed"


@pytest.mark.parametrize(
    ("error", "expected_status"),
    (
        (
            TikTokRateLimitedError("TikTok publishing is temporarily limited"),
            PublicationStatus.RETRYABLE_FAILED,
        ),
        (
            TikTokReconnectRequiredError("TikTok authorization must be reconnected"),
            PublicationStatus.RECONNECT_REQUIRED,
        ),
        (
            TikTokPermanentError("TikTok rejected the publishing operation"),
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
    seed = _seed_tiktok_publication(engine, suffix=f"tiktok-error-{expected_status.value}")

    with Session(engine) as session, session.begin():
        coordinator = TikTokPublicationCoordinator(session)
        _submitted(coordinator, seed)
        coordinator.apply_error(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            stage="tiktok_submit",
            error=error,
            now=NOW,
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.status is expected_status
        assert publication.normalized_error_code
        assert "log_id" not in (publication.sanitized_error_message or "")


def test_status_naming_another_delivery_cannot_advance_a_publication(
    engine: Engine, clean_database: None
) -> None:
    """Evidence about a different delivery must never move this destination's truth."""
    seed = _seed_tiktok_publication(engine, suffix="tiktok-other-publish")

    with Session(engine) as session, session.begin():
        coordinator = TikTokPublicationCoordinator(session)
        _submitted(coordinator, seed)
        with pytest.raises(TikTokPublicationInvalidError):
            coordinator.apply_status(
                workspace_id=seed["workspace"],
                publication_id=seed["publication"],
                status=TikTokPublishStatus(
                    publish_id="publish-other",
                    state=PublishState.PUBLISHED,
                    failure_code=None,
                    public_post_id=None,
                ),
                poll_sequence=1,
                now=NOW,
            )


def test_a_publication_bound_to_another_provider_is_refused(
    engine: Engine, clean_database: None
) -> None:
    """The TikTok coordinator must not be able to write another destination's state."""
    seed = _seed_tiktok_publication(engine, suffix="tiktok-wrong-provider", provider="youtube")

    with (
        Session(engine) as session,
        session.begin(),
        pytest.raises(TikTokPublicationInvalidError),
    ):
        _submitted(TikTokPublicationCoordinator(session), seed)


def test_another_workspace_cannot_reach_this_publication(
    engine: Engine, clean_database: None
) -> None:
    """A guessed Publication identifier must be indistinguishable from a missing one."""
    first = _seed_tiktok_publication(engine, suffix="tiktok-tenant-a")
    second = _seed_tiktok_publication(engine, suffix="tiktok-tenant-b")

    with (
        Session(engine) as session,
        session.begin(),
        pytest.raises(TikTokPublicationInvalidError),
    ):
        TikTokPublicationCoordinator(session).save_transfer(
            workspace_id=second["workspace"],
            publication_id=first["publication"],
            checkpoint=TransferCheckpoint(
                publish_id="publish-1", mode=DeliveryMode.DRAFT_INBOX, created_at=NOW
            ),
            now=NOW,
        )


# --- The audit flip -----------------------------------------------------------------


def _access(seed: dict[str, Any]) -> WorkspaceAccess:
    """Build the approving actor's Workspace access for the publication use cases."""
    return WorkspaceAccess(
        workspace_id=seed["workspace"],
        user_id=seed["user"],
        role=WorkspaceRole.OWNER,
        publishing_role_policy=PublishingRolePolicy.OWNER_ADMIN_EDITOR,
    )


def test_preflight_freezes_the_current_tiktok_delivery_mode(
    engine: Engine, clean_database: None
) -> None:
    """An unaudited deployment must record the draft fallback it will actually use."""
    seed = _seed_tiktok_publication(engine, suffix="tiktok-preflight", status="draft")

    with Session(engine) as session, session.begin():
        preflight_publication_draft(
            session,
            access=_access(seed),
            batch_id=seed["batch"],
            now=NOW,
            tiktok_direct_post_approved=False,
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.checkpoint_metadata is not None
        evidence = publication.checkpoint_metadata["tiktokPolicy"]
        assert evidence["effectiveMode"] == "draft_inbox"
        assert evidence["restriction"] == "tiktok_direct_post_audit_required"


def test_approval_granted_between_preflight_and_confirm_returns_to_review(
    engine: Engine, clean_database: None
) -> None:
    """A destination that becomes Direct Post must be re-approved, never flipped silently."""
    seed = _seed_tiktok_publication(engine, suffix="tiktok-audit-flip", status="draft")

    with Session(engine) as session, session.begin():
        preflight_publication_draft(
            session,
            access=_access(seed),
            batch_id=seed["batch"],
            now=NOW,
            tiktok_direct_post_approved=False,
        )

    with Session(engine) as session, session.begin(), pytest.raises(PublicationInvalidError):
        confirm_publication_draft(
            session,
            access=_access(seed),
            batch_id=seed["batch"],
            now=NOW,
            tiktok_direct_post_approved=True,
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.status is PublicationStatus.AWAITING_APPROVAL


def test_an_unchanged_audit_state_confirms_without_a_second_approval(
    engine: Engine, clean_database: None
) -> None:
    """Approval evidence must survive when the destination behaviour did not change."""
    seed = _seed_tiktok_publication(engine, suffix="tiktok-audit-stable", status="draft")

    with Session(engine) as session, session.begin():
        preflight_publication_draft(
            session,
            access=_access(seed),
            batch_id=seed["batch"],
            now=NOW,
            tiktok_direct_post_approved=True,
        )
        confirm_publication_draft(
            session,
            access=_access(seed),
            batch_id=seed["batch"],
            now=NOW,
            tiktok_direct_post_approved=True,
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.status is PublicationStatus.PREFLIGHTING
        assert publication.checkpoint_metadata is not None
        assert publication.checkpoint_metadata["tiktokPolicy"]["effectiveMode"] == "direct_post"


def test_policy_evidence_is_reproducible_from_the_audit_flag_alone(
    engine: Engine, clean_database: None
) -> None:
    """The frozen evidence must depend on nothing but the deployment's audit state."""
    seed = _seed_tiktok_publication(engine, suffix="tiktok-evidence")

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert tiktok_policy_evidence(publication=publication, now=NOW, audit_approved=True) == {
            "requestedMode": "direct_post",
            "effectiveMode": "direct_post",
            "restriction": None,
            "remainingUserAction": None,
        }
