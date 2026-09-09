"""Real-Postgres contracts for durable Publication orchestration."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from clipah.models import (
    ProviderEvent,
    Publication,
    PublicationAttempt,
    PublicationOutbox,
    PublishingRolePolicy,
    SocialAccount,
    WorkspaceRole,
)
from clipah.publishing.models import PublicationDestinationDraft, PublicationStatus
from clipah.publishing.outbox import PublicationOutboxService
from clipah.publishing.profiles import profile_for
from clipah.publishing.repository import PublicationRepository
from clipah.publishing.scheduler import PublicationScheduler
from clipah.publishing.state_machine import PublicationCancellationRejectedError
from clipah.publishing.tasks import (
    PublicationDispatchInvalidError,
    bind_publication_rendition,
    revalidate_publication_dispatch,
)
from clipah.publishing.use_cases import (
    PublicationFutureWorkCoordinator,
    PublicationIdempotencyConflictError,
    PublicationInvalidError,
    PublicationRetryBlockedError,
    cancel_publication,
    confirm_publication_draft,
    preflight_publication_draft,
    prepare_publication_draft,
    retry_publication,
)
from clipah.social_accounts.models import SocialProvider
from clipah.workspaces.models import WorkspaceAccess
from harness import Browser, Clock, StubGoogleProvider, build_app, sign_in
from support import provision_identity

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 9, 5, 0, tzinfo=UTC)


def _seed_publication(
    engine: Engine,
    *,
    suffix: str,
    user_id: UUID | None = None,
    workspace_id: UUID | None = None,
    watermark_text: str | None = None,
    render_duration_ms: int = 1_000,
) -> dict[str, Any]:
    """Create the smallest real graph ending in one draft Publication."""
    if user_id is None or workspace_id is None:
        user_id, workspace_id = provision_identity(engine, suffix=suffix)
    ids = {
        name: uuid4()
        for name in (
            "project",
            "asset",
            "transcript",
            "candidate",
            "edit",
            "revision",
            "decision",
            "render",
            "account",
            "batch",
            "publication",
        )
    }
    digest = bytes.fromhex("42" * 32)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects
                    (id, workspace_id, created_by_user_id, name, status, source_kind)
                VALUES (:project, :workspace, :user, 'Publication', 'ready', 'upload')
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
                        'source.mp4', 'video/mp4', 100, 1000, :digest)
                """
            ),
            {**ids, "workspace": workspace_id, "digest": digest},
        )
        connection.execute(
            text(
                """
                INSERT INTO transcripts
                    (id, workspace_id, project_id, asset_id, provider, provider_version, model,
                     language, full_text, words, speaker_segments, utterances, duration_ms,
                     raw_result_storage_key)
                VALUES (:transcript, :workspace, :project, :asset, 'fake', '1', 'fake',
                        'en', 'hello', '[]', '[]', '[]', 1000, 'transcript.json')
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
                        'story', '{}', 0, 1000, 'hello', '{}', '{}', '[]', '{}')
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
            {**ids, "workspace": workspace_id, "user": user_id, "digest": digest},
        )
        connection.execute(
            text(
                """
                INSERT INTO edit_review_decisions
                    (id, workspace_id, clip_edit_id, clip_edit_revision_id, actor_user_id,
                     sequence, decision, created_at)
                VALUES (:decision, :workspace, :edit, :revision, :user, 1, 'approve', :now)
                """
            ),
            {**ids, "workspace": workspace_id, "user": user_id, "now": NOW},
        )
        connection.execute(
            text(
                """
                INSERT INTO render_artifacts
                    (id, workspace_id, clip_edit_revision_id, preset, composition_hash, sha256,
                     watermark_text, storage_key, size_bytes, duration_ms)
                VALUES (:render, :workspace, :revision, '1080x1920', :digest, :digest,
                        :watermark_text, 'render.mp4', 100, :render_duration_ms)
                """
            ),
            {
                **ids,
                "workspace": workspace_id,
                "digest": digest,
                "watermark_text": watermark_text,
                "render_duration_ms": render_duration_ms,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO social_accounts
                    (id, workspace_id, provider, external_account_id, display_name, login_family,
                     api_version, connection_status, capability_snapshot, authorized_by_user_id,
                     created_at)
                VALUES (:account, :workspace, 'youtube', :external, 'Channel', 'social_oauth',
                        'v3', 'active', '{"version":"cap-v1","values":{}}', :user, :now)
                """
            ),
            {
                **ids,
                "workspace": workspace_id,
                "user": user_id,
                "external": f"channel-{suffix}",
                "now": NOW,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO publication_batches
                    (id, workspace_id, edit_revision_id, render_artifact_id, created_by_user_id,
                     idempotency_key, request_fingerprint, created_at)
                VALUES (:batch, :workspace, :revision, :render, :user, :key, :digest, :now)
                """
            ),
            {
                **ids,
                "workspace": workspace_id,
                "user": user_id,
                "key": f"prepare-{suffix}",
                "digest": digest,
                "now": NOW,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO publications
                    (id, workspace_id, batch_id, social_account_id, edit_revision_id,
                     render_artifact_id, artifact_sha256, metadata_snapshot, provider_options,
                     consent_snapshot, display_timezone, status, idempotency_key,
                     provider_operation_key, created_at)
                VALUES (:publication, :workspace, :batch, :account, :revision, :render, :digest,
                        '{}', '{}', '{}', 'UTC', 'draft', :key, :operation, :now)
                """
            ),
            {
                **ids,
                "workspace": workspace_id,
                "key": f"prepare-{suffix}",
                "operation": f"publication:{ids['publication']}:1",
                "digest": digest,
                "now": NOW,
            },
        )
    return {**ids, "workspace": workspace_id, "user": user_id, "digest": digest}


