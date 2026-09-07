"""Browser-facing login, logout, and Session management endpoints."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import RedirectResponse, Response

from clipah.api.dependencies import (
    CurrentUserDependency,
    DatabaseSession,
    auth_components_for,
    clear_session_cookies,
    issue_csrf_token,
    require_csrf,
    session_secret_for,
    set_session_cookies,
    settings_for,
)
from clipah.api.errors import ApiError
from clipah.auth.identities import resolve_login_identity
from clipah.auth.models import AuthError, IdentityConflictError, UserDisabledError
from clipah.auth.sessions import (
    issue_session,
    list_active_sessions,
    revoke_all_sessions,
    revoke_session,
)
from clipah.auth.state_cookie import open_pending_authorization, seal_pending_authorization
from clipah.config import Settings
from clipah.db import set_actor_context
from clipah.models import User

router = APIRouter(prefix="/api/v1", tags=["auth"])


class CapabilitiesResponse(BaseModel):
    """The features this deployment has switched on, as the browser needs to know them.

    A capability is not an authority: it says the server would accept the request at all,
    and every route still proves membership, role, and freshness for itself.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    authenticated_youtube_import: bool = Field(alias="authenticatedYoutubeImport")
    collaboration: bool


class CurrentUserResponse(BaseModel):
    """The signed-in User, how recently they proved who they are, and what is switched on."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    email: str
    display_name: str | None = Field(alias="displayName")
    avatar_url: str | None = Field(alias="avatarUrl")
    session_id: UUID = Field(alias="sessionId")
    recent_authentication: bool = Field(alias="recentAuthentication")
    capabilities: CapabilitiesResponse


@router.get("/auth/google/start")
def start_google_login(request: Request) -> Response:
    """Begin one Google login ceremony and hand its bindings to the browser."""
    settings = settings_for(request)
    components = auth_components_for(request)
    redirect = components.oidc_flow().start(now=components.now())

    response = RedirectResponse(redirect.authorization_url, status_code=302)
    _set_ceremony_cookie(
        response,
        settings=settings,
        value=seal_pending_authorization(redirect.pending, secret=session_secret_for(settings)),
        max_age=int(components.authorization_lifetime.total_seconds()),
    )
    return response


@router.get("/auth/google/callback")
def complete_google_login(
    request: Request, session: DatabaseSession, code: str, state: str
) -> Response:
    """Redeem the provider callback and start a Clipah Session for the resolved User."""
    settings = settings_for(request)
    components = auth_components_for(request)
    secret = session_secret_for(settings)
    now = components.now()

    sealed = request.cookies.get(settings.oidc_state_cookie_name)
    if not sealed:
        raise ApiError(status_code=401, code="AUTHENTICATION_FAILED")

    try:
        pending = open_pending_authorization(sealed, secret=secret)
        profile = components.oidc_flow().complete(code=code, state=state, pending=pending, now=now)
        resolved = resolve_login_identity(session, profile=profile, now=now)
    except IdentityConflictError as error:
        raise ApiError(status_code=409, code="IDENTITY_CONFLICT") from error
    except UserDisabledError as error:
        raise ApiError(status_code=403, code="ACCOUNT_DISABLED") from error
    except AuthError as error:
        raise ApiError(status_code=401, code="AUTHENTICATION_FAILED") from error

    set_actor_context(session, user_id=resolved.user_id)
    issued = issue_session(
        session,
        user_id=resolved.user_id,
        secret=secret,
        policy=components.policy,
        now=now,
        client_ip=request.client.host if request.client is not None else None,
        user_agent=request.headers.get("user-agent"),
    )

    response = RedirectResponse(settings.frontend_origin or "/", status_code=302)
    set_session_cookies(
        response,
        settings=settings,
        session_token=issued.token,
        csrf_token=issue_csrf_token(),
        max_age=issued.idle_expires_at - now,
    )
    _clear_ceremony_cookie(response, settings=settings)
    return response


@router.post("/auth/logout", status_code=204, dependencies=[Depends(require_csrf)])
def logout(request: Request, session: DatabaseSession, user: CurrentUserDependency) -> Response:
    """End the Session behind this request and clear its browser cookies."""
    settings = settings_for(request)
    components = auth_components_for(request)
    revoke_session(
        session,
        user_id=user.user_id,
        session_id=user.session.session_id,
        now=components.now(),
    )
    response = Response(status_code=204)
    clear_session_cookies(response, settings=settings)
    return response


@router.get("/me", response_model=CurrentUserResponse)
def read_current_user(
    request: Request, session: DatabaseSession, user: CurrentUserDependency
) -> CurrentUserResponse:
    """Describe the authenticated User and the freshness of their authentication."""
    components = auth_components_for(request)
    # Authentication already proved this User is active inside this same transaction.
    record = session.get_one(User, user.user_id)
    return CurrentUserResponse(
        id=record.id,
        email=record.primary_email,
        displayName=record.display_name,
        avatarUrl=record.avatar_url,
        sessionId=user.session.session_id,
        recentAuthentication=user.session.has_recent_authentication(
            policy=components.policy, now=components.now()
        ),
        capabilities=CapabilitiesResponse(
            collaboration=settings_for(request).collaboration_enabled,
            authenticatedYoutubeImport=(
                settings_for(request).authenticated_source_import_enabled
                and settings_for(request).secret_encryption_key is not None
            ),
        ),
    )


@router.get("/me/sessions")
def list_sessions(
    request: Request, session: DatabaseSession, user: CurrentUserDependency
) -> dict[str, Any]:
    """List the Sessions this User could still use, marking the current one."""
    components = auth_components_for(request)
    summaries = list_active_sessions(session, user_id=user.user_id, now=components.now())
    return {
        "sessions": [
            {
                "id": str(summary.session_id),
                "createdAt": summary.created_at.isoformat(),
                "lastSeenAt": summary.last_seen_at.isoformat(),
                "idleExpiresAt": summary.idle_expires_at.isoformat(),
                "absoluteExpiresAt": summary.absolute_expires_at.isoformat(),
                "userAgent": summary.user_agent_summary,
                "current": summary.session_id == user.session.session_id,
            }
            for summary in summaries
        ]
    }


@router.delete("/me/sessions", dependencies=[Depends(require_csrf)])
def revoke_other_sessions(
    request: Request, session: DatabaseSession, user: CurrentUserDependency
) -> dict[str, int]:
    """Sign every other device out while keeping this Session alive."""
    components = auth_components_for(request)
    revoked = revoke_all_sessions(
        session,
        user_id=user.user_id,
        now=components.now(),
        keep_session_id=user.session.session_id,
    )
    return {"revokedCount": revoked}


@router.delete("/me/sessions/{session_id}", status_code=204, dependencies=[Depends(require_csrf)])
def revoke_one_session(
    request: Request, session: DatabaseSession, user: CurrentUserDependency, session_id: UUID
) -> Response:
    """Revoke one of this User's own Sessions, or report it as absent."""
    settings = settings_for(request)
    components = auth_components_for(request)
    if not revoke_session(
        session, user_id=user.user_id, session_id=session_id, now=components.now()
    ):
        # Another User's live Session is indistinguishable from one that never existed.
        raise ApiError(status_code=404, code="NOT_FOUND")

    response = Response(status_code=204)
    if session_id == user.session.session_id:
        clear_session_cookies(response, settings=settings)
    return response


def _set_ceremony_cookie(
    response: Response, *, settings: Settings, value: str, max_age: int
) -> None:
    """Hold one pending ceremony in a short-lived, host-locked cookie."""
    response.set_cookie(
        settings.oidc_state_cookie_name,
        value,
        max_age=max_age,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        # The provider returns through a cross-site top-level redirect, which Lax allows.
        samesite="lax",
    )


def _clear_ceremony_cookie(response: Response, *, settings: Settings) -> None:
    """Retire a ceremony cookie the moment its code has been redeemed."""
    response.delete_cookie(
        settings.oidc_state_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
