"""Workspace-scoped use cases for deriving campaign copy from an approved Revision.

Three rules shape everything here. Copy is derived from one exact immutable Revision that
the caller names, never from "whatever is current", so copy can never describe a cut
nobody approved. An Edit this member has no standing on is absent, not forbidden. And
nothing in this module publishes anything: a Campaign Output is text a member reads and
decides about, and the decision to post belongs to the publication composer.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from clipah.brands.repository import BrandRepository
from clipah.brands.use_cases import BrandNotFoundError, resolve_kit_definition
from clipah.campaigns.generator import ClipSummary, generate_campaign_outputs
from clipah.campaigns.models import CampaignLanguage
from clipah.campaigns.repository import CampaignOutputSummary, CampaignRepository
from clipah.editor.models import CompositionValidationError, parse_composition
from clipah.editor.reviews import has_current_approval
from clipah.search.indexer import index_project
from clipah.variants.models import Platform
from clipah.workspaces.models import WorkspaceAccess


class CampaignTargetNotFoundError(Exception):
    """The Edit or the Revision is not visible inside this Workspace."""


class CampaignApprovalRequiredError(Exception):
    """The exact Revision selected for packaging lacks a current approval."""


def generate_outputs(
    session: Session,
    *,
    access: WorkspaceAccess,
    edit_id: UUID,
    revision: int,
    platforms: Sequence[Platform],
    languages: Sequence[CampaignLanguage],
    require_approval: bool = False,
) -> tuple[CampaignOutputSummary, ...]:
    """Derive one piece of copy per destination and language from one exact Revision."""
    repository = CampaignRepository(session)
    target = repository.revision_target(
        workspace_id=access.workspace_id, edit_id=edit_id, revision=revision
    )
    if target is None:
        raise CampaignTargetNotFoundError(f"{edit_id} r{revision}")
    if require_approval and not has_current_approval(
        session,
        workspace_id=access.workspace_id,
        edit_id=edit_id,
        revision_id=target.revision_id,
    ):
        raise CampaignApprovalRequiredError(str(target.revision_id))

    clip = _with_brand_constraints(
        session, access=access, target_clip=target.clip, composition=target.composition
    )
    # The HTTP layer already refuses an empty destination or language list, so the
    # generator's own guard is a domain invariant rather than a second validation here.
    drafts = generate_campaign_outputs(clip, platforms=tuple(platforms), languages=tuple(languages))

    repository.record_outputs(
        workspace_id=access.workspace_id,
        project_id=target.project_id,
        revision_id=target.revision_id,
        drafts=drafts,
        created_by_user_id=access.user_id,
    )
    # Copy a member can read but cannot find again is copy they will write twice.
    index_project(session, workspace_id=access.workspace_id, project_id=target.project_id)
    return tuple(
        output
        for output in repository.outputs_for_edit(
            workspace_id=access.workspace_id, edit_id=target.edit_id
        )
        if output.revision_id == target.revision_id
    )


def list_outputs(
    session: Session, *, access: WorkspaceAccess, edit_id: UUID, revision: int
) -> tuple[CampaignOutputSummary, ...]:
    """Read the copy already derived from any Revision of one Edit."""
    repository = CampaignRepository(session)
    if (
        repository.revision_target(
            workspace_id=access.workspace_id, edit_id=edit_id, revision=revision
        )
        is None
    ):
        raise CampaignTargetNotFoundError(str(edit_id))
    return repository.outputs_for_edit(workspace_id=access.workspace_id, edit_id=edit_id)


def _with_brand_constraints(
    session: Session,
    *,
    access: WorkspaceAccess,
    target_clip: ClipSummary,
    composition: dict[str, object],
) -> ClipSummary:
    """Tell the generator what the clip's own Brand Kit version forbids, if it declares one.

    The brand is read at the version the composition names rather than at whatever the kit
    says today, for the same reason the export is: the rules a member was shown are the
    rules their copy is checked against.
    """
    try:
        declared = parse_composition(composition).brand_kit
    except CompositionValidationError:  # pragma: no cover - stored compositions are valid
        return target_clip
    if declared is None:
        return target_clip
    try:
        definition = resolve_kit_definition(
            BrandRepository(session),
            access=access,
            brand_kit_id=declared.id,
            version=declared.version,
        )
    except BrandNotFoundError:  # pragma: no cover - a declared version is never deleted
        return target_clip
    return ClipSummary(
        hook=target_clip.hook,
        payoff=target_clip.payoff,
        reason=target_clip.reason,
        category=target_clip.category,
        tags=target_clip.tags,
        quote=target_clip.quote,
        quote_language=target_clip.quote_language,
        duration_ms=target_clip.duration_ms,
        context_warnings=target_clip.context_warnings,
        claim_phrases_at_risk=definition.claim_rules.forbidden_claim_phrases,
        visual_exclusions=definition.visual_exclusions,
    )
