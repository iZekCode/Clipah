"""Live Deepgram Nova-3 challenger adapter for the transcription evaluation.

Deepgram is a challenger, not a dependency: nothing in `src/clipah` imports this module, and
ordinary CI never runs it. It speaks the REST API directly through httpx so the backend gains
no new package for a comparison it may never adopt, and it normalizes through the same
transcript rules AssemblyAI is held to, so the two reports are comparable.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx

from clipah.transcripts.evaluation import TranscriptionCase, TranscriptionObservation
from clipah.transcripts.models import RawUtterance, RawWord
from clipah.transcripts.use_cases import TranscriptValidationError, normalize_transcript

ADAPTER_NAME = "deepgram"
PROVIDER = "deepgram"
MODEL = "nova-3"
API_URL = "https://api.deepgram.com/v1/listen"
API_KEY_VARIABLE = "CLIPAH_EVAL_DEEPGRAM_API_KEY"
REQUEST_TIMEOUT_SECONDS = 600.0

# List price per source hour in USD micros at the time of writing. Re-check before any
# provider decision is frozen; the report records what was assumed, not what was invoiced.
COST_MICROS_PER_SOURCE_HOUR = 258_000

_MS_PER_HOUR = 3_600_000
_MS_PER_SECOND = 1_000


class DeepgramEvaluationTranscriber:
    """Measure one case with Deepgram Nova-3 word timings and diarization."""

    name = ADAPTER_NAME
    provider = PROVIDER
    model = MODEL
    provider_version = MODEL

    def __init__(self, *, api_key: str, audio_directory: Path) -> None:
        """Bind the credential and the local directory holding evaluation audio."""
        self._api_key = api_key
        self._audio_directory = audio_directory

    def observe(self, case: TranscriptionCase) -> TranscriptionObservation:
        """Transcribe one case's local audio, recording latency and assumed cost."""
        path = self._audio_directory / f"{case.case_id}.wav"
        if not path.is_file():
            raise FileNotFoundError(f"evaluation audio for {case.case_id} is missing at {path}")
        started = time.monotonic()
        try:
            payload = self._request(path, case)
        except httpx.HTTPError:
            return self._failure(case, "TRANSCRIPTION_PROVIDER_UNAVAILABLE", started)
        try:
            transcript = _normalized(payload, case)
        except (TranscriptValidationError, KeyError, TypeError, ValueError):
            return self._failure(case, "TRANSCRIPTION_PROVIDER_INVALID", started)
        return TranscriptionObservation(
            case_id=case.case_id,
            transcript=transcript,
            error_code=None,
            latency_ms=int((time.monotonic() - started) * _MS_PER_SECOND),
            cost_micros=round(case.duration_ms * COST_MICROS_PER_SOURCE_HOUR / _MS_PER_HOUR),
        )

    def _request(self, path: Path, case: TranscriptionCase) -> dict[str, Any]:
        """Perform exactly one pre-recorded request with diarization and word timings."""
        parameters: dict[str, str] = {
            "model": MODEL,
            "diarize": "true",
            "punctuate": "true",
            "utterances": "true",
            "smart_format": "false",
        }
        if case.language != "mixed":
            parameters["language"] = case.language
        response = httpx.post(
            API_URL,
            params=parameters,
            content=path.read_bytes(),
            headers={"Authorization": f"Token {self._api_key}", "Content-Type": "audio/wav"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        document: dict[str, Any] = response.json()
        return document

    def _failure(
        self, case: TranscriptionCase, code: str, started: float
    ) -> TranscriptionObservation:
        """Record one failed measurement without leaking provider diagnostics."""
        return TranscriptionObservation(
            case_id=case.case_id,
            transcript=None,
            error_code=code,
            latency_ms=int((time.monotonic() - started) * _MS_PER_SECOND),
            cost_micros=0,
        )


def _normalized(payload: dict[str, Any], case: TranscriptionCase) -> Any:
    """Translate one Deepgram response into the same transcript shape as every provider."""
    alternative = payload["results"]["channels"][0]["alternatives"][0]
    words = tuple(
        RawWord(
            text=str(word["punctuated_word"] if "punctuated_word" in word else word["word"]),
            start_ms=int(float(word["start"]) * _MS_PER_SECOND),
            end_ms=int(float(word["end"]) * _MS_PER_SECOND),
            confidence=float(word.get("confidence", 0.0)),
            speaker=f"speaker_{int(word.get('speaker', 0))}",
        )
        for word in alternative["words"]
    )
    utterances = tuple(
        RawUtterance(
            text=str(utterance["transcript"]),
            start_ms=int(float(utterance["start"]) * _MS_PER_SECOND),
            end_ms=int(float(utterance["end"]) * _MS_PER_SECOND),
            speaker=f"speaker_{int(utterance.get('speaker', 0))}",
        )
        for utterance in payload["results"].get("utterances", ())
    )
    return normalize_transcript(
        provider=PROVIDER,
        provider_version=MODEL,
        model=MODEL,
        language=str(payload["results"]["channels"][0].get("detected_language", case.language)),
        duration_ms=case.duration_ms,
        words=words,
        utterances=utterances,
        raw_result={},
    )


def api_key_from_environment() -> str | None:
    """Read the challenger credential, which is never part of ordinary configuration."""
    return os.environ.get(API_KEY_VARIABLE)
