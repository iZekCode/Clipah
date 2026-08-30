"""Shared connection, migration, and provisioning helpers for integration tests."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid4

from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from clipah.config import Environment, ProcessRole, Settings
from clipah.db import RuntimeRole, create_user_with_personal_workspace

DATABASE_URL = os.getenv(
    "CLIPAH_TEST_DATABASE_URL",
    "postgresql+psycopg://clipah_migrator:clipah_migrator_local@localhost:55433/"
    "clipah_rebuild_foundation",
)
API_RUNTIME_DATABASE_URL = os.getenv(
    "CLIPAH_TEST_API_RUNTIME_DATABASE_URL",
    "postgresql+psycopg://clipah_api_runtime:clipah_api_runtime_local@localhost:55433/"
    "clipah_rebuild_foundation",
)
WORKER_RUNTIME_DATABASE_URL = os.getenv(
    "CLIPAH_TEST_WORKER_RUNTIME_DATABASE_URL",
    "postgresql+psycopg://clipah_worker_runtime:clipah_worker_runtime_local@localhost:55433/"
    "clipah_rebuild_foundation",
)
RUNTIME_LOGINS = {
    RuntimeRole.API: ("clipah_api_runtime", API_RUNTIME_DATABASE_URL),
    RuntimeRole.WORKER: ("clipah_worker_runtime", WORKER_RUNTIME_DATABASE_URL),
}
BACKEND_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_INIT_SQL = BACKEND_ROOT.parent / "infra" / "postgres" / "init-runtime.sql"


def alembic_config() -> Config:
    """Build Alembic configuration against only this worktree's database."""
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", DATABASE_URL)
    return config


def runtime_settings(runtime_role: RuntimeRole = RuntimeRole.API, **overrides: object) -> Settings:
    """Build the settings one deployment process would hold for its own runtime login."""
    _, database_url = RUNTIME_LOGINS[runtime_role]
    if runtime_role is RuntimeRole.WORKER:
        process: dict[str, object] = {
            "process_role": ProcessRole.WORKER,
            "worker_database_url": database_url,
        }
    else:
        process = {"process_role": ProcessRole.API, "database_url": database_url}
    return Settings(environment=Environment.TEST, **{**process, **overrides})  # type: ignore[arg-type]


def provision_identity(engine: Engine, *, suffix: str) -> tuple[UUID, UUID]:
    """Create a User and personal Workspace through the production helper."""
    with Session(engine) as session, session.begin():
        provisioned = create_user_with_personal_workspace(
            session,
            primary_email=f"{suffix}@example.com",
            display_name=f"User {suffix}",
            workspace_name=f"{suffix.title()} Workspace",
            workspace_slug=f"{suffix}-{uuid4().hex[:8]}",
        )
        return provisioned.user.id, provisioned.workspace.id
