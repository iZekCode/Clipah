#!/bin/sh
# Prove this deployment's telemetry actually works before it is relied on in an incident.
#
#   scripts/check-observability.sh
#
# Runs three checks against the configuration this shell has:
#
#   1. a local trace smoke test, which creates a nested trace and reads it back, because a
#      deployment whose tracing is misconfigured looks exactly like one with no incidents;
#   2. the provider readiness and deprecation monitor, which warns before an announced
#      shutdown and fails only once one has already passed;
#   3. one scrubbed log line, so the redaction pipeline is exercised rather than assumed.
#
# It reads only the environment, starts no server, and contacts no collector.
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_root/backend"

exec uv run python -c '
import sys
from datetime import date

from clipah.config import Settings
from clipah.observability.logging import capture_logs, configure_logging, get_logger
from clipah.observability.tracing import trace_smoke
from clipah.observability.usage import readiness_report

settings = Settings()
configure_logging(level=settings.log_level.upper())

smoke = trace_smoke()
if smoke.span_names != ("clipah.smoke.child", "clipah.smoke.parent") or not smoke.trace_ids_shared:
    print("trace smoke test failed: nested spans were not recorded", file=sys.stderr)
    raise SystemExit(1)
print("trace smoke test passed:", ", ".join(smoke.span_names))

report = readiness_report(settings, today=date.today())
for warning in report.warnings:
    print("deprecation warning:", warning)
for failure in report.failures:
    print("retired provider version:", failure, file=sys.stderr)
if not report.ready:
    raise SystemExit(1)
print("provider readiness passed")

with capture_logs() as events:
    get_logger("smoke").info("observability.smoke", reason="Bearer aB3xY9zQ1mN7pR2sT5vW8kL0")
rendered = repr(events[0])
if "aB3xY9zQ1mN7pR2sT5vW8kL0" in rendered:
    print("redaction failed: a credential survived a log event", file=sys.stderr)
    raise SystemExit(1)
print("log redaction passed")
'
