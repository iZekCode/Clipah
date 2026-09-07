"""Schema contracts for append-only Workspace collaboration and Edit review evidence."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, inspect, text

COLLABORATION_TABLES = {
    "workspace_membership_events",
    "edit_review_comments",
    "edit_review_comment_resolutions",
    "edit_review_decisions",
}


@pytest.mark.integration
def test_collaboration_tables_are_present_with_tenant_identity(engine: Engine) -> None:
    """Every collaboration record must carry the Workspace that owns its authority."""
    inspector = inspect(engine)

    assert set(inspector.get_table_names()) >= COLLABORATION_TABLES
    for table_name in COLLABORATION_TABLES:
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        assert {"id", "workspace_id", "created_at"} <= columns


@pytest.mark.integration
@pytest.mark.parametrize("table_name", sorted(COLLABORATION_TABLES))
def test_collaboration_tables_force_row_level_security(engine: Engine, table_name: str) -> None:
    """A runtime role must never bypass tenant isolation for collaboration evidence."""
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT relrowsecurity, relforcerowsecurity
                FROM pg_class
                WHERE oid = CAST(:table_name AS regclass)
                """
            ),
            {"table_name": table_name},
        ).one()

    assert row == (True, True)


@pytest.mark.integration
def test_membership_and_review_history_are_append_only_for_the_api(engine: Engine) -> None:
    """An API process may append evidence but can never rewrite or erase its audit trail."""
    with engine.connect() as connection:
        privileges = {
            (row.table_name, row.privilege_type)
            for row in connection.execute(
                text(
                    """
                    SELECT table_name, privilege_type
                    FROM information_schema.role_table_grants
                    WHERE grantee = 'clipah_api'
                      AND table_name = ANY(:table_names)
                    """
                ),
                {"table_names": sorted(COLLABORATION_TABLES)},
            )
        }

    assert privileges == {
        (table_name, privilege)
        for table_name in COLLABORATION_TABLES
        for privilege in {"SELECT", "INSERT"}
    }
