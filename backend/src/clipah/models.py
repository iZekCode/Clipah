"""Central SQLAlchemy declarations for Clipah's foundational durable schema."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
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
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# The B-roll vocabulary is domain data with no persistence of its own, so the ORM borrows
# it rather than restating it. The dependency runs this way round on purpose: schema knows
# about meaning, and the planning modules stay free of SQLAlchemy.
from clipah.brands.models import TemplateKind
from clipah.broll.models import BrollCoverage, BrollSourceType, BrollSuggestionStatus
from clipah.campaigns.models import CampaignLanguage
from clipah.search.models import ExportState, SearchEntityType, SearchLanguage
from clipah.variants.models import HookStrategy, Platform

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
    # Retrieved or generated footage a suggestion may place over the dialogue, and the
    # normalized rendition the editor plays instead of the original.
    BROLL = "broll"
    BROLL_PROXY = "broll_proxy"


class AssetSourceType(StrEnum):
    USER_UPLOAD = "user_upload"
    SOURCE_IMPORT = "source_import"
    DERIVED = "derived"
    GENERATED = "generated"
    STOCK = "stock"


class SourceConnectionProvider(StrEnum):
    """The source a connection authenticates against."""

    YOUTUBE = "youtube"


class SourceConnectionKind(StrEnum):
    """How a connection proves the member's own access to that source."""

    COOKIE = "cookie"


class SourceConnectionStatus(StrEnum):
    """Whether a connection may still be leased for work."""

    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


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


class ClaimVerificationStatus(StrEnum):
    """What a User has said about the evidence behind one claim.

    Clipah never sets anything but the default. A status beyond `unverified` is always a
    person's assertion, recorded with the person who made it.
    """

    UNVERIFIED = "unverified"
    SUPPORTED = "supported"
    DISPUTED = "disputed"
    RETRACTED = "retracted"


class QuotaResource(StrEnum):
    """Metered Workspace budgets that reset with each calendar month."""

    ANALYSES = "analyses"
    STOCK_REQUESTS = "stock_requests"
    GENERATED_IMAGES = "generated_images"
    GENERATED_VIDEOS = "generated_videos"
    GENERATED_SECONDS = "generated_seconds"
    SOCIAL_PUBLICATIONS = "social_publications"


class QuotaReservationStatus(StrEnum):
    """Lifecycle of one budget reservation from estimate to reconciled outcome."""

    RESERVED = "reserved"
    SETTLED = "settled"
    RELEASED = "released"


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


