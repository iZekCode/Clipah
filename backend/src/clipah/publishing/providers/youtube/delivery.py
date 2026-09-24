"""Deliver one approved Publication to YouTube: the destination driver the dispatcher calls.

By the time this runs the dispatcher has revalidated authority, the connection, the
approval, and the frozen evidence, and holds the Social Account's lock and one unit of
publishing quota. What is left is the provider conversation: prove the grant still names
the approved channel, open a resumable upload, send the frozen bytes, and record the video
YouTube created. Every provider failure leaves as the dispatcher's own
:class:`DeliveryFailedError`, so retry policy is decided in one place.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.models import Publication, SocialAccount
from clipah.publishing.dispatcher import (
    DeliveryFailedError,
    FailureKind,
    failure_from_provider_error,
)
from clipah.publishing.providers.youtube.adapter import (
    YouTubeAuditRestrictionError,
    YouTubeChannelMismatchError,
    YouTubePolicy,
    YouTubePrivacy,
    YouTubePublisher,
    YouTubePublishRequest,
)
from clipah.publishing.providers.youtube.publications import YouTubePublicationCoordinator
from clipah.publishing.providers.youtube.resumable import (
    UploadCheckpoint,
    UploadProgress,
    YouTubeResumableError,
    YouTubeUploadContext,
)
from clipah.publishing.providers.youtube.status import YouTubeProviderError

# YouTube requires a category and at least one tag. People & Blogs is the category YouTube
# itself defaults an upload to, and "Shorts" is the one tag every Short honestly carries.
DEFAULT_CATEGORY_ID = "22"
DEFAULT_TAGS = ("Shorts",)
# How many chunks are sent between two durable progress records.
CHECKPOINT_EVERY_CHUNKS = 16


class InProcessUploadVault:
    """Hold resumable-session URIs in this process only, never in a database or a log.

    A session URI is a bearer capability for the upload. One delivery runs start to end
    in one worker process, so nothing outside it ever needs the URI; a worker that dies
    loses it, and the next attempt opens a new session and sends the bytes again.
    """

    def __init__(self) -> None:
        """Start with no sessions."""
        self._sessions: dict[str, tuple[YouTubeUploadContext, SecretStr]] = {}

    def store(self, context: YouTubeUploadContext, session_uri: SecretStr) -> str:
        """Keep one URI for exactly one attempt and return a reference that reveals nothing."""
        reference = f"process:{secrets.token_urlsafe(24)}"
        self._sessions[reference] = (context, session_uri)
        return reference

    @contextmanager
    def lease(self, reference: str, context: YouTubeUploadContext) -> Iterator[SecretStr]:
        """Lend the URI back only to the attempt that stored it."""
        entry = self._sessions.get(reference)
        if entry is None or entry[0] != context:
            raise YouTubeResumableError("upload session is not available to this attempt")
        yield entry[1]


@dataclass(frozen=True, slots=True)
class LocalMedia:
    """The approved bytes, already downloaded and checked, read back range by range."""

    path: Path
    content_type: str
    size_bytes: int
    sha256: bytes

    def read_range(self, start: int, end: int) -> bytes:
        """Read the half-open byte range ``start:end`` from the local copy."""
        with self.path.open("rb") as handle:
            handle.seek(start)
            return handle.read(end - start)


TokenSource = Callable[[Session, Publication, datetime], SecretStr]
PublisherFactory = Callable[
    [YouTubePolicy, YouTubeUploadContext, InProcessUploadVault], YouTubePublisher
]


class YouTubeDeliveryDriver:
    """Upload one Publication's frozen bytes to the channel it was approved for."""

    def __init__(
        self,
        *,
        media: LocalMedia,
        tokens: TokenSource,
        publishers: PublisherFactory,
        audit_approved: bool,
    ) -> None:
        """Bind the downloaded bytes, a token source, and the deployment's audit state."""
        self._media = media
        self._tokens = tokens
        self._publishers = publishers
        self._audit_approved = audit_approved

    def deliver(self, *, session: Session, publication: Publication, now: datetime) -> None:
        """Send the video, or raise the dispatcher's failure naming why it was not sent."""
        account = session.scalar(
            select(SocialAccount).where(
                SocialAccount.workspace_id == publication.workspace_id,
                SocialAccount.id == publication.social_account_id,
            )
        )
        if account is None:
            raise DeliveryFailedError(code="social_account_missing", kind=FailureKind.RECONNECT)
        if bytes(publication.artifact_sha256) != self._media.sha256:
            raise DeliveryFailedError(
                code="rendition_checksum_mismatch", kind=FailureKind.PERMANENT
            )
        request = publish_request(publication)
        token = self._tokens(session, publication, now)
        policy = YouTubePolicy(audit_approved=self._audit_approved, now=now)
        context = YouTubeUploadContext(
            workspace_id=publication.workspace_id,
            publication_id=publication.id,
            attempt=publication.attempt_count + 1,
        )
        vault = InProcessUploadVault()
        publisher = self._publishers(policy, context, vault)
        coordinator = YouTubePublicationCoordinator(session)
        try:
            publisher.confirm_destination(
                access_token=token, expected_account_id=account.external_account_id
            )
            checkpoint = publisher.begin(request=request, media=self._media, access_token=token)
            self._save(coordinator, publication, checkpoint, now=now)
            checkpoint = self._send(publisher, coordinator, publication, checkpoint, token, now)
        except YouTubeChannelMismatchError:
            raise DeliveryFailedError(
                code="youtube_channel_mismatch", kind=FailureKind.RECONNECT
            ) from None
        except YouTubeAuditRestrictionError:
            raise DeliveryFailedError(
                code="youtube_audit_restriction", kind=FailureKind.PERMANENT
            ) from None
        except YouTubeResumableError as error:
            raise DeliveryFailedError(
                code="youtube_upload_interrupted", kind=FailureKind.RETRYABLE
            ) from error
        except YouTubeProviderError as error:
            raise failure_from_provider_error(error) from None
        video_id = checkpoint.provider_video_id
        if video_id is None:
            raise DeliveryFailedError(code="youtube_upload_interrupted", kind=FailureKind.RETRYABLE)
        coordinator.record_video_created(
            workspace_id=publication.workspace_id,
            publication_id=publication.id,
            video_id=video_id,
            provider_request_id=None,
            now=now,
        )

    def _send(
        self,
        publisher: YouTubePublisher,
        coordinator: YouTubePublicationCoordinator,
        publication: Publication,
        checkpoint: UploadCheckpoint,
        token: SecretStr,
        now: datetime,
    ) -> UploadCheckpoint:
        """Send chunks until YouTube names the video, reconciling any ambiguous request."""
        chunks = 0
        while checkpoint.progress is UploadProgress.ACTIVE:
            checkpoint = publisher.transfer(
                checkpoint=checkpoint,
                read_range=self._media.read_range,
                access_token=token,
            )
            if checkpoint.final_request_ambiguous:
                checkpoint = publisher.query_upload(checkpoint=checkpoint, access_token=token)
            if checkpoint.progress is UploadProgress.EXPIRED_RESTARTABLE:
                raise YouTubeResumableError("YouTube expired the upload session")
            chunks += 1
            if chunks % CHECKPOINT_EVERY_CHUNKS == 0:
                self._save(coordinator, publication, checkpoint, now=now)
        self._save(coordinator, publication, checkpoint, now=now)
        return checkpoint

    @staticmethod
    def _save(
        coordinator: YouTubePublicationCoordinator,
        publication: Publication,
        checkpoint: UploadCheckpoint,
        *,
        now: datetime,
    ) -> None:
        """Record safe upload progress on the Publication and its attempt history."""
        coordinator.save_upload_checkpoint(
            workspace_id=publication.workspace_id,
            publication_id=publication.id,
            secret_reference=checkpoint.secret_reference,
            checkpoint=checkpoint,
            now=now,
        )


