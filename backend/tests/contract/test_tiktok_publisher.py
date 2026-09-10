"""Contracts for TikTok draft fallback and audited Direct Post against a fake boundary."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr, ValidationError

from clipah.api.app import create_app
from clipah.api.routes.tiktok_webhooks import (
    TikTokWebhookEvent,
    TikTokWebhookEventKind,
    TikTokWebhookVerificationError,
    TikTokWebhookVerifier,
)
from clipah.config import Environment, Settings
from clipah.publishing.providers.tiktok.adapter import (
    CONSENT_MAX_AGE,
    CREATOR_INFO_MAX_AGE,
    TikTokConsent,
    TikTokCreatorInfo,
    TikTokDeclarationError,
    TikTokPolicy,
    TikTokPostRequest,
    TikTokPrivacyLevel,
    TikTokPublisher,
)
from clipah.publishing.providers.tiktok.oauth import (
    TIKTOK_BASIC_SCOPE,
    TIKTOK_PUBLISH_SCOPE,
    TIKTOK_UPLOAD_SCOPE,
    TikTokOAuthClient,
    TikTokScopeMissingError,
    require_tiktok_publish_scopes,
)
from clipah.publishing.providers.tiktok.transfers import (
    DeliveryMode,
    PublishState,
    TikTokAccountMismatchError,
    TikTokPermanentError,
    TikTokProviderError,
    TikTokPublishStatus,
    TikTokPullUrlError,
    TikTokRateLimitedError,
    TikTokReconnectRequiredError,
    TikTokUnavailableError,
    TikTokUrlOwnershipError,
    TransferCheckpoint,
    parse_publish_status,
    verified_pull_url,
)
from clipah.social_accounts.models import SocialProvider

NOW = datetime(2026, 9, 10, 5, 0, tzinfo=UTC)
TOKEN = SecretStr("tiktok-access-token")
API_ORIGIN = "https://open.tiktokapis.test"
OPEN_ID = "open-id-1"
VERIFIED_PREFIX = "https://media.clipah.test/social-renditions/"
RENDITION_KEY = "social-renditions/abc/tiktok/2026-09-09.mp4"
PULL_URL = f"https://media.clipah.test/{RENDITION_KEY}?X-Amz-Signature=deadbeef"
CLIENT_KEY = "tiktok-client-key"
CLIENT_SECRET = "tiktok-client-secret"


class _Media:
    """Immutable in-memory provider rendition used by every transfer contract."""

    content_type = "video/mp4"
    size_bytes = 600_000
    sha256 = bytes.fromhex("ab" * 32)

    def read_range(self, start: int, end: int) -> bytes:
        """TikTok pulls the rendition itself, so no local range read is required."""
        raise NotImplementedError("TikTok fetches the rendition by URL")


def _pull_url(*, url: str = PULL_URL, lifetime: timedelta = timedelta(minutes=10)) -> object:
    """Build one policy-valid capability inside a verified TikTok URL prefix."""
    return verified_pull_url(
        url=url,
        expected_key=RENDITION_KEY,
        verified_prefixes=(VERIFIED_PREFIX,),
        now=NOW,
        expires_at=NOW + lifetime,
    )


def _creator_info(**changes: object) -> TikTokCreatorInfo:
    """Build one freshly fetched creator snapshot the composer would have shown."""
    values: dict[str, object] = {
        "creator_username": "clipah",
        "privacy_level_options": (
            TikTokPrivacyLevel.PUBLIC_TO_EVERYONE,
            TikTokPrivacyLevel.SELF_ONLY,
        ),
        "comment_disabled": False,
        "duet_disabled": False,
        "stitch_disabled": False,
        "max_video_post_duration_sec": 600,
        "fetched_at": NOW,
    }
    values.update(changes)
    return TikTokCreatorInfo.model_validate(values)


def _consent(**changes: object) -> TikTokConsent:
    """Build one explicit declaration set confirmed immediately before submission."""
    values: dict[str, object] = {
        "music_usage_confirmed": True,
        "consumer_terms_accepted": True,
        "branded_content_terms_accepted": False,
        "confirmed_at": NOW,
    }
    values.update(changes)
    return TikTokConsent.model_validate(values)


def _request(**changes: object) -> TikTokPostRequest:
    """Build one fully declared post whose overrides isolate each contract boundary."""
    values: dict[str, object] = {
        "title": "A useful clip",
        "privacy_level": TikTokPrivacyLevel.PUBLIC_TO_EVERYONE,
        "disable_comment": False,
        "disable_duet": False,
        "disable_stitch": False,
        "brand_content_toggle": False,
        "brand_organic_toggle": False,
        "is_aigc": False,
        "consent": _consent(),
    }
    values.update(changes)
    return TikTokPostRequest.model_validate(values)


def _publisher(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    direct_post_approved: bool = False,
    now: datetime = NOW,
) -> TikTokPublisher:
    """Build an adapter against a deterministic fake TikTok boundary."""
    capability = _pull_url()
    return TikTokPublisher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        api_origin=API_ORIGIN,
        policy=TikTokPolicy(direct_post_approved=direct_post_approved, now=now),
        media_url_provider=lambda: capability,
        clock=lambda: now,
    )


def _envelope(data: dict[str, object], *, code: str = "ok") -> dict[str, object]:
    """Wrap one payload in TikTok's documented data-and-error response envelope."""
    return {"data": data, "error": {"code": code, "message": "", "log_id": "log-1"}}


