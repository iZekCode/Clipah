"""Request-scoped authentication, CSRF, and cookie handling for the HTTP layer."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.orm import Session
from starlette.responses import Response

from clipah.api.errors import ApiError
from clipah.assets.storage import ObjectStore
from clipah.auth.google_oidc import AUTHORIZATION_LIFETIME, AuthlibGoogleProvider, GoogleOidcFlow
from clipah.auth.models import (
    AuthenticatedSession,
    SessionInvalidError,
    SessionPolicy,
    UserDisabledError,
)
from clipah.auth.sessions import authenticate_session
from clipah.config import Settings
from clipah.db import session_scope, set_actor_context, set_workspace_context
from clipah.workspaces.authorization import (
    DatabaseWorkspaceAuthorizer,
    requires_recent_authentication,
)
from clipah.workspaces.models import (
    WorkspaceAccess,
    WorkspaceAction,
    WorkspaceNotFoundError,
    WorkspacePermissionError,
)

CSRF_HEADER = "X-CSRF-Token"
CSRF_TOKEN_BYTES = 32
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def utcnow() -> datetime:
    """Return the current instant as the aware UTC value every deadline is measured against."""
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class AuthComponents:
    """Authentication collaborators supplied by the composition root."""

    oidc_flow: Callable[[], GoogleOidcFlow]
    open_session: Callable[[], AbstractContextManager[Session]]
    policy: SessionPolicy = field(default_factory=SessionPolicy)
    now: Callable[[], datetime] = utcnow
    authorization_lifetime: timedelta = AUTHORIZATION_LIFETIME


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """The actor behind one request, proven by a live Clipah Session."""

    user_id: UUID
    session: AuthenticatedSession


def session_policy(settings: Settings) -> SessionPolicy:
    """Build the Session deadline policy this deployment enforces."""
    return SessionPolicy(
        idle_ttl=timedelta(minutes=settings.session_idle_ttl_minutes),
        absolute_ttl=timedelta(minutes=settings.session_absolute_ttl_minutes),
        recent_auth_window=timedelta(minutes=settings.session_recent_auth_ttl_minutes),
    )


def default_auth_components(settings: Settings) -> AuthComponents:
    """Compose the production authentication collaborators from configuration."""

    def build_flow() -> GoogleOidcFlow:
        client_id = settings.google_oidc_client_id
        client_secret = settings.google_oidc_client_secret
        redirect_uri = settings.google_oidc_redirect_uri
        if client_id is None or client_secret is None or redirect_uri is None:
            raise ApiError(status_code=503, code="SERVICE_UNAVAILABLE")
        provider = AuthlibGoogleProvider(
            client_id=client_id, client_secret=client_secret.get_secret_value()
        )
        return GoogleOidcFlow(provider=provider, client_id=client_id, redirect_uri=redirect_uri)

    return AuthComponents(
        oidc_flow=build_flow,
        open_session=lambda: session_scope(settings=settings),
        policy=session_policy(settings),
    )


def settings_for(request: Request) -> Settings:
    """Return the settings the application was created with."""
    settings: Settings = request.app.state.settings
    return settings


def object_store_for(request: Request) -> ObjectStore:
    """Resolve the configured object-store boundary without exposing configuration details."""
    store: ObjectStore | None = request.app.state.object_store
    if store is None:
        raise ApiError(status_code=503, code="SERVICE_UNAVAILABLE")
    return store


def auth_components_for(request: Request) -> AuthComponents:
    """Return the authentication collaborators the application was created with."""
    components: AuthComponents = request.app.state.auth_components
    return components


def session_secret_for(settings: Settings) -> str:
    """Return the secret that keys Session-adjacent digests and sealed cookies."""
    if settings.session_secret is None:
        raise ApiError(status_code=503, code="SERVICE_UNAVAILABLE")
    return settings.session_secret.get_secret_value()


def open_database_session(request: Request) -> Iterator[Session]:
    """Run one request inside a single least-privilege database transaction."""
    with auth_components_for(request).open_session() as session:
        yield session


DatabaseSession = Annotated[Session, Depends(open_database_session)]


def require_authenticated_user(request: Request, session: DatabaseSession) -> CurrentUser:
    """Resolve the Session cookie into an actor, or refuse the request."""
    settings = settings_for(request)
    components = auth_components_for(request)
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise ApiError(status_code=401, code="UNAUTHENTICATED")
    try:
        authenticated = authenticate_session(
            session, token=token, policy=components.policy, now=components.now()
        )
    except SessionInvalidError as error:
        # Keep the closing write an expired token earns; refusing the request must not undo it.
        session.commit()
        raise ApiError(status_code=401, code="UNAUTHENTICATED") from error
    except UserDisabledError as error:
        raise ApiError(status_code=403, code="ACCOUNT_DISABLED") from error

    set_actor_context(session, user_id=authenticated.user_id)
    return CurrentUser(user_id=authenticated.user_id, session=authenticated)


CurrentUserDependency = Annotated[CurrentUser, Depends(require_authenticated_user)]


@dataclass(frozen=True, slots=True)
class CurrentWorkspace:
    """The tenant behind one request, proven by a live Workspace Membership."""

    access: WorkspaceAccess
    user: CurrentUser


def require_workspace(
    action: WorkspaceAction,
) -> Callable[[Request, Session, CurrentUser, UUID], CurrentWorkspace]:
    """Build the dependency that authorizes one Workspace action and installs its context."""

    def dependency(
        request: Request,
        session: DatabaseSession,
        user: CurrentUserDependency,
        workspace_id: UUID,
    ) -> CurrentWorkspace:
        components = auth_components_for(request)
        try:
            access = DatabaseWorkspaceAuthorizer(session).require(
                user_id=user.user_id, workspace_id=workspace_id, action=action
            )
        except WorkspaceNotFoundError as error:
            raise ApiError(status_code=404, code="NOT_FOUND") from error
        except WorkspacePermissionError as error:
            raise ApiError(status_code=403, code="FORBIDDEN") from error

        if requires_recent_authentication(action) and not user.session.has_recent_authentication(
            policy=components.policy, now=components.now()
        ):
            raise ApiError(status_code=403, code="RECENT_AUTHENTICATION_REQUIRED")

        # Every Workspace-scoped statement below runs under this tenant's row policies.
        set_workspace_context(session, workspace_id=workspace_id)
        return CurrentWorkspace(access=access, user=user)

    return dependency


def require_csrf(request: Request) -> None:
    """Require both a trusted originating site and a matching double-submit token."""
    if request.method not in UNSAFE_METHODS:
        return
    settings = settings_for(request)
    _assert_trusted_origin(request, settings=settings)

    cookie_token = request.cookies.get(settings.csrf_cookie_name)
    header_token = request.headers.get(CSRF_HEADER)
    if not cookie_token or not header_token:
        raise ApiError(status_code=403, code="CSRF_FAILED")
    if not secrets.compare_digest(cookie_token, header_token):
        raise ApiError(status_code=403, code="CSRF_FAILED")


def _assert_trusted_origin(request: Request, *, settings: Settings) -> None:
    """Refuse any state-changing request that cannot prove it came from our own site."""
    expected = settings.frontend_origin or _origin_of(str(request.base_url))
    presented = request.headers.get("origin") or _origin_of(request.headers.get("referer"))
    if presented is None or presented != expected:
        raise ApiError(status_code=403, code="CSRF_FAILED")


def _origin_of(url: str | None) -> str | None:
    """Reduce a URL to its scheme-and-host origin."""
    if not url:
        return None
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def issue_csrf_token() -> str:
    """Create the double-submit token a browser echoes back in a request header."""
    return secrets.token_urlsafe(CSRF_TOKEN_BYTES)


def set_session_cookies(
    response: Response,
    *,
    settings: Settings,
    session_token: str,
    csrf_token: str,
    max_age: timedelta,
) -> None:
    """Install the Session and CSRF cookies with host-locked, path-wide attributes."""
    seconds = max(int(max_age.total_seconds()), 0)
    response.set_cookie(
        settings.session_cookie_name,
        session_token,
        max_age=seconds,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite=settings.session_cookie_samesite,
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf_token,
        max_age=seconds,
        path="/",
        secure=settings.session_cookie_secure,
        # The double-submit token is deliberately readable by the first-party client.
        httponly=False,
        samesite=settings.session_cookie_samesite,
    )


def clear_session_cookies(response: Response, *, settings: Settings) -> None:
    """Remove the Session and CSRF cookies from the browser."""
    for name, http_only in (
        (settings.session_cookie_name, True),
        (settings.csrf_cookie_name, False),
    ):
        response.delete_cookie(
            name,
            path="/",
            secure=settings.session_cookie_secure,
            httponly=http_only,
            samesite=settings.session_cookie_samesite,
        )
