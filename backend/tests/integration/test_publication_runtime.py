"""Real-Postgres contracts for the worker side of publishing to YouTube.

The provider is a scripted YouTube behind ``httpx.MockTransport`` and storage is the
in-memory fake, so these tests prove the whole path a confirmed Publication takes —
relay, rendition, download, grant, resumable upload, and status — without a network.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from clipah.assets.ingest import DownloadedSource
from clipah.assets.storage import FakeObjectStore
from clipah.db import RuntimeRole
from clipah.models import (
    OAuthGrant,
    Publication,
    PublicationAttempt,
    PublicationOutbox,
    SocialAccount,
    WorkspaceQuotaReservation,
)
from clipah.publishing.dispatcher import DispatchOutcome
from clipah.publishing.models import PublicationStatus
from clipah.publishing.providers.youtube.adapter import YouTubePolicy, YouTubePublisher
from clipah.publishing.providers.youtube.delivery import InProcessUploadVault
from clipah.publishing.providers.youtube.resumable import YouTubeUploadContext
from clipah.publishing.runtime import PublishingRuntime, deliver, poll, relay
from clipah.social_accounts.models import OAuthGrantMaterial, SocialProvider
from clipah.social_accounts.oauth import SocialProviderGrantRejectedError, provider_policy
from clipah.social_accounts.secrets import LocalSocialSecretStore
from clipah.social_accounts.use_cases import _encode_material, _replace_grant, _secret_context
from integration.test_publications import _seed_publication
from support import runtime_settings

pytestmark = pytest.mark.integration

NOW = datetime.now(tz=UTC).replace(microsecond=0)
VIDEO = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 1200
VIDEO_SHA = hashlib.sha256(VIDEO).digest()
RENDER_KEY = "workspaces/render-under-test.mp4"
ACCESS_TOKEN = "live-access-token-that-must-never-leak"
SESSION_URI = "https://upload.youtube.test/session/abc"


@dataclass
class FakeYouTube:
    """A scripted YouTube Data API that remembers what it was asked."""

    channel_id: str
    processing: str = "succeeded"
    begin_status: int = 200
    received: bytearray = field(default_factory=bytearray)
    metadata: dict[str, Any] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer one request the way YouTube would."""
        assert request.headers["Authorization"] == f"Bearer {ACCESS_TOKEN}"
        self.calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/youtube/v3/channels":
            return httpx.Response(200, json={"items": [{"id": self.channel_id}]})
        if request.url.path == "/upload/youtube/v3/videos":
            self.metadata = json.loads(request.content)
            if self.begin_status != 200:
                return httpx.Response(self.begin_status, json={"error": {"errors": []}})
            return httpx.Response(200, headers={"Location": SESSION_URI})
        if str(request.url) == SESSION_URI:
            self.received.extend(request.content)
            if len(self.received) < len(VIDEO):
                return httpx.Response(308, headers={"Range": f"bytes=0-{len(self.received) - 1}"})
            return httpx.Response(200, json={"id": "video-123"})
        if request.url.path == "/youtube/v3/videos":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "video-123",
                            "status": {"uploadStatus": "processed", "privacyStatus": "private"},
                            "processingDetails": {"processingStatus": self.processing},
                        }
                    ]
                },
            )
        return httpx.Response(404)


class FakeDownloader:
    """Stream bodies the fake store holds, the way a signed URL would."""

    def __init__(self, store: FakeObjectStore) -> None:
        self._store = store

    def download(
        self,
        url: str,
        destination: BinaryIO,
        *,
        expected_size: int | None,
        max_bytes: int,
        cancellation_check: Callable[[], None],
    ) -> DownloadedSource:
        """Copy one stored body into the destination and report what was copied."""
        del expected_size, max_bytes, cancellation_check
        body = self._store.object_bodies[url.removeprefix("fake://download/")]
        destination.write(body)
        return DownloadedSource(size_bytes=len(body), sha256=hashlib.sha256(body).digest())


class RejectingRefresh:
    """A Google that has forgotten the grant: every refresh is refused."""

    def refresh(self, *, grant: OAuthGrantMaterial) -> Any:
        """Refuse, as Google does for a revoked or expired refresh token."""
        del grant
        raise SocialProviderGrantRejectedError("revoked")