def _body(request: httpx.Request) -> dict[str, object]:
    """Decode one JSON provider request body into comparable fields."""
    decoded = json.loads(request.content.decode("utf-8"))
    assert isinstance(decoded, dict)
    return decoded


# --- Login Kit scopes ---------------------------------------------------------------


@pytest.mark.unit
def test_draft_upload_needs_upload_scope_but_not_the_direct_post_scope() -> None:
    """A draft destination must not demand the broader Direct Post permission."""
    require_tiktok_publish_scopes(
        frozenset({TIKTOK_BASIC_SCOPE, TIKTOK_UPLOAD_SCOPE}), direct_post=False
    )

    with pytest.raises(TikTokScopeMissingError):
        require_tiktok_publish_scopes(
            frozenset({TIKTOK_BASIC_SCOPE, TIKTOK_UPLOAD_SCOPE}), direct_post=True
        )


@pytest.mark.unit
def test_direct_post_needs_the_publish_scope_as_well() -> None:
    """Direct Post is a distinct permission that a draft grant never implies."""
    require_tiktok_publish_scopes(
        frozenset({TIKTOK_BASIC_SCOPE, TIKTOK_UPLOAD_SCOPE, TIKTOK_PUBLISH_SCOPE}),
        direct_post=True,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "scopes",
    (frozenset(), frozenset({TIKTOK_BASIC_SCOPE}), frozenset({TIKTOK_UPLOAD_SCOPE})),
)
def test_a_partial_login_kit_grant_fails_before_provider_work(scopes: frozenset[str]) -> None:
    """A partial grant must fail locally rather than at a provider side effect."""
    with pytest.raises(TikTokScopeMissingError):
        require_tiktok_publish_scopes(scopes, direct_post=False)


# --- Refresh rotation ---------------------------------------------------------------


def _oauth_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> TikTokOAuthClient:
    """Build the credential-maintenance boundary against a fake token endpoint."""
    return TikTokOAuthClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        api_origin=API_ORIGIN,
        client_key=CLIENT_KEY,
        client_secret=SecretStr(CLIENT_SECRET),
    )


@pytest.mark.unit
def test_refresh_adopts_the_rotated_refresh_token_and_never_logs_either_secret() -> None:
    """TikTok rotates the refresh token, so the old one must not survive the exchange."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "tiktok-access-2",
                "expires_in": 86_400,
                "refresh_token": "tiktok-refresh-2",
                "refresh_expires_in": 31_536_000,
                "open_id": OPEN_ID,
                "scope": "user.info.basic,video.upload",
                "token_type": "Bearer",
            },
        )

    rotated = _oauth_client(respond).refresh(refresh_token=SecretStr("tiktok-refresh-1"), now=NOW)

    assert rotated.access_token.get_secret_value() == "tiktok-access-2"
    assert rotated.refresh_token.get_secret_value() == "tiktok-refresh-2"
    assert rotated.access_token_expires_at == NOW + timedelta(seconds=86_400)
    assert rotated.refresh_token_expires_at == NOW + timedelta(seconds=31_536_000)
    assert rotated.granted_scopes == frozenset({TIKTOK_BASIC_SCOPE, TIKTOK_UPLOAD_SCOPE})
    assert rotated.open_id == OPEN_ID
    assert "tiktok-refresh-2" not in repr(rotated)
    assert "tiktok-access-2" not in repr(rotated)
    assert CLIENT_SECRET not in str(requests[0].url)


@pytest.mark.unit
def test_a_refresh_that_returns_the_same_token_is_refused_as_unrotated() -> None:
    """A provider response that skips rotation must not be persisted as progress."""

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "tiktok-access-2",
                "expires_in": 86_400,
                "refresh_token": "tiktok-refresh-1",
                "refresh_expires_in": 31_536_000,
                "open_id": OPEN_ID,
                "scope": "user.info.basic,video.upload",
            },
        )

    with pytest.raises(TikTokPermanentError):
        _oauth_client(respond).refresh(refresh_token=SecretStr("tiktok-refresh-1"), now=NOW)


@pytest.mark.unit
def test_a_revoked_authorization_asks_for_reconnection() -> None:
    """A revoked or expired grant must reach the reconnect lifecycle, not a retry loop."""

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "invalid_grant", "error_description": ""})

    with pytest.raises(TikTokReconnectRequiredError):
        _oauth_client(respond).refresh(refresh_token=SecretStr("tiktok-refresh-1"), now=NOW)


# --- Destination confirmation -------------------------------------------------------


@pytest.mark.unit
def test_destination_proof_requires_the_exact_connected_open_id() -> None:
    """A valid token for another TikTok account must not redirect the Publication."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json=_envelope({"user": {"open_id": "open-id-other", "username": "other"}})
        )

    publisher = _publisher(respond)

    with pytest.raises(TikTokAccountMismatchError):
        publisher.confirm_destination(access_token=TOKEN, expected_account_id=OPEN_ID)

    assert requests[0].url.path == "/v2/user/info/"
    assert requests[0].headers["Authorization"] == "Bearer tiktok-access-token"
    assert publisher.provider is SocialProvider.TIKTOK


