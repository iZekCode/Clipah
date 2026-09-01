"""Tenant-scoped persistence for durable remote source imports."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from clipah.models import Asset, Job, Project, SourceImport

_LOCK_NAMESPACE = 0x0C11_9A10


class SourceImportNotFoundError(Exception):
    """A source import is absent or outside the caller's exact tenant binding."""


class SourceImportRepository:
    """Keep every source-import query constrained by its Workspace."""

    def __init__(self, session: Session) -> None:
        """Bind persistence to the caller's already-scoped transaction."""
        self._session = session

    def lock_idempotency(self, *, workspace_id: UUID, key: str) -> None:
        """Serialize one Workspace/route/key before replay lookup and admission."""
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:subject))"),
            {
                "namespace": _LOCK_NAMESPACE,
                "subject": f"{workspace_id}:youtube-imports:{key}",
            },
        )

    def active_project(self, *, workspace_id: UUID, project_id: UUID) -> Project | None:
        """Lock one active Project so archiving cannot race durable creation."""
        return self._session.scalar(
            select(Project)
            .where(
                Project.workspace_id == workspace_id,
                Project.id == project_id,
                Project.archived_at.is_(None),
            )
            .with_for_update()
        )

    def job_by_key(self, *, workspace_id: UUID, key: str) -> Job | None:
        """Find the durable work already bound to one Workspace idempotency key."""
        return self._session.scalar(
            select(Job).where(Job.workspace_id == workspace_id, Job.idempotency_key == key)
        )

    def by_job(self, *, workspace_id: UUID, job_id: UUID) -> SourceImport | None:
        """Find the SourceImport linked to one Job without crossing its Workspace."""
        return self._session.scalar(
            select(SourceImport).where(
                SourceImport.workspace_id == workspace_id, SourceImport.job_id == job_id
            )
        )

    def lock_for_worker(
        self, *, workspace_id: UUID, project_id: UUID, job_id: UUID
    ) -> SourceImport:
        """Lock the exact Workspace, Project, and Job binding a worker was given."""
        source = self._session.scalar(
            select(SourceImport)
            .where(
                SourceImport.workspace_id == workspace_id,
                SourceImport.project_id == project_id,
                SourceImport.job_id == job_id,
            )
            .with_for_update()
        )
        if source is None:
            raise SourceImportNotFoundError(str(job_id))
        return source

    def asset(self, *, workspace_id: UUID, source_import_id: UUID) -> Asset | None:
        """Find only the deterministic Asset owned by this SourceImport and Workspace."""
        return self._session.scalar(
            select(Asset).where(Asset.workspace_id == workspace_id, Asset.id == source_import_id)
        )

    def add(self, source_import: SourceImport) -> None:
        """Persist one source intent before any broker dispatch can occur."""
        self._session.add(source_import)
        self._session.flush()

    def add_asset(self, asset: Asset) -> None:
        """Persist one verified deterministic source Asset."""
        self._session.add(asset)
        self._session.flush()
