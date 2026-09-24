"""Contract tests for connecting a YouTube channel through Google's OAuth endpoints."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from clipah.social_accounts.models import OAuthGrantMaterial
from clipah.social_accounts.oauth import (
    SocialAuthorizationError,
    SocialDestinationMissingError,
    SocialProviderGrantRejectedError,
    SocialProviderUnavailableError,
)
from clipah.social_accounts.youtube_provider import (
    GOOGLE_REVOKE_ENDPOINT,
    GOOGLE_TOKEN_ENDPOINT,
    YouTubeOAuthProvider,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
SCOPES = (
    "https://www.googleapis.com/auth/youtube.readonly "
    "https://www.googleapis.com/auth/youtube.upload"
)
GRANT = OAuthGrantMaterial(access_token="access-1", refresh_token="refresh-1")


def _provider(
    handler: Callable[[httpx.Request], httpx.Response], *, audit_approved: bool = False
) -> tuple[YouTubeOAuthProvider, list[httpx.Request]]:
    """A provider whose every request is answered by ``handler`` and recorded."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    provider = YouTubeOAuthProvider(
        client_id="client-id",
        client_secret="client-secret",
        audit_approved=audit_approved,
        clock=lambda: NOW,
        client=httpx.Client(transport=httpx.MockTransport(record)),
    )
    return provider, seen


def _form(request: httpx.Request) -> dict[str, str]:
    """The single-valued form fields of one recorded request."""
    return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}


def _token(**overrides: Any) -> httpx.Response:
    """Google's answer to a successful token request."""
    body = {
        "access_token": "access-1",
        "expires_in": 3599,
        "refresh_token": "refresh-1",
        "scope": SCOPES,
        "token_type": "Bearer",
        **overrides,
    }
    return httpx.Response(
        200, json={key: value for key, value in body.items() if value is not None}
    )


def test_a_code_is_redeemed_with_its_verifier_for_a_refreshable_grant() -> None:
    """PKCE binds the code to this ceremony; the refresh token keeps the channel connected."""
    provider, seen = _provider(lambda _request: _token())

    result = provider.exchange_code(
        code="code-1", code_verifier="verifier-1", redirect_uri="http://localhost/cb"
    )

    assert str(seen[0].url) == GOOGLE_TOKEN_ENDPOINT
    assert _form(seen[0]) == {
        "grant_type": "authorization_code",
        "code": "code-1",
        "code_verifier": "verifier-1",
        "redirect_uri": "http://localhost/cb",
        "client_id": "client-id",
        "client_secret": "client-secret",
    }
    assert result.material.access_token == "access-1"
    assert result.material.refresh_token == "refresh-1"
    assert result.material.access_token_expires_at == NOW + timedelta(seconds=3599)
    assert result.granted_scopes == frozenset(SCOPES.split())


def test_a_grant_without_a_refresh_token_is_refused() -> None:
    """It would stop working with its first access token, an hour after connecting."""
    provider, _ = _provider(lambda _request: _token(refresh_token=None))

    with pytest.raises(SocialAuthorizationError):
        provider.exchange_code(code="c", code_verifier="v", redirect_uri="http://localhost/cb")


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        (httpx.Response(400, json={"error": "invalid_grant"}), SocialAuthorizationError),
        (httpx.Response(503), SocialProviderUnavailableError),
        (httpx.Response(429), SocialProviderUnavailableError),
        (httpx.Response(200, text="not json"), SocialProviderUnavailableError),
        (httpx.Response(200, json={"token_type": "Bearer"}), SocialProviderUnavailableError),
    ],
)
def test_a_refused_or_broken_exchange_becomes_the_services_own_error(
    answer: httpx.Response, error: type[Exception]
) -> None:
    """A refused code is the ceremony's failure; an outage is worth trying again."""
    provider, _ = _provider(lambda _request: answer)

    with pytest.raises(error):
        provider.exchange_code(code="c", code_verifier="v", redirect_uri="http://localhost/cb")


