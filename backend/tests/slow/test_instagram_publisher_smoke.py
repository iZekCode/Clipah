"""Explicitly gated live Reels smoke test for the Instagram sandbox destination."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from clipah.publishing.providers.instagram.adapter import (
    InstagramPublisher,
    InstagramPublishRequest,
)
from clipah.publishing.providers.instagram.containers import (
    ContainerState,
    build_pull_url,
    require_publishing_headroom,
)

pytestmark = pytest.mark.slow
SMOKE_ENABLED = os.getenv("CLIPAH_INSTAGRAM_PUBLISH_SMOKE") == "1"


class _RemoteMedia:
    """The immutable identity of one caller-supplied sandbox rendition."""

    content_type = "video/mp4"

    def __init__(self, size_bytes: int) -> None:
        """Record only the byte length the reachability proof must observe."""
        self.size_bytes = size_bytes
        self.sha256 = b"\x00" * 32

    def read_range(self, start: int, end: int) -> bytes:
        """Instagram pulls the media itself, so no local range read is required."""
        raise NotImplementedError("Instagram fetches the rendition by URL")


@pytest.mark.skipif(not SMOKE_ENABLED, reason="Instagram publish smoke is explicitly opt-in")
def test_reels_container_is_created_in_the_instagram_sandbox() -> None:
    """Live credentials may create one container without publishing it."""
    token = SecretStr(os.environ["CLIPAH_INSTAGRAM_SANDBOX_ACCESS_TOKEN"])
    account_id = os.environ["CLIPAH_INSTAGRAM_SANDBOX_ACCOUNT_ID"]
    media_url = os.environ["CLIPAH_INSTAGRAM_SANDBOX_MEDIA_URL"]
    media_key = os.environ["CLIPAH_INSTAGRAM_SANDBOX_MEDIA_KEY"]
    media = _RemoteMedia(int(os.environ["CLIPAH_INSTAGRAM_SANDBOX_MEDIA_SIZE_BYTES"]))
    now = datetime.now(UTC)
    capability = build_pull_url(
        url=media_url,
        expected_key=media_key,
        now=now,
        expires_at=now + timedelta(minutes=10),
    )
    publisher = InstagramPublisher(
        client=httpx.Client(timeout=httpx.Timeout(15.0)),
        graph_origin="https://graph.instagram.com",
        api_version=os.getenv("CLIPAH_INSTAGRAM_API_VERSION", "v22.0"),
        account_id=account_id,
        media_url_provider=lambda: capability,
        clock=lambda: datetime.now(UTC),
    )

    publisher.confirm_destination(access_token=token, expected_account_id=account_id)
    require_publishing_headroom(publisher.publishing_allowance(access_token=token))
    checkpoint = publisher.begin(
        request=InstagramPublishRequest(caption="Clipah publishing smoke", share_to_feed=False),
        media=media,
        access_token=token,
    )

    assert checkpoint.container_id is not None
    status = publisher.poll(provider_id=checkpoint.container_id, access_token=token)
    assert status.state in {
        ContainerState.IN_PROGRESS,
        ContainerState.FINISHED,
    }