def _runtime(
    youtube: FakeYouTube, store: FakeObjectStore, *, oauth: Any = None
) -> PublishingRuntime:
    """A runtime whose YouTube, storage, and key are all local to the test."""
    settings = runtime_settings(RuntimeRole.WORKER, monthly_social_publications=100)

    def publishers(
        policy: YouTubePolicy,
        context: YouTubeUploadContext | None,
        vault: InProcessUploadVault | None,
    ) -> YouTubePublisher:
        return YouTubePublisher(
            client=httpx.Client(transport=httpx.MockTransport(youtube)),
            policy=policy,
            api_origin="https://www.googleapis.com",
            checkpoint_vault=vault,
            upload_context=context,
        )

    return PublishingRuntime(
        settings=settings,
        store=store,
        secret_store=SECRET_STORE,
        oauth_providers={} if oauth is None else {SocialProvider.YOUTUBE: oauth},
        oauth_policies={
            SocialProvider.YOUTUBE: provider_policy(
                SocialProvider.YOUTUBE,
                client_id="client",
                redirect_uri="http://localhost/cb",
                api_version="v3",
            )
        },
        publishers=publishers,
        downloader=FakeDownloader(store),
    )


SECRET_STORE = LocalSocialSecretStore(key=b"k" * 32, key_reference="test", key_version=1)


