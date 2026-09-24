"""Workspace-scoped read of the media one Project's compositions may place.

A composition may only name assets the owning Project holds, and that rule is enforced
at save time. This module answers the same question ahead of time, so the editor's
assets panel offers exactly the media a save would accept — never a derived rendition
that exists for the pipeline's sake, and never another Project's media.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, exists, select
from sqlalchemy.orm import Session

from clipah.models import Asset, AssetKind, Project, RetentionTombstone
from clipah.retention.policy import RetentionEntityKind
from clipah.workspaces.models import WorkspaceAccess

# Proxies, thumbnails, waveforms, transcription audio, and renders are produced by the
# pipeline for the pipeline. A member never places one on a timeline. Retrieved B-roll is
# the exception among non-source media: a member chose it, and replacing an accepted
# suggestion's picture means naming another asset this Project already holds. A picture
# the member uploaded, such as a logo, is placed as a watermark.
PLACEABLE_KINDS: frozenset[AssetKind] = frozenset(
    {AssetKind.SOURCE, AssetKind.BROLL, AssetKind.PICTURE}
)


def not_removed(asset: type[Asset] = Asset) -> ColumnElement[bool]:
    """Say, in SQL, that an Asset is not a picture its member removed.

    Removal leaves the row as the record of what was kept, and schedules its bytes for
    deletion; every query that offers, signs, or authorizes media excludes it through
    this one predicate.
    """
    return ~exists().where(
        RetentionTombstone.workspace_id == asset.workspace_id,
        RetentionTombstone.entity_kind == RetentionEntityKind.REMOVED_PICTURE.value,
        RetentionTombstone.entity_id == asset.id,
    )


class ProjectNotFoundError(Exception):
    """The Project does not exist, or the caller may not learn that it does."""


@dataclass(frozen=True, slots=True)
class LibraryAsset:
    """One placeable asset and the shape the editor needs before it offers to add it."""

    asset_id: UUID
    kind: AssetKind
    content_type: str
    size_bytes: int
    duration_ms: int | None
    width: int | None
    height: int | None
    created_at: datetime


def project_assets(
    session: Session, *, access: WorkspaceAccess, project_id: UUID
) -> tuple[LibraryAsset, ...]:
    """List the media of one active Project the caller already has standing on.

    A Project that never existed, was deleted, or belongs to another Workspace all end
    here the same way, so a guessed identifier teaches nothing. An active Project that
    holds no placeable media answers with an empty list, which is a different fact.
    """
    exists = session.scalar(
        select(Project.id).where(
            Project.workspace_id == access.workspace_id,
            Project.id == project_id,
            Project.archived_at.is_(None),
        )
    )
    if exists is None:
        raise ProjectNotFoundError(str(project_id))
    assets = session.scalars(
        select(Asset)
        .where(
            Asset.workspace_id == access.workspace_id,
            Asset.project_id == project_id,
            Asset.kind.in_(PLACEABLE_KINDS),
            not_removed(),
        )
        .order_by(Asset.created_at.asc(), Asset.id.asc())
    )
    return tuple(
        LibraryAsset(
            asset_id=asset.id,
            kind=asset.kind,
            content_type=asset.content_type,
            size_bytes=asset.size_bytes,
            duration_ms=asset.duration_ms,
            width=asset.width,
            height=asset.height,
            created_at=asset.created_at,
        )
        for asset in assets
    )
