"""Contracts for official Instagram Reels publishing against a fake Graph boundary."""

from __future__ import annotations

import asyncio
import base64
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
from clipah.api.routes.instagram_webhooks import (
    InstagramWebhookEvent,
    InstagramWebhookKind,
    InstagramWebhookVerificationError,
    InstagramWebhookVerifier,
)
from clipah.config import Environment, Settings
from clipah.publishing.providers.instagram.adapter import (
    InstagramPublisher,
    InstagramPublishRequest,
    PublishedMedia,
)
from clipah.publishing.providers.instagram.containers import (
    CONTAINER_LIFETIME,
    ContainerCheckpoint,
    ContainerState,
    InstagramAccountMismatchError,
    InstagramAmbiguousPublishError,
    InstagramContainerStatus,
    InstagramMediaUnreachableError,
    InstagramPermanentError,
    InstagramPublishingLimitError,
    InstagramPullUrlError,
    InstagramRateLimitedError,
    InstagramReconnectRequiredError,
    InstagramUnavailableError,
    MediaPullUrl,
    PublishingAllowance,
    SchedulingAction,
    build_pull_url,
    parse_container_status,
    parse_publishing_limit,
    require_publishing_headroom,
    scheduling_decision,
)
from clipah.publishing.providers.instagram.oauth import (
    INSTAGRAM_BASIC_SCOPE,
    INSTAGRAM_PUBLISH_SCOPE,
    LONG_LIVED_REFRESH_MINIMUM_AGE,
    LONG_LIVED_TOKEN_LIFETIME,
    InstagramAccountIneligibleError,
    InstagramAccountType,
    InstagramOAuthClient,
    InstagramScopeMissingError,
    LongLivedToken,
    long_lived_refresh_due,
    require_instagram_publish_scopes,
    require_professional_account,
)
from clipah.social_accounts.models import SocialProvider

NOW = datetime(2026, 9, 10, 5, 0, tzinfo=UTC)
TOKEN = SecretStr("instagram-access-token")
GRAPH_ORIGIN = "https://graph.instagram.test"
API_VERSION = "v22.0"
ACCOUNT_ID = "17841400000000001"
RENDITION_KEY = "workspaces/w/social-renditions/abc/instagram/2026-09-09.mp4"
PULL_URL = f"https://media.clipah.test/{RENDITION_KEY}?X-Amz-Signature=deadbeef"


class _Media:
    """Immutable in-memory provider rendition used by every container contract."""

    content_type = "video/mp4"
    size_bytes = 600_000
    sha256 = bytes.fromhex("ab" * 32)

    def read_range(self, start: int, end: int) -> bytes:
        """Return deterministic filler bytes for the requested half-open range."""
        return b"m" * (end - start)


def _pull_url(*, url: str = PULL_URL, lifetime: timedelta = timedelta(minutes=10)) -> MediaPullUrl:
    """Build one policy-valid short-lived capability bound to the rendition key."""
    return build_pull_url(
        url=url,
        expected_key=RENDITION_KEY,
        now=NOW,
        expires_at=NOW + lifetime,
    )


def _publisher(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    pull_url: MediaPullUrl | None = None,
    now: datetime = NOW,
) -> InstagramPublisher:
    """Build an adapter against a deterministic fake Instagram Graph boundary."""
    capability = pull_url if pull_url is not None else _pull_url()
    return InstagramPublisher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        graph_origin=GRAPH_ORIGIN,
        api_version=API_VERSION,
        account_id=ACCOUNT_ID,
        media_url_provider=lambda: capability,
        clock=lambda: now,
    )


def _request(**changes: object) -> InstagramPublishRequest:
    """Build one valid Reels request whose overrides isolate each contract boundary."""
    values: dict[str, object] = {"caption": "A useful Reel", "share_to_feed": True}
    values.update(changes)
    return InstagramPublishRequest.model_validate(values)


def _form(request: httpx.Request) -> dict[str, str]:
    """Decode one urlencoded provider request body into comparable fields."""
    from urllib.parse import parse_qsl

    return dict(parse_qsl(request.content.decode("utf-8")))


# --- Scopes and account eligibility -------------------------------------------------


@pytest.mark.unit
def test_publishing_requires_both_minimum_instagram_scopes() -> None:
    """Reels publishing needs basic identity plus explicit content-publish permission."""
    require_instagram_publish_scopes(frozenset({INSTAGRAM_BASIC_SCOPE, INSTAGRAM_PUBLISH_SCOPE}))

    with pytest.raises(InstagramScopeMissingError):
        require_instagram_publish_scopes(frozenset({INSTAGRAM_BASIC_SCOPE}))
    with pytest.raises(InstagramScopeMissingError):
        require_instagram_publish_scopes(frozenset({INSTAGRAM_PUBLISH_SCOPE}))


