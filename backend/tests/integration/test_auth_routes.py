"""HTTP contracts for the browser-facing login, Session, and CSRF surface."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text

from clipah.api.dependencies import session_policy
from clipah.auth.sessions import hash_session_token, issue_session
from clipah.config import Settings
from clipah.db import session_scope
from harness import (
    AUTHORIZATION_ENDPOINT,
    FOREIGN_ORIGIN,
    NOW,
    SESSION_SECRET,
    SITE_ORIGIN,
    Browser,
    Clock,
    StubGoogleProvider,
    assert_error,
    build_app,
    sign_in,
)
from support import provision_identity


def count_live_sessions(engine: Engine) -> int:
    """Count every Session row that has not been revoked."""
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT count(*) FROM auth_sessions WHERE revoked_at IS NULL")
            ).scalar_one()
        )


def disable_user(engine: Engine, email: str) -> None:
    """Disable one User the way an administrative action would."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE users SET status = 'disabled', disabled_at = :now "
                "WHERE primary_email = :email"
            ),
            {"now": NOW, "email": email},
        )


@pytest.mark.integration
def test_start_redirects_to_google_with_pkce_and_seals_the_ceremony(engine: Engine) -> None:
    """The browser must leave holding only an opaque, sealed copy of the ceremony."""
    del engine
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)

    response = browser.get("/api/v1/auth/google/start")

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(AUTHORIZATION_ENDPOINT)
    query = parse_qs(urlsplit(location).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == [flow.last_pending_state]
    assert "code_verifier" not in query
    sealed = browser.cookies.get("clipah_oidc")
    assert sealed is not None
    assert flow.last_pending_state not in sealed
    assert "httponly" in response.headers["set-cookie"].lower()


@pytest.mark.integration
def test_callback_signs_in_and_bootstraps_the_user(engine: Engine) -> None:
    """A first successful callback creates the User, their Workspace, and one Session."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)

    response = sign_in(browser, flow)

    assert response.status_code == 302
    assert response.headers["location"] == SITE_ORIGIN
    token = browser.cookies.get("clipah_session")
    assert token is not None
    assert browser.cookies.get("clipah_csrf") is not None
    assert browser.cookies.get("clipah_oidc") is None
    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT user_id FROM auth_sessions WHERE token_hash = :digest"),
            {"digest": hash_session_token(token)},
        ).scalar_one()
        workspaces = connection.execute(
            text("SELECT count(*) FROM workspace_memberships WHERE user_id = :user_id"),
            {"user_id": stored},
        ).scalar_one()
    assert workspaces == 1


@pytest.mark.integration
def test_session_cookies_are_host_locked_when_the_deployment_requires_https(
    engine: Engine,
) -> None:
    """Production-shaped settings must emit __Host- prefixed, Secure, HttpOnly cookies."""
    del engine
    clock = Clock(NOW)
    app, flow, _ = build_app(
        clock,
        StubGoogleProvider(clock),
        session_cookie_name="__Host-clipah_session",
        session_cookie_secure=True,
        frontend_origin="https://testserver",
    )
    browser = Browser(app, origin="https://testserver")

    response = sign_in(browser, flow)

    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(value for value in cookies if value.startswith("__Host-clipah_session="))
    csrf_cookie = next(value for value in cookies if value.startswith("__Host-clipah_csrf="))
    for value in (session_cookie, csrf_cookie):
        assert "Secure" in value
        assert "Path=/" in value
        assert "Domain=" not in value
        assert "SameSite=lax" in value
    assert "HttpOnly" in session_cookie
    assert "HttpOnly" not in csrf_cookie


@pytest.mark.integration
def test_callback_refuses_a_state_that_does_not_match_the_ceremony(engine: Engine) -> None:
    """A forged or replayed state must never reach the provider exchange."""
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    app, _, _ = build_app(clock, provider)
    browser = Browser(app)

    browser.get("/api/v1/auth/google/start")
    response = browser.get("/api/v1/auth/google/callback?code=authorization-code&state=forged")

    assert_error(response, status_code=401, code="AUTHENTICATION_FAILED")
    assert provider.exchanges == []
    assert count_live_sessions(engine) == 0


@pytest.mark.integration
def test_callback_without_a_sealed_ceremony_is_refused(engine: Engine) -> None:
    """A callback that carries no ceremony cookie cannot be trusted at all."""
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    app, _, _ = build_app(clock, provider)
    browser = Browser(app)

    response = browser.get("/api/v1/auth/google/callback?code=authorization-code&state=anything")

    assert_error(response, status_code=401, code="AUTHENTICATION_FAILED")
    assert provider.exchanges == []
    assert count_live_sessions(engine) == 0


@pytest.mark.integration
def test_callback_refuses_an_unverified_provider_email(engine: Engine) -> None:
    """An unverified email address can never bootstrap an account."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock, email_verified=False))
    browser = Browser(app)

    response = sign_in(browser, flow)

    assert_error(response, status_code=401, code="AUTHENTICATION_FAILED")
    assert count_live_sessions(engine) == 0


@pytest.mark.integration
def test_callback_refuses_to_link_an_equal_email_owned_by_another_identity(
    engine: Engine,
) -> None:
    """Equal email addresses must never silently join two sign-in methods."""
    provision_identity(engine, suffix="creator")
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock, email="creator@example.com"))
    browser = Browser(app)

    response = sign_in(browser, flow)

    assert_error(response, status_code=409, code="IDENTITY_CONFLICT")
    assert count_live_sessions(engine) == 0


