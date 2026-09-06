"""Workspace-scoped use cases for B-roll plan admission and suggestion review.

`plan.md` does not name this module for Task 28, but `AGENTS.md` requires domain rules to
live in a `use_cases.py` and HTTP concerns to stay in `api/routes/`, so the admission rules
live here rather than inside the route that calls them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from clipah.broll.generation import (
    GenerationConfirmation,
    GenerationConfirmationInvalidError,
    GenerationEstimate,
    GenerationMediaKind,
    GenerationProviderRetryableError,
    open_generation_confirmation,
    seal_generation_confirmation,
)
from clipah.broll.generation_policy import (
    GenerationEligibility,
    GenerationProviders,
    GenerationTarget,
    GenerationUnavailableReason,
    generation_eligibility,
    generation_request_for,
    suggestion_generation_refusal,
)
from clipah.broll.models import BrollCoverage
from clipah.broll.repository import BrollRepository, SuggestionSummary
from clipah.config import Settings
from clipah.jobs.admission import AdmissionPolicy
from clipah.jobs.models import JobSnapshot
from clipah.jobs.use_cases import create_job
from clipah.models import BrollSuggestion, JobKind, QuotaResource
from clipah.workspaces.models import WorkspaceAccess


class BrollTargetNotFoundError(Exception):
    """The Project or Clip Candidate is not visible inside this Workspace."""


class BrollPlanConflictError(Exception):
    """The idempotency key is already bound to different planning work."""


class GenerationNotEligibleError(Exception):
    """This suggestion may not be sent to a generative provider in its current state."""


class GenerationVideoConfirmationRequiredError(Exception):
    """A generated video was requested without its explicit second confirmation."""


def start_broll_plan(
    session: Session,
    *,
    policy: AdmissionPolicy,
    access: WorkspaceAccess,
    project_id: UUID,
    candidate_id: UUID,
    coverage: BrollCoverage,
    idempotency_key: str,
    now: datetime,
) -> JobSnapshot:
    """Create one durable BROLL_PLAN Job after proving the clip is reviewable."""
    return _admit(
        session,
        policy=policy,
        access=access,
        project_id=project_id,
        candidate_id=candidate_id,
        coverage=coverage,
        kind=JobKind.BROLL_PLAN,
        idempotency_key=idempotency_key,
        now=now,
    )


def start_broll_retrieval(
    session: Session,
    *,
    policy: AdmissionPolicy,
    access: WorkspaceAccess,
    project_id: UUID,
    candidate_id: UUID,
    coverage: BrollCoverage,
    idempotency_key: str,
    now: datetime,
) -> JobSnapshot:
    """Create one durable BROLL_RETRIEVE Job to give a plan's beats their pictures.

    Planning and retrieval are two Jobs rather than one because they fail differently:
    a plan that cannot reach its language model is worth retrying on its own, and a
    search that finds nothing must not throw away beats a member can still read. Both
    read the clip and coverage they were admitted for from the same request row, so the
    worker never learns what to illustrate from the broker.
    """
    return _admit(
        session,
        policy=policy,
        access=access,
        project_id=project_id,
        candidate_id=candidate_id,
        coverage=coverage,
        kind=JobKind.BROLL_RETRIEVE,
        idempotency_key=idempotency_key,
        now=now,
    )


def _admit(
    session: Session,
    *,
    policy: AdmissionPolicy,
    access: WorkspaceAccess,
    project_id: UUID,
    candidate_id: UUID,
    coverage: BrollCoverage,
    kind: JobKind,
    idempotency_key: str,
    now: datetime,
) -> JobSnapshot:
    """Bind one idempotency key to one Job over one reviewable clip, exactly once."""
    repository = BrollRepository(session)
    repository.lock_idempotency(workspace_id=access.workspace_id, key=idempotency_key)
    existing = repository.job_by_key(workspace_id=access.workspace_id, key=idempotency_key)
    if existing is not None:
        if existing.kind is not kind or existing.project_id != project_id:
            raise BrollPlanConflictError(idempotency_key)
        return existing

    target = repository.lock_plan_target(
        workspace_id=access.workspace_id,
        project_id=project_id,
        candidate_id=candidate_id,
    )
    if target is None:
        raise BrollTargetNotFoundError(str(candidate_id))

    job = create_job(
        session,
        policy=policy,
        access=access,
        project_id=project_id,
        kind=kind,
        idempotency_key=idempotency_key,
        now=now,
    )
    repository.record_plan_request(
        workspace_id=access.workspace_id,
        candidate_id=target.candidate_id,
        job_id=job.job_id,
        coverage=coverage,
        requested_by_user_id=access.user_id,
    )
    return job


@dataclass(frozen=True, slots=True)
class GenerationOffer:
    """What a member may be shown about generating one picture, and at what price."""

    eligibility: GenerationEligibility
    estimate: GenerationEstimate | None = None
    confirmation_token: str | None = None
    # Whether a member may go on to ask what a clip would cost. The panel cannot work
    # this out for itself: it depends on a deployment's feature gate and credentials.
    video_offered: bool = False


def estimate_generation(
    session: Session,
    *,
    access: WorkspaceAccess,
    settings: Settings,
    providers: GenerationProviders,
    suggestion_id: UUID,
    media_kind: GenerationMediaKind,
    now: datetime,
) -> GenerationOffer:
    """Price one server-derived request without reserving budget or starting work."""
    repository = BrollRepository(session)
    suggestion = repository.lock_generation_target(
        workspace_id=access.workspace_id, suggestion_id=suggestion_id
    )
    if suggestion is None:
        raise BrollTargetNotFoundError(str(suggestion_id))

    eligibility = generation_eligibility(
        _target(suggestion), media_kind=media_kind, settings=settings, providers=providers
    )
    video_offered = (
        media_kind is GenerationMediaKind.IMAGE
        and generation_eligibility(
            _target(suggestion),
            media_kind=GenerationMediaKind.VIDEO,
            settings=settings,
            providers=providers,
        ).available
    )
    if not eligibility.available:
        return GenerationOffer(eligibility, video_offered=video_offered)

    provider = providers(media_kind)
    if provider is None:
        return GenerationOffer(
            GenerationEligibility(False, GenerationUnavailableReason.PROVIDER_UNAVAILABLE),
            video_offered=video_offered,
        )
    request = generation_request_for(_target(suggestion), media_kind=media_kind, settings=settings)
    try:
        estimate = provider.estimate(request=request)
    except GenerationProviderRetryableError:
        return GenerationOffer(
            GenerationEligibility(False, GenerationUnavailableReason.PROVIDER_UNAVAILABLE),
            video_offered=video_offered,
        )
    confirmation = GenerationConfirmation(
        workspace_id=access.workspace_id,
        user_id=access.user_id,
        suggestion_id=suggestion_id,
        request=request,
        estimate=estimate,
        expires_at=now + timedelta(seconds=settings.generation_estimate_token_ttl_seconds),
    )
    return GenerationOffer(
        eligibility,
        video_offered=video_offered,
        estimate=estimate,
        confirmation_token=seal_generation_confirmation(
            confirmation, secret=_deployment_secret(settings)
        ),
    )


def start_broll_generation(
    session: Session,
    *,
    policy: AdmissionPolicy,
    access: WorkspaceAccess,
    settings: Settings,
    providers: GenerationProviders,
    suggestion_id: UUID,
    confirmation_token: str,
    video_confirmed: bool,
    idempotency_key: str,
    now: datetime,
) -> JobSnapshot:
    """Admit one generation, its Job, and every budget it spends, in one transaction."""
    repository = BrollRepository(session)
    repository.lock_idempotency(workspace_id=access.workspace_id, key=idempotency_key)
    existing = repository.job_by_key(workspace_id=access.workspace_id, key=idempotency_key)

    suggestion = repository.lock_generation_target(
        workspace_id=access.workspace_id, suggestion_id=suggestion_id
    )
    if suggestion is None:
        raise BrollTargetNotFoundError(str(suggestion_id))
    if existing is not None:
        stored = suggestion.provider_metadata.get("generation", {})
        if existing.kind is not JobKind.BROLL_GENERATE or stored.get("job_id") != str(
            existing.job_id
        ):
            raise BrollPlanConflictError(idempotency_key)
        return existing

    if suggestion_generation_refusal(_target(suggestion), settings=settings) is not None:
        raise GenerationNotEligibleError(str(suggestion_id))

    confirmation = open_generation_confirmation(
        confirmation_token, secret=_deployment_secret(settings), now=now
    )
    if (
        confirmation.workspace_id != access.workspace_id
        or confirmation.user_id != access.user_id
        or confirmation.suggestion_id != suggestion_id
    ):
        raise GenerationConfirmationInvalidError

    media_kind = confirmation.request.media_kind
    if media_kind is GenerationMediaKind.VIDEO and not video_confirmed:
        raise GenerationVideoConfirmationRequiredError(str(suggestion_id))

    eligibility = generation_eligibility(
        _target(suggestion), media_kind=media_kind, settings=settings, providers=providers
    )
    if not eligibility.available:
        raise GenerationNotEligibleError(str(suggestion_id))
    if confirmation.request != generation_request_for(
        _target(suggestion), media_kind=media_kind, settings=settings
    ):
        raise GenerationConfirmationInvalidError

    job = create_job(
        session,
        policy=policy,
        access=access,
        project_id=suggestion.project_id,
        kind=JobKind.BROLL_GENERATE,
        idempotency_key=idempotency_key,
        now=now,
        quota_units=generation_quota_units(confirmation.estimate),
    )
    repository.record_generation_request(
        suggestion=suggestion,
        job_id=job.job_id,
        media_kind=media_kind.value,
        requested_by_user_id=access.user_id,
        estimate=_estimate_snapshot(confirmation.estimate),
        model_alias=confirmation.request.model_alias,
    )
    return job


def generation_quota_units(estimate: GenerationEstimate) -> dict[QuotaResource, Decimal]:
    """Name every metered budget one generated output spends, and how much of each."""
    if estimate.media_kind is GenerationMediaKind.IMAGE:
        return {QuotaResource.GENERATED_IMAGES: estimate.image_units}
    return {
        QuotaResource.GENERATED_VIDEOS: estimate.video_units,
        QuotaResource.GENERATED_SECONDS: estimate.generated_seconds,
    }


def _estimate_snapshot(estimate: GenerationEstimate) -> dict[str, str | int | None]:
    """Record the agreed price in a shape a later settlement can compare against."""
    return {
        "output_count": estimate.output_count,
        "duration_ms": estimate.duration_ms,
        "width": estimate.width,
        "height": estimate.height,
        "latency_class": estimate.latency_class.value,
        "image_units": str(estimate.image_units),
        "video_units": str(estimate.video_units),
        "generated_seconds": str(estimate.generated_seconds),
        "provider_credits": str(estimate.provider_credits),
        "cost_usd": str(estimate.cost_usd),
    }


def _target(suggestion: BrollSuggestion) -> GenerationTarget:
    """Read only the stored facts eligibility and request derivation are allowed to use."""
    return GenerationTarget(
        status=suggestion.status,
        asset_id=suggestion.asset_id,
        relevance_score=(
            None if suggestion.relevance_score is None else float(suggestion.relevance_score)
        ),
        visual_intent=dict(suggestion.visual_intent),
        search_terms=dict(suggestion.search_terms),
        exclusions=tuple(suggestion.exclusions),
    )


def _deployment_secret(settings: Settings) -> str:
    """Return the secret confirmations are sealed with, refusing to seal without one."""
    if settings.session_secret is None:
        raise GenerationConfirmationInvalidError
    return settings.session_secret.get_secret_value()


def list_suggestions(
    repository: BrollRepository,
    *,
    access: WorkspaceAccess,
    project_id: UUID,
    candidate_id: UUID,
) -> tuple[SuggestionSummary, ...]:
    """Return one clip's proposals, refusing a clip this member has no standing on."""
    if not repository.candidate_is_visible(
        workspace_id=access.workspace_id,
        project_id=project_id,
        candidate_id=candidate_id,
    ):
        raise BrollTargetNotFoundError(str(candidate_id))
    return repository.suggestions_for_candidate(
        workspace_id=access.workspace_id,
        candidate_id=candidate_id,
    )
