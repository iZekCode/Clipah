"""Integration contracts for durable, retry-safe diarized transcription."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clipah.assets.storage import FakeObjectStore, ObjectStoreUnavailableError, StoredObject
from clipah.db import RuntimeRole
from clipah.jobs.models import JobContext, RetryableJobError, TerminalJobError
from clipah.jobs.tasks import stage_runners
from clipah.jobs.transcribe_task import TranscribeStageRunner, TranscriptionDependencies
from clipah.models import (
    Asset,
    AssetKind,
    AssetSourceType,
    Job,
    JobKind,
    JobStatus,
    Project,
    ProjectStatus,
    SourceKind,
    Transcript,
)
from clipah.transcripts.assemblyai_adapter import AssemblyAITranscriber
from clipah.transcripts.models import RawUtterance, RawWord
from clipah.transcripts.provider import (
    FakeTranscriber,
    TranscriptionProviderRetryableError,
    TranscriptionProviderTerminalError,
)
from clipah.transcripts.use_cases import normalize_transcript, raw_transcript_key
from support import provision_identity, runtime_settings


class UnavailableObjectStore(FakeObjectStore):
    """Represent a temporary failure while retaining the real fake's other behavior."""

    def put_file(self, **_kwargs: object) -> StoredObject:
        """Fail before any raw provider document becomes durable."""
        raise ObjectStoreUnavailableError("secret storage diagnostic")


@pytest.mark.integration
def test_database_allows_only_one_transcript_per_source_asset(
    engine: Engine, clean_database: None
) -> None:
    """Concurrent deliveries must be stopped by storage, not only application timing."""
    del clean_database
    _, workspace_id, project_id, source_id = _seed_source(engine, suffix="transcript-unique")
    first = _transcript_row(workspace_id, project_id, source_id)
    second = _transcript_row(workspace_id, project_id, source_id)

    with Session(engine) as session:
        session.add(first)
        session.commit()
        session.add(second)
        with pytest.raises(IntegrityError):
            session.commit()

    with Session(engine) as session:
        count = session.scalar(
            select(func.count())
            .select_from(Transcript)
            .where(Transcript.workspace_id == workspace_id, Transcript.asset_id == source_id)
        )
        assert count == 1


@pytest.mark.integration
def test_transcription_runner_is_registered_for_the_existing_job_kind() -> None:
    """Queued TRANSCRIBE Jobs must not fall through to the unsupported-kind failure."""
    assert JobKind.TRANSCRIBE in stage_runners()


@pytest.mark.integration
def test_worker_persists_one_transcript_and_skips_provider_on_repeated_delivery(
    engine: Engine, clean_database: None
) -> None:
    """A redelivered Job must reuse canonical evidence without paying the provider twice."""
    del clean_database
    context, source_id, audio = _seed_transcription(engine, suffix="transcript-success")
    result = _result()
    transcriber = FakeTranscriber(result=result)
    store = FakeObjectStore(now=lambda: datetime(2026, 9, 1, tzinfo=UTC))
    runner = TranscribeStageRunner(
        dependencies_factory=lambda _: TranscriptionDependencies(
            transcriber=transcriber,
            store=store,
        )
    )

    runner(context)
    runner(context)

    with Session(engine) as session:
        transcript = session.scalar(
            select(Transcript).where(
                Transcript.workspace_id == context.workspace_id,
                Transcript.asset_id == source_id,
            )
        )
        assert transcript is not None
        assert transcript.project_id == context.project_id
        assert transcript.provider == "assemblyai"
        assert transcript.model == "universal-3-pro"
        assert transcript.language == "en"
        assert transcript.full_text == "Hello, world!"
        assert transcript.words[0]["word_id"] == "w000001"
        assert transcript.words[0]["speaker"] == "A"
        assert transcript.utterances[0]["word_ids"] == ["w000001", "w000002"]
        assert transcript.speaker_segments[0]["speaker"] == "A"
        expected_key = raw_transcript_key(context.workspace_id, context.project_id, source_id)
        assert transcript.raw_result_storage_key == expected_key
    assert transcriber.calls == [(audio, None)]
    expected_key = raw_transcript_key(context.workspace_id, context.project_id, source_id)
    assert json.loads(store.object_bodies[expected_key]) == {
        "provider": "assemblyai",
        "provider_version": "1.0.0",
        "model": "universal-3-pro",
        "language": "en",
        "duration_ms": 2_000,
        "raw_result": result.raw_result,
    }
    assert (
        store.objects[expected_key].sha256
        == hashlib.sha256(store.object_bodies[expected_key]).digest()
    )