def test_an_unreachable_google_is_an_outage() -> None:
    """A transport failure must not look like a refusal of the member's consent."""

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    provider, _ = _provider(fail)

    with pytest.raises(SocialProviderUnavailableError):
        provider.exchange_code(code="c", code_verifier="v", redirect_uri="http://localhost/cb")


def test_the_grant_names_its_own_channel() -> None:
    """The channel ID is what every later upload is checked against."""
    channel = {
        "id": "UC123",
        "snippet": {
            "title": "  Clipah Studio  ",
            "thumbnails": {"default": {"url": "https://yt3.ggpht.com/a.jpg"}},
        },
    }
    provider, seen = _provider(lambda _request: httpx.Response(200, json={"items": [channel]}))

    identity = provider.account_identity(grant=GRANT)

    assert seen[0].url.path == "/youtube/v3/channels"
    assert seen[0].url.params["mine"] == "true"
    assert seen[0].headers["Authorization"] == "Bearer access-1"
    assert identity.external_account_id == "UC123"
    assert identity.display_name == "Clipah Studio"
    assert identity.avatar_url == "https://yt3.ggpht.com/a.jpg"
    assert identity.account_type == "channel"


def test_a_google_account_without_a_channel_cannot_be_connected() -> None:
    """There would be nothing to publish to."""
    provider, _ = _provider(lambda _request: httpx.Response(200, json={"items": []}))

    with pytest.raises(SocialDestinationMissingError):
        provider.account_identity(grant=GRANT)


def test_a_channel_read_the_grant_may_not_make_is_a_refused_authorization() -> None:
    """A token Google will not honour for the channel list was never a usable grant."""
    provider, _ = _provider(lambda _request: httpx.Response(403, json={"error": {}}))

    with pytest.raises(SocialAuthorizationError):
        provider.account_identity(grant=GRANT)


@pytest.mark.parametrize(
    ("audit_approved", "privacy"),
    [(False, ["private"]), (True, ["private", "unlisted", "public"])],
)
def test_capabilities_say_which_visibility_this_deployment_may_ask_for(
    audit_approved: bool, privacy: list[str]
) -> None:
    """An unaudited API project may only upload privately; the composer must know that."""
    provider, seen = _provider(lambda _request: httpx.Response(500), audit_approved=audit_approved)

    capabilities = provider.capabilities(grant=GRANT)

    assert capabilities.as_dict() == {"privacy_options": privacy, "audit_approved": audit_approved}
    assert seen == []


def test_a_refresh_keeps_the_refresh_token_google_did_not_rotate() -> None:
    """Dropping it would leave the next refresh with nothing to present."""
    provider, seen = _provider(lambda _request: _token(access_token="access-2", refresh_token=None))

    refreshed = provider.refresh(grant=GRANT)

    assert _form(seen[0])["grant_type"] == "refresh_token"
    assert _form(seen[0])["refresh_token"] == "refresh-1"
    assert refreshed.material.access_token == "access-2"
    assert refreshed.material.refresh_token == "refresh-1"


def test_a_refresh_google_refuses_means_the_member_must_reconnect() -> None:
    """A revoked or expired grant cannot be repaired without the member."""
    provider, _ = _provider(lambda _request: httpx.Response(400, json={"error": "invalid_grant"}))

    with pytest.raises(SocialProviderGrantRejectedError):
        provider.refresh(grant=GRANT)
    with pytest.raises(SocialProviderGrantRejectedError):
        provider.refresh(grant=OAuthGrantMaterial(access_token="only-access"))


def test_revoking_sends_the_refresh_token_and_tolerates_one_already_gone() -> None:
    """Revoking the refresh token ends every access token issued from it."""
    provider, seen = _provider(lambda _request: httpx.Response(400))

    provider.revoke(grant=GRANT)

    assert str(seen[0].url) == GOOGLE_REVOKE_ENDPOINT
    assert _form(seen[0]) == {"token": "refresh-1"}


def test_a_revocation_google_cannot_answer_is_an_outage() -> None:
    """The service erases locally either way, but it must know the provider did not hear."""
    provider, _ = _provider(lambda _request: httpx.Response(502))

    with pytest.raises(SocialProviderUnavailableError):
        provider.revoke(grant=GRANT)
