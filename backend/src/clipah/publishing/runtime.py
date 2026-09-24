"""The worker side of publishing: what moves an approved Publication to its provider.

Confirming a publication only records intent: a Publication in ``preflighting`` and one
outbox message, or a ``scheduled`` Publication with a time. Three pieces of periodic and
queued work turn that intent into a video on the provider:

* :func:`relay` claims scheduled Publications that are due, then hands every outbox
  message to the delivery queue, one Workspace at a time.
* :func:`deliver` makes sure the approved bytes have a provider rendition, downloads them,
  and lets the dispatcher revalidate everything and run the provider driver.
* :func:`poll` asks the provider how each uploaded video is getting on, until it is
  published or refused.

Only identifiers cross the broker. Every step opens its own short tenant transaction,
except the upload itself, which holds one Social Account's lock for its whole length so
two uploads to one channel can never interleave.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.assets.ingest import HttpxSourceDownloader, SourceDownloader
from clipah.assets.storage import ObjectStore
from clipah.config import Settings
from clipah.db import RuntimeRole, session_scope
from clipah.jobs.admission import QuotaLedger
from clipah.models import (
    Publication,
    QuotaResource,
    RenderArtifact,
    SocialAccount,
    SocialRendition,
    Workspace,
)
from clipah.observability.logging import get_logger, log_context
from clipah.publishing.dispatcher import (
    DeliveryFailedError,
    DispatchOutcome,
    DispatchPolicy,
    FailureKind,
    PublicationDispatcher,
)
from clipah.publishing.models import PublicationStatus
from clipah.publishing.outbox import PublicationOutboxService
from clipah.publishing.preflight import publication_evidence_from_snapshots, render_artifact_media
from clipah.publishing.profiles import ProviderProfile, profile_for
from clipah.publishing.providers.youtube.adapter import YouTubePolicy, YouTubePublisher
from clipah.publishing.providers.youtube.delivery import (
    InProcessUploadVault,
    LocalMedia,
    YouTubeDeliveryDriver,
)
from clipah.publishing.providers.youtube.publications import (
    YouTubePublicationCoordinator,
    YouTubePublicationInvalidError,
)
from clipah.publishing.providers.youtube.resumable import YouTubeUploadContext
from clipah.publishing.providers.youtube.status import YouTubeProviderError, YouTubeStatus
from clipah.publishing.render_tasks import RenderedRendition
from clipah.publishing.renditions import (
    RenditionIntegrityError,
    RenditionPreflightError,
    ensure_social_rendition,
)
from clipah.publishing.scheduler import PublicationScheduler
from clipah.publishing.state_machine import transition
from clipah.publishing.tasks import bind_publication_rendition
from clipah.social_accounts.models import SocialProvider
from clipah.social_accounts.oauth import (
    ProviderPolicy,
    SocialOAuthProvider,
    SocialProviderUnavailableError,
    provider_policy,
)
from clipah.social_accounts.secrets import SocialSecretStore, social_secret_store_for
from clipah.social_accounts.use_cases import (
    SocialAccountReconnectRequiredError,
    SocialAccountService,
    SocialSecretBackendUnavailableError,
)
from clipah.social_accounts.youtube_provider import YOUTUBE_API_ORIGIN, YouTubeOAuthProvider
from clipah.workspaces.authorization import DatabaseWorkspaceAuthorizer
from clipah.workspaces.models import (
    WorkspaceAction,
    WorkspaceNotFoundError,
    WorkspacePermissionError,
)

# Publishing work is started by a schedule, not by a person. Row-level security still
# asks every transaction for an actor, so the relay and the poller declare this fixed
# identifier: it belongs to no User and grants nothing. Authority to publish is always
# re-proven from the approving member's live Membership.
PUBLISHING_ACTOR_ID = UUID("00000000-0000-4000-8000-000000000002")
RELAY_BATCH = 20
PREFLIGHT_TOPIC = "publication.preflight"
UPLOAD_TIMEOUT_SECONDS = 120.0
_DOWNLOAD_TTL = timedelta(minutes=5)
_logger = get_logger(__name__)

PublisherFactory = Callable[
    [YouTubePolicy, YouTubeUploadContext | None, InProcessUploadVault | None], YouTubePublisher
]


@dataclass(frozen=True, slots=True)
class PublishingRuntime:
    """Everything outside the database that publishing work needs, injected once."""

    settings: Settings
    store: ObjectStore
    secret_store: SocialSecretStore
    oauth_providers: Mapping[SocialProvider, SocialOAuthProvider]
    oauth_policies: Mapping[SocialProvider, ProviderPolicy]
    publishers: PublisherFactory
    downloader: SourceDownloader


def production_runtime(settings: Settings, *, store: ObjectStore) -> PublishingRuntime:
    """Compose the runtime this worker's settings describe, refusing a half-configured one."""
    secret_store = social_secret_store_for(settings)
    if secret_store is None:
        raise RuntimeError("publishing requires a configured social secret store")
    secret = settings.youtube_oauth_client_secret
    if (
        settings.youtube_oauth_client_id is None
        or secret is None
        or settings.youtube_oauth_redirect_uri is None
    ):
        raise RuntimeError("YouTube publishing requires its OAuth client")
    oauth = YouTubeOAuthProvider(
        client_id=settings.youtube_oauth_client_id,
        client_secret=secret.get_secret_value(),
        audit_approved=settings.youtube_audit_approved,
        clock=_utc_now,
    )
    policy = provider_policy(
        SocialProvider.YOUTUBE,
        client_id=settings.youtube_oauth_client_id,
        redirect_uri=settings.youtube_oauth_redirect_uri,
        api_version=settings.youtube_api_version,
    )

    def publishers(
        youtube_policy: YouTubePolicy,
        context: YouTubeUploadContext | None,
        vault: InProcessUploadVault | None,
    ) -> YouTubePublisher:
        return YouTubePublisher(
            client=httpx.Client(timeout=UPLOAD_TIMEOUT_SECONDS),
            policy=youtube_policy,
            api_origin=YOUTUBE_API_ORIGIN,
            checkpoint_vault=vault,
            upload_context=context,
        )

    return PublishingRuntime(
        settings=settings,
        store=store,
        secret_store=secret_store,
        oauth_providers={SocialProvider.YOUTUBE: oauth},
        oauth_policies={SocialProvider.YOUTUBE: policy},
        publishers=publishers,
        downloader=HttpxSourceDownloader(),
    )