@pytest.mark.integration
def test_provider_transport_failure_is_retryable_and_sanitized(
    engine: Engine, clean_database: None
) -> None:
    """Temporary transcription outages retain retry semantics without provider detail."""
    del clean_database
    context, _, _ = _seed_transcription(engine, suffix="transcript-retry")
    fake = FakeTranscriber(
        error=TranscriptionProviderRetryableError("TRANSCRIPTION_PROVIDER_UNAVAILABLE")
    )

    with pytest.raises(RetryableJobError, match=r"^TRANSCRIPTION_PROVIDER_UNAVAILABLE$"):
        _runner(fake)(context)


@pytest.mark.integration
def test_provider_rejection_is_terminal_and_sanitized(engine: Engine, clean_database: None) -> None:
    """A provider rejection cannot loop forever or leak its provider payload."""
    del clean_database
    context, _, _ = _seed_transcription(engine, suffix="transcript-terminal")
    fake = FakeTranscriber(
        error=TranscriptionProviderTerminalError("TRANSCRIPTION_PROVIDER_REJECTED")
    )

    with pytest.raises(TerminalJobError, match=r"^TRANSCRIPTION_PROVIDER_REJECTED$"):
        _runner(fake)(context)


@pytest.mark.integration
def test_missing_transcription_audio_is_terminal_before_provider_work(
    engine: Engine, clean_database: None
) -> None:
    """A Job without Task 11 audio evidence cannot send an ambiguous provider request."""
    del clean_database
    context, _, _ = _seed_transcription(engine, suffix="transcript-no-audio", include_audio=False)
    fake = FakeTranscriber(result=_result())

    with pytest.raises(TerminalJobError, match=r"^TRANSCRIPTION_AUDIO_NOT_FOUND$"):
        _runner(fake)(context)

    assert fake.calls == []


@pytest.mark.integration
def test_raw_result_storage_failure_is_retryable_without_a_transcript_row(
    engine: Engine, clean_database: None
) -> None:
    """A private object outage must not leave a Transcript pointing at absent evidence."""
    del clean_database
    context, _, _ = _seed_transcription(engine, suffix="transcript-storage")
    store = UnavailableObjectStore(now=lambda: datetime(2026, 9, 1, tzinfo=UTC))
    runner = TranscribeStageRunner(
        dependencies_factory=lambda _: TranscriptionDependencies(
            transcriber=FakeTranscriber(result=_result()),
            store=store,
        )
    )

    with pytest.raises(RetryableJobError, match=r"^TRANSCRIPTION_STORAGE_UNAVAILABLE$"):
        runner(context)

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Transcript)) == 0


@pytest.mark.integration
@pytest.mark.slow
def test_assemblyai_provider_contract_smoke() -> None:
    """An explicit opt-in proves the locked SDK still returns diarized word evidence."""
    if os.getenv("CLIPAH_RUN_ASSEMBLYAI_SMOKE") != "1":
        pytest.skip("set CLIPAH_RUN_ASSEMBLYAI_SMOKE=1 to enable the provider contract")
    api_key = os.getenv("CLIPAH_ASSEMBLYAI_API_KEY")
    audio_url = os.getenv("CLIPAH_ASSEMBLYAI_SMOKE_AUDIO_URL")
    duration = os.getenv("CLIPAH_ASSEMBLYAI_SMOKE_DURATION_MS")
    if not api_key or not audio_url or not duration:
        pytest.skip("AssemblyAI smoke requires API key, audio URL, and duration")
    audio = StoredObject(
        key=audio_url,
        content_type="audio/wav",
        content_length=1,
        duration_ms=int(duration),
    )

    result = AssemblyAITranscriber(
        api_key=api_key,
        audio_url_resolver=lambda stored: stored.key,
    ).transcribe(audio=audio, language=None)

    assert result.provider == "assemblyai"
    assert result.model in {"universal-2", "universal-3-pro"}
    assert result.language
    assert result.words
    assert result.utterances
    assert result.speaker_segments


