"""Workspace-scoped use cases for Brand Kits and Workspace-owned templates.

Three rules shape everything here. A kit or template this member has no standing on is
absent, not forbidden — the same 404 a missing one answers with. Every edit publishes a
new version rather than rewriting the one a clip was judged against. And a kit may only
name media its own Workspace owns, because a logo and a licensed font are somebody's
property before they are a design decision.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from clipah.brands.models import (
    BrandKitDefinition,
    BrandViolation,
    WorkspaceTemplateDefinition,
    evaluate_brand_constraints,
)
from clipah.brands.repository import (
    BrandKitSummary,
    BrandKitVersionSummary,
    BrandRepository,
    TemplateSummary,
    TemplateVersionSummary,
)
from clipah.editor.models import CompositionV1, collect_asset_ids
from clipah.workspaces.models import WorkspaceAccess


class BrandNotFoundError(Exception):
    """The Brand Kit or template is not visible inside this Workspace."""


class BrandDefinitionInvalidError(Exception):
    """The submitted definition is not one this deployment can enforce."""


class BrandAssetForbiddenError(Exception):
    """The definition names media this Workspace does not own."""

    def __init__(self, unauthorized: frozenset[UUID]) -> None:
        """Carry the offending asset identity for the audit trail, never for the client."""
        super().__init__("brand kit names unauthorized assets")
        self.unauthorized = unauthorized


class BrandArchivedError(Exception):
    """The Brand Kit or template has been archived and is no longer edited."""


def create_brand_kit(
    repository: BrandRepository,
    *,
    access: WorkspaceAccess,
    name: str,
    document: dict[str, Any],
    now: datetime,
) -> BrandKitSummary:
    """Publish one Brand Kit at version 1, or refuse rules nobody could enforce."""
    definition = _parse_kit(document)
    _require_owned_assets(repository, access=access, definition=definition)
    brand_kit_id = repository.create_kit(
        workspace_id=access.workspace_id,
        name=name,
        definition=_stored(definition),
        created_by_user_id=access.user_id,
        now=now,
    )
    return get_brand_kit(repository, access=access, brand_kit_id=brand_kit_id)


def get_brand_kit(
    repository: BrandRepository, *, access: WorkspaceAccess, brand_kit_id: UUID
) -> BrandKitSummary:
    """Read one Brand Kit, hiding another Workspace's kit behind the same absence."""
    summary = repository.kit(workspace_id=access.workspace_id, brand_kit_id=brand_kit_id)
    if summary is None:
        raise BrandNotFoundError(str(brand_kit_id))
    return summary


def get_brand_kit_version(
    repository: BrandRepository, *, access: WorkspaceAccess, brand_kit_id: UUID, version: int
) -> BrandKitVersionSummary:
    """Read one published version, refusing to approximate a version nobody published."""
    summary = repository.kit_version(
        workspace_id=access.workspace_id, brand_kit_id=brand_kit_id, version=version
    )
    if summary is None:
        raise BrandNotFoundError(f"{brand_kit_id} v{version}")
    return summary


def list_brand_kits(
    repository: BrandRepository, *, access: WorkspaceAccess, include_archived: bool = False
) -> tuple[BrandKitSummary, ...]:
    """List the kits this Workspace still uses, or every kit it has ever published."""
    return repository.kits(workspace_id=access.workspace_id, include_archived=include_archived)


def update_brand_kit(
    repository: BrandRepository,
    *,
    access: WorkspaceAccess,
    brand_kit_id: UUID,
    name: str | None,
    document: dict[str, Any] | None,
    now: datetime,
) -> BrandKitSummary:
    """Rename one kit, publish its next version of rules, or both.

    A name is not a rule, so renaming publishes nothing: a Revision judged under version 1
    stays judged under version 1 however often the kit is relabelled.
    """
    locked = repository.lock_kit(workspace_id=access.workspace_id, brand_kit_id=brand_kit_id)
    if locked is None:
        raise BrandNotFoundError(str(brand_kit_id))
    if locked.archived_at is not None:
        raise BrandArchivedError(str(brand_kit_id))
    if name is not None:
        locked.name = name
    if document is not None:
        definition = _parse_kit(document)
        _require_owned_assets(repository, access=access, definition=definition)
        locked.current_version += 1
        repository.publish_kit_version(
            workspace_id=access.workspace_id,
            brand_kit_id=brand_kit_id,
            version=locked.current_version,
            definition=_stored(definition),
            created_by_user_id=access.user_id,
            now=now,
        )
    locked.updated_at = now
    return get_brand_kit(repository, access=access, brand_kit_id=brand_kit_id)


