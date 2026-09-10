"""Locked persistence coordinator for one YouTube Publication."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.models import Publication, PublicationAttempt, SocialAccount, SocialRendition
from clipah.publishing.models import PublicationStatus
from clipah.publishing.providers.youtube.adapter import YouTubePrivacy
from clipah.publishing.providers.youtube.resumable import UploadCheckpoint
from clipah.publishing.providers.youtube.status import (
    AttachmentResult,
    YouTubeOutcome,
    YouTubePermanentError,
    YouTubeProviderError,
    YouTubeReconnectRequiredError,
    YouTubeStatus,
)
from clipah.publishing.state_machine import transition
from clipah.social_accounts.models import SocialProvider


class YouTubePublicationInvalidError(Exception):
    """The durable Publication cannot safely accept this YouTube result."""


class YouTubePublicationCoordinator:
    """Apply safe adapter results to one locked tenant-scoped Publication."""

    def __init__(self, session: Session) -> None:
        """Share the transaction that makes one provider checkpoint durable."""
        self._session = session

    def save_upload_checkpoint(
        self,
        *,
        workspace_id: UUID,
        publication_id: UUID,
        secret_reference: str,
        checkpoint: UploadCheckpoint,
        now: datetime,
    ) -> None:
        """Persist an opaque vault reference and safe progress before more bytes are sent."""
        publication, _account, rendition = self._locked_youtube_publication(
            workspace_id=workspace_id,
            publication_id=publication_id,
        )
        if publication.status not in {
            PublicationStatus.PREFLIGHTING,
            PublicationStatus.TRANSFERRING,
        }:
            raise YouTubePublicationInvalidError("Publication is not transferable")
        if (
            secret_reference != checkpoint.secret_reference
            or bytes(rendition.output_sha256) != checkpoint.source_sha256
            or rendition.size_bytes != checkpoint.total_bytes
        ):
            raise YouTubePublicationInvalidError("checkpoint does not match the bound rendition")
        if publication.status is PublicationStatus.PREFLIGHTING:
            publication.status = transition(
                current=publication.status,
                target=PublicationStatus.TRANSFERRING,
            ).current
            publication.dispatched_at = now
        publication.encrypted_checkpoint_reference = secret_reference
        metadata = dict(publication.checkpoint_metadata or {})
        metadata["youtubeUpload"] = checkpoint.safe_dict()
        publication.checkpoint_metadata = metadata
        stage = (
            "youtube_begin"
            if checkpoint.acknowledged_bytes == 0
            else f"youtube_chunk_{checkpoint.acknowledged_bytes}"
        )
        self._append_attempt(
            publication=publication,
            stage=stage,
            byte_checkpoint=checkpoint.acknowledged_bytes,
            response_metadata={"result": checkpoint.progress.value},
            now=now,
        )
        self._session.flush()

    def record_video_created(
        self,
        *,
        workspace_id: UUID,
        publication_id: UUID,
        video_id: str,
        provider_request_id: str | None,
        now: datetime,
    ) -> None:
        """Retain one authoritative video ID and move transfer truth to processing."""
        publication, _account, _rendition = self._locked_youtube_publication(
            workspace_id=workspace_id,
            publication_id=publication_id,
        )
        if publication.provider_publication_id is not None:
            if publication.provider_publication_id != video_id:
                raise YouTubePublicationInvalidError("Publication already names another video")
            return
        if publication.status is not PublicationStatus.TRANSFERRING or not video_id:
            raise YouTubePublicationInvalidError("Publication cannot record a YouTube video")
        publication.provider_publication_id = video_id
        publication.provider_permalink = (
            f"https://www.youtube.com/watch?v={quote(video_id, safe='')}"
        )
        publication.status = transition(
            current=publication.status,
            target=PublicationStatus.PROCESSING,
        ).current
        publication.transferred_at = now
        publication.processing_at = now
        self._append_attempt(
            publication=publication,
            stage="youtube_complete",
            provider_request_id=provider_request_id,
            response_metadata={"result": "video_created"},
            now=now,
        )
        self._session.flush()

    def apply_status(
        self,
        *,
        workspace_id: UUID,
        publication_id: UUID,
        status: YouTubeStatus,
        poll_sequence: int,
        now: datetime,
    ) -> None:
        """Apply authoritative processing truth without permitting another upload."""
        publication, _account, _rendition = self._locked_youtube_publication(
            workspace_id=workspace_id,
            publication_id=publication_id,
        )
        if publication.provider_publication_id != status.video_id:
            raise YouTubePublicationInvalidError("YouTube status names another video")
        if publication.status not in {
            PublicationStatus.PROCESSING,
            PublicationStatus.SCHEDULED,
            PublicationStatus.PUBLISHED,
        }:
            raise YouTubePublicationInvalidError("Publication is not awaiting YouTube status")
        if (
            publication.status is PublicationStatus.SCHEDULED
            and status.outcome is YouTubeOutcome.SUCCEEDED
            and (
                status.privacy is not YouTubePrivacy.PRIVATE
                or (status.scheduled_for is not None and status.scheduled_for <= now)
            )
        ):
            publication.status = transition(
                current=publication.status,
                target=PublicationStatus.PUBLISHED,
            ).current
            publication.published_at = now
        if publication.status is PublicationStatus.PROCESSING:
            if status.outcome is YouTubeOutcome.SUCCEEDED:
                if status.scheduled_for is not None and status.scheduled_for > now:
                    if publication.scheduled_for != status.scheduled_for:
                        raise YouTubePublicationInvalidError(
                            "YouTube schedule differs from the frozen Publication"
                        )
                    publication.status = transition(
                        current=publication.status,
                        target=PublicationStatus.SCHEDULED,
                    ).current
                else:
                    publication.status = transition(
                        current=publication.status,
                        target=PublicationStatus.PUBLISHED,
                    ).current
                    publication.published_at = now
            elif status.outcome in {YouTubeOutcome.FAILED, YouTubeOutcome.REJECTED}:
                publication.status = transition(
                    current=publication.status,
                    target=PublicationStatus.PERMANENT_FAILED,
                ).current
                publication.normalized_error_code = status.failure_code or "youtube_rejected"
                publication.sanitized_error_message = "YouTube could not publish this video."
                publication.failed_at = now
        self._append_attempt(
            publication=publication,
            stage=f"youtube_status_{poll_sequence}",
            response_metadata={"result": status.outcome.value},
            error_code=publication.normalized_error_code,
            error_message=publication.sanitized_error_message,
            now=now,
        )
        self._session.flush()

    def record_attachment(
        self,
        *,
        workspace_id: UUID,
        publication_id: UUID,
        kind: str,
        result: AttachmentResult,
        attachment_attempt: int,
        now: datetime,
    ) -> None:
        """Persist one attachment outcome without rewriting base-video state."""
        publication, _account, _rendition = self._locked_youtube_publication(
            workspace_id=workspace_id,
            publication_id=publication_id,
        )
        if (
            kind not in {"thumbnail", "caption"}
            or publication.provider_publication_id != result.video_id
        ):
            raise YouTubePublicationInvalidError("attachment does not match the Publication")
        if publication.status not in {PublicationStatus.PROCESSING, PublicationStatus.PUBLISHED}:
            raise YouTubePublicationInvalidError("base video has not been created")
        metadata = dict(publication.checkpoint_metadata or {})
        key = "youtubeThumbnail" if kind == "thumbnail" else "youtubeCaption"
        metadata[key] = {
            "state": result.state.value,
            "resourceId": result.resource_id,
        }
        publication.checkpoint_metadata = metadata
        self._append_attempt(
            publication=publication,
            stage=f"youtube_{kind}_{attachment_attempt}",
            response_metadata={"result": result.state.value},
            now=now,
        )
        self._session.flush()

    def apply_error(
        self,
        *,
        workspace_id: UUID,
        publication_id: UUID,
        stage: str,
        error: Exception,
        now: datetime,
    ) -> None:
        """Map one sanitized provider failure onto the truthful durable lifecycle."""
        publication, _account, _rendition = self._locked_youtube_publication(
            workspace_id=workspace_id,
            publication_id=publication_id,
        )
        if publication.status not in {
            PublicationStatus.PREFLIGHTING,
            PublicationStatus.TRANSFERRING,
            PublicationStatus.PROCESSING,
        }:
            raise YouTubePublicationInvalidError("Publication cannot accept a provider failure")
        if isinstance(error, YouTubeReconnectRequiredError):
            target = PublicationStatus.RECONNECT_REQUIRED
            message = "Reconnect the YouTube Social Account before retrying."
        elif isinstance(error, YouTubePermanentError):
            target = PublicationStatus.PERMANENT_FAILED
            message = "YouTube rejected the publishing operation."
        elif isinstance(error, YouTubeProviderError):
            target = PublicationStatus.RETRYABLE_FAILED
            message = "YouTube publishing is temporarily unavailable."
        else:
            raise YouTubePublicationInvalidError("provider failure is not normalized")
        publication.status = transition(current=publication.status, target=target).current
        publication.normalized_error_code = error.code
        publication.sanitized_error_message = message
        if target is PublicationStatus.PERMANENT_FAILED:
            publication.failed_at = now
        self._append_attempt(
            publication=publication,
            stage=stage,
            response_metadata={"result": target.value},
            error_code=error.code,
            error_message=message,
            now=now,
        )
        self._session.flush()

    def _locked_youtube_publication(
        self, *, workspace_id: UUID, publication_id: UUID
    ) -> tuple[Publication, SocialAccount, SocialRendition]:
        """Lock and validate one exact YouTube Publication and bound rendition."""
        publication = self._session.scalar(
            select(Publication)
            .where(
                Publication.workspace_id == workspace_id,
                Publication.id == publication_id,
            )
            .with_for_update()
        )
        if publication is None or publication.social_rendition_id is None:
            raise YouTubePublicationInvalidError("Publication is unavailable")
        account = self._session.scalar(
            select(SocialAccount).where(
                SocialAccount.workspace_id == workspace_id,
                SocialAccount.id == publication.social_account_id,
            )
        )
        rendition = self._session.scalar(
            select(SocialRendition).where(
                SocialRendition.workspace_id == workspace_id,
                SocialRendition.id == publication.social_rendition_id,
                SocialRendition.render_artifact_id == publication.render_artifact_id,
            )
        )
        if (
            account is None
            or rendition is None
            or account.provider is not SocialProvider.YOUTUBE
            or rendition.provider is not SocialProvider.YOUTUBE
        ):
            raise YouTubePublicationInvalidError("Publication is not a bound YouTube delivery")
        return publication, account, rendition

    def _append_attempt(
        self,
        *,
        publication: Publication,
        stage: str,
        response_metadata: dict[str, object],
        now: datetime,
        provider_request_id: str | None = None,
        byte_checkpoint: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Append one bounded secret-free durable stage exactly once."""
        attempt_number = publication.attempt_count + 1
        existing = self._session.scalar(
            select(PublicationAttempt.id).where(
                PublicationAttempt.workspace_id == publication.workspace_id,
                PublicationAttempt.publication_id == publication.id,
                PublicationAttempt.attempt == attempt_number,
                PublicationAttempt.stage == stage,
            )
        )
        if existing is not None:
            return
        self._session.add(
            PublicationAttempt(
                workspace_id=publication.workspace_id,
                publication_id=publication.id,
                attempt=attempt_number,
                stage=stage,
                provider_request_id=provider_request_id,
                byte_checkpoint=byte_checkpoint,
                request_metadata={"operation": stage},
                response_metadata=response_metadata,
                error_code=error_code,
                error_message=error_message,
                started_at=now,
                finished_at=now,
                created_at=now,
            )
        )
