"""Real-Postgres contracts for Login Identities and Clipah Sessions."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session
from support import runtime_settings

from clipah.auth.identities import resolve_login_identity
from clipah.auth.models import (
    IdentityConflictError,
    OidcProfile,
    SessionInvalidError,
    SessionPolicy,
    UnverifiedEmailError,
    UserDisabledError,
)
from clipah.auth.sessions import (
    authenticate_session,
    issue_session,
    list_active_sessions,
    revoke_all_sessions,
    revoke_session,
    rotate_session,
)
from clipah.db import session_scope
from clipah.models import AuthIdentity, AuthSession, User, Workspace, WorkspaceMembership

GOOGLE_ISSUER = "https://accounts.google.com"
SESSION_SECRET = "a-test-session-secret-of-at-least-32-characters"
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
POLICY = SessionPolicy(
    idle_ttl=timedelta(days=7),
    absolute_ttl=timedelta(days=30),
    recent_auth_window=timedelta(minutes=10),
)


def google_profile(**overrides: object) -> OidcProfile:
    """Build a verified Google profile for the identity under test."""
    defaults: dict[str, object] = {
        "issuer": GOOGLE_ISSUER,
        "subject": "108422224444555566667",
        "email": "creator@example.com",
        "email_verified": True,
        "display_name": "Creator Example",
        "avatar_url": "https://lh3.googleusercontent.com/a/avatar",
    }
    return OidcProfile(**{**defaults, **overrides})  # type: ignore[arg-type]


@pytest.fixture
def api_session(engine: Engine) -> Iterator[Session]:
    """Run each contract through the same least-privilege runtime role the API uses."""
    del engine
    with session_scope(settings=runtime_settings()) as session:
        yield session


def login(session: Session, profile: OidcProfile | None = None, *, now: datetime = NOW) -> UUID:
    """Resolve one Login Identity and return the owning User."""
    resolved = resolve_login_identity(session, profile=profile or google_profile(), now=now)
    return resolved.user_id


@pytest.mark.integration
def test_first_login_creates_identity_user_and_personal_workspace(api_session: Session) -> None:
    """A new Google Login Identity must bootstrap exactly one owned personal Workspace."""
    resolved = resolve_login_identity(api_session, profile=google_profile(), now=NOW)
    api_session.flush()

    identity = api_session.get(AuthIdentity, resolved.identity_id)
    user = api_session.get(User, resolved.user_id)
    workspace = api_session.get(Workspace, resolved.workspace_id)
    membership = api_session.get(
        WorkspaceMembership, {"workspace_id": resolved.workspace_id, "user_id": resolved.user_id}
    )
    assert resolved.created is True
    assert identity is not None
    assert identity.issuer == GOOGLE_ISSUER
    assert identity.subject == "108422224444555566667"
    assert identity.provider == "google"
    assert identity.last_login_at == NOW
    assert user is not None
    assert user.primary_email == "creator@example.com"
    assert workspace is not None
    assert workspace.kind == "personal"
    assert membership is not None
    assert membership.role == "owner"


@pytest.mark.integration
def test_repeat_login_reuses_the_identity_instead_of_creating_a_second_user(
    api_session: Session,
) -> None:
    """The `(issuer, subject)` pair, not the email, is the durable authentication key."""
    first = resolve_login_identity(api_session, profile=google_profile(), now=NOW)
    later = NOW + timedelta(days=1)

    second = resolve_login_identity(
        api_session,
        profile=google_profile(email="renamed@example.com", display_name="Renamed"),
        now=later,
    )
    api_session.flush()

    identity = api_session.get(AuthIdentity, second.identity_id)
    assert second.user_id == first.user_id
    assert second.identity_id == first.identity_id
    assert second.created is False
    assert identity is not None
    assert identity.email_at_provider == "renamed@example.com"
    assert identity.last_login_at == later
    assert api_session.query(User).count() == 1


@pytest.mark.integration
def test_an_equal_email_from_another_issuer_never_auto_links_the_existing_user(
    api_session: Session,
) -> None:
    """Silent linking on a matching email address is an account-takeover path."""
    first = resolve_login_identity(api_session, profile=google_profile(), now=NOW)
    api_session.flush()

    with pytest.raises(IdentityConflictError):
        resolve_login_identity(
            api_session,
            profile=google_profile(issuer="https://login.microsoftonline.com", subject="other"),
            now=NOW,
        )

    assert api_session.query(User).count() == 1
    assert api_session.query(AuthIdentity).count() == 1
    assert api_session.query(AuthIdentity).one().user_id == first.user_id


@pytest.mark.integration
def test_an_unverified_provider_email_cannot_create_an_identity(api_session: Session) -> None:
    """An unverified address proves nothing about who controls the mailbox."""
    with pytest.raises(UnverifiedEmailError):
        resolve_login_identity(api_session, profile=google_profile(email_verified=False), now=NOW)

    assert api_session.query(User).count() == 0
    assert api_session.query(AuthIdentity).count() == 0


@pytest.mark.integration
def test_a_disabled_user_cannot_log_in_again(api_session: Session) -> None:
    """Disabling a User must close the login path, not only existing Sessions."""
    user_id = login(api_session)
    api_session.flush()
    api_session.execute(
        text("UPDATE users SET status = 'disabled', disabled_at = :now WHERE id = :user_id"),
        {"now": NOW, "user_id": user_id},
    )

    with pytest.raises(UserDisabledError):
        resolve_login_identity(api_session, profile=google_profile(), now=NOW)


@pytest.mark.integration
def test_postgres_stores_only_the_hash_of_a_256_bit_session_token(api_session: Session) -> None:
    """A readable token column would turn one database leak into full account access."""
    user_id = login(api_session)

    issued = issue_session(
        api_session,
        user_id=user_id,
        secret=SESSION_SECRET,
        policy=POLICY,
        now=NOW,
        client_ip="203.0.113.10",
        user_agent="Mozilla/5.0 (Macintosh)",
    )
    api_session.flush()

    stored = api_session.get(AuthSession, issued.session_id)
    assert stored is not None
    assert stored.token_hash == hashlib.sha256(issued.token.encode("ascii")).digest()
    assert len(stored.token_hash) == 32
    assert len(issued.token.encode("ascii")) >= 32
    row = api_session.execute(
        text("SELECT * FROM auth_sessions WHERE id = :session_id"),
        {"session_id": issued.session_id},
    ).one()
    assert issued.token not in " ".join(str(value) for value in row)


@pytest.mark.integration
def test_the_client_address_is_stored_as_a_keyed_digest(api_session: Session) -> None:
    """An unkeyed address digest is trivially reversible from a small address space."""
    user_id = login(api_session)

    issued = issue_session(
        api_session,
        user_id=user_id,
        secret=SESSION_SECRET,
        policy=POLICY,
        now=NOW,
        client_ip="203.0.113.10",
        user_agent="Mozilla/5.0 (Macintosh)",
    )
    api_session.flush()

    stored = api_session.get(AuthSession, issued.session_id)
    assert stored is not None
    assert (
        stored.ip_hash
        == hmac.new(SESSION_SECRET.encode("utf-8"), b"203.0.113.10", hashlib.sha256).digest()
    )
    assert stored.ip_hash != hashlib.sha256(b"203.0.113.10").digest()


@pytest.mark.integration
def test_authentication_slides_idle_expiry_but_never_past_the_absolute_deadline(
    api_session: Session,
) -> None:
    """Sliding past the absolute deadline would make a Session effectively immortal."""
    user_id = login(api_session)
    issued = issue_session(
        api_session, user_id=user_id, secret=SESSION_SECRET, policy=POLICY, now=NOW
    )
    api_session.flush()

    midpoint = NOW + timedelta(days=3)
    authenticated = authenticate_session(
        api_session, token=issued.token, policy=POLICY, now=midpoint
    )
    assert authenticated.user_id == user_id
    assert authenticated.idle_expires_at == midpoint + POLICY.idle_ttl

    # Keep the Session alive with real activity until one more slide would pass the deadline.
    latest = authenticated
    cursor = midpoint
    while cursor + POLICY.idle_ttl < issued.absolute_expires_at:
        cursor += timedelta(days=6)
        latest = authenticate_session(api_session, token=issued.token, policy=POLICY, now=cursor)

    assert latest.idle_expires_at == issued.absolute_expires_at
    assert latest.idle_expires_at < cursor + POLICY.idle_ttl


@pytest.mark.integration
@pytest.mark.parametrize(
    ("offset", "reason"),
    (
        pytest.param(timedelta(days=8), "idle", id="idle_expiry"),
        pytest.param(timedelta(days=31), "absolute", id="absolute_expiry"),
    ),
)
def test_an_expired_session_is_rejected_and_permanently_closed(
    api_session: Session, offset: timedelta, reason: str
) -> None:
    """An expired Session must not become usable again by simply retrying earlier."""
    del reason
    user_id = login(api_session)
    issued = issue_session(
        api_session, user_id=user_id, secret=SESSION_SECRET, policy=POLICY, now=NOW
    )
    api_session.flush()

    with pytest.raises(SessionInvalidError):
        authenticate_session(
            api_session,
            token=issued.token,
            policy=POLICY,
            now=NOW + offset,
        )

    stored = api_session.get(AuthSession, issued.session_id)
    assert stored is not None
    assert stored.revoked_at is not None
    with pytest.raises(SessionInvalidError):
        authenticate_session(
            api_session,
            token=issued.token,
            policy=POLICY,
            now=NOW + timedelta(minutes=1),
        )


@pytest.mark.integration
def test_an_unknown_token_is_rejected_without_revealing_whether_a_session_exists(
    api_session: Session,
) -> None:
    """Guessed tokens and revoked tokens must fail through the same closed path."""
    user_id = login(api_session)
    issued = issue_session(
        api_session, user_id=user_id, secret=SESSION_SECRET, policy=POLICY, now=NOW
    )
    api_session.flush()
    revoke_session(api_session, user_id=user_id, session_id=issued.session_id, now=NOW)

    with pytest.raises(SessionInvalidError):
        authenticate_session(api_session, token=issued.token, policy=POLICY, now=NOW)
    with pytest.raises(SessionInvalidError):
        authenticate_session(api_session, token="never-issued", policy=POLICY, now=NOW)


@pytest.mark.integration
def test_rotation_invalidates_the_previous_token_and_keeps_the_absolute_deadline(
    api_session: Session,
) -> None:
    """Rotation must not silently extend how long one login can live."""
    user_id = login(api_session)
    issued = issue_session(
        api_session, user_id=user_id, secret=SESSION_SECRET, policy=POLICY, now=NOW
    )
    api_session.flush()
    later = NOW + timedelta(days=2)
    current = authenticate_session(api_session, token=issued.token, policy=POLICY, now=later)

    rotated = rotate_session(
        api_session, current=current, secret=SESSION_SECRET, policy=POLICY, now=later
    )
    api_session.flush()

    assert rotated.token != issued.token
    assert rotated.session_id != issued.session_id
    assert rotated.absolute_expires_at == issued.absolute_expires_at
    previous = api_session.get(AuthSession, issued.session_id)
    assert previous is not None
    assert previous.revoked_at == later
    with pytest.raises(SessionInvalidError):
        authenticate_session(api_session, token=issued.token, policy=POLICY, now=later)
    assert (
        authenticate_session(api_session, token=rotated.token, policy=POLICY, now=later).session_id
        == rotated.session_id
    )


@pytest.mark.integration
def test_reauthentication_during_rotation_refreshes_the_recent_auth_window(
    api_session: Session,
) -> None:
    """Only a real re-authentication may reopen the ten-minute sensitive-action window."""
    user_id = login(api_session)
    issued = issue_session(
        api_session, user_id=user_id, secret=SESSION_SECRET, policy=POLICY, now=NOW
    )
    api_session.flush()
    later = NOW + timedelta(days=2)
    current = authenticate_session(api_session, token=issued.token, policy=POLICY, now=later)

    carried = rotate_session(
        api_session, current=current, secret=SESSION_SECRET, policy=POLICY, now=later
    )
    reauthenticated = rotate_session(
        api_session,
        current=authenticate_session(api_session, token=carried.token, policy=POLICY, now=later),
        secret=SESSION_SECRET,
        policy=POLICY,
        now=later,
        reauthenticated=True,
    )
    api_session.flush()

    assert carried.recent_auth_at == NOW
    assert reauthenticated.recent_auth_at == later


@pytest.mark.integration
def test_recent_authentication_expires_ten_minutes_after_the_last_proof(
    api_session: Session,
) -> None:
    """Sensitive operations must require a fresh proof, not an old login."""
    user_id = login(api_session)
    issued = issue_session(
        api_session, user_id=user_id, secret=SESSION_SECRET, policy=POLICY, now=NOW
    )
    api_session.flush()

    inside = authenticate_session(
        api_session,
        token=issued.token,
        policy=POLICY,
        now=NOW + timedelta(minutes=9),
    )
    outside = authenticate_session(
        api_session,
        token=issued.token,
        policy=POLICY,
        now=NOW + timedelta(minutes=11),
    )

    assert inside.has_recent_authentication(policy=POLICY, now=NOW + timedelta(minutes=9)) is True
    assert (
        outside.has_recent_authentication(policy=POLICY, now=NOW + timedelta(minutes=11)) is False
    )


@pytest.mark.integration
def test_logout_closes_one_session_while_revoke_all_closes_the_rest(
    api_session: Session,
) -> None:
    """Revoking every Session is the account-recovery lever; it must miss nothing."""
    user_id = login(api_session)
    other_user_id = login(
        api_session, google_profile(subject="other-subject", email="other@example.com")
    )
    tokens = [
        issue_session(api_session, user_id=user_id, secret=SESSION_SECRET, policy=POLICY, now=NOW)
        for _ in range(3)
    ]
    foreign = issue_session(
        api_session, user_id=other_user_id, secret=SESSION_SECRET, policy=POLICY, now=NOW
    )
    api_session.flush()

    revoke_session(api_session, user_id=user_id, session_id=tokens[0].session_id, now=NOW)
    assert len(list_active_sessions(api_session, user_id=user_id, now=NOW)) == 2

    revoked = revoke_all_sessions(
        api_session, user_id=user_id, now=NOW, keep_session_id=tokens[1].session_id
    )

    assert revoked == 1
    remaining = list_active_sessions(api_session, user_id=user_id, now=NOW)
    assert [summary.session_id for summary in remaining] == [tokens[1].session_id]
    assert len(list_active_sessions(api_session, user_id=other_user_id, now=NOW)) == 1
    assert (
        authenticate_session(api_session, token=foreign.token, policy=POLICY, now=NOW).user_id
        == other_user_id
    )


@pytest.mark.integration
def test_one_user_cannot_revoke_another_users_session(api_session: Session) -> None:
    """Session identifiers are guessable enough that ownership must be re-checked."""
    user_id = login(api_session)
    other_user_id = login(
        api_session, google_profile(subject="other-subject", email="other@example.com")
    )
    victim = issue_session(
        api_session, user_id=other_user_id, secret=SESSION_SECRET, policy=POLICY, now=NOW
    )
    api_session.flush()

    assert revoke_session(api_session, user_id=user_id, session_id=victim.session_id, now=NOW) is (
        False
    )
    assert revoke_session(api_session, user_id=user_id, session_id=uuid4(), now=NOW) is False
    assert (
        authenticate_session(api_session, token=victim.token, policy=POLICY, now=NOW).session_id
        == victim.session_id
    )


@pytest.mark.integration
def test_disabling_a_user_closes_every_live_session_at_the_next_request(
    api_session: Session,
) -> None:
    """A disabled User must lose access without waiting for Session expiry."""
    user_id = login(api_session)
    issued = issue_session(
        api_session, user_id=user_id, secret=SESSION_SECRET, policy=POLICY, now=NOW
    )
    api_session.flush()
    api_session.execute(
        text("UPDATE users SET status = 'disabled', disabled_at = :now WHERE id = :user_id"),
        {"now": NOW, "user_id": user_id},
    )

    with pytest.raises(UserDisabledError):
        authenticate_session(api_session, token=issued.token, policy=POLICY, now=NOW)
