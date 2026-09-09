"""HTTP surface for Workspace Social Account OAuth Grant lifecycles."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from starlette.responses import RedirectResponse

from clipah.api.dependencies import (
    CurrentUserDependency,
    CurrentWorkspace,
    DatabaseSession,
    auth_components_for,
    authorize_workspace,
    require_csrf,
    require_workspace,
    settings_for,
)
from clipah.api.errors import ApiError, error_response
from clipah.api.request_id import request_id_for
from clipah.config import Settings
from clipah.publishing.use_cases import PublicationFutureWorkCoordinator
from clipah.social_accounts.models import PublishingCapabilities, SocialProvider
from clipah.social_accounts.oauth import (
    ProviderPolicy,
    SocialAuthorizationError,
    SocialProviderUnavailableError,
    SocialScopeMissingError,
    open_social_oauth_cookie,
    provider_policy,
    seal_social_oauth_cookie,
)
from clipah.social_accounts.secrets import SocialSecretStore, SocialSecretUnavailableError
from clipah.social_accounts.use_cases import (
    FuturePublicationCoordinator,
    SocialAccountNotFoundError,
    SocialAccountReconnectRequiredError,
    SocialAccountService,
    SocialAccountSummary,
    SocialOAuthInvalidError,
    SocialSecretBackendUnavailableError,
)
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["social-accounts"])
ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.WORKSPACE_READ))
]
ManagingWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.SOCIAL_CONNECTION_MANAGE))
]


class SocialConnectionStartResponse(BaseModel):
    """The provider authorization URL a browser should visit next."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    authorization_url: str = Field(alias="authorizationUrl")


class SocialAccountResponse(BaseModel):
    """One Social Account with only redacted grant status metadata."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    provider: str
    external_account_id: str = Field(alias="externalAccountId")
    display_name: str = Field(alias="displayName")
    avatar_url: str | None = Field(alias="avatarUrl")
    account_type: str | None = Field(alias="accountType")
    login_family: str = Field(alias="loginFamily")
    api_version: str = Field(alias="apiVersion")
    connection_status: str = Field(alias="connectionStatus")
    granted_scopes: list[str] = Field(alias="grantedScopes")
    capability_version: str = Field(alias="capabilityVersion")
    authorized_by_user_id: UUID = Field(alias="authorizedByUserId")
    access_token_expires_at: datetime | None = Field(alias="accessTokenExpiresAt")
    refresh_token_expires_at: datetime | None = Field(alias="refreshTokenExpiresAt")
    last_validated_at: datetime | None = Field(alias="lastValidatedAt")
    created_at: datetime = Field(alias="createdAt")
    revoked_at: datetime | None = Field(alias="revokedAt")

    @field_serializer(
        "access_token_expires_at",
        "refresh_token_expires_at",
        "last_validated_at",
        "created_at",
        "revoked_at",
    )
    def serialize_timestamp(self, value: datetime | None) -> str | None:
        """Keep every lifecycle instant in the established explicit-offset shape."""
        return None if value is None else value.isoformat()


class SocialAccountsResponse(BaseModel):
    """Every Social Account visible in one Workspace."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    social_accounts: list[SocialAccountResponse] = Field(alias="socialAccounts")


class SocialCapabilitiesResponse(BaseModel):
    """One safe versioned provider capability snapshot."""

    model_config = ConfigDict(extra="forbid")

    version: str
    capabilities: dict[str, object]


@router.get("/workspaces/{workspace_id}/social-accounts", response_model=SocialAccountsResponse)
def index(
    request: Request,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
) -> SocialAccountsResponse:
    """List redacted Social Account metadata for one live Workspace member."""
    service = _service(request, session)
    return SocialAccountsResponse(
        socialAccounts=[_account_response(row) for row in service.list(access=workspace.access)]
    )


@router.post(
    "/workspaces/{workspace_id}/social-accounts/{provider}/connect",
    response_model=SocialConnectionStartResponse,
    dependencies=[Depends(require_csrf)],
)
def start_connection(
    request: Request,
    response: Response,
    provider: SocialProvider,
    session: DatabaseSession,
    workspace: ManagingWorkspace,
) -> SocialConnectionStartResponse:
    """Start one provider-specific OAuth ceremony with durable replay protection."""
    _require_enabled_provider(request, provider)
    settings = settings_for(request)
    started = _service(request, session).start(
        access=workspace.access,
        provider=provider,
        now=auth_components_for(request).now(),
    )
    secret = _session_secret(settings)
    response.set_cookie(
        settings.social_oauth_cookie_name,
        seal_social_oauth_cookie(started.cookie, secret=secret),
        max_age=15 * 60,
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
        path="/",
    )
    session.commit()
    return SocialConnectionStartResponse(authorizationUrl=started.redirect.authorization_url)


