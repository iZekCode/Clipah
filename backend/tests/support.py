"""Shared connection, migration, and provisioning helpers for integration tests."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from alembic.config import Config
from pydantic import SecretStr
from redis import Redis
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from clipah.broll.generation import (
    FakeGenerativeMediaProvider,
    GenerationEstimate,
    GenerationLatencyClass,
    GenerationMediaKind,
    GenerationModerationResult,
    GenerationOutput,
    GenerationResult,
    GenerationStatus,
    GenerationUsage,
    GenerativeMediaProvider,
)
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
REDIS_URL = os.getenv("CLIPAH_TEST_REDIS_URL", "redis://localhost:56380/0")
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


@contextmanager
def redis_client() -> Iterator[Redis]:
    """Open one real Redis connection against an emptied local test keyspace."""
    client: Redis = Redis.from_url(REDIS_URL)
    try:
        client.flushdb()
        yield client
    finally:
        client.close()


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


def fake_generation_providers(
    clock: Callable[[], datetime],
    *,
    image_cost_usd: Decimal = Decimal("0.08"),
    video_cost_usd: Decimal = Decimal("0.25"),
    duration_ms: int = 5_000,
) -> Callable[[GenerationMediaKind], GenerativeMediaProvider | None]:
    """Offer deterministic image and video providers that reach no network."""
    image = FakeGenerativeMediaProvider(
        estimate=GenerationEstimate.image(
            output_count=1,
            width=1080,
            height=1920,
            latency_class=GenerationLatencyClass.STANDARD,
            provider_credits=Decimal(1),
            cost_usd=image_cost_usd,
        ),
        result=_generated_result(
            media_kind=GenerationMediaKind.IMAGE,
            cost_usd=image_cost_usd,
            duration_ms=None,
        ),
        utc_clock=clock,
    )
    video = FakeGenerativeMediaProvider(
        estimate=GenerationEstimate.video(
            output_count=1,
            duration_ms=duration_ms,
            width=720,
            height=1280,
            latency_class=GenerationLatencyClass.SLOW,
            provider_credits=Decimal(25),
            cost_usd=video_cost_usd,
        ),
        result=_generated_result(
            media_kind=GenerationMediaKind.VIDEO,
            cost_usd=video_cost_usd,
            duration_ms=duration_ms,
        ),
        utc_clock=clock,
    )

    def select(media_kind: GenerationMediaKind) -> GenerativeMediaProvider | None:
        return image if media_kind is GenerationMediaKind.IMAGE else video

    return select


def _generated_result(
    *, media_kind: GenerationMediaKind, cost_usd: Decimal, duration_ms: int | None
) -> GenerationResult:
    """Build one successful result with the moderation evidence a success requires."""
    is_image = media_kind is GenerationMediaKind.IMAGE
    return GenerationResult(
        status=GenerationStatus.SUCCEEDED,
        output=GenerationOutput(
            url=SecretStr("https://provider.invalid/ephemeral-output"),
            content_type="image/png" if is_image else "video/mp4",
            width=1080 if is_image else 720,
            height=1920 if is_image else 1280,
            duration_ms=duration_ms,
        ),
        usage=GenerationUsage(
            generated_images=1 if is_image else 0,
            generated_videos=0 if is_image else 1,
            generated_seconds=Decimal(0) if duration_ms is None else Decimal(duration_ms) / 1_000,
            provider_credits=Decimal(1) if is_image else Decimal(25),
            cost_usd=cost_usd,
        ),
        seed=None,
        moderation=GenerationModerationResult.APPROVED,
    )
