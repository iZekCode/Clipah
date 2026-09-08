"""Social Account connection, capability, refresh, lease, and disconnect use cases."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from clipah.models import OAuthGrant, SocialAccount
from clipah.social_accounts.models import (
    OAuthGrantMaterial,
    PublishingCapabilities,
    SocialConnectionStatus,
    SocialProvider,
)
from clipah.social_accounts.oauth import (
    AUTHORIZATION_LIFETIME,
    ProviderPolicy,
    SocialAuthorizationRedirect,
    SocialOAuthCookie,
    SocialOAuthProvider,
    SocialProviderGrantRejectedError,
    SocialScopeMissingError,
    require_minimum_scopes,
    start_authorization,
    validate_authorization,
)
from clipah.social_accounts.repository import SocialAccountRepository
from clipah.social_accounts.secrets import (
    EncryptedOAuthGrant,
    OAuthGrantLease,
    OAuthSecretContext,
    SocialSecretDecryptionError,
    SocialSecretStore,
    SocialSecretUnavailableError,
)
from clipah.workspaces.models import WorkspaceAccess

LEASE_TTL = timedelta(minutes=5)


class SocialAccountNotFoundError(Exception):
    """The Social Account is missing or outside the caller's Workspace."""


class SocialOAuthInvalidError(Exception):
    """The durable ceremony is missing, expired, mismatched, or already consumed."""


class SocialAccountReconnectRequiredError(Exception):
    """The account has no usable OAuth Grant and needs explicit reconnection."""


class SocialSecretBackendUnavailableError(Exception):
    """The OAuth Grant store cannot safely encrypt or decrypt right now."""


class FuturePublicationCoordinator(Protocol):
    """Pause or cancel unpublished work before one destination is disconnected."""

    def cancel_or_pause_unpublished(
        self, *, workspace_id: UUID, social_account_id: UUID, now: datetime
    ) -> None:
        """Make future external side effects for this account ineligible."""


class EmptyFuturePublicationCoordinator:
    """Task 36 implementation for a schema that has no Publications yet."""

    def cancel_or_pause_unpublished(
        self, *, workspace_id: UUID, social_account_id: UUID, now: datetime
    ) -> None:
        """Accept the future contract while no Publication rows can exist."""
        del workspace_id, social_account_id, now


@dataclass(frozen=True, slots=True)
class StartedSocialConnection:
    """Provider redirect and authenticated cookie payload for one new ceremony."""

    redirect: SocialAuthorizationRedirect
    cookie: SocialOAuthCookie


@dataclass(frozen=True, slots=True)
class SocialAccountSummary:
    """Social Account and grant metadata safe for a Workspace member to inspect."""

    account_id: UUID
    provider: SocialProvider
    external_account_id: str
    display_name: str
    avatar_url: str | None
    account_type: str | None
    login_family: str
    api_version: str
    connection_status: SocialConnectionStatus
    granted_scopes: tuple[str, ...]
    capabilities: PublishingCapabilities
    authorized_by_user_id: UUID
    access_token_expires_at: datetime | None
    refresh_token_expires_at: datetime | None
    last_validated_at: datetime | None
    created_at: datetime
    revoked_at: datetime | None


