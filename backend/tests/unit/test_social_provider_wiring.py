"""Unit contracts for which social OAuth providers a deployment builds from its settings."""

from __future__ import annotations

import pytest

from clipah.api.app import _configured_social_providers
from clipah.config import Settings
from clipah.social_accounts.models import SocialProvider
from clipah.social_accounts.youtube_provider import YouTubeOAuthProvider

pytestmark = pytest.mark.unit

CALLBACK = "http://localhost:3000/api/v1/social-oauth/youtube/callback"


def _settings(**overrides: object) -> Settings:
    """A local deployment with YouTube publishing fully configured unless overridden."""
    values: dict[str, object] = {
        "environment": "local",
        "social_publishing_enabled": True,
        "youtube_publishing_enabled": True,
        "youtube_oauth_client_id": "client-id",
        "youtube_oauth_client_secret": "client-secret",
        "youtube_oauth_redirect_uri": CALLBACK,
        **overrides,
    }
    return Settings(**values)  # type: ignore[arg-type]


def test_a_configured_youtube_deployment_can_connect_channels() -> None:
    """Without this the connect route answers 404 even with every setting in place."""
    providers = _configured_social_providers(_settings())

    assert isinstance(providers[SocialProvider.YOUTUBE], YouTubeOAuthProvider)


@pytest.mark.parametrize(
    "overrides",
    [
        {"social_publishing_enabled": False},
        {"youtube_publishing_enabled": False},
        {"youtube_oauth_client_id": None},
        {"youtube_oauth_client_secret": ""},
    ],
)
def test_youtube_is_absent_unless_switched_on_and_registered(overrides: dict[str, object]) -> None:
    """A half-configured provider must look exactly like one that does not exist."""
    assert _configured_social_providers(_settings(**overrides)) == {}
