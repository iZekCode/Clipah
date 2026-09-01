"""Provider-neutral transcription port, fixed errors, and deterministic fake."""

from __future__ import annotations

from typing import Protocol

from clipah.assets.storage import StoredObject
from clipah.transcripts.models import TranscriptResult


class TranscriptionProviderRetryableError(Exception):
    """Represent a temporary transcription boundary failure through a stable code."""

    def __init__(self, code: str) -> None:
        """Retain only public-safe retry information."""
        self.code = code
        super().__init__(code)


class TranscriptionProviderTerminalError(Exception):
    """Represent a permanent transcription boundary failure through a stable code."""

    def __init__(self, code: str) -> None:
        """Retain only public-safe terminal information."""
        self.code = code
        super().__init__(code)


class Transcriber(Protocol):
    """Provider-independent one-pass transcription capability."""

    def transcribe(self, *, audio: StoredObject, language: str | None) -> TranscriptResult:
        """Return one normalized transcript for the exact private audio object."""


class FakeTranscriber:
    """Deterministic Transcriber used where real provider work would be inappropriate."""

    def __init__(
        self,
        *,
        result: TranscriptResult | None = None,
        error: Exception | None = None,
    ) -> None:
        """Bind exactly one result or error and begin with no calls."""
        self.result = result
        self.error = error
        self.calls: list[tuple[StoredObject, str | None]] = []

    def transcribe(self, *, audio: StoredObject, language: str | None) -> TranscriptResult:
        """Record one exact request before returning the configured behavior."""
        self.calls.append((audio, language))
        if self.error is not None:
            raise self.error
        if self.result is None:
            raise RuntimeError("FakeTranscriber requires a result or error")
        return self.result
