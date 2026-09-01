"""Optional self-hosted WhisperX adapter for the transcription evaluation.

WhisperX is optional in the strongest sense: it is not a backend dependency, it is imported
lazily, and a machine without it simply cannot select this adapter. It exists so a self-hosted
option can be compared on the same labeled cases before any provider decision is frozen.
Diarization requires a Hugging Face token and is skipped when none is configured, in which case
the diarization numbers in the report are not comparable and the report says so.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from clipah.transcripts.evaluation import TranscriptionCase, TranscriptionObservation
from clipah.transcripts.models import RawUtterance, RawWord
from clipah.transcripts.use_cases import TranscriptValidationError, normalize_transcript

ADAPTER_NAME = "whisperx"
PROVIDER = "whisperx"
DEFAULT_MODEL = "large-v3"
MODEL_VARIABLE = "CLIPAH_EVAL_WHISPERX_MODEL"
DEVICE_VARIABLE = "CLIPAH_EVAL_WHISPERX_DEVICE"
DIARIZATION_TOKEN_VARIABLE = "CLIPAH_EVAL_HUGGINGFACE_TOKEN"

# Self-hosted inference has no list price. Compute cost belongs to whoever runs the machine,
# so the report records zero rather than inventing a number nobody can invoice against.
COST_MICROS_PER_SOURCE_HOUR = 0

_MS_PER_SECOND = 1_000
_UNKNOWN_SPEAKER = "speaker_0"


class WhisperXEvaluationTranscriber:
    """Measure one case with a locally installed WhisperX pipeline."""

    name = ADAPTER_NAME
    provider = PROVIDER

    def __init__(self, *, audio_directory: Path, model: str | None = None) -> None:
        """Bind the audio directory and resolve the model and device from the environment."""
        self.model = model or os.environ.get(MODEL_VARIABLE, DEFAULT_MODEL)
        self._audio_directory = audio_directory
        self._device = os.environ.get(DEVICE_VARIABLE, "cpu")
        self._whisperx = _import_whisperx()
        self.provider_version = str(getattr(self._whisperx, "__version__", "unknown"))

    def observe(self, case: TranscriptionCase) -> TranscriptionObservation:
        """Transcribe and align one case's local audio, diarizing only when configured."""
        path = self._audio_directory / f"{case.case_id}.wav"
        if not path.is_file():
            raise FileNotFoundError(f"evaluation audio for {case.case_id} is missing at {path}")
        started = time.monotonic()
        try:
            segments = self._segments(str(path), case)
            transcript = _normalized(segments, case, self.model, self.provider_version)
        except TranscriptValidationError as error:
            return _failure(case, error.code, started)
        except (KeyError, TypeError, ValueError, RuntimeError):
            return _failure(case, "TRANSCRIPTION_PROVIDER_INVALID", started)
        return TranscriptionObservation(
            case_id=case.case_id,
            transcript=transcript,
            error_code=None,
            latency_ms=int((time.monotonic() - started) * _MS_PER_SECOND),
            cost_micros=0,
        )

    def _segments(self, path: str, case: TranscriptionCase) -> list[dict[str, Any]]:
        """Run transcription, word alignment, and optional diarization in one pass."""
        audio = self._whisperx.load_audio(path)
        model = self._whisperx.load_model(self.model, self._device)
        language = None if case.language == "mixed" else case.language
        result = model.transcribe(audio, language=language)
        aligner, metadata = self._whisperx.load_align_model(
            language_code=result["language"], device=self._device
        )
        aligned = self._whisperx.align(result["segments"], aligner, metadata, audio, self._device)
        token = os.environ.get(DIARIZATION_TOKEN_VARIABLE)
        if token:
            diarization = self._whisperx.DiarizationPipeline(
                use_auth_token=token, device=self._device
            )(audio)
            aligned = self._whisperx.assign_word_speakers(diarization, aligned)
        segments: list[dict[str, Any]] = list(aligned["segments"])
        return segments


def _normalized(
    segments: list[dict[str, Any]], case: TranscriptionCase, model: str, version: str
) -> Any:
    """Translate aligned WhisperX segments into the shared transcript shape."""
    words = tuple(
        RawWord(
            text=str(word["word"]).strip(),
            start_ms=int(float(word["start"]) * _MS_PER_SECOND),
            end_ms=int(float(word["end"]) * _MS_PER_SECOND),
            confidence=float(word.get("score", 0.0)),
            speaker=str(word.get("speaker", _UNKNOWN_SPEAKER)),
        )
        for segment in segments
        for word in segment.get("words", ())
        if word.get("start") is not None and word.get("end") is not None
    )
    utterances = tuple(
        RawUtterance(
            text=str(segment["text"]).strip(),
            start_ms=int(float(segment["start"]) * _MS_PER_SECOND),
            end_ms=int(float(segment["end"]) * _MS_PER_SECOND),
            speaker=str(segment.get("speaker", _UNKNOWN_SPEAKER)),
        )
        for segment in segments
    )
    return normalize_transcript(
        provider=PROVIDER,
        provider_version=version,
        model=model,
        language=case.language,
        duration_ms=case.duration_ms,
        words=words,
        utterances=utterances,
        raw_result={},
    )


def _failure(case: TranscriptionCase, code: str, started: float) -> TranscriptionObservation:
    """Record one failed measurement without leaking local diagnostics."""
    return TranscriptionObservation(
        case_id=case.case_id,
        transcript=None,
        error_code=code,
        latency_ms=int((time.monotonic() - started) * _MS_PER_SECOND),
        cost_micros=0,
    )


def _import_whisperx() -> Any:
    """Import WhisperX only when this adapter is selected, and say so plainly when absent."""
    try:
        import whisperx
    except ImportError as error:  # pragma: no cover - exercised only on a prepared machine
        raise RuntimeError(
            "the whisperx adapter requires whisperx to be installed in this environment"
        ) from error
    return whisperx
