"""What a hostile web page, and a hostile browser tab, can and cannot do with a Session.

The Session cookie is the whole of a member's standing, so this suite attacks the three
ways a browser hands it over by accident: a state-changing request forged by another site,
a Session value planted before the member ever signed in, and a redirect that would carry
either of them somewhere the deployment does not own. It also checks the two headers whose
absence is the protection — no Access-Control-Allow-Origin means no other site may read a
credentialed response — and that nothing the API writes down ever repeats the token.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import pytest
from httpx import Response
from sqlalchemy import Engine

from harness import (
    FOREIGN_ORIGIN,
    NOW,
    SITE_ORIGIN,
    Browser,
    Clock,
    StubGoogleProvider,
    build_app,
    sign_in,
)

SESSION_COOKIE = "clipah_session"
CSRF_COOKIE = "clipah_csrf"
PLANTED_TOKEN = "planted-session-token-8f2b1c9d"


@pytest.mark.integration
def test_a_state_changing_request_from_another_site_is_refused(
    engine: Engine, clean_database: None
) -> None:
    """A cookie the browser attaches by itself must not be enough to change anything."""
    del engine, clean_database
    browser, workspace_id = _signed_in()

    response = browser.request(
        "POST",
        f"/api/v1/workspaces?workspace_id={workspace_id}",
        json={"name": "Forged"},
        origin=FOREIGN_ORIGIN,
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"


@pytest.mark.integration
def test_a_state_changing_request_with_no_origin_at_all_is_refused(
    engine: Engine, clean_database: None
) -> None:
    """A request that will not say where it came from has not proven it came from us."""
    del engine, clean_database
    browser, _ = _signed_in()

    response = _unsafe(browser, headers={"X-CSRF-Token": browser.cookies.get(CSRF_COOKIE) or ""})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"


@pytest.mark.integration
def test_a_referer_from_our_own_site_stands_in_for_a_missing_origin(
    engine: Engine, clean_database: None
) -> None:
    """Some browsers omit Origin on same-site requests, so Referer is the documented fallback."""
    del engine, clean_database
    browser, _ = _signed_in()

    response = _unsafe(
        browser,
        headers={
            "Referer": f"{SITE_ORIGIN}/dashboard",
            "X-CSRF-Token": browser.cookies.get(CSRF_COOKIE) or "",
        },
    )

    assert response.status_code == 201, response.text


@pytest.mark.integration
def test_a_referer_from_another_site_does_not(engine: Engine, clean_database: None) -> None:
    """The fallback is a same-origin proof, not a way around one."""
    del engine, clean_database
    browser, _ = _signed_in()

    response = _unsafe(
        browser,
        headers={
            "Referer": f"{FOREIGN_ORIGIN}/attack",
            "X-CSRF-Token": browser.cookies.get(CSRF_COOKIE) or "",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"


@pytest.mark.integration
@pytest.mark.parametrize("echoed", ["", "not-the-cookie-value"])
def test_the_double_submit_token_must_match_the_cookie_exactly(
    engine: Engine, clean_database: None, echoed: str
) -> None:
    """A same-origin proof alone is not enough while subdomains can write cookies."""
    del engine, clean_database
    browser, _ = _signed_in()

    response = _unsafe(browser, headers={"Origin": SITE_ORIGIN, "X-CSRF-Token": echoed})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"


@pytest.mark.integration
def test_the_session_cookie_is_not_readable_by_script_and_the_csrf_token_is(
    engine: Engine, clean_database: None
) -> None:
    """The double-submit token is deliberately readable; the Session must never be."""
    del engine, clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)

    response = sign_in(browser, flow)

    attributes = _cookie_attributes(response)
    assert "httponly" in attributes[SESSION_COOKIE]
    assert "httponly" not in attributes[CSRF_COOKIE]
    assert attributes[SESSION_COOKIE]["samesite"] == "lax"


@pytest.mark.integration
def test_a_deployment_on_https_locks_both_cookies_to_the_host(
    engine: Engine, clean_database: None
) -> None:
    """A cookie that can be set by a subdomain is a cookie an attacker can plant."""
    del engine, clean_database
    clock = Clock(NOW)
    secure_origin = "https://testserver"
    app, flow, _ = build_app(
        clock,
        StubGoogleProvider(clock),
        session_cookie_name="__Host-clipah_session",
        session_cookie_secure=True,
        frontend_origin=secure_origin,
    )
    browser = Browser(app, origin=secure_origin)

    response = sign_in(browser, flow)

    attributes = _cookie_attributes(response)
    assert "__Host-clipah_session" in attributes
    for name, values in attributes.items():
        assert "secure" in values, name
        assert values["path"] == "/", name


@pytest.mark.integration
def test_a_session_value_planted_before_login_is_never_adopted(
    engine: Engine, clean_database: None
) -> None:
    """Signing in must issue a new Session rather than bless whatever the browser carried."""
    del engine, clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    browser.cookies.set(SESSION_COOKIE, PLANTED_TOKEN, domain="testserver")

    response = sign_in(browser, flow)

    issued = _cookie_values(response)[SESSION_COOKIE]
    assert issued != PLANTED_TOKEN
    assert issued != ""


@pytest.mark.integration
def test_a_planted_session_value_is_refused_on_its_own(
    engine: Engine, clean_database: None
) -> None:
    """The planted value must authenticate nobody, before or after a real login exists."""
    del engine, clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    member = Browser(app)
    sign_in(member, flow)
    attacker = Browser(app)
    attacker.cookies.set(SESSION_COOKIE, PLANTED_TOKEN, domain="testserver")

    response = attacker.get("/api/v1/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.integration
def test_two_logins_never_share_one_session_value(engine: Engine, clean_database: None) -> None:
    """One browser's Session must be worthless in another, even for the same account."""
    del engine, clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    first = Browser(app)
    second = Browser(app)

    sign_in(first, flow)
    sign_in(second, flow)

    assert first.cookies.get(SESSION_COOKIE) != second.cookies.get(SESSION_COOKIE)