def _access(seed: dict[str, Any]) -> WorkspaceAccess:
    """Return the owner standing provisioned by the seed graph."""
    return WorkspaceAccess(
        workspace_id=seed["workspace"],
        user_id=seed["user"],
        role=WorkspaceRole.OWNER,
        publishing_role_policy=PublishingRolePolicy.OWNER_ADMIN_EDITOR,
    )


def test_repository_hides_a_publication_from_another_workspace(
    engine: Engine, clean_database: None
) -> None:
    """Removing the Workspace predicate would turn a guessed UUID into tenant leakage."""
    first = _seed_publication(engine, suffix="repository-a")
    second = _seed_publication(engine, suffix="repository-b")

    with Session(engine) as session:
        hidden = PublicationRepository(session).publication(
            workspace_id=UUID(str(second["workspace"])),
            publication_id=UUID(str(first["publication"])),
        )

    assert hidden is None


def test_provider_events_deduplicate_by_provider_id_or_stable_hash(
    engine: Engine, clean_database: None
) -> None:
    """A repeated webhook or poll result must not apply provider truth twice."""
    seed = _seed_publication(engine, suffix="event-dedup")
    with Session(engine) as session, session.begin():
        repository = PublicationRepository(session)
        first = repository.record_provider_event(
            workspace_id=seed["workspace"],
            social_account_id=seed["account"],
            publication_id=seed["publication"],
            provider_event_id="provider-event-1",
            payload=b'{"status":"published"}',
            event_type="status",
            signature_valid=True,
            normalized_status="published",
            received_at=NOW,
        )
        replay = repository.record_provider_event(
            workspace_id=seed["workspace"],
            social_account_id=seed["account"],
            publication_id=seed["publication"],
            provider_event_id="provider-event-1",
            payload=b'{"status":"changed-but-same-id"}',
            event_type="status",
            signature_valid=True,
            normalized_status="published",
            received_at=NOW,
        )
        hash_first = repository.record_provider_event(
            workspace_id=seed["workspace"],
            social_account_id=seed["account"],
            publication_id=seed["publication"],
            provider_event_id=None,
            payload=b'{"status":"processing"}',
            event_type="poll",
            signature_valid=True,
            normalized_status="processing",
            received_at=NOW,
        )
        hash_replay = repository.record_provider_event(
            workspace_id=seed["workspace"],
            social_account_id=seed["account"],
            publication_id=seed["publication"],
            provider_event_id=None,
            payload=b'{"status":"processing"}',
            event_type="poll",
            signature_valid=True,
            normalized_status="processing",
            received_at=NOW,
        )
        first_id = first.id
        replay_id = replay.id
        hash_first_id = hash_first.id
        hash_replay_id = hash_replay.id

    assert replay_id == first_id
    assert hash_replay_id == hash_first_id


def test_unacknowledged_outbox_message_survives_delivery_restart(
    engine: Engine, clean_database: None
) -> None:
    """A worker crash after reading dispatch intent must leave that work recoverable."""
    seed = _seed_publication(engine, suffix="outbox-restart")
    with Session(engine) as session, session.begin():
        service = PublicationOutboxService(session)
        created = service.enqueue(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            topic="publication.preflight",
            operation_key="preflight:1",
            available_at=NOW,
        )
        created_id = created.id

    with Session(engine) as session, session.begin():
        first_read = tuple(
            message.id for message in PublicationOutboxService(session).pending(now=NOW, limit=10)
        )
    with Session(engine) as session, session.begin():
        second_read = tuple(
            message.id for message in PublicationOutboxService(session).pending(now=NOW, limit=10)
        )
        PublicationOutboxService(session).acknowledge(message_id=created_id, delivered_at=NOW)
    with Session(engine) as session, session.begin():
        after_ack = PublicationOutboxService(session).pending(now=NOW, limit=10)

    assert first_read == (created_id,)
    assert second_read == (created_id,)
    assert after_ack == ()


