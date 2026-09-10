"""What Clipah keeps, for how long, and the exact storage prefix it may ever touch.

Deletion is the one operation no later task can undo, so the rules live here as data:
one duration per kind of expired record, one prefix builder per scope, and one
containment check every batch must pass before a single object key is removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from clipah.config import Settings

# Retention runs as no member of any Workspace. The row policies still demand an actor,
# so the sweep declares this fixed identifier: it belongs to no User, owns no Membership,
# and therefore grants nothing beyond satisfying the predicate it was written for.
RETENTION_ACTOR_ID = UUID("00000000-0000-4000-8000-000000000001")


class RetentionEntityKind(StrEnum):
    """The kinds of expired data a tombstone may name.

    A tombstone's kind decides both how long it waits and what its purge is allowed
    to remove, so nothing broader than the named entity is ever in scope.
    """

    PROJECT = "project"
    WORKSPACE = "workspace"
    USER = "user"
    MULTIPART_UPLOAD = "multipart_upload"
    JOB_WORKSPACE = "job_workspace"
    SOURCE_CONNECTION = "source_connection"
    SOCIAL_ACCOUNT = "social_account"
    GENERATED_DRAFT = "generated_draft"
    STOCK_PREVIEW = "stock_preview"


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """One deployment's retention durations and batch bounds."""

    delays: MappingProxyType[RetentionEntityKind, timedelta]
    batch_size: int
    listing_page_size: int
    max_failures: int

    @classmethod
    def from_settings(cls, settings: Settings) -> RetentionPolicy:
        """Read every duration from configuration so no module invents one of its own."""
        return cls(
            delays=MappingProxyType(
                {
                    RetentionEntityKind.PROJECT: timedelta(
                        days=settings.retention_soft_deleted_project_days
                    ),
                    RetentionEntityKind.WORKSPACE: timedelta(
                        days=settings.retention_soft_deleted_workspace_days
                    ),
                    RetentionEntityKind.USER: timedelta(days=settings.retention_deleted_user_days),
                    RetentionEntityKind.MULTIPART_UPLOAD: timedelta(
                        hours=settings.retention_abandoned_upload_hours
                    ),
                    RetentionEntityKind.JOB_WORKSPACE: timedelta(
                        days=settings.retention_failed_job_workspace_days
                    ),
                    # A revoked connection's credentials are deleted at the first
                    # opportunity: Section 7 gives expired cookie material no window.
                    RetentionEntityKind.SOURCE_CONNECTION: timedelta(0),
                    RetentionEntityKind.SOCIAL_ACCOUNT: timedelta(0),
                    RetentionEntityKind.GENERATED_DRAFT: timedelta(
                        hours=settings.retention_rejected_generated_draft_hours
                    ),
                    RetentionEntityKind.STOCK_PREVIEW: timedelta(
                        hours=settings.retention_unselected_stock_preview_hours
                    ),
                }
            ),
            batch_size=settings.retention_batch_size,
            listing_page_size=settings.retention_listing_page_size,
            max_failures=settings.retention_max_failures,
        )

    def delay_for(self, kind: RetentionEntityKind) -> timedelta:
        """Return how long this kind of data survives the request that ended it."""
        return self.delays[kind]

    def eligible_at(self, kind: RetentionEntityKind, *, now: datetime) -> datetime:
        """Return the instant a sweep may first remove data deleted at ``now``."""
        return now + self.delay_for(kind)


def workspace_prefix(workspace_id: UUID) -> str:
    """Return the one storage prefix that holds a Workspace's media and nothing else."""
    return f"workspaces/{workspace_id}/"


def project_prefix(*, workspace_id: UUID, project_id: UUID) -> str:
    """Return the one storage prefix that holds a Project's media and nothing else."""
    return f"{workspace_prefix(workspace_id)}projects/{project_id}/"


def contains_key(prefix: str, key: str) -> bool:
    """Report whether one object key genuinely lies inside the named prefix.

    A provider listing is evidence about the world, not an instruction, so every key
    it returns is checked against the prefix the tombstone recorded. The prefix always
    ends in a separator, so a neighbouring name cannot be swept by a shared stem, and a
    key containing a traversal segment is refused rather than resolved.
    """
    if not prefix.endswith("/"):
        return False
    if not key.startswith(prefix) or len(key) == len(prefix):
        return False
    return ".." not in key.split("/")
