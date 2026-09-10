"""Prepare, approve, read, retry, and cancel durable Publications."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.editor.reviews import has_current_approval
from clipah.models import (
    ClipEdit,
    ClipEditRevision,
    Publication,
    PublicationBatch,
    RenderArtifact,
    SocialAccount,
)
from clipah.publishing.models import (
    PreflightDifference,
    PublicationBatchSummary,
    PublicationDestinationDraft,
    PublicationStatus,
    PublicationSummary,
)
from clipah.publishing.outbox import PublicationOutboxService
from clipah.publishing.preflight import (
    preflight,
    publication_evidence_from_snapshots,
    render_artifact_media,
)
from clipah.publishing.profiles import profile_for
from clipah.publishing.providers.tiktok.adapter import TikTokPolicy
from clipah.publishing.providers.youtube.adapter import (
    YouTubeAuditRestrictionError,
    YouTubePolicy,
    YouTubePrivacy,
)
from clipah.publishing.state_machine import may_cancel, transition
from clipah.social_accounts.models import SocialConnectionStatus, SocialProvider
from clipah.workspaces.models import WorkspaceAccess


class PublicationNotFoundError(Exception):
    """The requested publishing resource is missing or outside the Workspace."""


class PublicationInvalidError(Exception):
    """The draft does not name a complete, approved, reproducible delivery."""


class PublicationIdempotencyConflictError(Exception):
    """One request key was reused for different publication choices."""


class PublicationRetryBlockedError(Exception):
    """A retry would risk duplicating provider work before reconciliation."""


def get_publication_batch(
    session: Session, *, access: WorkspaceAccess, batch_id: UUID
) -> PublicationBatchSummary:
    """Read one tenant-scoped batch without exposing provider secrets."""
    batch = session.scalar(
        select(PublicationBatch).where(
            PublicationBatch.workspace_id == access.workspace_id,
            PublicationBatch.id == batch_id,
        )
    )
    if batch is None:
        raise PublicationNotFoundError(str(batch_id))
    return _batch_summary(session, batch=batch)


def get_publication(
    session: Session, *, access: WorkspaceAccess, publication_id: UUID
) -> PublicationSummary:
    """Read one tenant-scoped destination without leaking a guessed identifier."""
    publication = session.scalar(
        select(Publication).where(
            Publication.workspace_id == access.workspace_id,
            Publication.id == publication_id,
        )
    )
    if publication is None:
        raise PublicationNotFoundError(str(publication_id))
    return _publication_summary(publication)


def list_publications(
    session: Session, *, access: WorkspaceAccess
) -> tuple[PublicationSummary, ...]:
    """List one Workspace's destinations in deterministic newest-first order."""
    return tuple(
        _publication_summary(publication)
        for publication in session.scalars(
            select(Publication)
            .where(Publication.workspace_id == access.workspace_id)
            .order_by(Publication.created_at.desc(), Publication.id.desc())
        )
    )


