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

from clipah.assets.storage import FakeObjectStore, StoredObject
from clipah.config import Environment, Settings
from clipah.db import RuntimeRole, session_scope
from clipah.dev.seed import (
    SeededBrowserSession,
    SeededProject,
    SeedRefusedError,
    main,
    seed_analysed_project,
    seed_browser_session,
)
from harness import NOW, SESSION_SECRET, Browser, Clock, StubGoogleProvider, build_app
from support import RUNTIME_LOGINS, runtime_settings


def _seeding_settings() -> Settings:
    """Settings that carry both logins, because seeding writes what both roles own."""
    return runtime_settings(
        session_secret=SESSION_SECRET,
        worker_database_url=RUNTIME_LOGINS[RuntimeRole.WORKER][1],
    )


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


def _browser_carrying(
    seeded: SeededBrowserSession, *, store: FakeObjectStore | None = None
) -> Browser:
    """Open a browser holding exactly the cookies the seeding helper handed out."""
    clock = Clock(NOW)
    # The editor asks for a signed proxy before it draws, so the harness needs a store.
    app, _, _ = build_app(
        clock, StubGoogleProvider(clock), object_store=store or FakeObjectStore(now=clock)
    )
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


@pytest.mark.integration
def test_a_seeded_analysed_project_exposes_one_reviewable_clip(
    engine: Engine, clean_database: None
) -> None:
    """A browser test cannot stage a Clip Candidate through any API, so seeding must."""
    del clean_database
    seeded = _seed()
    project = _seed_clip(seeded)
    browser = _browser_carrying(seeded)

    response = browser.get(
        f"/api/v1/projects/{project.project_id}/candidates?workspace_id={seeded.workspace_id}"
    )

    assert response.status_code == 200
    candidates = response.json()["candidates"]
    assert [candidate["id"] for candidate in candidates] == [str(project.candidate_id)]


@pytest.mark.integration
def test_a_seeded_clip_can_be_opened_as_an_edit(engine: Engine, clean_database: None) -> None:
    """The scenarios this unblocks all start by opening the editor on a real clip."""
    del clean_database
    seeded = _seed()
    project = _seed_clip(seeded)
    browser = _browser_carrying(seeded)

    created = browser.request(
        "POST",
        f"/api/v1/projects/{project.project_id}/candidates/{project.candidate_id}/edits"
        f"?workspace_id={seeded.workspace_id}",
        json=None,
    )

    assert created.status_code == 201
    composition = created.json()["composition"]
    assert composition["sourceAssetId"] == str(project.source_asset_id)
    # The captions are the transcript's own words, which is what makes a trim meaningful.
    assert [word["text"] for word in composition["captions"]["words"]] != []


@pytest.mark.integration
def test_a_seeded_project_offers_the_proxy_the_editor_plays(
    engine: Engine, clean_database: None
) -> None:
    """The editor asks for a proxy before it draws, so a seeded clip must have one."""
    del clean_database
    seeded = _seed()
    project = _seed_clip(seeded)
    store = FakeObjectStore(now=Clock(NOW))
    browser = _browser_carrying(seeded, store=store)
    # A real store signs a key whether or not an object sits behind it; the fake refuses
    # to sign what it has never been given, so the proxy the seed named is put there.
    store.object_bodies[_proxy_key(seeded, project)] = b"proxy"
    store.objects[_proxy_key(seeded, project)] = StoredObject(
        key=_proxy_key(seeded, project),
        content_length=5,
        content_type="video/mp4",
        sha256=bytes(32),
    )

    response = browser.get(
        f"/api/v1/projects/{project.project_id}/proxy?workspace_id={seeded.workspace_id}"
    )

    assert response.status_code == 200
    assert response.json()["durationMs"] > 0


@pytest.mark.integration
def test_seeding_a_clip_is_refused_outside_development(
    engine: Engine, clean_database: None
) -> None:
    """Inventing analysed media is exactly what must never touch a real deployment."""
    del clean_database
    seeded = _seed()
    settings = _seeding_settings().model_copy(update={"environment": Environment.PRODUCTION})

    with pytest.raises(SeedRefusedError):
        seed_analysed_project(
            settings=settings,
            workspace_id=seeded.workspace_id,
            user_id=seeded.user_id,
            name="Refused",
            now=NOW,
        )


@pytest.mark.integration
def test_the_command_line_prints_the_seeded_clip(
    engine: Engine, clean_database: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """The Playwright runner reads these identifiers, so they have to reach stdout."""
    del clean_database
    settings = _seeding_settings()

    exit_code = main(
        ["--email", "clip@example.com", "--with-clip", "Seeded Episode"], settings=settings
    )

    assert exit_code == 0
    printed = json.loads(capsys.readouterr().out)
    assert UUID(printed["project"]["projectId"])
    assert UUID(printed["project"]["candidateId"])
    assert printed["project"]["name"] == "Seeded Episode"


def _proxy_key(seeded: SeededBrowserSession, project: SeededProject) -> str:
    """The key the seed gave the Project's proxy rendition."""
    return f"workspaces/{seeded.workspace_id}/projects/{project.project_id}/derivatives/proxy.mp4"


def _seed_clip(seeded: SeededBrowserSession) -> SeededProject:
    """Stage one analysed Project inside the Workspace a seeded member already owns."""
    return seed_analysed_project(
        settings=_seeding_settings(),
        workspace_id=seeded.workspace_id,
        user_id=seeded.user_id,
        name="Seeded Episode",
        now=NOW,
    )


@pytest.mark.integration
def test_seeding_a_clip_needs_the_login_the_worker_half_is_written_with(
    engine: Engine, clean_database: None
) -> None:
    """Seeding writes what two roles own, so one role's credentials are not enough."""
    del clean_database
    seeded = _seed()

    with pytest.raises(SeedRefusedError):
        seed_analysed_project(
            settings=runtime_settings(session_secret=SESSION_SECRET),
            workspace_id=seeded.workspace_id,
            user_id=seeded.user_id,
            name="No Worker Login",
            now=NOW,
        )
