"""The declared metrics of this system, and the only way to emit one.

Every instrument is declared with the labels it accepts. A metric name that was never
declared is refused at the call site, because the failure it otherwise produces is a
dashboard that is quietly always empty and an alert that stays green through an incident.
Labels are closed for the same reason cardinality is: one unbounded label ends a metrics
backend.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum

from opentelemetry import metrics as otel_metrics
from opentelemetry.metrics import Counter, Histogram

from clipah.config import Settings
from clipah.observability.logging import scrub_text

METER_NAME = "clipah"


class InstrumentKind(StrEnum):
    """Whether an instrument accumulates or keeps the shape of its observations."""

    COUNTER = "counter"
    HISTOGRAM = "histogram"


@dataclass(frozen=True, slots=True)
class Instrument:
    """One declared metric: what it measures, in what unit, cut by which labels."""

    name: str
    kind: InstrumentKind
    unit: str
    description: str
    labels: frozenset[str]


def _counter(name: str, unit: str, description: str, *labels: str) -> Instrument:
    """Declare an accumulating instrument."""
    return Instrument(name, InstrumentKind.COUNTER, unit, description, frozenset(labels))


def _histogram(name: str, unit: str, description: str, *labels: str) -> Instrument:
    """Declare a distribution instrument."""
    return Instrument(name, InstrumentKind.HISTOGRAM, unit, description, frozenset(labels))


_DECLARED: tuple[Instrument, ...] = (
    _histogram(
        "clipah.queue.latency",
        "ms",
        "Time between enqueueing a job and a worker starting it.",
        "jobKind",
        "queue",
    ),
    _histogram(
        "clipah.scheduler.latency",
        "ms",
        "Time between a publication becoming due and the scheduler claiming it.",
        "provider",
    ),
    _histogram(
        "clipah.stage.duration",
        "ms",
        "Wall time of one job stage attempt.",
        "jobKind",
        "outcome",
    ),
    _counter(
        "clipah.stage.outcome",
        "1",
        "Job stage attempts by how they ended.",
        "jobKind",
        "outcome",
        "code",
    ),
    _counter("clipah.bytes.uploaded", "By", "Bytes accepted into object storage.", "operation"),
    _counter("clipah.bytes.rendered", "By", "Bytes written by a completed render.", "jobKind"),
    _counter(
        "clipah.provider.units",
        "1",
        "Billable units charged by an external provider.",
        "provider",
        "operation",
        "direction",
    ),
    _histogram(
        "clipah.provider.cost",
        "USD",
        "Estimated cost of one external provider request.",
        "provider",
        "operation",
    ),
    _counter(
        "clipah.provider.request",
        "1",
        "External provider requests by outcome.",
        "provider",
        "operation",
        "outcome",
        "code",
    ),
    _counter(
        "clipah.oauth.refresh",
        "1",
        "OAuth grant refreshes by outcome.",
        "provider",
        "outcome",
    ),
    _histogram(
        "clipah.connection.expiry",
        "s",
        "Seconds remaining before a connection's access expires.",
        "provider",
        "kind",
    ),
    _counter(
        "clipah.webhook.rejected",
        "1",
        "Webhook deliveries refused before persistence.",
        "provider",
        "reason",
    ),
    _counter(
        "clipah.webhook.accepted",
        "1",
        "Webhook deliveries verified and recorded as evidence.",
        "provider",
        "outcome",
    ),
    _histogram(
        "clipah.publication.time_to_publish",
        "s",
        "Seconds between approving a publication and the provider publishing it.",
        "provider",
    ),
    _counter(
        "clipah.publication.partial_success",
        "1",
        "Publication batches where some destinations published and others did not.",
        "provider",
    ),
    _histogram(
        "clipah.publication.stuck_age",
        "s",
        "Age of a publication found stuck in a transfer or processing state.",
        "provider",
        "status",
    ),
    _counter(
        "clipah.publication.outcome",
        "1",
        "Publication destinations by terminal state.",
        "provider",
        "outcome",
        "code",
    ),
    _counter(
        "clipah.provider.quota",
        "1",
        "Provider quota and throttling refusals.",
        "provider",
        "quotaResource",
        "outcome",
    ),
    _histogram(
        "clipah.candidates.count",
        "1",
        "Candidates surviving extraction and reranking for one analysis.",
        "stage",
    ),
    _counter(
        "clipah.context.warnings",
        "1",
        "Context warnings attached to candidates.",
        "reason",
    ),
    _counter(
        "clipah.broll.decision",
        "1",
        "B-roll placement decisions by what was decided.",
        "decision",
        "provider",
    ),
    _histogram(
        "clipah.generation.cost_per_exported_minute",
        "USD",
        "Generated-media cost divided by the exported duration it paid for.",
        "provider",
    ),
    _histogram(
        "clipah.render.speed_ratio",
        "1",
        "Render wall time divided by the duration of the video it produced.",
        "jobKind",
    ),
    _counter(
        "clipah.jobs.active",
        "1",
        "Change in the number of jobs a worker is currently running.",
        "jobKind",
    ),
    _counter(
        "clipah.sse.connections",
        "1",
        "Change in the number of open job-event stream connections.",
        "outcome",
    ),
    _histogram(
        "clipah.http.duration",
        "ms",
        "Wall time of one HTTP request.",
        "method",
        "route",
        "statusCode",
    ),
    _histogram(
        "clipah.db.duration",
        "ms",
        "Wall time of one tenant-scoped database session.",
        "role",
        "outcome",
    ),
    _histogram(
        "clipah.storage.duration",
        "ms",
        "Wall time of one object-store operation.",
        "operation",
        "outcome",
    ),
    _counter(
        "clipah.build.info",
        "1",
        "The model, configuration, and capability versions that produced everything else.",
        "environment",
        "version",
        "extractionModel",
        "rerankingModel",
        "capabilityVersion",
    ),
)

INSTRUMENTS: Mapping[str, Instrument] = {instrument.name: instrument for instrument in _DECLARED}

BUILD_VERSION = "0.1.0"
CAPABILITY_VERSION = "1"


@dataclass(frozen=True, slots=True)
class Sample:
    """One recorded metric, kept as it would have been exported."""

    name: str
    value: float
    labels: Mapping[str, str]


@dataclass
class RecordedMetrics:
    """What a block of code emitted, in the terms a test asks its questions in."""

    samples: list[Sample] = field(default_factory=list)

    def total(self, name: str, **labels: str) -> float:
        """Sum every sample of one instrument that carries the given labels."""
        return sum(
            sample.value
            for sample in self.samples
            if sample.name == name and _matches(sample.labels, labels)
        )

    def observations(self, name: str, **labels: str) -> list[float]:
        """Return every value recorded for one instrument, in order."""
        return [
            sample.value
            for sample in self.samples
            if sample.name == name and _matches(sample.labels, labels)
        ]


def _matches(recorded: Mapping[str, str], wanted: Mapping[str, str]) -> bool:
    """Whether a sample carries every label a query asked for."""
    return all(recorded.get(key) == value for key, value in wanted.items())


_RECORDER: RecordedMetrics | None = None
_COUNTERS: dict[str, Counter] = {}
_HISTOGRAMS: dict[str, Histogram] = {}


def _labels(instrument: Instrument, labels: Mapping[str, object]) -> dict[str, str]:
    """Check one call's labels against the declaration and scrub what survives."""
    unknown = sorted(set(labels) - instrument.labels)
    if unknown:
        raise ValueError(f"undeclared label for {instrument.name}: {', '.join(unknown)}")
    return {key: scrub_text(str(value)) for key, value in labels.items()}


