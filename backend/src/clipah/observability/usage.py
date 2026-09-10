"""Workspace-attributed provider usage, and the deprecation monitor that guards it.

Two things are recorded here. Every external request this system pays for becomes one row
attributed to the Workspace that caused it, so cost can be limited, billed, and explained
rather than discovered on an invoice. And every external model and API version this
deployment is configured to call is checked against its announced shutdown, so a migration
is a planned piece of work instead of an outage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol
from uuid import UUID

from clipah.config import Settings
from clipah.models import ProviderUsage
from clipah.observability.logging import get_logger, log_context
from clipah.observability.metrics import count, observe

_logger = get_logger(__name__)

# How far ahead of an announced shutdown a deployment starts being told about it.
WARNING_HORIZON = timedelta(days=180)


class UsageSink(Protocol):
    """The one thing usage recording needs from a Session: somewhere to put a row."""

    def add(self, instance: object) -> None:
        """Stage one row for the surrounding transaction."""


@dataclass(frozen=True, slots=True)
class ProviderCallRecord:
    """Everything one paid external request must be remembered by.

    It carries the provider's own request identifier, which is what a support case is
    opened with, and never the request itself: no headers, no URL, no prompt.
    """

    provider: str
    operation: str
    model_or_api_version: str
    request_id: str
    input_units: int = 0
    output_units: int = 0
    estimated_cost_usd: float = 0.0


def record_provider_usage(
    session: UsageSink,
    *,
    workspace_id: UUID,
    job_id: UUID | None,
    call: ProviderCallRecord,
    publication_id: UUID | None = None,
) -> ProviderUsage:
    """Charge one provider request to the Workspace that caused it, and measure it.

    The ledger row answers the invoice and the metrics answer the alert; a deployment
    needs both, because a row cannot be alerted on and a metric cannot be billed.
    """
    row = ProviderUsage(
        workspace_id=workspace_id,
        provider=call.provider,
        operation=call.operation,
        model_or_api_version=call.model_or_api_version,
        request_id=call.request_id,
        input_units=call.input_units,
        output_units=call.output_units,
        estimated_cost_usd=call.estimated_cost_usd,
        job_id=job_id,
    )
    session.add(row)
    count(
        "clipah.provider.units",
        call.input_units,
        provider=call.provider,
        operation=call.operation,
        direction="input",
    )
    count(
        "clipah.provider.units",
        call.output_units,
        provider=call.provider,
        operation=call.operation,
        direction="output",
    )
    observe(
        "clipah.provider.cost",
        call.estimated_cost_usd,
        provider=call.provider,
        operation=call.operation,
    )
    with log_context(workspaceId=workspace_id, jobId=job_id, publicationId=publication_id):
        _logger.info(
            "provider.usage",
            provider=call.provider,
            operation=call.operation,
            model=call.model_or_api_version,
            inputUnits=call.input_units,
            outputUnits=call.output_units,
            estimatedCostUsd=call.estimated_cost_usd,
        )
    return row


def generation_cost_per_exported_minute(
    *, cost_usd: float, exported_seconds: float
) -> float | None:
    """Express generated-media cost against the exported duration it paid for.

    Cost per job cannot be compared across projects, and a render that produced nothing
    has no cost per minute at all rather than an infinite one.
    """
    if exported_seconds <= 0:
        return None
    return cost_usd / (exported_seconds / 60.0)


def record_generation_cost(
    *, provider: str, cost_usd: float, exported_seconds: float
) -> float | None:
    """Publish the cost-per-exported-minute of one generation, when there is one."""
    per_minute = generation_cost_per_exported_minute(
        cost_usd=cost_usd, exported_seconds=exported_seconds
    )
    if per_minute is not None:
        observe("clipah.generation.cost_per_exported_minute", per_minute, provider=provider)
    return per_minute


@dataclass(frozen=True, slots=True)
class ProviderRelease:
    """One external model or API version this deployment may be configured to call."""

    provider: str
    kind: str
    identifier: str
    shutdown_on: date | None
    replacement: str | None = None


# The external releases this deployment knows how to call. Only one carries a date in the
# code, because only one is documented in the plan; every other announced shutdown reaches
# the monitor through ``CLIPAH_PROVIDER_SHUTDOWNS``, which is where an operator records
# what a provider has announced. Inventing dates here would produce confident warnings
# about retirements nobody announced.
PROVIDER_RELEASES: tuple[ProviderRelease, ...] = (
    ProviderRelease(
        provider="openai",
        kind="model",
        identifier="sora",
        shutdown_on=date(2026, 9, 24),
        replacement="a separately approved generative-video provider",
    ),
    ProviderRelease(
        provider="groq", kind="model", identifier="openai/gpt-oss-20b", shutdown_on=None
    ),
    ProviderRelease(
        provider="groq", kind="model", identifier="openai/gpt-oss-120b", shutdown_on=None
    ),
    ProviderRelease(
        provider="fal", kind="model", identifier="fal-ai/nano-banana-2", shutdown_on=None
    ),
    ProviderRelease(
        provider="fal",
        kind="model",
        identifier="fal-ai/kling-video/v2.6/pro/text-to-video",
        shutdown_on=None,
    ),
    ProviderRelease(provider="youtube", kind="api", identifier="v3", shutdown_on=None),
    ProviderRelease(provider="instagram", kind="api", identifier="v22.0", shutdown_on=None),
    ProviderRelease(provider="tiktok", kind="api", identifier="v2", shutdown_on=None),
)


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """What the deprecation monitor found in one deployment's configuration."""

    warnings: tuple[str, ...]
    failures: tuple[str, ...]

    @property
    def ready(self) -> bool:
        """Readiness fails only for a configured version that is already retired."""
        return not self.failures