def test_publication_attempt_history_is_append_only(engine: Engine, clean_database: None) -> None:
    """Provider execution evidence must not be editable after it is observed."""
    seed = _seed_publication(engine, suffix="attempt-history")
    with Session(engine) as session, session.begin():
        attempt = PublicationRepository(session).append_attempt(
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            attempt=1,
            stage="preflight",
            started_at=NOW,
        )
        attempt_id = attempt.id

    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("UPDATE publication_attempts SET stage = 'transfer' WHERE id = :id"),
            {"id": attempt_id},
        )

    with Session(engine) as session:
        assert (
            session.scalar(
                select(PublicationAttempt.stage).where(PublicationAttempt.id == attempt_id)
            )
            == "preflight"
        )


def test_publication_batch_is_immutable(engine: Engine, clean_database: None) -> None:
    """The convenience grouping must never be rewritten after preparation."""
    seed = _seed_publication(engine, suffix="immutable-batch")

    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("UPDATE publication_batches SET idempotency_key = 'changed' WHERE id = :id"),
            {"id": seed["batch"]},
        )


def test_confirmation_freezes_every_approval_input_and_normalizes_schedule_to_utc(
    engine: Engine, clean_database: None
) -> None:
    """Later Edit or Social Account changes must not rewrite what the User approved."""
    seed = _seed_publication(engine, suffix="immutable-confirmation")
    scheduled_local = datetime(2026, 9, 10, 9, 30, tzinfo=ZoneInfo("Asia/Jakarta"))
    destination = PublicationDestinationDraft(
        social_account_id=seed["account"],
        metadata={"title": "Exact title", "caption": "Exact caption"},
        provider_options={"privacy": "private"},
        consent={"confirmed": True, "confirmedAt": NOW.isoformat()},
        scheduled_for=scheduled_local,
        display_timezone="Asia/Jakarta",
    )

    with Session(engine) as session, session.begin():
        draft = prepare_publication_draft(
            session,
            access=_access(seed),
            edit_id=seed["edit"],
            revision=1,
            render_artifact_id=seed["render"],
            destinations=(destination,),
            idempotency_key="confirmation-snapshot",
            now=NOW,
        )
        preflight_publication_draft(session, access=_access(seed), batch_id=draft.batch_id, now=NOW)
        confirmed = confirm_publication_draft(
            session,
            access=_access(seed),
            batch_id=draft.batch_id,
            now=NOW,
        )
        publication_id = confirmed.publications[0].publication_id

    with Session(engine) as session, session.begin():
        account = session.get(SocialAccount, seed["account"])
        assert account is not None
        account.capability_snapshot = {"version": "cap-v2", "values": {"privacy": ["public"]}}

    with Session(engine) as session:
        publication = session.get(Publication, publication_id)
        assert publication is not None
        assert publication.edit_revision_id == seed["revision"]
        assert publication.render_artifact_id == seed["render"]
        assert bytes(publication.artifact_sha256) == seed["digest"]
        assert publication.metadata_snapshot == {
            "title": "Exact title",
            "caption": "Exact caption",
        }
        assert publication.provider_options == {"privacy": "private"}
        assert publication.consent_snapshot == {
            "confirmed": True,
            "confirmedAt": NOW.isoformat(),
        }
        assert publication.approved_by_user_id == seed["user"]
        assert publication.capability_version == "cap-v1"
        assert publication.provider_policy_version == "youtube:v3"
        assert publication.checkpoint_metadata is not None
        assert publication.checkpoint_metadata["preflight"] == {
            "passed": True,
            "profileVersion": "2026-09-09",
            "provider": "youtube",
            "violations": [],
        }
        assert publication.checkpoint_metadata["preflightCapabilityVersion"] == "cap-v1"
        assert publication.display_timezone == "Asia/Jakarta"
        assert publication.scheduled_for == scheduled_local.astimezone(UTC)
        assert publication.status is PublicationStatus.SCHEDULED

    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("UPDATE publications SET metadata_snapshot = '{}' WHERE id = :id"),
            {"id": publication_id},
        )


def test_tiktok_preflight_records_promotional_watermark_remediation_and_blocks_confirmation(
    engine: Engine, clean_database: None
) -> None:
    """TikTok-bound media must be re-rendered cleanly rather than altered automatically."""
    seed = _seed_publication(engine, suffix="tiktok-watermark", watermark_text="Clipah")
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE social_accounts SET provider = 'tiktok', api_version = 'v2' WHERE id = :id"
            ),
            {"id": seed["account"]},
        )
    destination = PublicationDestinationDraft(
        social_account_id=seed["account"],
        metadata={"title": "Creator post"},
        provider_options={},
        consent={"confirmed": True},
        scheduled_for=None,
        display_timezone="UTC",
    )
    with Session(engine) as session, session.begin():
        draft = prepare_publication_draft(
            session,
            access=_access(seed),
            edit_id=seed["edit"],
            revision=1,
            render_artifact_id=seed["render"],
            destinations=(destination,),
            idempotency_key="tiktok-watermark",
            now=NOW,
        )
        preflight_publication_draft(session, access=_access(seed), batch_id=draft.batch_id, now=NOW)
        publication_id = draft.publications[0].publication_id
        publication = session.get(Publication, publication_id)
        assert publication is not None
        assert publication.checkpoint_metadata is not None
        violations = publication.checkpoint_metadata["preflight"]["violations"]
        assert violations == [
            {
                "code": "promotional_watermark",
                "field": "watermarks",
                "message": "Promotional branding is not permitted for this destination.",
                "remediation": "Render a clean master without promotional branding.",
            }
        ]
        with pytest.raises(PublicationInvalidError, match="preflight"):
            confirm_publication_draft(
                session,
                access=_access(seed),
                batch_id=draft.batch_id,
                now=NOW,
            )


