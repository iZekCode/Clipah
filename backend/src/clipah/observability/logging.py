"""Structured logging whose vocabulary is fixed and whose values are scrubbed.

Two decisions carry this module. A log event may only use field names that were declared
here, so a careless caller cannot invent a field that carries a transcript or a token; and
every value that survives that check is passed through a scrubber, so the text a provider
library handed us cannot smuggle a credential through a field that was legitimate.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Iterator, Mapping, MutableMapping, Sequence
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import structlog
from structlog.contextvars import bind_contextvars, merge_contextvars, reset_contextvars
from structlog.typing import EventDict, WrappedLogger

REDACTED = "[redacted]"

# The only field names a log event may carry. Anything else is dropped and counted, which
# is what makes "no transcript ever reaches a log" a property rather than a hope.
LOG_FIELDS: frozenset[str] = frozenset(
    {
        # Correlation.
        "requestId",
        "traceId",
        "jobId",
        "workspaceId",
        "projectId",
        "userId",
        "publicationId",
        "batchId",
        "socialAccountId",
        "sourceConnectionId",
        "editId",
        "revisionId",
        "renderId",
        "assetId",
        "candidateId",
        "campaignId",
        "providerEventId",
        "idempotencyKey",
        # What happened.
        "event",
        "level",
        "logger",
        "timestamp",
        "code",
        "outcome",
        "reason",
        "retryable",
        "attempt",
        "stage",
        "jobKind",
        "queue",
        "status",
        "statusCode",
        "method",
        "route",
        "provider",
        "operation",
        "model",
        "apiVersion",
        "quotaResource",
        "decision",
        "environment",
        "capabilityVersion",
        "configVersion",
        # How much and how long.
        "durationMs",
        "latencyMs",
        "ageSeconds",
        "bytes",
        "count",
        "inputUnits",
        "outputUnits",
        "estimatedCostUsd",
        "speedRatio",
        # Bookkeeping for the allowlist itself.
        "droppedFields",
    }
)

# Fields this application composes itself, which therefore need no scrubbing and would be
# damaged by it: a route template is path-shaped on purpose.
_UNSCRUBBED_FIELDS: frozenset[str] = frozenset({"route", "event", "logger", "level", "timestamp"})

_AUTHORIZATION = re.compile(r"(?i)\b(?:bearer|basic|token)\s+\S+")
_URL = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://\S+")
_ASSIGNED_SECRET = re.compile(r"[\w\-]{3,}=[A-Za-z0-9+/=_.\-]{16,}")
_FILESYSTEM_PATH = re.compile(r"(?:/[\w.\-]+){2,}/?")
_OPAQUE_RUN = re.compile(r"[A-Za-z0-9+/=_.]{24,}")


def scrub_text(value: str) -> str:
    """Remove anything credential-shaped from one string without emptying the message.

    The operational half of a message — a stable code, a UUID, a provider request
    identifier — is what makes the record worth keeping, so the patterns below are chosen
    to leave those intact while removing authorization headers, URLs, assignments, local
    paths, and high-entropy runs.
    """
    scrubbed = _AUTHORIZATION.sub(REDACTED, value)
    scrubbed = _URL.sub(REDACTED, scrubbed)
    scrubbed = _ASSIGNED_SECRET.sub(REDACTED, scrubbed)
    scrubbed = _FILESYSTEM_PATH.sub(REDACTED, scrubbed)
    return _OPAQUE_RUN.sub(_redact_opaque, scrubbed)


def _redact_opaque(match: re.Match[str]) -> str:
    """Redact a long run only when it looks generated rather than written.

    A stable error code is long, upper-case, and has no digits; a token mixes cases and
    digits because it was drawn from a random alphabet. Keeping the first apart from the
    second is the difference between a readable log and a redacted one.
    """
    run = match.group()
    if not (any(c.islower() for c in run) and any(c.isupper() for c in run)):
        return run
    if not any(c.isdigit() for c in run):
        return run
    return REDACTED


def scrub(value: object) -> object:
    """Apply the text scrubber through mappings and sequences to whatever they hold."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, Mapping):
        return {key: scrub(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(item) for item in value]
    return value


def _normalize(value: object) -> object:
    """Render identifiers as the strings every telemetry backend actually stores."""
    if isinstance(value, UUID):
        return str(value)
    return value


def _validated(fields: Mapping[str, object]) -> dict[str, object]:
    """Reject a field name the allowlist does not declare, at the call site."""
    unknown = sorted(set(fields) - LOG_FIELDS)
    if unknown:
        raise ValueError(f"undeclared log fields: {', '.join(unknown)}")
    return {key: _normalize(value) for key, value in fields.items()}


@contextmanager
def log_context(**fields: object) -> Iterator[None]:
    """Bind correlation identifiers for the duration of one request, job, or stage.

    Binding is scoped rather than global so that one job's identifiers can never appear on
    another job's line, which is the failure mode that makes correlated logs misleading
    instead of merely incomplete.
    """
    tokens = bind_contextvars(**_validated(fields))
    try:
        yield
    finally:
        reset_contextvars(**tokens)


def get_logger(name: str) -> Any:
    """Return the bound logger for one module, configured or not."""
    return structlog.get_logger(name)


def _enforce_allowlist(
    _logger: WrappedLogger, _name: str, event_dict: EventDict
) -> MutableMapping[str, Any]:
    """Drop every undeclared field and record how many were dropped."""
    allowed: dict[str, Any] = {}
    dropped = 0
    for key, value in event_dict.items():
        if key in LOG_FIELDS:
            allowed[key] = _normalize(value)
        else:
            dropped += 1
    if dropped:
        allowed["droppedFields"] = dropped
    return allowed


def _scrub_values(
    _logger: WrappedLogger, _name: str, event_dict: EventDict
) -> MutableMapping[str, Any]:
    """Scrub every value the application did not compose itself."""
    return {
        key: value if key in _UNSCRUBBED_FIELDS else scrub(value)
        for key, value in event_dict.items()
    }


_CAPTURED: list[dict[str, Any]] | None = None


def _capture(_logger: WrappedLogger, _name: str, event_dict: EventDict) -> MutableMapping[str, Any]:
    """Divert events into a test's list instead of the process's output stream."""
    if _CAPTURED is not None:
        _CAPTURED.append(dict(event_dict))
        raise structlog.DropEvent
    return event_dict


def _processors() -> Sequence[Any]:
    """Order the pipeline so that nothing can be logged before it has been checked."""
    return (
        merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _enforce_allowlist,
        _scrub_values,
        _capture,
        structlog.processors.JSONRenderer(sort_keys=True),
    )


def configure_logging(level: str = "INFO") -> None:
    """Install the one logging configuration every process in this system uses."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=list(_processors()),
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )


@contextmanager
def capture_logs() -> Iterator[list[dict[str, Any]]]:
    """Collect the events a block of code emits, after every processor has run.

    Capturing at the end of the pipeline rather than at the call site is deliberate: a
    test asserting that a secret never appears must see exactly what would have been
    written, not what was passed in.
    """
    global _CAPTURED
    configure_logging(level="DEBUG")
    captured: list[dict[str, Any]] = []
    _CAPTURED = captured
    try:
        yield captured
    finally:
        _CAPTURED = None
