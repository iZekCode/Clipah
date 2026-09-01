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
from clipah.models import JobStatus
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
    """The only caller-selected field of a public source-import intent."""

    model_config = ConfigDict(extra="forbid")

    url: Annotated[str, Field(min_length=1, max_length=2048)]


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
            )
    return {
        "sourceImportId": str(snapshot.source_import_id),
        "jobId": str(snapshot.job_id),
        "status": snapshot.status.value,
    }
