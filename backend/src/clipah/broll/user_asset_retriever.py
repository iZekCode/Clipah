"""Search a Workspace's own accepted B-roll before anybody pays a stock provider.

Footage the Workspace already holds is free, immediate, already licensed, and already
normalized, so it is always searched first. What makes it searchable is its provenance:
the query it was retrieved for, the words the provider used to describe it, and its
attribution. A Workspace that has accepted a picture of a signup form once should not buy
a second one.

Matching happens in Python rather than in SQL on purpose. The candidate set is one
Workspace's own accepted assets, which is small, and doing it here means the same token
rules decide a local match and a stock match rather than two different notions of
relevance living in two languages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.broll.retriever import (
    ExternalAssetCandidate,
    LicenseTerms,
    MediaKind,
    SearchRequest,
)
from clipah.models import Asset, AssetKind, AssetProvenance

WORKSPACE_PROVIDER = "workspace"
_TOKEN = re.compile(r"[^\w]+", re.UNICODE)
_MIN_TOKEN_LENGTH = 3


@dataclass(frozen=True, slots=True)
class StoredBrollAsset:
    """One accepted asset of this Workspace, with the provenance that describes it."""

    asset_id: UUID
    project_id: UUID
    storage_key: str
    content_type: str
    width: int
    height: int
    duration_ms: int | None
    sha256: bytes
    provider: str
    provider_asset_id: str
    source_url: str
    author: str
    author_url: str
    license_name: str
    license_url: str
    terms_snapshot: str
    attribution_text: str
    query: str


class UserAssetRetriever:
    """Offer the Workspace's own accepted footage as candidates for one intent."""

    def __init__(self, session: Session, *, workspace_id: UUID) -> None:
        """Bind the transaction holding verified RLS context for one Workspace."""
        self._session = session
        self._workspace_id = workspace_id

    def search(self, *, request: SearchRequest) -> tuple[ExternalAssetCandidate, ...]:
        """Return this Workspace's accepted assets whose provenance matches the intent.

        Only the query an asset was retrieved for is matched against. Attribution lines
        and source URLs are provider chrome, and matching on them makes a stopword like
        "on" look like evidence that a clip illustrates a beat.
        """
        wanted = _tokens(
            request.intent.subject,
            request.intent.action,
            request.intent.setting,
            " ".join(request.intent.search_terms_id),
            " ".join(request.intent.search_terms_en),
        )
        if not wanted:
            return ()
        matched = [
            stored
            for stored in accepted_broll_assets(self._session, workspace_id=self._workspace_id)
            if _tokens(stored.query) & wanted
        ]
        ordered = sorted(matched, key=lambda stored: str(stored.asset_id))
        return tuple(
            _candidate(stored, query=request.queries[0] if request.queries else "")
            for stored in ordered[: request.limit]
        )


def accepted_broll_assets(session: Session, *, workspace_id: UUID) -> tuple[StoredBrollAsset, ...]:
    """Read every accepted B-roll asset this Workspace already holds.

    Only assets that carry provenance are returned. An asset without it could never have
    become selectable in the first place, so offering it again would smuggle a picture
    past the gate that refused it.
    """
    rows = session.execute(
        select(Asset, AssetProvenance)
        .join(
            AssetProvenance,
            (AssetProvenance.workspace_id == Asset.workspace_id)
            & (AssetProvenance.asset_id == Asset.id),
        )
        .where(Asset.workspace_id == workspace_id, Asset.kind == AssetKind.BROLL)
        .order_by(Asset.id)
    ).all()
    return tuple(_stored(asset, provenance) for asset, provenance in rows)


def find_by_checksum(
    session: Session, *, workspace_id: UUID, sha256: bytes
) -> StoredBrollAsset | None:
    """Find footage this Workspace already stored, so identical media is fetched once.

    Reuse is scoped to one Workspace deliberately. Two Workspaces retrieving the same
    stock clip each store their own copy: sharing one row across tenants would make a
    Workspace's asset library depend on what another Workspace happened to search for.
    """
    row = session.execute(
        select(Asset, AssetProvenance)
        .join(
            AssetProvenance,
            (AssetProvenance.workspace_id == Asset.workspace_id)
            & (AssetProvenance.asset_id == Asset.id),
        )
        .where(
            Asset.workspace_id == workspace_id,
            Asset.kind == AssetKind.BROLL,
            Asset.sha256 == sha256,
        )
        .order_by(Asset.created_at, Asset.id)
    ).first()
    if row is None:
        return None
    asset, provenance = row
    return _stored(asset, provenance)


def _stored(asset: Asset, provenance: AssetProvenance) -> StoredBrollAsset:
    """Detach one accepted asset and its provenance from SQLAlchemy."""
    return StoredBrollAsset(
        asset_id=asset.id,
        project_id=asset.project_id,
        storage_key=asset.storage_key,
        content_type=asset.content_type,
        width=asset.width or 0,
        height=asset.height or 0,
        duration_ms=asset.duration_ms,
        sha256=asset.sha256,
        provider=provenance.provider,
        provider_asset_id=provenance.provider_asset_id,
        source_url=provenance.source_url,
        author=provenance.author,
        author_url=provenance.author_url,
        license_name=provenance.license_name,
        license_url=provenance.license_url,
        terms_snapshot=provenance.terms_snapshot,
        attribution_text=provenance.attribution_text,
        query=provenance.query,
    )


def _candidate(stored: StoredBrollAsset, *, query: str) -> ExternalAssetCandidate:
    """Present one already-stored asset in the same shape a provider result takes.

    The download URL is empty because there is nothing left to fetch: this footage is
    already in Clipah's own storage, which is the entire reason to prefer it.
    """
    return ExternalAssetCandidate(
        provider=WORKSPACE_PROVIDER,
        provider_asset_id=str(stored.asset_id),
        media_kind=MediaKind.VIDEO,
        source_url=stored.source_url,
        download_url="",
        author=stored.author,
        author_url=stored.author_url,
        license=LicenseTerms(
            name=stored.license_name,
            url=stored.license_url,
            attribution_required=False,
            snapshot=stored.terms_snapshot,
        ),
        width=stored.width,
        height=stored.height,
        duration_ms=stored.duration_ms,
        attribution_text=stored.attribution_text,
        query=query or stored.query,
        safe=True,
        description=stored.query,
        tags=tuple(sorted(_tokens(stored.query))),
    )


def _tokens(*values: str) -> set[str]:
    """Reduce text to comparable lowercase tokens, dropping ones too short to mean anything.

    Two-letter tokens are articles, prepositions, and glue. Treating one as a match is how
    a clip about a signup form ends up illustrating a beat about an activation chart.
    """
    joined = " ".join(values).casefold()
    return {token for token in _TOKEN.split(joined) if len(token) >= _MIN_TOKEN_LENGTH}