def publish_request(publication: Publication) -> YouTubePublishRequest:
    """Build the frozen YouTube request from what the member approved, and nothing else.

    The composer records visibility as ``privacyStatus``; older drafts said ``privacy``.
    Anything the approved snapshot does not say falls back to the most private reading.
    """
    metadata = publication.metadata_snapshot
    options = publication.provider_options
    tags = metadata.get("tags")
    try:
        return YouTubePublishRequest(
            title=str(metadata.get("title") or "").strip()[:100],
            description=str(metadata.get("description") or ""),
            tags=(
                tuple(str(tag) for tag in tags if str(tag).strip())
                if isinstance(tags, list) and any(str(tag).strip() for tag in tags)
                else DEFAULT_TAGS
            ),
            category_id=str(metadata.get("categoryId") or DEFAULT_CATEGORY_ID),
            made_for_kids=options.get("selfDeclaredMadeForKids") is True,
            contains_synthetic_media=options.get("containsSyntheticMedia") is True,
            requested_privacy=YouTubePrivacy(
                options.get("privacyStatus") or options.get("privacy") or "private"
            ),
            scheduled_for=publication.scheduled_for,
        )
    except (ValidationError, ValueError):
        raise DeliveryFailedError(
            code="youtube_metadata_invalid", kind=FailureKind.PERMANENT
        ) from None
