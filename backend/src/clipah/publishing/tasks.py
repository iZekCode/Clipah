"""Worker-side guards for durable Publication dispatch."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.models import Publication, SocialAccount
from clipah.publishing.models import PublicationStatus, PublicationSummary
from clipah.publishing.state_machine import transition
from clipah.social_accounts.models import SocialConnectionStatus
from clipah.workspaces.authorization import DatabaseWorkspaceAuthorizer
from clipah.workspaces.models import (
    WorkspaceAction,
    WorkspaceNotFoundError,
    WorkspacePermissionError,
)


class PublicationDispatchInvalidError(Exception):
    """The claimed row cannot safely begin provider work."""


def revalidate_publication_dispatch(
    session: Session,
    *,
    workspace_id: UUID,
    publication_id: UUID,
    now: datetime,
) -> PublicationSummary:
    """Recheck live authority, connection, and capabilities before provider I/O."""
    publication = session.scalar(
        select(Publication)
        .where(
            Publication.workspace_id == workspace_id,
            Publication.id == publication_id,
        )
        .with_for_update()
    )
    if publication is None or publication.status is not PublicationStatus.PREFLIGHTING:
        raise PublicationDispatchInvalidError(str(publication_id))
    if publication.approved_by_user_id is None:
        raise PublicationDispatchInvalidError("publication has no approving actor")

    try:
        DatabaseWorkspaceAuthorizer(session).require(
            user_id=publication.approved_by_user_id,
            workspace_id=workspace_id,
            action=WorkspaceAction.PUBLISH,
        )
    except (WorkspaceNotFoundError, WorkspacePermissionError):
        publication.status = transition(
            current=publication.status, target=PublicationStatus.CANCELLED
        ).current
        publication.cancelled_at = now
        session.flush()
        return _summary(publication)

    account = session.scalar(
        select(SocialAccount)
        .where(
            SocialAccount.workspace_id == workspace_id,
            SocialAccount.id == publication.social_account_id,
        )
        .with_for_update()
    )
    if account is None or account.connection_status is not SocialConnectionStatus.ACTIVE:
        publication.status = transition(
            current=publication.status, target=PublicationStatus.RECONNECT_REQUIRED
        ).current
    elif account.capability_snapshot.get("version") != publication.capability_version:
        publication.status = transition(
            current=publication.status, target=PublicationStatus.AWAITING_APPROVAL
        ).current
    session.flush()
    return _summary(publication)


def _summary(publication: Publication) -> PublicationSummary:
    """Detach the safe dispatch result from its locked ORM row."""
    return PublicationSummary(
        publication_id=publication.id,
        social_account_id=publication.social_account_id,
        status=publication.status,
        scheduled_for=publication.scheduled_for,
        display_timezone=publication.display_timezone,
    )
