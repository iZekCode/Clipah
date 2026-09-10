"""Secret-safe checkpoints for YouTube's resumable upload protocol."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import SecretStr


class YouTubeResumableError(Exception):
    """A resumable response cannot safely advance the durable checkpoint."""


class YouTubeAmbiguousCompletionError(YouTubeResumableError):
    """YouTube cannot disprove creation after an ambiguous final request."""


class UploadProgress(StrEnum):
    """Safe local interpretation of one resumable upload session."""

    ACTIVE = "active"
    COMPLETE = "complete"
    EXPIRED_RESTARTABLE = "expired_restartable"


@dataclass(frozen=True, slots=True)
class YouTubeUploadContext:
    """Tenant and attempt binding for one encrypted resumable-session URI."""

    workspace_id: UUID
    publication_id: UUID
    attempt: int

    def __post_init__(self) -> None:
        """Reject a context that cannot name one durable provider attempt."""
        if self.attempt < 1:
            raise ValueError("attempt must be positive")


class YouTubeCheckpointVault(Protocol):
    """Encrypted storage boundary for secret resumable-session URIs."""

    def store(self, context: YouTubeUploadContext, session_uri: SecretStr) -> str:
        """Store one session URI and return only an opaque durable reference."""

    def lease(
        self, reference: str, context: YouTubeUploadContext
    ) -> AbstractContextManager[SecretStr]:
        """Lease one URI only to its original tenant-bound attempt."""


@dataclass(frozen=True, slots=True)
class UploadCheckpoint:
    """Durable non-secret progress for one YouTube resumable session."""

    secret_reference: str
    total_bytes: int
    acknowledged_bytes: int
    source_sha256: bytes
    generation: int
    final_request_ambiguous: bool
    progress: UploadProgress = UploadProgress.ACTIVE
    provider_video_id: str | None = None

    def __post_init__(self) -> None:
        """Reject progress that could authorize an unsafe or impossible byte range."""
        if not self.secret_reference or len(self.source_sha256) != 32:
            raise ValueError("checkpoint identity is invalid")
        if self.total_bytes <= 0 or not 0 <= self.acknowledged_bytes <= self.total_bytes:
            raise ValueError("checkpoint byte range is invalid")
        if self.generation < 1:
            raise ValueError("checkpoint generation must be positive")
        if self.progress is UploadProgress.COMPLETE and not self.provider_video_id:
            raise ValueError("completed checkpoint requires a provider video ID")

    def safe_dict(self) -> dict[str, object]:
        """Serialize only progress that is safe for Postgres and audit evidence."""
        return {
            "totalBytes": self.total_bytes,
            "acknowledgedBytes": self.acknowledged_bytes,
            "sourceSha256": self.source_sha256.hex(),
            "generation": self.generation,
            "finalRequestAmbiguous": self.final_request_ambiguous,
        }
