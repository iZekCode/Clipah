"""What one login ceremony is bound to, and what happens when any binding is broken.

The Google ceremony is the only way an account comes into existence, so the bindings that
hold it together are the ones an attacker wants loose: the `state` that ties the callback
to the browser that started it, the `nonce` that ties the ID token to that same ceremony,
the PKCE verifier that ties the authorization code to it, and the lifetime that stops any
of them being reused tomorrow. Each test here breaks exactly one of them.

The social-connection ceremony is attacked in `test_oauth_grants.py`; this suite is about
the sign-in ceremony, which issues the Session everything else depends on.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import Engine

from harness import (
    NOW,
    SESSION_SECRET,
    Browser,
    Clock,
    StubGoogleProvider,
    build_app,
    sign_in,
)

CEREMONY_COOKIE = "clipah_oidc"
SESSION_COOKIE = "clipah_session"


@pytest.mark.integration
def test_the_authorization_request_proves_possession_with_pkce_s256(
    engine: Engine, clean_database: None
) -> None:
    """A stolen authorization code is worthless without the verifier that started it."""
    del engine, clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)

    started = browser.get("/api/v1/auth/google/start")
    query = _query_of(started.headers["location"])
    browser.get(f"/api/v1/auth/google/callback?code=code-1&state={flow.last_pending_state}")

    assert query["code_challenge_method"] == ["S256"]
    redeemed = flow.stub.exchanges[-1]["code_verifier"]
    assert query["code_challenge"] == [_s256(redeemed)]
    assert redeemed not in started.headers["location"]


@pytest.mark.integration
def test_the_ceremony_cookie_reveals_none_of_the_bindings_it_carries(
    engine: Engine, clean_database: None
) -> None:
    """The cookie travels through the browser, so its contents must be opaque there."""
    del engine, clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)

    browser.get("/api/v1/auth/google/start")

    sealed = browser.cookies.get(CEREMONY_COOKIE)
    assert sealed is not None
    assert flow.last_pending_state not in sealed
    assert flow.stub.nonce not in sealed


@pytest.mark.integration
def test_a_callback_cannot_be_replayed_once_it_has_signed_someone_in(
    engine: Engine, clean_database: None
) -> None:
    """The ceremony is single-use, so its cookie is gone the moment it is redeemed."""
    del engine, clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)

    first = sign_in(browser, flow)
    replayed = browser.get(
        f"/api/v1/auth/google/callback?code=authorization-code&state={flow.last_pending_state}"
    )

    assert first.status_code == 302
    assert browser.cookies.get(CEREMONY_COOKIE) in {None, ""}
    assert replayed.status_code == 401
    assert replayed.json()["error"]["code"] == "AUTHENTICATION_FAILED"


@pytest.mark.integration
def test_a_ceremony_that_has_expired_is_no_longer_redeemable(
    engine: Engine, clean_database: None
) -> None:
    """A redirect left open in a tab overnight must not still be worth a Session."""
    del engine, clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    browser.get("/api/v1/auth/google/start")

    clock.advance(timedelta(hours=1))
    response = browser.get(
        f"/api/v1/auth/google/callback?code=authorization-code&state={flow.last_pending_state}"
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_FAILED"
    assert SESSION_COOKIE not in _set_cookie_names(response)


@pytest.mark.integration
def test_a_ceremony_sealed_by_another_deployment_is_refused(
    engine: Engine, clean_database: None
) -> None:
    """The seal is keyed to this deployment, so another's cookie proves nothing here."""
    del engine, clean_database
    clock = Clock(NOW)
    theirs, their_flow, _ = build_app(
        clock, StubGoogleProvider(clock), session_secret=f"{SESSION_SECRET}-other-deployment"
    )
    ours, _, _ = build_app(clock, StubGoogleProvider(clock))
    attacker = Browser(theirs)
    attacker.get("/api/v1/auth/google/start")
    stolen = attacker.cookies.get(CEREMONY_COOKIE)
    assert stolen is not None

    victim = Browser(ours)
    victim.cookies.set(CEREMONY_COOKIE, stolen, domain="testserver")
    response = victim.get(
        f"/api/v1/auth/google/callback?code=authorization-code&state={their_flow.last_pending_state}"
    )

    assert response.status_code == 401
    assert SESSION_COOKIE not in _set_cookie_names(response)


