"""Unit contracts for server-generated tenant object keys."""

from __future__ import annotations

from uuid import UUID

import pytest

from clipah.assets.keys import derived_asset_key, generated_asset_key, source_upload_key
from clipah.models import AssetKind


@pytest.mark.unit
def test_source_upload_key_is_tenant_prefixed_and_does_not_use_client_filename() -> None:
    """A hostile display filename must never influence the private object location."""
    workspace_id = UUID("11111111-1111-1111-1111-111111111111")
    project_id = UUID("22222222-2222-2222-2222-222222222222")

    key = source_upload_key(
        workspace_id=workspace_id,
        project_id=project_id,
        object_id=UUID("33333333-3333-3333-3333-333333333333"),
        client_filename="../../other-workspace/secret.mp4",
    )

    assert key == (
        "workspaces/11111111-1111-1111-1111-111111111111/"
        "projects/22222222-2222-2222-2222-222222222222/source/"
        "33333333-3333-3333-3333-333333333333"
    )


@pytest.mark.unit
def test_source_upload_key_rejects_an_object_identifier_that_cannot_be_server_generated() -> None:
    """The key boundary refuses arbitrary string identifiers and traversal fragments."""
    with pytest.raises(TypeError):
        source_upload_key(  # type: ignore[arg-type]
            workspace_id="workspace",
            project_id=UUID("22222222-2222-2222-2222-222222222222"),
            object_id=UUID("33333333-3333-3333-3333-333333333333"),
            client_filename="normal.mp4",
        )


@pytest.mark.unit
def test_derived_asset_key_is_scoped_to_source_identity_and_kind() -> None:
    """Retries must converge on one server-owned derivative location per source and kind."""
    key = derived_asset_key(
        workspace_id=UUID("11111111-1111-1111-1111-111111111111"),
        project_id=UUID("22222222-2222-2222-2222-222222222222"),
        source_asset_id=UUID("33333333-3333-3333-3333-333333333333"),
        kind=AssetKind.PROXY,
    )

    assert key == (
        "workspaces/11111111-1111-1111-1111-111111111111/"
        "projects/22222222-2222-2222-2222-222222222222/"
        "derived/33333333-3333-3333-3333-333333333333/proxy"
    )


@pytest.mark.unit
def test_derived_asset_key_rejects_untrusted_identifiers_and_non_ingest_kinds() -> None:
    """No caller may widen the deterministic derivative namespace with strings or render kinds."""
    identifiers = {
        "workspace_id": UUID("11111111-1111-1111-1111-111111111111"),
        "project_id": UUID("22222222-2222-2222-2222-222222222222"),
        "source_asset_id": UUID("33333333-3333-3333-3333-333333333333"),
    }
    with pytest.raises(TypeError):
        derived_asset_key(  # type: ignore[arg-type]
            **{**identifiers, "source_asset_id": "../source"}, kind=AssetKind.PROXY
        )
    with pytest.raises(ValueError):
        derived_asset_key(**identifiers, kind=AssetKind.RENDER)


@pytest.mark.unit
def test_generated_asset_key_separates_model_output_from_retrieved_footage() -> None:
    """Retention and audit both need to find generated media without walking stock."""
    key = generated_asset_key(
        workspace_id=UUID("11111111-1111-1111-1111-111111111111"),
        project_id=UUID("22222222-2222-2222-2222-222222222222"),
        asset_id=UUID("44444444-4444-4444-4444-444444444444"),
        kind=AssetKind.BROLL,
    )

    assert key == (
        "workspaces/11111111-1111-1111-1111-111111111111/"
        "projects/22222222-2222-2222-2222-222222222222/"
        "generated/44444444-4444-4444-4444-444444444444/broll"
    )


@pytest.mark.unit
def test_generated_asset_key_rejects_untrusted_identifiers_and_foreign_kinds() -> None:
    """No caller may place arbitrary media, or a path fragment, inside the generated prefix."""
    identifiers = {
        "workspace_id": UUID("11111111-1111-1111-1111-111111111111"),
        "project_id": UUID("22222222-2222-2222-2222-222222222222"),
        "asset_id": UUID("44444444-4444-4444-4444-444444444444"),
    }
    with pytest.raises(TypeError):
        generated_asset_key(  # type: ignore[arg-type]
            **{**identifiers, "asset_id": "../escape"}, kind=AssetKind.BROLL
        )
    with pytest.raises(ValueError):
        generated_asset_key(**identifiers, kind=AssetKind.SOURCE)
