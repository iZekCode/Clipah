"""Explicitly gated live private-upload smoke test for the YouTube sandbox."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from clipah.publishing.providers.youtube.adapter import (
    YouTubePolicy,
    YouTubePrivacy,
    YouTubePublisher,
    YouTubePublishRequest,
)
from clipah.publishing.providers.youtube.resumable import (
    UploadProgress,
    YouTubeUploadContext,
)
from clipah.publishing.providers.youtube.status import YouTubeOutcome, YouTubeStatus

pytestmark = pytest.mark.slow
SMOKE_ENABLED = os.getenv("CLIPAH_YOUTUBE_PUBLISH_SMOKE") == "1"


class _ProcessVault:
    """Keep the live session URI redacted and in memory for this one smoke process."""

    def __init__(self) -> None:
        """Start with no reusable provider material."""
        self._value: SecretStr | None = None

    def store(self, context: YouTubeUploadContext, session_uri: SecretStr) -> str:
        """Retain one URI without returning it as durable metadata."""
        del context
        self._value = session_uri
        return "memory://youtube-smoke-session"

    @contextmanager
    def lease(self, reference: str, context: YouTubeUploadContext) -> Iterator[SecretStr]:
        """Lease the process-local URI to the one active smoke attempt."""
        del context
        if reference != "memory://youtube-smoke-session" or self._value is None:
            raise RuntimeError("YouTube smoke session is unavailable")
        yield self._value


class _FileMedia:
    """Immutable caller-supplied private smoke fixture."""

    content_type = "video/mp4"

    def __init__(self, path: Path) -> None:
        """Measure the exact bytes once before provider work starts."""
        self._path = path
        body = path.read_bytes()
        self.size_bytes = len(body)
        self.sha256 = hashlib.sha256(body).digest()

    def read_range(self, start: int, end: int) -> bytes:
        """Read one requested half-open byte range from the frozen fixture."""
        with self._path.open("rb") as stream:
            stream.seek(start)
            return stream.read(end - start)


@pytest.mark.skipif(not SMOKE_ENABLED, reason="YouTube publish smoke is explicitly opt-in")
def test_private_youtube_upload_in_sandbox() -> None:
    """Live credentials may create only an explicitly private sandbox upload."""
    token = SecretStr(os.environ["CLIPAH_YOUTUBE_SANDBOX_ACCESS_TOKEN"])
    channel_id = os.environ["CLIPAH_YOUTUBE_SANDBOX_CHANNEL_ID"]
    media = _FileMedia(Path(os.environ["CLIPAH_YOUTUBE_SANDBOX_MEDIA_PATH"]))
    context = YouTubeUploadContext(
        workspace_id=UUID("00000000-0000-0000-0000-000000000001"),
        publication_id=UUID("00000000-0000-0000-0000-000000000002"),
        attempt=1,
    )
    publisher = YouTubePublisher(
        client=httpx.Client(timeout=httpx.Timeout(15.0)),
        policy=YouTubePolicy(
            audit_approved=False,
            now=datetime.now(UTC),
        ),
        api_origin="https://www.googleapis.com",
        checkpoint_vault=_ProcessVault(),
        upload_context=context,
    )
    request = YouTubePublishRequest(
        title="Clipah private publishing smoke",
        description="Private automated verification upload.",
        tags=("clipah", "smoke-test"),
        category_id="22",
        made_for_kids=False,
        contains_synthetic_media=False,
        requested_privacy=YouTubePrivacy.PRIVATE,
    )

    publisher.confirm_destination(access_token=token, expected_account_id=channel_id)
    checkpoint = publisher.begin(request=request, media=media, access_token=token)
    safe_checkpoints: list[dict[str, object]] = [checkpoint.safe_dict()]
    while checkpoint.progress is UploadProgress.ACTIVE:
        checkpoint = publisher.transfer(
            checkpoint=checkpoint,
            read_range=media.read_range,
            access_token=token,
        )
        safe_checkpoints.append(checkpoint.safe_dict())
        if checkpoint.final_request_ambiguous:
            checkpoint = publisher.query_upload(checkpoint=checkpoint, access_token=token)
    assert checkpoint.provider_video_id is not None
    status = publisher.poll(provider_id=checkpoint.provider_video_id, access_token=token)
    assert isinstance(status, YouTubeStatus)
    assert status.outcome in {YouTubeOutcome.PROCESSING, YouTubeOutcome.SUCCEEDED}
    assert request.requested_privacy is YouTubePrivacy.PRIVATE
    assert safe_checkpoints
