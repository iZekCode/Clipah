"""FastAPI application factory and dependency-free health endpoints."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from redis import Redis
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from clipah.api.dependencies import AuthComponents, default_auth_components
from clipah.api.errors import ApiError, error_response
from clipah.api.request_id import REQUEST_ID_HEADER, assign_request_id, request_id_for
from clipah.api.routes import auth as auth_routes
from clipah.api.routes import jobs as job_routes
from clipah.api.routes import projects as project_routes
from clipah.api.routes import uploads as upload_routes
from clipah.api.routes import workspaces as workspace_routes
from clipah.assets.storage import ObjectStore, S3ObjectStore
from clipah.auth.limits import RateLimiter, RedisRateLimiter
from clipah.config import Settings
from clipah.jobs.events import (
    JobEventNotifier,
    PollingJobEventNotifier,
    RedisJobEventNotifier,
)

VERSION = "0.1.0"
READINESS_TIMEOUT_SECONDS = 2.0
ReadinessProbe = Callable[[], Awaitable[None]]


async def _ready() -> None:
    """Represent an adapter that has not been installed by a later task yet."""


@dataclass(frozen=True, slots=True)
class ReadinessProbes:
    """Health probes supplied by the database and infrastructure composition root."""

    database: ReadinessProbe = _ready
    redis: ReadinessProbe = _ready
    object_store: ReadinessProbe = _ready

    def all(self) -> Iterable[ReadinessProbe]:
        """Return every dependency probe without exposing route-layer details."""
        return self.database, self.redis, self.object_store


def create_app(
    settings: Settings,
    *,
    readiness_probes: ReadinessProbes | None = None,
    auth_components: AuthComponents | None = None,
    object_store: ObjectStore | None = None,
    rate_limiter: RateLimiter | None = None,
    job_event_notifier: JobEventNotifier | None = None,
) -> FastAPI:
    """Create the typed HTTP application with stable health and failure contracts."""
    probes = readiness_probes or ReadinessProbes()
    # Public error responses stay sanitized even when local configuration enables debugging.
    app = FastAPI(title="Clipah API", version=VERSION, debug=False)
    app.state.settings = settings
    app.state.auth_components = auth_components or default_auth_components(settings)
    app.state.object_store = object_store or _configured_object_store(settings)
    app.state.rate_limiter = rate_limiter or _configured_rate_limiter(settings, app)
    app.state.job_event_notifier = job_event_notifier or _configured_job_event_notifier(settings)

    @app.middleware("http")
    async def add_request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = assign_request_id(request)
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, error: ApiError) -> Response:
        return error_response(
            status_code=error.status_code,
            code=error.code,
            request_id=request_id_for(request),
            retry_after_seconds=error.retry_after_seconds,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, error: StarletteHTTPException) -> Response:
        code = "NOT_FOUND" if error.status_code == 404 else "HTTP_ERROR"
        return error_response(
            status_code=error.status_code,
            code=code,
            request_id=request_id_for(request),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, _: RequestValidationError) -> Response:
        return error_response(
            status_code=422,
            code="VALIDATION_ERROR",
            request_id=request_id_for(request),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, _: Exception) -> Response:
        return error_response(
            status_code=500,
            code="INTERNAL_ERROR",
            request_id=request_id_for(request),
        )

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        """Confirm that the HTTP process can accept requests without external I/O."""
        return health_response()

    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        """Confirm that all mandatory infrastructure adapters are reachable."""
        results = await asyncio.gather(
            *(_run_probe(probe) for probe in probes.all()), return_exceptions=True
        )
        if any(isinstance(result, BaseException) for result in results):
            raise ApiError(
                status_code=503,
                code="SERVICE_UNAVAILABLE",
                message="A required service is unavailable.",
            )
        return health_response()

    app.include_router(auth_routes.router)
    app.include_router(workspace_routes.router)
    app.include_router(project_routes.router)
    app.include_router(upload_routes.router)
    app.include_router(job_routes.router)
    return app


def _configured_job_event_notifier(settings: Settings) -> JobEventNotifier:
    """Push job wakeups over Redis when there is one, and poll the database otherwise."""
    if settings.redis_url is None:
        return PollingJobEventNotifier()
    return RedisJobEventNotifier(Redis.from_url(settings.redis_url))


def _configured_object_store(settings: Settings) -> ObjectStore | None:
    """Create the production adapter only when every S3-compatible setting is configured."""
    if (
        settings.object_store_bucket is None
        or settings.object_store_access_key_id is None
        or settings.object_store_secret_access_key is None
    ):
        return None
    return S3ObjectStore(
        bucket=settings.object_store_bucket,
        endpoint_url=settings.object_store_endpoint,
        access_key_id=settings.object_store_access_key_id.get_secret_value(),
        secret_access_key=settings.object_store_secret_access_key.get_secret_value(),
    )


def _configured_rate_limiter(settings: Settings, app: FastAPI) -> RateLimiter | None:
    """Build the shared limiter only for a deployment that was given Redis.

    Production configuration already requires ``CLIPAH_REDIS_URL``, so only local
    profiles run without one, and they run unlimited by design.
    """
    if settings.redis_url is None:
        return None
    components: AuthComponents = app.state.auth_components
    return RedisRateLimiter(Redis.from_url(settings.redis_url), now=components.now)


async def _run_probe(probe: ReadinessProbe) -> None:
    """Run one readiness probe with its own bounded timeout."""
    await asyncio.wait_for(probe(), timeout=READINESS_TIMEOUT_SECONDS)


def health_response() -> dict[str, str]:
    """Return the common versioned health resource representation."""
    return {"status": "ok", "version": VERSION}
