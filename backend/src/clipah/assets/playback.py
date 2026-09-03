"""Workspace-scoped read of the bounded proxy capability clip review plays against."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.assets.storage import ObjectStore, SignedUrl
from clipah.assets.uploads import SIGNED_URL_TTL
from clipah.models import Asset, AssetKind, Project
from clipah.workspaces.models import WorkspaceAccess


class ProxyNotFoundError(Exception):
    """No playable proxy exists for this Project inside the authorized Workspace."""


@dataclass(frozen=True, slots=True)
class ProxyPlayback:
    """One time-bounded proxy capability and the shape of the media behind it."""

    download: SignedUrl
    content_type: str
    duration_ms: int | None
    width: int | None
    height: int | None


def proxy_playback(
    session: Session,
    store: ObjectStore,
    *,
    access: WorkspaceAccess,
    project_id: UUID,
) -> ProxyPlayback:
    """Sign the newest proxy of one active Project the caller already has standing on.

    A Project that was never produced, was deleted, or belongs to another Workspace all
    end here the same way: there is nothing to play, and the caller learns nothing else.
    """
    asset = session.scalar(
        select(Asset)
        .join(
            Project,
            (Project.workspace_id == Asset.workspace_id) & (Project.id == Asset.project_id),
        )
        .where(
            Asset.workspace_id == access.workspace_id,
            Asset.project_id == project_id,
            Asset.kind == AssetKind.PROXY,
            Project.archived_at.is_(None),
        )
        .order_by(Asset.created_at.desc(), Asset.id.desc())
        .limit(1)
    )
    if asset is None:
        raise ProxyNotFoundError(str(project_id))
    return ProxyPlayback(
        download=store.sign_download(key=asset.storage_key, expires_in=SIGNED_URL_TTL),
        content_type=asset.content_type,
        duration_ms=asset.duration_ms,
        width=asset.width,
        height=asset.height,
    )
