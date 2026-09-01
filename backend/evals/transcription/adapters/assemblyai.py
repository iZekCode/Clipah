"""Live AssemblyAI adapter for the transcription evaluation.

This adapter is never exercised by ordinary CI. It requires `CLIPAH_ASSEMBLYAI_API_KEY` and a
local directory of evaluation audio, because evaluation audio is deliberately not checked into
the repository. It reuses the production adapter so the evaluation measures the same code path
the product runs, not a second implementation of it.
"""

from __future__ import annotations

import time
from pathlib import Path

from clipah.assets.storage import StoredObject
from clipah.transcripts.assemblyai_adapter import AssemblyAITranscriber
from clipah.transcripts.evaluation import TranscriptionCase, TranscriptionObservation
from clipah.transcripts.provider import (
    TranscriptionProviderRetryableError,
    TranscriptionProviderTerminalError,
)

ADAPTER_NAME = "assemblyai"
PROVIDER = "assemblyai"
MODEL = "universal"

# List price per source hour in USD micros at the time of writing. Re-check before any
# provider decision is frozen; the report records what was assumed, not what was invoiced.
COST_MICROS_PER_SOURCE_HOUR = 270_000

_MS_PER_HOUR = 3_600_000


class AssemblyAIEvaluationTranscriber:
    """Measure one case with the production AssemblyAI adapter over local audio."""

    name = ADAPTER_NAME
    provider = PROVIDER
    model = MODEL

    def __init__(self, *, api_key: str, audio_directory: Path, provider_version: str) -> None:
        """Bind the credential, the local audio directory, and the SDK version measured."""
        self.provider_version = provider_version
        self._audio_directory = audio_directory
        self._transcriber = AssemblyAITranscriber(
            api_key=api_key,
            audio_url_resolver=lambda audio: str(self._audio_directory / Path(audio.key).name),
        )

    def observe(self, case: TranscriptionCase) -> TranscriptionObservation:
        """Transcribe one case's local audio, recording latency and assumed cost."""
        audio_path = _audio_path(self._audio_directory, case)
        started = time.monotonic()
        try:
            transcript = self._transcriber.transcribe(
                audio=StoredObject(
                    key=audio_path.name,
                    content_type="audio/wav",
                    content_length=audio_path.stat().st_size,
                    duration_ms=case.duration_ms,
                ),
                language=None if case.language == "mixed" else case.language,
            )
        except (
            TranscriptionProviderRetryableError,
            TranscriptionProviderTerminalError,
        ) as error:
            return TranscriptionObservation(
                case_id=case.case_id,
                transcript=None,
                error_code=error.code,
                latency_ms=_elapsed_ms(started),
                cost_micros=0,
            )
        return TranscriptionObservation(
            case_id=case.case_id,
            transcript=transcript,
            error_code=None,
            latency_ms=_elapsed_ms(started),
            cost_micros=estimated_cost_micros(case, COST_MICROS_PER_SOURCE_HOUR),
        )


def _audio_path(directory: Path, case: TranscriptionCase) -> Path:
    """Locate one case's evaluation audio, which is provided out of band."""
    path = directory / f"{case.case_id}.wav"
    if not path.is_file():
        raise FileNotFoundError(f"evaluation audio for {case.case_id} is missing at {path}")
    return path


def _elapsed_ms(started: float) -> int:
    """Measure completion time without reading the wall clock."""
    return int((time.monotonic() - started) * 1_000)


def estimated_cost_micros(case: TranscriptionCase, per_source_hour: int) -> int:
    """Price one case from its source duration and the adapter's recorded list price."""
    return round(case.duration_ms * per_source_hour / _MS_PER_HOUR)
