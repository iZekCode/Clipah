"""Public HTTP contracts for service health and failure responses."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import perf_counter

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from clipah.api.app import ReadinessProbes, create_app
from clipah.api.errors import ApiError
from clipah.config import Environment, Settings
from clipah.observability.logging import capture_logs


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

    response = request(app, "GET", "/health/live")

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

    response = request(app, "GET", "/health/ready")

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

    response = request(app, "GET", "/health/ready", headers={"X-Request-ID": "ready-123"})

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
    response = request(app, "GET", "/health/ready")
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
    response = request(
        default_app(), "GET", "/not-a-route", headers={"X-Request-ID": "request-123_ABC"}
    )

    assert_error(
        response,
        status_code=404,
        code="NOT_FOUND",
        message="The requested resource was not found.",
        request_id="request-123_ABC",
    )


def test_framework_method_errors_keep_their_http_status() -> None:
    """A framework HTTP error must use the stable envelope without changing its status."""
    response = request(default_app(), "POST", "/health/live")

    assert_error(
        response,
        status_code=405,
        code="HTTP_ERROR",
        message="The request could not be completed.",
    )


def test_unsafe_request_id_is_replaced() -> None:
    """An invalid caller-supplied request ID must never be reflected to other clients."""
    response = request(
        default_app(), "GET", "/not-a-route", headers={"X-Request-ID": "unsafe request id"}
    )

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

    response = request(app, "GET", "/_contract/validation?limit=invalid")

    assert_error(
        response,
        status_code=422,
        code="VALIDATION_ERROR",
        message="Request validation failed.",
    )


def test_api_errors_use_the_stable_error_envelope() -> None:
    """Domain-level API failures must derive their public message from the error code."""
    app = create_app(Settings(environment=Environment.TEST))

    @app.get("/_contract/api-error")
    async def api_error_endpoint() -> None:
        raise ApiError(status_code=409, code="CONFLICT", message="The resource changed.")

    response = request(app, "GET", "/_contract/api-error")

    assert_error(
        response,
        status_code=409,
        code="CONFLICT",
        message="The resource changed.",
    )


def test_api_errors_replace_untrusted_messages_with_catalog_value() -> None:
    """A route must not serialize provider or credential details carried by an ApiError."""
    app = create_app(Settings(environment=Environment.TEST))
    untrusted_detail = "provider=https://storage.example/private?token=secret-value path=/srv/jobs"

    @app.get("/_contract/api-error-detail")
    async def api_error_detail_endpoint() -> None:
        raise ApiError(status_code=502, code="UPSTREAM_FAILURE", message=untrusted_detail)

    response = request(app, "GET", "/_contract/api-error-detail")

    assert_error(
        response,
        status_code=502,
        code="UPSTREAM_FAILURE",
        message="The request could not be completed.",
    )
    assert "storage.example" not in response.text
    assert "secret-value" not in response.text
    assert "/srv/jobs" not in response.text


def test_unhandled_errors_are_sanitized() -> None:
    """Unexpected exceptions must not disclose internal exception text or stack traces."""
    app = create_app(Settings(environment=Environment.TEST))

    @app.get("/_contract/internal-error")
    async def internal_error_endpoint() -> None:
        raise RuntimeError("command failed: /usr/bin/tool --token secret-value")

    response = request(app, "GET", "/_contract/internal-error", raise_app_exceptions=False)

    assert_error(
        response,
        status_code=500,
        code="INTERNAL_ERROR",
        message="An unexpected error occurred.",
    )
    assert "secret-value" not in response.text
    assert "/usr/bin/tool" not in response.text


def default_app() -> FastAPI:
    """Build a default test application without infrastructure access."""
    return create_app(Settings(environment=Environment.TEST))


def request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    raise_app_exceptions: bool = True,
) -> Response:
    """Make one supported in-process ASGI request without Starlette's deprecated client."""
    return asyncio.run(
        _request(
            app,
            method,
            path,
            headers=headers,
            raise_app_exceptions=raise_app_exceptions,
        )
    )


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None,
    raise_app_exceptions: bool,
) -> Response:
    transport = ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, headers=headers)


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


def test_a_request_is_logged_with_its_identifier_route_and_outcome() -> None:
    """An access log nobody can join to an error report is not evidence of anything."""
    app = create_app(Settings(environment=Environment.TEST))

    with capture_logs() as events:
        response = request(app, "GET", "/health/live")

    logged = [event for event in events if event["event"] == "http.request"]
    assert logged
    assert logged[-1]["route"] == "/health/live"
    assert logged[-1]["statusCode"] == 200
    assert logged[-1]["requestId"] == response.headers["X-Request-ID"]


def test_an_unmatched_path_is_logged_without_repeating_what_was_asked_for() -> None:
    """A scanned URL is attacker-controlled text and must not become a metric label."""
    app = create_app(Settings(environment=Environment.TEST))

    with capture_logs() as events:
        request(app, "GET", "/../../etc/passwd", raise_app_exceptions=False)

    logged = [event for event in events if event["event"] == "http.request"]
    assert logged[-1]["route"] == "unmatched"
    assert "passwd" not in repr(logged[-1])


def test_readiness_fails_while_a_configured_provider_version_is_already_retired() -> None:
    """A deployment calling a switched-off API is broken whether or not anybody looks."""
    app = create_app(
        Settings(
            environment=Environment.TEST,
            provider_shutdowns=("api:v3=2020-01-01",),
        )
    )

    response = request(app, "GET", "/health/ready", raise_app_exceptions=False)

    assert_error(
        response,
        status_code=503,
        code="SERVICE_UNAVAILABLE",
        message="A required service is unavailable.",
    )
