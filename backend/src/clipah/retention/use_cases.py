"""Scheduling, claiming, and discharging one tombstone at a time.

A tombstone is the only thing that authorizes a deletion: it names one entity, one
Workspace, and one storage prefix, and nothing a sweep does may reach beyond what it
records. Failures are written back onto the same tombstone so an outage costs another
attempt rather than a wider one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from clipah.assets.storage import ObjectStore
from clipah.models import Base, RetentionTombstone
from clipah.retention.policy import RetentionEntityKind, contains_key

# Tables a Workspace purge keeps. Deleting a Workspace must still leave the evidence
# that it was deleted, who could act in it, and what it was billed for.
WORKSPACE_PRESERVED_TABLES = frozenset(
    {
        "audit_events",
        "retention_tombstones",
        "workspace_memberships",
        "workspace_membership_events",
        "workspace_quota_reservations",
    }
)
# Tenant tables that belong to a Workspace rather than to any one Project, so a Project
# purge never touches them. Listing them is deliberate: a table absent from both this set
# and the predicates below fails the classification test rather than surviving unnoticed.
PROJECT_UNSCOPED_TABLES = frozenset(
    {
        "audit_events",
        "brand_kits",
        "brand_kit_versions",
        "brand_templates",
        "brand_template_versions",
        "idempotency_keys",
        "oauth_grants",
        "projects",
        "retention_tombstones",
        "social_accounts",
        "social_oauth_ceremonies",
        "source_connections",
        "source_connection_secrets",
        "workspace_invites",
        "workspace_memberships",
        "workspace_membership_events",
        "workspace_quota_reservations",
    }
)
# How each Project-scoped table names the Project it belongs to. Tables that carry no
# ``project_id`` reach it through the parent they do carry, and because deletion runs
# children first, the parent each subquery reads is still present when it runs.
_EDITS_OF_PROJECT = (
    "SELECT id FROM clip_edits WHERE workspace_id = :workspace_id AND candidate_id IN "
    "(SELECT id FROM clip_candidates "
    "WHERE workspace_id = :workspace_id AND project_id = :project_id)"
)
_REVISIONS_OF_PROJECT = (
    f"SELECT id FROM clip_edit_revisions "
    f"WHERE workspace_id = :workspace_id AND clip_edit_id IN ({_EDITS_OF_PROJECT})"
)
_ARTIFACTS_OF_PROJECT = (
    f"SELECT id FROM render_artifacts "
    f"WHERE workspace_id = :workspace_id AND clip_edit_revision_id IN ({_REVISIONS_OF_PROJECT})"
)
_PUBLICATIONS_OF_PROJECT = (
    f"SELECT id FROM publications "
    f"WHERE workspace_id = :workspace_id AND render_artifact_id IN ({_ARTIFACTS_OF_PROJECT})"
)
PROJECT_SCOPE_PREDICATES: Mapping[str, str] = MappingProxyType(
    {
        "assets": "project_id = :project_id",
        "asset_provenance": (
            "asset_id IN (SELECT id FROM assets "
            "WHERE workspace_id = :workspace_id AND project_id = :project_id)"
        ),
        "broll_plan_requests": (
            "candidate_id IN (SELECT id FROM clip_candidates "
            "WHERE workspace_id = :workspace_id AND project_id = :project_id)"
        ),
        "broll_suggestions": "project_id = :project_id",
        "campaign_outputs": "project_id = :project_id",
        "claim_evidence": "project_id = :project_id",
        "clip_candidates": "project_id = :project_id",
        "clip_edits": f"id IN ({_EDITS_OF_PROJECT})",
        "clip_edit_revisions": f"id IN ({_REVISIONS_OF_PROJECT})",
        "clip_variants": "project_id = :project_id",
        "edit_review_comments": f"clip_edit_id IN ({_EDITS_OF_PROJECT})",
        "edit_review_comment_resolutions": (
            f"comment_id IN (SELECT id FROM edit_review_comments "
            f"WHERE workspace_id = :workspace_id AND clip_edit_id IN ({_EDITS_OF_PROJECT}))"
        ),
        "edit_review_decisions": f"clip_edit_id IN ({_EDITS_OF_PROJECT})",
        "job_events": (
            "job_id IN (SELECT id FROM jobs "
            "WHERE workspace_id = :workspace_id AND project_id = :project_id)"
        ),
        "jobs": "project_id = :project_id",
        "multipart_uploads": "project_id = :project_id",
        "provider_events": f"publication_id IN ({_PUBLICATIONS_OF_PROJECT})",
        "provider_usage": (
            "job_id IN (SELECT id FROM jobs "
            "WHERE workspace_id = :workspace_id AND project_id = :project_id)"
        ),
        "publication_attempts": f"publication_id IN ({_PUBLICATIONS_OF_PROJECT})",
        "publication_batches": f"render_artifact_id IN ({_ARTIFACTS_OF_PROJECT})",
        "publication_outbox": f"publication_id IN ({_PUBLICATIONS_OF_PROJECT})",
        "publications": f"render_artifact_id IN ({_ARTIFACTS_OF_PROJECT})",
        "render_artifacts": f"id IN ({_ARTIFACTS_OF_PROJECT})",
        "render_requests": f"clip_edit_revision_id IN ({_REVISIONS_OF_PROJECT})",
        "search_documents": "project_id = :project_id",
        "social_renditions": f"render_artifact_id IN ({_ARTIFACTS_OF_PROJECT})",
        "source_imports": "project_id = :project_id",
        "transcripts": "project_id = :project_id",
    }
)


@dataclass(frozen=True, slots=True)
class DueTombstone:
    """One claimed tombstone, locked for the transaction that read it."""

    tombstone_id: UUID
    workspace_id: UUID
    entity_kind: RetentionEntityKind
    entity_id: UUID
    storage_prefix: str | None
    failure_count: int
    deleted_at: datetime | None


@dataclass(frozen=True, slots=True)
class PurgeOutcome:
    """What one bounded pass over a storage prefix accomplished."""

    deleted_keys: int
    finished: bool


def schedule_tombstone(
    session: Session,
    *,
    workspace_id: UUID,
    entity_kind: RetentionEntityKind,
    entity_id: UUID,
    storage_prefix: str | None,
    eligible_at: datetime,
) -> None:
    """Record that one entity's data expires, exactly once.

    A member who deletes the same Project twice, or a retried request that arrives
    again, must leave one tombstone rather than two purges of the same prefix.
    """
    session.execute(
        insert(RetentionTombstone)
        .values(
            workspace_id=workspace_id,
            entity_kind=entity_kind.value,
            entity_id=entity_id,
            storage_prefix=storage_prefix,
            eligible_at=eligible_at,
        )
        .on_conflict_do_nothing(constraint="uq_retention_tombstones_workspace_entity")
    )


def cancel_tombstone(
    session: Session, *, workspace_id: UUID, entity_kind: RetentionEntityKind, entity_id: UUID
) -> None:
    """Withdraw a scheduled purge because the entity it named was recovered.

    Only a tombstone that has not yet been discharged can be withdrawn: once data is
    gone, its tombstone is the record that it went, and nothing removes that.
    """
    session.execute(
        delete(RetentionTombstone).where(
            RetentionTombstone.workspace_id == workspace_id,
            RetentionTombstone.entity_kind == entity_kind.value,
            RetentionTombstone.entity_id == entity_id,
            RetentionTombstone.deleted_at.is_(None),
        )
    )


def claim_due_tombstones(
    session: Session, *, now: datetime, limit: int, max_failures: int | None = None
) -> tuple[DueTombstone, ...]:
    """Lock a bounded batch of tombstones whose window has elapsed.

    Rows are taken with ``SKIP LOCKED`` so a second sweeper works on different data
    instead of waiting behind the first, and a tombstone that has failed too often is
    left for an operator rather than retried forever.
    """
    statement = (
        select(RetentionTombstone)
        .where(
            RetentionTombstone.deleted_at.is_(None),
            RetentionTombstone.eligible_at <= now,
        )
        .order_by(RetentionTombstone.eligible_at, RetentionTombstone.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    if max_failures is not None:
        statement = statement.where(RetentionTombstone.failure_count < max_failures)
    return tuple(_claimed(row) for row in session.scalars(statement))


def record_purge_failure(
    session: Session, *, tombstone: DueTombstone, error_code: str, now: datetime
) -> None:
    """Count one failed attempt against the tombstone that authorized it.

    Only the failure and its stable code are written: the target never changes, so a
    struggling attempt can never graduate into a broader one.
    """
    del now
    row = _locked(session, tombstone)
    row.failure_count += 1
    row.last_error = error_code
    session.flush()


def mark_tombstone_purged(session: Session, *, tombstone: DueTombstone, now: datetime) -> None:
    """Close one tombstone once its data is gone, keeping it as the compliance record."""
    row = _locked(session, tombstone)
    row.deleted_at = now
    row.last_error = None
    session.flush()


def purge_storage_prefix(store: ObjectStore, *, prefix: str, page_size: int) -> PurgeOutcome:
    """Delete at most one page of objects beneath one prefix.

    The listing is bounded so a large Workspace is swept across several passes rather
    than held in memory, and every key a provider returns is checked against the prefix
    before it is deleted, because a listing is evidence rather than an instruction.
    """
    listing = store.list_objects(prefix=prefix, limit=page_size)
    deleted = 0
    for key in listing.keys:
        if not contains_key(prefix, key):
            continue
        store.delete_object(key=key)
        deleted += 1
    return PurgeOutcome(deleted_keys=deleted, finished=listing.next_token is None)


def purge_project_rows(session: Session, *, workspace_id: UUID, project_id: UUID) -> int:
    """Delete one Project's durable rows, children before the parents they reference.

    The order is the schema's own dependency order read backwards, so a foreign key can
    never be violated by a table this task did not think about, and the Project row
    itself is removed last.
    """
    deleted = sum(
        _delete_where(
            session,
            table=table_name,
            predicate=PROJECT_SCOPE_PREDICATES[table_name],
            parameters={"workspace_id": workspace_id, "project_id": project_id},
        )
        for table_name in _child_first_tables()
        if table_name in PROJECT_SCOPE_PREDICATES
    )
    return deleted + _delete_where(
        session,
        table="projects",
        predicate="id = :project_id",
        parameters={"workspace_id": workspace_id, "project_id": project_id},
    )


def purge_workspace_rows(session: Session, *, workspace_id: UUID) -> int:
    """Delete one Workspace's tenant rows while keeping its compliance record.

    The Workspace row survives, marked deleted, because its tombstone, its audit trail,
    and its Membership history all reference it and are the evidence that the deletion
    happened at all.
    """
    return sum(
        _delete_where(
            session,
            table=table_name,
            predicate="TRUE",
            parameters={"workspace_id": workspace_id},
        )
        for table_name in _child_first_tables()
        if table_name not in WORKSPACE_PRESERVED_TABLES
    )


def _child_first_tables() -> tuple[str, ...]:
    """Return every tenant table in an order that never orphans a foreign key."""
    return tuple(
        table.name
        for table in reversed(Base.metadata.sorted_tables)
        if "workspace_id" in table.columns
    )


def _delete_where(
    session: Session, *, table: str, predicate: str, parameters: Mapping[str, object]
) -> int:
    """Delete from one named table inside the Workspace the caller already declared.

    Both the table name and the predicate come from the tables above rather than from
    any caller input, and every statement carries its Workspace explicitly even though
    row-level security would confine it anyway.
    """
    result = session.connection().execute(
        text(f"DELETE FROM {table} WHERE workspace_id = :workspace_id AND ({predicate})"),
        dict(parameters),
    )
    return int(result.rowcount or 0)


def _locked(session: Session, tombstone: DueTombstone) -> RetentionTombstone:
    """Re-read the claimed row inside the transaction that holds its lock."""
    row = session.get(RetentionTombstone, tombstone.tombstone_id)
    if row is None:
        raise LookupError("claimed tombstone is unavailable")
    return row


def _claimed(row: RetentionTombstone) -> DueTombstone:
    """Describe one locked tombstone without handing an ORM row to a caller."""
    return DueTombstone(
        tombstone_id=row.id,
        workspace_id=row.workspace_id,
        entity_kind=RetentionEntityKind(row.entity_kind),
        entity_id=row.entity_id,
        storage_prefix=row.storage_prefix,
        failure_count=row.failure_count,
        deleted_at=row.deleted_at,
    )
