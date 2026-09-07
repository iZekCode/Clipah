"""Private persistence for Brand Kits, Workspace-owned templates, and their versions.

Every read here is bound to one Workspace, and every write appends. An identity row is
renamed, re-pointed at its newest version, and archived; a published version is never
updated, because a Revision that names one has to keep resolving to what it actually said.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.models import (
    Asset,
    AssetProvenance,
    BrandKit,
    BrandKitVersion,
    BrandTemplate,
    BrandTemplateVersion,
)


@dataclass(frozen=True, slots=True)
class BrandKitSummary:
    """One Brand Kit and its newest published version, detached for the API to render."""

    brand_kit_id: UUID
    name: str
    version: int
    definition: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


@dataclass(frozen=True, slots=True)
class BrandKitVersionSummary:
    """One published Brand Kit version, exactly as it was published."""

    brand_kit_id: UUID
    version: int
    definition: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TemplateSummary:
    """One Workspace-owned look and its newest published version."""

    template_id: UUID
    brand_kit_id: UUID | None
    name: str
    kind: str
    version: int
    definition: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


@dataclass(frozen=True, slots=True)
class TemplateVersionSummary:
    """One published template version, exactly as it was published."""

    template_id: UUID
    version: int
    definition: dict[str, Any]
    created_at: datetime


class BrandRepository:
    """Keep Brand Kit and template ORM details behind one tenant-scoped boundary."""

    def __init__(self, session: Session) -> None:
        """Bind persistence to the transaction holding verified RLS context."""
        self._session = session

    def owned_asset_ids(self, *, workspace_id: UUID, asset_ids: frozenset[UUID]) -> frozenset[UUID]:
        """Report which of these assets this Workspace actually owns.

        A Brand Kit names media across Projects — a logo belongs to the brand rather than
        to one video — so ownership is proven at the Workspace rather than the Project.
        """
        if not asset_ids:
            return frozenset()
        rows = self._session.scalars(
            select(Asset.id).where(Asset.workspace_id == workspace_id, Asset.id.in_(asset_ids))
        ).all()
        return frozenset(rows)

    def asset_descriptions(
        self, *, workspace_id: UUID, asset_ids: frozenset[UUID]
    ) -> dict[UUID, str]:
        """Report what retrieval or generation recorded each placed asset as showing.

        Nobody can see inside a stock clip from the timeline, so the search that found it
        and the prompt that produced it are the evidence a visual exclusion is judged on.
        """
        if not asset_ids:
            return {}
        rows = self._session.execute(
            select(AssetProvenance.asset_id, AssetProvenance.query, AssetProvenance.prompt).where(
                AssetProvenance.workspace_id == workspace_id,
                AssetProvenance.asset_id.in_(asset_ids),
            )
        ).all()
        return {
            asset_id: " ".join(part for part in (query, prompt) if part)
            for asset_id, query, prompt in rows
        }

    def create_kit(
        self,
        *,
        workspace_id: UUID,
        name: str,
        definition: dict[str, Any],
        created_by_user_id: UUID,
        now: datetime,
    ) -> UUID:
        """Publish one Brand Kit and the first version of its rules."""
        brand_kit_id = uuid4()
        self._session.add(
            BrandKit(
                id=brand_kit_id,
                workspace_id=workspace_id,
                name=name,
                current_version=1,
                created_by_user_id=created_by_user_id,
                created_at=now,
                updated_at=now,
            )
        )
        self._session.add(
            BrandKitVersion(
                id=uuid4(),
                workspace_id=workspace_id,
                brand_kit_id=brand_kit_id,
                version=1,
                definition=definition,
                created_by_user_id=created_by_user_id,
                created_at=now,
            )
        )
        self._session.flush()
        return brand_kit_id

    def lock_kit(self, *, workspace_id: UUID, brand_kit_id: UUID) -> BrandKit | None:
        """Lock one kit of this Workspace before its next version is published."""
        return self._session.scalar(
            select(BrandKit)
            .where(BrandKit.workspace_id == workspace_id, BrandKit.id == brand_kit_id)
            .with_for_update()
        )

    def publish_kit_version(
        self,
        *,
        workspace_id: UUID,
        brand_kit_id: UUID,
        version: int,
        definition: dict[str, Any],
        created_by_user_id: UUID,
        now: datetime,
    ) -> None:
        """Append one version of a kit's rules, leaving every earlier version alone."""
        self._session.add(
            BrandKitVersion(
                id=uuid4(),
                workspace_id=workspace_id,
                brand_kit_id=brand_kit_id,
                version=version,
                definition=definition,
                created_by_user_id=created_by_user_id,
                created_at=now,
            )
        )
        self._session.flush()

    def kit(self, *, workspace_id: UUID, brand_kit_id: UUID) -> BrandKitSummary | None:
        """Read one kit of this Workspace at its newest published version."""
        row = self._session.execute(
            select(BrandKit, BrandKitVersion)
            .join(
                BrandKitVersion,
                (BrandKitVersion.workspace_id == BrandKit.workspace_id)
                & (BrandKitVersion.brand_kit_id == BrandKit.id)
                & (BrandKitVersion.version == BrandKit.current_version),
            )
            .where(BrandKit.workspace_id == workspace_id, BrandKit.id == brand_kit_id)
        ).first()
        if row is None:
            return None
        kit, version = row
        return _kit_summary(kit, version)

    def kit_version(
        self, *, workspace_id: UUID, brand_kit_id: UUID, version: int
    ) -> BrandKitVersionSummary | None:
        """Read exactly one published version of one kit."""
        row = self._session.scalar(
            select(BrandKitVersion).where(
                BrandKitVersion.workspace_id == workspace_id,
                BrandKitVersion.brand_kit_id == brand_kit_id,
                BrandKitVersion.version == version,
            )
        )
        if row is None:
            return None
        return BrandKitVersionSummary(
            brand_kit_id=row.brand_kit_id,
            version=row.version,
            definition=dict(row.definition),
            created_at=row.created_at,
        )

    def kits(self, *, workspace_id: UUID, include_archived: bool) -> tuple[BrandKitSummary, ...]:
        """List this Workspace's kits, newest first, hiding archived ones by default."""
        statement = (
            select(BrandKit, BrandKitVersion)
            .join(
                BrandKitVersion,
                (BrandKitVersion.workspace_id == BrandKit.workspace_id)
                & (BrandKitVersion.brand_kit_id == BrandKit.id)
                & (BrandKitVersion.version == BrandKit.current_version),
            )
            .where(BrandKit.workspace_id == workspace_id)
            .order_by(BrandKit.created_at.desc(), BrandKit.id)
        )
        if not include_archived:
            statement = statement.where(BrandKit.archived_at.is_(None))
        return tuple(
            _kit_summary(kit, version) for kit, version in self._session.execute(statement)
        )

    def create_template(
        self,
        *,
        workspace_id: UUID,
        brand_kit_id: UUID | None,
        name: str,
        kind: str,
        definition: dict[str, Any],
        created_by_user_id: UUID,
        now: datetime,
    ) -> UUID:
        """Publish one Workspace-owned look and the first version of its definition."""
        template_id = uuid4()
        self._session.add(
            BrandTemplate(
                id=template_id,
                workspace_id=workspace_id,
                brand_kit_id=brand_kit_id,
                name=name,
                kind=kind,
                current_version=1,
                created_by_user_id=created_by_user_id,
                created_at=now,
                updated_at=now,
            )
        )
        self._session.add(
            BrandTemplateVersion(
                id=uuid4(),
                workspace_id=workspace_id,
                template_id=template_id,
                version=1,
                definition=definition,
                created_by_user_id=created_by_user_id,
                created_at=now,
            )
        )
        self._session.flush()
        return template_id

    def lock_template(self, *, workspace_id: UUID, template_id: UUID) -> BrandTemplate | None:
        """Lock one template of this Workspace before its next version is published."""
        return self._session.scalar(
            select(BrandTemplate)
            .where(BrandTemplate.workspace_id == workspace_id, BrandTemplate.id == template_id)
            .with_for_update()
        )

    def publish_template_version(
        self,
        *,
        workspace_id: UUID,
        template_id: UUID,
        version: int,
        definition: dict[str, Any],
        created_by_user_id: UUID,
        now: datetime,
    ) -> None:
        """Append one version of a look, leaving every earlier version alone."""
        self._session.add(
            BrandTemplateVersion(
                id=uuid4(),
                workspace_id=workspace_id,
                template_id=template_id,
                version=version,
                definition=definition,
                created_by_user_id=created_by_user_id,
                created_at=now,
            )
        )
        self._session.flush()

    def template(self, *, workspace_id: UUID, template_id: UUID) -> TemplateSummary | None:
        """Read one template of this Workspace at its newest published version."""
        row = self._session.execute(
            select(BrandTemplate, BrandTemplateVersion)
            .join(
                BrandTemplateVersion,
                (BrandTemplateVersion.workspace_id == BrandTemplate.workspace_id)
                & (BrandTemplateVersion.template_id == BrandTemplate.id)
                & (BrandTemplateVersion.version == BrandTemplate.current_version),
            )
            .where(BrandTemplate.workspace_id == workspace_id, BrandTemplate.id == template_id)
        ).first()
        if row is None:
            return None
        template, version = row
        return _template_summary(template, version)

    def template_version(
        self, *, workspace_id: UUID, template_id: UUID, version: int
    ) -> TemplateVersionSummary | None:
        """Read exactly one published version of one look."""
        row = self._session.scalar(
            select(BrandTemplateVersion).where(
                BrandTemplateVersion.workspace_id == workspace_id,
                BrandTemplateVersion.template_id == template_id,
                BrandTemplateVersion.version == version,
            )
        )
        if row is None:
            return None
        return TemplateVersionSummary(
            template_id=row.template_id,
            version=row.version,
            definition=dict(row.definition),
            created_at=row.created_at,
        )

    def templates(
        self, *, workspace_id: UUID, include_archived: bool
    ) -> tuple[TemplateSummary, ...]:
        """List this Workspace's looks, newest first, hiding archived ones by default."""
        statement = (
            select(BrandTemplate, BrandTemplateVersion)
            .join(
                BrandTemplateVersion,
                (BrandTemplateVersion.workspace_id == BrandTemplate.workspace_id)
                & (BrandTemplateVersion.template_id == BrandTemplate.id)
                & (BrandTemplateVersion.version == BrandTemplate.current_version),
            )
            .where(BrandTemplate.workspace_id == workspace_id)
            .order_by(BrandTemplate.created_at.desc(), BrandTemplate.id)
        )
        if not include_archived:
            statement = statement.where(BrandTemplate.archived_at.is_(None))
        return tuple(
            _template_summary(template, version)
            for template, version in self._session.execute(statement)
        )


def _kit_summary(kit: BrandKit, version: BrandKitVersion) -> BrandKitSummary:
    """Detach exactly what a member is allowed to read about one kit."""
    return BrandKitSummary(
        brand_kit_id=kit.id,
        name=kit.name,
        version=version.version,
        definition=dict(version.definition),
        created_at=kit.created_at,
        updated_at=kit.updated_at,
        archived_at=kit.archived_at,
    )


def _template_summary(template: BrandTemplate, version: BrandTemplateVersion) -> TemplateSummary:
    """Detach exactly what a member is allowed to read about one look."""
    return TemplateSummary(
        template_id=template.id,
        brand_kit_id=template.brand_kit_id,
        name=template.name,
        kind=template.kind.value,
        version=version.version,
        definition=dict(version.definition),
        created_at=template.created_at,
        updated_at=template.updated_at,
        archived_at=template.archived_at,
    )