@router.get("/social-oauth/{provider}/callback")
def callback(
    request: Request,
    provider: SocialProvider,
    code: str,
    state: str,
    session: DatabaseSession,
    user: CurrentUserDependency,
) -> Response:
    """Redeem one exact single-use callback and return to Workspace Connections."""
    _require_enabled_provider(request, provider)
    settings = settings_for(request)
    request_id = request_id_for(request)
    sealed = request.cookies.get(settings.social_oauth_cookie_name)
    if sealed is None:
        raise ApiError(status_code=400, code="SOCIAL_OAUTH_INVALID")
    try:
        cookie = open_social_oauth_cookie(sealed, secret=_session_secret(settings))
        workspace = authorize_workspace(
            request,
            session,
            user,
            cookie.workspace_id,
            WorkspaceAction.SOCIAL_CONNECTION_MANAGE,
        )
        _service(request, session).connect(
            access=workspace.access,
            cookie=cookie,
            provider=provider,
            state=state,
            code=code,
            now=auth_components_for(request).now(),
            request_id=request_id,
        )
    except SocialScopeMissingError:
        # The provider code has already been redeemed. Preserve the durable one-use
        # ceremony while rolling back no account/grant rows (none exist yet).
        session.commit()
        refusal = error_response(
            status_code=422, code="SOCIAL_SCOPE_MISSING", request_id=request_id
        )
        refusal.delete_cookie(settings.social_oauth_cookie_name, path="/")
        return refusal
    except (SocialAuthorizationError, SocialOAuthInvalidError):
        session.commit()
        refusal = error_response(
            status_code=400, code="SOCIAL_OAUTH_INVALID", request_id=request_id
        )
        refusal.delete_cookie(settings.social_oauth_cookie_name, path="/")
        return refusal
    except (SocialSecretUnavailableError, SocialSecretBackendUnavailableError):
        session.commit()
        refusal = error_response(status_code=503, code="SERVICE_UNAVAILABLE", request_id=request_id)
        refusal.delete_cookie(settings.social_oauth_cookie_name, path="/")
        return refusal
    session.commit()
    destination = f"{settings.frontend_origin or ''}/dashboard/settings/connections"
    response = RedirectResponse(destination, status_code=303)
    response.delete_cookie(settings.social_oauth_cookie_name, path="/")
    return response


@router.post(
    "/social-accounts/{social_account_id}/refresh",
    response_model=SocialAccountResponse,
    dependencies=[Depends(require_csrf)],
)
def refresh(
    request: Request,
    social_account_id: UUID,
    session: DatabaseSession,
    workspace: ManagingWorkspace,
) -> SocialAccountResponse:
    """Refresh and rotate one tenant-visible Social Account OAuth Grant."""
    try:
        summary = _service(request, session).refresh(
            access=workspace.access,
            social_account_id=social_account_id,
            now=auth_components_for(request).now(),
            request_id=request_id_for(request),
        )
    except SocialAccountNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except SocialAccountReconnectRequiredError as error:
        session.commit()
        raise ApiError(status_code=409, code="SOCIAL_ACCOUNT_RECONNECT_REQUIRED") from error
    except (SocialProviderUnavailableError, SocialSecretBackendUnavailableError) as error:
        raise ApiError(status_code=503, code="SERVICE_UNAVAILABLE") from error
    session.commit()
    return _account_response(summary)


@router.delete(
    "/social-accounts/{social_account_id}",
    status_code=204,
    dependencies=[Depends(require_csrf)],
)
def disconnect(
    request: Request,
    social_account_id: UUID,
    session: DatabaseSession,
    workspace: ManagingWorkspace,
) -> None:
    """Disconnect one Social Account and cryptographically erase its OAuth Grant."""
    try:
        _service(request, session).disconnect(
            access=workspace.access,
            social_account_id=social_account_id,
            now=auth_components_for(request).now(),
            request_id=request_id_for(request),
        )
    except SocialAccountNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    session.commit()


