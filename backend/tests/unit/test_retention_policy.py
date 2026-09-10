"""Retention durations, prefixes, and containment as pure policy.

Retention is the one subsystem whose mistakes are unrecoverable, so the rules it
follows are data a test can read rather than behaviour hidden in a worker loop.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from clipah.config import Settings
from clipah.retention.policy import (
    RetentionEntityKind,
    RetentionPolicy,
    contains_key,
    project_prefix,
    workspace_prefix,
)

NOW = datetime(2026, 3, 1, 12, tzinfo=UTC)


def default_policy() -> RetentionPolicy:
    """Build the policy a deployment holding only default settings would run."""
    return RetentionPolicy.from_settings(Settings())


@pytest.mark.unit
def test_default_durations_are_the_ones_section_seven_names() -> None:
    """A changed default here changes how long a member's media survives deletion."""
    policy = default_policy()

    assert policy.delay_for(RetentionEntityKind.MULTIPART_UPLOAD) == timedelta(hours=24)
    assert policy.delay_for(RetentionEntityKind.JOB_WORKSPACE) == timedelta(days=7)
    assert policy.delay_for(RetentionEntityKind.PROJECT) == timedelta(days=30)
    assert policy.delay_for(RetentionEntityKind.WORKSPACE) == timedelta(days=30)
    assert policy.delay_for(RetentionEntityKind.GENERATED_DRAFT) == timedelta(hours=24)
    assert policy.delay_for(RetentionEntityKind.STOCK_PREVIEW) == timedelta(hours=24)


@pytest.mark.unit
def test_revoked_source_connections_expire_immediately() -> None:
    """An expired cookie connection is deleted now, not on the next convenient sweep."""
    policy = default_policy()

    assert policy.delay_for(RetentionEntityKind.SOURCE_CONNECTION) == timedelta(0)
    assert policy.eligible_at(RetentionEntityKind.SOURCE_CONNECTION, now=NOW) == NOW


@pytest.mark.unit
def test_eligibility_is_the_deletion_instant_plus_its_own_duration() -> None:
    """Every tombstone carries the one deadline its own kind of data earned."""
    policy = default_policy()

    eligible = policy.eligible_at(RetentionEntityKind.PROJECT, now=NOW)

    assert eligible == NOW + timedelta(days=30)


@pytest.mark.unit
def test_durations_are_configuration_rather_than_code() -> None:
    """An operator who must shorten retention changes settings, not a worker module."""
    policy = RetentionPolicy.from_settings(
        Settings(retention_soft_deleted_project_days=7, retention_abandoned_upload_hours=1)
    )

    assert policy.delay_for(RetentionEntityKind.PROJECT) == timedelta(days=7)
    assert policy.delay_for(RetentionEntityKind.MULTIPART_UPLOAD) == timedelta(hours=1)


@pytest.mark.unit
def test_prefixes_match_the_keys_media_is_actually_written_under() -> None:
    """A prefix that does not match production keys deletes nothing or too much."""
    workspace_id = UUID("11111111-1111-4111-8111-111111111111")
    project_id = UUID("22222222-2222-4222-8222-222222222222")

    assert workspace_prefix(workspace_id) == f"workspaces/{workspace_id}/"
    assert project_prefix(workspace_id=workspace_id, project_id=project_id) == (
        f"workspaces/{workspace_id}/projects/{project_id}/"
    )


@pytest.mark.unit
def test_containment_refuses_a_key_outside_the_declared_prefix() -> None:
    """A batch may only delete what its own tombstone named, whatever a provider returns."""
    workspace_id = uuid4()
    other_workspace_id = uuid4()
    prefix = workspace_prefix(workspace_id)

    assert contains_key(prefix, f"{prefix}projects/x/source/y")
    assert not contains_key(prefix, workspace_prefix(other_workspace_id) + "source/y")
    assert not contains_key(prefix, "workspaces/")


@pytest.mark.unit
def test_containment_refuses_a_neighbouring_prefix_that_merely_starts_the_same() -> None:
    """``workspaces/abc`` must never sweep ``workspaces/abcdef``, so prefixes end in a slash."""
    prefix = "workspaces/abc/"

    assert not contains_key(prefix, "workspaces/abcdef/source/y")
    assert not contains_key(prefix, "workspaces/abc")


@pytest.mark.unit
def test_a_prefix_cannot_be_widened_by_traversal() -> None:
    """A stored prefix is evidence, not a path expression a provider may reinterpret."""
    workspace_id = uuid4()
    prefix = workspace_prefix(workspace_id)

    assert not contains_key(prefix, f"{prefix}../other/source")
    assert not contains_key(prefix, f"{prefix}nested/../../escape")


@pytest.mark.unit
def test_every_tenant_table_is_classified_for_a_project_purge() -> None:
    """A table added by a later task must be scoped deliberately, not forgotten silently."""
    from clipah.models import Base
    from clipah.retention.use_cases import (
        PROJECT_SCOPE_PREDICATES,
        PROJECT_UNSCOPED_TABLES,
    )

    tenant_tables = {
        table.name for table in Base.metadata.sorted_tables if "workspace_id" in table.columns
    }
    classified = set(PROJECT_SCOPE_PREDICATES) | set(PROJECT_UNSCOPED_TABLES)

    assert tenant_tables == classified
    assert not set(PROJECT_SCOPE_PREDICATES) & set(PROJECT_UNSCOPED_TABLES)


@pytest.mark.unit
def test_a_workspace_purge_preserves_only_the_named_compliance_records() -> None:
    """Deleting a Workspace must still leave the evidence that it was deleted."""
    from clipah.retention.use_cases import WORKSPACE_PRESERVED_TABLES

    assert (
        frozenset(
            {
                "audit_events",
                "retention_tombstones",
                "workspace_memberships",
                "workspace_membership_events",
                "workspace_quota_reservations",
            }
        )
        == WORKSPACE_PRESERVED_TABLES
    )