def test_confirmation_rejects_an_approval_withdrawn_after_prepare(
    engine: Engine, clean_database: None
) -> None:
    """A prepared draft cannot bypass a newer request-changes decision."""
    seed = _seed_publication(engine, suffix="withdrawn-approval")
    destination = PublicationDestinationDraft(
        social_account_id=seed["account"],
        metadata={"title": "No longer approved"},
        provider_options={"privacy": "private"},
        consent={"confirmed": True},
        scheduled_for=None,
        display_timezone="UTC",
    )
    with Session(engine) as session, session.begin():
        draft = prepare_publication_draft(
            session,
            access=_access(seed),
            edit_id=seed["edit"],
            revision=1,
            render_artifact_id=seed["render"],
            destinations=(destination,),
            idempotency_key="withdrawn-approval",
            now=NOW,
        )
        preflight_publication_draft(session, access=_access(seed), batch_id=draft.batch_id, now=NOW)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO edit_review_decisions
                    (id, workspace_id, clip_edit_id, clip_edit_revision_id, actor_user_id,
                     sequence, decision, created_at)
                VALUES (:id, :workspace, :edit, :revision, :user, 2, 'request_changes', :now)
                """
            ),
            {
                "id": uuid4(),
                "workspace": seed["workspace"],
                "edit": seed["edit"],
                "revision": seed["revision"],
                "user": seed["user"],
                "now": NOW + timedelta(seconds=1),
            },
        )

    with Session(engine) as session, session.begin(), pytest.raises(PublicationInvalidError):
        confirm_publication_draft(
            session,
            access=_access(seed),
            batch_id=draft.batch_id,
            now=NOW + timedelta(seconds=2),
        )


def test_confirm_replay_returns_the_frozen_result_after_editorial_state_changes(
    engine: Engine, clean_database: None
) -> None:
    """A lost confirmation response may replay without reinterpreting approved history."""
    seed = _seed_publication(engine, suffix="confirm-replay")
    destination = PublicationDestinationDraft(
        social_account_id=seed["account"],
        metadata={"title": "Frozen approval"},
        provider_options={"privacy": "private"},
        consent={"confirmed": True},
        scheduled_for=None,
        display_timezone="UTC",
    )
    with Session(engine) as session, session.begin():
        draft = prepare_publication_draft(
            session,
            access=_access(seed),
            edit_id=seed["edit"],
            revision=1,
            render_artifact_id=seed["render"],
            destinations=(destination,),
            idempotency_key="confirm-replay",
            now=NOW,
        )
        preflight_publication_draft(session, access=_access(seed), batch_id=draft.batch_id, now=NOW)
        first = confirm_publication_draft(
            session, access=_access(seed), batch_id=draft.batch_id, now=NOW
        )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO edit_review_decisions
                    (id, workspace_id, clip_edit_id, clip_edit_revision_id, actor_user_id,
                     sequence, decision, created_at)
                VALUES (:id, :workspace, :edit, :revision, :user, 2, 'request_changes', :now)
                """
            ),
            {
                "id": uuid4(),
                "workspace": seed["workspace"],
                "edit": seed["edit"],
                "revision": seed["revision"],
                "user": seed["user"],
                "now": NOW + timedelta(seconds=1),
            },
        )

    with Session(engine) as session, session.begin():
        replay = confirm_publication_draft(
            session,
            access=_access(seed),
            batch_id=draft.batch_id,
            now=NOW + timedelta(seconds=2),
        )

    assert replay == first


def test_prepare_replays_same_request_and_conflicts_when_payload_changes(
    engine: Engine, clean_database: None
) -> None:
    """An idempotency key must name exactly one immutable preparation request."""
    seed = _seed_publication(engine, suffix="prepare-replay")
    destination = PublicationDestinationDraft(
        social_account_id=seed["account"],
        metadata={"title": "First"},
        provider_options={"privacy": "private"},
        consent={},
        scheduled_for=None,
        display_timezone="UTC",
    )
    with Session(engine) as session, session.begin():
        first = prepare_publication_draft(
            session,
            access=_access(seed),
            edit_id=seed["edit"],
            revision=1,
            render_artifact_id=seed["render"],
            destinations=(destination,),
            idempotency_key="same-key",
            now=NOW,
        )
        replay = prepare_publication_draft(
            session,
            access=_access(seed),
            edit_id=seed["edit"],
            revision=1,
            render_artifact_id=seed["render"],
            destinations=(destination,),
            idempotency_key="same-key",
            now=NOW + timedelta(seconds=1),
        )
        changed = PublicationDestinationDraft(
            social_account_id=seed["account"],
            metadata={"title": "Changed"},
            provider_options={"privacy": "private"},
            consent={},
            scheduled_for=None,
            display_timezone="UTC",
        )
        with pytest.raises(PublicationIdempotencyConflictError):
            prepare_publication_draft(
                session,
                access=_access(seed),
                edit_id=seed["edit"],
                revision=1,
                render_artifact_id=seed["render"],
                destinations=(changed,),
                idempotency_key="same-key",
                now=NOW,
            )

    assert replay.batch_id == first.batch_id
    assert replay.publications[0].publication_id == first.publications[0].publication_id


