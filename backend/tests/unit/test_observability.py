"""What the telemetry of this system is allowed to say, and what it may never say.

Logs, traces, and error reports are read by people who are not looking at the code, and
they are stored for far longer than any request lives. So the questions these tests ask
are the two that matter operationally: can an incident be followed from an HTTP request
through a job to one Publication, and is it impossible for a credential, a signed URL, a
transcript, or a local filesystem path to arrive in that record along the way.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from io import BytesIO
from typing import Any
from uuid import UUID, uuid4

import pytest

from clipah.config import Environment, Settings
from clipah.observability.logging import (
    LOG_FIELDS,
    REDACTED,
    capture_logs,
    get_logger,
    log_context,
    scrub,
    scrub_text,
)
from clipah.observability.metrics import (
    INSTRUMENTS,
    capture_metrics,
    count,
    observe,
)
from clipah.observability.tracing import (
    capture_spans,
    sentry_before_send,
    span,
    trace_smoke,
)
from clipah.observability.usage import (
    PROVIDER_RELEASES,
    ProviderCallRecord,
    generation_cost_per_exported_minute,
    readiness_report,
    record_provider_usage,
)

pytestmark = pytest.mark.unit


REQUEST = "req-0123456789abcdef"
WORKSPACE = UUID("11111111-1111-4111-8111-111111111111")
PROJECT = UUID("22222222-2222-4222-8222-222222222222")
JOB = UUID("33333333-3333-4333-8333-333333333333")
PUBLICATION = UUID("44444444-4444-4444-8444-444444444444")

SECRETS = (
    "ya29.a0AfH6SMBx7QwErTyUiOpAsDfGhJkLzXcVbNm1234567890",
    "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
    "4/0AeanS0bQZ3xKpLmNoPqRsTuVwXyZ0123456789abcdefghij",
    "https://rupload.facebook.com/video-upload/v22.0/1234?ig_pull=eyJhbGciOi",
    "/var/folders/9k/tmpjob8fj2/clipah-job/source.mp4",
    "__Host-clipah_session=Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MGFiY2RlZmdoaWprbG0=",
)


@dataclass
class _StubSession:
    """Stand in for a Session so usage recording can be proved without Postgres."""

    added: list[Any]

    def add(self, instance: Any) -> None:
        """Collect what the recorder wanted to persist."""
        self.added.append(instance)


def _settings(**overrides: Any) -> Settings:
    """Build local settings, which is the only profile a unit test may construct."""
    return Settings(environment=Environment.LOCAL, **overrides)


class TestCorrelation:
    """An operator has to be able to follow one piece of work across every process."""

    def test_a_log_event_carries_every_identifier_the_incident_is_followed_by(self) -> None:
        """Correlation is the whole point: a log line nobody can join is not evidence."""
        with (
            capture_logs() as events,
            log_context(
                requestId=REQUEST,
                workspaceId=WORKSPACE,
                projectId=PROJECT,
                jobId=JOB,
                publicationId=PUBLICATION,
            ),
        ):
            get_logger("test").info("publication.dispatched")

        assert len(events) == 1
        event = events[0]
        assert event["event"] == "publication.dispatched"
        assert event["requestId"] == REQUEST
        assert event["workspaceId"] == str(WORKSPACE)
        assert event["projectId"] == str(PROJECT)
        assert event["jobId"] == str(JOB)
        assert event["publicationId"] == str(PUBLICATION)

    def test_context_does_not_leak_out_of_the_work_it_belongs_to(self) -> None:
        """One job's identifiers appearing on another's line would misdirect an incident."""
        with capture_logs() as events:
            with log_context(jobId=JOB):
                get_logger("test").info("job.started")
            get_logger("test").info("worker.idle")

        assert events[0]["jobId"] == str(JOB)
        assert "jobId" not in events[1]

    def test_nested_context_keeps_the_outer_identifiers(self) -> None:
        """A stage inside a job still belongs to the request that asked for the job."""
        with capture_logs() as events, log_context(requestId=REQUEST), log_context(jobId=JOB):
            get_logger("test").info("stage.started")

        assert events[0]["requestId"] == REQUEST
        assert events[0]["jobId"] == str(JOB)


class TestRedaction:
    """Everything a log line is not explicitly allowed to say, it does not say."""

    @pytest.mark.parametrize("secret", SECRETS)
    def test_a_secret_never_survives_a_log_event(self, secret: str) -> None:
        """One leaked credential in a retained log outlives every rotation policy."""
        with capture_logs() as events:
            get_logger("test").warning("provider.failed", reason=secret)

        rendered = repr(events[0])
        assert secret not in rendered
        assert REDACTED in rendered

    def test_a_field_nobody_declared_is_dropped_rather_than_guessed_at(self) -> None:
        """An allowlist fails closed; a denylist fails the first time somebody is creative."""
        with capture_logs() as events:
            get_logger("test").info(
                "analysis.finished",
                transcriptText="he said the quiet part out loud",
                accessToken="ya29.notatokenreally",
                jobId=JOB,
            )

        event = events[0]
        assert "transcriptText" not in event
        assert "accessToken" not in event
        assert event["jobId"] == str(JOB)
        assert event["droppedFields"] == 2

    def test_the_declared_fields_are_the_ones_instrumentation_actually_needs(self) -> None:
        """The allowlist is the contract; these are the joins an operator cannot work without."""
        assert {
            "requestId",
            "jobId",
            "workspaceId",
            "projectId",
            "publicationId",
            "provider",
            "operation",
            "code",
            "durationMs",
        } <= LOG_FIELDS
        assert "accessToken" not in LOG_FIELDS
        assert "checkpoint" not in LOG_FIELDS

    def test_an_exception_message_is_scrubbed_before_it_is_recorded(self) -> None:
        """Provider libraries put the whole failing request, headers and all, in one string."""
        with capture_logs() as events:
            try:
                raise RuntimeError(f"upload rejected for {SECRETS[3]}")
            except RuntimeError as error:
                get_logger("test").error("provider.failed", reason=str(error))

        assert "rupload.facebook.com" not in repr(events[0])

    def test_a_route_template_survives_because_it_names_no_resource(self) -> None:
        """Scrubbing that eats the route makes every access log useless."""
        with capture_logs() as events:
            get_logger("test").info(
                "http.request", route="/api/v1/workspaces/{workspace_id}/publications", method="GET"
            )

        assert events[0]["route"] == "/api/v1/workspaces/{workspace_id}/publications"

    def test_scrubbing_reaches_inside_nested_structures(self) -> None:
        """A secret one level down in a mapping is exactly as leaked as one at the top."""
        scrubbed = scrub({"outer": [{"inner": SECRETS[0]}]})

        assert SECRETS[0] not in repr(scrubbed)

    def test_scrubbing_leaves_ordinary_operational_text_alone(self) -> None:
        """Redaction that eats the message defeats the reason the message was written."""
        assert scrub_text("render failed: RENDER_ENCODER_UNAVAILABLE") == (
            "render failed: RENDER_ENCODER_UNAVAILABLE"
        )
        assert scrub_text(str(JOB)) == str(JOB)


class TestErrorReporting:
    """Sentry keeps what it is sent for months, so it is scrubbed on the way out."""

    def test_an_error_report_is_scrubbed_before_it_leaves_the_process(self) -> None:
        """The exception value is where provider SDKs hide the authorization header."""
        event = {
            "exception": {
                "values": [{"type": "HTTPError", "value": f"401 for {SECRETS[1]}"}],
            },
            "extra": {"grant": "encrypted:AAAAB3NzaC1yc2EAAAADAQABAAABg"},
            "request": {"headers": {"Authorization": SECRETS[1]}, "cookies": "session=abc"},
        }

        sent = sentry_before_send(event, {})

        assert sent is not None
        rendered = repr(sent)
        assert SECRETS[1] not in rendered
        assert "encrypted:AAAAB3NzaC1yc2EAAAADAQABAAABg" not in rendered
        assert sent["exception"]["values"][0]["type"] == "HTTPError"

    def test_request_headers_and_cookies_are_removed_outright(self) -> None:
        """No header this application sends is worth the risk of retaining one that is."""
        event: dict[str, Any] = {"request": {"headers": {"X-CSRF-Token": "abc"}, "cookies": "x=y"}}

        sent = sentry_before_send(event, {})

        assert sent is not None
        assert "headers" not in sent["request"]
        assert "cookies" not in sent["request"]


class TestTracing:
    """A span is a log line with a duration, and it is governed by the same rules."""

    def test_a_span_records_the_work_and_its_declared_attributes(self) -> None:
        """Without spans, the only latency anybody can see is the total."""
        with capture_spans() as spans, span("job.stage", jobKind="render", workspaceId=WORKSPACE):
            pass

        assert [recorded.name for recorded in spans] == ["job.stage"]
        assert spans[0].attributes["jobKind"] == "render"
        assert spans[0].attributes["workspaceId"] == str(WORKSPACE)

    def test_a_span_attribute_is_scrubbed_like_a_log_field(self) -> None:
        """Traces are exported to a third party, so they are the worst place to leak."""
        with capture_spans() as spans, span("provider.call", reason=SECRETS[3]):
            pass

        assert SECRETS[3] not in repr(dict(spans[0].attributes))

    def test_a_failing_span_records_its_stable_code_and_not_its_traceback(self) -> None:
        """The code is what an alert groups by; the traceback is what leaks."""
        with capture_spans() as spans, pytest.raises(RuntimeError), span("provider.call"):
            raise RuntimeError(f"boom {SECRETS[0]}")

        recorded = spans[0]
        assert recorded.status.status_code.name == "ERROR"
        assert SECRETS[0] not in repr(recorded.attributes) + repr(recorded.events)

    def test_the_trace_smoke_test_proves_a_nested_trace_is_actually_produced(self) -> None:
        """A deployment with broken tracing looks exactly like one with no incidents."""
        result = trace_smoke()

        assert result.span_names == ("clipah.smoke.child", "clipah.smoke.parent")
        assert result.trace_ids_shared is True


class TestMetrics:
    """Counters and histograms are the only telemetry an alert can be written against."""

    def test_every_metric_the_runbook_alerts_on_is_declared(self) -> None:
        """An alert on a metric nobody emits stays green through the whole incident."""
        required = {
            "clipah.queue.latency",
            "clipah.scheduler.latency",
            "clipah.stage.duration",
            "clipah.stage.outcome",
            "clipah.bytes.uploaded",
            "clipah.bytes.rendered",
            "clipah.provider.units",
            "clipah.provider.cost",
            "clipah.oauth.refresh",
            "clipah.connection.expiry",
            "clipah.webhook.rejected",
            "clipah.publication.time_to_publish",
            "clipah.publication.partial_success",
            "clipah.publication.stuck_age",
            "clipah.provider.quota",
            "clipah.candidates.count",
            "clipah.context.warnings",
            "clipah.broll.decision",
            "clipah.generation.cost_per_exported_minute",
            "clipah.render.speed_ratio",
            "clipah.jobs.active",
            "clipah.build.info",
        }

        assert required <= set(INSTRUMENTS)

    def test_a_counter_records_its_value_against_its_labels(self) -> None:
        """A count without labels cannot answer which provider or Workspace is failing."""
        with capture_metrics() as recorded:
            count("clipah.stage.outcome", jobKind="render", outcome="failed")
            count("clipah.stage.outcome", jobKind="render", outcome="failed")

        assert recorded.total("clipah.stage.outcome", jobKind="render", outcome="failed") == 2

    def test_a_histogram_keeps_every_observation(self) -> None:
        """A duration averaged in the process cannot be re-bucketed by whoever reads it."""
        with capture_metrics() as recorded:
            observe("clipah.stage.duration", 1200.0, jobKind="render", outcome="succeeded")
            observe("clipah.stage.duration", 800.0, jobKind="render", outcome="succeeded")

        assert recorded.observations("clipah.stage.duration") == [1200.0, 800.0]

    def test_an_undeclared_metric_is_refused_at_the_call_site(self) -> None:
        """A typo that silently invents a metric is a dashboard that is quietly always empty."""
        with capture_metrics(), pytest.raises(KeyError):
            count("clipah.stage.outcomes", jobKind="render", outcome="failed")

    def test_an_undeclared_label_is_refused_so_cardinality_stays_bounded(self) -> None:
        """One unbounded label is how a metrics bill and a metrics backend both fall over."""
        with capture_metrics(), pytest.raises(ValueError, match="label"):
            count("clipah.stage.outcome", jobKind="render", outcome="failed", filename="a.mp4")

    def test_a_label_value_is_scrubbed_like_every_other_telemetry_value(self) -> None:
        """Labels are copied into every backend that stores a metric."""
        with capture_metrics() as recorded:
            count("clipah.webhook.rejected", provider="tiktok", reason=SECRETS[3])

        assert SECRETS[3] not in repr(recorded.samples)


class TestProviderUsage:
    """Every provider request is charged to the Workspace that caused it."""

    def test_one_provider_call_becomes_one_workspace_attributed_record(self) -> None:
        """Cost that is not attributed cannot be limited, billed, or explained."""
        session = _StubSession(added=[])
        call = ProviderCallRecord(
            provider="groq",
            operation="extract",
            model_or_api_version="openai/gpt-oss-20b",
            request_id="groq-req-1",
            input_units=1200,
            output_units=340,
            estimated_cost_usd=0.0042,
        )

        record_provider_usage(
            session, workspace_id=WORKSPACE, job_id=JOB, call=call, publication_id=None
        )

        assert len(session.added) == 1
        row = session.added[0]
        assert row.workspace_id == WORKSPACE
        assert row.job_id == JOB
        assert row.provider == "groq"
        assert row.input_units == 1200
        assert float(row.estimated_cost_usd) == pytest.approx(0.0042)

    def test_recording_usage_also_emits_the_units_and_cost_metrics(self) -> None:
        """The ledger answers the invoice; the metric answers the alert, and both are needed."""
        session = _StubSession(added=[])
        call = ProviderCallRecord(
            provider="fal",
            operation="generate_video",
            model_or_api_version="fal-ai/kling-video/v2.6/pro/text-to-video",
            request_id="fal-req-1",
            input_units=1,
            output_units=8,
            estimated_cost_usd=1.25,
        )

        with capture_metrics() as recorded:
            record_provider_usage(
                session, workspace_id=WORKSPACE, job_id=JOB, call=call, publication_id=None
            )

        assert recorded.total("clipah.provider.units", provider="fal", operation="generate_video")
        assert recorded.observations("clipah.provider.cost") == [1.25]

    def test_a_provider_request_identifier_is_not_treated_as_a_secret(self) -> None:
        """Support cases are opened with the provider's own request identifier."""
        assert scrub_text("groq-req-1") == "groq-req-1"

    def test_generation_cost_is_expressed_per_exported_minute(self) -> None:
        """Cost per job is meaningless across projects; cost per exported minute compares."""
        assert generation_cost_per_exported_minute(
            cost_usd=3.0, exported_seconds=90.0
        ) == pytest.approx(2.0)

    def test_generation_cost_of_an_export_that_produced_nothing_is_not_infinite(self) -> None:
        """A failed render must not publish a division by zero into a dashboard."""
        assert generation_cost_per_exported_minute(cost_usd=3.0, exported_seconds=0.0) is None


