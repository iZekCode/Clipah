"""Unit contracts for server-generated tenant object keys."""

from __future__ import annotations

from uuid import UUID

import pytest

from clipah.assets.keys import source_upload_key


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
