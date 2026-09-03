"""Contract for the published OpenAPI document the typed frontend client is generated from.

The browser never hand-writes a request or response type; it generates them from this
document. An endpoint the product UI reads must therefore describe its response, because
an undescribed response becomes an untyped bag of keys in every caller.
"""

from __future__ import annotations

from typing import Any

import pytest

from clipah.api.app import ReadinessProbes, create_app
from clipah.config import Environment, Settings

# Every endpoint the signed-in dashboard reads or writes on its first screens.
TYPED_OPERATIONS = (
    ("/api/v1/me", "get", "200"),
    ("/api/v1/workspaces", "get", "200"),
    ("/api/v1/workspaces", "post", "201"),
    ("/api/v1/workspaces/{workspace_id}", "get", "200"),
    ("/api/v1/workspaces/{workspace_id}/members", "get", "200"),
    ("/api/v1/projects", "get", "200"),
    ("/api/v1/projects", "post", "201"),
    ("/api/v1/projects/{project_id}", "get", "200"),
    ("/api/v1/projects/{project_id}", "patch", "200"),
    ("/api/v1/dashboard/summary", "get", "200"),
    ("/api/v1/edits/{edit_id}", "get", "200"),
    ("/api/v1/edits/{edit_id}", "put", "200"),
    ("/api/v1/edits/{edit_id}/revisions", "get", "200"),
    ("/api/v1/projects/{project_id}/candidates/{candidate_id}/edits", "post", "200"),
)


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    """Build the document exactly as `scripts/export-openapi.sh` publishes it."""
    app = create_app(Settings(environment=Environment.TEST), readiness_probes=ReadinessProbes())
    return app.openapi()


@pytest.mark.unit
@pytest.mark.parametrize(("path", "method", "status"), TYPED_OPERATIONS)
def test_every_dashboard_endpoint_publishes_a_named_response_schema(
    document: dict[str, Any], path: str, method: str, status: str
) -> None:
    """A generated client can only be typed if the document says what comes back."""
    schema = _success_schema(document, path=path, method=method, status=status)

    assert "$ref" in schema, f"{method.upper()} {path} returns an undescribed object"


@pytest.mark.unit
@pytest.mark.parametrize(("path", "method", "status"), TYPED_OPERATIONS)
def test_no_dashboard_response_schema_allows_undeclared_fields(
    document: dict[str, Any], path: str, method: str, status: str
) -> None:
    """A response that permits extra keys hides the day one of them starts leaking."""
    schema = _success_schema(document, path=path, method=method, status=status)
    name = schema["$ref"].removeprefix("#/components/schemas/")
    component = document["components"]["schemas"][name]

    assert component.get("additionalProperties") is not True


def _success_schema(
    document: dict[str, Any], *, path: str, method: str, status: str
) -> dict[str, Any]:
    """Read the JSON schema one successful operation promises its callers."""
    responses = document["paths"][path][method]["responses"]
    schema: dict[str, Any] = responses[status]["content"]["application/json"]["schema"]
    return schema
