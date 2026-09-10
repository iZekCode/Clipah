"""The scheduled sweep: find what has expired, then discharge it one tombstone at a time.

Nothing here decides how long data lives — that is `policy.py` — and nothing here widens
a target. The sweep only notices that a window has closed, claims a bounded batch, and
removes exactly what each tombstone names, recording a failure when it cannot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from clipah.assets.storage import ObjectStore, ObjectStoreUnavailableError
from clipah.config import Settings
from clipah.db import retention_session_scope
from clipah.jobs.admission import ACTIVE_JOB_STATUSES
from clipah.jobs.workspace import remove_job_workspaces
from clipah.models import (
    Job,
    MultipartUpload,
    MultipartUploadStatus,
    Publication,
    SourceConnection,
    SourceConnectionStatus,
    Workspace,
)
from clipah.observability.logging import get_logger
from clipah.observability.metrics import count
from clipah.publishing.models import PublicationStatus
from clipah.retention.policy import RetentionEntityKind, RetentionPolicy
from clipah.retention.use_cases import (
    DueTombstone,
    claim_due_tombstones,
    mark_tombstone_purged,
    purge_project_rows,
    purge_storage_prefix,
    purge_workspace_rows,
    record_purge_failure,
    schedule_tombstone,
)

STORAGE_FAILURE_CODE = "RETENTION_STORAGE_UNAVAILABLE"
# A suggestion in one of these states was offered and not taken, so the media behind it
# is a preview nobody chose. A still-proposed suggestion is left alone: it is what the
# member is looking at, and its Project's own retention covers it in the end.
_UNSELECTED_SUGGESTION_STATUSES = ("rejected", "removed", "replaced")
_logger = get_logger(__name__)


class DischargeOutcome(StrEnum):
    """How one attempt at one tombstone ended."""

    PURGED = "purged"
    INCOMPLETE = "incomplete"
    DEFERRED = "deferred"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SweepReport:
    """What one pass of the sweep accomplished across every Workspace it visited."""

    scheduled: int
    purged: int
    deferred: int
    failed: int


def scan_workspace(session: Session, *, policy: RetentionPolicy, now: datetime) -> int:
    """Record a tombstone for everything in one Workspace whose window has closed.

    Expiry is discovered rather than announced: nobody deletes an abandoned upload or a
    rejected draft on purpose, so the sweep looks for the states Section 7 gives a
    lifetime to and writes the same kind of tombstone a deliberate deletion would.
    """
    scheduled = 0
    scheduled += _expire_abandoned_uploads(session, policy=policy, now=now)
    scheduled += _expire_revoked_source_connections(session, policy=policy, now=now)
    scheduled += _expire_unaccepted_broll(session, policy=policy, now=now)
    scheduled += _expire_failed_job_workspaces(session, policy=policy, now=now)
    return scheduled


def discharge_tombstone(
    session: Session,
    store: ObjectStore,
    *,
    tombstone: DueTombstone,
    policy: RetentionPolicy,
    now: datetime,
) -> DischargeOutcome:
    """Carry out one claimed tombstone, or say honestly why it did not finish.

    Media is removed before rows, because a row is the only remaining record of where
    an object lives: losing it while the object survives leaves storage nobody can find
    or bill for. Rows therefore outlive a storage outage, and the attempt is retried.
    """
    if _has_unfinished_work(session, tombstone=tombstone):
        return DischargeOutcome.DEFERRED

    if tombstone.storage_prefix is not None:
        try:
            _abort_provider_upload(session, store, tombstone=tombstone)
            outcome = _purge_storage(store, tombstone=tombstone, policy=policy)
        except ObjectStoreUnavailableError:
            record_purge_failure(
                session, tombstone=tombstone, error_code=STORAGE_FAILURE_CODE, now=now
            )
            count("clipah.retention.outcome", outcome="failed", code=STORAGE_FAILURE_CODE)
            return DischargeOutcome.FAILED
        if not outcome:
            # More objects remain than one bounded pass may remove. The tombstone stays
            # open so the next sweep continues from where this one stopped.
            count("clipah.retention.outcome", outcome="incomplete", code="OK")
            return DischargeOutcome.INCOMPLETE

    _purge_rows(session, tombstone=tombstone)
    mark_tombstone_purged(session, tombstone=tombstone, now=now)
    count("clipah.retention.outcome", outcome="purged", code="OK")
    return DischargeOutcome.PURGED


def sweep(
    *,
    settings: Settings,
    store: ObjectStore,
    now: datetime,
    policy: RetentionPolicy | None = None,
) -> SweepReport:
    """Scan and discharge every Workspace, one short transaction per Workspace at a time."""
    resolved = policy or RetentionPolicy.from_settings(settings)
    scheduled = purged = deferred = failed = 0
    for workspace_id in _workspaces(settings):
        with retention_session_scope(settings=settings, workspace_id=workspace_id) as session:
            scheduled += scan_workspace(session, policy=resolved, now=now)
            for tombstone in claim_due_tombstones(
                session, now=now, limit=resolved.batch_size, max_failures=resolved.max_failures
            ):
                outcome = discharge_tombstone(
                    session, store, tombstone=tombstone, policy=resolved, now=now
                )
                purged += outcome is DischargeOutcome.PURGED
                deferred += outcome is DischargeOutcome.DEFERRED
                failed += outcome is DischargeOutcome.FAILED
    report = SweepReport(scheduled=scheduled, purged=purged, deferred=deferred, failed=failed)
    _logger.info(
        "retention.sweep.finished",
        scheduled=report.scheduled,
        purged=report.purged,
        deferred=report.deferred,
        failed=report.failed,
    )
    return report


def _workspaces(settings: Settings) -> tuple[UUID, ...]:
    """List every Workspace the sweep must visit, including the deleted ones."""
    with retention_session_scope(settings=settings, workspace_id=UUID(int=0)) as session:
        return tuple(session.scalars(select(Workspace.id).order_by(Workspace.created_at)))


def _abort_provider_upload(
    session: Session, store: ObjectStore, *, tombstone: DueTombstone
) -> None:
    """Tell the provider to release the parts of an upload nobody finished.

    Deleting the row and the final key is not enough: an incomplete multipart upload
    holds bytes at the provider that no key names, and only an abort against the exact
    recorded upload identifier releases them.
    """
    if tombstone.entity_kind is not RetentionEntityKind.MULTIPART_UPLOAD:
        return
    upload = session.get(MultipartUpload, tombstone.entity_id)
    if upload is None:
        return
    try:
        store.abort_multipart_upload(upload_id=upload.storage_upload_id, key=upload.storage_key)
    except Exception:
        # A provider that has already forgotten this upload, or one that refuses the
        # abort, must not stop the rest of the purge: the row and the key still go, and
        # the attempt is recorded rather than retried against something broader.
        _logger.warning("retention.upload.abort_refused", code="RETENTION_ABORT_REFUSED")


def _purge_storage(store: ObjectStore, *, tombstone: DueTombstone, policy: RetentionPolicy) -> bool:
    """Remove one bounded page of the media this tombstone names, reporting completion."""
    prefix = tombstone.storage_prefix
    if prefix is None:
        return True
    if prefix.endswith("/"):
        return purge_storage_prefix(
            store, prefix=prefix, page_size=policy.listing_page_size
        ).finished
    # Everything else names one exact object rather than a scope, so no listing is
    # needed and no neighbouring key can be caught by it.
    store.delete_object(key=prefix)
    return True


def _purge_rows(session: Session, *, tombstone: DueTombstone) -> None:
    """Remove the durable rows this tombstone's kind is responsible for."""
    if tombstone.entity_kind is RetentionEntityKind.PROJECT:
        purge_project_rows(
            session, workspace_id=tombstone.workspace_id, project_id=tombstone.entity_id
        )
    elif tombstone.entity_kind is RetentionEntityKind.WORKSPACE:
        purge_workspace_rows(session, workspace_id=tombstone.workspace_id)
    elif tombstone.entity_kind is RetentionEntityKind.MULTIPART_UPLOAD:
        session.connection().execute(
            text(
                "DELETE FROM multipart_uploads "
                "WHERE workspace_id = :workspace_id AND id = :entity_id"
            ),
            {"workspace_id": tombstone.workspace_id, "entity_id": tombstone.entity_id},
        )
    elif tombstone.entity_kind is RetentionEntityKind.SOURCE_CONNECTION:
        session.connection().execute(
            text(
                "DELETE FROM source_connection_secrets "
                "WHERE workspace_id = :workspace_id AND connection_id = :entity_id"
            ),
            {"workspace_id": tombstone.workspace_id, "entity_id": tombstone.entity_id},
        )
    elif tombstone.entity_kind is RetentionEntityKind.USER:
        _erase_identity(session, user_id=tombstone.entity_id)
    elif tombstone.entity_kind is RetentionEntityKind.JOB_WORKSPACE:
        remove_job_workspaces(tombstone.entity_id)


