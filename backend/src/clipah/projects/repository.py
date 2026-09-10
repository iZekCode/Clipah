"""Private SQLAlchemy persistence operations for Workspace-scoped Projects."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clipah.models import IdempotencyKey, Project, ProjectStatus, SourceKind
from clipah.projects.schemas import (
    IdempotencyReplay,
    ProjectPage,
    ProjectPageBoundary,
    ProjectRestoreResult,
    ProjectSummary,
)
from clipah.retention.policy import RetentionEntityKind
from clipah.retention.use_cases import cancel_tombstone, schedule_tombstone


class ProjectRepository:
    """Keep all Project persistence, mutation, and ORM mapping private to this module."""

    def __init__(self, session: Session) -> None:
        """Bind this repository to the already authorized request transaction."""
        self._session = session

    def create(
        self,
        *,
        workspace_id: UUID,
        created_by_user_id: UUID,
        name: str,
        source_kind: str,
        now: datetime,
    ) -> ProjectSummary:
        """Persist a newly created Project and return its domain value."""
        project = Project(
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            name=name,
            status=ProjectStatus.CREATED,
            source_kind=SourceKind(source_kind),
            created_at=now,
            updated_at=now,
        )
        self._session.add(project)
        self._session.flush()
        return _summary(project)

    def active_by_id(self, *, workspace_id: UUID, project_id: UUID) -> ProjectSummary | None:
        """Return one non-archived Project domain value inside its Workspace."""
        project = self._session.execute(
            select(Project).where(
                Project.workspace_id == workspace_id,
                Project.id == project_id,
                Project.archived_at.is_(None),
            )
        ).scalar_one_or_none()
        return None if project is None else _summary(project)

    def rename(
        self, *, workspace_id: UUID, project_id: UUID, name: str, now: datetime
    ) -> ProjectSummary | None:
        """Rename one active Project and return its new domain value."""
        project = _active_project(self._session, workspace_id=workspace_id, project_id=project_id)
        if project is None:
            return None
        project.name = name
        project.updated_at = now
        self._session.flush()
        return _summary(project)

    def schedule_retention(
        self, *, workspace_id: UUID, project_id: UUID, storage_prefix: str, eligible_at: datetime
    ) -> None:
        """Record when this Project's media and rows stop being recoverable."""
        schedule_tombstone(
            self._session,
            workspace_id=workspace_id,
            entity_kind=RetentionEntityKind.PROJECT,
            entity_id=project_id,
            storage_prefix=storage_prefix,
            eligible_at=eligible_at,
        )

    def cancel_retention(self, *, workspace_id: UUID, project_id: UUID) -> None:
        """Withdraw a scheduled purge for a Project a member brought back."""
        cancel_tombstone(
            self._session,
            workspace_id=workspace_id,
            entity_kind=RetentionEntityKind.PROJECT,
            entity_id=project_id,
        )

    def archive(self, *, workspace_id: UUID, project_id: UUID, now: datetime) -> bool:
        """Soft-delete one active Project, reporting whether it was visible to this Workspace."""
        project = _active_project(self._session, workspace_id=workspace_id, project_id=project_id)
        if project is None:
            return False
        project.archived_at = now
        project.updated_at = now
        self._session.flush()
        return True

    def restore(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        now: datetime,
        recovery_window: timedelta,
    ) -> ProjectRestoreResult:
        """Restore a recent archived Project or report its recovery-window result."""
        project = self._session.execute(
            select(Project).where(
                Project.workspace_id == workspace_id,
                Project.id == project_id,
                Project.archived_at.is_not(None),
            )
        ).scalar_one_or_none()
        if project is None:
            return ProjectRestoreResult(project=None, recovery_window_elapsed=False)
        if project.archived_at is None or now - project.archived_at > recovery_window:
            return ProjectRestoreResult(project=None, recovery_window_elapsed=True)
        project.archived_at = None
        project.updated_at = now
        self._session.flush()
        return ProjectRestoreResult(project=_summary(project), recovery_window_elapsed=False)

    def page(
        self,
        *,
        workspace_id: UUID,
        limit: int,
        after: ProjectPageBoundary | None,
    ) -> ProjectPage:
        """Return one stable non-archived Project page and a domain pagination boundary."""
        conditions = [Project.workspace_id == workspace_id, Project.archived_at.is_(None)]
        if after is not None:
            conditions.append(
                or_(
                    Project.created_at > after.created_at,
                    and_(
                        Project.created_at == after.created_at,
                        Project.id > after.project_id,
                    ),
                )
            )
        projects = list(
            self._session.execute(
                select(Project)
                .where(*conditions)
                .order_by(Project.created_at, Project.id)
                .limit(limit + 1)
            ).scalars()
        )
        page_projects = projects[:limit]
        boundary = None
        if len(projects) > limit:
            last = page_projects[-1]
            boundary = ProjectPageBoundary(created_at=last.created_at, project_id=last.id)
        return ProjectPage(
            projects=tuple(_summary(project) for project in page_projects), next_boundary=boundary
        )

    def idempotency_replay(
        self, *, workspace_id: UUID, route: str, key: str
    ) -> IdempotencyReplay | None:
        """Return the durable Workspace-wide replay record for one route and key."""
        record = self._session.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.workspace_id == workspace_id,
                IdempotencyKey.route == route,
                IdempotencyKey.key == key,
            )
        ).scalar_one_or_none()
        if record is None:
            return None
        return IdempotencyReplay(
            request_hash=record.request_hash,
            project=(
                None if record.response_body is None else _summary_from_body(record.response_body)
            ),
        )

    def reserve_idempotency_key(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID | None,
        route: str,
        key: str,
        request_hash: bytes,
    ) -> bool:
        """Reserve a Workspace-wide key, reporting whether this request won the race."""
        record = IdempotencyKey(
            workspace_id=workspace_id,
            user_id=user_id,
            route=route,
            key=key,
            request_hash=request_hash,
        )
        try:
            with self._session.begin_nested():
                self._session.add(record)
                self._session.flush()
        except IntegrityError:
            return False
        return True

    def complete_idempotency_key(
        self,
        *,
        workspace_id: UUID,
        route: str,
        key: str,
        project: ProjectSummary,
    ) -> None:
        """Persist an HTTP-neutral Project replay value after its resource is durable."""
        record = self._session.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.workspace_id == workspace_id,
                IdempotencyKey.route == route,
                IdempotencyKey.key == key,
            )
        ).scalar_one()
        record.response_body = _summary_body(project)
        self._session.flush()


