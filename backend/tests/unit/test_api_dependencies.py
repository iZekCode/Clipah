"""Unit contracts for the request-scoped auth composition and CSRF checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Request

from clipah.api.dependencies import (
    CSRF_HEADER,
    default_auth_components,
    require_csrf,
    session_policy,
    session_secret_for,
    utcnow,
)
from clipah.api.errors import ApiError
from clipah.config import Environment, Settings

SESSION_SECRET = "a-test-session-secret-of-at-least-32-characters"
SITE_ORIGIN = "https://app.clipah.test"


def settings_with(**overrides: object) -> Settings:
    """Build test-profile settings that already trust one browser origin."""
    return Settings(
        environment=Environment.TEST,
        session_secret=SESSION_SECRET,
        frontend_origin=SITE_ORIGIN,
        **overrides,  # type: ignore[arg-type]
    )


def build_request(
    *, method: str = "POST", headers: dict[str, str], settings: Settings | None = None
) -> Request:
    """Build one ASGI request scope with the application state a dependency reads."""

    class _App:
        pass

    app = _App()
    app.state = _App()  # type: ignore[attr-defined]
    app.state.settings = settings or settings_with()  # type: ignore[attr-defined]
    scope = {
        "type": "http",
        "method": method,
        "path": "/api/v1/auth/logout",
        "root_path": "",
        "scheme": "https",
        "server": ("app.clipah.test", 443),
        "query_string": b"",
        "headers": [
            (name.lower().encode("ascii"), value.encode("ascii")) for name, value in headers.items()
        ],
        "app": app,
    }
    return Request(scope)


@pytest.mark.unit
def test_the_application_clock_reports_an_aware_utc_instant() -> None:
    """Every deadline comparison depends on an aware UTC clock."""
    now = utcnow()

    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
    assert abs(now - datetime.now(tz=UTC)) < timedelta(seconds=5)


@pytest.mark.unit
def test_session_policy_follows_the_configured_deadlines() -> None:
    """Deployment configuration, not a hard-coded default, sets Session lifetimes."""
    policy = session_policy(
        settings_with(
            session_idle_ttl_minutes=30,
            session_absolute_ttl_minutes=600,
            session_recent_auth_ttl_minutes=5,
        )
    )

    assert policy.idle_ttl == timedelta(minutes=30)
    assert policy.absolute_ttl == timedelta(minutes=600)
    assert policy.recent_auth_window == timedelta(minutes=5)


@pytest.mark.unit
def test_a_deployment_without_a_session_secret_cannot_serve_authentication() -> None:
    """Missing key material fails closed instead of falling back to a weak default."""
    with pytest.raises(ApiError) as failure:
        session_secret_for(Settings(environment=Environment.TEST))

    assert failure.value.status_code == 503


@pytest.mark.unit
def test_the_login_flow_is_composed_from_the_configured_google_client() -> None:
    """The default composition binds the ceremony to the configured client registration."""
    components = default_auth_components(
        settings_with(
            google_oidc_client_id="client-id",
            google_oidc_client_secret="client-secret",
            google_oidc_redirect_uri=f"{SITE_ORIGIN}/api/v1/auth/google/callback",
        )
    )

    redirect = components.oidc_flow().start(now=utcnow())

    assert "client_id=client-id" in redirect.authorization_url
    assert redirect.pending.redirect_uri == f"{SITE_ORIGIN}/api/v1/auth/google/callback"


@pytest.mark.unit
@pytest.mark.parametrize(
    "provider_settings",
    [
        {},
        {"google_oidc_client_id": "client-id"},
        {"google_oidc_client_id": "client-id", "google_oidc_client_secret": "client-secret"},
    ],
    ids=["nothing_configured", "no_secret", "no_redirect_uri"],
)
def test_an_unconfigured_provider_cannot_start_a_login(
    provider_settings: dict[str, object],
) -> None:
    """A half-configured provider must refuse the ceremony rather than improvise."""
    components = default_auth_components(settings_with(**provider_settings))

    with pytest.raises(ApiError) as failure:
        components.oidc_flow()

    assert failure.value.status_code == 503


@pytest.mark.unit
def test_a_safe_method_needs_no_double_submit_token() -> None:
    """CSRF defences apply only to the methods that can change state."""
    require_csrf(build_request(method="GET", headers={}))


@pytest.mark.unit
def test_a_referer_can_prove_the_originating_site_when_no_origin_is_sent() -> None:
    """Older clients that send only a Referer are still verifiable."""
    require_csrf(
        build_request(
            headers={
                "referer": f"{SITE_ORIGIN}/library",
                "cookie": "clipah_csrf=matching-token",
                CSRF_HEADER: "matching-token",
            }
        )
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "headers",
    [
        {"cookie": "clipah_csrf=matching-token", CSRF_HEADER: "matching-token"},
        {
            "referer": "not-a-url",
            "cookie": "clipah_csrf=matching-token",
            CSRF_HEADER: "matching-token",
        },
        {
            "origin": "https://attacker.example",
            "cookie": "clipah_csrf=matching-token",
            CSRF_HEADER: "matching-token",
        },
    ],
    ids=["no_origin_or_referer", "unusable_referer", "foreign_origin"],
)
def test_a_request_that_cannot_prove_its_origin_is_refused(headers: dict[str, str]) -> None:
    """An unprovable originating site is refused even with a matching token pair."""
    with pytest.raises(ApiError) as failure:
        require_csrf(build_request(headers=headers))

    assert failure.value.code == "CSRF_FAILED"


@pytest.mark.unit
def test_the_requests_own_origin_is_trusted_when_no_frontend_origin_is_configured() -> None:
    """A single-origin deployment still verifies that the request came from itself."""
    settings = Settings(environment=Environment.TEST, session_secret=SESSION_SECRET)

    require_csrf(
        build_request(
            headers={
                "origin": SITE_ORIGIN,
                "host": "app.clipah.test",
                "cookie": "clipah_csrf=matching-token",
                CSRF_HEADER: "matching-token",
            },
            settings=settings,
        )
    )
