"""HTTP adapter for the source credentials a Workspace has explicitly consented to.

Every route here is behind a feature flag that is off by default and stays off in
production until the security review this feature depends on is recorded. While it is
off, these paths answer exactly like paths that do not exist: a member cannot discover the
feature by probing for it.

The request that creates a connection carries the jar, two confirmations, and nothing else.
The responses carry no part of the credential — not its bytes, not its length, not a
prefix — because a connection is described by who authorized it and when it expires, and
never by what it contains.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from clipah.api.dependencies import (
    CurrentWorkspace,
    DatabaseSession,
    auth_components_for,
    require_csrf,
    require_workspace,
    settings_for,
)
from clipah.api.errors import ApiError
from clipah.config import Settings
from clipah.source_connectors.connections import (
    SourceConnectionConsentError,
    SourceConnectionNotFoundError,
    SourceConnectionService,
    SourceConnectionSummary,
)
from clipah.source_connectors.cookies import (
    COOKIE_FILE_MALFORMED,
    COOKIE_FILE_TOO_LARGE,
    MAX_COOKIE_FILE_BYTES,
    CookieValidationError,
)
from clipah.source_connectors.secrets import SecretStore, local_secret_store
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["source-connections"])
# Reading the list carries no credential, so it does not demand a fresh authentication;
# creating or revoking one does, because both change what this system holds for a member.
ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.SOURCE_CONNECTION_READ))
]
ManagingWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.SOURCE_CONNECTION_MANAGE))
]


# Base64 expands by four bytes for every three, and the encoded field is capped so an
# oversized upload is refused before any of it is decoded.
MAX_ENCODED_COOKIE_CHARS = ((MAX_COOKIE_FILE_BYTES + 2) // 3) * 4 + 4


class SourceConnectionRequest(BaseModel):
    """One cookie jar and the two confirmations it may only be stored with."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    cookies_base64: Annotated[str, Field(alias="cookiesBase64", min_length=1)]
    consent_acknowledged: Annotated[bool, Field(alias="consentAcknowledged")]
    ownership_attested: Annotated[bool, Field(alias="ownershipAttested")]


class SourceConnectionResponse(BaseModel):
    """One connection, described without describing the credential it holds."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    provider: str
    kind: str
    status: str
    label: str
    domain_scope: str = Field(alias="domainScope")
    authorized_by_user_id: UUID = Field(alias="authorizedByUserId")
    consented_at: datetime = Field(alias="consentedAt")
    expires_at: datetime = Field(alias="expiresAt")
    revoked_at: datetime | None = Field(alias="revokedAt")

    @field_serializer("consented_at", "expires_at")
    def serialize_timestamp(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return value.isoformat()

    @field_serializer("revoked_at")
    def serialize_revoked_at(self, value: datetime | None) -> str | None:
        """A connection that was never revoked has no instant to report."""
        return None if value is None else value.isoformat()


class SourceConnectionsResponse(BaseModel):
    """Every source connection one Workspace holds."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    connections: list[SourceConnectionResponse]


@router.get("/source-connections", response_model=SourceConnectionsResponse)
def index(
    request: Request,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
) -> SourceConnectionsResponse:
    """List the connections this Workspace has, or answer as though none could exist."""
    settings = _enabled(request)
    now = auth_components_for(request).now()
    service = SourceConnectionService(session, store=_store(settings))
    return SourceConnectionsResponse(
        connections=[
            _response(summary)
            for summary in service.list_connections(access=workspace.access, now=now)
        ]
    )


@router.post(
    "/source-connections",
    status_code=201,
    response_model=SourceConnectionResponse,
    dependencies=[Depends(require_csrf)],
)
def create(
    request: Request,
    session: DatabaseSession,
    workspace: ManagingWorkspace,
    payload: SourceConnectionRequest,
) -> SourceConnectionResponse:
    """Accept one cookie jar, keep only what YouTube authentication needs, and encrypt it."""
    settings = _enabled(request)
    document = _decoded(payload.cookies_base64)
    now = auth_components_for(request).now()
    service = SourceConnectionService(session, store=_store(settings))
    try:
        summary = service.create_youtube_cookie_connection(
            access=workspace.access,
            document=document,
            consented=payload.consent_acknowledged,
            ownership_attested=payload.ownership_attested,
            now=now,
        )
    except SourceConnectionConsentError as error:
        raise ApiError(status_code=422, code="SOURCE_CONNECTION_CONSENT_REQUIRED") from error
    except CookieValidationError as error:
        raise ApiError(status_code=422, code=error.code) from None
    session.commit()
    return _response(summary)


@router.delete(
    "/source-connections/{connection_id}",
    status_code=204,
    dependencies=[Depends(require_csrf)],
)
def revoke(
    request: Request,
    connection_id: UUID,
    session: DatabaseSession,
    workspace: ManagingWorkspace,
) -> None:
    """Revoke one connection and destroy the credential it was holding."""
    settings = _enabled(request)
    now = auth_components_for(request).now()
    service = SourceConnectionService(session, store=_store(settings))
    try:
        service.revoke(access=workspace.access, connection_id=connection_id, now=now)
    except SourceConnectionNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    session.commit()


def _decoded(encoded: str) -> bytes:
    """Decode the uploaded jar, refusing an oversized or unreadable body first."""
    if len(encoded) > MAX_ENCODED_COOKIE_CHARS:
        raise ApiError(status_code=422, code=COOKIE_FILE_TOO_LARGE)
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ApiError(status_code=422, code=COOKIE_FILE_MALFORMED) from error


def _enabled(request: Request) -> Settings:
    """Refuse exactly like a route that does not exist while the feature is off."""
    settings = settings_for(request)
    if not settings.authenticated_source_import_enabled:
        raise ApiError(status_code=404, code="NOT_FOUND")
    return settings


def _store(settings: Settings) -> SecretStore:
    """Build the store this deployment wraps credentials with, or refuse to hold any."""
    if settings.secret_encryption_key is None:
        raise ApiError(status_code=503, code="SERVICE_UNAVAILABLE")
    return local_secret_store(settings.secret_encryption_key.get_secret_value())


def _response(summary: SourceConnectionSummary) -> SourceConnectionResponse:
    """Describe one connection in the shape the browser reads."""
    return SourceConnectionResponse(
        id=summary.connection_id,
        provider=summary.provider.value,
        kind=summary.kind.value,
        status=summary.status.value,
        label=summary.label,
        domainScope=summary.domain_scope,
        authorizedByUserId=summary.authorized_by_user_id,
        consentedAt=summary.consented_at,
        expiresAt=summary.expires_at,
        revokedAt=summary.revoked_at,
    )