def test_confirmation_enqueues_only_immediate_destination_without_touching_sibling(
    engine: Engine, clean_database: None
) -> None:
    """One scheduled destination must not block or duplicate an immediate sibling."""
    seed = _seed_publication(engine, suffix="independent-destinations", render_duration_ms=3_000)
    second_account_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO social_accounts
                    (id, workspace_id, provider, external_account_id, display_name, login_family,
                     api_version, connection_status, capability_snapshot, authorized_by_user_id,
                     created_at)
                VALUES (:id, :workspace, 'instagram', 'ig-second', 'Second', 'social_oauth',
                        'v22', 'active', '{"version":"ig-cap-v1","values":{}}', :user, :now)
                """
            ),
            {
                "id": second_account_id,
                "workspace": seed["workspace"],
                "user": seed["user"],
                "now": NOW,
            },
        )
    immediate = PublicationDestinationDraft(
        social_account_id=seed["account"],
        metadata={"title": "Now"},
        provider_options={"privacy": "private"},
        consent={"confirmed": True},
        scheduled_for=None,
        display_timezone="UTC",
    )
    scheduled = PublicationDestinationDraft(
        social_account_id=second_account_id,
        metadata={"caption": "Later"},
        provider_options={"privacy": "followers"},
        consent={"confirmed": True},
        scheduled_for=NOW + timedelta(hours=2),
        display_timezone="UTC",
    )

    with Session(engine) as session, session.begin():
        draft = prepare_publication_draft(
            session,
            access=_access(seed),
            edit_id=seed["edit"],
            revision=1,
            render_artifact_id=seed["render"],
            destinations=(immediate, scheduled),
            idempotency_key="two-destinations",
            now=NOW,
        )
        preflight_publication_draft(session, access=_access(seed), batch_id=draft.batch_id, now=NOW)
        confirm_publication_draft(session, access=_access(seed), batch_id=draft.batch_id, now=NOW)
        immediate_publication_id = next(
            item.publication_id
            for item in draft.publications
            if item.social_account_id == seed["account"]
        )

    with Session(engine) as session:
        states = dict(
            session.execute(
                select(Publication.social_account_id, Publication.status).where(
                    Publication.batch_id == draft.batch_id
                )
            ).all()
        )
        queued = session.scalars(
            select(PublicationOutbox).where(PublicationOutbox.workspace_id == seed["workspace"])
        ).all()

    assert states == {
        seed["account"]: PublicationStatus.PREFLIGHTING,
        second_account_id: PublicationStatus.SCHEDULED,
    }
    assert len(queued) == 1
    assert queued[0].publication_id == immediate_publication_id


def test_provider_event_table_itself_remains_immutable(
    engine: Engine, clean_database: None
) -> None:
    """Deduplication evidence cannot be rewritten after reconciliation sees it."""
    seed = _seed_publication(engine, suffix="event-history")
    with Session(engine) as session, session.begin():
        event = PublicationRepository(session).record_provider_event(
            workspace_id=seed["workspace"],
            social_account_id=seed["account"],
            publication_id=seed["publication"],
            provider_event_id="immutable-event",
            payload=b"{}",
            event_type="status",
            signature_valid=True,
            normalized_status=None,
            received_at=NOW,
        )
        event_id = event.id

    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("UPDATE provider_events SET normalized_status = 'published' WHERE id = :id"),
            {"id": event_id},
        )
    with Session(engine) as session:
        assert (
            session.scalar(
                select(ProviderEvent.normalized_status).where(ProviderEvent.id == event_id)
            )
            is None
        )


def _set_publication_state(
    engine: Engine,
    seed: dict[str, Any],
    *,
    status: PublicationStatus,
    scheduled_for: datetime | None = None,
) -> None:
    """Advance a seeded draft directly to a realistic approved fixture state."""
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE publications
                SET status = :status, approved_by_user_id = :user, approved_at = :now,
                    capability_version = 'cap-v1',
                    provider_policy_version = 'youtube:v3', scheduled_for = :scheduled_for
                WHERE id = :publication
                """
            ),
            {
                "status": status.value,
                "user": seed["user"],
                "now": NOW,
                "scheduled_for": scheduled_for,
                "publication": seed["publication"],
            },
        )


