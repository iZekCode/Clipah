"""Worker-side guards for durable Publication dispatch."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.editor.reviews import has_current_approval
from clipah.models import (
    ClipEditRevision,
    Publication,
    RenderArtifact,
    SocialAccount,
    SocialRendition,
)
from clipah.publishing.models import PublicationStatus, PublicationSummary
from clipah.publishing.preflight import (
    preflight,
    publication_evidence_from_snapshots,
    render_artifact_media,
)
from clipah.publishing.profiles import profile_for
from clipah.publishing.state_machine import transition
from clipah.publishing.use_cases import tiktok_policy_evidence, youtube_policy_evidence
from clipah.social_accounts.models import SocialConnectionStatus, SocialProvider
from clipah.workspaces.authorization import DatabaseWorkspaceAuthorizer
from clipah.workspaces.models import (
    WorkspaceAction,
    WorkspaceNotFoundError,
    WorkspacePermissionError,
)


class PublicationDispatchInvalidError(Exception):
    """The claimed row cannot safely begin provider work."""


def bind_publication_rendition(
    session: Session,
    *,
    workspace_id: UUID,
    publication_id: UUID,
    rendition_id: UUID,
) -> PublicationSummary:
    """Bind one exact compatible rendition once before provider side effects begin."""
    publication = session.scalar(
        select(Publication)
        .where(Publication.workspace_id == workspace_id, Publication.id == publication_id)
        .with_for_update()
    )
    if publication is None or publication.status is not PublicationStatus.PREFLIGHTING:
        raise PublicationDispatchInvalidError(str(publication_id))
    if publication.social_rendition_id is not None:
        if publication.social_rendition_id != rendition_id:
            raise PublicationDispatchInvalidError("Publication already has a different rendition")
        return _summary(publication)
    rendition = session.scalar(
        select(SocialRendition).where(
            SocialRendition.workspace_id == workspace_id,
            SocialRendition.id == rendition_id,
            SocialRendition.render_artifact_id == publication.render_artifact_id,
            SocialRendition.source_sha256 == publication.artifact_sha256,
        )
    )
    account = session.scalar(
        select(SocialAccount).where(
            SocialAccount.workspace_id == workspace_id,
            SocialAccount.id == publication.social_account_id,
        )
    )
    if (
        rendition is None
        or account is None
        or rendition.provider is not account.provider
        or rendition.profile_version != profile_for(account.provider).version
    ):
        raise PublicationDispatchInvalidError("rendition does not match Publication snapshot")
    publication.social_rendition_id = rendition.id
    session.flush()
    return _summary(publication)


def revalidate_publication_dispatch(
    session: Session,
    *,
    workspace_id: UUID,
    publication_id: UUID,
    now: datetime,
    youtube_audit_approved: bool = False,
    tiktok_direct_post_approved: bool = False,
) -> PublicationSummary:
    """Recheck live authority, connection, and capabilities before provider I/O."""
    publication = session.scalar(
        select(Publication)
        .where(
            Publication.workspace_id == workspace_id,
            Publication.id == publication_id,
        )
        .with_for_update()
    )
    if publication is None or publication.status is not PublicationStatus.PREFLIGHTING:
        raise PublicationDispatchInvalidError(str(publication_id))
    if publication.approved_by_user_id is None:
        raise PublicationDispatchInvalidError("publication has no approving actor")

    try:
        DatabaseWorkspaceAuthorizer(session).require(
            user_id=publication.approved_by_user_id,
            workspace_id=workspace_id,
            action=WorkspaceAction.PUBLISH,
        )
    except (WorkspaceNotFoundError, WorkspacePermissionError):
        publication.status = transition(
            current=publication.status, target=PublicationStatus.CANCELLED
        ).current
        publication.cancelled_at = now
        session.flush()
        return _summary(publication)

    account = session.scalar(
        select(SocialAccount)
        .where(
            SocialAccount.workspace_id == workspace_id,
            SocialAccount.id == publication.social_account_id,
        )
        .with_for_update()
    )
    if account is None or account.connection_status is not SocialConnectionStatus.ACTIVE:
        publication.status = transition(
            current=publication.status, target=PublicationStatus.RECONNECT_REQUIRED
        ).current
    elif account.capability_snapshot.get("version") != publication.capability_version:
        publication.status = transition(
            current=publication.status, target=PublicationStatus.AWAITING_APPROVAL
        ).current
        publication.checkpoint_metadata = {
            "preflightDiff": [
                {
                    "field": "capabilityVersion",
                    "approved": publication.capability_version,
                    "current": account.capability_snapshot.get("version"),
                }
            ]
        }
    elif not _approval_is_current(session, publication=publication):
        publication.status = transition(
            current=publication.status, target=PublicationStatus.AWAITING_APPROVAL
        ).current
        publication.checkpoint_metadata = {
            "preflightDiff": [
                {
                    "field": "editRevisionApproval",
                    "approved": True,
                    "current": False,
                }
            ]
        }
    elif not _current_preflight_matches(
        session,
        publication=publication,
        account=account,
        now=now,
        youtube_audit_approved=youtube_audit_approved,
        tiktok_direct_post_approved=tiktok_direct_post_approved,
    ):
        publication.status = transition(
            current=publication.status, target=PublicationStatus.AWAITING_APPROVAL
        ).current
    session.flush()
    return _summary(publication)


def _current_preflight_matches(
    session: Session,
    *,
    publication: Publication,
    account: SocialAccount,
    now: datetime,
    youtube_audit_approved: bool,
    tiktok_direct_post_approved: bool,
) -> bool:
    """Repeat provider validation against frozen bytes and choices immediately before I/O."""
    checkpoint = dict(publication.checkpoint_metadata or {})
    approved_report = checkpoint.get("preflight")
    if not isinstance(approved_report, dict):
        return True
    if account.provider is SocialProvider.YOUTUBE:
        approved_policy = checkpoint.get("youtubePolicy")
        current_policy = youtube_policy_evidence(
            publication=publication,
            now=now,
            audit_approved=youtube_audit_approved,
        )
        if approved_policy != current_policy:
            checkpoint["preflightDiff"] = [
                {
                    "field": "youtubePolicy",
                    "approved": approved_policy,
                    "current": current_policy,
                }
            ]
            publication.checkpoint_metadata = checkpoint
            return False
    if account.provider is SocialProvider.TIKTOK:
        approved_delivery = checkpoint.get("tiktokPolicy")
        current_delivery = tiktok_policy_evidence(
            publication=publication,
            now=now,
            audit_approved=tiktok_direct_post_approved,
        )
        if approved_delivery != current_delivery:
            checkpoint["preflightDiff"] = [
                {
                    "field": "tiktokPolicy",
                    "approved": approved_delivery,
                    "current": current_delivery,
                }
            ]
            publication.checkpoint_metadata = checkpoint
            return False
    current_profile = profile_for(account.provider)
    approved_version = approved_report.get("profileVersion")
    if approved_version != current_profile.version:
        checkpoint["preflightDiff"] = [
            {
                "field": "profileVersion",
                "approved": approved_version,
                "current": current_profile.version,
            }
        ]
        publication.checkpoint_metadata = checkpoint
        return False
    artifact = session.scalar(
        select(RenderArtifact).where(
            RenderArtifact.workspace_id == publication.workspace_id,
            RenderArtifact.id == publication.render_artifact_id,
        )
    )
    if artifact is None or artifact.sha256 is None:
        raise PublicationDispatchInvalidError("approved master is unavailable")
    current_report = preflight(
        profile=current_profile,
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
    ).as_dict()
    if current_report != approved_report:
        checkpoint["preflightDiff"] = [
            {
                "field": "validationReport",
                "approved": approved_report,
                "current": current_report,
            }
        ]
        publication.checkpoint_metadata = checkpoint
        return False
    if publication.social_rendition_id is not None:
        rendition = session.scalar(
            select(SocialRendition).where(
                SocialRendition.workspace_id == publication.workspace_id,
                SocialRendition.id == publication.social_rendition_id,
                SocialRendition.render_artifact_id == publication.render_artifact_id,
                SocialRendition.source_sha256 == publication.artifact_sha256,
                SocialRendition.provider == account.provider,
                SocialRendition.profile_version == current_profile.version,
            )
        )
        if rendition is None:
            raise PublicationDispatchInvalidError("bound rendition is unavailable")
    return True


def _approval_is_current(session: Session, *, publication: Publication) -> bool:
    """Reprove the exact Revision approval and immutable master checksum before I/O."""
    row = session.execute(
        select(ClipEditRevision, RenderArtifact)
        .join(
            RenderArtifact,
            (RenderArtifact.workspace_id == ClipEditRevision.workspace_id)
            & (RenderArtifact.clip_edit_revision_id == ClipEditRevision.id),
        )
        .where(
            ClipEditRevision.workspace_id == publication.workspace_id,
            ClipEditRevision.id == publication.edit_revision_id,
            RenderArtifact.id == publication.render_artifact_id,
        )
    ).one_or_none()
    return bool(
        row is not None
        and row.RenderArtifact.sha256 is not None
        and bytes(row.RenderArtifact.sha256) == bytes(publication.artifact_sha256)
        and has_current_approval(
            session,
            workspace_id=publication.workspace_id,
            edit_id=row.ClipEditRevision.clip_edit_id,
            revision_id=row.ClipEditRevision.id,
        )
    )


def _summary(publication: Publication) -> PublicationSummary:
    """Detach the safe dispatch result from its locked ORM row."""
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
    )