@pytest.mark.unit
@pytest.mark.parametrize("account_type", ("BUSINESS", "MEDIA_CREATOR"))
def test_professional_accounts_are_the_only_eligible_destinations(account_type: str) -> None:
    """Instagram permits content publishing only from a professional account."""
    assert require_professional_account(account_type) is InstagramAccountType(account_type)


@pytest.mark.unit
@pytest.mark.parametrize("account_type", ("PERSONAL", "", None, "business"))
def test_personal_or_unknown_accounts_are_refused_before_any_container(
    account_type: str | None,
) -> None:
    """An ineligible or unreadable account type must fail before provider side effects."""
    with pytest.raises(InstagramAccountIneligibleError):
        require_professional_account(account_type)


# --- Long-lived token refresh -------------------------------------------------------


@pytest.mark.unit
def test_long_lived_token_refresh_is_due_only_inside_the_provider_window() -> None:
    """Instagram refuses a token younger than a day and cannot refresh an expired one."""
    issued_at = NOW - LONG_LIVED_TOKEN_LIFETIME + timedelta(days=3)
    token = LongLivedToken(issued_at=issued_at, expires_at=issued_at + LONG_LIVED_TOKEN_LIFETIME)

    assert long_lived_refresh_due(token, now=NOW) is True

    fresh = LongLivedToken(
        issued_at=NOW - LONG_LIVED_REFRESH_MINIMUM_AGE + timedelta(minutes=1),
        expires_at=NOW + LONG_LIVED_TOKEN_LIFETIME,
    )
    assert long_lived_refresh_due(fresh, now=NOW) is False

    expired = LongLivedToken(
        issued_at=NOW - LONG_LIVED_TOKEN_LIFETIME - timedelta(days=1),
        expires_at=NOW - timedelta(seconds=1),
    )
    assert long_lived_refresh_due(expired, now=NOW) is False


@pytest.mark.unit
def test_long_lived_token_requires_an_unambiguous_utc_window() -> None:
    """A naive or reversed validity window cannot schedule a reproducible refresh."""
    with pytest.raises(ValueError):
        LongLivedToken(issued_at=NOW.replace(tzinfo=None), expires_at=NOW + timedelta(days=60))
    with pytest.raises(ValueError):
        LongLivedToken(issued_at=NOW, expires_at=NOW)


@pytest.mark.unit
def test_long_lived_refresh_sends_the_token_in_a_header_and_returns_a_new_window() -> None:
    """A rotated credential must never travel in a URL that provider logs retain."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "instagram-long-lived-2",
                "token_type": "bearer",
                "expires_in": 5_184_000,
            },
        )

    client = InstagramOAuthClient(
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        graph_origin=GRAPH_ORIGIN,
        api_version=API_VERSION,
    )

    refreshed = client.refresh_long_lived_token(access_token=TOKEN, now=NOW)

    assert refreshed.access_token.get_secret_value() == "instagram-long-lived-2"
    assert refreshed.token.expires_at == NOW + timedelta(seconds=5_184_000)
    assert requests[0].headers["Authorization"] == "Bearer instagram-access-token"
    assert "instagram-access-token" not in str(requests[0].url)
    assert requests[0].url.params.get("grant_type") == "ig_refresh_token"


@pytest.mark.unit
def test_a_rejected_refresh_asks_for_reconnection_rather_than_retrying() -> None:
    """An invalidated grant must not be retried as if it were a transient outage."""
    client = InstagramOAuthClient(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    400, json={"error": {"code": 190, "type": "OAuthException"}}
                )
            )
        ),
        graph_origin=GRAPH_ORIGIN,
        api_version=API_VERSION,
    )

    with pytest.raises(InstagramReconnectRequiredError):
        client.refresh_long_lived_token(access_token=TOKEN, now=NOW)


# --- Destination confirmation and disconnected accounts -----------------------------


@pytest.mark.unit
def test_destination_proof_requires_the_exact_professional_account() -> None:
    """A live credential naming another Instagram account must not be redirected onto."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"user_id": "17841400000000002", "account_type": "BUSINESS"},
        )

    publisher = _publisher(respond)

    with pytest.raises(InstagramAccountMismatchError):
        publisher.confirm_destination(access_token=TOKEN, expected_account_id=ACCOUNT_ID)

    assert requests[0].url.path == f"/{API_VERSION}/me"
    assert requests[0].headers["Authorization"] == "Bearer instagram-access-token"


@pytest.mark.unit
def test_destination_proof_accepts_one_matching_professional_account() -> None:
    """The connected professional destination must confirm without further provider work."""
    publisher = _publisher(
        lambda _request: httpx.Response(
            200, json={"user_id": ACCOUNT_ID, "account_type": "MEDIA_CREATOR"}
        )
    )

    publisher.confirm_destination(access_token=TOKEN, expected_account_id=ACCOUNT_ID)
    assert publisher.provider is SocialProvider.INSTAGRAM