def test_scheduler_claims_due_work_once_in_bounded_batches_and_writes_outbox(
    engine: Engine, clean_database: None
) -> None:
    """Scheduler restarts and competing loops must not duplicate dispatch intent."""
    first = _seed_publication(engine, suffix="scheduler-first")
    second = _seed_publication(engine, suffix="scheduler-second")
    future = _seed_publication(engine, suffix="scheduler-future")
    _set_publication_state(engine, first, status=PublicationStatus.SCHEDULED, scheduled_for=NOW)
    _set_publication_state(engine, second, status=PublicationStatus.SCHEDULED, scheduled_for=NOW)
    _set_publication_state(
        engine,
        future,
        status=PublicationStatus.SCHEDULED,
        scheduled_for=NOW + timedelta(hours=1),
    )

    with Session(engine) as session, session.begin():
        first_claim = PublicationScheduler(session).claim_due(now=NOW, limit=1)
    with Session(engine) as session, session.begin():
        second_claim = PublicationScheduler(session).claim_due(now=NOW, limit=10)
    with Session(engine) as session, session.begin():
        restart_claim = PublicationScheduler(session).claim_due(now=NOW, limit=10)
        message_publication_ids = set(
            session.scalars(select(PublicationOutbox.publication_id)).all()
        )

    assert len(first_claim) == 1
    assert len(second_claim) == 1
    assert set(first_claim + second_claim) == {first["publication"], second["publication"]}
    assert restart_claim == []
    assert message_publication_ids == {first["publication"], second["publication"]}


def test_competing_schedulers_skip_locked_publication(engine: Engine, clean_database: None) -> None:
    """Two live schedulers must converge on one claim without waiting or duplicating it."""
    seed = _seed_publication(engine, suffix="scheduler-concurrency")
    _set_publication_state(engine, seed, status=PublicationStatus.SCHEDULED, scheduled_for=NOW)
    barrier = Barrier(2)

    def claim() -> list[UUID]:
        with Session(engine) as session, session.begin():
            barrier.wait()
            claimed = PublicationScheduler(session).claim_due(now=NOW, limit=1)
            barrier.wait()
            return claimed

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = [future.result() for future in (pool.submit(claim), pool.submit(claim))]

    assert sorted(len(items) for items in claims) == [0, 1]
    assert [item for items in claims for item in items] == [seed["publication"]]


def test_retry_requires_reconciliation_after_an_ambiguous_provider_timeout(
    engine: Engine, clean_database: None
) -> None:
    """A possibly successful provider create must reconcile before another create is queued."""
    seed = _seed_publication(engine, suffix="ambiguous-retry")
    _set_publication_state(engine, seed, status=PublicationStatus.RETRYABLE_FAILED)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE publications
                SET checkpoint_metadata = CAST(:checkpoint AS jsonb)
                WHERE id = :publication
                """
            ),
            {
                "publication": seed["publication"],
                "checkpoint": '{"ambiguous":true,"reconciled":false}',
            },
        )

    with Session(engine) as session, session.begin(), pytest.raises(PublicationRetryBlockedError):
        retry_publication(
            session,
            access=_access(seed),
            publication_id=seed["publication"],
            now=NOW,
        )

    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.status is PublicationStatus.RETRYABLE_FAILED


def test_cancellation_is_idempotent_before_transfer_and_truthful_after_transfer(
    engine: Engine, clean_database: None
) -> None:
    """A local success response must never contradict an irreversible provider operation."""
    cancellable = _seed_publication(engine, suffix="cancel-scheduled")
    late = _seed_publication(engine, suffix="cancel-late")
    _set_publication_state(
        engine, cancellable, status=PublicationStatus.SCHEDULED, scheduled_for=NOW
    )
    _set_publication_state(engine, late, status=PublicationStatus.TRANSFERRING)

    with Session(engine) as session, session.begin():
        first = cancel_publication(
            session,
            access=_access(cancellable),
            publication_id=cancellable["publication"],
            provider_cancellable=True,
            now=NOW,
        )
        replay = cancel_publication(
            session,
            access=_access(cancellable),
            publication_id=cancellable["publication"],
            provider_cancellable=True,
            now=NOW,
        )
    with (
        Session(engine) as session,
        session.begin(),
        pytest.raises(PublicationCancellationRejectedError),
    ):
        cancel_publication(
            session,
            access=_access(late),
            publication_id=late["publication"],
            provider_cancellable=False,
            now=NOW,
        )

    assert first.status is PublicationStatus.CANCELLED
    assert replay.status is PublicationStatus.CANCELLED


def test_disconnect_coordinator_affects_only_unpublished_work_for_one_account(
    engine: Engine, clean_database: None
) -> None:
    """Revoking one destination must not cancel a sibling or rewrite a published post."""
    scheduled = _seed_publication(engine, suffix="disconnect-scheduled")
    published = _seed_publication(engine, suffix="disconnect-published")
    sibling = _seed_publication(engine, suffix="disconnect-sibling")
    _set_publication_state(engine, scheduled, status=PublicationStatus.SCHEDULED, scheduled_for=NOW)
    _set_publication_state(engine, published, status=PublicationStatus.PUBLISHED)
    _set_publication_state(engine, sibling, status=PublicationStatus.SCHEDULED, scheduled_for=NOW)

    with Session(engine) as session, session.begin():
        PublicationFutureWorkCoordinator(session).cancel_or_pause_unpublished(
            workspace_id=scheduled["workspace"],
            social_account_id=scheduled["account"],
            now=NOW,
        )

    with Session(engine) as session:
        scheduled_row = session.get(Publication, scheduled["publication"])
        published_row = session.get(Publication, published["publication"])
        sibling_row = session.get(Publication, sibling["publication"])
        assert scheduled_row is not None
        assert published_row is not None
        assert sibling_row is not None
        assert scheduled_row.status is PublicationStatus.CANCELLED
        assert published_row.status is PublicationStatus.PUBLISHED
        assert sibling_row.status is PublicationStatus.SCHEDULED


def test_dispatch_revalidation_cancels_when_approver_loses_publish_authority(
    engine: Engine, clean_database: None
) -> None:
    """A queued operation must not outlive the approving User's live authority."""
    seed = _seed_publication(engine, suffix="dispatch-authority")
    _set_publication_state(engine, seed, status=PublicationStatus.PREFLIGHTING)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE workspaces
                SET publishing_role_policy = 'owner_admin'
                WHERE id = :workspace
                """
            ),
            {"workspace": seed["workspace"]},
        )
        connection.execute(
            text(
                """
                UPDATE workspace_memberships
                SET role = 'editor'
                WHERE workspace_id = :workspace AND user_id = :user
                """
            ),
            {"workspace": seed["workspace"], "user": seed["user"]},
        )

    with Session(engine) as session, session.begin():
        result = revalidate_publication_dispatch(
            session,
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            now=NOW,
        )

    assert result.status is PublicationStatus.CANCELLED


def test_dispatch_revalidation_returns_capability_drift_to_approval(
    engine: Engine, clean_database: None
) -> None:
    """Changed provider constraints require approval of the exact destination choices."""
    seed = _seed_publication(engine, suffix="dispatch-capability")
    _set_publication_state(engine, seed, status=PublicationStatus.PREFLIGHTING)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE social_accounts
                SET capability_snapshot = CAST(:capabilities AS jsonb)
                WHERE id = :account
                """
            ),
            {
                "account": seed["account"],
                "capabilities": '{"version":"cap-v2","values":{}}',
            },
        )

    with Session(engine) as session, session.begin():
        result = revalidate_publication_dispatch(
            session,
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            now=NOW,
        )

    assert result.status is PublicationStatus.AWAITING_APPROVAL
    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.checkpoint_metadata == {
            "preflightDiff": [
                {
                    "field": "capabilityVersion",
                    "approved": "cap-v1",
                    "current": "cap-v2",
                }
            ]
        }


