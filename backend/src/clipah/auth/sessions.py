"""Opaque, rotatable Clipah Sessions with idle, absolute, and recent-auth deadlines."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from clipah.auth.models import (
    AuthenticatedSession,
    IssuedSession,
    SessionInvalidError,
    SessionPolicy,
    SessionSummary,
    UserDisabledError,
)
from clipah.models import AuthSession, User, UserStatus

# 32 bytes of entropy, rendered URL-safe; Postgres only ever sees its SHA-256 digest.
SESSION_TOKEN_BYTES = 32
MAX_USER_AGENT_SUMMARY_LENGTH = 200


def hash_session_token(token: str) -> bytes:
    """Return the only Session token representation that may reach the database."""
    return hashlib.sha256(token.encode("ascii")).digest()


def hash_client_ip(client_ip: str, *, secret: str) -> bytes:
    """Key the address digest so a database leak cannot enumerate the address space."""
    return hmac.new(secret.encode("utf-8"), client_ip.encode("utf-8"), hashlib.sha256).digest()


def issue_session(
    session: Session,
    *,
    user_id: UUID,
    secret: str,
    policy: SessionPolicy,
    now: datetime,
    client_ip: str | None = None,
    user_agent: str | None = None,
    absolute_expires_at: datetime | None = None,
    recent_auth_at: datetime | None = None,
) -> IssuedSession:
    """Create one Session and return its plaintext token exactly once."""
    token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
    absolute_deadline = absolute_expires_at or now + policy.absolute_ttl
    record = AuthSession(
        id=uuid4(),
        user_id=user_id,
        token_hash=hash_session_token(token),
        created_at=now,
        last_seen_at=now,
        idle_expires_at=min(now + policy.idle_ttl, absolute_deadline),
        absolute_expires_at=absolute_deadline,
        recent_auth_at=recent_auth_at or now,
        ip_hash=None if client_ip is None else hash_client_ip(client_ip, secret=secret),
        user_agent_summary=_summarize_user_agent(user_agent),
    )
    session.add(record)
    session.flush()
    return IssuedSession(
        session_id=record.id,
        user_id=user_id,
        token=token,
        created_at=record.created_at,
        recent_auth_at=record.recent_auth_at,
        idle_expires_at=record.idle_expires_at,
        absolute_expires_at=record.absolute_expires_at,
    )


def authenticate_session(
    session: Session, *, token: str, policy: SessionPolicy, now: datetime
) -> AuthenticatedSession:
    """Verify one presented token and slide its idle deadline forward."""
    record = session.scalars(
        select(AuthSession).where(AuthSession.token_hash == hash_session_token(token))
    ).one_or_none()
    if record is None or record.revoked_at is not None:
        raise SessionInvalidError("session token is not valid")
    if now >= record.idle_expires_at or now >= record.absolute_expires_at:
        # Close the record so an expired token can never be retried against an earlier clock.
        record.revoked_at = now
        session.flush()
        raise SessionInvalidError("session token is not valid")

    user = session.get(User, record.user_id)
    if user is None or user.status is not UserStatus.ACTIVE:
        raise UserDisabledError("this account can no longer authenticate")

    record.last_seen_at = now
    record.idle_expires_at = min(now + policy.idle_ttl, record.absolute_expires_at)
    session.flush()
    return AuthenticatedSession(
        session_id=record.id,
        user_id=record.user_id,
        recent_auth_at=record.recent_auth_at,
        idle_expires_at=record.idle_expires_at,
        absolute_expires_at=record.absolute_expires_at,
    )


def rotate_session(
    session: Session,
    *,
    current: AuthenticatedSession,
    secret: str,
    policy: SessionPolicy,
    now: datetime,
    client_ip: str | None = None,
    user_agent: str | None = None,
    reauthenticated: bool = False,
) -> IssuedSession:
    """Replace a live Session token while preserving its absolute deadline."""
    revoke_session(session, user_id=current.user_id, session_id=current.session_id, now=now)
    return issue_session(
        session,
        user_id=current.user_id,
        secret=secret,
        policy=policy,
        now=now,
        client_ip=client_ip,
        user_agent=user_agent,
        absolute_expires_at=current.absolute_expires_at,
        recent_auth_at=now if reauthenticated else current.recent_auth_at,
    )


def revoke_session(session: Session, *, user_id: UUID, session_id: UUID, now: datetime) -> bool:
    """Revoke one Session the caller owns and report whether it was live."""
    result = cast(
        "CursorResult[Any]",
        session.execute(
            update(AuthSession)
            .where(
                AuthSession.id == session_id,
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        ),
    )
    return result.rowcount == 1


def revoke_all_sessions(
    session: Session, *, user_id: UUID, now: datetime, keep_session_id: UUID | None = None
) -> int:
    """Revoke every live Session for one User, optionally sparing the current one."""
    statement = (
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    if keep_session_id is not None:
        statement = statement.where(AuthSession.id != keep_session_id)
    return int(cast("CursorResult[Any]", session.execute(statement)).rowcount)


def list_active_sessions(session: Session, *, user_id: UUID, now: datetime) -> list[SessionSummary]:
    """List the Sessions a User could still use, newest activity last."""
    records = session.scalars(
        select(AuthSession)
        .where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.idle_expires_at > now,
            AuthSession.absolute_expires_at > now,
        )
        .order_by(AuthSession.created_at, AuthSession.id)
    ).all()
    return [
        SessionSummary(
            session_id=record.id,
            created_at=record.created_at,
            last_seen_at=record.last_seen_at,
            idle_expires_at=record.idle_expires_at,
            absolute_expires_at=record.absolute_expires_at,
            user_agent_summary=record.user_agent_summary,
        )
        for record in records
    ]


def _summarize_user_agent(user_agent: str | None) -> str | None:
    """Keep a short, bounded device hint instead of the full client header."""
    if user_agent is None:
        return None
    summary = " ".join(user_agent.split())[:MAX_USER_AGENT_SUMMARY_LENGTH]
    return summary or None