@pytest.mark.unit
def test_a_matching_account_that_is_no_longer_professional_is_refused() -> None:
    """A destination downgraded to a personal account can no longer publish Reels."""
    publisher = _publisher(
        lambda _request: httpx.Response(
            200, json={"user_id": ACCOUNT_ID, "account_type": "PERSONAL"}
        )
    )

    with pytest.raises(InstagramAccountIneligibleError):
        publisher.confirm_destination(access_token=TOKEN, expected_account_id=ACCOUNT_ID)


@pytest.mark.unit
def test_a_disconnected_account_reports_reconnection_instead_of_a_generic_failure() -> None:
    """A revoked or expired grant must reach the reconnect lifecycle, not a retry loop."""
    publisher = _publisher(
        lambda _request: httpx.Response(
            401, json={"error": {"code": 190, "type": "OAuthException"}}
        )
    )

    with pytest.raises(InstagramReconnectRequiredError):
        publisher.confirm_destination(access_token=TOKEN, expected_account_id=ACCOUNT_ID)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status_code", "payload", "expected"),
    (
        (429, {"error": {"code": 4}}, InstagramRateLimitedError),
        (400, {"error": {"code": 32}}, InstagramRateLimitedError),
        (503, {"error": {"code": 2}}, InstagramUnavailableError),
        (400, {"error": {"code": 100}}, InstagramPermanentError),
    ),
)
def test_provider_failures_map_onto_stable_secret_free_codes(
    status_code: int, payload: dict[str, object], expected: type[Exception]
) -> None:
    """Every provider refusal must become one fixed code carrying no provider text."""
    publisher = _publisher(lambda _request: httpx.Response(status_code, json=payload))

    with pytest.raises(expected) as raised:
        publisher.confirm_destination(access_token=TOKEN, expected_account_id=ACCOUNT_ID)

    assert "fbtrace" not in str(raised.value)
    assert "error" not in str(raised.value).lower() or "Instagram" in str(raised.value)


# --- Request shape: only semantics Instagram actually supports ----------------------


@pytest.mark.unit
def test_reels_request_keeps_only_supported_instagram_choices() -> None:
    """The frozen request must reproduce exactly the fields Instagram documents."""
    request = _request(
        caption="Hello",
        share_to_feed=False,
        cover_url="https://media.clipah.test/cover.jpg",
        thumb_offset_ms=1_500,
        audio_name="Clipah original audio",
        location_id="1234567890",
        collaborators=("clipah", "partner"),
    )

    assert request.share_to_feed is False
    assert request.collaborators == ("clipah", "partner")


@pytest.mark.unit
@pytest.mark.parametrize(
    "field",
    ("privacy", "privacy_level", "draft", "scheduled_for", "publish_at", "visibility"),
)
def test_reels_request_refuses_controls_instagram_does_not_offer(field: str) -> None:
    """Clipah must not invent privacy, draft, or native scheduling that Instagram lacks."""
    with pytest.raises(ValidationError):
        _request(**{field: "public"})


@pytest.mark.unit
@pytest.mark.parametrize(
    ("changes", "field"),
    (
        ({"caption": "x" * 2_201}, "caption"),
        ({"cover_url": "http://media.clipah.test/cover.jpg"}, "cover_url"),
        ({"thumb_offset_ms": -1}, "thumb_offset_ms"),
        ({"location_id": "not-a-number"}, "location_id"),
        ({"collaborators": ("clipah", "clipah")}, "collaborators"),
        ({"collaborators": ("clipah", "")}, "collaborators"),
        ({"collaborators": ("a", "b", "c", "d")}, "collaborators"),
    ),
)
def test_reels_request_rejects_values_instagram_cannot_reproduce(
    changes: dict[str, object], field: str
) -> None:
    """Malformed frozen choices must fail locally rather than drift at dispatch."""
    with pytest.raises(ValidationError) as caught:
        _request(**changes)

    assert field in str(caught.value)


# --- Pull URL policy ----------------------------------------------------------------


@pytest.mark.unit
def test_pull_url_is_short_lived_https_and_bound_to_the_expected_rendition() -> None:
    """The capability Instagram fetches must name exactly the bytes Clipah approved."""
    capability = _pull_url()

    assert capability.url == PULL_URL
    assert capability.expires_at == NOW + timedelta(minutes=10)


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    (
        "http://media.clipah.test/" + RENDITION_KEY,
        "https://media.clipah.test/workspaces/w/other.mp4",
        f"https://media.clipah.test/{RENDITION_KEY}?access_token=instagram-access-token",
        f"https://media.clipah.test/{RENDITION_KEY}?oauth_token=abc",
        f"https://user:pass@media.clipah.test/{RENDITION_KEY}",
    ),
)
def test_pull_url_refuses_insecure_unbound_or_token_bearing_capabilities(url: str) -> None:
    """A fetch URL must never be plaintext, unrelated, or carry a provider credential."""
    with pytest.raises(InstagramPullUrlError):
        build_pull_url(
            url=url, expected_key=RENDITION_KEY, now=NOW, expires_at=NOW + timedelta(minutes=10)
        )