@pytest.mark.integration
@pytest.mark.parametrize("mangled", ["{sealed}-tampered", "not-a-sealed-ceremony"])
def test_a_ceremony_cookie_that_was_edited_is_refused(
    engine: Engine, clean_database: None, mangled: str
) -> None:
    """Editing the seal must fail closed rather than degrade into an unbound ceremony."""
    del engine, clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    started = Browser(app)
    started.get("/api/v1/auth/google/start")
    sealed = started.cookies.get(CEREMONY_COOKIE) or ""
    browser = Browser(app)
    browser.cookies.set(CEREMONY_COOKIE, mangled.format(sealed=sealed), domain="testserver")

    response = browser.get(
        f"/api/v1/auth/google/callback?code=authorization-code&state={flow.last_pending_state}"
    )

    assert response.status_code == 401
    assert SESSION_COOKIE not in _set_cookie_names(response)


@pytest.mark.integration
def test_a_state_from_a_different_ceremony_is_refused(engine: Engine, clean_database: None) -> None:
    """Two tabs must not be able to complete each other's login."""
    del engine, clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    first = Browser(app)
    second = Browser(app)
    first.get("/api/v1/auth/google/start")
    first_state = flow.last_pending_state
    second.get("/api/v1/auth/google/start")

    response = second.get(
        f"/api/v1/auth/google/callback?code=authorization-code&state={first_state}"
    )

    assert response.status_code == 401
    assert SESSION_COOKIE not in _set_cookie_names(response)


@pytest.mark.integration
def test_an_id_token_bound_to_another_ceremony_is_refused(
    engine: Engine, clean_database: None
) -> None:
    """The nonce is what stops a token minted elsewhere from being replayed here."""
    del engine, clean_database
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock, nonce="a-nonce-from-somewhere-else")
    app, flow, _ = build_app(clock, provider)
    browser = Browser(app)
    browser.get("/api/v1/auth/google/start")

    response = browser.get(
        f"/api/v1/auth/google/callback?code=authorization-code&state={flow.last_pending_state}"
    )

    assert response.status_code == 401
    assert SESSION_COOKIE not in _set_cookie_names(response)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("issuer", "https://accounts.evil.example"),
        ("audience", "another-client-id.apps.googleusercontent.com"),
    ],
)
def test_an_id_token_from_the_wrong_issuer_or_audience_is_refused(
    engine: Engine, clean_database: None, claim: str, value: str
) -> None:
    """A token Google never minted for this client must not create an account."""
    del engine, clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock, **{claim: value}))
    browser = Browser(app)
    browser.get("/api/v1/auth/google/start")

    response = browser.get(
        f"/api/v1/auth/google/callback?code=authorization-code&state={flow.last_pending_state}"
    )

    assert response.status_code == 401
    assert SESSION_COOKIE not in _set_cookie_names(response)


@pytest.mark.integration
def test_a_refused_callback_repeats_neither_the_code_nor_the_state_it_refused(
    engine: Engine, clean_database: None
) -> None:
    """A refusal that echoed either one would write a live credential into a log."""
    del engine, clean_database
    clock = Clock(NOW)
    app, _, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)

    response = browser.get(
        "/api/v1/auth/google/callback?code=a-live-authorization-code&state=a-guessed-state"
    )

    assert response.status_code == 401
    assert "a-live-authorization-code" not in response.text
    assert "a-guessed-state" not in response.text


def _query_of(url: str) -> dict[str, list[str]]:
    """Read the query of one redirect the way the provider would."""
    return parse_qs(urlsplit(url).query)


def _s256(verifier: str) -> str:
    """Spell one PKCE challenge exactly as RFC 7636 does."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _set_cookie_names(response: Any) -> set[str]:
    """Every cookie name one response sets, so an absent Session is observable."""
    return {header.split("=", 1)[0].strip() for header in response.headers.get_list("set-cookie")}