class TestProviderReadiness:
    """A model that is switched off on a Monday is an outage nobody scheduled."""

    def test_an_announced_shutdown_warns_well_before_it_happens(self) -> None:
        """The point of the monitor is to make the migration a planned piece of work."""
        report = readiness_report(
            _settings(provider_shutdowns=("model:openai/gpt-oss-20b=2027-01-15",)),
            today=date(2026, 9, 10),
        )

        assert report.ready is True
        assert any("2027-01-15" in warning for warning in report.warnings)

    def test_a_model_that_is_already_retired_fails_readiness(self) -> None:
        """Once the date has passed, the deployment is broken whether or not anybody looks."""
        report = readiness_report(
            _settings(provider_shutdowns=("api:v22.0=2026-01-01",)), today=date(2026, 9, 10)
        )

        assert report.ready is False
        assert any("v22.0" in failure for failure in report.failures)

    def test_a_shutdown_of_something_this_deployment_does_not_call_is_ignored(self) -> None:
        """Alerting about a model nobody configured is how alerts stop being read."""
        report = readiness_report(
            _settings(provider_shutdowns=("model:some-other-vendor/model=2026-01-01",)),
            today=date(2026, 9, 10),
        )

        assert report.ready is True
        assert report.warnings == ()

    def test_a_shutdown_further_out_than_the_horizon_stays_quiet(self) -> None:
        """A warning two years early is noise by the time it matters."""
        report = readiness_report(
            _settings(provider_shutdowns=("api:v3=2030-01-01",)), today=date(2026, 9, 10)
        )

        assert report.ready is True
        assert report.warnings == ()

    def test_a_malformed_announced_shutdown_is_refused_at_startup(self) -> None:
        """Configuration nobody can parse is configuration nobody is protected by."""
        with pytest.raises(ValueError, match="PROVIDER_SHUTDOWNS"):
            _settings(provider_shutdowns=("model:openai/gpt-oss-20b",))
        with pytest.raises(ValueError, match="PROVIDER_SHUTDOWNS"):
            _settings(provider_shutdowns=("model:openai/gpt-oss-20b=not-a-date",))

    def test_a_configuration_with_no_announced_shutdown_is_ready_and_quiet(self) -> None:
        """Warning about everything trains everybody to ignore the warnings."""
        report = readiness_report(_settings(), today=date(2026, 1, 1))

        assert report.ready is True
        assert report.warnings == ()

    def test_the_inventory_covers_the_provider_versions_this_deployment_calls(self) -> None:
        """A monitor that does not know about a provider cannot warn about it."""
        providers = {release.provider for release in PROVIDER_RELEASES}

        assert {"youtube", "instagram", "tiktok", "groq", "fal"} <= providers

    def test_every_announced_release_carries_a_date_a_human_can_act_on(self) -> None:
        """A deprecation without a date cannot be planned around."""
        for release in PROVIDER_RELEASES:
            assert release.shutdown_on is None or isinstance(release.shutdown_on, date)
            assert release.identifier


