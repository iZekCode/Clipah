"""Session policy rules that need no database to be decisive."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from clipah.auth.models import (
    AuthenticatedSession,
    RecentAuthenticationRequiredError,
    SessionPolicy,
)

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
POLICY = SessionPolicy()


def authenticated(recent_auth_at: datetime) -> AuthenticatedSession:
    """Build one verified Session observation."""
    return AuthenticatedSession(
        session_id=uuid4(),
        user_id=uuid4(),
        recent_auth_at=recent_auth_at,
        idle_expires_at=NOW + timedelta(days=7),
        absolute_expires_at=NOW + timedelta(days=30),
    )


@pytest.mark.unit
def test_a_sensitive_operation_needs_authentication_within_the_last_ten_minutes() -> None:
    """Ownership transfer and deletion must not ride on an hours-old login."""
    session = authenticated(NOW)

    session.require_recent_authentication(policy=POLICY, now=NOW + timedelta(minutes=9))

    with pytest.raises(RecentAuthenticationRequiredError):
        session.require_recent_authentication(policy=POLICY, now=NOW + timedelta(minutes=10))