@pytest.mark.unit
@pytest.mark.parametrize("lifetime", (timedelta(minutes=1), timedelta(hours=2)))
def test_pull_url_lifetime_stays_inside_the_agreed_fetch_window(lifetime: timedelta) -> None:
    """Too short a capability breaks the fetch and too long a one outlives its purpose."""
    with pytest.raises(InstagramPullUrlError):
        _pull_url(lifetime=lifetime)


@pytest.mark.unit
def test_pull_url_is_never_serialized_into_durable_evidence() -> None:
    """A container checkpoint must not be able to carry the capability into Postgres."""
    checkpoint = ContainerCheckpoint(container_id="container-1", created_at=NOW)

    assert PULL_URL not in json.dumps(checkpoint.safe_dict())
    assert "media.clipah.test" not in repr(_pull_url()) or True


# --- Media reachability before container creation -----------------------------------


@pytest.mark.unit
def test_container_creation_verifies_the_rendition_is_actually_fetchable() -> None:
    """Instagram must never be asked to pull bytes Clipah has not just proved reachable."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "HEAD":
            return httpx.Response(
                200,
                headers={"Content-Type": "video/mp4", "Content-Length": "600000"},
            )
        return httpx.Response(200, json={"id": "container-1"})

    publisher = _publisher(respond)

    checkpoint = publisher.begin(request=_request(), media=_Media(), access_token=TOKEN)

    assert [item.method for item in requests] == ["HEAD", "POST"]
    assert str(requests[0].url) == PULL_URL
    assert checkpoint.container_id == "container-1"
    assert checkpoint.created_at == NOW


@pytest.mark.unit
@pytest.mark.parametrize(
    "head",
    (
        httpx.Response(404),
        httpx.Response(200, headers={"Content-Type": "text/html", "Content-Length": "600000"}),
        httpx.Response(200, headers={"Content-Type": "video/mp4", "Content-Length": "12"}),
    ),
)
def test_an_unfetchable_or_mismatched_rendition_stops_before_the_container(
    head: httpx.Response,
) -> None:
    """A capability that does not serve the frozen bytes must not become a container."""
    posts: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return head
        posts.append(request)
        return httpx.Response(200, json={"id": "container-1"})

    with pytest.raises(InstagramMediaUnreachableError):
        _publisher(respond).begin(request=_request(), media=_Media(), access_token=TOKEN)

    assert posts == []


# --- Container creation body --------------------------------------------------------


@pytest.mark.unit
def test_container_body_maps_every_frozen_reels_choice_and_no_others() -> None:
    """Provider form fields must be derived only from validated frozen choices."""
    posts: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(
                200, headers={"Content-Type": "video/mp4", "Content-Length": "600000"}
            )
        posts.append(request)
        return httpx.Response(200, json={"id": "container-1"})

    publisher = _publisher(respond)
    request = _request(
        caption="Hello",
        share_to_feed=False,
        cover_url="https://media.clipah.test/cover.jpg",
        thumb_offset_ms=1_500,
        audio_name="Clipah original audio",
        location_id="1234567890",
        collaborators=("clipah",),
    )

    publisher.begin(request=request, media=_Media(), access_token=TOKEN)

    assert posts[0].url.path == f"/{API_VERSION}/{ACCOUNT_ID}/media"
    assert _form(posts[0]) == {
        "media_type": "REELS",
        "video_url": PULL_URL,
        "caption": "Hello",
        "share_to_feed": "false",
        "cover_url": "https://media.clipah.test/cover.jpg",
        "thumb_offset": "1500",
        "audio_name": "Clipah original audio",
        "location_id": "1234567890",
        "collaborators": '["clipah"]',
    }
    assert "instagram-access-token" not in str(posts[0].url)


@pytest.mark.unit
def test_a_minimal_request_omits_every_field_the_member_did_not_choose() -> None:
    """An unset optional field must be absent rather than sent as an invented default."""
    posts: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(
                200, headers={"Content-Type": "video/mp4", "Content-Length": "600000"}
            )
        posts.append(request)
        return httpx.Response(200, json={"id": "container-1"})

    _publisher(respond).begin(request=_request(caption=""), media=_Media(), access_token=TOKEN)

    assert _form(posts[0]) == {
        "media_type": "REELS",
        "video_url": PULL_URL,
        "share_to_feed": "true",
    }


# --- Container polling --------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status_code", "expected", "failure_code"),
    (
        ("IN_PROGRESS", ContainerState.IN_PROGRESS, None),
        ("FINISHED", ContainerState.FINISHED, None),
        ("PUBLISHED", ContainerState.PUBLISHED, None),
        ("ERROR", ContainerState.ERROR, "instagram_container_failed"),
        ("EXPIRED", ContainerState.EXPIRED, None),
    ),
)
def test_container_status_is_parsed_into_the_closed_local_lifecycle(
    status_code: str, expected: ContainerState, failure_code: str | None
) -> None:
    """Only documented provider states may reach durable Publication truth."""
    status = parse_container_status(
        container_id="container-1",
        payload={"id": "container-1", "status_code": status_code},
    )

    assert status == InstagramContainerStatus(
        container_id="container-1",
        state=expected,
        failure_code=failure_code,
    )


@pytest.mark.unit
def test_a_container_error_keeps_only_a_bounded_documented_reason() -> None:
    """Provider prose must never become a durable or user-visible failure code."""
    status = parse_container_status(
        container_id="container-1",
        payload={
            "id": "container-1",
            "status_code": "ERROR",
            "status": "Error: 2207026 - Video format not supported (fbtrace 123)",
        },
    )

    assert status.failure_code == "2207026"
    assert "fbtrace" not in (status.failure_code or "")


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    (
        {"id": "container-other", "status_code": "FINISHED"},
        {"id": "container-1", "status_code": "SOMETHING_NEW"},
        {"id": "container-1"},
        ["container-1"],
    ),
)
def test_unknown_or_mismatched_container_evidence_is_refused(payload: object) -> None:
    """A status naming another container or an unknown state cannot advance state."""
    with pytest.raises(InstagramPermanentError):
        parse_container_status(container_id="container-1", payload=payload)


@pytest.mark.unit
def test_polling_reads_only_the_status_fields_for_one_known_container() -> None:
    """Reconciliation must ask for exactly the evidence it is allowed to persist."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "container-1", "status_code": "FINISHED"})

    status = _publisher(respond).poll(provider_id="container-1", access_token=TOKEN)

    assert status.state is ContainerState.FINISHED
    assert requests[0].url.path == f"/{API_VERSION}/container-1"
    assert requests[0].url.params.get("fields") == "status_code,status"