def test_dispatch_revalidation_returns_a_withdrawn_review_to_approval(
    engine: Engine, clean_database: None
) -> None:
    """Provider I/O must stop when the exact Edit Revision is no longer approved."""
    seed = _seed_publication(engine, suffix="dispatch-review")
    _set_publication_state(engine, seed, status=PublicationStatus.PREFLIGHTING)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO edit_review_decisions
                    (id, workspace_id, clip_edit_id, clip_edit_revision_id, actor_user_id,
                     sequence, decision, created_at)
                VALUES (:id, :workspace, :edit, :revision, :user, 2, 'request_changes', :now)
                """
            ),
            {
                "id": uuid4(),
                "workspace": seed["workspace"],
                "edit": seed["edit"],
                "revision": seed["revision"],
                "user": seed["user"],
                "now": NOW + timedelta(seconds=1),
            },
        )

    with Session(engine) as session, session.begin():
        result = revalidate_publication_dispatch(
            session,
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            now=NOW + timedelta(seconds=2),
        )

    assert result.status is PublicationStatus.AWAITING_APPROVAL
    with Session(engine) as session:
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.checkpoint_metadata == {
            "preflightDiff": [
                {
                    "field": "editRevisionApproval",
                    "approved": True,
                    "current": False,
                }
            ]
        }


def test_dispatch_revalidation_returns_profile_drift_to_approval_with_a_clear_diff(
    engine: Engine, clean_database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deploying a new checked-in profile must never silently alter approved behavior."""
    seed = _seed_publication(engine, suffix="dispatch-profile")
    destination = PublicationDestinationDraft(
        social_account_id=seed["account"],
        metadata={"title": "Frozen profile"},
        provider_options={"privacy": "private"},
        consent={"confirmed": True},
        scheduled_for=None,
        display_timezone="UTC",
    )
    with Session(engine) as session, session.begin():
        draft = prepare_publication_draft(
            session,
            access=_access(seed),
            edit_id=seed["edit"],
            revision=1,
            render_artifact_id=seed["render"],
            destinations=(destination,),
            idempotency_key="dispatch-profile",
            now=NOW,
        )
        preflight_publication_draft(session, access=_access(seed), batch_id=draft.batch_id, now=NOW)
        confirmed = confirm_publication_draft(
            session, access=_access(seed), batch_id=draft.batch_id, now=NOW
        )
        publication_id = confirmed.publications[0].publication_id

    changed = replace(profile_for(SocialProvider.YOUTUBE), version="2026-10-01")
    monkeypatch.setattr("clipah.publishing.tasks.profile_for", lambda _provider: changed)
    with Session(engine) as session, session.begin():
        result = revalidate_publication_dispatch(
            session,
            workspace_id=seed["workspace"],
            publication_id=publication_id,
            now=NOW,
        )

    assert result.status is PublicationStatus.AWAITING_APPROVAL
    with Session(engine) as session:
        publication = session.get(Publication, publication_id)
        assert publication is not None
        assert publication.checkpoint_metadata is not None
        assert publication.checkpoint_metadata["preflightDiff"] == [
            {
                "field": "profileVersion",
                "approved": "2026-09-09",
                "current": "2026-10-01",
            }
        ]


