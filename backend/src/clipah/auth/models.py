"""Provider-neutral authentication values shared by identities, sessions, and routes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID


class AuthError(Exception):
    """Base class for every authentication failure the API maps to a stable code."""


class AuthorizationError(AuthError):
    """The OpenID Connect ceremony could not be trusted end to end."""


class IdentityConflictError(AuthError):
    """A different Login Identity already claims this email address."""


class UnverifiedEmailError(AuthError):
    """The provider did not verify the email address it returned."""


class UserDisabledError(AuthError):
    """The User exists but is no longer allowed to authenticate."""


class SessionInvalidError(AuthError):
    """The presented Session token is unknown, revoked, or expired."""


class RecentAuthenticationRequiredError(AuthError):
    """The operation is sensitive and the Session has no fresh authentication proof."""


@dataclass(frozen=True, slots=True)
class OidcProfile:
    """The provider-neutral subset of an ID token Clipah is willing to persist."""

    issuer: str
    subject: str
    email: str
    email_verified: bool
    display_name: str
    avatar_url: str | None = None


@dataclass(frozen=True, slots=True)
class PendingAuthorization:
    """The single-use bindings that tie one callback to one authorization request."""

    state: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuthorizationRedirect:
    """The provider redirect plus the bindings the caller must store server-side."""

    authorization_url: str
    pending: PendingAuthorization


@dataclass(frozen=True, slots=True)
class ResolvedIdentity:
    """The User a completed login ceremony resolved to."""

    user_id: UUID
    identity_id: UUID
    workspace_id: UUID | None
    created: bool


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    """Session lifetime rules kept in configuration rather than in route code."""

    idle_ttl: timedelta = timedelta(days=7)
    absolute_ttl: timedelta = timedelta(days=30)
    recent_auth_window: timedelta = timedelta(minutes=10)


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """A newly created Session; the only place the plaintext token ever exists."""

    session_id: UUID
    user_id: UUID
    token: str
    created_at: datetime
    recent_auth_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    """A verified Session as observed during one request."""

    session_id: UUID
    user_id: UUID
    recent_auth_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime

    def has_recent_authentication(self, *, policy: SessionPolicy, now: datetime) -> bool:
        """Report whether a sensitive operation may proceed without re-authentication."""
        return now - self.recent_auth_at < policy.recent_auth_window

    def require_recent_authentication(self, *, policy: SessionPolicy, now: datetime) -> None:
        """Fail closed when a sensitive operation lacks a fresh authentication proof."""
        if not self.has_recent_authentication(policy=policy, now=now):
            raise RecentAuthenticationRequiredError("recent authentication required")


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """A Session as it is safe to display to its owner."""

    session_id: UUID
    created_at: datetime
    last_seen_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    user_agent_summary: str | None
