"""Public HTTP contracts for service health and failure responses."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import perf_counter

from fastapi.testclient import TestClient
from httpx import Response

from clipah.api.app import ReadinessProbes, create_app
from clipah.api.errors import ApiError
from clipah.config import Environment, Settings


def test_live_reports_version_without_contacting_dependencies() -> None:
    """A liveness check must stay available even when dependencies are unavailable."""

    async def unavailable_probe() -> None:
        raise AssertionError("liveness must not invoke a readiness probe")

    app = create_app(
        Settings(environment=Environment.TEST),
        readiness_probes=ReadinessProbes(
            database=unavailable_probe,
            redis=unavailable_probe,
            object_store=unavailable_probe,
        ),
    )

    response = TestClient(app).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}
    assert app.version == "0.1.0"


def test_ready_reports_healthy_after_all_dependency_probes_succeed() -> None:
    """A readiness response is healthy only after every dependency probe succeeds."""
    completed: list[str] = []

    def probe(name: str) -> Callable[[], Awaitable[None]]:
        async def run() -> None:
            completed.append(name)

        return run

    app = create_app(
        Settings(environment=Environment.TEST),
        readiness_probes=ReadinessProbes(
            database=probe("database"),
            redis=probe("redis"),
            object_store=probe("object_store"),
        ),
    )

    response = TestClient(app).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}
    assert sorted(completed) == ["database", "object_store", "redis"]


def test_readiness_failure_is_sanitized_and_correlated() -> None:
    """A dependency error must not leak provider details through readiness."""

    async def failed_database_probe() -> None:
        raise RuntimeError("postgres password=leaked at /private/credentials")

    app = create_app(
        Settings(environment=Environment.TEST),
        readiness_probes=ReadinessProbes(database=failed_database_probe),
    )

    response = TestClient(app).get("/health/ready", headers={"X-Request-ID": "ready-123"})

    assert_error(
        response,
        status_code=503,
        code="SERVICE_UNAVAILABLE",
        message="A required service is unavailable.",
        request_id="ready-123",
    )
    assert "password" not in response.text
    assert "/private" not in response.text


def test_readiness_times_out_each_probe_concurrently() -> None:
    """Three hung dependencies must fail readiness near one two-second probe timeout."""

    async def hung_probe() -> None:
        await asyncio.sleep(5)

    app = create_app(
        Settings(environment=Environment.TEST),
        readiness_probes=ReadinessProbes(
            database=hung_probe,
            redis=hung_probe,
            object_store=hung_probe,
        ),
    )

    started_at = perf_counter()
    response = TestClient(app).get("/health/ready")
    elapsed_seconds = perf_counter() - started_at

    assert_error(
        response,
        status_code=503,
        code="SERVICE_UNAVAILABLE",
        message="A required service is unavailable.",
    )
    assert 1.75 <= elapsed_seconds < 3.5


def test_safe_request_id_is_propagated_to_unknown_route_errors() -> None:
    """A caller's safe correlation ID must remain intact across framework 404 handling."""
    response = client().get("/not-a-route", headers={"X-Request-ID": "request-123_ABC"})

    assert_error(
        response,
        status_code=404,
        code="NOT_FOUND",
        message="The requested resource was not found.",
        request_id="request-123_ABC",
    )


def test_framework_method_errors_keep_their_http_status() -> None:
    """A framework HTTP error must use the stable envelope without changing its status."""
    response = client().post("/health/live")

    assert_error(
        response,
        status_code=405,
        code="HTTP_ERROR",
        message="The request could not be completed.",
    )


def test_unsafe_request_id_is_replaced() -> None:
    """An invalid caller-supplied request ID must never be reflected to other clients."""
    response = client().get("/not-a-route", headers={"X-Request-ID": "unsafe request id"})

    assert response.status_code == 404
    request_id = response.json()["error"]["requestId"]
    assert request_id == response.headers["X-Request-ID"]
    assert request_id != "unsafe request id"


def test_validation_errors_use_the_stable_error_envelope() -> None:
    """Request validation failures must not expose framework-specific details."""
    app = create_app(Settings(environment=Environment.TEST))

    @app.get("/_contract/validation")
    async def validation_endpoint(limit: int) -> dict[str, int]:
        return {"limit": limit}

    response = TestClient(app).get("/_contract/validation?limit=invalid")

    assert_error(
        response,
        status_code=422,
        code="VALIDATION_ERROR",
        message="Request validation failed.",
    )


def test_api_errors_use_the_stable_error_envelope() -> None:
    """Domain-level API failures must preserve their explicit safe code and message."""
    app = create_app(Settings(environment=Environment.TEST))

    @app.get("/_contract/api-error")
    async def api_error_endpoint() -> None:
        raise ApiError(status_code=409, code="CONFLICT", message="The resource changed.")

    response = TestClient(app).get("/_contract/api-error")

    assert_error(
        response,
        status_code=409,
        code="CONFLICT",
        message="The resource changed.",
    )


def test_unhandled_errors_are_sanitized() -> None:
    """Unexpected exceptions must not disclose internal exception text or stack traces."""
    app = create_app(Settings(environment=Environment.TEST))

    @app.get("/_contract/internal-error")
    async def internal_error_endpoint() -> None:
        raise RuntimeError("command failed: /usr/bin/tool --token secret-value")

    response = TestClient(app, raise_server_exceptions=False).get("/_contract/internal-error")

    assert_error(
        response,
        status_code=500,
        code="INTERNAL_ERROR",
        message="An unexpected error occurred.",
    )
    assert "secret-value" not in response.text
    assert "/usr/bin/tool" not in response.text


def client() -> TestClient:
    """Build a default test application without infrastructure access."""
    return TestClient(create_app(Settings(environment=Environment.TEST)))


def assert_error(
    response: Response,
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str | None = None,
) -> None:
    """Assert the public envelope shared by every HTTP failure path."""
    actual_status_code = response.status_code
    actual_json = response.json()
    actual_headers = response.headers
    actual_request_id = request_id or actual_json["error"]["requestId"]
    assert actual_status_code == status_code
    assert actual_json == {
        "error": {"code": code, "message": message, "requestId": actual_request_id}
    }
    assert actual_headers["X-Request-ID"] == actual_request_id
