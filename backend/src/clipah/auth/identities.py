"""Resolve verified provider profiles into durable Clipah Login Identities."""

from __future__ import annotations

import re
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.auth.models import (
    IdentityConflictError,
    OidcProfile,
    ResolvedIdentity,
    UnverifiedEmailError,
    UserDisabledError,
)
from clipah.db import create_user_with_personal_workspace
from clipah.models import AuthIdentity, User, UserStatus

PROVIDER_BY_ISSUER = {
    "https://accounts.google.com": "google",
    "accounts.google.com": "google",
}
UNSAFE_SLUG_CHARACTERS = re.compile(r"[^a-z0-9]+")
MAX_SLUG_STEM_LENGTH = 32


def resolve_login_identity(
    session: Session, *, profile: OidcProfile, now: datetime
) -> ResolvedIdentity:
    """Return the User behind one verified provider profile, creating it on first login.

    The unique authentication key is ``(issuer, subject)``. A matching email address is
    never enough to attach a new provider identity to an existing User, because provider
    email addresses can be reassigned and are not proof of account ownership.
    """
    if not profile.email_verified:
        raise UnverifiedEmailError("the provider did not verify this email address")

    identity = session.scalars(
        select(AuthIdentity)
        .where(AuthIdentity.issuer == profile.issuer, AuthIdentity.subject == profile.subject)
        .with_for_update()
    ).one_or_none()
    if identity is not None:
        return _resume_existing_identity(session, identity=identity, profile=profile, now=now)

    conflicting_user = session.scalars(
        select(User).where(User.primary_email == profile.email)
    ).one_or_none()
    if conflicting_user is not None:
        raise IdentityConflictError("this email address already belongs to another Login Identity")

    return _create_identity_with_personal_workspace(session, profile=profile, now=now)


def _resume_existing_identity(
    session: Session, *, identity: AuthIdentity, profile: OidcProfile, now: datetime
) -> ResolvedIdentity:
    """Refresh provider-owned attributes without touching the User's own profile."""
    user = session.get(User, identity.user_id)
    if user is None or user.status is not UserStatus.ACTIVE:
        raise UserDisabledError("this account can no longer authenticate")

    identity.email_at_provider = profile.email
    identity.email_verified = profile.email_verified
    identity.last_login_at = now
    session.flush()
    return ResolvedIdentity(
        user_id=identity.user_id, identity_id=identity.id, workspace_id=None, created=False
    )


def _create_identity_with_personal_workspace(
    session: Session, *, profile: OidcProfile, now: datetime
) -> ResolvedIdentity:
    """Create the User, its personal Workspace, and the Login Identity in one transaction."""
    provisioned = create_user_with_personal_workspace(
        session,
        primary_email=profile.email,
        display_name=profile.display_name,
        workspace_name=f"{profile.display_name}'s Workspace",
        workspace_slug=personal_workspace_slug(profile.display_name),
    )
    provisioned.user.avatar_url = profile.avatar_url
    identity = AuthIdentity(
        id=uuid4(),
        user_id=provisioned.user.id,
        provider=PROVIDER_BY_ISSUER.get(profile.issuer, "oidc"),
        issuer=profile.issuer,
        subject=profile.subject,
        email_at_provider=profile.email,
        email_verified=profile.email_verified,
        created_at=now,
        last_login_at=now,
    )
    session.add(identity)
    session.flush()
    return ResolvedIdentity(
        user_id=provisioned.user.id,
        identity_id=identity.id,
        workspace_id=provisioned.workspace.id,
        created=True,
    )


def personal_workspace_slug(display_name: str) -> str:
    """Derive a collision-resistant slug that never exposes provider identifiers."""
    stem = UNSAFE_SLUG_CHARACTERS.sub("-", display_name.lower()).strip("-")
    stem = stem[:MAX_SLUG_STEM_LENGTH].strip("-") or "workspace"
    return f"{stem}-{uuid4().hex[:8]}"