class IdempotencyKey(Base):
    """Persist one Workspace-scoped create response for safe request replay."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "route",
            "key",
            name="uq_idempotency_keys_workspace_route_key",
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
    user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    route: Mapped[str] = mapped_column(String(128), nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


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
        ForeignKeyConstraint(
            ["workspace_id", "source_connection_id"],
            ["source_connections.workspace_id", "source_connections.id"],
            name="fk_source_imports_workspace_connection",
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
    source_connection_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[SourceImportStatus] = mapped_column(
        enum_type(SourceImportStatus, "source_import_status"),
        nullable=False,
        server_default=text("'queued'"),
    )
    job_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SourceConnection(Base):
    """One member's own credential for a source, described without describing the secret."""

    __tablename__ = "source_connections"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_source_connections_workspace_id_id"),
        CheckConstraint("expires_at > consented_at", name="connection_outlives_consent"),
        Index("ix_source_connections_workspace_status", "workspace_id", "status"),
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
    provider: Mapped[SourceConnectionProvider] = mapped_column(
        enum_type(SourceConnectionProvider, "source_connection_provider"), nullable=False
    )
    kind: Mapped[SourceConnectionKind] = mapped_column(
        enum_type(SourceConnectionKind, "source_connection_kind"), nullable=False
    )
    status: Mapped[SourceConnectionStatus] = mapped_column(
        enum_type(SourceConnectionStatus, "source_connection_status"),
        nullable=False,
        server_default=text("'active'"),
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    domain_scope: Mapped[str] = mapped_column(Text, nullable=False)
    secret_reference: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    authorized_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SourceConnectionSecret(Base):
    """The encrypted credential itself, kept apart from everything that names it."""

    __tablename__ = "source_connection_secrets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_source_connection_secrets_workspace_id_id"),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["source_connections.workspace_id", "source_connections.id"],
            name="fk_source_connection_secrets_workspace_connection",
            ondelete="CASCADE",
        ),
        Index(
            "ix_source_connection_secrets_workspace_connection",
            "workspace_id",
            "connection_id",
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
    connection_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    key_reference: Mapped[str] = mapped_column(Text, nullable=False)
    wrapped_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
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
        CheckConstraint(
            "declared_size_bytes > 0 AND declared_size_bytes <= 2147483648",
            name="declared_size_within_initial_media_limit",
        ),
        CheckConstraint(
            "completed_size_bytes IS NULL OR "
            "(completed_size_bytes > 0 AND completed_size_bytes <= 2147483648)",
            name="completed_size_within_initial_media_limit",
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
    storage_upload_id: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    client_filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    declared_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    completed_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
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
        UniqueConstraint("workspace_id", "asset_id", name="uq_transcripts_workspace_asset"),
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
    payoff: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    start_word_id: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("''")
    )
    end_word_id: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("''"))
    transcript_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    context_dependencies: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    context_warnings: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    visual_opportunities: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    model_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AssetProvenance(Base):
    """Where one retrieved or generated asset came from, and under what licence.

    This row is the only record that can answer, a year later, whether a picture in a
    published clip was ever licensed for that use. It is written in the same transaction
    that stores the media, and the plan forbids it from ever carrying a secret.
    """

    __tablename__ = "asset_provenance"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_asset_provenance_workspace_id_id"),
        UniqueConstraint("workspace_id", "asset_id", name="uq_asset_provenance_workspace_asset"),
        ForeignKeyConstraint(
            ["workspace_id", "asset_id"],
            ["assets.workspace_id", "assets.id"],
            name="fk_asset_provenance_workspace_id_asset_id_assets",
            ondelete="CASCADE",
        ),
        Index(
            "ix_asset_provenance_workspace_provider_asset",
            "workspace_id",
            "provider",
            "provider_asset_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    asset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_asset_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(Text, nullable=False)
    author_url: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    license_name: Mapped[str] = mapped_column(Text, nullable=False)
    license_url: Mapped[str] = mapped_column(Text, nullable=False)
    terms_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    # Generation fills these where retrieval leaves them empty, and the reverse.
    prompt: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str | None] = mapped_column(Text)
    seed: Mapped[str | None] = mapped_column(Text)
    moderation_result: Mapped[str] = mapped_column(String(32), nullable=False)
    attribution_text: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BrollPlanRequest(Base):
    """What one admitted planning Job was created to cover, written where it can be read.

    A Job carries identifiers and nothing else across the broker, so the clip and the
    coverage a member asked for are recorded here by the API and read back by the worker.
    """

    __tablename__ = "broll_plan_requests"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_broll_plan_requests_workspace_id_id"),
        UniqueConstraint("workspace_id", "job_id", name="uq_broll_plan_requests_workspace_job"),
        ForeignKeyConstraint(
            ["workspace_id", "candidate_id"],
            ["clip_candidates.workspace_id", "clip_candidates.id"],
            name="fk_broll_plan_requests_workspace_candidate_clip_candidates",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["jobs.workspace_id", "jobs.id"],
            name="fk_broll_plan_requests_workspace_id_job_id_jobs",
            ondelete="RESTRICT",
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
    candidate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    job_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    coverage: Mapped[BrollCoverage] = mapped_column(
        enum_type(BrollCoverage, "broll_coverage"), nullable=False
    )
    requested_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BrollSuggestion(Base):
    """One proposed visual placement, which changes no Edit until a member accepts it."""

    __tablename__ = "broll_suggestions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_broll_suggestions_workspace_id_id"),
        # One plan is identified by its candidate, planner version, and coverage; inside
        # one plan a beat appears once. A replayed planning Job therefore converges on the
        # rows it already wrote instead of proposing the same picture a second time.
        UniqueConstraint(
            "workspace_id",
            "candidate_id",
            "planner_version",
            "coverage",
            "beat_start_word_id",
            name="uq_broll_suggestions_plan_beat",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_broll_suggestions_workspace_id_project_id_projects",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "candidate_id"],
            ["clip_candidates.workspace_id", "clip_candidates.id"],
            name="fk_broll_suggestions_workspace_id_candidate_id_clip_candidates",
            ondelete="RESTRICT",
        ),
        CheckConstraint("start_ms >= 0 AND end_ms > start_ms", name="valid_shot_range"),
        Index("ix_broll_suggestions_workspace_candidate", "workspace_id", "candidate_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    planner_version: Mapped[str] = mapped_column(String(64), nullable=False)
    coverage: Mapped[BrollCoverage] = mapped_column(
        enum_type(BrollCoverage, "broll_coverage"), nullable=False
    )
    beat_start_word_id: Mapped[str] = mapped_column(String(32), nullable=False)
    beat_end_word_id: Mapped[str] = mapped_column(String(32), nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    visual_intent: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    search_terms: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    exclusions: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    status: Mapped[BrollSuggestionStatus] = mapped_column(
        enum_type(BrollSuggestionStatus, "broll_suggestion_status"),
        nullable=False,
        server_default=text("'proposed'"),
    )
    placement_reason: Mapped[str] = mapped_column(Text, nullable=False)
    # Retrieval belongs to a later task: at planning time no source has been chosen and no
    # asset exists, so both stay empty rather than being guessed at now.
    source_type: Mapped[BrollSourceType | None] = mapped_column(
        enum_type(BrollSourceType, "broll_source_type")
    )
    asset_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    edit_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    relevance_score: Mapped[float | None] = mapped_column(Numeric(6, 5))
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClipVariant(Base):
    """One proposed alternative cut of a Clip Candidate, packaged for one destination.

    A Variant changes no Edit. It records a boundary the context rules found honest, the
    strategy that chose its opening, the target it was asked for, and the packaging one
    platform requires — so a member can compare readings of the same moment without any
    of them duplicating the source, the transcript, or a single asset.
    """

    __tablename__ = "clip_variants"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_clip_variants_workspace_id_id"),
        # One candidate yields at most one Variant per strategy, target, and destination.
        # A repeated request therefore converges on the rows it already wrote.
        UniqueConstraint(
            "workspace_id",
            "candidate_id",
            "hook_strategy",
            "target_duration_ms",
            "platform",
            name="uq_clip_variants_candidate_strategy_target_platform",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_clip_variants_workspace_id_project_id_projects",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "candidate_id"],
            ["clip_candidates.workspace_id", "clip_candidates.id"],
            name="fk_clip_variants_workspace_id_candidate_id_clip_candidates",
            ondelete="RESTRICT",
        ),
        CheckConstraint("start_ms >= 0 AND end_ms > start_ms", name="valid_variant_range"),
        CheckConstraint("target_duration_ms > 0", name="positive_variant_target"),
        Index("ix_clip_variants_workspace_candidate", "workspace_id", "candidate_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    hook_strategy: Mapped[HookStrategy] = mapped_column(
        enum_type(HookStrategy, "clip_variant_hook_strategy"), nullable=False
    )
    platform: Mapped[Platform] = mapped_column(
        enum_type(Platform, "clip_variant_platform"), nullable=False
    )
    target_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    start_word_id: Mapped[str] = mapped_column(String(32), nullable=False)
    end_word_id: Mapped[str] = mapped_column(String(32), nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    # The warnings this boundary carried when it was offered, and the packaging its
    # destination asked for. Both are evidence a member decided on, so both are kept.
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    packaging: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ClaimEvidence(Base):
    """What a User said about the source behind a claim, never what Clipah verified.

    Version 1 records evidence and shows it beside the claim as a reviewable citation
    suggestion. Nothing Clipah does sets `verification_status`: only a User may say a
    claim is verified, and the actor who said so is kept beside the assertion.
    """

    __tablename__ = "claim_evidence"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_claim_evidence_workspace_id_id"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_claim_evidence_workspace_id_project_id_projects",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "candidate_id"],
            ["clip_candidates.workspace_id", "clip_candidates.id"],
            name="fk_claim_evidence_workspace_id_candidate_id_clip_candidates",
            ondelete="RESTRICT",
        ),
        Index("ix_claim_evidence_workspace_candidate", "workspace_id", "candidate_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    start_word_id: Mapped[str] = mapped_column(String(32), nullable=False)
    end_word_id: Mapped[str] = mapped_column(String(32), nullable=False)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_title: Mapped[str] = mapped_column(Text, nullable=False)
    publisher: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verification_status: Mapped[ClaimVerificationStatus] = mapped_column(
        enum_type(ClaimVerificationStatus, "claim_verification_status"),
        nullable=False,
        server_default=text("'unverified'"),
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
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


class RenderRequest(Base):
    """What one admitted render Job was created to produce, written where it can be read."""

    __tablename__ = "render_requests"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_render_requests_workspace_id_id"),
        UniqueConstraint("workspace_id", "job_id", name="uq_render_requests_workspace_job"),
        ForeignKeyConstraint(
            ["workspace_id", "clip_edit_revision_id"],
            ["clip_edit_revisions.workspace_id", "clip_edit_revisions.id"],
            name="fk_render_requests_workspace_revision_clip_edit_revisions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["jobs.workspace_id", "jobs.id"],
            name="fk_render_requests_workspace_id_job_id_jobs",
            ondelete="RESTRICT",
        ),
        Index("ix_render_requests_workspace_job", "workspace_id", "job_id"),
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
    clip_edit_revision_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    job_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    preset: Mapped[str] = mapped_column(String(64), nullable=False)
    composition_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
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


class WorkspaceQuotaReservation(Base):
    """One monthly budget charge held against a Workspace until its real cost is known."""

    __tablename__ = "workspace_quota_reservations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "id", name="uq_workspace_quota_reservations_workspace_id_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "resource",
            "reference_kind",
            "reference_id",
            name="uq_workspace_quota_reservations_workspace_resource_reference",
        ),
        CheckConstraint("estimated_units >= 0", name="nonnegative_estimated_units"),
        CheckConstraint(
            "actual_units IS NULL OR actual_units >= 0", name="nonnegative_actual_units"
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
    resource: Mapped[QuotaResource] = mapped_column(
        enum_type(QuotaResource, "quota_resource"), nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[QuotaReservationStatus] = mapped_column(
        enum_type(QuotaReservationStatus, "quota_reservation_status"),
        nullable=False,
        server_default=text("'reserved'"),
    )
    estimated_units: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    actual_units: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    reference_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BrandKit(Base):
    """What one Workspace's brand promises, as an identity its versions hang from.

    The row a member renames and archives is deliberately not the row that holds the
    rules. A Revision records the exact Brand Kit version it was judged against, so a kit
    edited next month must not silently re-judge a clip approved under this month's rules.
    """

    __tablename__ = "brand_kits"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_brand_kits_workspace_id_id"),
        CheckConstraint("current_version > 0", name="positive_brand_kit_version"),
        Index("ix_brand_kits_workspace", "workspace_id"),
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
    name: Mapped[str] = mapped_column(Text, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    # Archived rather than deleted: a Revision that names one of this kit's versions has
    # to keep resolving after somebody stops using the kit.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BrandKitVersion(Base):
    """One immutable published version of a Brand Kit's constraints."""

    __tablename__ = "brand_kit_versions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_brand_kit_versions_workspace_id_id"),
        UniqueConstraint(
            "workspace_id",
            "brand_kit_id",
            "version",
            name="uq_brand_kit_versions_workspace_kit_version",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "brand_kit_id"],
            ["brand_kits.workspace_id", "brand_kits.id"],
            name="fk_brand_kit_versions_workspace_id_brand_kit_id_brand_kits",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version > 0", name="positive_brand_kit_version_number"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    brand_kit_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BrandTemplate(Base):
    """One reusable look a Workspace owns, as an identity its versions hang from."""

    __tablename__ = "brand_templates"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_brand_templates_workspace_id_id"),
        ForeignKeyConstraint(
            ["workspace_id", "brand_kit_id"],
            ["brand_kits.workspace_id", "brand_kits.id"],
            name="fk_brand_templates_workspace_id_brand_kit_id_brand_kits",
            ondelete="RESTRICT",
        ),
        CheckConstraint("current_version > 0", name="positive_template_version"),
        Index("ix_brand_templates_workspace", "workspace_id"),
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
    brand_kit_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[TemplateKind] = mapped_column(
        enum_type(TemplateKind, "brand_template_kind"), nullable=False
    )
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BrandTemplateVersion(Base):
    """One immutable published version of a Workspace-owned look."""

    __tablename__ = "brand_template_versions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_brand_template_versions_workspace_id_id"),
        UniqueConstraint(
            "workspace_id",
            "template_id",
            "version",
            name="uq_brand_template_versions_workspace_template_version",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "template_id"],
            ["brand_templates.workspace_id", "brand_templates.id"],
            name="fk_brand_template_versions_workspace_id_template_id_templates",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version > 0", name="positive_template_version_number"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    template_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CampaignOutput(Base):
    """Supporting copy derived from one immutable Edit Revision, for one destination.

    A Campaign Output is not a Publication and never becomes one on its own: it is copy a
    member reads, edits elsewhere, and decides about. It is bound to the Revision it was
    derived from, so copy can never outlive the cut it describes.
    """

    __tablename__ = "campaign_outputs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_campaign_outputs_workspace_id_id"),
        UniqueConstraint(
            "workspace_id",
            "clip_edit_revision_id",
            "platform",
            "language",
            name="uq_campaign_outputs_revision_platform_language",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_campaign_outputs_workspace_id_project_id_projects",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "clip_edit_revision_id"],
            ["clip_edit_revisions.workspace_id", "clip_edit_revisions.id"],
            name="fk_campaign_outputs_workspace_id_revision_id_revisions",
            ondelete="RESTRICT",
        ),
        Index("ix_campaign_outputs_workspace_revision", "workspace_id", "clip_edit_revision_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    clip_edit_revision_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    platform: Mapped[Platform] = mapped_column(
        enum_type(Platform, "clip_variant_platform"), nullable=False
    )
    language: Mapped[CampaignLanguage] = mapped_column(
        enum_type(CampaignLanguage, "campaign_language"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    post_copy: Mapped[str] = mapped_column(Text, nullable=False)
    cta: Mapped[str] = mapped_column(Text, nullable=False)
    hashtags: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    thumbnail_brief: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # The caveats this clip carried when the copy was derived. Copy and caveat are read
    # together or the caveat may as well not exist.
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    model_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SearchDocument(Base):
    """One derived, searchable record of a single piece of a Workspace's work.

    This is the only tenant table that holds no authority: every column is recomputed
    from a Project, a Transcript, a Clip Candidate, or a Campaign Output. It is a cache
    with a schema, and it may be dropped and rebuilt without losing anything.
    """

    __tablename__ = "search_documents"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_search_documents_workspace_id_id"),
        UniqueConstraint(
            "workspace_id",
            "entity_type",
            "entity_id",
            "segment_ordinal",
            name="uq_search_documents_workspace_entity_segment",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_search_documents_workspace_id_project_id_projects",
            ondelete="CASCADE",
        ),
        CheckConstraint("segment_ordinal >= 0", name="nonnegative_segment_ordinal"),
        CheckConstraint(
            "(start_ms IS NULL AND end_ms IS NULL) OR (start_ms >= 0 AND end_ms > start_ms)",
            name="valid_document_time_range",
        ),
        Index("ix_search_documents_workspace_project", "workspace_id", "project_id"),
        Index(
            "ix_search_documents_workspace_type_created",
            "workspace_id",
            "entity_type",
            "source_created_at",
        ),
        Index("ix_search_documents_vector", "search_vector", postgresql_using="gin"),
        Index(
            "ix_search_documents_title_trgm",
            "title_normalized",
            postgresql_using="gin",
            postgresql_ops={"title_normalized": "gin_trgm_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    entity_type: Mapped[SearchEntityType] = mapped_column(
        enum_type(SearchEntityType, "search_entity_type"), nullable=False
    )
    entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    anchor_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    segment_ordinal: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    title_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    speaker: Mapped[str | None] = mapped_column(Text)
    topics: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    language: Mapped[SearchLanguage] = mapped_column(
        enum_type(SearchLanguage, "search_language"), nullable=False
    )
    start_ms: Mapped[int | None] = mapped_column(Integer)
    end_ms: Mapped[int | None] = mapped_column(Integer)
    export_state: Mapped[ExportState] = mapped_column(
        enum_type(ExportState, "search_export_state"), nullable=False
    )
    source_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    search_vector: Mapped[str] = mapped_column(TSVECTOR, nullable=False)


# Explicit tenant-leading indexes for the tables whose primary/unique keys do not
# already cover the most common actor-oriented access path.
Index(
    "ix_workspace_quota_reservations_workspace_resource_period",
    WorkspaceQuotaReservation.workspace_id,
    WorkspaceQuotaReservation.resource,
    WorkspaceQuotaReservation.period_start,
)
Index(
    "ix_workspace_memberships_workspace_id_role",
    WorkspaceMembership.workspace_id,
    WorkspaceMembership.role,
)
