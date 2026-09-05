"""Source connections: a member's own credential, held for as short a time as possible.

A connection is two rows. One describes it — which provider, who authorized it, when they
consented, when it expires, whether it has been revoked — and is the only part a member
ever sees. The other holds the encrypted credential, in a table the API may write and
destroy but never read back; only a worker performing an import may read it, and only
through a lease.

The rules this module exists to keep:

* **Consent is recorded, not assumed.** A connection is created only when the member has
  confirmed both that they understand the risk and that the account is theirs, and the
  instant of that confirmation is stored with the connection.
* **A connection is short-lived.** It expires when its own credential does, or after the
  maximum window this system will hold one, whichever comes first.
* **Revoking destroys the material.** The secret row is deleted, so a revoked connection
  is not merely marked — there is nothing left to lease.
* **A lease is bounded twice.** It ends when the connection ends, and sooner if the lease
  window is shorter, so a Job that hangs cannot hold a credential open indefinitely.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from clipah.models import (
    SourceConnection,
    SourceConnectionKind,
    SourceConnectionProvider,
    SourceConnectionSecret,
    SourceConnectionStatus,
)
from clipah.source_connectors.cookies import (
    serialize_cookie_jar,
    validate_cookie_jar,
)
from clipah.source_connectors.secrets import (
    EncryptedSecret,
    SecretContext,
    SecretLease,
    SecretStore,
)
from clipah.workspaces.models import WorkspaceAccess

SOURCE_CONNECTION_NOT_FOUND = "SOURCE_CONNECTION_NOT_FOUND"
SOURCE_CONNECTION_EXPIRED = "SOURCE_CONNECTION_EXPIRED"
SOURCE_CONNECTION_REVOKED = "SOURCE_CONNECTION_REVOKED"
SOURCE_CONNECTION_CONSENT_REQUIRED = "SOURCE_CONNECTION_CONSENT_REQUIRED"

# The longest this system will hold somebody's cookie jar, whatever the jar itself says.
MAX_CONNECTION_TTL = timedelta(days=7)
# The longest one import may hold a credential open.
LEASE_TTL = timedelta(minutes=30)
YOUTUBE_DOMAIN_SCOPE = ".youtube.com,.google.com"


class SourceConnectionNotFoundError(Exception):
    """The connection does not exist, or the caller may not learn that it does."""


class SourceConnectionUnusableError(Exception):
    """The connection exists but may not be leased, with the reason as a stable code."""

    def __init__(self, code: str) -> None:
        """Carry the code a client is allowed to see, and nothing about the credential."""
        super().__init__(code)
        self.code = code


class SourceConnectionConsentError(Exception):
    """A connection was requested without the confirmations it may only exist with."""


@dataclass(frozen=True, slots=True)
class SourceConnectionSummary:
    """One connection as it is safe to show the member who owns it."""

    connection_id: UUID
    provider: SourceConnectionProvider
    kind: SourceConnectionKind
    status: SourceConnectionStatus
    label: str
    domain_scope: str
    authorized_by_user_id: UUID
    consented_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


class SourceConnectionService:
    """Create, list, revoke, and lease the credentials a Workspace has consented to."""

    def __init__(self, session: Session, *, store: SecretStore) -> None:
        """Bind one transaction and the store that wraps every secret written in it."""
        self._session = session
        self._store = store

    def create_youtube_cookie_connection(
        self,
        *,
        access: WorkspaceAccess,
        document: bytes,
        consented: bool,
        ownership_attested: bool,
        now: datetime,
    ) -> SourceConnectionSummary:
        """Validate an uploaded jar, encrypt what survives, and record the consent.

        The jar is reduced to the rows YouTube authentication needs before anything is
        stored, so the credential this system holds is smaller than the one the member
        exported — and the connection expires with its shortest-lived cookie.
        """
        if not (consented and ownership_attested):
            raise SourceConnectionConsentError(SOURCE_CONNECTION_CONSENT_REQUIRED)
        jar = validate_cookie_jar(document, now_epoch=int(now.timestamp()))
        expires_at = min(
            datetime.fromtimestamp(jar.expires_at_epoch, tz=now.tzinfo),
            now + MAX_CONNECTION_TTL,
        )
        connection_id = uuid4()
        secret_reference = uuid4()
        encrypted = self._store.encrypt(
            serialize_cookie_jar(jar).encode("utf-8"),
            context=SecretContext(workspace_id=access.workspace_id, connection_id=connection_id),
        )
        connection = SourceConnection(
            id=connection_id,
            workspace_id=access.workspace_id,
            provider=SourceConnectionProvider.YOUTUBE,
            kind=SourceConnectionKind.COOKIE,
            status=SourceConnectionStatus.ACTIVE,
            label=f"YouTube cookies ({len(jar.cookies)} accepted)",
            domain_scope=YOUTUBE_DOMAIN_SCOPE,
            secret_reference=secret_reference,
            authorized_by_user_id=access.user_id,
            consented_at=now,
            expires_at=expires_at,
        )
        self._session.add(connection)
        # Written as a plain insert with every value supplied: an ORM insert would ask
        # for the stored row back, and the API process is not allowed to read this one.
        self._session.execute(
            insert(SourceConnectionSecret).values(
                id=secret_reference,
                workspace_id=access.workspace_id,
                connection_id=connection_id,
                key_reference=encrypted.key_reference,
                wrapped_key=encrypted.wrapped_key,
                nonce=encrypted.nonce,
                ciphertext=encrypted.ciphertext,
                created_at=now,
            )
        )
        self._session.flush()
        return _summary(connection, now=now)

    def list_connections(
        self, *, access: WorkspaceAccess, now: datetime
    ) -> tuple[SourceConnectionSummary, ...]:
        """List this Workspace's connections, newest first, without their credentials."""
        rows = self._session.scalars(
            select(SourceConnection)
            .where(SourceConnection.workspace_id == access.workspace_id)
            .order_by(SourceConnection.created_at.desc(), SourceConnection.id.desc())
        )
        return tuple(_summary(row, now=now) for row in rows)

    def revoke(self, *, access: WorkspaceAccess, connection_id: UUID, now: datetime) -> None:
        """Mark one connection revoked and destroy the credential it was holding."""
        connection = self._session.scalar(
            select(SourceConnection)
            .where(
                SourceConnection.workspace_id == access.workspace_id,
                SourceConnection.id == connection_id,
            )
            .with_for_update()
        )
        if connection is None:
            raise SourceConnectionNotFoundError(str(connection_id))
        connection.status = SourceConnectionStatus.REVOKED
        connection.revoked_at = now
        self._session.execute(
            delete(SourceConnectionSecret).where(
                SourceConnectionSecret.workspace_id == access.workspace_id,
                SourceConnectionSecret.connection_id == connection_id,
            )
        )
        self._session.flush()

    def lease(
        self, *, workspace_id: UUID, connection_id: UUID, job_id: UUID, now: datetime
    ) -> SecretLease:
        """Borrow one credential for one Job, refusing anything that may not be used.

        A revoked or expired connection is refused by its own stable code, and a
        connection whose secret row is gone is refused as revoked rather than as missing:
        the material was destroyed on purpose.
        """
        connection = self._session.scalar(
            select(SourceConnection).where(
                SourceConnection.workspace_id == workspace_id,
                SourceConnection.id == connection_id,
            )
        )
        if connection is None:
            raise SourceConnectionNotFoundError(str(connection_id))
        if connection.status is SourceConnectionStatus.REVOKED:
            raise SourceConnectionUnusableError(SOURCE_CONNECTION_REVOKED)
        if connection.status is SourceConnectionStatus.EXPIRED or connection.expires_at <= now:
            raise SourceConnectionUnusableError(SOURCE_CONNECTION_EXPIRED)
        stored = self._session.scalar(
            select(SourceConnectionSecret).where(
                SourceConnectionSecret.workspace_id == workspace_id,
                SourceConnectionSecret.connection_id == connection_id,
            )
        )
        if stored is None:
            raise SourceConnectionUnusableError(SOURCE_CONNECTION_REVOKED)
        plaintext = self._store.decrypt(
            _encrypted(stored),
            context=SecretContext(workspace_id=workspace_id, connection_id=connection_id),
        )
        return SecretLease(
            connection_id=connection_id,
            job_id=job_id,
            secret=bytearray(plaintext),
            expires_at=min(connection.expires_at, now + LEASE_TTL),
        )


def _encrypted(stored: SourceConnectionSecret) -> EncryptedSecret:
    """Read one stored row back into the shape the secret store understands."""
    return EncryptedSecret(
        key_reference=stored.key_reference,
        wrapped_key=stored.wrapped_key,
        nonce=stored.nonce,
        ciphertext=stored.ciphertext,
    )


def _summary(connection: SourceConnection, *, now: datetime) -> SourceConnectionSummary:
    """Describe one connection, reporting an expired one as expired even before a sweep."""
    status = connection.status
    if status is SourceConnectionStatus.ACTIVE and connection.expires_at <= now:
        status = SourceConnectionStatus.EXPIRED
    return SourceConnectionSummary(
        connection_id=connection.id,
        provider=connection.provider,
        kind=connection.kind,
        status=status,
        label=connection.label,
        domain_scope=connection.domain_scope,
        authorized_by_user_id=connection.authorized_by_user_id,
        consented_at=connection.consented_at,
        expires_at=connection.expires_at,
        revoked_at=connection.revoked_at,
    )