def workspace_ids(settings: Settings) -> tuple[UUID, ...]:
    """List the Workspaces periodic publishing work visits."""
    with session_scope(settings=settings, runtime_role=RuntimeRole.WORKER) as session:
        return tuple(session.scalars(select(Workspace.id).order_by(Workspace.created_at)))


def relay(runtime: PublishingRuntime, *, now: datetime, send: Callable[[UUID, UUID], None]) -> int:
    """Claim due scheduled Publications and hand every due outbox message to delivery.

    A message is acknowledged in the same transaction that sent it. Sending twice is
    harmless — the dispatcher only delivers a Publication that is still ready, under the
    account's lock — while acknowledging without sending would strand it.
    """
    sent = 0
    for workspace_id in workspace_ids(runtime.settings):
        with _tenant(runtime.settings, workspace_id) as session:
            PublicationScheduler(session).claim_due(now=now, limit=RELAY_BATCH)
            outbox = PublicationOutboxService(session)
            for message in outbox.pending(now=now, limit=RELAY_BATCH):
                if message.topic == PREFLIGHT_TOPIC:
                    send(workspace_id, message.publication_id)
                    sent += 1
                outbox.acknowledge(message_id=message.id, delivered_at=now)
    return sent


def deliver(
    runtime: PublishingRuntime,
    *,
    workspace_id: UUID,
    publication_id: UUID,
    now: datetime,
    workspace: Path,
) -> DispatchOutcome | None:
    """Carry one Publication from ready to uploaded, or record why it could not be."""
    with log_context(workspaceId=workspace_id, publicationId=publication_id):
        ready = _prepare(
            runtime,
            workspace_id=workspace_id,
            publication_id=publication_id,
            now=now,
            workspace=workspace,
        )
        if ready is None:
            return None
        media = _download(runtime, ready, workspace=workspace)
        with _tenant(runtime.settings, workspace_id) as session:
            dispatcher = PublicationDispatcher(
                session,
                drivers={
                    SocialProvider.YOUTUBE: YouTubeDeliveryDriver(
                        media=media,
                        tokens=_token_source(runtime),
                        publishers=lambda policy, context, vault: runtime.publishers(
                            policy, context, vault
                        ),
                        audit_approved=runtime.settings.youtube_audit_approved,
                    )
                },
                quota=QuotaLedger(
                    session,
                    limits={
                        QuotaResource.SOCIAL_PUBLICATIONS: (
                            runtime.settings.monthly_social_publications
                        )
                    },
                ),
                policy=DispatchPolicy(
                    youtube_audit_approved=runtime.settings.youtube_audit_approved
                ),
            )
            result = dispatcher.dispatch(
                workspace_id=workspace_id, publication_id=publication_id, now=now
            )
        _logger.info("publication.delivery.finished", outcome=result.outcome.value)
        return result.outcome


