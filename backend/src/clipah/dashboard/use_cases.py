"""The single Workspace overview read the dashboard's first screen is built from."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from clipah.dashboard.repository import DashboardRepository
from clipah.dashboard.schemas import DashboardSummary, UsageSummary
from clipah.jobs.admission import AdmissionPolicy, QuotaLedger
from clipah.models import QuotaResource
from clipah.workspaces.models import WorkspaceAccess
from clipah.workspaces.use_cases import describe_workspace

RECENT_PROJECT_LIMIT = 5
ACTIVE_JOB_LIMIT = 20
TOP_CANDIDATE_LIMIT = 5


def summarize_dashboard(
    session: Session,
    *,
    access: WorkspaceAccess,
    policy: AdmissionPolicy,
    now: datetime,
) -> DashboardSummary:
    """Read one Workspace's current position without duplicating authoritative state."""
    repository = DashboardRepository(session)
    ledger = QuotaLedger(session, limits=policy.quota_limits)
    return DashboardSummary(
        workspace=describe_workspace(session, access=access),
        active_project_count=repository.active_project_count(workspace_id=access.workspace_id),
        recent_projects=repository.recent_projects(
            workspace_id=access.workspace_id, limit=RECENT_PROJECT_LIMIT
        ),
        active_jobs=repository.active_jobs(
            workspace_id=access.workspace_id, limit=ACTIVE_JOB_LIMIT
        ),
        usage=tuple(
            UsageSummary(
                resource=resource,
                consumed=ledger.consumed(
                    workspace_id=access.workspace_id, resource=resource, now=now
                ),
                limit=policy.quota_limits[resource],
            )
            for resource in QuotaResource
        ),
        top_candidates=repository.top_candidates(
            workspace_id=access.workspace_id, limit=TOP_CANDIDATE_LIMIT
        ),
    )
