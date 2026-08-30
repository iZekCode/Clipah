"""Transactional database boundaries and tenant-context helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from uuid import UUID, uuid4

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from clipah.config import Settings
from clipah.models import (
    User,
    Workspace,
    WorkspaceKind,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)


class RuntimeRole(StrEnum):
    """Database roles allowed at application runtime."""

    API = "clipah_api"
    WORKER = "clipah_worker"


@dataclass(frozen=True, slots=True)
class PersonalWorkspaceProvisioning:
    """Rows atomically created for a new human identity."""

    user: User
    workspace: Workspace
    membership: WorkspaceMembership


@lru_cache(maxsize=8)
def _engine_for_url(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def get_engine(settings: Settings | None = None) -> Engine:
    """Return a pooled engine for an explicitly configured durable Postgres database."""
    resolved_settings = settings or Settings()
    if not resolved_settings.database_url:
        raise RuntimeError("CLIPAH_DATABASE_URL is required for database access")
    return _engine_for_url(resolved_settings.database_url)


def _set_transaction_context(
    session: Session,
    *,
    workspace_id: UUID | None,
    user_id: UUID | None,
) -> None:
    """Set both RLS variables locally; empty values deliberately fail policies closed."""
    session.execute(
        text("SELECT set_config('clipah.workspace_id', :workspace_id, true)"),
        {"workspace_id": "" if workspace_id is None else str(workspace_id)},
    )
    session.execute(
        text("SELECT set_config('clipah.user_id', :user_id, true)"),
        {"user_id": "" if user_id is None else str(user_id)},
    )


@contextmanager
def session_scope(
    *,
    settings: Settings | None = None,
    workspace_id: UUID | None = None,
    user_id: UUID | None = None,
    runtime_role: RuntimeRole | None = None,
) -> Iterator[Session]:
    """Commit one unit of work with transaction-local tenant and actor context."""
    for context_name, context_value in (("workspace_id", workspace_id), ("user_id", user_id)):
        if context_value is not None and not isinstance(context_value, UUID):
            raise ValueError(f"{context_name} must be a UUID")

    with Session(get_engine(settings)) as session, session.begin():
        if runtime_role is not None:
            # RuntimeRole is a closed enum, so the identifier cannot contain user input.
            session.execute(text(f"SET LOCAL ROLE {runtime_role.value}"))
        _set_transaction_context(session, workspace_id=workspace_id, user_id=user_id)
        yield session


def create_user_with_personal_workspace(
    session: Session,
    *,
    primary_email: str,
    display_name: str,
    workspace_name: str,
    workspace_slug: str,
) -> PersonalWorkspaceProvisioning:
    """Stage a User, personal Workspace, and owner Membership in one transaction."""
    user = User(id=uuid4(), primary_email=primary_email, display_name=display_name)
    workspace = Workspace(
        id=uuid4(),
        name=workspace_name,
        slug=workspace_slug,
        kind=WorkspaceKind.PERSONAL,
        status=WorkspaceStatus.ACTIVE,
    )
    session.add_all((user, workspace))
    session.flush()

    _set_transaction_context(session, workspace_id=workspace.id, user_id=user.id)
    membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id=user.id,
        role=WorkspaceRole.OWNER,
    )
    session.add(membership)
    session.flush()
    return PersonalWorkspaceProvisioning(user=user, workspace=workspace, membership=membership)