def _erase_identity(session: Session, *, user_id: UUID) -> None:
    """Remove what identified one person, keeping the row their history still points at.

    Every audit event, Membership record, and Project this person created references
    their User row, so the row survives with nothing personal left in it: the sign-in
    identities are deleted, and the name and address become a placeholder derived from
    an identifier that was never personal to begin with.
    """
    connection = session.connection()
    connection.execute(
        text("DELETE FROM auth_identities WHERE user_id = :user_id"), {"user_id": user_id}
    )
    connection.execute(
        text("DELETE FROM auth_sessions WHERE user_id = :user_id"), {"user_id": user_id}
    )
    connection.execute(
        text(
            "UPDATE users SET primary_email = :email, display_name = :name, avatar_url = NULL "
            "WHERE id = :user_id"
        ),
        {
            "user_id": user_id,
            "email": f"deleted-{user_id}@deleted.invalid",
            "name": "Deleted account",
        },
    )


def _has_unfinished_work(session: Session, *, tombstone: DueTombstone) -> bool:
    """Report whether anything is still running that this purge would break.

    A Job mid-flight is writing the rows the purge would remove, and a Publication that
    has not settled is still expecting an artifact to exist. Both defer the purge rather
    than cancelling it: the tombstone keeps its date and the next sweep tries again.
    """
    if tombstone.entity_kind not in {
        RetentionEntityKind.PROJECT,
        RetentionEntityKind.WORKSPACE,
    }:
        return False
    jobs = select(Job.id).where(
        Job.workspace_id == tombstone.workspace_id, Job.status.in_(ACTIVE_JOB_STATUSES)
    )
    publications = select(Publication.id).where(
        Publication.workspace_id == tombstone.workspace_id,
        Publication.status.not_in(
            {
                PublicationStatus.PUBLISHED,
                PublicationStatus.PERMANENT_FAILED,
                PublicationStatus.CANCELLED,
            }
        ),
    )
    if tombstone.entity_kind is RetentionEntityKind.PROJECT:
        jobs = jobs.where(Job.project_id == tombstone.entity_id)
    return (
        session.scalars(jobs.limit(1)).first() is not None
        or session.scalars(publications.limit(1)).first() is not None
    )