@pytest.mark.unit
def test_destination_proof_accepts_the_connected_account() -> None:
    """The connected destination must confirm without further provider work."""
    _publisher(
        lambda _request: httpx.Response(
            200, json=_envelope({"user": {"open_id": OPEN_ID, "username": "clipah"}})
        )
    ).confirm_destination(access_token=TOKEN, expected_account_id=OPEN_ID)


# --- Provider error mapping ---------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status_code", "payload", "expected"),
    (
        (200, _envelope({}, code="access_token_invalid"), TikTokReconnectRequiredError),
        (200, _envelope({}, code="scope_not_authorized"), TikTokReconnectRequiredError),
        (200, _envelope({}, code="rate_limit_exceeded"), TikTokRateLimitedError),
        (200, _envelope({}, code="spam_risk_too_many_posts"), TikTokRateLimitedError),
        (200, _envelope({}, code="reached_active_user_cap"), TikTokRateLimitedError),
        (200, _envelope({}, code="spam_risk_user_banned_from_posting"), TikTokPermanentError),
        (200, _envelope({}, code="url_ownership_unverified"), TikTokUrlOwnershipError),
        (200, _envelope({}, code="privacy_level_option_mismatch"), TikTokPermanentError),
        (200, _envelope({}, code="internal_error"), TikTokUnavailableError),
        (401, {"error": {"code": "unauthorized"}}, TikTokReconnectRequiredError),
        (429, {"error": {"code": "too_many"}}, TikTokRateLimitedError),
        (503, {"error": {"code": "down"}}, TikTokUnavailableError),
    ),
)
def test_provider_failures_map_onto_stable_secret_free_codes(
    status_code: int, payload: dict[str, object], expected: type[TikTokProviderError]
) -> None:
    """Every provider refusal must become one fixed code carrying no provider text."""
    publisher = _publisher(lambda _request: httpx.Response(status_code, json=payload))

    with pytest.raises(expected) as raised:
        publisher.confirm_destination(access_token=TOKEN, expected_account_id=OPEN_ID)

    assert "log_id" not in str(raised.value)
    assert "log-1" not in str(raised.value)


# --- Creator info -------------------------------------------------------------------


