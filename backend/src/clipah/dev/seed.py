"""Mint one signed-in browser Session for local end-to-end tests.

Browser tests need a Session a real browser can carry, and the only other way to obtain
one is a live Google login. This helper skips authentication entirely, so it refuses to
run against a production deployment and is never imported by the application itself.
"""

from __future__ import annotations

import argparse
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from clipah.auth.models import SessionPolicy
from clipah.auth.sessions import issue_session
from clipah.config import Environment, Settings
from clipah.db import create_user_with_personal_workspace, session_scope, set_actor_context
from clipah.workspaces.use_cases import workspace_slug

CSRF_TOKEN_BYTES = 32


class SeedRefusedError(RuntimeError):
    """Raised when seeding is attempted somewhere real accounts could be affected."""


@dataclass(frozen=True, slots=True)
class SeededBrowserSession:
    """Everything a browser needs to arrive already signed in."""

    user_id: UUID
    workspace_id: UUID
    session_cookie_name: str
    session_token: str
    csrf_cookie_name: str
    csrf_token: str


def seed_browser_session(
    session: Session,
    *,
    settings: Settings,
    email: str,
    display_name: str,
    workspace_name: str,
    now: datetime,
) -> SeededBrowserSession:
    """Create one User with their personal Workspace and a Session for that User."""
    if settings.environment is Environment.PRODUCTION:
        raise SeedRefusedError("browser-test seeding is refused outside development")

    provisioned = create_user_with_personal_workspace(
        session,
        primary_email=email,
        display_name=display_name,
        workspace_name=workspace_name,
        workspace_slug=f"{workspace_slug(workspace_name)}-{uuid4().hex[:8]}",
    )
    set_actor_context(session, user_id=provisioned.user.id)
    issued = issue_session(
        session,
        user_id=provisioned.user.id,
        secret=_session_secret(settings),
        policy=_policy(settings),
        now=now,
    )
    return SeededBrowserSession(
        user_id=provisioned.user.id,
        workspace_id=provisioned.workspace.id,
        session_cookie_name=settings.session_cookie_name,
        session_token=issued.token,
        csrf_cookie_name=settings.csrf_cookie_name,
        csrf_token=_csrf_token(),
    )


def main(argv: list[str] | None = None, *, settings: Settings | None = None) -> int:
    """Seed one member from the command line and print their cookies as JSON."""
    parser = argparse.ArgumentParser(description="Seed one signed-in member for browser tests.")
    parser.add_argument("--email", default="e2e@example.com")
    parser.add_argument("--display-name", default="End To End")
    parser.add_argument("--workspace-name", default="End To End")
    arguments = parser.parse_args(argv)

    settings = settings or Settings()
    with session_scope(settings=settings) as session:
        seeded = seed_browser_session(
            session,
            settings=settings,
            email=arguments.email,
            display_name=arguments.display_name,
            workspace_name=arguments.workspace_name,
            now=datetime.now(tz=UTC),
        )
    print(json.dumps(_as_json(seeded)))
    return 0


def _as_json(seeded: SeededBrowserSession) -> dict[str, str]:
    """Render one seeded Session for a test runner written in another language."""
    return {
        "userId": str(seeded.user_id),
        "workspaceId": str(seeded.workspace_id),
        "sessionCookieName": seeded.session_cookie_name,
        "sessionToken": seeded.session_token,
        "csrfCookieName": seeded.csrf_cookie_name,
        "csrfToken": seeded.csrf_token,
    }


def _policy(settings: Settings) -> SessionPolicy:
    """Give a seeded Session the same deadlines a real login would have given it."""
    return SessionPolicy(
        idle_ttl=timedelta(minutes=settings.session_idle_ttl_minutes),
        absolute_ttl=timedelta(minutes=settings.session_absolute_ttl_minutes),
        recent_auth_window=timedelta(minutes=settings.session_recent_auth_ttl_minutes),
    )


def _session_secret(settings: Settings) -> str:
    """Read the secret Sessions are keyed with, refusing to invent one."""
    secret = settings.session_secret
    if secret is None:
        raise SeedRefusedError("CLIPAH_SESSION_SECRET must be configured to seed a Session")
    return secret.get_secret_value()


def _csrf_token() -> str:
    """Mint the double-submit token the browser echoes back on unsafe requests."""
    return secrets.token_urlsafe(CSRF_TOKEN_BYTES)


if __name__ == "__main__":
    raise SystemExit(main())