class TestConfiguration:
    """Telemetry must be safe to switch on in production and safe to leave off locally."""

    def test_a_deployment_without_a_collector_still_logs_and_still_runs(self) -> None:
        """Local development has no collector, and no part of it may depend on one."""
        settings = _settings()

        with capture_logs() as events:
            get_logger("test").info("http.request", method="GET", statusCode=200)

        assert settings.otel_exporter_endpoint is None
        assert events[0]["statusCode"] == 200

    def test_build_information_is_reported_once_as_a_metric(self) -> None:
        """Which model, config, and capability version produced a number is part of the number."""
        from clipah.observability.metrics import record_build_info

        with capture_metrics() as recorded:
            record_build_info(_settings())

        samples = [sample for sample in recorded.samples if sample.name == "clipah.build.info"]
        assert samples
        assert samples[0].labels["environment"] == "local"


class TestInstrumentedStorage:
    """Storage failures are slow rather than loud, so they are measured rather than noticed."""

    def test_every_storage_operation_is_timed_without_naming_the_object(self) -> None:
        """The object key is the one part of a storage call that identifies a member's media."""
        from clipah.assets.storage import FakeObjectStore, ObservedObjectStore

        store = ObservedObjectStore(FakeObjectStore(now=lambda: _fixed_now()))

        with capture_metrics() as recorded:
            upload = store.create_multipart_upload(key="w/p/source.mp4", content_type="video/mp4")
            store.sign_upload_part(upload_id=upload.upload_id, key="w/p/source.mp4", part_number=1)
            store.abort_multipart_upload(upload_id=upload.upload_id, key="w/p/source.mp4")

        operations = {sample.labels["operation"] for sample in recorded.samples}
        assert operations == {
            "create_multipart_upload",
            "sign_upload_part",
            "abort_multipart_upload",
        }
        assert "source.mp4" not in repr(recorded.samples)

    def test_a_stored_upload_is_counted_in_bytes(self) -> None:
        """Uploaded bytes are what a storage bill and a capacity plan are both made of."""
        from clipah.assets.storage import FakeObjectStore, ObservedObjectStore

        store = ObservedObjectStore(FakeObjectStore(now=lambda: _fixed_now()))

        with capture_metrics() as recorded:
            store.put_file(key="w/p/a.mp4", content_type="video/mp4", file=BytesIO(b"0123456789"))

        assert recorded.total("clipah.bytes.uploaded", operation="put_file") == 10

    def test_a_failing_operation_is_recorded_as_failed_and_still_raises(self) -> None:
        """A swallowed storage error is worse than a slow one, because nothing reports it."""
        from clipah.assets.storage import FakeObjectStore, ObservedObjectStore

        store = ObservedObjectStore(FakeObjectStore(now=lambda: _fixed_now()))

        with capture_metrics() as recorded, pytest.raises(KeyError):
            store.head_object(key="w/p/missing.mp4")

        assert recorded.samples[0].labels["outcome"] == "failed"