def poll(runtime: PublishingRuntime, *, now: datetime) -> int:
    """Ask YouTube how every uploaded video is getting on, and record what it says."""
    applied = 0
    tokens = _token_source(runtime)
    for workspace_id in workspace_ids(runtime.settings):
        with _tenant(runtime.settings, workspace_id) as session:
            waiting = session.scalars(
                select(Publication)
                .join(
                    SocialAccount,
                    (SocialAccount.workspace_id == Publication.workspace_id)
                    & (SocialAccount.id == Publication.social_account_id),
                )
                .where(
                    SocialAccount.provider == SocialProvider.YOUTUBE,
                    Publication.status == PublicationStatus.PROCESSING,
                    Publication.provider_publication_id.is_not(None),
                )
                .order_by(Publication.processing_at, Publication.id)
                .limit(RELAY_BATCH)
            ).all()
            for publication in waiting:
                applied += _poll_one(runtime, session, publication, tokens=tokens, now=now)
    return applied


@dataclass(frozen=True, slots=True)
class _ReadyRendition:
    """The provider bytes one Publication is bound to, located but not yet fetched."""

    storage_key: str
    size_bytes: int
    sha256: bytes


def _prepare(
    runtime: PublishingRuntime,
    *,
    workspace_id: UUID,
    publication_id: UUID,
    now: datetime,
    workspace: Path,
) -> _ReadyRendition | None:
    """Make a due retry ready again and bind the Publication to its provider rendition."""
    with _tenant(runtime.settings, workspace_id) as session:
        publication = session.scalar(
            select(Publication)
            .where(Publication.workspace_id == workspace_id, Publication.id == publication_id)
            .with_for_update()
        )
        if publication is None:
            return None
        _reopen_due_retry(publication, now=now)
        if publication.status is not PublicationStatus.PREFLIGHTING:
            return None
        if publication.social_rendition_id is None:
            try:
                _bind_rendition(runtime, session, publication, now=now, workspace=workspace)
            except (RenditionPreflightError, RenditionIntegrityError):
                publication.status = transition(
                    current=publication.status, target=PublicationStatus.PERMANENT_FAILED
                ).current
                publication.normalized_error_code = "rendition_unavailable"
                publication.sanitized_error_message = (
                    "This export cannot be sent to this platform as it is."
                )
                publication.failed_at = now
                return None
        rendition = session.scalar(
            select(SocialRendition).where(
                SocialRendition.workspace_id == workspace_id,
                SocialRendition.id == publication.social_rendition_id,
            )
        )
        if rendition is None:
            return None
        return _ReadyRendition(
            storage_key=rendition.storage_key,
            size_bytes=rendition.size_bytes,
            sha256=bytes(rendition.output_sha256),
        )


def _reopen_due_retry(publication: Publication, *, now: datetime) -> None:
    """Return a retryable failure whose wait is over to the ready state, as one new attempt.

    An upload whose outcome YouTube could not confirm is left alone: sending it again could
    publish the video twice, so it waits for reconciliation instead.
    """
    if publication.status is not PublicationStatus.RETRYABLE_FAILED:
        return
    checkpoint = publication.checkpoint_metadata or {}
    if checkpoint.get("ambiguous") is True and checkpoint.get("reconciled") is not True:
        return
    if publication.next_attempt_at is not None and publication.next_attempt_at > now:
        return
    publication.status = transition(
        current=publication.status, target=PublicationStatus.PREFLIGHTING
    ).current
    publication.attempt_count += 1
    publication.next_attempt_at = None
    publication.normalized_error_code = None
    publication.sanitized_error_message = None