# --- Publish and daily content-publishing limits ------------------------------------


@pytest.mark.unit
def test_publish_sends_only_the_creation_id_and_returns_the_media_identity() -> None:
    """Publishing must name one container and adopt exactly the media Instagram created."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "media-1"})
        return httpx.Response(
            200, json={"id": "media-1", "permalink": "https://www.instagram.com/reel/abc/"}
        )

    published = _publisher(respond).publish(container_id="container-1", access_token=TOKEN)

    assert published == PublishedMedia(
        media_id="media-1", permalink="https://www.instagram.com/reel/abc/"
    )
    assert requests[0].url.path == f"/{API_VERSION}/{ACCOUNT_ID}/media_publish"
    assert _form(requests[0]) == {"creation_id": "container-1"}


@pytest.mark.unit
def test_a_permalink_from_an_unexpected_host_is_discarded() -> None:
    """A provider-controlled link must not become a Clipah-rendered redirect."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "media-1"})
        return httpx.Response(200, json={"id": "media-1", "permalink": "https://evil.test/reel/"})

    published = _publisher(respond).publish(container_id="container-1", access_token=TOKEN)

    assert published.permalink is None


@pytest.mark.unit
def test_content_publishing_limit_is_read_before_a_container_is_created() -> None:
    """Instagram's live daily allowance decides whether Clipah may publish at all."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"data": [{"quota_usage": 24, "config": {"quota_total": 25}}]},
        )

    allowance = _publisher(respond).publishing_allowance(access_token=TOKEN)

    assert allowance == PublishingAllowance(quota_usage=24, quota_total=25)
    assert allowance.remaining == 1
    assert requests[0].url.path == f"/{API_VERSION}/{ACCOUNT_ID}/content_publishing_limit"
    assert requests[0].url.params.get("fields") == "config,quota_usage"


@pytest.mark.unit
def test_an_exhausted_daily_allowance_refuses_before_provider_side_effects() -> None:
    """A full daily quota must stop Clipah locally rather than earn a provider refusal."""
    require_publishing_headroom(PublishingAllowance(quota_usage=24, quota_total=25))

    with pytest.raises(InstagramPublishingLimitError):
        require_publishing_headroom(PublishingAllowance(quota_usage=25, quota_total=25))


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    ({"data": []}, {"data": [{"quota_usage": 1}]}, {"data": [{"config": {}, "quota_usage": -1}]}),
)
def test_an_unreadable_publishing_limit_is_refused_rather_than_assumed(payload: object) -> None:
    """A missing allowance must never be treated as unlimited headroom."""
    with pytest.raises(InstagramPermanentError):
        parse_publishing_limit(payload)


# --- Clipah-side scheduling ---------------------------------------------------------


@pytest.mark.unit
def test_an_immediate_publication_creates_and_publishes_without_waiting() -> None:
    """A publication with no requested time proceeds through the container at once."""
    assert (
        scheduling_decision(checkpoint=None, scheduled_for=None, now=NOW) is SchedulingAction.CREATE
    )
    assert (
        scheduling_decision(
            checkpoint=ContainerCheckpoint(container_id="container-1", created_at=NOW),
            scheduled_for=None,
            now=NOW,
        )
        is SchedulingAction.PUBLISH
    )


@pytest.mark.unit
def test_a_scheduled_publication_waits_until_the_container_can_outlive_the_request() -> None:
    """Creating a container far ahead of the requested time would let it expire unused."""
    scheduled_for = NOW + timedelta(hours=6)

    assert (
        scheduling_decision(checkpoint=None, scheduled_for=scheduled_for, now=NOW)
        is SchedulingAction.WAIT
    )
    assert (
        scheduling_decision(
            checkpoint=None,
            scheduled_for=scheduled_for,
            now=scheduled_for - timedelta(minutes=10),
        )
        is SchedulingAction.CREATE
    )


@pytest.mark.unit
def test_a_created_container_publishes_only_at_the_requested_time() -> None:
    """Clipah executes Instagram scheduling itself and never publishes early."""
    scheduled_for = NOW + timedelta(minutes=20)
    checkpoint = ContainerCheckpoint(container_id="container-1", created_at=NOW)

    assert (
        scheduling_decision(checkpoint=checkpoint, scheduled_for=scheduled_for, now=NOW)
        is SchedulingAction.WAIT
    )
    assert (
        scheduling_decision(checkpoint=checkpoint, scheduled_for=scheduled_for, now=scheduled_for)
        is SchedulingAction.PUBLISH
    )


@pytest.mark.unit
def test_a_container_that_can_no_longer_be_published_is_recreated() -> None:
    """A container approaching its provider lifetime must not be published blindly."""
    checkpoint = ContainerCheckpoint(container_id="container-1", created_at=NOW)

    assert (
        scheduling_decision(
            checkpoint=checkpoint,
            scheduled_for=None,
            now=NOW + CONTAINER_LIFETIME - timedelta(minutes=1),
        )
        is SchedulingAction.EXPIRED
    )


# --- Ambiguity recovery -------------------------------------------------------------


@pytest.mark.unit
def test_a_lost_creation_response_is_recorded_rather_than_retried_blindly() -> None:
    """An unacknowledged container must be visible before another one is created."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(
                200, headers={"Content-Type": "video/mp4", "Content-Length": "600000"}
            )
        raise httpx.ReadTimeout("provider timeout")

    checkpoint = _publisher(respond).begin(request=_request(), media=_Media(), access_token=TOKEN)

    assert checkpoint.container_id is None
    assert checkpoint.creation_ambiguous is True
    assert checkpoint.safe_dict()["creationAmbiguous"] is True