class SocialAccountService:
    """Coordinate provider OAuth with tenant persistence and bounded secret access."""

    def __init__(
        self,
        session: Session,
        *,
        providers: Mapping[SocialProvider, SocialOAuthProvider],
        policies: Mapping[SocialProvider, ProviderPolicy],
        store: SocialSecretStore,
        publications: FuturePublicationCoordinator | None = None,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        """Bind one request transaction and its injected external boundaries."""
        self._repository = SocialAccountRepository(session)
        self._providers = providers
        self._policies = policies
        self._store = store
        self._publications = publications or EmptyFuturePublicationCoordinator()
        self._id_factory = id_factory

    def start(
        self,
        *,
        access: WorkspaceAccess,
        provider: SocialProvider,
        now: datetime,
    ) -> StartedSocialConnection:
        """Create one durable replay record and its browser-held secret bindings."""
        policy = self._policies[provider]
        redirect = start_authorization(policy=policy, now=now)
        ceremony_id = self._id_factory()
        self._repository.create_ceremony(
            ceremony_id=ceremony_id,
            workspace_id=access.workspace_id,
            actor_user_id=access.user_id,
            provider=provider,
            state=redirect.pending.state,
            redirect_uri=redirect.pending.redirect_uri,
            requested_scopes=redirect.pending.requested_scopes,
            created_at=now,
            expires_at=now + AUTHORIZATION_LIFETIME,
        )
        return StartedSocialConnection(
            redirect=redirect,
            cookie=SocialOAuthCookie(
                ceremony_id=ceremony_id,
                workspace_id=access.workspace_id,
                actor_user_id=access.user_id,
                pending=redirect.pending,
            ),
        )

    def connect(
        self,
        *,
        access: WorkspaceAccess,
        cookie: SocialOAuthCookie,
        provider: SocialProvider,
        state: str,
        code: str,
        now: datetime,
        request_id: str,
    ) -> SocialAccountSummary:
        """Redeem one ceremony and atomically persist safe identity plus encrypted grant."""
        policy = self._policies[provider]
        validate_authorization(
            pending=cookie.pending,
            provider=provider,
            state=state,
            redirect_uri=policy.redirect_uri,
            now=now,
        )
        if (
            cookie.workspace_id != access.workspace_id
            or cookie.actor_user_id != access.user_id
            or self._repository.consume_ceremony(
                workspace_id=access.workspace_id,
                ceremony_id=cookie.ceremony_id,
                actor_user_id=access.user_id,
                provider=provider,
                state=state,
                now=now,
            )
            is None
        ):
            raise SocialOAuthInvalidError("social authorization cannot be redeemed")

        adapter = self._providers[provider]
        token = adapter.exchange_code(
            code=code,
            code_verifier=cookie.pending.code_verifier,
            redirect_uri=policy.redirect_uri,
        )
        require_minimum_scopes(granted_scopes=token.granted_scopes, policy=policy)
        identity = adapter.account_identity(grant=token.material)
        capabilities = adapter.capabilities(grant=token.material)
        account = self._repository.account_by_external_for_update(
            workspace_id=access.workspace_id,
            provider=provider,
            external_account_id=identity.external_account_id,
        )
        existing_grant = None
        is_new_account = account is None
        if account is None:
            account = SocialAccount(
                id=self._id_factory(),
                workspace_id=access.workspace_id,
                provider=provider,
                external_account_id=identity.external_account_id,
                display_name=identity.display_name,
                avatar_url=identity.avatar_url,
                account_type=identity.account_type,
                login_family=identity.login_family,
                api_version=policy.api_version,
                connection_status=SocialConnectionStatus.ACTIVE,
                capability_snapshot=_capability_document(capabilities),
                authorized_by_user_id=access.user_id,
                last_validated_at=now,
                created_at=now,
            )
            grant_id = self._id_factory()
            token_version = 1
        else:
            existing_grant = self._repository.grant(
                workspace_id=access.workspace_id,
                social_account_id=account.id,
                for_update=True,
            )
            grant_id = existing_grant.id if existing_grant is not None else self._id_factory()
            token_version = existing_grant.token_version + 1 if existing_grant is not None else 1

        encrypted = self._store.encrypt(
            _encode_material(token.material),
            context=_secret_context(
                account=account, grant_id=grant_id, token_version=token_version
            ),
        )
        account.display_name = identity.display_name
        account.avatar_url = identity.avatar_url
        account.account_type = identity.account_type
        account.login_family = identity.login_family
        account.api_version = policy.api_version
        account.connection_status = SocialConnectionStatus.ACTIVE
        account.capability_snapshot = _capability_document(capabilities)
        account.authorized_by_user_id = access.user_id
        account.last_validated_at = now
        account.revoked_at = None
        if is_new_account:
            self._repository.session.add(account)
            self._repository.session.flush()
        grant = existing_grant or OAuthGrant(
            id=grant_id,
            workspace_id=access.workspace_id,
            social_account_id=account.id,
            created_at=now,
        )
        _replace_grant(
            grant,
            encrypted=encrypted,
            access_token_expires_at=token.material.access_token_expires_at,
            refresh_token_expires_at=token.material.refresh_token_expires_at,
            granted_scopes=token.granted_scopes,
            token_version=token_version,
            refreshed_at=None,
        )
        if existing_grant is None:
            self._repository.session.add(grant)
        self._repository.audit(
            workspace_id=access.workspace_id,
            actor_user_id=access.user_id,
            action="social_account.connected",
            target_id=account.id,
            before=None,
            after={"provider": provider.value, "status": "active"},
            request_id=request_id,
            now=now,
        )
        self._repository.session.flush()
        return _summary(account, grant)

    def list(self, *, access: WorkspaceAccess) -> tuple[SocialAccountSummary, ...]:
        """List safe connection metadata without reading grant plaintext."""
        summaries: list[SocialAccountSummary] = []
        for account in self._repository.accounts(workspace_id=access.workspace_id):
            grant = self._repository.grant(
                workspace_id=access.workspace_id, social_account_id=account.id
            )
            if grant is not None:
                summaries.append(_summary(account, grant))
        return tuple(summaries)

    def get_capabilities(
        self, *, access: WorkspaceAccess, social_account_id: UUID
    ) -> PublishingCapabilities:
        """Return the safe capability snapshot for one tenant-visible account."""
        account = self._repository.account(
            workspace_id=access.workspace_id, social_account_id=social_account_id
        )
        if account is None:
            raise SocialAccountNotFoundError(str(social_account_id))
        if account.connection_status is not SocialConnectionStatus.ACTIVE:
            raise SocialAccountReconnectRequiredError("Social Account must reconnect")
        return _capabilities(account)

    def refresh(
        self,
        *,
        access: WorkspaceAccess,
        social_account_id: UUID,
        now: datetime,
        request_id: str,
    ) -> SocialAccountSummary:
        """Serialize one token refresh and atomically rotate encrypted material."""
        account, grant = self._active_account_and_grant(
            access=access, social_account_id=social_account_id
        )
        lease = self._grant_lease(account=account, grant=grant, now=now)
        next_version = grant.token_version + 1
        try:
            with lease.open(now=now) as document:
                old_material = _decode_material(document)
                refreshed = self._providers[account.provider].refresh(grant=old_material)
                policy = self._policies[account.provider]
                require_minimum_scopes(granted_scopes=refreshed.granted_scopes, policy=policy)
                material = refreshed.material
                if material.refresh_token is None:
                    material = OAuthGrantMaterial(
                        access_token=material.access_token,
                        refresh_token=old_material.refresh_token,
                        token_type=material.token_type,
                        access_token_expires_at=material.access_token_expires_at,
                        refresh_token_expires_at=material.refresh_token_expires_at,
                    )
                encrypted = self._store.encrypt(
                    _encode_material(material),
                    context=_secret_context(
                        account=account, grant_id=grant.id, token_version=next_version
                    ),
                )
                granted_scopes = refreshed.granted_scopes
                refreshed_capabilities = refreshed.capabilities
                access_token_expires_at = material.access_token_expires_at
                refresh_token_expires_at = material.refresh_token_expires_at
                del old_material, material, refreshed
        except (SocialProviderGrantRejectedError, SocialScopeMissingError):
            self._mark_reconnect_required(
                access=access,
                account=account,
                grant=grant,
                reason="provider_grant_rejected",
                now=now,
                request_id=request_id,
            )
            raise SocialAccountReconnectRequiredError("Social Account must reconnect") from None
        _replace_grant(
            grant,
            encrypted=encrypted,
            access_token_expires_at=access_token_expires_at,
            refresh_token_expires_at=refresh_token_expires_at,
            granted_scopes=granted_scopes,
            token_version=next_version,
            refreshed_at=now,
        )
        if refreshed_capabilities is not None:
            account.capability_snapshot = _capability_document(refreshed_capabilities)
            account.last_validated_at = now
        self._repository.audit(
            workspace_id=access.workspace_id,
            actor_user_id=access.user_id,
            action="social_account.refreshed",
            target_id=account.id,
            before={"status": account.connection_status.value},
            after={"status": account.connection_status.value},
            request_id=request_id,
            now=now,
        )
        return _summary(account, grant)

    def disconnect(
        self,
        *,
        access: WorkspaceAccess,
        social_account_id: UUID,
        now: datetime,
        request_id: str,
    ) -> None:
        """Immediately revoke a destination, then erase its reusable local grant."""
        account = self._repository.account(
            workspace_id=access.workspace_id,
            social_account_id=social_account_id,
            for_update=True,
        )
        if account is None:
            raise SocialAccountNotFoundError(str(social_account_id))
        if account.connection_status is SocialConnectionStatus.REVOKED:
            return
        grant = self._repository.grant(
            workspace_id=access.workspace_id,
            social_account_id=social_account_id,
            for_update=True,
        )
        account.connection_status = SocialConnectionStatus.REVOKED
        account.revoked_at = now
        self._repository.session.flush()
        self._publications.cancel_or_pause_unpublished(
            workspace_id=access.workspace_id, social_account_id=social_account_id, now=now
        )
        if grant is not None and grant.revoked_at is None:
            try:
                lease = self._grant_lease(account=account, grant=grant, now=now)
                with lease.open(now=now) as document:
                    material = _decode_material(document)
                    self._providers[account.provider].revoke(grant=material)
                    del material
            except Exception:
                # Local cryptographic erasure remains mandatory when provider revocation fails.
                pass
            grant.wrapped_key = b""
            grant.nonce = b""
            grant.ciphertext = b""
            grant.revoked_at = now
        self._repository.audit(
            workspace_id=access.workspace_id,
            actor_user_id=access.user_id,
            action="social_account.disconnected",
            target_id=account.id,
            before={"status": "active"},
            after={"status": "revoked"},
            request_id=request_id,
            now=now,
        )

    def lease(
        self,
        *,
        workspace_id: UUID,
        social_account_id: UUID,
        operation_id: UUID,
        now: datetime,
    ) -> OAuthGrantLease:
        """Borrow one active grant for a bounded selected-adapter operation."""
        account = self._repository.account(
            workspace_id=workspace_id, social_account_id=social_account_id
        )
        grant = self._repository.grant(
            workspace_id=workspace_id, social_account_id=social_account_id
        )
        if (
            account is None
            or grant is None
            or account.connection_status is not SocialConnectionStatus.ACTIVE
            or grant.revoked_at is not None
            or (grant.access_token_expires_at is not None and grant.access_token_expires_at <= now)
        ):
            raise SocialAccountReconnectRequiredError("Social Account must reconnect")
        return self._grant_lease(account=account, grant=grant, now=now, operation_id=operation_id)

    def _grant_lease(
        self,
        *,
        account: SocialAccount,
        grant: OAuthGrant,
        now: datetime,
        operation_id: UUID | None = None,
    ) -> OAuthGrantLease:
        """Decrypt one grant directly into a bounded mutable loan."""
        try:
            secret = self._store.decrypt(
                _encrypted(grant), context=_secret_context(account=account, grant=grant)
            )
        except SocialSecretUnavailableError:
            raise SocialSecretBackendUnavailableError(
                "Social Account secret backend is unavailable"
            ) from None
        except SocialSecretDecryptionError:
            raise SocialAccountReconnectRequiredError("Social Account must reconnect") from None
        return OAuthGrantLease(
            social_account_id=account.id,
            operation_id=operation_id or self._id_factory(),
            expires_at=now + LEASE_TTL,
            secret=secret,
        )

    def _mark_reconnect_required(
        self,
        *,
        access: WorkspaceAccess,
        account: SocialAccount,
        grant: OAuthGrant,
        reason: str,
        now: datetime,
        request_id: str,
    ) -> None:
        """Erase a rejected grant and retain only a fixed reconnect tombstone."""
        account.connection_status = SocialConnectionStatus.RECONNECT_REQUIRED
        account.revoked_at = None
        grant.wrapped_key = b""
        grant.nonce = b""
        grant.ciphertext = b""
        grant.reconnect_reason = reason
        grant.revoked_at = now
        self._repository.audit(
            workspace_id=access.workspace_id,
            actor_user_id=access.user_id,
            action="social_account.reconnect_required",
            target_id=account.id,
            before={"status": "active"},
            after={"status": "reconnect_required", "reason": reason},
            request_id=request_id,
            now=now,
        )

    def _active_account_and_grant(
        self, *, access: WorkspaceAccess, social_account_id: UUID
    ) -> tuple[SocialAccount, OAuthGrant]:
        """Lock one usable account/grant pair or return a non-enumerating refusal."""
        account = self._repository.account(
            workspace_id=access.workspace_id,
            social_account_id=social_account_id,
            for_update=True,
        )
        if account is None:
            raise SocialAccountNotFoundError(str(social_account_id))
        grant = self._repository.grant(
            workspace_id=access.workspace_id,
            social_account_id=social_account_id,
            for_update=True,
        )
        if (
            grant is None
            or grant.revoked_at is not None
            or account.connection_status is not SocialConnectionStatus.ACTIVE
        ):
            raise SocialAccountReconnectRequiredError("Social Account must reconnect")
        return account, grant


def _secret_context(
    *,
    account: SocialAccount,
    grant: OAuthGrant | None = None,
    grant_id: UUID | None = None,
    token_version: int | None = None,
) -> OAuthSecretContext:
    """Build authenticated encryption context from durable account/grant identity."""
    resolved_grant_id = grant.id if grant is not None else grant_id
    resolved_version = grant.token_version if grant is not None else token_version
    if resolved_grant_id is None or resolved_version is None:
        raise ValueError("grant identity and version are required")
    return OAuthSecretContext(
        workspace_id=account.workspace_id,
        social_account_id=account.id,
        grant_id=resolved_grant_id,
        provider=account.provider,
        token_version=resolved_version,
    )


def _encode_material(material: OAuthGrantMaterial) -> bytes:
    """Serialize only the fixed grant fields that encryption protects."""
    return json.dumps(
        {
            "access_token": material.access_token,
            "refresh_token": material.refresh_token,
            "token_type": material.token_type,
            "access_token_expires_at": _timestamp(material.access_token_expires_at),
            "refresh_token_expires_at": _timestamp(material.refresh_token_expires_at),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _decode_material(document: bytes | bytearray) -> OAuthGrantMaterial:
    """Validate one decrypted canonical document into provider-neutral material."""
    payload: dict[str, Any] = json.loads(document)
    access_token = payload["access_token"]
    if not isinstance(access_token, str) or not access_token:
        raise ValueError("grant access token is invalid")
    refresh_token = payload.get("refresh_token")
    if refresh_token is not None and not isinstance(refresh_token, str):
        raise ValueError("grant refresh token is invalid")
    return OAuthGrantMaterial(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type=str(payload.get("token_type", "Bearer")),
        access_token_expires_at=_parse_timestamp(payload.get("access_token_expires_at")),
        refresh_token_expires_at=_parse_timestamp(payload.get("refresh_token_expires_at")),
    )


def _replace_grant(
    grant: OAuthGrant,
    *,
    encrypted: EncryptedOAuthGrant,
    access_token_expires_at: datetime | None,
    refresh_token_expires_at: datetime | None,
    granted_scopes: frozenset[str],
    token_version: int,
    refreshed_at: datetime | None,
) -> None:
    """Replace one encrypted version and its non-secret expiry/scope metadata."""
    grant.key_reference = encrypted.key_reference
    grant.key_version = encrypted.key_version
    grant.wrapped_key = encrypted.wrapped_key
    grant.nonce = encrypted.nonce
    grant.ciphertext = encrypted.ciphertext
    grant.granted_scopes = sorted(granted_scopes)
    grant.access_token_expires_at = access_token_expires_at
    grant.refresh_token_expires_at = refresh_token_expires_at
    grant.token_version = token_version
    grant.last_refreshed_at = refreshed_at
    grant.reconnect_reason = None
    grant.revoked_at = None


def _encrypted(grant: OAuthGrant) -> EncryptedOAuthGrant:
    """Restore the envelope shape from its dedicated database columns."""
    return EncryptedOAuthGrant(
        key_reference=grant.key_reference,
        key_version=grant.key_version,
        wrapped_key=grant.wrapped_key,
        nonce=grant.nonce,
        ciphertext=grant.ciphertext,
    )


def _capability_document(capabilities: PublishingCapabilities) -> dict[str, Any]:
    """Persist a version beside the safe provider-specific capability values."""
    return {"version": capabilities.version, "capabilities": capabilities.as_dict()}


def _capabilities(account: SocialAccount) -> PublishingCapabilities:
    """Read one validated durable capability snapshot."""
    document = account.capability_snapshot
    return PublishingCapabilities(
        version=str(document["version"]), values=dict(document["capabilities"])
    )


def _summary(account: SocialAccount, grant: OAuthGrant) -> SocialAccountSummary:
    """Join safe account and grant metadata without decrypting its credential."""
    return SocialAccountSummary(
        account_id=account.id,
        provider=account.provider,
        external_account_id=account.external_account_id,
        display_name=account.display_name,
        avatar_url=account.avatar_url,
        account_type=account.account_type,
        login_family=account.login_family,
        api_version=account.api_version,
        connection_status=account.connection_status,
        granted_scopes=tuple(sorted(grant.granted_scopes)),
        capabilities=_capabilities(account),
        authorized_by_user_id=account.authorized_by_user_id,
        access_token_expires_at=grant.access_token_expires_at,
        refresh_token_expires_at=grant.refresh_token_expires_at,
        last_validated_at=account.last_validated_at,
        created_at=account.created_at,
        revoked_at=account.revoked_at,
    )


def _timestamp(value: datetime | None) -> str | None:
    """Serialize one aware expiry without adding a provider-specific format."""
    return None if value is None else value.isoformat()


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse one stored expiry or preserve absence."""
    return None if value is None else datetime.fromisoformat(str(value))