def _seed_source(engine: Engine, *, suffix: str) -> tuple[UUID, UUID, UUID, UUID]:
    """Create one Project and fully ingested source Asset for persistence tests."""
    user_id, workspace_id = provision_identity(engine, suffix=suffix)
    project_id = uuid4()
    source_id = uuid4()
    now = datetime.now(tz=UTC)
    with engine.begin() as connection:
        connection.execute(
            Project.__table__.insert().values(
                id=project_id,
                workspace_id=workspace_id,
                created_by_user_id=user_id,
                name=f"Transcript {suffix}",
                status=ProjectStatus.ANALYZING,
                source_kind=SourceKind.UPLOAD,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            Asset.__table__.insert().values(
                id=source_id,
                workspace_id=workspace_id,
                project_id=project_id,
                kind=AssetKind.SOURCE,
                source_type=AssetSourceType.USER_UPLOAD,
                storage_key=f"workspaces/{workspace_id}/projects/{project_id}/source/original",
                content_type="video/mp4",
                size_bytes=100,
                duration_ms=2_000,
                sha256=b"s" * 32,
            )
        )
    return user_id, workspace_id, project_id, source_id


def _seed_transcription(
    engine: Engine, *, suffix: str, include_audio: bool = True
) -> tuple[JobContext, UUID, StoredObject]:
    """Create one running TRANSCRIBE Job with Task 11 source/audio evidence."""
    user_id, workspace_id, project_id, source_id = _seed_source(engine, suffix=suffix)
    job_id = uuid4()
    audio_id = uuid5(source_id, AssetKind.TRANSCRIPTION_AUDIO.value)
    audio_key = (
        f"workspaces/{workspace_id}/projects/{project_id}/derived/{source_id}/transcription_audio"
    )
    audio = StoredObject(
        key=audio_key,
        content_type="audio/wav",
        content_length=80,
        sha256=b"a" * 32,
        duration_ms=2_000,
    )
    with engine.begin() as connection:
        connection.execute(
            Job.__table__.insert().values(
                id=job_id,
                workspace_id=workspace_id,
                project_id=project_id,
                kind=JobKind.TRANSCRIBE,
                status=JobStatus.RUNNING,
                stage="queued",
                progress=0,
                attempt=1,
                idempotency_key=f"transcribe-{suffix}",
            )
        )
        if include_audio:
            connection.execute(
                Asset.__table__.insert().values(
                    id=audio_id,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    kind=AssetKind.TRANSCRIPTION_AUDIO,
                    source_type=AssetSourceType.DERIVED,
                    storage_key=audio.key,
                    content_type=audio.content_type,
                    size_bytes=audio.content_length,
                    duration_ms=audio.duration_ms,
                    audio_codec="pcm_s16le",
                    sha256=audio.sha256,
                )
            )
    return (
        JobContext(
            job_id=job_id,
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
            attempt=1,
            settings=runtime_settings(RuntimeRole.WORKER),
        ),
        source_id,
        audio,
    )


def _result():
    """Return one complete normalized provider result for runner tests."""
    return normalize_transcript(
        provider="assemblyai",
        provider_version="1.0.0",
        model="universal-3-pro",
        language="en",
        duration_ms=2_000,
        words=(RawWord("Hello,", 0, 400, 0.99, "A"), RawWord("world!", 450, 900, 0.97, "A")),
        utterances=(RawUtterance("Hello, world!", 0, 900, "A"),),
        raw_result={"id": "provider-id", "speech_model_used": "universal-3-pro"},
    )


def _runner(fake: FakeTranscriber) -> TranscribeStageRunner:
    """Create the real runner around a deterministic provider and object store."""
    store = FakeObjectStore(now=lambda: datetime(2026, 9, 1, tzinfo=UTC))
    return TranscribeStageRunner(
        dependencies_factory=lambda _: TranscriptionDependencies(transcriber=fake, store=store)
    )


def _transcript_row(workspace_id: UUID, project_id: UUID, source_id: UUID) -> Transcript:
    """Build one complete canonical Transcript row with an independent identity."""
    return Transcript(
        id=uuid4(),
        workspace_id=workspace_id,
        project_id=project_id,
        asset_id=source_id,
        provider="assemblyai",
        provider_version="1.0.0",
        model="universal-3-pro",
        language="en",
        full_text="Hello!",
        words=[
            {
                "word_id": "w000001",
                "text": "Hello",
                "punctuation": "!",
                "start_ms": 0,
                "end_ms": 500,
                "confidence": 0.99,
                "speaker": "A",
            }
        ],
        speaker_segments=[
            {
                "segment_id": "s000001",
                "speaker": "A",
                "start_ms": 0,
                "end_ms": 500,
                "word_ids": ["w000001"],
            }
        ],
        utterances=[
            {
                "utterance_id": "u000001",
                "text": "Hello!",
                "start_ms": 0,
                "end_ms": 500,
                "speaker": "A",
                "word_ids": ["w000001"],
            }
        ],
        duration_ms=2_000,
        raw_result_storage_key=(
            f"workspaces/{workspace_id}/projects/{project_id}/"
            f"transcripts/{source_id}/assemblyai.json"
        ),
    )