def test_dispatch_binds_one_exact_rendition_and_refuses_replacement(
    engine: Engine, clean_database: None
) -> None:
    """Retries must keep using the rendition bytes selected before provider I/O."""
    seed = _seed_publication(engine, suffix="dispatch-rendition")
    _set_publication_state(engine, seed, status=PublicationStatus.PREFLIGHTING)
    rendition_ids = (uuid4(), uuid4())
    with engine.begin() as connection:
        for rendition_id, digest, profile_version in zip(
            rendition_ids,
            (bytes.fromhex("31" * 32), bytes.fromhex("32" * 32)),
            ("2026-09-09", "replacement-attempt"),
            strict=True,
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO social_renditions
                        (id, workspace_id, render_artifact_id, source_sha256, provider,
                         profile_version, output_sha256, storage_key, size_bytes, duration_ms,
                         provenance, validation_report, reused_master, created_at)
                        VALUES (:id, :workspace, :render, :source_sha256,
                            'youtube', :profile_version, :output_sha256,
                            :storage_key, 90, 1000, '{}',
                            CAST(:validation_report AS jsonb), false, :now)
                    """
                ),
                {
                    "id": rendition_id,
                    "workspace": seed["workspace"],
                    "render": seed["render"],
                    "source_sha256": seed["digest"],
                    "output_sha256": digest,
                    "profile_version": profile_version,
                    "storage_key": f"renditions/{rendition_id}.mp4",
                    "validation_report": json.dumps({"passed": True}),
                    "now": NOW,
                },
            )

    with Session(engine) as session, session.begin():
        bind_publication_rendition(
            session,
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            rendition_id=rendition_ids[0],
        )
        publication = session.get(Publication, seed["publication"])
        assert publication is not None
        assert publication.social_rendition_id == rendition_ids[0]

    with (
        Session(engine) as session,
        session.begin(),
        pytest.raises(PublicationDispatchInvalidError),
    ):
        bind_publication_rendition(
            session,
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            rendition_id=rendition_ids[1],
        )


def test_publication_api_prepares_preflights_confirms_and_reads_independent_work(
    engine: Engine, clean_database: None
) -> None:
    """The explicit HTTP ceremony preserves user intent and supports safe replay."""
    del clean_database
    clock = Clock(NOW)
    app, login_flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, login_flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    user_id = UUID(browser.get("/api/v1/me").json()["id"])
    seed = _seed_publication(
        engine,
        suffix="publication-api",
        user_id=user_id,
        workspace_id=workspace_id,
    )
    path = (
        f"/api/v1/edits/{seed['edit']}/revisions/1/publication-drafts?workspace_id={workspace_id}"
    )
    payload = {
        "renderArtifactId": str(seed["render"]),
        "destinations": [
            {
                "socialAccountId": str(seed["account"]),
                "metadata": {"title": "Approved title"},
                "providerOptions": {"privacy": "private"},
                "consent": {"confirmed": True},
                "displayTimezone": "Asia/Jakarta",
            }
        ],
    }

    prepared = browser.request(
        "POST", path, json=payload, headers={"Idempotency-Key": "api-publication"}
    )
    assert prepared.status_code == 201, prepared.text
    batch_id = prepared.json()["id"]
    replay = browser.request(
        "POST", path, json=payload, headers={"Idempotency-Key": "api-publication"}
    )
    preflight = browser.request(
        "POST",
        f"/api/v1/publication-drafts/{batch_id}/preflight?workspace_id={workspace_id}",
    )
    confirmed = browser.request(
        "POST",
        f"/api/v1/publication-drafts/{batch_id}/confirm?workspace_id={workspace_id}",
    )
    listed = browser.get(f"/api/v1/publications?workspace_id={workspace_id}")

    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == batch_id
    assert preflight.status_code == 200, preflight.text
    assert preflight.json()["publications"][0]["status"] == "awaiting_approval"
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["publications"][0]["status"] == "preflighting"
    assert listed.status_code == 200, listed.text
    confirmed_id = confirmed.json()["publications"][0]["id"]
    assert any(item["id"] == confirmed_id for item in listed.json()["publications"])
