"""Real-Postgres fixtures shared by every integration module."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, inspect, text
from support import DATABASE_URL, alembic_config


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Upgrade the dedicated test database and expose a real SQLAlchemy engine."""
    command.upgrade(alembic_config(), "head")
    database_engine = create_engine(DATABASE_URL)
    yield database_engine
    database_engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(engine: Engine) -> Iterator[None]:
    """Keep tests isolated without invoking protected row-delete triggers."""
    command.upgrade(alembic_config(), "head")
    table_names = sorted(inspect(engine).get_table_names())
    application_tables = [name for name in table_names if name != "alembic_version"]
    if application_tables:
        quoted = ", ".join(f'"{name}"' for name in application_tables)
        with engine.begin() as connection:
            connection.execute(text(f"TRUNCATE TABLE {quoted} CASCADE"))
    yield