def configured_releases(settings: Settings) -> tuple[tuple[str, str], ...]:
    """List the (kind, identifier) pairs this deployment is actually configured to call."""
    models = (
        settings.groq_extraction_model,
        settings.groq_reranking_model,
        settings.fal_image_model_id,
        settings.fal_video_model_id,
        settings.runway_video_model_id,
    )
    versions = (
        settings.youtube_api_version,
        settings.instagram_api_version,
        settings.tiktok_api_version,
    )
    return tuple(("model", value) for value in models if value) + tuple(
        ("api", value) for value in versions if value
    )


def announced_shutdowns(settings: Settings) -> tuple[ProviderRelease, ...]:
    """Combine the releases known in code with the dates an operator has recorded."""
    declared: dict[tuple[str, str], date] = {}
    for entry in settings.provider_shutdowns:
        kind, _, remainder = entry.partition(":")
        identifier, _, announced = remainder.rpartition("=")
        declared[(kind, identifier)] = date.fromisoformat(announced)
    known = {(release.kind, release.identifier): release for release in PROVIDER_RELEASES}
    combined = list(PROVIDER_RELEASES)
    for key, announced_on in declared.items():
        release = known.get(key)
        replaced = ProviderRelease(
            provider=release.provider if release else key[0],
            kind=key[0],
            identifier=key[1],
            shutdown_on=announced_on,
            replacement=release.replacement if release else None,
        )
        if release is not None:
            combined[combined.index(release)] = replaced
        else:
            combined.append(replaced)
    return tuple(combined)


def readiness_report(settings: Settings, *, today: date) -> ReadinessReport:
    """Warn before an announced shutdown, and fail only once one has passed.

    Failing early would take a working deployment down for a migration it could still
    schedule; failing late would let it discover the retirement from its own users.
    """
    configured = configured_releases(settings)
    warnings: list[str] = []
    failures: list[str] = []
    for release in announced_shutdowns(settings):
        shutdown_on = release.shutdown_on
        if shutdown_on is None or (release.kind, release.identifier) not in configured:
            continue
        subject = f"{release.provider} {release.kind} {release.identifier}"
        if shutdown_on <= today:
            failures.append(f"{subject} was retired on {shutdown_on.isoformat()}")
        elif shutdown_on - today <= WARNING_HORIZON:
            notice = f"{subject} shuts down on {shutdown_on.isoformat()}"
            replacement = release.replacement
            warnings.append(notice if replacement is None else f"{notice}; move to {replacement}")
    return ReadinessReport(warnings=tuple(warnings), failures=tuple(failures))