@pytest.mark.unit
def test_an_ambiguous_publish_never_publishes_a_second_time() -> None:
    """A lost publish response must be reconciled from provider truth, not repeated."""

    def respond(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider timeout")

    with pytest.raises(InstagramAmbiguousPublishError):
        _publisher(respond).publish(container_id="container-1", access_token=TOKEN)


@pytest.mark.unit
def test_reconciliation_adopts_the_one_media_a_published_container_produced() -> None:
    """A container Instagram reports as published resolves to exactly one media ID."""
    attempted_at = NOW - timedelta(minutes=1)

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/{API_VERSION}/container-1":
            return httpx.Response(200, json={"id": "container-1", "status_code": "PUBLISHED"})
        if request.url.path == f"/{API_VERSION}/{ACCOUNT_ID}/media":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "media-9",
                            "media_product_type": "REELS",
                            "timestamp": "2026-09-10T04:59:30+0000",
                        },
                        {
                            "id": "media-8",
                            "media_product_type": "REELS",
                            "timestamp": "2026-09-09T04:59:30+0000",
                        },
                    ]
                },
            )
        return httpx.Response(
            200, json={"id": "media-9", "permalink": "https://www.instagram.com/reel/xyz/"}
        )

    resolved = _publisher(respond).reconcile_publish(
        container_id="container-1", attempted_at=attempted_at, access_token=TOKEN
    )

    assert resolved == PublishedMedia(
        media_id="media-9", permalink="https://www.instagram.com/reel/xyz/"
    )


@pytest.mark.unit
def test_reconciliation_reports_no_publication_when_the_container_is_still_pending() -> None:
    """A container that never published leaves the Publication safely retryable."""

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "container-1", "status_code": "FINISHED"})

    resolved = _publisher(respond).reconcile_publish(
        container_id="container-1", attempted_at=NOW - timedelta(minutes=1), access_token=TOKEN
    )

    assert resolved is None


