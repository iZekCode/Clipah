"""Central SQLAlchemy declarations for Clipah's foundational durable schema."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Single metadata registry for production and migration comparisons."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    DELETED = "deleted"


class WorkspaceKind(StrEnum):
    PERSONAL = "personal"
    TEAM = "team"


class PublishingRolePolicy(StrEnum):
    OWNER_ADMIN_EDITOR = "owner_admin_editor"
    OWNER_ADMIN = "owner_admin"


class WorkspaceStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class WorkspaceRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


class ProjectStatus(StrEnum):
    CREATED = "created"
    UPLOADING = "uploading"
    INGESTING = "ingesting"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    READY = "ready"
    FAILED = "failed"
    ARCHIVED = "archived"


class SourceKind(StrEnum):
    UPLOAD = "upload"
    PUBLIC_URL = "public_url"
    AUTHENTICATED_SOURCE = "authenticated_source"


class AssetKind(StrEnum):
    SOURCE = "source"
    PROXY = "proxy"
    THUMBNAIL = "thumbnail"
    WAVEFORM = "waveform"
    TRANSCRIPTION_AUDIO = "transcription_audio"
    RENDER = "render"


class AssetSourceType(StrEnum):
    USER_UPLOAD = "user_upload"
    SOURCE_IMPORT = "source_import"
    DERIVED = "derived"
    GENERATED = "generated"


class SourceImportStatus(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class MultipartUploadStatus(StrEnum):
    PENDING = "pending"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    ABORTED = "aborted"
    EXPIRED = "expired"


class JobKind(StrEnum):
    SOURCE_IMPORT = "source_import"
    INGEST = "ingest"
    TRANSCRIBE = "transcribe"
    ANALYZE = "analyze"
    BROLL_PLAN = "broll_plan"
    BROLL_RETRIEVE = "broll_retrieve"
    BROLL_GENERATE = "broll_generate"
    RENDER = "render"
    CAMPAIGN_GENERATE = "campaign_generate"
    SOCIAL_RENDITION = "social_rendition"
    SOCIAL_PUBLISH = "social_publish"
    SOCIAL_RECONCILE = "social_reconcile"
    CLEANUP = "cleanup"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


def enum_type(enum_class: type[StrEnum], name: str) -> SAEnum:
    """Persist stable enum values rather than Python member names."""
    return SAEnum(
        enum_class,
        name=name,
        values_callable=lambda members: [member.value for member in members],
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    primary_email: Mapped[str] = mapped_column(CITEXT(), nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[UserStatus] = mapped_column(
        enum_type(UserStatus, "user_status"), nullable=False, server_default=text("'active'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthIdentity(Base):
    __tablename__ = "auth_identities"
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_auth_identities_issuer_subject"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    issuer: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    email_at_provider: Mapped[str | None] = mapped_column(CITEXT())
    email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint("idle_expires_at > created_at", name="idle_expiry_after_creation"),
        CheckConstraint("absolute_expires_at > created_at", name="absolute_expiry_after_creation"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recent_auth_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    user_agent_summary: Mapped[str | None] = mapped_column(Text)


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(CITEXT(), nullable=False, unique=True)
    kind: Mapped[WorkspaceKind] = mapped_column(
        enum_type(WorkspaceKind, "workspace_kind"), nullable=False
    )
    publishing_role_policy: Mapped[PublishingRolePolicy] = mapped_column(
        enum_type(PublishingRolePolicy, "publishing_role_policy"),
        nullable=False,
        server_default=text("'owner_admin_editor'"),
    )
    status: Mapped[WorkspaceStatus] = mapped_column(
        enum_type(WorkspaceStatus, "workspace_status"),
        nullable=False,
        server_default=text("'active'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkspaceMembership(Base):
    __tablename__ = "workspace_memberships"

    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    role: Mapped[WorkspaceRole] = mapped_column(
        enum_type(WorkspaceRole, "workspace_role"), nullable=False
    )
    invited_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkspaceInvite(Base):
    __tablename__ = "workspace_invites"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_workspace_invites_workspace_id_id"),
        UniqueConstraint("token_hash", name="uq_workspace_invites_token_hash"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    email: Mapped[str] = mapped_column(CITEXT(), nullable=False)
    role: Mapped[WorkspaceRole] = mapped_column(
        enum_type(WorkspaceRole, "workspace_role"), nullable=False
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("workspace_id", "id", name="uq_projects_workspace_id_id"),)

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ProjectStatus] = mapped_column(
        enum_type(ProjectStatus, "project_status"), nullable=False, server_default=text("'created'")
    )
    source_kind: Mapped[SourceKind] = mapped_column(
        enum_type(SourceKind, "source_kind"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_assets_workspace_id_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_assets_workspace_id_project_id_projects",
            ondelete="RESTRICT",
        ),
        CheckConstraint("size_bytes >= 0", name="nonnegative_size"),
        CheckConstraint("duration_ms IS NULL OR duration_ms > 0", name="positive_duration"),
        CheckConstraint("width IS NULL OR width > 0", name="positive_width"),
        CheckConstraint("height IS NULL OR height > 0", name="positive_height"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    kind: Mapped[AssetKind] = mapped_column(enum_type(AssetKind, "asset_kind"), nullable=False)
    source_type: Mapped[AssetSourceType] = mapped_column(
        enum_type(AssetSourceType, "asset_source_type"), nullable=False
    )
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    video_codec: Mapped[str | None] = mapped_column(Text)
    audio_codec: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_jobs_workspace_id_id"),
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_jobs_workspace_id_idempotency_key"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_jobs_workspace_id_project_id_projects",
            ondelete="RESTRICT",
        ),
        CheckConstraint("progress >= 0 AND progress <= 1", name="progress_range"),
        CheckConstraint("attempt >= 0", name="nonnegative_attempt"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    kind: Mapped[JobKind] = mapped_column(enum_type(JobKind, "job_kind"), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        enum_type(JobStatus, "job_status"), nullable=False, server_default=text("'queued'")
    )
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    progress: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, server_default=text("0"))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceImport(Base):
    __tablename__ = "source_imports"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_source_imports_workspace_id_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_source_imports_workspace_id_project_id_projects",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["jobs.workspace_id", "jobs.id"],
            name="fk_source_imports_workspace_id_job_id_jobs",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    normalized_source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_video_id: Mapped[str] = mapped_column(Text, nullable=False)
    authorization_attested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[SourceImportStatus] = mapped_column(
        enum_type(SourceImportStatus, "source_import_status"),
        nullable=False,
        server_default=text("'queued'"),
    )
    job_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MultipartUpload(Base):
    __tablename__ = "multipart_uploads"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_multipart_uploads_workspace_id_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_multipart_uploads_workspace_id_project_id_projects",
            ondelete="RESTRICT",
        ),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    storage_upload_id: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[MultipartUploadStatus] = mapped_column(
        enum_type(MultipartUploadStatus, "multipart_upload_status"),
        nullable=False,
        server_default=text("'pending'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JobEvent(Base):
    __tablename__ = "job_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_job_events_workspace_id_id"),
        UniqueConstraint(
            "workspace_id", "job_id", "sequence", name="uq_job_events_workspace_id_job_sequence"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["jobs.workspace_id", "jobs.id"],
            name="fk_job_events_workspace_id_job_id_jobs",
            ondelete="RESTRICT",
        ),
        CheckConstraint("sequence > 0", name="positive_sequence"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    job_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Transcript(Base):
    __tablename__ = "transcripts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_transcripts_workspace_id_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_transcripts_workspace_id_project_id_projects",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "asset_id"],
            ["assets.workspace_id", "assets.id"],
            name="fk_transcripts_workspace_id_asset_id_assets",
            ondelete="RESTRICT",
        ),
        CheckConstraint("duration_ms > 0", name="positive_duration"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    asset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    language: Mapped[str] = mapped_column(String(32), nullable=False)
    full_text: Mapped[str] = mapped_column(Text, nullable=False)
    words: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    speaker_segments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    utterances: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_result_storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ClipCandidate(Base):
    __tablename__ = "clip_candidates"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_clip_candidates_workspace_id_id"),
        UniqueConstraint(
            "workspace_id", "project_id", "rank", name="uq_clip_candidates_workspace_project_rank"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_clip_candidates_workspace_id_project_id_projects",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "transcript_id"],
            ["transcripts.workspace_id", "transcripts.id"],
            name="fk_clip_candidates_workspace_id_transcript_id_transcripts",
            ondelete="RESTRICT",
        ),
        CheckConstraint("rank > 0", name="positive_rank"),
        CheckConstraint("score >= 0 AND score <= 1", name="score_range"),
        CheckConstraint("start_ms >= 0 AND end_ms > start_ms", name="valid_time_range"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    transcript_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    hook: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    transcript_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    context_warnings: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    visual_opportunities: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    model_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ClipEdit(Base):
    __tablename__ = "clip_edits"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_clip_edits_workspace_id_id"),
        ForeignKeyConstraint(
            ["workspace_id", "candidate_id"],
            ["clip_candidates.workspace_id", "clip_candidates.id"],
            name="fk_clip_edits_workspace_id_candidate_id_clip_candidates",
            ondelete="RESTRICT",
        ),
        CheckConstraint("current_revision > 0", name="positive_current_revision"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ClipEditRevision(Base):
    __tablename__ = "clip_edit_revisions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_clip_edit_revisions_workspace_id_id"),
        UniqueConstraint(
            "workspace_id",
            "clip_edit_id",
            "revision",
            name="uq_clip_edit_revisions_workspace_edit_revision",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "clip_edit_id"],
            ["clip_edits.workspace_id", "clip_edits.id"],
            name="fk_clip_edit_revisions_workspace_id_clip_edit_id_clip_edits",
            ondelete="RESTRICT",
        ),
        CheckConstraint("revision > 0", name="positive_revision"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    clip_edit_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    composition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    composition_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RenderArtifact(Base):
    __tablename__ = "render_artifacts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_render_artifacts_workspace_id_id"),
        UniqueConstraint(
            "workspace_id",
            "composition_hash",
            "preset",
            name="uq_render_artifacts_workspace_composition_preset",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "clip_edit_revision_id"],
            ["clip_edit_revisions.workspace_id", "clip_edit_revisions.id"],
            name="fk_render_artifacts_workspace_revision_clip_edit_revisions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["jobs.workspace_id", "jobs.id"],
            name="fk_render_artifacts_workspace_id_job_id_jobs",
            ondelete="RESTRICT",
        ),
        CheckConstraint("size_bytes >= 0", name="nonnegative_size"),
        CheckConstraint("duration_ms > 0", name="positive_duration"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    clip_edit_revision_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    job_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    preset: Mapped[str] = mapped_column(String(64), nullable=False)
    composition_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_audit_events_workspace_id_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    before_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProviderUsage(Base):
    __tablename__ = "provider_usage"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_provider_usage_workspace_id_id"),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["jobs.workspace_id", "jobs.id"],
            name="fk_provider_usage_workspace_id_job_id_jobs",
            ondelete="RESTRICT",
        ),
        CheckConstraint("input_units >= 0", name="nonnegative_input_units"),
        CheckConstraint("output_units >= 0", name="nonnegative_output_units"),
        CheckConstraint("estimated_cost_usd >= 0", name="nonnegative_estimated_cost"),
        CheckConstraint(
            "actual_cost_usd IS NULL OR actual_cost_usd >= 0", name="nonnegative_actual_cost"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    model_or_api_version: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    input_units: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    output_units: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    estimated_cost_usd: Mapped[float] = mapped_column(
        Numeric(14, 6), nullable=False, server_default=text("0")
    )
    actual_cost_usd: Mapped[float | None] = mapped_column(Numeric(14, 6))
    job_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RetentionTombstone(Base):
    __tablename__ = "retention_tombstones"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_retention_tombstones_workspace_id_id"),
        UniqueConstraint(
            "workspace_id",
            "entity_kind",
            "entity_id",
            name="uq_retention_tombstones_workspace_entity",
        ),
        CheckConstraint("failure_count >= 0", name="nonnegative_failure_count"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    entity_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    storage_prefix: Mapped[str | None] = mapped_column(Text)
    eligible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# Explicit tenant-leading indexes for the tables whose primary/unique keys do not
# already cover the most common actor-oriented access path.
Index(
    "ix_workspace_memberships_workspace_id_role",
    WorkspaceMembership.workspace_id,
    WorkspaceMembership.role,
)
