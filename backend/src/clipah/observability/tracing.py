"""Spans and error reports, held to the same disclosure rules as logs.

A trace and a Sentry event both leave this process for a system that retains them, so both
are scrubbed here rather than at whatever boundary happens to notice. A failing span keeps
the stable code an alert can group by and discards the text that would have leaked.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, cast

from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Status, StatusCode

from clipah.config import Settings
from clipah.observability.logging import scrub, scrub_text

TRACER_NAME = "clipah"


def configure_tracing(settings: Settings) -> None:
    """Install the tracer provider, exporting only when a collector was configured.

    A deployment without a collector still creates spans, because the alternative is code
    whose instrumented path is never exercised outside production.
    """
    provider = TracerProvider()
    endpoint = settings.otel_exporter_endpoint
    if endpoint is not None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=str(endpoint))))
    _install(provider)


_PROVIDER: TracerProvider | None = None


def _install(provider: TracerProvider | None) -> TracerProvider | None:
    """Make one provider the current one and hand back the one it replaced."""
    global _PROVIDER
    previous = _PROVIDER
    _PROVIDER = provider
    return previous


def _tracer() -> trace.Tracer:
    """Return the tracer of whichever provider this process installed."""
    if _PROVIDER is None:
        return trace.get_tracer(TRACER_NAME)
    return _PROVIDER.get_tracer(TRACER_NAME)


@contextmanager
def span(name: str, **attributes: object) -> Iterator[trace.Span]:
    """Record one unit of work, its scrubbed attributes, and how it ended."""
    tracer = _tracer()
    with tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            current.set_attribute(key, _attribute(value))
        try:
            yield current
        except Exception as error:
            current.set_status(Status(StatusCode.ERROR, scrub_text(type(error).__name__)))
            current.set_attribute("errorType", type(error).__name__)
            raise
        else:
            current.set_status(Status(StatusCode.OK))


def _attribute(value: object) -> Any:
    """Coerce one attribute to a scrubbed scalar OpenTelemetry will accept."""
    if isinstance(value, (bool, int, float)):
        return value
    return scrub_text(str(value))


@dataclass(frozen=True, slots=True)
class SmokeResult:
    """What a local trace smoke test observed, in terms a script can assert on."""

    span_names: tuple[str, ...]
    trace_ids_shared: bool


def trace_smoke() -> SmokeResult:
    """Produce a nested trace in memory and report what was actually recorded.

    A deployment whose tracing is misconfigured looks identical to one with no incidents,
    so this is run at startup verification rather than trusted to be working.
    """
    with (
        capture_spans() as spans,
        span("clipah.smoke.parent", environment="smoke"),
        span("clipah.smoke.child", environment="smoke"),
    ):
        pass
    names = tuple(recorded.name for recorded in spans)
    trace_ids = {recorded.context.trace_id for recorded in spans if recorded.context is not None}
    return SmokeResult(span_names=names, trace_ids_shared=len(trace_ids) == 1)


@contextmanager
def capture_spans() -> Iterator[list[ReadableSpan]]:
    """Collect the spans a block of code produces, exported nowhere else."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    previous = _install(provider)
    recorded: list[ReadableSpan] = []
    try:
        yield recorded
    finally:
        provider.force_flush()
        recorded.extend(exporter.get_finished_spans())
        _install(previous)


def configure_error_reporting(settings: Settings) -> None:
    """Initialise Sentry only where one was configured, and never with personal data."""
    dsn = settings.sentry_dsn
    if dsn is None:
        return
    import sentry_sdk
    from sentry_sdk._types import Event

    def before_send(event: Event, hint: dict[str, Any]) -> Event | None:
        """Adapt the scrubber to the typed hook Sentry expects."""
        return cast(Event, sentry_before_send(cast(dict[str, Any], event), hint))

    sentry_sdk.init(
        dsn=dsn.get_secret_value(),
        environment=settings.environment.value,
        send_default_pii=False,
        max_request_body_size="never",
        before_send=before_send,
    )


def sentry_before_send(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any] | None:
    """Scrub an error report before it leaves the process.

    Headers and cookies are removed outright rather than scrubbed, because no header this
    application sends is worth the risk of retaining one that carries a session.
    """
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("headers", None)
        request.pop("cookies", None)
        request.pop("data", None)
    scrubbed = scrub(event)
    assert isinstance(scrubbed, dict)
    return scrubbed