class TestProcessConfiguration:
    """Every process installs the same telemetry, whether or not it can export any."""

    def test_a_process_without_a_collector_configures_without_raising(self) -> None:
        """Local development and the test suite both run with nothing to export to."""
        from clipah.observability import configure_observability

        configure_observability(_settings())

        assert INSTRUMENTS

    def test_instrument_names_are_reported_for_documentation(self) -> None:
        """A runbook that lists metrics by hand goes stale on the first new one."""
        from clipah.observability.metrics import instrument_names

        assert "clipah.build.info" in instrument_names()

    def test_a_declared_shutdown_replaces_the_one_known_in_code(self) -> None:
        """When a provider announces a date, the operator's date is the one that counts."""
        from clipah.observability.usage import announced_shutdowns

        combined = announced_shutdowns(_settings(provider_shutdowns=("api:v3=2028-05-01",)))

        youtube = next(r for r in combined if r.kind == "api" and r.identifier == "v3")
        assert youtube.shutdown_on == date(2028, 5, 1)

    def test_a_declared_shutdown_for_something_unknown_is_still_carried(self) -> None:
        """A provider this deployment adds later must not need a code change to be watched."""
        from clipah.observability.usage import announced_shutdowns

        declared = ("model:new/model=2028-05-01",)
        combined = announced_shutdowns(_settings(provider_shutdowns=declared))

        assert any(release.identifier == "new/model" for release in combined)

    def test_generation_cost_is_published_only_when_there_is_one(self) -> None:
        """A failed generation has no cost per minute, and must not invent one."""
        from clipah.observability.usage import record_generation_cost

        with capture_metrics() as recorded:
            record_generation_cost(provider="fal", cost_usd=6.0, exported_seconds=120.0)
            record_generation_cost(provider="fal", cost_usd=6.0, exported_seconds=0.0)

        assert recorded.observations("clipah.generation.cost_per_exported_minute") == [3.0]


def _fixed_now() -> datetime:
    """Return one pinned instant, because storage adapters must never read a clock."""
    return datetime(2026, 9, 10, tzinfo=UTC)


def test_a_random_identifier_is_never_mistaken_for_a_secret() -> None:
    """Every job in this system is named by a UUID, and every one of them must log."""
    identifier = str(uuid4())

    assert scrub_text(identifier) == identifier