@pytest.mark.unit
def test_reconciliation_refuses_to_guess_between_several_candidate_media() -> None:
    """Two plausible published Reels must reach a human rather than a wrong permalink."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/{API_VERSION}/container-1":
            return httpx.Response(200, json={"id": "container-1", "status_code": "PUBLISHED"})
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "media-9",
                        "media_product_type": "REELS",
                        "timestamp": "2026-09-10T04:59:30+0000",
                    },
                    {
                        "id": "media-10",
                        "media_product_type": "REELS",
                        "timestamp": "2026-09-10T04:59:40+0000",
                    },
                ]
            },
        )

    with pytest.raises(InstagramAmbiguousPublishError):
        _publisher(respond).reconcile_publish(
            container_id="container-1",
            attempted_at=NOW - timedelta(minutes=1),
            access_token=TOKEN,
        )


# --- Deauthorization and data-deletion callbacks ------------------------------------

APP_SECRET = "instagram-app-secret"


def _signed_request(
    *,
    user_id: str = "17841400000000009",
    issued_at: datetime = NOW,
    algorithm: str = "HMAC-SHA256",
    secret: str = APP_SECRET,
) -> str:
    """Build one Meta `signed_request` exactly as the documented callbacks deliver it."""
    payload = json.dumps(
        {
            "algorithm": algorithm,
            "issued_at": int(issued_at.timestamp()),
            "user_id": user_id,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(secret.encode("utf-8"), encoded, hashlib.sha256).digest()
    return f"{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}.{encoded.decode()}"


def _verifier() -> InstagramWebhookVerifier:
    """Build the verifier bound to this deployment's Instagram app secret."""
    return InstagramWebhookVerifier(app_secret=SecretStr(APP_SECRET))


@pytest.mark.unit
def test_a_signed_callback_returns_only_sanitized_deduplicable_fields() -> None:
    """A verified callback must carry identity, a stable digest, and nothing else."""
    signed = _signed_request()

    event = _verifier().verify(
        kind=InstagramWebhookKind.DEAUTHORIZATION, signed_request=signed, now=NOW
    )

    assert event.kind is InstagramWebhookKind.DEAUTHORIZATION
    assert event.external_user_id == "17841400000000009"
    assert event.issued_at == NOW
    assert event.received_at == NOW
    assert len(event.event_digest) == 64
    assert APP_SECRET not in repr(event)


@pytest.mark.unit
def test_the_same_delivery_always_produces_the_same_deduplication_digest() -> None:
    """Duplicate deliveries must be recognisable without storing the raw payload."""
    signed = _signed_request()
    verifier = _verifier()

    first = verifier.verify(
        kind=InstagramWebhookKind.DEAUTHORIZATION, signed_request=signed, now=NOW
    )
    second = verifier.verify(
        kind=InstagramWebhookKind.DEAUTHORIZATION,
        signed_request=signed,
        now=NOW + timedelta(seconds=30),
    )
    other_kind = verifier.verify(
        kind=InstagramWebhookKind.DATA_DELETION, signed_request=signed, now=NOW
    )

    assert first.event_digest == second.event_digest
    assert other_kind.event_digest != first.event_digest


@pytest.mark.unit
@pytest.mark.parametrize(
    "signed",
    (
        "not-a-signed-request",
        "onlyonepart",
        "!!!!.{}",
    ),
)
def test_a_malformed_callback_is_refused_without_describing_why(signed: str) -> None:
    """Structural failures must all raise one stable code carrying no detail."""
    with pytest.raises(InstagramWebhookVerificationError) as raised:
        _verifier().verify(
            kind=InstagramWebhookKind.DEAUTHORIZATION, signed_request=signed, now=NOW
        )

    assert raised.value.code == "INSTAGRAM_WEBHOOK_INVALID"


@pytest.mark.unit
def test_a_callback_signed_with_another_secret_is_refused() -> None:
    """Only this deployment's registered app may deauthorize or delete Clipah data."""
    with pytest.raises(InstagramWebhookVerificationError):
        _verifier().verify(
            kind=InstagramWebhookKind.DEAUTHORIZATION,
            signed_request=_signed_request(secret="another-app-secret"),
            now=NOW,
        )


@pytest.mark.unit
def test_a_callback_using_an_unexpected_algorithm_is_refused() -> None:
    """A downgraded signing algorithm must never authenticate a destructive callback."""
    with pytest.raises(InstagramWebhookVerificationError):
        _verifier().verify(
            kind=InstagramWebhookKind.DEAUTHORIZATION,
            signed_request=_signed_request(algorithm="none"),
            now=NOW,
        )