def _active_project(session: Session, *, workspace_id: UUID, project_id: UUID) -> Project | None:
    """Load one active ORM Project only for persistence-layer mutation."""
    return session.execute(
        select(Project).where(
            Project.workspace_id == workspace_id,
            Project.id == project_id,
            Project.archived_at.is_(None),
        )
    ).scalar_one_or_none()


def _summary(project: Project) -> ProjectSummary:
    """Map ORM state to an application-level Project value at the persistence boundary."""
    return ProjectSummary(
        project_id=project.id,
        workspace_id=project.workspace_id,
        name=project.name,
        status=project.status.value,
        source_kind=project.source_kind.value,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _summary_body(project: ProjectSummary) -> dict[str, object]:
    """Encode a Project domain value for durable replay without HTTP presentation fields."""
    return {
        "project_id": str(project.project_id),
        "workspace_id": str(project.workspace_id),
        "name": project.name,
        "status": project.status,
        "source_kind": project.source_kind,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }


def _summary_from_body(body: dict[str, object]) -> ProjectSummary:
    """Decode the durable HTTP-neutral Project replay value inside the persistence layer."""
    return ProjectSummary(
        project_id=UUID(str(body["project_id"])),
        workspace_id=UUID(str(body["workspace_id"])),
        name=str(body["name"]),
        status=str(body["status"]),
        source_kind=str(body["source_kind"]),
        created_at=datetime.fromisoformat(str(body["created_at"])),
        updated_at=datetime.fromisoformat(str(body["updated_at"])),
    )