def _ready(
    engine: Engine,
    *,
    suffix: str,
    title: str = "Karier tanpa drama",
    token_expires_at: datetime | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Seed one confirmed YouTube Publication, its grant, and one outbox message."""
    seed = _seed_publication(
        engine, suffix=suffix, render_bytes=VIDEO, render_key=RENDER_KEY, render_duration_ms=20_000
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE publications
                SET status = 'preflighting', artifact_sha256 = :sha,
                    approved_by_user_id = :user, approved_at = :now,
                    capability_version = 'cap-v1',
                    metadata_snapshot = CAST(:metadata AS jsonb),
                    provider_options = CAST(:options AS jsonb),
                    consent_snapshot = '{"confirmed": true}',
                    checkpoint_metadata = '{"preflightCapabilityVersion": "cap-v1"}'
                WHERE id = :publication
                """
            ),
            {
                "sha": VIDEO_SHA,
                "user": seed["user"],
                "now": NOW,
                "publication": seed["publication"],
                "metadata": json.dumps({"title": title, "description": "Clip"}),
                "options": json.dumps(
                    {
                        "privacyStatus": "public",
                        "selfDeclaredMadeForKids": False,
                        "containsSyntheticMedia": False,
                        **options,
                    }
                ),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO publication_outbox
                    (id, workspace_id, publication_id, topic, payload, operation_key,
                     available_at, attempt_count, created_at)
                VALUES (:id, :workspace, :publication, 'publication.preflight', '{}',
                        :operation, :now, 0, :now)
                """
            ),
            {
                "id": uuid4(),
                "workspace": seed["workspace"],
                "publication": seed["publication"],
                "operation": f"publication:{seed['publication']}:1:attempt:0",
                "now": NOW - timedelta(seconds=1),
            },
        )
    with Session(engine) as session, session.begin():
        account = session.get(SocialAccount, seed["account"])
        assert account is not None
        grant = OAuthGrant(
            id=uuid4(), workspace_id=seed["workspace"], social_account_id=account.id, created_at=NOW
        )
        material = OAuthGrantMaterial(
            access_token=ACCESS_TOKEN,
            refresh_token="refresh",
            access_token_expires_at=token_expires_at or NOW + timedelta(hours=1),
        )
        _replace_grant(
            grant,
            encrypted=SECRET_STORE.encrypt(
                _encode_material(material),
                context=_secret_context(account=account, grant_id=grant.id, token_version=1),
            ),
            access_token_expires_at=material.access_token_expires_at,
            refresh_token_expires_at=None,
            granted_scopes=frozenset({"youtube.upload"}),
            token_version=1,
            refreshed_at=None,
        )
        session.add(grant)
        external = account.external_account_id
    return {**seed, "external": external}


def _publication(engine: Engine, publication_id: UUID) -> Publication:
    """Read one Publication as it now stands."""
    with Session(engine, expire_on_commit=False) as session:
        publication = session.get(Publication, publication_id)
        assert publication is not None
        return publication


def _store() -> FakeObjectStore:
    """A fake store already holding the approved export's bytes."""
    store = FakeObjectStore(now=lambda: NOW)
    store.put_file(key=RENDER_KEY, content_type="video/mp4", file=io.BytesIO(VIDEO))
    return store


def test_a_confirmed_publication_is_relayed_uploaded_and_then_published(
    engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    """The whole path a confirmed Short takes, ending on YouTube with the approved bytes."""
    del clean_database
    seed = _ready(engine, suffix="runtime-happy")
    youtube = FakeYouTube(channel_id=seed["external"])
    runtime = _runtime(youtube, _store())
    sent: list[tuple[UUID, UUID]] = []

    assert relay(runtime, now=NOW, send=lambda ws, pub: sent.append((ws, pub))) == 1
    assert sent == [(seed["workspace"], seed["publication"])]
    assert relay(runtime, now=NOW, send=lambda ws, pub: sent.append((ws, pub))) == 0

    outcome = deliver(
        runtime,
        workspace_id=seed["workspace"],
        publication_id=seed["publication"],
        now=NOW,
        workspace=tmp_path,
    )

    assert outcome is DispatchOutcome.DELIVERED
    assert bytes(youtube.received) == VIDEO
    assert youtube.metadata["snippet"]["title"] == "Karier tanpa drama"
    # An unaudited API project may only upload privately, whatever the member asked for.
    assert youtube.metadata["status"]["privacyStatus"] == "private"
    uploaded = _publication(engine, seed["publication"])
    assert uploaded.status is PublicationStatus.PROCESSING
    assert uploaded.provider_publication_id == "video-123"
    assert uploaded.provider_permalink == "https://www.youtube.com/watch?v=video-123"
    assert SESSION_URI not in json.dumps(uploaded.checkpoint_metadata)
    assert uploaded.encrypted_checkpoint_reference is not None
    assert SESSION_URI not in uploaded.encrypted_checkpoint_reference

    assert poll(runtime, now=NOW + timedelta(minutes=1)) == 1

    published = _publication(engine, seed["publication"])
    assert published.status is PublicationStatus.PUBLISHED
    with Session(engine) as session:
        stages = session.scalars(
            select(PublicationAttempt.stage).where(
                PublicationAttempt.publication_id == seed["publication"]
            )
        ).all()
        reservation = session.scalars(
            select(WorkspaceQuotaReservation.status).where(
                WorkspaceQuotaReservation.reference_id == seed["publication"]
            )
        ).one()
    assert "youtube_begin" in stages and "youtube_complete" in stages
    assert reservation.value == "settled"
    assert ACCESS_TOKEN not in json.dumps([str(stage) for stage in stages])


def test_a_video_youtube_is_still_processing_stays_processing(
    engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    """Only YouTube's own word moves a video to published."""
    del clean_database
    seed = _ready(engine, suffix="runtime-processing")
    youtube = FakeYouTube(channel_id=seed["external"], processing="processing")
    runtime = _runtime(youtube, _store())
    deliver(
        runtime,
        workspace_id=seed["workspace"],
        publication_id=seed["publication"],
        now=NOW,
        workspace=tmp_path,
    )

    poll(runtime, now=NOW + timedelta(minutes=1))

    assert _publication(engine, seed["publication"]).status is PublicationStatus.PROCESSING


def test_a_grant_for_another_channel_uploads_nothing(
    engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    """A reconnect to a different channel must not publish to it by accident."""
    del clean_database
    seed = _ready(engine, suffix="runtime-mismatch")
    youtube = FakeYouTube(channel_id="some-other-channel")
    runtime = _runtime(youtube, _store())

    outcome = deliver(
        runtime,
        workspace_id=seed["workspace"],
        publication_id=seed["publication"],
        now=NOW,
        workspace=tmp_path,
    )

    assert outcome is DispatchOutcome.RECONNECT_REQUIRED
    assert youtube.received == bytearray()
    assert not any("upload" in call for call in youtube.calls)
    refused = _publication(engine, seed["publication"])
    assert refused.status is PublicationStatus.RECONNECT_REQUIRED
    assert refused.normalized_error_code == "youtube_channel_mismatch"


def test_an_export_whose_bytes_changed_is_never_sent(
    engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    """The dispatcher sends exactly the approved bytes or nothing at all."""
    del clean_database
    seed = _ready(engine, suffix="runtime-tampered")
    youtube = FakeYouTube(channel_id=seed["external"])
    store = _store()
    store.object_bodies[RENDER_KEY] = VIDEO[:-1] + b"!"
    runtime = _runtime(youtube, store)

    with pytest.raises(Exception, match="checksum"):
        deliver(
            runtime,
            workspace_id=seed["workspace"],
            publication_id=seed["publication"],
            now=NOW,
            workspace=tmp_path,
        )

    assert youtube.calls == []


def test_a_publication_that_is_not_ready_is_left_alone(
    engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    """A second delivery of the same message must not upload the video twice."""
    del clean_database
    seed = _ready(engine, suffix="runtime-twice")
    youtube = FakeYouTube(channel_id=seed["external"])
    runtime = _runtime(youtube, _store())
    deliver(
        runtime,
        workspace_id=seed["workspace"],
        publication_id=seed["publication"],
        now=NOW,
        workspace=tmp_path,
    )
    uploads = youtube.calls.count("POST /upload/youtube/v3/videos")

    again = deliver(
        runtime,
        workspace_id=seed["workspace"],
        publication_id=seed["publication"],
        now=NOW,
        workspace=tmp_path,
    )

    assert again is None
    assert youtube.calls.count("POST /upload/youtube/v3/videos") == uploads == 1


def test_a_due_retry_is_reopened_as_a_new_attempt(
    engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    """A retryable failure whose wait is over is sent again, counted as one more attempt."""
    del clean_database
    seed = _ready(engine, suffix="runtime-retry")
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE publications SET status = 'retryable_failed', next_attempt_at = :due, "
                "normalized_error_code = 'youtube_unavailable' WHERE id = :publication"
            ),
            {"due": NOW - timedelta(seconds=1), "publication": seed["publication"]},
        )
    youtube = FakeYouTube(channel_id=seed["external"])

    outcome = deliver(
        _runtime(youtube, _store()),
        workspace_id=seed["workspace"],
        publication_id=seed["publication"],
        now=NOW,
        workspace=tmp_path,
    )

    assert outcome is DispatchOutcome.DELIVERED
    retried = _publication(engine, seed["publication"])
    assert retried.attempt_count == 1
    assert retried.normalized_error_code is None


def test_the_relay_marks_every_message_it_handed_on(engine: Engine, clean_database: None) -> None:
    """An acknowledged message is never handed on again, and none is left behind."""
    del clean_database
    seed = _ready(engine, suffix="runtime-ack")
    runtime = _runtime(FakeYouTube(channel_id=seed["external"]), _store())

    relay(runtime, now=NOW, send=lambda ws, pub: None)

    with Session(engine) as session:
        pending = session.scalars(
            select(PublicationOutbox).where(PublicationOutbox.delivered_at.is_(None))
        ).all()
    assert pending == []


def _deliver(runtime: PublishingRuntime, seed: dict[str, Any], tmp_path: Path) -> Any:
    """Deliver the seeded Publication once, now."""
    return deliver(
        runtime,
        workspace_id=seed["workspace"],
        publication_id=seed["publication"],
        now=NOW,
        workspace=tmp_path,
    )


def test_a_publication_without_a_title_fails_without_calling_youtube_twice(
    engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    """YouTube requires a title; a blank one can never succeed, so it is not retried."""
    del clean_database
    seed = _ready(engine, suffix="runtime-untitled", title="   ")
    youtube = FakeYouTube(channel_id=seed["external"])

    outcome = _deliver(_runtime(youtube, _store()), seed, tmp_path)

    assert outcome is DispatchOutcome.PERMANENT_FAILED
    failed = _publication(engine, seed["publication"])
    assert failed.normalized_error_code == "youtube_metadata_invalid"
    assert youtube.calls == []


def test_a_youtube_outage_is_retried_later_rather_than_failed(
    engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    """A 503 is YouTube's problem for now, not the member's clip."""
    del clean_database
    seed = _ready(engine, suffix="runtime-outage")
    youtube = FakeYouTube(channel_id=seed["external"], begin_status=503)

    outcome = _deliver(_runtime(youtube, _store()), seed, tmp_path)

    assert outcome is DispatchOutcome.RETRYABLE
    waiting = _publication(engine, seed["publication"])
    assert waiting.status is PublicationStatus.RETRYABLE_FAILED
    assert waiting.next_attempt_at is not None and waiting.next_attempt_at > NOW
    with Session(engine) as session:
        retry = session.scalars(
            select(PublicationOutbox.available_at).where(
                PublicationOutbox.publication_id == seed["publication"],
                PublicationOutbox.operation_key.endswith(":retry:1"),
            )
        ).all()
    assert retry == [waiting.next_attempt_at]


def test_an_expired_grant_google_refuses_to_refresh_asks_for_a_reconnect(
    engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    """Without a live token nothing is uploaded, and the member is told to reconnect."""
    del clean_database
    seed = _ready(engine, suffix="runtime-expired", token_expires_at=NOW - timedelta(minutes=1))
    youtube = FakeYouTube(channel_id=seed["external"])

    outcome = _deliver(_runtime(youtube, _store(), oauth=RejectingRefresh()), seed, tmp_path)

    assert outcome is DispatchOutcome.RECONNECT_REQUIRED
    assert youtube.calls == []
    with Session(engine) as session:
        account = session.get(SocialAccount, seed["account"])
        assert account is not None
        assert account.connection_status.value == "reconnect_required"