@pytest.mark.unit
def test_creator_info_is_read_fresh_and_exposes_only_current_provider_choices() -> None:
    """The composer must offer exactly what TikTok says this creator may pick now."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=_envelope(
                {
                    "creator_username": "clipah",
                    "privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"],
                    "comment_disabled": False,
                    "duet_disabled": True,
                    "stitch_disabled": False,
                    "max_video_post_duration_sec": 600,
                }
            ),
        )

    info = _publisher(respond).creator_info(access_token=TOKEN)

    assert info.privacy_level_options == (
        TikTokPrivacyLevel.PUBLIC_TO_EVERYONE,
        TikTokPrivacyLevel.SELF_ONLY,
    )
    assert info.duet_disabled is True
    assert info.fetched_at == NOW
    assert requests[0].url.path == "/v2/post/publish/creator_info/query/"


@pytest.mark.unit
def test_creator_info_with_an_unknown_privacy_option_is_refused() -> None:
    """An option Clipah cannot reproduce must never be offered to a member."""

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_envelope(
                {
                    "creator_username": "clipah",
                    "privacy_level_options": ["SOMETHING_NEW"],
                    "comment_disabled": False,
                    "duet_disabled": False,
                    "stitch_disabled": False,
                    "max_video_post_duration_sec": 600,
                }
            ),
        )

    with pytest.raises(TikTokPermanentError):
        _publisher(respond).creator_info(access_token=TOKEN)


# --- Request shape: nothing preselected, nothing inferred ---------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "omitted",
    (
        "privacy_level",
        "disable_comment",
        "disable_duet",
        "disable_stitch",
        "brand_content_toggle",
        "brand_organic_toggle",
        "is_aigc",
        "consent",
    ),
)
def test_no_privacy_interaction_or_disclosure_choice_has_a_silent_default(
    omitted: str,
) -> None:
    """Every TikTok declaration must be an explicit member choice, never a default."""
    values = {
        "title": "A useful clip",
        "privacy_level": TikTokPrivacyLevel.PUBLIC_TO_EVERYONE,
        "disable_comment": False,
        "disable_duet": False,
        "disable_stitch": False,
        "brand_content_toggle": False,
        "brand_organic_toggle": False,
        "is_aigc": False,
        "consent": _consent(),
    }
    del values[omitted]

    with pytest.raises(ValidationError):
        TikTokPostRequest.model_validate(values)


@pytest.mark.unit
def test_consent_declarations_are_individually_required() -> None:
    """Music and consumer-terms confirmations must each be made explicitly."""
    for omitted in ("music_usage_confirmed", "consumer_terms_accepted", "confirmed_at"):
        values = {
            "music_usage_confirmed": True,
            "consumer_terms_accepted": True,
            "confirmed_at": NOW,
        }
        del values[omitted]
        with pytest.raises(ValidationError):
            TikTokConsent.model_validate(values)


@pytest.mark.unit
@pytest.mark.parametrize("field", ("scheduled_for", "publish_at", "draft"))
def test_the_request_refuses_controls_tiktok_does_not_offer(field: str) -> None:
    """Clipah must not invent scheduling or draft controls the API does not expose."""
    with pytest.raises(ValidationError):
        _request(**{field: "value"})


# --- Declarations checked immediately before submission -----------------------------


@pytest.mark.unit
def test_current_declarations_pass_for_a_freshly_confirmed_direct_post() -> None:
    """A complete, fresh, permitted declaration set must be accepted unchanged."""
    _publisher(lambda _request: httpx.Response(500)).require_current_declarations(
        request=_request(),
        creator_info=_creator_info(),
        duration_ms=60_000,
        mode=DeliveryMode.DIRECT_POST,
        now=NOW,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("changes", "creator_changes", "duration_ms", "code"),
    (
        (
            {},
            {"fetched_at": NOW - CREATOR_INFO_MAX_AGE - timedelta(seconds=1)},
            60_000,
            "creator_info_stale",
        ),
        ({"consent": None}, {}, 60_000, "consent_stale"),
        (
            {"privacy_level": TikTokPrivacyLevel.FOLLOWER_OF_CREATOR},
            {},
            60_000,
            "privacy_level_option_mismatch",
        ),
        (
            {
                "brand_content_toggle": True,
                "privacy_level": TikTokPrivacyLevel.SELF_ONLY,
                "consent": _consent(branded_content_terms_accepted=True),
            },
            {},
            60_000,
            "branded_content_visibility",
        ),
        ({"brand_content_toggle": True}, {}, 60_000, "branded_content_terms"),
        ({"brand_organic_toggle": True}, {}, 60_000, "branded_content_terms"),
        ({"disable_duet": False}, {"duet_disabled": True}, 60_000, "interaction_unavailable"),
        ({"disable_comment": False}, {"comment_disabled": True}, 60_000, "interaction_unavailable"),
        ({"disable_stitch": False}, {"stitch_disabled": True}, 60_000, "interaction_unavailable"),
        ({}, {"max_video_post_duration_sec": 30}, 60_000, "duration_not_permitted"),
    ),
)
def test_stale_incomplete_or_unpermitted_declarations_are_refused(
    changes: dict[str, object],
    creator_changes: dict[str, object],
    duration_ms: int,
    code: str,
) -> None:
    """TikTok's own rules must be enforced locally immediately before submission."""
    if changes.get("consent", "keep") is None:
        changes = {
            **changes,
            "consent": _consent(confirmed_at=NOW - CONSENT_MAX_AGE - timedelta(seconds=1)),
        }
    publisher = _publisher(lambda _request: httpx.Response(500))

    with pytest.raises(TikTokDeclarationError) as raised:
        publisher.require_current_declarations(
            request=_request(**changes),
            creator_info=_creator_info(**creator_changes),
            duration_ms=duration_ms,
            mode=DeliveryMode.DIRECT_POST,
            now=NOW,
        )

    assert raised.value.code == code


@pytest.mark.unit
def test_a_draft_destination_still_requires_music_and_consumer_declarations() -> None:
    """The draft fallback carries the same creator declarations TikTok requires."""
    publisher = _publisher(lambda _request: httpx.Response(500))

    with pytest.raises(TikTokDeclarationError) as raised:
        publisher.require_current_declarations(
            request=_request(consent=_consent(music_usage_confirmed=False)),
            creator_info=_creator_info(),
            duration_ms=60_000,
            mode=DeliveryMode.DRAFT_INBOX,
            now=NOW,
        )

    assert raised.value.code == "consent_missing"