def count(name: str, value: float = 1.0, **labels: object) -> None:
    """Add to a declared counter."""
    instrument = INSTRUMENTS[name]
    attributes = _labels(instrument, labels)
    if _RECORDER is not None:
        _RECORDER.samples.append(Sample(name=name, value=value, labels=attributes))
        return
    _counter_for(instrument).add(value, attributes)


def observe(name: str, value: float, **labels: object) -> None:
    """Record one observation against a declared histogram."""
    instrument = INSTRUMENTS[name]
    attributes = _labels(instrument, labels)
    if _RECORDER is not None:
        _RECORDER.samples.append(Sample(name=name, value=value, labels=attributes))
        return
    _histogram_for(instrument).record(value, attributes)


def _meter() -> otel_metrics.Meter:
    """Return this process's meter, whether or not an exporter was configured."""
    return otel_metrics.get_meter(METER_NAME)


def _counter_for(instrument: Instrument) -> Counter:
    """Create one counter per declaration, once."""
    existing = _COUNTERS.get(instrument.name)
    if existing is None:
        existing = _meter().create_counter(
            instrument.name, unit=instrument.unit, description=instrument.description
        )
        _COUNTERS[instrument.name] = existing
    return existing


def _histogram_for(instrument: Instrument) -> Histogram:
    """Create one histogram per declaration, once."""
    existing = _HISTOGRAMS.get(instrument.name)
    if existing is None:
        existing = _meter().create_histogram(
            instrument.name, unit=instrument.unit, description=instrument.description
        )
        _HISTOGRAMS[instrument.name] = existing
    return existing


def record_publication_outcome(
    *,
    provider: str,
    outcome: str,
    code: str | None = None,
    seconds_to_publish: float | None = None,
) -> None:
    """Record how one destination ended, and how long a published one took.

    Time to publish is measured from the member's approval rather than from dispatch,
    because approval is the moment they started waiting.
    """
    count("clipah.publication.outcome", provider=provider, outcome=outcome, code=code or "none")
    if seconds_to_publish is not None:
        observe(
            "clipah.publication.time_to_publish", max(seconds_to_publish, 0.0), provider=provider
        )


def record_build_info(settings: Settings) -> None:
    """State once, as a metric, which versions produced every other number."""
    count(
        "clipah.build.info",
        environment=settings.environment.value,
        version=BUILD_VERSION,
        extractionModel=settings.groq_extraction_model,
        rerankingModel=settings.groq_reranking_model,
        capabilityVersion=CAPABILITY_VERSION,
    )


@contextmanager
def capture_metrics() -> Iterator[RecordedMetrics]:
    """Collect what a block of code recorded instead of exporting it."""
    global _RECORDER
    recorder = RecordedMetrics()
    previous = _RECORDER
    _RECORDER = recorder
    try:
        yield recorder
    finally:
        _RECORDER = previous


def configure_metrics(settings: Settings) -> None:
    """Install the meter provider, exporting only when a collector was configured."""
    endpoint = settings.otel_exporter_endpoint
    if endpoint is None:
        return
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

    reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=str(endpoint)))
    otel_metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    _COUNTERS.clear()
    _HISTOGRAMS.clear()


def instrument_names() -> tuple[str, ...]:
    """Name every declared instrument, for documentation and readiness checks."""
    return tuple(sorted(INSTRUMENTS))
