"""Telemetry for this system: correlated logs, spans, metrics, and provider usage.

Every module here obeys one rule that the rest of the application depends on: telemetry
records what happened, never the material that made it happen. Credentials, signed URLs,
transcript text, and local filesystem paths are removed on the way out rather than trusted
not to have been passed in.
"""

from __future__ import annotations

from clipah.config import Settings
from clipah.observability.logging import configure_logging
from clipah.observability.metrics import configure_metrics, record_build_info
from clipah.observability.tracing import configure_error_reporting, configure_tracing

__all__ = ["configure_observability"]


def configure_observability(settings: Settings) -> None:
    """Install logging, tracing, metrics, and error reporting for one process.

    Every process calls this once at startup, so a log line from a worker joins a log line
    from the API without either of them having to agree on anything but this function.
    """
    configure_logging(level=settings.log_level.upper())
    configure_tracing(settings)
    configure_metrics(settings)
    configure_error_reporting(settings)
    record_build_info(settings)
