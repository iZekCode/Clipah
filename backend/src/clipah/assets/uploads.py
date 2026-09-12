"""Durable Workspace-scoped multipart upload transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from clipah.assets.keys import source_upload_key
from clipah.assets.storage import (
    CompletedPart,
    MultipartCompletionError,
    ObjectStore,
    SignedUrl,
    StoredObject,
)
from clipah.models import MultipartUpload as MultipartUploadRecord
from clipah.models import MultipartUploadStatus, Project
from clipah.workspaces.models import WorkspaceAccess

MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
MULTIPART_UPLOAD_TTL = timedelta(hours=24)
SIGNED_URL_TTL = timedelta(minutes=5)
MAX_PART_NUMBER = 10_000


class UploadNotFoundError(Exception):
    """Raised when an upload is absent from the authorized Project boundary."""


class UploadConflictError(Exception):
    """Raised when a terminal or expired upload transition cannot be repeated."""


class UploadValidationError(Exception):
    """Raised when declared or observed upload data fails a fixed media boundary."""


@dataclass(frozen=True)
class CreateUploadCommand:
    """Validated display and content metadata used to create one source upload."""

    filename: str
    content_type: str
    content_length: int


@dataclass(frozen=True)
class UploadCreated:
    """Opaque public upload identifier returned after durable storage intent is recorded."""

    upload_id: UUID
    expires_at: datetime


@dataclass(frozen=True)
class UploadCompleted:
    """Public completion outcome with one five-minute private download capability."""

    upload_id: UUID
    object: StoredObject
    download: SignedUrl


def create_upload(
    session: Session,
    store: ObjectStore,
    *,
    access: WorkspaceAccess,
    project_id: UUID,
    command: CreateUploadCommand,
    now: datetime,
) -> UploadCreated:
    """Create one bounded source upload after resolving its Project in the authorized Workspace."""
    _validate_declared_size(command.content_length)
    _validate_filename(command.filename)
    _require_active_project(session, workspace_id=access.workspace_id, project_id=project_id)
    upload_id = uuid4()
    storage_key = source_upload_key(
        workspace_id=access.workspace_id,
        project_id=project_id,
        object_id=upload_id,
        client_filename=command.filename,
    )
    storage_upload = store.create_multipart_upload(
        key=storage_key, content_type=command.content_type
    )
    expires_at = now + MULTIPART_UPLOAD_TTL
    session.add(
        MultipartUploadRecord(
            id=upload_id,
            workspace_id=access.workspace_id,
            project_id=project_id,
            storage_upload_id=storage_upload.upload_id,
            storage_key=storage_key,
            status=MultipartUploadStatus.PENDING,
            client_filename=command.filename,
            content_type=command.content_type,
            declared_size_bytes=command.content_length,
            expires_at=expires_at,
        )
    )
    session.flush()
    return UploadCreated(upload_id=upload_id, expires_at=expires_at)


def sign_part(
    session: Session,
    store: ObjectStore,
    *,
    access: WorkspaceAccess,
    project_id: UUID,
    upload_id: UUID,
    part_number: int,
    now: datetime,
) -> SignedUrl:
    """Sign one valid part after expiring and cleaning only the matching durable record."""
    _validate_part_number(part_number)
    upload = _load_upload(
        session, workspace_id=access.workspace_id, project_id=project_id, upload_id=upload_id
    )
    _expire_if_needed(upload, store=store, now=now)
    if upload.status not in (MultipartUploadStatus.PENDING, MultipartUploadStatus.UPLOADING):
        raise UploadConflictError("upload cannot accept more parts")
    upload.status = MultipartUploadStatus.UPLOADING
    return store.sign_upload_part(
        upload_id=upload.storage_upload_id, key=upload.storage_key, part_number=part_number
    )


def complete_upload(
    session: Session,
    store: ObjectStore,
    *,
    access: WorkspaceAccess,
    project_id: UUID,
    upload_id: UUID,
    parts: list[CompletedPart],
    now: datetime,
) -> UploadCompleted:
    """Complete an active upload and verify final object metadata before exposing its URL."""
    _validate_parts(parts)
    upload = _load_upload(
        session, workspace_id=access.workspace_id, project_id=project_id, upload_id=upload_id
    )
    _expire_if_needed(upload, store=store, now=now)
    if upload.status == MultipartUploadStatus.COMPLETED:
        raise UploadConflictError("upload already completed")
    if upload.status not in (MultipartUploadStatus.PENDING, MultipartUploadStatus.UPLOADING):
        raise UploadConflictError("upload cannot be completed")
    try:
        stored = store.complete_multipart_upload(
            upload_id=upload.storage_upload_id,
            key=upload.storage_key,
            parts=sorted(parts, key=lambda part: part.part_number),
        )
    except MultipartCompletionError as error:
        raise UploadValidationError("invalid multipart completion") from error
    observed = store.head_object(key=upload.storage_key)
    if (
        stored.key != upload.storage_key
        or observed.key != upload.storage_key
        or observed.content_length != upload.declared_size_bytes
        or observed.content_length > MAX_UPLOAD_BYTES
    ):
        store.delete_object(key=upload.storage_key)
        upload.status = MultipartUploadStatus.ABORTED
        raise UploadValidationError("stored object metadata does not match declaration")
    upload.status = MultipartUploadStatus.COMPLETED
    upload.completed_size_bytes = observed.content_length
    download = store.sign_download(key=upload.storage_key, expires_in=SIGNED_URL_TTL)
    return UploadCompleted(upload_id=upload.id, object=observed, download=download)


def abort_upload(
    session: Session,
    store: ObjectStore,
    *,
    access: WorkspaceAccess,
    project_id: UUID,
    upload_id: UUID,
    now: datetime,
) -> None:
    """Abort an active upload by using only its exact recorded provider ID and key."""
    upload = _load_upload(
        session, workspace_id=access.workspace_id, project_id=project_id, upload_id=upload_id
    )
    _expire_if_needed(upload, store=store, now=now)
    if upload.status not in (MultipartUploadStatus.PENDING, MultipartUploadStatus.UPLOADING):
        raise UploadConflictError("upload cannot be aborted")
    store.abort_multipart_upload(upload_id=upload.storage_upload_id, key=upload.storage_key)
    upload.status = MultipartUploadStatus.ABORTED


def _require_active_project(session: Session, *, workspace_id: UUID, project_id: UUID) -> None:
    """Hide missing, archived, and cross-Workspace Projects behind the same absence result."""
    project = session.scalar(
        select(Project.id)
        .where(
            Project.workspace_id == workspace_id,
            Project.id == project_id,
            Project.archived_at.is_(None),
        )
        .with_for_update()
    )
    if project is None:
        raise UploadNotFoundError("project unavailable")


def _load_upload(
    session: Session, *, workspace_id: UUID, project_id: UUID, upload_id: UUID
) -> MultipartUploadRecord:
    """Lock one active Project upload before any mutable storage transition begins."""
    upload = session.scalar(
        select(MultipartUploadRecord)
        .join(
            Project,
            and_(
                Project.workspace_id == MultipartUploadRecord.workspace_id,
                Project.id == MultipartUploadRecord.project_id,
            ),
        )
        .where(
            MultipartUploadRecord.workspace_id == workspace_id,
            MultipartUploadRecord.project_id == project_id,
            MultipartUploadRecord.id == upload_id,
            Project.archived_at.is_(None),
        )
        .with_for_update()
    )
    if upload is None:
        raise UploadNotFoundError("upload unavailable")
    return upload


def _expire_if_needed(upload: MultipartUploadRecord, *, store: ObjectStore, now: datetime) -> None:
    """Transition overdue active rows and abort exactly their recorded provider upload target."""
    if (
        upload.status in (MultipartUploadStatus.PENDING, MultipartUploadStatus.UPLOADING)
        and now >= upload.expires_at
    ):
        store.abort_multipart_upload(upload_id=upload.storage_upload_id, key=upload.storage_key)
        upload.status = MultipartUploadStatus.EXPIRED
    if upload.status == MultipartUploadStatus.EXPIRED:
        raise UploadConflictError("upload expired")


def _validate_declared_size(content_length: int) -> None:
    """Enforce the initial-release 2 GiB source-media boundary before storage allocation."""
    if not 0 < content_length <= MAX_UPLOAD_BYTES:
        raise UploadValidationError("content length outside allowed range")


def _validate_filename(filename: str) -> None:
    """Refuse a display filename carrying characters that are not display at all.

    The filename never reaches the object key, but it is kept and shown back, so a control
    character in it is only ever useful somewhere else: a NUL cannot be stored in Postgres
    text, and a newline is how one value is smuggled into a second log line or header.
    """
    if not filename.isprintable():
        raise UploadValidationError("display filename contains control characters")


def _validate_part_number(part_number: int) -> None:
    """Reject S3-invalid part numbers before issuing any provider capability URL."""
    if not 1 <= part_number <= MAX_PART_NUMBER:
        raise UploadValidationError("part number outside allowed range")


def _validate_parts(parts: list[CompletedPart]) -> None:
    """Require a nonempty unique bounded sequence before provider completion is attempted."""
    if not parts:
        raise UploadValidationError("at least one completed part is required")
    numbers = [part.part_number for part in parts]
    if len(numbers) != len(set(numbers)):
        raise UploadValidationError("completed parts must be unique")
    for part_number in numbers:
        _validate_part_number(part_number)
