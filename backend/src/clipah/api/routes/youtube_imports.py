"""HTTP adapter for durable public YouTube source imports."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from clipah.api.dependencies import (
    CurrentWorkspace,
    DatabaseSession,
    auth_components_for,
    rate_limiter_for,
    require_csrf,
    require_workspace,
    settings_for,
)
from clipah.api.errors import ApiError
from clipah.assets.youtube import NormalizedYouTubeUrl, SourceImportError
from clipah.jobs.admission import ConcurrencyLimitError, admission_policy
from clipah.models import JobKind, JobStatus, SourceConnectionStatus
from clipah.source_connectors.connections import (
    SOURCE_CONNECTION_EXPIRED,
    SOURCE_CONNECTION_REVOKED,
    SourceConnectionService,
)
from clipah.source_connectors.secrets import local_secret_store
from clipah.source_imports.dispatch import JobDispatcher
from clipah.source_imports.use_cases import (
    SourceImportConflictError,
    SourceImportProjectNotFoundError,
    create_source_import,
)
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["source-imports"])
WritableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_WRITE))
]
IdempotencyHeader = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]
SourceUrlValidator = Callable[[str], NormalizedYouTubeUrl]


class YouTubeImportRequest(BaseModel):
    """What a caller chooses about one source import, and nothing else."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    url: Annotated[str, Field(min_length=1, max_length=2048)]
    # Naming a connection makes this an authenticated import. It is only ever accepted
    # while the feature is enabled, and only for a connection this Workspace owns.
    source_connection_id: UUID | None = Field(default=None, alias="sourceConnectionId")


@router.post(
    "/projects/{project_id}/youtube-imports",
    status_code=202,
    dependencies=[Depends(require_csrf)],
)
def create(
    request: Request,
    project_id: UUID,
    session: DatabaseSession,
    workspace: WritableWorkspace,
    payload: YouTubeImportRequest,
    idempotency_key: IdempotencyHeader,
) -> dict[str, str]:
    """Commit durable intent before making a best-effort broker dispatch."""
    validator: SourceUrlValidator = request.app.state.source_url_validator
    dispatcher: JobDispatcher = request.app.state.job_dispatcher
    connection_id = _usable_connection(
        request, session, workspace=workspace, connection_id=payload.source_connection_id
    )
    try:
        source = validator(payload.url)
        snapshot = create_source_import(
            session,
            policy=admission_policy(settings_for(request), rate_limiter_for(request)),
            access=workspace.access,
            project_id=project_id,
            source=source,
            idempotency_key=idempotency_key,
            source_import_id=uuid4(),
            now=auth_components_for(request).now(),
            source_connection_id=connection_id,
        )
    except SourceImportError as error:
        raise ApiError(status_code=422, code=error.code) from error
    except SourceImportProjectNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except SourceImportConflictError as error:
        raise ApiError(status_code=409, code="CONFLICT") from error
    except ConcurrencyLimitError as error:
        raise ApiError(status_code=429, code="CONCURRENCY_LIMIT") from error

    session.commit()
    if snapshot.job_status is JobStatus.QUEUED:
        with suppress(Exception):
            dispatcher.dispatch(
                job_id=snapshot.job_id,
                workspace_id=workspace.access.workspace_id,
                user_id=workspace.access.user_id,
                kind=JobKind.SOURCE_IMPORT,
            )
    return {
        "sourceImportId": str(snapshot.source_import_id),
        "jobId": str(snapshot.job_id),
        "status": snapshot.status.value,
    }


def _usable_connection(
    request: Request,
    session: DatabaseSession,
    *,
    workspace: CurrentWorkspace,
    connection_id: UUID | None,
) -> UUID | None:
    """Prove one named connection may still be used, before any Job is admitted.

    A member who names a connection while the feature is off is answered as though the
    field did not exist, and a connection that has been revoked or has expired is refused
    now rather than at the moment the worker would have failed on it.
    """
    if connection_id is None:
        return None
    settings = settings_for(request)
    if not settings.authenticated_source_import_enabled:
        raise ApiError(status_code=404, code="NOT_FOUND")
    if settings.secret_encryption_key is None:
        raise ApiError(status_code=503, code="SERVICE_UNAVAILABLE")
    service = SourceConnectionService(
        session, store=local_secret_store(settings.secret_encryption_key.get_secret_value())
    )
    now = auth_components_for(request).now()
    summaries = {
        summary.connection_id: summary
        for summary in service.list_connections(access=workspace.access, now=now)
    }
    summary = summaries.get(connection_id)
    if summary is None:
        raise ApiError(status_code=404, code="NOT_FOUND")
    if summary.status is SourceConnectionStatus.REVOKED:
        raise ApiError(status_code=422, code=SOURCE_CONNECTION_REVOKED)
    if summary.status is SourceConnectionStatus.EXPIRED:
        raise ApiError(status_code=422, code=SOURCE_CONNECTION_EXPIRED)
    return connection_id
