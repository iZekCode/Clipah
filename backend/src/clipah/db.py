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


def _assert_safe_runtime_identity(session: Session, *, runtime_role: RuntimeRole) -> None:
    """Reject owners and cluster-privileged logins even after they assume a runtime role."""
    identity = session.execute(
        text(
            """
            SELECT
                session_user,
                current_user,
                session_role.rolcanlogin AS session_can_login,
                session_role.rolinherit AS session_inherits,
                session_role.rolsuper AS session_is_super,
                session_role.rolcreatedb AS session_can_create_db,
                session_role.rolcreaterole AS session_can_create_role,
                session_role.rolreplication AS session_can_replicate,
                session_role.rolbypassrls AS session_bypasses_rls,
                assumed_role.rolcanlogin AS current_can_login,
                assumed_role.rolinherit AS current_inherits,
                assumed_role.rolsuper AS current_is_super,
                assumed_role.rolcreatedb AS current_can_create_db,
                assumed_role.rolcreaterole AS current_can_create_role,
                assumed_role.rolreplication AS current_can_replicate,
                assumed_role.rolbypassrls AS current_bypasses_rls,
                EXISTS (
                    SELECT 1
                    FROM pg_class AS relation
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = 'public'
                      AND relation.relkind IN ('r', 'p')
                      AND pg_get_userbyid(relation.relowner) IN (session_user, current_user)
                ) AS owns_public_table,
                EXISTS (
                    SELECT 1
                    FROM pg_class AS relation
                    JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                    CROSS JOIN LATERAL aclexplode(
                        COALESCE(relation.relacl, acldefault('r', relation.relowner))
                    ) AS privilege
                    WHERE namespace.nspname = 'public'
                      AND relation.relkind IN ('r', 'p')
                      AND privilege.grantee = session_role.oid
                ) AS session_has_direct_table_grant
            FROM pg_roles AS session_role
            CROSS JOIN pg_roles AS assumed_role
            WHERE session_role.rolname = session_user
              AND assumed_role.rolname = current_user
            """
        )
    ).one()
    is_unsafe = (
        identity.current_user != runtime_role.value
        or not identity.session_can_login
        or identity.session_inherits
        or identity.session_is_super
        or identity.session_can_create_db
        or identity.session_can_create_role
        or identity.session_can_replicate
        or identity.session_bypasses_rls
        or identity.current_can_login
        or identity.current_inherits
        or identity.current_is_super
        or identity.current_can_create_db
        or identity.current_can_create_role
        or identity.current_can_replicate
        or identity.current_bypasses_rls
        or identity.owns_public_table
        or identity.session_has_direct_table_grant
    )
    if is_unsafe:
        raise RuntimeError("unsafe database session identity for application runtime")


@contextmanager
def session_scope(
    *,
    settings: Settings | None = None,
    workspace_id: UUID | None = None,
    user_id: UUID | None = None,
    runtime_role: RuntimeRole = RuntimeRole.API,
) -> Iterator[Session]:
    """Commit one unit of work with transaction-local tenant and actor context."""
    for context_name, context_value in (("workspace_id", workspace_id), ("user_id", user_id)):
        if context_value is not None and not isinstance(context_value, UUID):
            raise ValueError(f"{context_name} must be a UUID")

    with Session(get_engine(settings)) as session, session.begin():
        # RuntimeRole is a closed enum, so the identifier cannot contain user input.
        session.execute(text(f"SET LOCAL ROLE {runtime_role.value}"))
        _assert_safe_runtime_identity(session, runtime_role=runtime_role)
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