@pytest.mark.integration
@pytest.mark.parametrize("cookie", [None, "not-a-real-session-token"])
def test_me_requires_a_live_session_cookie(engine: Engine, cookie: str | None) -> None:
    """No Session cookie and an unknown Session cookie are refused identically."""
    del engine
    clock = Clock(NOW)
    app, _, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    if cookie is not None:
        browser.cookies.set("clipah_session", cookie, domain="testserver", path="/")

    assert_error(browser.get("/api/v1/me"), status_code=401, code="UNAUTHENTICATED")


@pytest.mark.integration
def test_me_describes_the_signed_in_user(engine: Engine) -> None:
    """The profile endpoint reports the User and the freshness of their authentication."""
    del engine
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)

    body = browser.get("/api/v1/me").json()

    assert body["email"] == "creator@example.com"
    assert body["displayName"] == "Creator Example"
    assert body["recentAuthentication"] is True


@pytest.mark.integration
def test_recent_authentication_lapses_after_the_configured_window(engine: Engine) -> None:
    """A Session stays valid but stops counting as recently authenticated."""
    del engine
    clock = Clock(NOW)
    app, flow, settings = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)

    clock.advance(session_policy(settings).recent_auth_window)

    body = browser.get("/api/v1/me").json()
    assert body["recentAuthentication"] is False


@pytest.mark.integration
@pytest.mark.parametrize("lapse", ["idle", "absolute"])
def test_an_expired_session_is_refused_and_cannot_be_retried(engine: Engine, lapse: str) -> None:
    """Both deadlines close the Session for good, even against an earlier clock."""
    del engine
    clock = Clock(NOW)
    app, flow, settings = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    policy = session_policy(settings)

    if lapse == "idle":
        clock.advance(policy.idle_ttl)
    else:
        while clock() < NOW + policy.absolute_ttl:
            clock.advance(policy.idle_ttl - timedelta(days=1))
            browser.get("/api/v1/me")

    assert_error(browser.get("/api/v1/me"), status_code=401, code="UNAUTHENTICATED")
    clock.now = NOW + timedelta(minutes=1)
    assert_error(browser.get("/api/v1/me"), status_code=401, code="UNAUTHENTICATED")


@pytest.mark.integration
def test_a_disabled_user_loses_access_on_the_next_request(engine: Engine) -> None:
    """Disabling an account must end its access without waiting for any deadline."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)

    disable_user(engine, "creator@example.com")

    assert_error(browser.get("/api/v1/me"), status_code=403, code="ACCOUNT_DISABLED")


@pytest.mark.integration
def test_a_disabled_user_cannot_sign_in_again(engine: Engine) -> None:
    """A disabled account is refused at the callback, before any Session is issued."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    disable_user(engine, "creator@example.com")

    response = sign_in(Browser(app), flow)

    assert_error(response, status_code=403, code="ACCOUNT_DISABLED")
    assert count_live_sessions(engine) == 1


@pytest.mark.integration
def test_revoking_another_of_this_users_sessions_leaves_the_current_one_signed_in(
    engine: Engine,
) -> None:
    """Signing one device out from another must not disturb the device that asked."""
    del engine
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    first = Browser(app)
    second = Browser(app)
    sign_in(first, flow)
    sign_in(second, flow)
    other_session_id = second.get("/api/v1/me").json()["sessionId"]

    response = first.request("DELETE", f"/api/v1/me/sessions/{other_session_id}")

    assert response.status_code == 204
    assert first.cookies.get("clipah_session") is not None
    assert first.get("/api/v1/me").status_code == 200
    assert_error(second.get("/api/v1/me"), status_code=401, code="UNAUTHENTICATED")


