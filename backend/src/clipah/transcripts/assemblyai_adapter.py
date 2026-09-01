"""AssemblyAI SDK 1.x adapter for one-pass word timestamps and speaker diarization."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import Enum
from typing import Any, Protocol, cast

import assemblyai as aai

from clipah.assets.storage import StoredObject
from clipah.transcripts.models import JsonValue, RawUtterance, RawWord, TranscriptResult
from clipah.transcripts.provider import (
    TranscriptionProviderRetryableError,
    TranscriptionProviderTerminalError,
)
from clipah.transcripts.use_cases import TranscriptValidationError, normalize_transcript

UNIVERSAL_3_PRO = "universal-3-pro"
UNIVERSAL_2 = "universal-2"
UNIVERSAL_3_PRO_LANGUAGES = frozenset({"en", "es", "de", "fr", "pt", "it"})
PROVIDER_TIMEOUT_SECONDS = 300.0

AudioUrlResolver = Callable[[StoredObject], str]


class _SdkTranscriber(Protocol):
    """Structural subset of the SDK transcriber isolated inside this adapter."""

    def transcribe(
        self, audio_url: str, config: Any, *, poll_timeout: float | None = None
    ) -> object:
        """Submit and await one pre-recorded transcription."""


class _SdkWord(Protocol):
    """Structural word fields copied from the SDK response."""

    text: object
    start: object
    end: object
    confidence: object
    speaker: object


class _SdkUtterance(Protocol):
    """Structural utterance fields copied from the SDK response."""

    text: object
    start: object
    end: object
    speaker: object


class AssemblyAITranscriber:
    """Translate the locked AssemblyAI SDK into Clipah's normalized transcript contract."""

    def __init__(
        self,
        *,
        api_key: str,
        audio_url_resolver: AudioUrlResolver,
        sdk_transcriber: _SdkTranscriber | None = None,
    ) -> None:
        """Bind a private-audio capability resolver without mutating SDK globals."""
        self._audio_url_resolver = audio_url_resolver
        self._transcriber = sdk_transcriber or cast(
            _SdkTranscriber, aai.Transcriber(api_key=api_key)
        )

    def transcribe(self, *, audio: StoredObject, language: str | None) -> TranscriptResult:
        """Perform one provider request and discard every provider-specific object afterward."""
        if audio.duration_ms is None or audio.duration_ms <= 0:
            raise TranscriptionProviderTerminalError("TRANSCRIPT_DURATION_INVALID")
        audio_url = self._audio_url_resolver(audio)
        config = _config_for(language)
        try:
            response = self._transcriber.transcribe(
                audio_url,
                config,
                poll_timeout=PROVIDER_TIMEOUT_SECONDS,
            )
        except Exception:
            raise TranscriptionProviderRetryableError(
                "TRANSCRIPTION_PROVIDER_UNAVAILABLE"
            ) from None

        if _string_value(getattr(response, "status", None)) == "error":
            raise TranscriptionProviderTerminalError("TRANSCRIPTION_PROVIDER_REJECTED")
        try:
            model = _required_string(getattr(response, "speech_model_used", None))
            detected_language = _required_string(getattr(response, "language_code", None))
            raw_words = tuple(_raw_word(word) for word in _required_sequence(response, "words"))
            raw_utterances = tuple(
                _raw_utterance(utterance)
                for utterance in _required_sequence(response, "utterances")
            )
            raw_result = _json_mapping(getattr(response, "json_response", None))
            return normalize_transcript(
                provider="assemblyai",
                provider_version=str(aai.__version__),
                model=model,
                language=detected_language,
                duration_ms=audio.duration_ms,
                words=raw_words,
                utterances=raw_utterances,
                raw_result=raw_result,
            )
        except TranscriptValidationError as error:
            raise TranscriptionProviderTerminalError(error.code) from None
        except (TypeError, ValueError, AttributeError):
            raise TranscriptionProviderTerminalError("TRANSCRIPTION_PROVIDER_INVALID") from None


def _config_for(language: str | None) -> aai.TranscriptionConfig:
    """Route only supported requested languages to Universal-3 Pro."""
    if language is None:
        speech_models = [UNIVERSAL_3_PRO, UNIVERSAL_2]
    elif language in UNIVERSAL_3_PRO_LANGUAGES:
        speech_models = [UNIVERSAL_3_PRO]
    else:
        speech_models = [UNIVERSAL_2]
    return aai.TranscriptionConfig(
        speech_models=speech_models,
        language_code=language,
        language_detection=language is None,
        speaker_labels=True,
        punctuate=True,
        format_text=True,
    )


def _required_sequence(response: object, field: str) -> tuple[object, ...]:
    """Read one non-empty provider sequence without retaining its SDK container."""
    value = getattr(response, field)
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"missing {field}")
    return tuple(value)


def _raw_word(value: object) -> RawWord:
    """Copy one SDK word into the provider-neutral raw value."""
    word = cast(_SdkWord, value)
    return RawWord(
        text=_required_string(word.text),
        start_ms=_required_int(word.start),
        end_ms=_required_int(word.end),
        confidence=_required_float(word.confidence),
        speaker=_required_string(word.speaker),
    )


def _raw_utterance(value: object) -> RawUtterance:
    """Copy one SDK utterance into the provider-neutral raw value."""
    utterance = cast(_SdkUtterance, value)
    return RawUtterance(
        text=_required_string(utterance.text),
        start_ms=_required_int(utterance.start),
        end_ms=_required_int(utterance.end),
        speaker=_required_string(utterance.speaker),
    )


def _required_string(value: object) -> str:
    """Return one non-empty provider string or reject the payload."""
    normalized = _string_value(value)
    if normalized is None or not normalized.strip():
        raise ValueError("provider string missing")
    return normalized


def _string_value(value: object) -> str | None:
    """Normalize SDK string enums without allowing arbitrary object formatting."""
    if isinstance(value, str):
        return value
    if isinstance(value, Enum) and isinstance(value.value, str):
        return value.value
    return None


def _required_int(value: object) -> int:
    """Return a provider integer while refusing booleans and coercion."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("provider integer missing")
    return value


def _required_float(value: object) -> float:
    """Return a provider number as a float while refusing booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("provider number missing")
    return float(value)


def _json_mapping(value: object) -> dict[str, JsonValue]:
    """Normalize the raw SDK response into a JSON-safe string-key mapping."""
    normalized = _json_value(value)
    if not isinstance(normalized, dict):
        raise TypeError("provider JSON missing")
    return normalized


def _json_value(value: object) -> JsonValue:
    """Recursively strip SDK enums and containers from retained provider evidence."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise TypeError("provider value is not JSON-safe")
