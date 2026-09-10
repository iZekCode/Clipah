"""FastAPI application factory and dependency-free health endpoints."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from redis import Redis
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from clipah.api.dependencies import AuthComponents, default_auth_components
from clipah.api.errors import ApiError, error_response
from clipah.api.request_id import REQUEST_ID_HEADER, assign_request_id, request_id_for
from clipah.api.routes import account as account_routes
from clipah.api.routes import analysis as analysis_routes
from clipah.api.routes import assets as asset_routes
from clipah.api.routes import auth as auth_routes
from clipah.api.routes import brand_kits as brand_kit_routes
from clipah.api.routes import broll as broll_routes
from clipah.api.routes import campaigns as campaign_routes
from clipah.api.routes import candidates as candidate_routes
from clipah.api.routes import claim_evidence as claim_evidence_routes
from clipah.api.routes import dashboard as dashboard_routes
from clipah.api.routes import edit_reviews as edit_review_routes
from clipah.api.routes import edits as edit_routes
from clipah.api.routes import generation_webhooks as generation_webhook_routes
from clipah.api.routes import instagram_webhooks as instagram_webhook_routes
from clipah.api.routes import jobs as job_routes
from clipah.api.routes import playback as playback_routes
from clipah.api.routes import projects as project_routes
from clipah.api.routes import publications as publication_routes
from clipah.api.routes import renders as render_routes
from clipah.api.routes import search as search_routes
from clipah.api.routes import social_accounts as social_account_routes
from clipah.api.routes import source_connections as source_connection_routes
from clipah.api.routes import templates as template_routes
from clipah.api.routes import tiktok_webhooks as tiktok_webhook_routes
from clipah.api.routes import uploads as upload_routes
from clipah.api.routes import variants as variant_routes
from clipah.api.routes import workspace_memberships as workspace_membership_routes
from clipah.api.routes import workspaces as workspace_routes
from clipah.api.routes import youtube_imports as youtube_import_routes
from clipah.api.routes.generation_webhooks import FalWebhookVerifier, GenerationWebhookSink
from clipah.api.routes.instagram_webhooks import (
    InstagramWebhookSink,
    InstagramWebhookVerifier,
)
from clipah.api.routes.tiktok_webhooks import TikTokWebhookSink, TikTokWebhookVerifier
from clipah.assets.source_validation import validate_youtube_url
from clipah.assets.storage import ObjectStore, ObservedObjectStore, S3ObjectStore
from clipah.auth.limits import RateLimiter, RedisRateLimiter
from clipah.broll.generation_policy import GenerationProviders, configured_generation_providers
from clipah.config import Settings
from clipah.jobs.events import (
    JobEventNotifier,
    PollingJobEventNotifier,
    RedisJobEventNotifier,
)
from clipah.observability import configure_observability
from clipah.observability.logging import get_logger, log_context
from clipah.observability.metrics import observe
from clipah.observability.tracing import span
from clipah.observability.usage import readiness_report
from clipah.social_accounts.models import SocialProvider
from clipah.social_accounts.oauth import SocialOAuthProvider
from clipah.social_accounts.secrets import (
    SocialSecretStore,
    local_social_secret_store,
)
from clipah.social_accounts.use_cases import FuturePublicationCoordinator
from clipah.source_imports.dispatch import CeleryJobDispatcher, JobDispatcher
from clipah.variants.assessor import ContextSafetyAssessor, configured_context_assessor

VERSION = "0.1.0"
READINESS_TIMEOUT_SECONDS = 2.0
_logger = get_logger(__name__)
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
    job_dispatcher: JobDispatcher | None = None,
    source_url_validator: Callable[[str], object] | None = None,
    generation_webhook_verifier: FalWebhookVerifier | None = None,
    generation_webhook_sink: GenerationWebhookSink | None = None,
    generation_webhook_clock: Callable[[], datetime] | None = None,
    instagram_webhook_verifier: InstagramWebhookVerifier | None = None,
    instagram_webhook_sink: InstagramWebhookSink | None = None,
    instagram_webhook_clock: Callable[[], datetime] | None = None,
    tiktok_webhook_verifier: TikTokWebhookVerifier | None = None,
    tiktok_webhook_sink: TikTokWebhookSink | None = None,
    tiktok_webhook_clock: Callable[[], datetime] | None = None,
    generation_providers: GenerationProviders | None = None,
    context_assessor: ContextSafetyAssessor | None = None,
    social_providers: Mapping[SocialProvider, SocialOAuthProvider] | None = None,
    social_secret_store: SocialSecretStore | None = None,
    future_publications: FuturePublicationCoordinator | None = None,
) -> FastAPI:
    """Create the typed HTTP application with stable health and failure contracts."""
    configure_observability(settings)
    probes = readiness_probes or ReadinessProbes()
    # Public error responses stay sanitized even when local configuration enables debugging.
    app = FastAPI(title="Clipah API", version=VERSION, debug=False)
    app.state.settings = settings
    app.state.auth_components = auth_components or default_auth_components(settings)
    app.state.object_store = object_store or _configured_object_store(settings)
    app.state.rate_limiter = rate_limiter or _configured_rate_limiter(settings, app)
    app.state.job_event_notifier = job_event_notifier or _configured_job_event_notifier(settings)
    app.state.job_dispatcher = job_dispatcher or CeleryJobDispatcher()
    app.state.source_url_validator = source_url_validator or validate_youtube_url
    app.state.generation_webhook_verifier = generation_webhook_verifier
    app.state.generation_webhook_sink = generation_webhook_sink
    app.state.generation_webhook_clock = generation_webhook_clock or _utc_now
    app.state.instagram_webhook_verifier = instagram_webhook_verifier
    app.state.instagram_webhook_sink = instagram_webhook_sink
    app.state.instagram_webhook_clock = instagram_webhook_clock or _utc_now
    app.state.tiktok_webhook_verifier = tiktok_webhook_verifier
    app.state.tiktok_webhook_sink = tiktok_webhook_sink
    app.state.tiktok_webhook_clock = tiktok_webhook_clock or _utc_now
    app.state.generation_providers = generation_providers or configured_generation_providers(
        settings
    )
    app.state.context_assessor = context_assessor or configured_context_assessor(settings)
    app.state.social_providers = dict(social_providers or {})
    app.state.social_secret_store = social_secret_store or _configured_social_secret_store(settings)
    app.state.future_publications = future_publications

    @app.middleware("http")
    async def observe_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Correlate, time, and trace one request without ever naming its contents."""
        request_id = assign_request_id(request)
        started = perf_counter()
        status_code = 500
        with log_context(requestId=request_id), span("http.request", method=request.method):
            try:
                response = await call_next(request)
                status_code = response.status_code
                response.headers[REQUEST_ID_HEADER] = request_id
                return response
            finally:
                _record_request(request, status_code=status_code, started=started)

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, error: ApiError) -> Response:
        return error_response(
            status_code=error.status_code,
            code=error.code,
            request_id=request_id_for(request),
            retry_after_seconds=error.retry_after_seconds,
            extra_headers=error.headers,
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
        """Confirm that all mandatory adapters are reachable and no version has retired."""
        report = readiness_report(settings, today=_utc_now().date())
        for warning in report.warnings:
            _logger.warning("provider.deprecation", reason=warning)
        if not report.ready:
            for failure in report.failures:
                _logger.error("provider.retired", reason=failure)
            raise ApiError(
                status_code=503,
                code="SERVICE_UNAVAILABLE",
                message="A required service is unavailable.",
            )
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
    app.include_router(account_routes.router)
    app.include_router(workspace_routes.router)
    app.include_router(workspace_membership_routes.router)
    app.include_router(project_routes.router)
    app.include_router(upload_routes.router)
    app.include_router(job_routes.router)
    app.include_router(youtube_import_routes.router)
    app.include_router(analysis_routes.router)
    app.include_router(candidate_routes.router)
    app.include_router(broll_routes.router)
    app.include_router(playback_routes.router)
    app.include_router(publication_routes.router)
    app.include_router(asset_routes.router)
    app.include_router(source_connection_routes.router)
    app.include_router(social_account_routes.router)
    app.include_router(dashboard_routes.router)
    app.include_router(edit_routes.router)
    app.include_router(edit_review_routes.router)
    app.include_router(render_routes.router)
    app.include_router(generation_webhook_routes.router)
    app.include_router(instagram_webhook_routes.router)
    app.include_router(tiktok_webhook_routes.router)
    app.include_router(variant_routes.router)
    app.include_router(claim_evidence_routes.router)
    app.include_router(brand_kit_routes.router)
    app.include_router(template_routes.router)
    app.include_router(campaign_routes.router)
    app.include_router(search_routes.router)
    return app


def _record_request(request: Request, *, status_code: int, started: float) -> None:
    """Record one request as a duration and one access line, named only by its route."""
    duration_ms = (perf_counter() - started) * 1000
    route = getattr(request.scope.get("route"), "path", "unmatched")
    observe(
        "clipah.http.duration",
        duration_ms,
        method=request.method,
        route=route,
        statusCode=str(status_code),
    )
    _logger.info(
        "http.request",
        method=request.method,
        route=route,
        statusCode=status_code,
        durationMs=round(duration_ms, 3),
    )


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
    return ObservedObjectStore(
        S3ObjectStore(
            bucket=settings.object_store_bucket,
            endpoint_url=settings.object_store_endpoint,
            access_key_id=settings.object_store_access_key_id.get_secret_value(),
            secret_access_key=settings.object_store_secret_access_key.get_secret_value(),
        )
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


def _configured_social_secret_store(settings: Settings) -> SocialSecretStore | None:
    """Build local envelope encryption only when this process has wrapping material."""
    if settings.secret_encryption_key is None:
        return None
    return local_social_secret_store(settings.secret_encryption_key.get_secret_value())


async def _run_probe(probe: ReadinessProbe) -> None:
    """Run one readiness probe with its own bounded timeout."""
    await asyncio.wait_for(probe(), timeout=READINESS_TIMEOUT_SECONDS)


def health_response() -> dict[str, str]:
    """Return the common versioned health resource representation."""
    return {"status": "ok", "version": VERSION}


def _utc_now() -> datetime:
    """Return an aware UTC instant for webhook replay validation."""
    return datetime.now(tz=UTC)