def _expire_abandoned_uploads(session: Session, *, policy: RetentionPolicy, now: datetime) -> int:
    """Schedule the removal of uploads nobody finished inside the allowed window."""
    deadline = now - policy.delay_for(RetentionEntityKind.MULTIPART_UPLOAD)
    uploads = session.scalars(
        select(MultipartUpload).where(
            MultipartUpload.status.in_(
                (MultipartUploadStatus.PENDING, MultipartUploadStatus.UPLOADING)
            ),
            MultipartUpload.created_at <= deadline,
        )
    )
    scheduled = 0
    for upload in uploads:
        schedule_tombstone(
            session,
            workspace_id=upload.workspace_id,
            entity_kind=RetentionEntityKind.MULTIPART_UPLOAD,
            entity_id=upload.id,
            storage_prefix=upload.storage_key,
            eligible_at=now,
        )
        scheduled += 1
    return scheduled


def _expire_revoked_source_connections(
    session: Session, *, policy: RetentionPolicy, now: datetime
) -> int:
    """Schedule immediate deletion of credentials that no longer authorize anything."""
    connections = session.scalars(
        select(SourceConnection).where(
            SourceConnection.status.in_(
                (SourceConnectionStatus.REVOKED, SourceConnectionStatus.EXPIRED)
            )
        )
    )
    scheduled = 0
    for connection in connections:
        schedule_tombstone(
            session,
            workspace_id=connection.workspace_id,
            entity_kind=RetentionEntityKind.SOURCE_CONNECTION,
            entity_id=connection.id,
            storage_prefix=None,
            eligible_at=policy.eligible_at(RetentionEntityKind.SOURCE_CONNECTION, now=now),
        )
        scheduled += 1
    return scheduled


def _expire_unaccepted_broll(session: Session, *, policy: RetentionPolicy, now: datetime) -> int:
    """Schedule removal of rejected generated drafts and stock nobody chose."""
    from clipah.models import Asset, AssetSourceType, BrollSuggestion

    scheduled = 0
    for kind, source_types in (
        (RetentionEntityKind.GENERATED_DRAFT, (AssetSourceType.GENERATED,)),
        (RetentionEntityKind.STOCK_PREVIEW, (AssetSourceType.STOCK,)),
    ):
        deadline = now - policy.delay_for(kind)
        rows = session.execute(
            select(BrollSuggestion, Asset)
            .join(Asset, Asset.id == BrollSuggestion.asset_id)
            .where(
                BrollSuggestion.status.in_(_UNSELECTED_SUGGESTION_STATUSES),
                BrollSuggestion.asset_id.is_not(None),
                BrollSuggestion.decided_at.is_not(None),
                BrollSuggestion.decided_at <= deadline,
                Asset.source_type.in_(source_types),
            )
        ).all()
        for suggestion, asset in rows:
            schedule_tombstone(
                session,
                workspace_id=suggestion.workspace_id,
                entity_kind=kind,
                entity_id=asset.id,
                storage_prefix=asset.storage_key,
                eligible_at=now,
            )
            scheduled += 1
    return scheduled


def _expire_failed_job_workspaces(
    session: Session, *, policy: RetentionPolicy, now: datetime
) -> int:
    """Schedule cleanup of the temporary directories a failed Job may have left behind."""
    from clipah.models import JobStatus

    deadline = now - policy.delay_for(RetentionEntityKind.JOB_WORKSPACE)
    jobs = session.scalars(
        select(Job).where(
            Job.status == JobStatus.FAILED,
            Job.finished_at.is_not(None),
            Job.finished_at <= deadline,
        )
    )
    scheduled = 0
    for job in jobs:
        schedule_tombstone(
            session,
            workspace_id=job.workspace_id,
            entity_kind=RetentionEntityKind.JOB_WORKSPACE,
            entity_id=job.id,
            storage_prefix=None,
            eligible_at=now,
        )
        scheduled += 1
    return scheduled
