"""Provider-neutral contracts for public remote-video source imports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address
from pathlib import Path
from typing import ClassVar, Protocol

from clipah.assets.storage import StoredObject

MAX_SOURCE_DURATION_SECONDS = 4 * 60 * 60
MAX_SOURCE_SIZE_BYTES = 2 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class NormalizedYouTubeUrl:
    """One allowlisted YouTube video identity and its validated DNS observation."""

    canonical_url: str
    video_id: str
    host: str
    addresses: frozenset[IPv4Address | IPv6Address]


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    """Provider metadata reduced to fields the source-import domain actually needs."""

    video_id: str
    duration_seconds: int
    content_type: str
    extension: str


class SourceImportError(Exception):
    """A sanitized source-import refusal carrying only a stable public code."""

    code: ClassVar[str]
    retryable: ClassVar[bool] = False

    def __init__(self) -> None:
        """Discard provider detail by constructing errors from their fixed code alone."""
        super().__init__(self.code)


class SourceUnsupportedError(SourceImportError):
    """The requested source form or destination violates the public-import policy."""

    code = "SOURCE_UNSUPPORTED"


class SourcePrivateError(SourceImportError):
    """The provider reports media that requires private or age-gated access."""

    code = "SOURCE_PRIVATE"


class SourceTooLongError(SourceImportError):
    """The provider duration exceeds the first-release source limit."""

    code = "SOURCE_TOO_LONG"


class SourceTlsError(SourceImportError):
    """Verified TLS could not be established for the public source."""

    code = "SOURCE_TLS_FAILED"


class SourceUnavailableError(SourceImportError):
    """The public provider is temporarily unreachable and may succeed on retry."""

    code = "SOURCE_UNAVAILABLE"
    retryable = True


class PoTokenProvider(Protocol):
    """Optional future port for a separately gated proof-of-origin token adapter."""

    def token_for(self, *, video_id: str) -> str | None:
        """Return an ephemeral token when an explicitly enabled adapter can provide one."""


class SourceImporter(Protocol):
    """Import one already-normalized public source into a server-selected object key."""

    def import_source(
        self,
        source: NormalizedYouTubeUrl,
        *,
        workspace: Path,
        object_key: str,
        cancellation_check: Callable[[], None],
    ) -> StoredObject:
        """Download, validate, and store one source without exposing provider payloads."""
