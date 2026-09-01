"""Contracts for AssemblyAI 1.x routing and provider-neutral result conversion."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from clipah.assets.storage import StoredObject
from clipah.transcripts.assemblyai_adapter import AssemblyAITranscriber
from clipah.transcripts.models import RawUtterance, RawWord
from clipah.transcripts.provider import (
    FakeTranscriber,
    TranscriptionProviderRetryableError,
    TranscriptionProviderTerminalError,
)
from clipah.transcripts.use_cases import normalize_transcript


class RecordingSdkTranscriber:
    """Record the provider boundary while returning one complete SDK-shaped response."""

    def __init__(self, response: object | None = None, error: Exception | None = None) -> None:
        """Bind either one response or one transport failure."""
        self.response = response or _response()
        self.error = error
        self.calls: list[tuple[str, Any, float | None]] = []

    def transcribe(
        self, audio_url: str, config: Any, *, poll_timeout: float | None = None
    ) -> object:
        """Return the configured SDK result after retaining exact call configuration."""
        self.calls.append((audio_url, config, poll_timeout))
        if self.error is not None:
            raise self.error
        return self.response


def _audio() -> StoredObject:
    """Return one fully validated transcription-audio object."""
    return StoredObject(
        key="workspaces/w/projects/p/derived/source/transcription_audio",
        content_type="audio/wav",
        content_length=80,
        sha256=b"a" * 32,
        duration_ms=2_000,
    )


def _response(
    *,
    status: str = "completed",
    model: str = "universal-3-pro",
    language: str = "en",
) -> SimpleNamespace:
    """Mirror the SDK fields the adapter is allowed to consume."""
    words = [
        SimpleNamespace(text="Hello,", start=0, end=400, confidence=0.99, speaker="A"),
        SimpleNamespace(text="world!", start=450, end=900, confidence=0.97, speaker="A"),
    ]
    utterances = [
        SimpleNamespace(
            text="Hello, world!",
            start=0,
            end=900,
            confidence=0.98,
            speaker="A",
            words=words,
        )
    ]
    return SimpleNamespace(
        id="provider-id",
        status=status,
        error="provider secret diagnostic" if status == "error" else None,
        text="Hello, world!",
        words=words,
        utterances=utterances,
        speech_model_used=model,
        language_code=language,
        json_response={
            "id": "provider-id",
            "status": status,
            "speech_model_used": model,
            "language_code": language,
        },
    )


def _adapter(sdk: RecordingSdkTranscriber) -> AssemblyAITranscriber:
    """Create the real adapter with only its network operation replaced."""
    return AssemblyAITranscriber(
        api_key="test-api-key",
        audio_url_resolver=lambda audio: f"https://private.test/{audio.key}",
        sdk_transcriber=sdk,
    )


@pytest.mark.unit
def test_fake_transcriber_returns_configured_result_and_records_inputs() -> None:
    """Pipeline tests need a deterministic port double without provider SDK values."""
    result = normalize_transcript(
        provider="fake",
        provider_version="1",
        model="fake-model",
        language="id",
        duration_ms=2_000,
        words=(RawWord("Halo!", 0, 500, 0.9, "A"),),
        utterances=(RawUtterance("Halo!", 0, 500, "A"),),
        raw_result={"id": "fake"},
    )
    fake = FakeTranscriber(result=result)

    assert fake.transcribe(audio=_audio(), language="id") is result
    assert fake.calls == [(_audio(), "id")]


@pytest.mark.unit
@pytest.mark.parametrize("language", ["en", "es", "de", "fr", "pt", "it"])
def test_supported_languages_use_only_universal_3_pro(language: str) -> None:
    """A supported requested language must never be silently downgraded to Universal-2."""
    sdk = RecordingSdkTranscriber(_response(language=language))

    _adapter(sdk).transcribe(audio=_audio(), language=language)

    _, config, poll_timeout = sdk.calls[0]
    assert config.speech_models == ["universal-3-pro"]
    assert config.language_code == language
    assert config.language_detection is False
    assert config.speaker_labels is True
    assert poll_timeout == 300


@pytest.mark.unit
@pytest.mark.parametrize("language", ["id", "ja"])
def test_languages_outside_universal_3_pro_use_only_universal_2(language: str) -> None:
    """Indonesian and other unsupported U3 languages must route to broad-coverage U2."""
    sdk = RecordingSdkTranscriber(_response(model="universal-2", language=language))

    _adapter(sdk).transcribe(audio=_audio(), language=language)

    assert sdk.calls[0][1].speech_models == ["universal-2"]
    assert sdk.calls[0][1].language_code == language


@pytest.mark.unit
def test_unspecified_language_uses_detected_u3_then_u2_routing() -> None:
    """Detection must try U3 only where supported and retain U2 as the broad fallback."""
    sdk = RecordingSdkTranscriber()

    _adapter(sdk).transcribe(audio=_audio(), language=None)

    config = sdk.calls[0][1]
    assert config.speech_models == ["universal-3-pro", "universal-2"]
    assert config.language_code is None
    assert config.language_detection is True


@pytest.mark.unit
def test_converts_complete_sdk_response_without_leaking_sdk_types() -> None:
    """Durable use cases must receive word IDs, punctuation, confidence, and speakers."""
    result = _adapter(RecordingSdkTranscriber()).transcribe(audio=_audio(), language="en")

    assert result.provider == "assemblyai"
    assert result.provider_version == "1.0.0"
    assert result.model == "universal-3-pro"
    assert result.language == "en"
    assert result.full_text == "Hello, world!"
    assert result.words[0].word_id == "w000001"
    assert result.words[0].speaker == "A"
    assert result.words[0].confidence == 0.99
    assert result.utterances[0].word_ids == ("w000001", "w000002")
    assert result.raw_result["id"] == "provider-id"


@pytest.mark.unit
def test_transport_failure_is_retryable_and_sanitized() -> None:
    """Temporary provider failures must retry without persisting provider diagnostics."""
    sdk = RecordingSdkTranscriber(error=RuntimeError("secret provider URL"))

    with pytest.raises(TranscriptionProviderRetryableError) as raised:
        _adapter(sdk).transcribe(audio=_audio(), language="en")

    assert raised.value.code == "TRANSCRIPTION_PROVIDER_UNAVAILABLE"
    assert str(raised.value) == "TRANSCRIPTION_PROVIDER_UNAVAILABLE"


@pytest.mark.unit
def test_provider_error_status_is_terminal_and_sanitized() -> None:
    """A completed provider rejection cannot become a retry loop or leak its message."""
    sdk = RecordingSdkTranscriber(_response(status="error"))

    with pytest.raises(TranscriptionProviderTerminalError) as raised:
        _adapter(sdk).transcribe(audio=_audio(), language="en")

    assert raised.value.code == "TRANSCRIPTION_PROVIDER_REJECTED"
    assert "secret" not in str(raised.value)
