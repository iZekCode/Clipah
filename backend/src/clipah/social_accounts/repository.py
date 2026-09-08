"""Tenant-scoped persistence for Social Accounts, OAuth Grants, and ceremonies."""

from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.models import AuditEvent, OAuthGrant, SocialAccount, SocialOAuthCeremony
from clipah.social_accounts.models import SocialProvider


class SocialAccountRepository:
    """Keep every Social Account query explicit about its Workspace boundary."""

    def __init__(self, session: Session) -> None:
        """Bind one transaction without owning its commit boundary."""
        self.session = session

    def create_ceremony(
        self,
        *,
        ceremony_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        provider: SocialProvider,
        state: str,
        redirect_uri: str,
        requested_scopes: frozenset[str],
        created_at: datetime,
        expires_at: datetime,
    ) -> SocialOAuthCeremony:
        """Persist only a hash of callback state for one short-lived ceremony."""
        ceremony = SocialOAuthCeremony(
            id=ceremony_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            provider=provider,
            state_hash=state_digest(state),
            redirect_uri=redirect_uri,
            requested_scopes=sorted(requested_scopes),
            created_at=created_at,
            expires_at=expires_at,
        )
        self.session.add(ceremony)
        self.session.flush()
        return ceremony

    def consume_ceremony(
        self,
        *,
        workspace_id: UUID,
        ceremony_id: UUID,
        actor_user_id: UUID,
        provider: SocialProvider,
        state: str,
        now: datetime,
    ) -> SocialOAuthCeremony | None:
        """Atomically consume one exact live callback ceremony."""
        ceremony = self.session.scalar(
            select(SocialOAuthCeremony)
            .where(
                SocialOAuthCeremony.workspace_id == workspace_id,
                SocialOAuthCeremony.id == ceremony_id,
                SocialOAuthCeremony.actor_user_id == actor_user_id,
                SocialOAuthCeremony.provider == provider,
                SocialOAuthCeremony.state_hash == state_digest(state),
                SocialOAuthCeremony.expires_at >= now,
                SocialOAuthCeremony.consumed_at.is_(None),
            )
            .with_for_update()
        )
        if ceremony is not None:
            ceremony.consumed_at = now
            self.session.flush()
        return ceremony

    def account_by_external_for_update(
        self, *, workspace_id: UUID, provider: SocialProvider, external_account_id: str
    ) -> SocialAccount | None:
        """Lock an existing provider destination so duplicate callbacks converge."""
        return self.session.scalar(
            select(SocialAccount)
            .where(
                SocialAccount.workspace_id == workspace_id,
                SocialAccount.provider == provider,
                SocialAccount.external_account_id == external_account_id,
            )
            .with_for_update()
        )

    def account(
        self, *, workspace_id: UUID, social_account_id: UUID, for_update: bool = False
    ) -> SocialAccount | None:
        """Find one account only inside its Workspace, optionally locking it."""
        statement = select(SocialAccount).where(
            SocialAccount.workspace_id == workspace_id,
            SocialAccount.id == social_account_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def accounts(self, *, workspace_id: UUID) -> tuple[SocialAccount, ...]:
        """List account tombstones and active destinations in stable creation order."""
        return tuple(
            self.session.scalars(
                select(SocialAccount)
                .where(SocialAccount.workspace_id == workspace_id)
                .order_by(SocialAccount.created_at.desc(), SocialAccount.id.desc())
            )
        )

    def grant(
        self, *, workspace_id: UUID, social_account_id: UUID, for_update: bool = False
    ) -> OAuthGrant | None:
        """Find the separately stored grant for one tenant-correct account."""
        statement = select(OAuthGrant).where(
            OAuthGrant.workspace_id == workspace_id,
            OAuthGrant.social_account_id == social_account_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def audit(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        action: str,
        target_id: UUID,
        before: dict[str, object] | None,
        after: dict[str, object] | None,
        request_id: str,
        now: datetime,
    ) -> None:
        """Append safe Social Account lifecycle evidence to the common audit ledger."""
        self.session.add(
            AuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                action=action,
                target_kind="social_account",
                target_id=target_id,
                before_metadata=before,
                after_metadata=after,
                request_id=request_id,
                created_at=now,
            )
        )
        self.session.flush()


def state_digest(state: str) -> bytes:
    """Hash OAuth state before it reaches durable storage."""
    return hashlib.sha256(state.encode("ascii")).digest()