def archive_brand_kit(
    repository: BrandRepository, *, access: WorkspaceAccess, brand_kit_id: UUID, now: datetime
) -> BrandKitSummary:
    """Stop offering one kit without destroying the versions clips were judged against."""
    locked = repository.lock_kit(workspace_id=access.workspace_id, brand_kit_id=brand_kit_id)
    if locked is None:
        raise BrandNotFoundError(str(brand_kit_id))
    if locked.archived_at is None:
        locked.archived_at = now
        locked.updated_at = now
    return get_brand_kit(repository, access=access, brand_kit_id=brand_kit_id)


def create_template(
    repository: BrandRepository,
    *,
    access: WorkspaceAccess,
    name: str,
    brand_kit_id: UUID | None,
    document: dict[str, Any],
    now: datetime,
) -> TemplateSummary:
    """Publish one Workspace-owned look at version 1."""
    definition = _parse_template(document)
    _require_own_kit(repository, access=access, brand_kit_id=brand_kit_id)
    template_id = repository.create_template(
        workspace_id=access.workspace_id,
        brand_kit_id=brand_kit_id,
        name=name,
        kind=definition.kind.value,
        definition=_stored(definition),
        created_by_user_id=access.user_id,
        now=now,
    )
    return get_template(repository, access=access, template_id=template_id)


def get_template(
    repository: BrandRepository, *, access: WorkspaceAccess, template_id: UUID
) -> TemplateSummary:
    """Read one look, hiding another Workspace's look behind the same absence."""
    summary = repository.template(workspace_id=access.workspace_id, template_id=template_id)
    if summary is None:
        raise BrandNotFoundError(str(template_id))
    return summary


def get_template_version(
    repository: BrandRepository, *, access: WorkspaceAccess, template_id: UUID, version: int
) -> TemplateVersionSummary:
    """Read one published look version, including one whose template is archived.

    An archived template still renders: a Revision built on it names this version, and
    archiving is a decision to stop offering a look rather than to break the clips that
    already use it.
    """
    summary = repository.template_version(
        workspace_id=access.workspace_id, template_id=template_id, version=version
    )
    if summary is None:
        raise BrandNotFoundError(f"{template_id} v{version}")
    return summary


def list_templates(
    repository: BrandRepository, *, access: WorkspaceAccess, include_archived: bool = False
) -> tuple[TemplateSummary, ...]:
    """List the looks this Workspace still offers, or every look it has published."""
    return repository.templates(workspace_id=access.workspace_id, include_archived=include_archived)


def update_template(
    repository: BrandRepository,
    *,
    access: WorkspaceAccess,
    template_id: UUID,
    name: str | None,
    brand_kit_id: UUID | None,
    document: dict[str, Any] | None,
    now: datetime,
) -> TemplateSummary:
    """Rename one look, publish its next version, or both."""
    locked = repository.lock_template(workspace_id=access.workspace_id, template_id=template_id)
    if locked is None:
        raise BrandNotFoundError(str(template_id))
    if locked.archived_at is not None:
        raise BrandArchivedError(str(template_id))
    if name is not None:
        locked.name = name
    if brand_kit_id is not None:
        _require_own_kit(repository, access=access, brand_kit_id=brand_kit_id)
        locked.brand_kit_id = brand_kit_id
    if document is not None:
        definition = _parse_template(document)
        locked.kind = definition.kind
        locked.current_version += 1
        repository.publish_template_version(
            workspace_id=access.workspace_id,
            template_id=template_id,
            version=locked.current_version,
            definition=_stored(definition),
            created_by_user_id=access.user_id,
            now=now,
        )
    locked.updated_at = now
    return get_template(repository, access=access, template_id=template_id)


