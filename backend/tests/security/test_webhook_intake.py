"""Nothing arriving at a webhook route is trusted until its signature says so.

Each provider's signature, replay window, and tampering behaviour is proven in that
provider's own contract suite. What is proven here is the property that has to hold for
*every* webhook route, including one added next year: an unsigned delivery is never
accepted, a browser Session buys nothing at these routes, and a refusal repeats none of
the body it refused.

The routes are enumerated from the application rather than listed here, so a new webhook
route is covered the moment it exists.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine

from harness import NOW, Browser, Clock, StubGoogleProvider, build_app, sign_in

CANARY = "canary-payload-value-8f2b1c9d"
UNSIGNED_BODY: dict[str, Any] = {
    "status": "OK",
    "request_id": CANARY,
    "publish_id": CANARY,
    "signed_request": CANARY,
}


def _post_paths(app: FastAPI) -> set[str]:
    """Read every webhook POST path out of the routing tree, included routers and all."""
    found: set[str] = set()
    for route in _routes(app.routes):
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if path.startswith("/api/v1/webhooks/") and "POST" in methods:
            found.add(path)
    return found


def _routes(routes: Any) -> list[Any]:
    """Flatten the routing tree, because an included router is itself one entry."""
    flattened: list[Any] = []
    for entry in routes:
        included = getattr(entry, "original_router", None)
        flattened.extend(_routes(included.routes) if included is not None else [entry])
    return flattened


def webhook_paths() -> tuple[str, ...]:
    """Every POST route the application serves under the webhook prefix."""
    clock = Clock(NOW)
    app, _, _ = build_app(clock, StubGoogleProvider(clock))
    return tuple(sorted(_post_paths(app)))


PATHS = webhook_paths()


@pytest.mark.integration
def test_the_application_serves_the_webhook_routes_this_suite_believes_it_does(
    engine: Engine, clean_database: None
) -> None:
    """An empty enumeration would make every test below pass while proving nothing."""
    del engine, clean_database

    assert PATHS, "no webhook route was found to attack"
    assert all(path.startswith("/api/v1/webhooks/") for path in PATHS)


@pytest.mark.integration
@pytest.mark.parametrize("path", PATHS)
def test_an_unsigned_delivery_is_never_accepted(
    engine: Engine, clean_database: None, path: str
) -> None:
    """Anyone can post to these routes, so the signature is the whole of the authority.

    A deployment holding no key for that provider answers "unavailable" and one holding a
    key answers "invalid". Neither is acceptance, which is the property this guard is for;
    each provider's configured rejection is proven in its own contract suite.
    """
    del engine, clean_database
    clock = Clock(NOW)
    app, _, _ = build_app(clock, StubGoogleProvider(clock))
    caller = Browser(app)

    response = caller.request("POST", path, json=UNSIGNED_BODY)

    assert response.status_code >= 400, response.text
    assert response.json()["error"]["code"] in {
        "SERVICE_UNAVAILABLE",
        "FAL_WEBHOOK_INVALID",
        "TIKTOK_WEBHOOK_INVALID",
        "INSTAGRAM_WEBHOOK_INVALID",
        "VALIDATION_ERROR",
    }


@pytest.mark.integration
@pytest.mark.parametrize("path", PATHS)
def test_a_browser_session_grants_no_authority_at_a_webhook_route(
    engine: Engine, clean_database: None, path: str
) -> None:
    """A webhook is authenticated by its signature, never by whoever happens to be signed in."""
    del engine, clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    member = Browser(app)
    sign_in(member, flow)

    response = member.request("POST", path, json=UNSIGNED_BODY)

    assert response.status_code >= 400, response.text


@pytest.mark.integration
@pytest.mark.parametrize("path", PATHS)
def test_a_refused_delivery_repeats_nothing_it_was_sent(
    engine: Engine, clean_database: None, path: str
) -> None:
    """A refusal that echoed the body would turn every webhook route into a reflector."""
    del engine, clean_database
    clock = Clock(NOW)
    app, _, _ = build_app(clock, StubGoogleProvider(clock))
    caller = Browser(app)

    response = caller.request("POST", path, json=UNSIGNED_BODY)

    assert CANARY not in response.text
    assert CANARY not in json.dumps(dict(response.headers))