@pytest.mark.integration
def test_logout_revokes_the_session_and_clears_its_cookies(engine: Engine) -> None:
    """Logging out ends the Session on the server, not only in the browser."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)

    response = browser.request("POST", "/api/v1/auth/logout")

    assert response.status_code == 204
    assert browser.cookies.get("clipah_session") is None
    assert browser.cookies.get("clipah_csrf") is None
    assert count_live_sessions(engine) == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    ("origin", "csrf_token"),
    [
        (SITE_ORIGIN, ""),
        (SITE_ORIGIN, "a-token-the-browser-never-received"),
        (FOREIGN_ORIGIN, None),
    ],
    ids=["missing_token", "mismatched_token", "foreign_origin"],
)
def test_a_state_changing_request_without_proof_of_origin_is_refused(
    engine: Engine, origin: str, csrf_token: str | None
) -> None:
    """A cross-site request cannot end a Session it can neither read nor echo."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)

    response = browser.request("POST", "/api/v1/auth/logout", origin=origin, csrf_token=csrf_token)

    assert_error(response, status_code=403, code="CSRF_FAILED")
    assert count_live_sessions(engine) == 1
    assert browser.get("/api/v1/me").status_code == 200


@pytest.mark.integration
def test_sessions_list_marks_the_current_session_only(engine: Engine) -> None:
    """A User sees each of their live Sessions and which one is answering now."""
    del engine
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    first = Browser(app)
    second = Browser(app)
    sign_in(first, flow)
    sign_in(second, flow)

    body = first.get("/api/v1/me/sessions").json()

    assert len(body["sessions"]) == 2
    current = [entry for entry in body["sessions"] if entry["current"]]
    assert [entry["id"] for entry in current] == [first.get("/api/v1/me").json()["sessionId"]]


@pytest.mark.integration
def test_revoking_every_other_session_keeps_this_one_alive(engine: Engine) -> None:
    """Signing out other devices must not sign out the device that asked."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    first = Browser(app)
    second = Browser(app)
    sign_in(first, flow)
    sign_in(second, flow)

    response = first.request("DELETE", "/api/v1/me/sessions")

    assert response.json() == {"revokedCount": 1}
    assert first.get("/api/v1/me").status_code == 200
    assert_error(second.get("/api/v1/me"), status_code=401, code="UNAUTHENTICATED")
    assert count_live_sessions(engine) == 1


@pytest.mark.integration
def test_revoking_the_current_session_clears_its_cookies(engine: Engine) -> None:
    """Revoking the Session behind the request is a logout for that browser."""
    del engine
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    session_id = browser.get("/api/v1/me").json()["sessionId"]

    response = browser.request("DELETE", f"/api/v1/me/sessions/{session_id}")

    assert response.status_code == 204
    assert browser.cookies.get("clipah_session") is None
    assert_error(browser.get("/api/v1/me"), status_code=401, code="UNAUTHENTICATED")


@pytest.mark.integration
def test_revoking_an_unowned_session_is_indistinguishable_from_a_missing_one(
    engine: Engine,
) -> None:
    """A guessed Session identifier must not reveal that it belongs to someone else."""
    other_user_id, _ = provision_identity(engine, suffix="other")
    clock = Clock(NOW)
    app, flow, settings = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    foreign_session_id = issue_foreign_session(settings, user_id=other_user_id)

    unowned = browser.request("DELETE", f"/api/v1/me/sessions/{foreign_session_id}")
    unknown = browser.request("DELETE", f"/api/v1/me/sessions/{uuid4()}")

    assert_error(unowned, status_code=404, code="NOT_FOUND")
    assert_error(unknown, status_code=404, code="NOT_FOUND")
    assert count_live_sessions(engine) == 2


def issue_foreign_session(settings: Settings, *, user_id: UUID) -> UUID:
    """Give another User a live Session this browser must never be able to revoke."""
    with session_scope(settings=settings) as session:
        issued = issue_session(
            session,
            user_id=user_id,
            secret=SESSION_SECRET,
            policy=session_policy(settings),
            now=NOW,
        )
        return issued.session_id
