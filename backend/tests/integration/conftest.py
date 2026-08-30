"""Real-Postgres fixtures shared by every integration module."""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def integration_database(clean_database: None) -> Iterator[None]:
    """Give every integration test the shared truncate-first database without asking."""
    yield