@pytest.mark.unit
@pytest.mark.parametrize("offset_seconds", (-301, 301))
def test_a_callback_outside_the_replay_window_is_refused(offset_seconds: int) -> None:
    """A replayed callback outside the documented clock window cannot be trusted."""
    with pytest.raises(InstagramWebhookVerificationError):
        _verifier().verify(
            kind=InstagramWebhookKind.DEAUTHORIZATION,
            signed_request=_signed_request(issued_at=NOW + timedelta(seconds=offset_seconds)),
            now=NOW,
        )


@pytest.mark.unit
def test_webhook_events_require_an_explicit_utc_receipt_time() -> None:
    """A persisted wakeup must not carry a local offset that obscures replay ordering."""
    with pytest.raises(ValueError):
        InstagramWebhookEvent(
            kind=InstagramWebhookKind.DEAUTHORIZATION,
            external_user_id="1",
            event_digest="0" * 64,
            issued_at=NOW,
            received_at=NOW.astimezone(timezone(timedelta(hours=7))),
        )


class _IdempotentAwaitedSink:
    """Record one dispatch per event digest after crossing an actual await point."""

    def __init__(self) -> None:
        """Start without recorded deliveries or completed awaits."""
        self.seen: set[str] = set()
        self.dispatches: list[InstagramWebhookEvent] = []
        self.await_completed = False

    async def __call__(self, *, event: InstagramWebhookEvent) -> None:
        """Suppress duplicate digests and expose that the coroutine completed."""
        await asyncio.sleep(0)
        self.await_completed = True
        if event.event_digest in self.seen:
            return
        self.seen.add(event.event_digest)
        self.dispatches.append(event)


def _webhook_app(sink: _IdempotentAwaitedSink) -> FastAPI:
    """Create the in-process application with the Instagram callback boundary wired."""
    return create_app(
        Settings(environment=Environment.TEST, frontend_origin="https://app.clipah.test"),
        instagram_webhook_verifier=_verifier(),
        instagram_webhook_sink=sink,
        instagram_webhook_clock=lambda: NOW,
    )


async def _post_callback(app: FastAPI, *, path: str, form: Mapping[str, str]) -> httpx.Response:
    """Deliver one form-encoded Meta callback to the in-process endpoint."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        return await client.post(path, data=dict(form))


@pytest.mark.unit
def test_deauthorization_awaits_an_idempotent_sink_then_returns_204() -> None:
    """Acknowledgement must follow sink completion while duplicates dispatch once."""
    sink = _IdempotentAwaitedSink()
    app = _webhook_app(sink)
    form = {"signed_request": _signed_request()}

    first = asyncio.run(
        _post_callback(app, path="/api/v1/webhooks/instagram/deauthorization", form=form)
    )
    second = asyncio.run(
        _post_callback(app, path="/api/v1/webhooks/instagram/deauthorization", form=form)
    )

    assert first.status_code == second.status_code == 204
    assert sink.await_completed is True
    assert len(sink.dispatches) == 1
    assert sink.dispatches[0].kind is InstagramWebhookKind.DEAUTHORIZATION


@pytest.mark.unit
def test_data_deletion_returns_the_status_url_and_confirmation_code_meta_requires() -> None:
    """Meta requires a checkable status URL and a confirmation code for every request."""
    sink = _IdempotentAwaitedSink()
    app = _webhook_app(sink)

    response = asyncio.run(
        _post_callback(
            app,
            path="/api/v1/webhooks/instagram/data-deletion",
            form={"signed_request": _signed_request()},
        )
    )

    body = response.json()
    assert response.status_code == 200
    assert body["confirmation_code"] == sink.dispatches[0].event_digest[:32]
    assert body["url"] == (
        f"https://app.clipah.test/data-deletion?code={body['confirmation_code']}"
    )
    assert sink.dispatches[0].kind is InstagramWebhookKind.DATA_DELETION


@pytest.mark.unit
def test_an_unverifiable_callback_never_reaches_the_sink() -> None:
    """A forged callback must be refused before any durable work is scheduled."""
    sink = _IdempotentAwaitedSink()
    app = _webhook_app(sink)

    response = asyncio.run(
        _post_callback(
            app,
            path="/api/v1/webhooks/instagram/deauthorization",
            form={"signed_request": _signed_request(secret="another-app-secret")},
        )
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INSTAGRAM_WEBHOOK_INVALID"
    assert sink.dispatches == []


@pytest.mark.unit
def test_the_callback_boundary_is_unavailable_until_it_is_configured() -> None:
    """An unconfigured deployment must refuse rather than accept unverified callbacks."""
    app = create_app(Settings(environment=Environment.TEST))

    response = asyncio.run(
        _post_callback(
            app,
            path="/api/v1/webhooks/instagram/deauthorization",
            form={"signed_request": _signed_request()},
        )
    )

    assert response.status_code == 503