@pytest.mark.unit
def test_a_draft_destination_ignores_direct_post_only_restrictions() -> None:
    """A draft carries no privacy or interaction fields, so TikTok decides them in-app."""
    _publisher(lambda _request: httpx.Response(500)).require_current_declarations(
        request=_request(privacy_level=TikTokPrivacyLevel.FOLLOWER_OF_CREATOR),
        creator_info=_creator_info(),
        duration_ms=60_000,
        mode=DeliveryMode.DRAFT_INBOX,
        now=NOW,
    )


# --- Audit gate ---------------------------------------------------------------------


@pytest.mark.unit
def test_direct_post_stays_disabled_until_the_audit_flag_is_set() -> None:
    """An unaudited deployment must route an explicitly labelled destination to drafts."""
    policy = TikTokPolicy(direct_post_approved=False, now=NOW)

    assert policy.delivery_mode() is DeliveryMode.DRAFT_INBOX
    evidence = policy.confirmation_evidence()
    assert evidence["requestedMode"] == "direct_post"
    assert evidence["effectiveMode"] == "draft_inbox"
    assert evidence["restriction"] == "tiktok_direct_post_audit_required"
    assert "TikTok app" in str(evidence["remainingUserAction"])


@pytest.mark.unit
def test_an_audited_deployment_posts_directly_with_no_remaining_user_action() -> None:
    """Approval flips the effective mode without changing the Publication contract."""
    policy = TikTokPolicy(direct_post_approved=True, now=NOW)

    assert policy.delivery_mode() is DeliveryMode.DIRECT_POST
    assert policy.confirmation_evidence() == {
        "requestedMode": "direct_post",
        "effectiveMode": "direct_post",
        "restriction": None,
        "remainingUserAction": None,
    }


# --- Pull URL policy ----------------------------------------------------------------


@pytest.mark.unit
def test_a_pull_url_must_sit_inside_a_verified_tiktok_prefix() -> None:
    """TikTok only fetches from a URL prefix whose ownership Clipah has proved."""
    capability = _pull_url()

    assert capability.url == PULL_URL

    with pytest.raises(TikTokPullUrlError):
        verified_pull_url(
            url=PULL_URL,
            expected_key=RENDITION_KEY,
            verified_prefixes=("https://other.clipah.test/social-renditions/",),
            now=NOW,
            expires_at=NOW + timedelta(minutes=10),
        )


@pytest.mark.unit
def test_a_deployment_with_no_verified_prefix_cannot_pull_at_all() -> None:
    """An unverified deployment must fail closed rather than let TikTok refuse later."""
    with pytest.raises(TikTokPullUrlError):
        verified_pull_url(
            url=PULL_URL,
            expected_key=RENDITION_KEY,
            verified_prefixes=(),
            now=NOW,
            expires_at=NOW + timedelta(minutes=10),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    (
        "http://media.clipah.test/" + RENDITION_KEY,
        "https://media.clipah.test/social-renditions/other.mp4",
        f"https://media.clipah.test/{RENDITION_KEY}?access_token=tiktok-access-token",
        f"https://user:pass@media.clipah.test/{RENDITION_KEY}",
    ),
)
def test_a_pull_url_refuses_insecure_unbound_or_token_bearing_capabilities(url: str) -> None:
    """A fetch URL must never be plaintext, unrelated, or carry a provider credential."""
    with pytest.raises(TikTokPullUrlError):
        verified_pull_url(
            url=url,
            expected_key=RENDITION_KEY,
            verified_prefixes=(VERIFIED_PREFIX,),
            now=NOW,
            expires_at=NOW + timedelta(minutes=10),
        )


@pytest.mark.unit
@pytest.mark.parametrize("lifetime", (timedelta(minutes=1), timedelta(hours=2)))
def test_a_pull_url_lifetime_stays_inside_the_agreed_fetch_window(lifetime: timedelta) -> None:
    """Too short a capability breaks the pull and too long a one outlives its purpose."""
    with pytest.raises(TikTokPullUrlError):
        _pull_url(lifetime=lifetime)


@pytest.mark.unit
def test_a_pull_url_is_never_serialized_into_durable_evidence() -> None:
    """A transfer checkpoint must not be able to carry the capability into Postgres."""
    checkpoint = TransferCheckpoint(
        publish_id="publish-1", mode=DeliveryMode.DRAFT_INBOX, created_at=NOW
    )

    assert PULL_URL not in json.dumps(checkpoint.safe_dict())


# --- Draft and Direct Post submission ----------------------------------------------


def _init_handler(
    posts: list[httpx.Request], *, publish_id: str = "publish-1"
) -> Callable[[httpx.Request], httpx.Response]:
    """Answer any init call with one publish identifier and record the request."""

    def respond(request: httpx.Request) -> httpx.Response:
        posts.append(request)
        return httpx.Response(200, json=_envelope({"publish_id": publish_id}))

    return respond