@router.get(
    "/social-accounts/{social_account_id}/capabilities",
    response_model=SocialCapabilitiesResponse,
)
def capabilities(
    request: Request,
    social_account_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
) -> SocialCapabilitiesResponse:
    """Read the safe capability snapshot for one Social Account."""
    try:
        snapshot = _service(request, session).get_capabilities(
            access=workspace.access, social_account_id=social_account_id
        )
    except SocialAccountNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except SocialAccountReconnectRequiredError as error:
        raise ApiError(status_code=409, code="SOCIAL_ACCOUNT_RECONNECT_REQUIRED") from error
    return _capabilities_response(snapshot)


def _service(request: Request, session: DatabaseSession) -> SocialAccountService:
    """Compose one request-scoped service from typed application dependencies."""
    store: SocialSecretStore | None = request.app.state.social_secret_store
    if store is None:
        raise ApiError(status_code=503, code="SERVICE_UNAVAILABLE")
    publications: FuturePublicationCoordinator = (
        request.app.state.future_publications or PublicationFutureWorkCoordinator(session)
    )
    return SocialAccountService(
        session,
        providers=request.app.state.social_providers,
        policies=_policies(settings_for(request)),
        store=store,
        publications=publications,
    )


def _policies(settings: Settings) -> dict[SocialProvider, ProviderPolicy]:
    """Build configured provider policies without sharing Login Identity settings."""
    values = {
        SocialProvider.YOUTUBE: (
            settings.youtube_oauth_client_id,
            settings.youtube_oauth_redirect_uri,
            settings.youtube_api_version,
        ),
        SocialProvider.INSTAGRAM: (
            settings.instagram_oauth_client_id,
            settings.instagram_oauth_redirect_uri,
            settings.instagram_api_version,
        ),
        SocialProvider.TIKTOK: (
            settings.tiktok_client_key,
            settings.tiktok_oauth_redirect_uri,
            settings.tiktok_api_version,
        ),
    }
    policies: dict[SocialProvider, ProviderPolicy] = {}
    for provider, (client_id, redirect_uri, api_version) in values.items():
        if client_id is not None and redirect_uri is not None:
            policies[provider] = provider_policy(
                provider,
                client_id=client_id,
                redirect_uri=redirect_uri,
                api_version=api_version,
            )
    return policies


def _require_enabled_provider(request: Request, provider: SocialProvider) -> None:
    """Make disabled or unbound provider routes indistinguishable from absent routes."""
    settings = settings_for(request)
    enabled = {
        SocialProvider.YOUTUBE: settings.youtube_publishing_enabled,
        SocialProvider.INSTAGRAM: settings.instagram_publishing_enabled,
        SocialProvider.TIKTOK: settings.tiktok_publishing_enabled,
    }
    if (
        not settings.social_publishing_enabled
        or not enabled[provider]
        or provider not in request.app.state.social_providers
        or provider not in _policies(settings)
    ):
        raise ApiError(status_code=404, code="NOT_FOUND")


def _session_secret(settings: Settings) -> str:
    """Return ceremony sealing material or fail closed before creating state."""
    if settings.session_secret is None:
        raise ApiError(status_code=503, code="SERVICE_UNAVAILABLE")
    return settings.session_secret.get_secret_value()


def _account_response(summary: SocialAccountSummary) -> SocialAccountResponse:
    """Render only Social Account and redacted grant metadata."""
    return SocialAccountResponse(
        id=summary.account_id,
        provider=summary.provider.value,
        externalAccountId=summary.external_account_id,
        displayName=summary.display_name,
        avatarUrl=summary.avatar_url,
        accountType=summary.account_type,
        loginFamily=summary.login_family,
        apiVersion=summary.api_version,
        connectionStatus=summary.connection_status.value,
        grantedScopes=list(summary.granted_scopes),
        capabilityVersion=summary.capabilities.version,
        authorizedByUserId=summary.authorized_by_user_id,
        accessTokenExpiresAt=summary.access_token_expires_at,
        refreshTokenExpiresAt=summary.refresh_token_expires_at,
        lastValidatedAt=summary.last_validated_at,
        createdAt=summary.created_at,
        revokedAt=summary.revoked_at,
    )


def _capabilities_response(snapshot: PublishingCapabilities) -> SocialCapabilitiesResponse:
    """Render a copy of safe capabilities so route code cannot mutate durable state."""
    return SocialCapabilitiesResponse(version=snapshot.version, capabilities=snapshot.as_dict())
