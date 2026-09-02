"""Integration contracts for the development-only browser-test seeding helper.

End-to-end browser tests need a Session that a real browser can carry, and the only other
way to obtain one is a live Google login. This helper mints one directly, which is exactly
why it must refuse to run against a production deployment.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from sqlalchemy import Engine

from clipah.config import Environment
from clipah.db import session_scope
from clipah.dev.seed import (
    SeededBrowserSession,
    SeedRefusedError,
    main,
    seed_browser_session,
)
from harness import NOW, SESSION_SECRET, Browser, Clock, StubGoogleProvider, build_app
from support import runtime_settings


def _seed() -> SeededBrowserSession:
    """Seed one signed-in member the way the browser-test entry point does."""
    settings = runtime_settings(session_secret=SESSION_SECRET)
    with session_scope(settings=settings) as session:
        return seed_browser_session(
            session,
            settings=settings,
            email="seeded@example.com",
            display_name="Seeded Member",
            workspace_name="Seeded Workspace",
            now=NOW,
        )


def _browser_carrying(seeded: SeededBrowserSession) -> Browser:
    """Open a browser holding exactly the cookies the seeding helper handed out."""
    clock = Clock(NOW)
    app, _, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    browser.cookies.set(seeded.session_cookie_name, seeded.session_token)
    browser.cookies.set(seeded.csrf_cookie_name, seeded.csrf_token)
    return browser


@pytest.mark.integration
def test_seeding_hands_back_a_session_a_browser_can_carry(
    engine: Engine, clean_database: None
) -> None:
    """The seeded token must authenticate exactly like one issued by a real login."""
    response = _browser_carrying(_seed()).get("/api/v1/me")

    assert response.status_code == 200
    assert response.json()["email"] == "seeded@example.com"


@pytest.mark.integration
def test_seeding_creates_the_personal_workspace_the_member_lands_in(
    engine: Engine, clean_database: None
) -> None:
    """A seeded member starts in their own Workspace, as a first login would leave them."""
    seeded = _seed()

    workspaces = _browser_carrying(seeded).get("/api/v1/workspaces").json()["workspaces"]

    assert [workspace["id"] for workspace in workspaces] == [str(seeded.workspace_id)]
    assert workspaces[0]["kind"] == "personal"


@pytest.mark.integration
def test_seeding_refuses_to_mint_a_session_in_production(
    engine: Engine, clean_database: None
) -> None:
    """A helper that skips authentication must never be usable against real accounts."""
    settings = runtime_settings(session_secret=SESSION_SECRET)
    production = settings.model_copy(update={"environment": Environment.PRODUCTION})

    with session_scope(settings=settings) as session, pytest.raises(SeedRefusedError):
        seed_browser_session(
            session,
            settings=production,
            email="seeded@example.com",
            display_name="Seeded Member",
            workspace_name="Seeded Workspace",
            now=NOW,
        )


@pytest.mark.integration
def test_the_command_line_prints_cookies_a_test_runner_can_read(
    engine: Engine, clean_database: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """The browser test runner reads this output, so it has to be plain JSON."""
    exit_code = main(
        ["--email", "cli@example.com", "--display-name", "CLI", "--workspace-name", "CLI"],
        settings=runtime_settings(session_secret=SESSION_SECRET),
    )

    printed = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert printed["sessionCookieName"] == "clipah_session"
    assert printed["csrfCookieName"] == "clipah_csrf"
    assert UUID(printed["workspaceId"])
    assert printed["sessionToken"] and printed["csrfToken"]