@pytest.mark.unit
def test_an_unaudited_destination_uses_the_official_draft_inbox_endpoint() -> None:
    """Before approval the fallback is TikTok's own draft flow, not a private path."""
    posts: list[httpx.Request] = []

    checkpoint = _publisher(_init_handler(posts)).begin(
        request=_request(), media=_Media(), access_token=TOKEN
    )

    assert posts[0].url.path == "/v2/post/publish/inbox/video/init/"
    assert _body(posts[0]) == {"source_info": {"source": "PULL_FROM_URL", "video_url": PULL_URL}}
    assert checkpoint.mode is DeliveryMode.DRAFT_INBOX
    assert checkpoint.publish_id == "publish-1"
    assert checkpoint.created_at == NOW


@pytest.mark.unit
def test_an_audited_destination_sends_every_declared_choice_to_direct_post() -> None:
    """Direct Post must carry exactly the declarations the member confirmed."""
    posts: list[httpx.Request] = []

    checkpoint = _publisher(_init_handler(posts), direct_post_approved=True).begin(
        request=_request(
            title="A useful clip",
            privacy_level=TikTokPrivacyLevel.SELF_ONLY,
            disable_comment=True,
            disable_duet=True,
            disable_stitch=True,
            video_cover_timestamp_ms=1_500,
            is_aigc=True,
        ),
        media=_Media(),
        access_token=TOKEN,
    )

    assert posts[0].url.path == "/v2/post/publish/video/init/"
    assert _body(posts[0]) == {
        "post_info": {
            "title": "A useful clip",
            "privacy_level": "SELF_ONLY",
            "disable_comment": True,
            "disable_duet": True,
            "disable_stitch": True,
            "video_cover_timestamp_ms": 1_500,
            "brand_content_toggle": False,
            "brand_organic_toggle": False,
            "is_aigc": True,
        },
        "source_info": {"source": "PULL_FROM_URL", "video_url": PULL_URL},
    }
    assert checkpoint.mode is DeliveryMode.DIRECT_POST


@pytest.mark.unit
def test_submission_refuses_undeclared_choices_before_any_provider_call() -> None:
    """A stale or impermissible declaration must stop before TikTok is contacted."""
    posts: list[httpx.Request] = []
    publisher = _publisher(_init_handler(posts), direct_post_approved=True)

    with pytest.raises(TikTokDeclarationError):
        publisher.begin(
            request=_request(privacy_level=TikTokPrivacyLevel.FOLLOWER_OF_CREATOR),
            media=_Media(),
            access_token=TOKEN,
            creator_info=_creator_info(),
            duration_ms=60_000,
        )

    assert posts == []


@pytest.mark.unit
def test_a_lost_init_response_is_recorded_rather_than_retried_blindly() -> None:
    """An unacknowledged submission must be visible before another one is attempted."""

    def respond(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider timeout")

    checkpoint = _publisher(respond).begin(request=_request(), media=_Media(), access_token=TOKEN)

    assert checkpoint.publish_id is None
    assert checkpoint.init_ambiguous is True
    assert checkpoint.safe_dict()["initAmbiguous"] is True


# --- Status reconciliation ----------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "expected"),
    (
        ("PROCESSING_UPLOAD", PublishState.PROCESSING),
        ("PROCESSING_DOWNLOAD", PublishState.PROCESSING),
        ("SEND_TO_USER_INBOX", PublishState.DELIVERED_TO_INBOX),
        ("PUBLISH_COMPLETE", PublishState.PUBLISHED),
        ("FAILED", PublishState.FAILED),
    ),
)
def test_publish_status_is_parsed_into_the_closed_local_lifecycle(
    status: str, expected: PublishState
) -> None:
    """Only documented provider states may reach durable Publication truth."""
    parsed = parse_publish_status(
        publish_id="publish-1", payload={"status": status, "publicaly_available_post_id": []}
    )

    assert parsed.state is expected
    assert parsed.publish_id == "publish-1"


@pytest.mark.unit
def test_a_completed_publish_keeps_the_one_public_post_identifier() -> None:
    """A published clip must retain the identifier TikTok made publicly available."""
    parsed = parse_publish_status(
        publish_id="publish-1",
        payload={"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": ["7000000000"]},
    )

    assert parsed.public_post_id == "7000000000"


@pytest.mark.unit
def test_a_failed_publish_keeps_only_a_bounded_documented_reason() -> None:
    """Provider prose must never become a durable or user-visible failure code."""
    parsed = parse_publish_status(
        publish_id="publish-1",
        payload={
            "status": "FAILED",
            "fail_reason": "picture_size_check_failed",
            "publicaly_available_post_id": [],
        },
    )

    assert parsed.failure_code == "picture_size_check_failed"

    unknown = parse_publish_status(
        publish_id="publish-1",
        payload={"status": "FAILED", "fail_reason": "log_id=abc trace things"},
    )
    assert unknown.failure_code == "tiktok_publish_failed"


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    ({"status": "SOMETHING_NEW"}, {}, ["PUBLISH_COMPLETE"]),
)
def test_unknown_publish_evidence_is_refused(payload: object) -> None:
    """An unknown provider state cannot advance a Publication."""
    with pytest.raises(TikTokPermanentError):
        parse_publish_status(publish_id="publish-1", payload=payload)


