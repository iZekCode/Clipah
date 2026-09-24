"""Server-owned object key construction for Workspace-scoped media."""

from __future__ import annotations

import re
from uuid import UUID

from clipah.models import AssetKind

_PREVIEW_MEDIA_NAME = re.compile(
    r"(?:storyboard-v1/sheet-\d{4}\.jpg|waveform-v1\.bin"
    r"|posters-v1/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jpg"
    r"|covers-v1/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jpg)"
)


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


def render_artifact_key(
    *, workspace_id: UUID, project_id: UUID, revision_id: UUID, preset: str
) -> str:
    """Return the deterministic private key one export of one Revision is stored under."""
    if not all(isinstance(value, UUID) for value in (workspace_id, project_id, revision_id)):
        raise TypeError("storage identifiers must be UUID values")
    if not preset or any(character not in "0123456789x" for character in preset):
        raise ValueError("a render preset names a frame size and nothing else")
    return f"workspaces/{workspace_id}/projects/{project_id}/renders/{revision_id}/{preset}.mp4"


def broll_asset_key(
    *, workspace_id: UUID, project_id: UUID, asset_id: UUID, kind: AssetKind
) -> str:
    """Return the deterministic private key one retrieved B-roll asset is stored under.

    Retrieved footage lives under its own prefix rather than beside the Project's source
    media, so retention can expire unselected stock without walking the source tree, and
    so nothing external is ever mistaken for something the member uploaded.
    """
    if not all(isinstance(value, UUID) for value in (workspace_id, project_id, asset_id)):
        raise TypeError("storage identifiers must be UUID values")
    if kind not in {AssetKind.BROLL, AssetKind.BROLL_PROXY}:
        raise ValueError("unsupported B-roll asset kind")
    return f"workspaces/{workspace_id}/projects/{project_id}/broll/{asset_id}/{kind.value}"


def generated_asset_key(
    *, workspace_id: UUID, project_id: UUID, asset_id: UUID, kind: AssetKind
) -> str:
    """Return the deterministic private key one generated B-roll asset is stored under.

    Generated media lives under its own prefix rather than beside retrieved footage,
    because the two carry different obligations: stock is licensed from someone, and a
    model's output has to be findable as a model's output for as long as it exists.
    """
    if not all(isinstance(value, UUID) for value in (workspace_id, project_id, asset_id)):
        raise TypeError("storage identifiers must be UUID values")
    if kind not in {AssetKind.BROLL, AssetKind.BROLL_PROXY}:
        raise ValueError("unsupported generated asset kind")
    return f"workspaces/{workspace_id}/projects/{project_id}/generated/{asset_id}/{kind.value}"


def preview_media_key(
    *, workspace_id: UUID, project_id: UUID, source_asset_id: UUID, name: str
) -> str:
    """Return the deterministic private key of one versioned preview beside its derivatives."""
    if not all(isinstance(value, UUID) for value in (workspace_id, project_id, source_asset_id)):
        raise TypeError("storage identifiers must be UUID values")
    if _PREVIEW_MEDIA_NAME.fullmatch(name) is None:
        raise ValueError("unsupported preview media name")
    return f"workspaces/{workspace_id}/projects/{project_id}/derived/{source_asset_id}/{name}"