@pytest.mark.integration
@pytest.mark.parametrize(
    "parameter",
    ["next=https://attacker.example/steal", "redirect_uri=https://attacker.example/steal"],
)
def test_no_query_parameter_can_choose_where_login_sends_the_browser(
    engine: Engine, clean_database: None, parameter: str
) -> None:
    """A login that honoured a caller-supplied return address is an open redirect."""
    del engine, clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)

    started = browser.get(f"/api/v1/auth/google/start?{parameter}")
    completed = browser.get(
        f"/api/v1/auth/google/callback?code=authorization-code&state={flow.last_pending_state}"
        f"&{parameter}"
    )

    assert started.headers["location"].startswith("https://accounts.google.com/")
    assert completed.headers["location"] == SITE_ORIGIN
    assert "attacker.example" not in started.headers["location"]
    assert "attacker.example" not in completed.headers["location"]


@pytest.mark.integration
def test_no_response_offers_another_site_permission_to_read_it(
    engine: Engine, clean_database: None
) -> None:
    """Without an Access-Control-Allow-Origin header, a cross-site read cannot succeed."""
    del engine, clean_database
    browser, workspace_id = _signed_in()

    read = browser.get("/api/v1/me", headers={"Origin": FOREIGN_ORIGIN})
    listed = browser.get(
        f"/api/v1/projects?workspace_id={workspace_id}", headers={"Origin": FOREIGN_ORIGIN}
    )

    for response in (read, listed):
        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers
        assert "access-control-allow-credentials" not in response.headers


@pytest.mark.integration
def test_a_cross_site_preflight_is_not_granted(engine: Engine, clean_database: None) -> None:
    """A granted preflight would let another origin send the unsafe request itself."""
    del engine, clean_database
    browser, _ = _signed_in()

    response = browser.request(
        "OPTIONS",
        "/api/v1/workspaces",
        headers={
            "Origin": FOREIGN_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-csrf-token",
        },
    )

    assert "access-control-allow-origin" not in response.headers


@pytest.mark.integration
def test_the_session_token_is_never_written_to_a_body_a_header_or_a_log(
    engine: Engine, clean_database: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A token repeated anywhere but the cookie outlives the browser that held it."""
    del engine, clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)

    with caplog.at_level(logging.DEBUG):
        sign_in(browser, flow)
        token = browser.cookies.get(SESSION_COOKIE)
        assert token is not None
        responses = [
            browser.get("/api/v1/me"),
            browser.get("/api/v1/me/sessions"),
            browser.get("/api/v1/workspaces"),
        ]

    for response in responses:
        assert token not in response.text
        assert token not in json.dumps(dict(response.headers))
    assert token not in "\n".join(record.getMessage() for record in caplog.records)


def _signed_in() -> tuple[Browser, UUID]:
    """Sign one member in and return their browser and the Workspace login created."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    return browser, workspace_id


def _unsafe(browser: Browser, *, headers: dict[str, str]) -> Response:
    """Send one representative state-changing request with exactly the headers given.

    The harness normally supplies the proofs a first-party client would; these tests are
    about their absence, so an empty `Origin` is sent unless the caller named one, which
    is how a request that carries no usable origin at all reaches the dependency.
    """
    return browser.request(
        "POST",
        "/api/v1/workspaces",
        json={"name": "Studio Team"},
        headers={"Origin": "", **headers},
        csrf_token="",
    )


def _cookie_values(response: Response) -> dict[str, str]:
    """Read the value of every cookie one response sets, straight from its headers."""
    values: dict[str, str] = {}
    for header in response.headers.get_list("set-cookie"):
        name, _, value = header.split(";")[0].partition("=")
        values[name.strip()] = value
    return values


def _cookie_attributes(response: Response) -> dict[str, dict[str, Any]]:
    """Read every cookie one response sets, with its attributes lowercased."""
    found: dict[str, dict[str, Any]] = {}
    for header in response.headers.get_list("set-cookie"):
        first, *rest = header.split(";")
        name = first.split("=", 1)[0].strip()
        attributes: dict[str, Any] = {}
        for attribute in rest:
            key, _, value = attribute.strip().partition("=")
            attributes[key.lower()] = value.lower() if value else True
        found[name] = attributes
    return found