def prepare_publication_draft(
    session: Session,
    *,
    access: WorkspaceAccess,
    edit_id: UUID,
    revision: int,
    render_artifact_id: UUID,
    destinations: tuple[PublicationDestinationDraft, ...],
    idempotency_key: str,
    now: datetime,
) -> PublicationBatchSummary:
    """Persist one independently recoverable draft per explicit destination."""
    if not destinations or len({item.social_account_id for item in destinations}) != len(
        destinations
    ):
        raise PublicationInvalidError("destinations must be nonempty and unique")
    normalized = tuple(_normalize_destination(item, now=now) for item in destinations)
    fingerprint = _request_fingerprint(
        edit_id=edit_id,
        revision=revision,
        render_artifact_id=render_artifact_id,
        destinations=normalized,
    )
    existing = session.scalar(
        select(PublicationBatch).where(
            PublicationBatch.workspace_id == access.workspace_id,
            PublicationBatch.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if bytes(existing.request_fingerprint) != fingerprint:
            raise PublicationIdempotencyConflictError(idempotency_key)
        return _batch_summary(session, batch=existing)

    target = session.execute(
        select(ClipEditRevision, RenderArtifact)
        .join(
            ClipEdit,
            (ClipEdit.workspace_id == ClipEditRevision.workspace_id)
            & (ClipEdit.id == ClipEditRevision.clip_edit_id),
        )
        .join(
            RenderArtifact,
            (RenderArtifact.workspace_id == ClipEditRevision.workspace_id)
            & (RenderArtifact.clip_edit_revision_id == ClipEditRevision.id),
        )
        .where(
            ClipEditRevision.workspace_id == access.workspace_id,
            ClipEditRevision.clip_edit_id == edit_id,
            ClipEditRevision.revision == revision,
            RenderArtifact.id == render_artifact_id,
        )
    ).one_or_none()
    if target is None or target.RenderArtifact.sha256 is None:
        raise PublicationNotFoundError(str(render_artifact_id))
    if not has_current_approval(
        session,
        workspace_id=access.workspace_id,
        edit_id=edit_id,
        revision_id=target.ClipEditRevision.id,
    ):
        raise PublicationInvalidError("the exact Edit Revision is not approved")

    accounts = {
        account.id: account
        for account in session.scalars(
            select(SocialAccount).where(
                SocialAccount.workspace_id == access.workspace_id,
                SocialAccount.id.in_([item.social_account_id for item in normalized]),
                SocialAccount.connection_status == SocialConnectionStatus.ACTIVE,
            )
        )
    }
    if len(accounts) != len(normalized):
        raise PublicationNotFoundError("one or more Social Accounts are unavailable")

    batch = PublicationBatch(
        id=uuid4(),
        workspace_id=access.workspace_id,
        edit_revision_id=target.ClipEditRevision.id,
        render_artifact_id=target.RenderArtifact.id,
        created_by_user_id=access.user_id,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        created_at=now,
    )
    session.add(batch)
    session.flush()
    for item in normalized:
        publication_id = uuid4()
        session.add(
            Publication(
                id=publication_id,
                workspace_id=access.workspace_id,
                batch_id=batch.id,
                social_account_id=item.social_account_id,
                edit_revision_id=target.ClipEditRevision.id,
                render_artifact_id=target.RenderArtifact.id,
                artifact_sha256=target.RenderArtifact.sha256,
                approved_by_user_id=None,
                metadata_snapshot=dict(item.metadata),
                provider_options=dict(item.provider_options),
                consent_snapshot=dict(item.consent),
                capability_version=None,
                provider_policy_version=None,
                scheduled_for=item.scheduled_for,
                display_timezone=item.display_timezone,
                status=PublicationStatus.DRAFT,
                idempotency_key=idempotency_key,
                provider_operation_key=f"publication:{publication_id}:1",
                attempt_count=0,
                created_at=now,
            )
        )
    session.flush()
    return _batch_summary(session, batch=batch)


def preflight_publication_draft(
    session: Session,
    *,
    access: WorkspaceAccess,
    batch_id: UUID,
    now: datetime,
    youtube_audit_approved: bool = False,
    tiktok_direct_post_approved: bool = False,
) -> PublicationBatchSummary:
    """Validate durable prerequisites and move every draft to approval review."""
    batch, publications = _locked_batch(
        session, workspace_id=access.workspace_id, batch_id=batch_id
    )
    for publication in publications:
        account = session.scalar(
            select(SocialAccount).where(
                SocialAccount.workspace_id == access.workspace_id,
                SocialAccount.id == publication.social_account_id,
                SocialAccount.connection_status == SocialConnectionStatus.ACTIVE,
            )
        )
        artifact = session.scalar(
            select(RenderArtifact).where(
                RenderArtifact.workspace_id == access.workspace_id,
                RenderArtifact.id == publication.render_artifact_id,
            )
        )
        if account is None or artifact is None or artifact.sha256 is None:
            raise PublicationNotFoundError("Publication preflight input is unavailable")
        capability_version = account.capability_snapshot.get("version")
        if not isinstance(capability_version, str) or not capability_version:
            raise PublicationInvalidError("Social Account has no capability version")
        profile = profile_for(account.provider)
        report = preflight(
            profile=profile,
            media=render_artifact_media(
                preset=artifact.preset,
                size_bytes=artifact.size_bytes,
                duration_ms=artifact.duration_ms,
            ),
            evidence=publication_evidence_from_snapshots(
                metadata=publication.metadata_snapshot,
                provider_options=publication.provider_options,
                consent=publication.consent_snapshot,
                watermark_text=artifact.watermark_text,
            ),
        )
        checkpoint = dict(publication.checkpoint_metadata or {})
        checkpoint.update(
            {
                "preflight": report.as_dict(),
                "preflightCapabilityVersion": capability_version,
            }
        )
        if account.provider is SocialProvider.YOUTUBE:
            checkpoint["youtubePolicy"] = youtube_policy_evidence(
                publication=publication,
                now=now,
                audit_approved=youtube_audit_approved,
            )
        elif account.provider is SocialProvider.TIKTOK:
            checkpoint["tiktokPolicy"] = tiktok_policy_evidence(
                publication=publication,
                now=now,
                audit_approved=tiktok_direct_post_approved,
            )
        publication.checkpoint_metadata = checkpoint
        if publication.status is PublicationStatus.DRAFT:
            publication.status = transition(
                current=publication.status, target=PublicationStatus.AWAITING_APPROVAL
            ).current
        elif publication.status is not PublicationStatus.AWAITING_APPROVAL:
            raise PublicationInvalidError("draft is no longer preflightable")
    session.flush()
    return _summary(batch, publications)


def confirm_publication_draft(
    session: Session,
    *,
    access: WorkspaceAccess,
    batch_id: UUID,
    now: datetime,
    youtube_audit_approved: bool = False,
    tiktok_direct_post_approved: bool = False,
) -> PublicationBatchSummary:
    """Freeze approval evidence and independently schedule or dispatch each destination."""
    batch, publications = _locked_batch(
        session, workspace_id=access.workspace_id, batch_id=batch_id
    )
    confirmed_states = {PublicationStatus.SCHEDULED, PublicationStatus.PREFLIGHTING}
    if all(publication.status in confirmed_states for publication in publications):
        return _summary(batch, publications)
    _require_current_approval(session, batch=batch, publications=publications)
    for publication in publications:
        if publication.status in confirmed_states:
            continue
        if publication.status is not PublicationStatus.AWAITING_APPROVAL:
            raise PublicationInvalidError("draft must pass preflight before confirmation")
        if publication.consent_snapshot.get("confirmed") is not True:
            raise PublicationInvalidError("each destination requires explicit consent")
        account = session.scalar(
            select(SocialAccount)
            .where(
                SocialAccount.workspace_id == access.workspace_id,
                SocialAccount.id == publication.social_account_id,
                SocialAccount.connection_status == SocialConnectionStatus.ACTIVE,
            )
            .with_for_update()
        )
        if account is None:
            raise PublicationNotFoundError(str(publication.social_account_id))
        capability_version = account.capability_snapshot.get("version")
        if not isinstance(capability_version, str) or not capability_version:
            raise PublicationInvalidError("Social Account has no capability version")
        checkpoint = publication.checkpoint_metadata or {}
        preflight_report = checkpoint.get("preflight")
        profile = profile_for(account.provider)
        if (
            not isinstance(preflight_report, dict)
            or preflight_report.get("passed") is not True
            or preflight_report.get("profileVersion") != profile.version
            or checkpoint.get("preflightCapabilityVersion") != capability_version
        ):
            raise PublicationInvalidError("Publication must pass current preflight")
        if account.provider is SocialProvider.YOUTUBE and checkpoint.get(
            "youtubePolicy"
        ) != youtube_policy_evidence(
            publication=publication,
            now=now,
            audit_approved=youtube_audit_approved,
        ):
            raise PublicationInvalidError("Publication must pass current YouTube policy review")
        if account.provider is SocialProvider.TIKTOK and checkpoint.get(
            "tiktokPolicy"
        ) != tiktok_policy_evidence(
            publication=publication,
            now=now,
            audit_approved=tiktok_direct_post_approved,
        ):
            raise PublicationInvalidError("Publication must pass current TikTok policy review")
        publication.approved_by_user_id = access.user_id
        publication.approved_at = now
        publication.capability_version = capability_version
        publication.provider_policy_version = f"{account.provider.value}:{account.api_version}"
        target = (
            PublicationStatus.SCHEDULED
            if publication.scheduled_for is not None
            else PublicationStatus.PREFLIGHTING
        )
        publication.status = transition(current=publication.status, target=target).current
        if target is PublicationStatus.PREFLIGHTING:
            PublicationOutboxService(session).enqueue(
                workspace_id=access.workspace_id,
                publication_id=publication.id,
                topic="publication.preflight",
                operation_key=f"{publication.provider_operation_key}:attempt:0",
                available_at=now,
            )
    session.flush()
    return _summary(batch, publications)


def youtube_policy_evidence(
    *, publication: Publication, now: datetime, audit_approved: bool
) -> dict[str, str | None]:
    """Build reproducible YouTube visibility evidence from frozen Publication choices."""
    raw_privacy = publication.provider_options.get("privacy", YouTubePrivacy.PRIVATE.value)
    try:
        requested = YouTubePrivacy(raw_privacy)
        return YouTubePolicy(audit_approved=audit_approved, now=now).confirmation_evidence_for(
            requested=requested,
            scheduled_for=publication.scheduled_for,
        )
    except (ValueError, YouTubeAuditRestrictionError) as error:
        raise PublicationInvalidError("YouTube publication policy is invalid") from error


def tiktok_policy_evidence(
    *, publication: Publication, now: datetime, audit_approved: bool
) -> dict[str, str | None]:
    """Build reproducible TikTok delivery evidence from this deployment's audit state."""
    del publication
    return TikTokPolicy(direct_post_approved=audit_approved, now=now).confirmation_evidence()


def retry_publication(
    session: Session, *, access: WorkspaceAccess, publication_id: UUID, now: datetime
) -> PublicationSummary:
    """Retry only a recoverable destination after ambiguous work is reconciled."""
    publication = _locked_publication(
        session, workspace_id=access.workspace_id, publication_id=publication_id
    )
    checkpoint = publication.checkpoint_metadata or {}
    if checkpoint.get("ambiguous") is True and checkpoint.get("reconciled") is not True:
        raise PublicationRetryBlockedError("provider truth must be reconciled before retry")
    if publication.status is not PublicationStatus.RETRYABLE_FAILED:
        raise PublicationInvalidError("only retryable failure may be retried")
    publication.status = transition(
        current=publication.status, target=PublicationStatus.PREFLIGHTING
    ).current
    publication.attempt_count += 1
    publication.next_attempt_at = None
    publication.normalized_error_code = None
    publication.sanitized_error_message = None
    PublicationOutboxService(session).enqueue(
        workspace_id=publication.workspace_id,
        publication_id=publication.id,
        topic="publication.preflight",
        operation_key=(f"{publication.provider_operation_key}:attempt:{publication.attempt_count}"),
        available_at=now,
    )
    session.flush()
    return _publication_summary(publication)


def cancel_publication(
    session: Session,
    *,
    access: WorkspaceAccess,
    publication_id: UUID,
    provider_cancellable: bool,
    now: datetime,
) -> PublicationSummary:
    """Cancel eligible work once, rejecting any false provider-side promise."""
    publication = _locked_publication(
        session, workspace_id=access.workspace_id, publication_id=publication_id
    )
    if publication.status is PublicationStatus.CANCELLED:
        return _publication_summary(publication)
    may_cancel(current=publication.status, provider_cancellable=provider_cancellable)
    publication.status = transition(
        current=publication.status, target=PublicationStatus.CANCELLED
    ).current
    publication.cancelled_at = now
    session.flush()
    return _publication_summary(publication)


def cancel_publications_approved_by(
    session: Session, *, workspace_id: UUID, user_id: UUID, now: datetime
) -> int:
    """Cancel unpublished work one departing person approved.

    Their approval is the authority the Publication would be dispatched under, and that
    authority ends with their account. Work somebody else approved is untouched, and
    anything already published stays published.
    """
    publications = tuple(
        session.scalars(
            select(Publication)
            .where(
                Publication.workspace_id == workspace_id,
                Publication.approved_by_user_id == user_id,
                Publication.status.not_in(
                    {
                        PublicationStatus.PUBLISHED,
                        PublicationStatus.PERMANENT_FAILED,
                        PublicationStatus.CANCELLED,
                    }
                ),
            )
            .with_for_update()
        )
    )
    for publication in publications:
        publication.status = transition(
            current=publication.status, target=PublicationStatus.CANCELLED
        ).current
        publication.cancelled_at = now
    session.flush()
    return len(publications)


class PublicationFutureWorkCoordinator:
    """Pause or cancel one disconnected Social Account's unpublished work."""

    def __init__(self, session: Session) -> None:
        """Share the Social Account disconnect transaction."""
        self._session = session

    def cancel_or_pause_unpublished(
        self, *, workspace_id: UUID, social_account_id: UUID, now: datetime
    ) -> None:
        """Make future provider side effects ineligible without changing terminal history."""
        publications = tuple(
            self._session.scalars(
                select(Publication)
                .where(
                    Publication.workspace_id == workspace_id,
                    Publication.social_account_id == social_account_id,
                    Publication.status.not_in(
                        {
                            PublicationStatus.PUBLISHED,
                            PublicationStatus.PERMANENT_FAILED,
                            PublicationStatus.CANCELLED,
                        }
                    ),
                )
                .with_for_update()
            )
        )
        for publication in publications:
            if publication.status in {
                PublicationStatus.TRANSFERRING,
                PublicationStatus.PROCESSING,
            }:
                target = PublicationStatus.RECONNECT_REQUIRED
            elif publication.status is PublicationStatus.DRAFT:
                # Draft has no external side effect and may be removed from eligibility directly.
                publication.status = PublicationStatus.CANCELLED
                publication.cancelled_at = now
                continue
            else:
                target = PublicationStatus.CANCELLED
            publication.status = transition(current=publication.status, target=target).current
            if target is PublicationStatus.CANCELLED:
                publication.cancelled_at = now
        self._session.flush()


def _locked_publication(
    session: Session, *, workspace_id: UUID, publication_id: UUID
) -> Publication:
    """Lock one Publication behind the guessed-ID-safe tenant predicate."""
    publication = session.scalar(
        select(Publication)
        .where(
            Publication.workspace_id == workspace_id,
            Publication.id == publication_id,
        )
        .with_for_update()
    )
    if publication is None:
        raise PublicationNotFoundError(str(publication_id))
    return publication


def _require_current_approval(
    session: Session,
    *,
    batch: PublicationBatch,
    publications: tuple[Publication, ...],
) -> None:
    """Reprove the editorial approval and exact artifact at confirmation time."""
    revision = session.scalar(
        select(ClipEditRevision).where(
            ClipEditRevision.workspace_id == batch.workspace_id,
            ClipEditRevision.id == batch.edit_revision_id,
        )
    )
    artifact = session.scalar(
        select(RenderArtifact).where(
            RenderArtifact.workspace_id == batch.workspace_id,
            RenderArtifact.id == batch.render_artifact_id,
            RenderArtifact.clip_edit_revision_id == batch.edit_revision_id,
        )
    )
    if (
        revision is None
        or artifact is None
        or artifact.sha256 is None
        or not has_current_approval(
            session,
            workspace_id=batch.workspace_id,
            edit_id=revision.clip_edit_id,
            revision_id=revision.id,
        )
        or any(bytes(item.artifact_sha256) != bytes(artifact.sha256) for item in publications)
    ):
        raise PublicationInvalidError("the approved Revision or artifact changed")


def _publication_summary(publication: Publication) -> PublicationSummary:
    """Detach safe state from one mutable ORM row."""
    return PublicationSummary(
        publication_id=publication.id,
        batch_id=publication.batch_id,
        social_account_id=publication.social_account_id,
        status=publication.status,
        scheduled_for=publication.scheduled_for,
        display_timezone=publication.display_timezone,
        provider_publication_id=publication.provider_publication_id,
        provider_permalink=publication.provider_permalink,
        normalized_error_code=publication.normalized_error_code,
        sanitized_error_message=publication.sanitized_error_message,
        attempt_count=publication.attempt_count,
        next_attempt_at=publication.next_attempt_at,
        created_at=publication.created_at,
        approved_at=publication.approved_at,
        dispatched_at=publication.dispatched_at,
        transferred_at=publication.transferred_at,
        processing_at=publication.processing_at,
        published_at=publication.published_at,
        failed_at=publication.failed_at,
        cancelled_at=publication.cancelled_at,
        preflight_diff=_preflight_diff(publication),
    )


def _preflight_diff(publication: Publication) -> tuple[PreflightDifference, ...]:
    """Read only the approval drift out of a checkpoint that also holds transfer state."""
    entries = (publication.checkpoint_metadata or {}).get("preflightDiff")
    if not isinstance(entries, list):
        return ()
    return tuple(
        PreflightDifference(
            field=str(entry["field"]),
            approved=_diff_value(entry.get("approved")),
            current=_diff_value(entry.get("current")),
        )
        for entry in entries
        if isinstance(entry, dict) and "field" in entry
    )


def _diff_value(value: object) -> str | None:
    """Render one drifted value as text a member can compare, or nothing at all."""
    if value is None:
        return None
    if isinstance(value, str | bool | int | float):
        return str(value)
    return None


def _normalize_destination(
    destination: PublicationDestinationDraft, *, now: datetime
) -> PublicationDestinationDraft:
    """Validate an IANA display zone and store any schedule as a UTC instant."""
    try:
        ZoneInfo(destination.display_timezone)
    except ZoneInfoNotFoundError as error:
        raise PublicationInvalidError("display timezone is unknown") from error
    scheduled_for = destination.scheduled_for
    if scheduled_for is not None:
        scheduled_for = scheduled_for.astimezone(UTC)
        if scheduled_for <= now.astimezone(UTC):
            raise PublicationInvalidError("scheduled time must be in the future")
    return destination.model_copy(update={"scheduled_for": scheduled_for})


def _request_fingerprint(
    *,
    edit_id: UUID,
    revision: int,
    render_artifact_id: UUID,
    destinations: tuple[PublicationDestinationDraft, ...],
) -> bytes:
    """Bind an idempotency key to canonical user-visible choices."""
    body = {
        "edit_id": str(edit_id),
        "revision": revision,
        "render_artifact_id": str(render_artifact_id),
        "destinations": [
            item.model_dump(mode="json")
            for item in sorted(destinations, key=lambda candidate: str(candidate.social_account_id))
        ],
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).digest()


def _locked_batch(
    session: Session, *, workspace_id: UUID, batch_id: UUID
) -> tuple[PublicationBatch, tuple[Publication, ...]]:
    """Lock one batch and all its destinations in stable order."""
    batch = session.scalar(
        select(PublicationBatch).where(
            PublicationBatch.workspace_id == workspace_id,
            PublicationBatch.id == batch_id,
        )
    )
    if batch is None:
        raise PublicationNotFoundError(str(batch_id))
    publications = tuple(
        session.scalars(
            select(Publication)
            .where(
                Publication.workspace_id == workspace_id,
                Publication.batch_id == batch_id,
            )
            .order_by(Publication.created_at, Publication.id)
            .with_for_update()
        )
    )
    if not publications:
        raise PublicationNotFoundError(str(batch_id))
    return batch, publications


def _batch_summary(session: Session, *, batch: PublicationBatch) -> PublicationBatchSummary:
    """Read one batch after an idempotent create replay."""
    publications = tuple(
        session.scalars(
            select(Publication)
            .where(
                Publication.workspace_id == batch.workspace_id,
                Publication.batch_id == batch.id,
            )
            .order_by(Publication.created_at, Publication.id)
        )
    )
    return _summary(batch, publications)


def _summary(
    batch: PublicationBatch, publications: tuple[Publication, ...]
) -> PublicationBatchSummary:
    """Detach safe immutable values from ORM rows before returning."""
    return PublicationBatchSummary(
        batch_id=batch.id,
        edit_revision_id=batch.edit_revision_id,
        render_artifact_id=batch.render_artifact_id,
        publications=tuple(_publication_summary(publication) for publication in publications),
    )