def _bind_rendition(
    runtime: PublishingRuntime,
    session: Session,
    publication: Publication,
    *,
    now: datetime,
    workspace: Path,
) -> None:
    """Reuse the approved export as the provider rendition, which it already satisfies."""
    artifact = session.scalar(
        select(RenderArtifact).where(
            RenderArtifact.workspace_id == publication.workspace_id,
            RenderArtifact.id == publication.render_artifact_id,
        )
    )
    account = session.scalar(
        select(SocialAccount).where(
            SocialAccount.workspace_id == publication.workspace_id,
            SocialAccount.id == publication.social_account_id,
        )
    )
    if artifact is None or account is None or artifact.sha256 is None:
        raise RenditionIntegrityError("the approved export is unavailable")
    profile = profile_for(account.provider)
    rendition = ensure_social_rendition(
        session,
        artifact=artifact,
        provider=account.provider,
        profile=profile,
        master_media=render_artifact_media(
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
        store=runtime.store,
        downloader=runtime.downloader,
        renderer=_RefusingRenderer(),
        workspace=workspace,
        cancellation_check=lambda: None,
        now=now,
    )
    bind_publication_rendition(
        session,
        workspace_id=publication.workspace_id,
        publication_id=publication.id,
        rendition_id=rendition.id,
    )


class _RefusingRenderer:
    """Refuse to re-encode: only an export that already fits the provider is published.

    The dispatcher sends exactly the bytes a member approved, so a re-encoded file could
    never pass its checksum proof. An export that does not fit fails honestly instead.
    """

    def render(
        self,
        *,
        profile: ProviderProfile,
        source: Path,
        workspace: Path,
        cancellation_check: Callable[[], None],
    ) -> RenderedRendition:
        """Refuse every request."""
        del profile, source, workspace, cancellation_check
        raise RenditionPreflightError("this export would need re-encoding for the provider")


def _download(runtime: PublishingRuntime, ready: _ReadyRendition, *, workspace: Path) -> LocalMedia:
    """Fetch the rendition once and prove it is exactly the bytes that were approved."""
    signed = runtime.store.sign_download(key=ready.storage_key, expires_in=_DOWNLOAD_TTL)
    path = workspace / "publication.mp4"
    with path.open("wb") as handle:
        downloaded = runtime.downloader.download(
            signed.url,
            handle,
            expected_size=ready.size_bytes,
            max_bytes=ready.size_bytes,
            cancellation_check=lambda: None,
        )
    if downloaded.size_bytes != ready.size_bytes or downloaded.sha256 != ready.sha256:
        raise RenditionIntegrityError("downloaded rendition does not match its checksum")
    return LocalMedia(
        path=path, content_type="video/mp4", size_bytes=ready.size_bytes, sha256=ready.sha256
    )


def _token_source(
    runtime: PublishingRuntime,
) -> Callable[[Session, Publication, datetime], SecretStr]:
    """Borrow the approving member's authority to fetch a live token for one Publication."""

    def token(session: Session, publication: Publication, now: datetime) -> SecretStr:
        if publication.approved_by_user_id is None:
            raise DeliveryFailedError(code="approval_missing", kind=FailureKind.PERMANENT)
        try:
            access = DatabaseWorkspaceAuthorizer(session).require(
                user_id=publication.approved_by_user_id,
                workspace_id=publication.workspace_id,
                action=WorkspaceAction.PUBLISH,
            )
            service = SocialAccountService(
                session,
                providers=runtime.oauth_providers,
                policies=runtime.oauth_policies,
                store=runtime.secret_store,
            )
            return service.access_token(
                access=access,
                social_account_id=publication.social_account_id,
                now=now,
                request_id=f"publication-{publication.id}",
            )
        except SocialAccountReconnectRequiredError:
            raise DeliveryFailedError(
                code="social_account_reconnect_required", kind=FailureKind.RECONNECT
            ) from None
        except (SocialProviderUnavailableError, SocialSecretBackendUnavailableError):
            raise DeliveryFailedError(
                code="oauth_unavailable", kind=FailureKind.RETRYABLE
            ) from None
        except (WorkspaceNotFoundError, WorkspacePermissionError):
            raise DeliveryFailedError(
                code="publish_authority_revoked", kind=FailureKind.PERMANENT
            ) from None

    return token


def _poll_one(
    runtime: PublishingRuntime,
    session: Session,
    publication: Publication,
    *,
    tokens: Callable[[Session, Publication, datetime], SecretStr],
    now: datetime,
) -> int:
    """Record one video's current YouTube state; a failed look is simply tried next time."""
    video_id = publication.provider_publication_id
    if video_id is None:
        return 0
    try:
        token = tokens(session, publication, now)
        publisher = runtime.publishers(
            YouTubePolicy(audit_approved=runtime.settings.youtube_audit_approved, now=now),
            None,
            None,
        )
        status = publisher.poll(provider_id=video_id, access_token=token)
        if not isinstance(status, YouTubeStatus):
            return 0
        YouTubePublicationCoordinator(session).apply_status(
            workspace_id=publication.workspace_id,
            publication_id=publication.id,
            status=status,
            poll_sequence=int(now.timestamp()),
            now=now,
        )
    except (DeliveryFailedError, YouTubeProviderError, YouTubePublicationInvalidError) as error:
        _logger.warning(
            "publication.poll.skipped",
            publicationId=str(publication.id),
            code=getattr(error, "code", type(error).__name__),
        )
        return 0
    return 1


@contextmanager
def _tenant(settings: Settings, workspace_id: UUID) -> Iterator[Session]:
    """Open one short worker transaction confined to one Workspace."""
    with session_scope(
        settings=settings,
        workspace_id=workspace_id,
        user_id=PUBLISHING_ACTOR_ID,
        runtime_role=RuntimeRole.WORKER,
    ) as session:
        yield session


def _utc_now() -> datetime:
    """Return an aware UTC instant."""
    return datetime.now(tz=UTC)
