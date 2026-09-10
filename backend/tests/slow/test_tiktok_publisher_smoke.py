"""Explicitly gated live draft-upload smoke test for the TikTok sandbox."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from clipah.publishing.providers.tiktok.adapter import (
    TikTokConsent,
    TikTokPolicy,
    TikTokPostRequest,
    TikTokPrivacyLevel,
    TikTokPublisher,
)
from clipah.publishing.providers.tiktok.transfers import (
    DeliveryMode,
    PublishState,
    verified_pull_url,
)

pytestmark = pytest.mark.slow
SMOKE_ENABLED = os.getenv("CLIPAH_TIKTOK_PUBLISH_SMOKE") == "1"


class _RemoteMedia:
    """The immutable identity of one caller-supplied sandbox rendition."""

    content_type = "video/mp4"
    size_bytes = 0
    sha256 = b"\x00" * 32

    def read_range(self, start: int, end: int) -> bytes:
        """TikTok pulls the rendition itself, so no local range read is required."""
        raise NotImplementedError("TikTok fetches the rendition by URL")


@pytest.mark.skipif(not SMOKE_ENABLED, reason="TikTok publish smoke is explicitly opt-in")
def test_a_draft_is_delivered_to_the_sandbox_creator_inbox() -> None:
    """Live credentials may deliver only an unaudited draft that the creator finishes."""
    token = SecretStr(os.environ["CLIPAH_TIKTOK_SANDBOX_ACCESS_TOKEN"])
    open_id = os.environ["CLIPAH_TIKTOK_SANDBOX_OPEN_ID"]
    media_url = os.environ["CLIPAH_TIKTOK_SANDBOX_MEDIA_URL"]
    media_key = os.environ["CLIPAH_TIKTOK_SANDBOX_MEDIA_KEY"]
    verified_prefix = os.environ["CLIPAH_TIKTOK_SANDBOX_VERIFIED_PREFIX"]
    now = datetime.now(UTC)
    capability = verified_pull_url(
        url=media_url,
        expected_key=media_key,
        verified_prefixes=(verified_prefix,),
        now=now,
        expires_at=now + timedelta(minutes=10),
    )
    publisher = TikTokPublisher(
        client=httpx.Client(timeout=httpx.Timeout(15.0)),
        api_origin="https://open.tiktokapis.com",
        policy=TikTokPolicy(direct_post_approved=False, now=now),
        media_url_provider=lambda: capability,
        clock=lambda: datetime.now(UTC),
    )

    publisher.confirm_destination(access_token=token, expected_account_id=open_id)
    creator_info = publisher.creator_info(access_token=token)
    request = TikTokPostRequest(
        title="Clipah publishing smoke",
        privacy_level=TikTokPrivacyLevel.SELF_ONLY,
        disable_comment=True,
        disable_duet=True,
        disable_stitch=True,
        brand_content_toggle=False,
        brand_organic_toggle=False,
        is_aigc=False,
        consent=TikTokConsent(
            music_usage_confirmed=True,
            consumer_terms_accepted=True,
            confirmed_at=datetime.now(UTC),
        ),
    )
    checkpoint = publisher.begin(
        request=request,
        media=_RemoteMedia(),
        access_token=token,
        creator_info=creator_info,
        duration_ms=5_000,
    )

    assert checkpoint.mode is DeliveryMode.DRAFT_INBOX
    assert checkpoint.publish_id is not None
    status = publisher.poll(provider_id=checkpoint.publish_id, access_token=token)
    assert status.state in {PublishState.PROCESSING, PublishState.DELIVERED_TO_INBOX}
