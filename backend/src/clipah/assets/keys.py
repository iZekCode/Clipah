"""Server-owned object key construction for Workspace-scoped media."""

from __future__ import annotations

from uuid import UUID

from clipah.models import AssetKind


def source_upload_key(
    *, workspace_id: UUID, project_id: UUID, object_id: UUID, client_filename: str
) -> str:
    """Return a private source-media key without using untrusted display metadata."""
    if not all(isinstance(value, UUID) for value in (workspace_id, project_id, object_id)):
        raise TypeError("storage identifiers must be UUID values")
    del client_filename
    return f"workspaces/{workspace_id}/projects/{project_id}/source/{object_id}"


def derived_asset_key(
    *, workspace_id: UUID, project_id: UUID, source_asset_id: UUID, kind: AssetKind
) -> str:
    """Return the deterministic private key for one supported ingest derivative."""
    if not all(isinstance(value, UUID) for value in (workspace_id, project_id, source_asset_id)):
        raise TypeError("storage identifiers must be UUID values")
    if kind not in {AssetKind.PROXY, AssetKind.THUMBNAIL, AssetKind.TRANSCRIPTION_AUDIO}:
        raise ValueError("unsupported ingest derivative kind")
    return f"workspaces/{workspace_id}/projects/{project_id}/derived/{source_asset_id}/{kind.value}"