@pytest.mark.unit
def test_polling_asks_only_for_the_status_of_one_known_publish_id() -> None:
    """Reconciliation must ask for exactly the evidence it is allowed to persist."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=_envelope({"status": "SEND_TO_USER_INBOX", "publicaly_available_post_id": []}),
        )

    status = _publisher(respond).poll(provider_id="publish-1", access_token=TOKEN)

    assert isinstance(status, TikTokPublishStatus)
    assert status.state is PublishState.DELIVERED_TO_INBOX
    assert requests[0].url.path == "/v2/post/publish/status/fetch/"
    assert _body(requests[0]) == {"publish_id": "publish-1"}


# --- Webhook verification and reconciliation ---------------------------------------


def _signed_headers(
    *, body: bytes, timestamp: datetime = NOW, secret: str = CLIENT_SECRET
) -> dict[str, str]:
    """Sign one raw delivery exactly as TikTok's documented webhook header does."""
    stamp = str(int(timestamp.timestamp()))
    signature = hmac.new(
        secret.encode("utf-8"), f"{stamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return {"TikTok-Signature": f"t={stamp},s={signature}"}


def _webhook_body(*, event: str = "post.publish.complete", publish_id: str = "publish-1") -> bytes:
    """Build one raw TikTok webhook delivery for the connected client key."""
    return json.dumps(
        {
            "client_key": CLIENT_KEY,
            "event": event,
            "create_time": int(NOW.timestamp()),
            "user_openid": OPEN_ID,
            "content": json.dumps({"publish_id": publish_id}, separators=(",", ":")),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _verifier() -> TikTokWebhookVerifier:
    """Build the verifier bound to this deployment's TikTok client registration."""
    return TikTokWebhookVerifier(client_key=CLIENT_KEY, client_secret=SecretStr(CLIENT_SECRET))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("event", "kind"),
    (
        ("post.publish.complete", TikTokWebhookEventKind.PUBLISH_COMPLETE),
        ("post.publish.failed", TikTokWebhookEventKind.PUBLISH_FAILED),
        ("post.publish.inbox_delivered", TikTokWebhookEventKind.INBOX_DELIVERED),
        ("authorization.removed", TikTokWebhookEventKind.AUTHORIZATION_REMOVED),
    ),
)
def test_a_signed_delivery_returns_only_sanitized_deduplicable_fields(
    event: str, kind: TikTokWebhookEventKind
) -> None:
    """A verified delivery must carry identity, a stable digest, and nothing else."""
    body = _webhook_body(event=event)

    result = _verifier().verify(headers=_signed_headers(body=body), body=body, now=NOW)

    assert result.kind is kind
    assert result.external_account_id == OPEN_ID
    assert result.publish_id == (None if kind is kind.AUTHORIZATION_REMOVED else "publish-1")
    assert len(result.event_digest) == 64
    assert CLIENT_SECRET not in repr(result)


@pytest.mark.unit
def test_the_same_delivery_always_produces_the_same_deduplication_digest() -> None:
    """Duplicate deliveries must be recognisable without storing the raw payload."""
    body = _webhook_body()
    verifier = _verifier()

    first = verifier.verify(headers=_signed_headers(body=body), body=body, now=NOW)
    second = verifier.verify(
        headers=_signed_headers(body=body, timestamp=NOW + timedelta(seconds=30)),
        body=body,
        now=NOW + timedelta(seconds=30),
    )
    other = _webhook_body(publish_id="publish-2")
    different = verifier.verify(headers=_signed_headers(body=other), body=other, now=NOW)

    assert first.event_digest == second.event_digest
    assert different.event_digest != first.event_digest


@pytest.mark.unit
@pytest.mark.parametrize(
    "header",
    ("", "t=123", "s=abc", "t=abc,s=abc", "t=123,s=zz"),
)
def test_a_malformed_signature_header_is_refused_without_describing_why(header: str) -> None:
    """Structural failures must all raise one stable code carrying no detail."""
    body = _webhook_body()

    with pytest.raises(TikTokWebhookVerificationError) as raised:
        _verifier().verify(headers={"TikTok-Signature": header}, body=body, now=NOW)

    assert raised.value.code == "TIKTOK_WEBHOOK_INVALID"


@pytest.mark.unit
def test_a_delivery_signed_with_another_secret_is_refused() -> None:
    """Only this deployment's registered client may report publication truth."""
    body = _webhook_body()

    with pytest.raises(TikTokWebhookVerificationError):
        _verifier().verify(
            headers=_signed_headers(body=body, secret="another-secret"), body=body, now=NOW
        )


@pytest.mark.unit
def test_a_delivery_for_another_client_key_is_refused() -> None:
    """A correctly signed body naming another application is not our event."""
    body = json.dumps(
        {
            "client_key": "another-client",
            "event": "post.publish.complete",
            "create_time": int(NOW.timestamp()),
            "user_openid": OPEN_ID,
            "content": json.dumps({"publish_id": "publish-1"}),
        },
        separators=(",", ":"),
    ).encode("utf-8")

    with pytest.raises(TikTokWebhookVerificationError):
        _verifier().verify(headers=_signed_headers(body=body), body=body, now=NOW)


@pytest.mark.unit
@pytest.mark.parametrize("offset_seconds", (-301, 301))
def test_a_delivery_outside_the_replay_window_is_refused(offset_seconds: int) -> None:
    """A replayed delivery outside the documented clock window cannot be trusted."""
    body = _webhook_body()
    stamped = NOW + timedelta(seconds=offset_seconds)

    with pytest.raises(TikTokWebhookVerificationError):
        _verifier().verify(
            headers=_signed_headers(body=body, timestamp=stamped), body=body, now=NOW
        )


@pytest.mark.unit
def test_a_mutated_body_no_longer_verifies() -> None:
    """The signature must cover the exact bytes the sink will act on."""
    body = _webhook_body()
    headers = _signed_headers(body=body)

    with pytest.raises(TikTokWebhookVerificationError):
        _verifier().verify(headers=headers, body=body + b" ", now=NOW)


@pytest.mark.unit
def test_webhook_events_require_an_explicit_utc_receipt_time() -> None:
    """A persisted wakeup must not carry a local offset that obscures replay ordering."""
    with pytest.raises(ValueError):
        TikTokWebhookEvent(
            kind=TikTokWebhookEventKind.PUBLISH_COMPLETE,
            external_account_id=OPEN_ID,
            publish_id="publish-1",
            event_digest="0" * 64,
            received_at=NOW.astimezone(timezone(timedelta(hours=7))),
        )


class _IdempotentAwaitedSink:
    """Record one dispatch per event digest after crossing an actual await point."""

    def __init__(self) -> None:
        """Start without recorded deliveries or completed awaits."""
        self.seen: set[str] = set()
        self.dispatches: list[TikTokWebhookEvent] = []
        self.await_completed = False

    async def __call__(self, *, event: TikTokWebhookEvent) -> None:
        """Suppress duplicate digests and expose that the coroutine completed."""
        await asyncio.sleep(0)
        self.await_completed = True
        if event.event_digest in self.seen:
            return
        self.seen.add(event.event_digest)
        self.dispatches.append(event)


async def _post_webhook(app: FastAPI, *, body: bytes, headers: Mapping[str, str]) -> httpx.Response:
    """Deliver raw bytes to the in-process webhook endpoint without re-encoding."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        return await client.post("/api/v1/webhooks/tiktok", content=body, headers=dict(headers))


@pytest.mark.unit
def test_the_webhook_route_awaits_an_idempotent_sink_then_returns_204() -> None:
    """Acknowledgement must follow sink completion while duplicates dispatch once."""
    sink = _IdempotentAwaitedSink()
    app = create_app(
        Settings(environment=Environment.TEST),
        tiktok_webhook_verifier=_verifier(),
        tiktok_webhook_sink=sink,
        tiktok_webhook_clock=lambda: NOW,
    )
    body = _webhook_body()
    headers = _signed_headers(body=body)

    first = asyncio.run(_post_webhook(app, body=body, headers=headers))
    second = asyncio.run(_post_webhook(app, body=body, headers=headers))

    assert first.status_code == second.status_code == 204
    assert sink.await_completed is True
    assert len(sink.dispatches) == 1
    assert sink.dispatches[0].publish_id == "publish-1"


@pytest.mark.unit
def test_an_unverifiable_delivery_never_reaches_the_sink() -> None:
    """A forged delivery must be refused before any durable work is scheduled."""
    sink = _IdempotentAwaitedSink()
    app = create_app(
        Settings(environment=Environment.TEST),
        tiktok_webhook_verifier=_verifier(),
        tiktok_webhook_sink=sink,
        tiktok_webhook_clock=lambda: NOW,
    )
    body = _webhook_body()

    response = asyncio.run(
        _post_webhook(app, body=body, headers=_signed_headers(body=body, secret="other"))
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TIKTOK_WEBHOOK_INVALID"
    assert sink.dispatches == []


@pytest.mark.unit
def test_the_webhook_boundary_is_unavailable_until_it_is_configured() -> None:
    """An unconfigured deployment must refuse rather than accept unverified deliveries."""
    app = create_app(Settings(environment=Environment.TEST))
    body = _webhook_body()

    response = asyncio.run(_post_webhook(app, body=body, headers=_signed_headers(body=body)))

    assert response.status_code == 503