def archive_template(
    repository: BrandRepository, *, access: WorkspaceAccess, template_id: UUID, now: datetime
) -> TemplateSummary:
    """Stop offering one look without breaking the compositions that already use it."""
    locked = repository.lock_template(workspace_id=access.workspace_id, template_id=template_id)
    if locked is None:
        raise BrandNotFoundError(str(template_id))
    if locked.archived_at is None:
        locked.archived_at = now
        locked.updated_at = now
    return get_template(repository, access=access, template_id=template_id)


def resolve_kit_definition(
    repository: BrandRepository, *, access: WorkspaceAccess, brand_kit_id: UUID, version: int
) -> BrandKitDefinition:
    """Read back the exact rules one composition declares it was judged against."""
    stored = get_brand_kit_version(
        repository, access=access, brand_kit_id=brand_kit_id, version=version
    )
    return _parse_kit(stored.definition)


def resolve_template_definition(
    repository: BrandRepository, *, access: WorkspaceAccess, template_id: UUID, version: int
) -> WorkspaceTemplateDefinition:
    """Read back the exact look one composition declares it was built from."""
    stored = get_template_version(
        repository, access=access, template_id=template_id, version=version
    )
    return _parse_template(stored.definition)


def violations_for_composition(
    repository: BrandRepository, *, access: WorkspaceAccess, composition: CompositionV1
) -> tuple[BrandViolation, ...]:
    """Judge one composition by the exact Brand Kit version it declares, if it declares one.

    A composition that names no kit is answerable to nothing, and a composition whose kit
    version has gone is reported as breaking no rule rather than as silently complying:
    the kit is archived, never deleted, so the version is there unless somebody bypassed
    the API entirely.
    """
    reference = composition.brand_kit
    if reference is None:
        return ()
    try:
        definition = resolve_kit_definition(
            repository,
            access=access,
            brand_kit_id=reference.id,
            version=reference.version,
        )
    except BrandNotFoundError:  # pragma: no cover - an archived kit keeps its versions
        return ()
    return evaluate_brand_constraints(
        composition,
        definition=definition,
        asset_descriptions=repository.asset_descriptions(
            workspace_id=access.workspace_id, asset_ids=collect_asset_ids(composition)
        ),
    )


def _parse_kit(document: dict[str, Any]) -> BrandKitDefinition:
    """Validate one untrusted document as a Brand Kit definition."""
    try:
        return BrandKitDefinition.model_validate(document)
    except ValidationError as error:
        raise BrandDefinitionInvalidError(_first_reason(error)) from error


def _parse_template(document: dict[str, Any]) -> WorkspaceTemplateDefinition:
    """Validate one untrusted document as a template definition."""
    try:
        return WorkspaceTemplateDefinition.model_validate(document)
    except ValidationError as error:
        raise BrandDefinitionInvalidError(_first_reason(error)) from error


def _require_owned_assets(
    repository: BrandRepository, *, access: WorkspaceAccess, definition: BrandKitDefinition
) -> None:
    """Refuse a definition that names a logo or a font file this Workspace does not own."""
    named = definition.referenced_asset_ids()
    owned = repository.owned_asset_ids(workspace_id=access.workspace_id, asset_ids=named)
    unauthorized = named - owned
    if unauthorized:
        raise BrandAssetForbiddenError(unauthorized)


def _require_own_kit(
    repository: BrandRepository, *, access: WorkspaceAccess, brand_kit_id: UUID | None
) -> None:
    """Refuse a template that borrows a Brand Kit belonging to another Workspace."""
    if brand_kit_id is None:
        return
    get_brand_kit(repository, access=access, brand_kit_id=brand_kit_id)


def _stored(definition: BrandKitDefinition | WorkspaceTemplateDefinition) -> dict[str, Any]:
    """Store one published version exactly as it will be read back."""
    stored: dict[str, Any] = json.loads(definition.model_dump_json(by_alias=True))
    return stored


def _first_reason(error: ValidationError) -> str:
    """Name the field at fault without leaking the submitted values back to a client."""
    first = error.errors()[0]
    location = ".".join(str(part) for part in first["loc"]) or "definition"
    return f"{location}: {first['msg']}"
