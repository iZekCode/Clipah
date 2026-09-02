"""HTTP adapter for durable highlight-analysis admission."""

from __future__ import annotations

from contextlib import suppress
from math import ceil
from typing import Annotated
from uuid import UUID

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
from clipah.auth.limits import RateLimitExceededError
from clipah.highlights.use_cases import (
    AnalysisConflictError,
    AnalysisProjectNotFoundError,
    start_analysis,
)
from clipah.jobs.admission import (
    ConcurrencyLimitError,
    QuotaExceededError,
    admission_policy,
)
from clipah.models import JobKind, JobStatus
from clipah.source_imports.dispatch import JobDispatcher
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["analysis"])
WritableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_WRITE))
]
IdempotencyHeader = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]


class AnalysisJobResponse(BaseModel):
    """The durable Job identity returned after analysis admission."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    job_id: UUID = Field(alias="jobId")
    status: JobStatus


@router.post(
    "/projects/{project_id}/analysis",
    status_code=202,
    response_model=AnalysisJobResponse,
    dependencies=[Depends(require_csrf)],
)
def create(
    request: Request,
    project_id: UUID,
    session: DatabaseSession,
    workspace: WritableWorkspace,
    idempotency_key: IdempotencyHeader,
) -> AnalysisJobResponse:
    """Commit paid analysis intent before a best-effort UUID-only dispatch."""
    dispatcher: JobDispatcher = request.app.state.job_dispatcher
    try:
        snapshot = start_analysis(
            session,
            policy=admission_policy(settings_for(request), rate_limiter_for(request)),
            access=workspace.access,
            project_id=project_id,
            idempotency_key=idempotency_key,
            now=auth_components_for(request).now(),
        )
    except AnalysisProjectNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except AnalysisConflictError as error:
        raise ApiError(status_code=409, code="CONFLICT") from error
    except RateLimitExceededError as error:
        raise ApiError(
            status_code=429,
            code="RATE_LIMITED",
            retry_after_seconds=max(ceil(error.retry_after.total_seconds()), 1),
        ) from error
    except QuotaExceededError as error:
        raise ApiError(
            status_code=429,
            code="QUOTA_EXCEEDED",
            retry_after_seconds=max(ceil(error.retry_after.total_seconds()), 1),
        ) from error
    except ConcurrencyLimitError as error:
        raise ApiError(status_code=429, code="CONCURRENCY_LIMIT") from error

    session.commit()
    if snapshot.status is JobStatus.QUEUED:
        with suppress(Exception):
            dispatcher.dispatch(
                job_id=snapshot.job_id,
                workspace_id=workspace.access.workspace_id,
                user_id=workspace.access.user_id,
                kind=JobKind.ANALYZE,
            )
    return AnalysisJobResponse(jobId=snapshot.job_id, status=snapshot.status)
