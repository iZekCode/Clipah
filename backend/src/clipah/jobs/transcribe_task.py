"""Durable TRANSCRIBE runner with one-pass provider work and retry convergence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
from typing import Any, cast
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clipah.assets.storage import (
    ObjectStore,
    ObjectStoreUnavailableError,
    S3ObjectStore,
    StoredObject,
)
from clipah.config import Settings
from clipah.db import RuntimeRole, session_scope
from clipah.jobs.models import JobCancelledError, JobContext, RetryableJobError, TerminalJobError
from clipah.models import Asset, AssetKind, Transcript
from clipah.transcripts.assemblyai_adapter import AssemblyAITranscriber
from clipah.transcripts.models import JsonValue, TranscriptResult
from clipah.transcripts.provider import (
    Transcriber,
    TranscriptionProviderRetryableError,
    TranscriptionProviderTerminalError,
)
from clipah.transcripts.use_cases import (
    TranscriptValidationError,
    raw_transcript_key,
    transcript_document,
)

SOURCE_NOT_FOUND_CODE = "TRANSCRIPTION_SOURCE_NOT_FOUND"
AUDIO_NOT_FOUND_CODE = "TRANSCRIPTION_AUDIO_NOT_FOUND"
INTEGRITY_CODE = "TRANSCRIPT_INTEGRITY"
STORAGE_UNAVAILABLE_CODE = "TRANSCRIPTION_STORAGE_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class TranscriptionDependencies:
    """External capabilities used after tenant evidence is detached."""

    transcriber: Transcriber
    store: ObjectStore


@dataclass(frozen=True, slots=True)
class _SourceSnapshot:
    """Source identity and duration safe to carry outside a transaction."""

    asset_id: UUID
    duration_ms: int


DependenciesFactory = Callable[[Settings], TranscriptionDependencies]


class TranscriptionIntegrityError(Exception):
    """Refuse incompatible provider, storage, or persisted transcript evidence."""


class TranscribeStageRunner:
    """Call the provider once and atomically converge one canonical Transcript row."""

    def __init__(self, *, dependencies_factory: DependenciesFactory) -> None:
        """Bind production or deterministic external capabilities."""
        self._dependencies_factory = dependencies_factory

    def __call__(self, context: JobContext) -> None:
        """Run one transcription attempt through stable retryable and terminal codes."""
        try:
            context.raise_if_cancelled()
            source, audio = self._load_assets(context)
            if self._already_complete(context, source):
                return
            dependencies = self._dependencies_factory(context.settings)
            context.raise_if_cancelled()
            result = dependencies.transcriber.transcribe(audio=audio, language=None)
            context.raise_if_cancelled()
            self._validate_result(source, result)
            raw_key = self._store_raw_result(
                context,
                source=source,
                result=result,
                store=dependencies.store,
            )
            context.raise_if_cancelled()
            try:
                self._persist(context, source=source, result=result, raw_key=raw_key)
            except IntegrityError:
                if not self._already_complete(context, source):
                    raise TranscriptionIntegrityError("concurrent Transcript conflict") from None
        except JobCancelledError:
            raise
        except TranscriptionProviderRetryableError as error:
            raise RetryableJobError(error.code) from None
        except TranscriptionProviderTerminalError as error:
            raise TerminalJobError(error.code) from None
        except ObjectStoreUnavailableError:
            raise RetryableJobError(STORAGE_UNAVAILABLE_CODE) from None
        except TranscriptValidationError as error:
            raise TerminalJobError(error.code) from None
        except TranscriptionIntegrityError:
            raise TerminalJobError(INTEGRITY_CODE) from None

    def _load_assets(self, context: JobContext) -> tuple[_SourceSnapshot, StoredObject]:
        """Resolve the sole source and its deterministic Task 11 audio derivative."""
        with _transaction(context) as session:
            sources = session.scalars(
                select(Asset)
                .where(
                    Asset.workspace_id == context.workspace_id,
                    Asset.project_id == context.project_id,
                    Asset.kind == AssetKind.SOURCE,
                )
                .order_by(Asset.created_at, Asset.id)
                .limit(2)
            ).all()
            if len(sources) != 1 or sources[0].duration_ms is None:
                raise TerminalJobError(SOURCE_NOT_FOUND_CODE)
            source = sources[0]
            audio_id = uuid5(source.id, AssetKind.TRANSCRIPTION_AUDIO.value)
            audio = session.scalar(
                select(Asset).where(
                    Asset.workspace_id == context.workspace_id,
                    Asset.project_id == context.project_id,
                    Asset.id == audio_id,
                    Asset.kind == AssetKind.TRANSCRIPTION_AUDIO,
                )
            )
            if (
                audio is None
                or audio.duration_ms is None
                or audio.duration_ms <= 0
                or audio.size_bytes <= 0
                or len(audio.sha256) != 32
            ):
                raise TerminalJobError(AUDIO_NOT_FOUND_CODE)
            if audio.duration_ms != source.duration_ms:
                raise TranscriptionIntegrityError("audio duration differs from source")
            return (
                _SourceSnapshot(asset_id=source.id, duration_ms=source.duration_ms),
                StoredObject(
                    key=audio.storage_key,
                    content_type=audio.content_type,
                    content_length=audio.size_bytes,
                    sha256=audio.sha256,
                    duration_ms=audio.duration_ms,
                ),
            )

    def _already_complete(self, context: JobContext, source: _SourceSnapshot) -> bool:
        """Reuse only structurally valid evidence for the exact source Asset."""
        with _transaction(context) as session:
            row = session.scalar(
                select(Transcript).where(
                    Transcript.workspace_id == context.workspace_id,
                    Transcript.project_id == context.project_id,
                    Transcript.asset_id == source.asset_id,
                )
            )
            if row is None:
                return False
            expected_key = raw_transcript_key(
                context.workspace_id,
                context.project_id,
                source.asset_id,
            )
            if not _valid_existing(row, source=source, raw_key=expected_key):
                raise TranscriptionIntegrityError("persisted Transcript is incompatible")
            return True

    def _validate_result(self, source: _SourceSnapshot, result: TranscriptResult) -> None:
        """Require provider-neutral evidence to describe the exact source duration."""
        if result.duration_ms != source.duration_ms or not result.words or not result.utterances:
            raise TranscriptionIntegrityError("provider result differs from source")

    def _store_raw_result(
        self,
        context: JobContext,
        *,
        source: _SourceSnapshot,
        result: TranscriptResult,
        store: ObjectStore,
    ) -> str:
        """Write one deterministic raw JSON object and verify its immutable metadata."""
        key = raw_transcript_key(context.workspace_id, context.project_id, source.asset_id)
        stored_document: dict[str, JsonValue] = {
            "provider": result.provider,
            "provider_version": result.provider_version,
            "model": result.model,
            "language": result.language,
            "duration_ms": result.duration_ms,
            "raw_result": result.raw_result,
        }
        body = json.dumps(
            stored_document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(body).digest()
        stored = store.put_file(
            key=key,
            content_type="application/json",
            file=BytesIO(body),
            sha256=digest,
        )
        if stored.key != key or stored.content_length != len(body) or stored.sha256 != digest:
            raise TranscriptionIntegrityError("raw Transcript object metadata mismatch")
        return key

    def _persist(
        self,
        context: JobContext,
        *,
        source: _SourceSnapshot,
        result: TranscriptResult,
        raw_key: str,
    ) -> None:
        """Insert the normalized Transcript in one tenant-scoped transaction."""
        document = transcript_document(result)
        with _transaction(context) as session:
            session.add(
                Transcript(
                    workspace_id=context.workspace_id,
                    project_id=context.project_id,
                    asset_id=source.asset_id,
                    provider=result.provider,
                    provider_version=result.provider_version,
                    model=result.model,
                    language=result.language,
                    full_text=result.full_text,
                    words=_json_rows(document["words"]),
                    speaker_segments=_json_rows(document["speaker_segments"]),
                    utterances=_json_rows(document["utterances"]),
                    duration_ms=result.duration_ms,
                    raw_result_storage_key=raw_key,
                )
            )
            session.flush()


def _json_rows(value: JsonValue) -> list[dict[str, Any]]:
    """Narrow one canonical document field to the ORM's JSON-row shape."""
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise TranscriptionIntegrityError("canonical Transcript JSON shape invalid")
    return cast(list[dict[str, Any]], value)


def _valid_existing(row: Transcript, *, source: _SourceSnapshot, raw_key: str) -> bool:
    """Recognize a complete canonical row safe to reuse without provider work."""
    if (
        row.asset_id != source.asset_id
        or row.duration_ms != source.duration_ms
        or row.raw_result_storage_key != raw_key
        or not row.provider
        or not row.provider_version
        or not row.model
        or not row.language
        or not row.full_text
        or not isinstance(row.words, list)
        or not isinstance(row.utterances, list)
        or not isinstance(row.speaker_segments, list)
        or not row.words
        or not row.utterances
        or not row.speaker_segments
    ):
        return False
    expected_ids = [f"w{index:06d}" for index in range(1, len(row.words) + 1)]
    observed_ids: list[object] = []
    previous_start = -1
    for word in row.words:
        if not isinstance(word, dict):
            return False
        observed_ids.append(word.get("word_id"))
        start = word.get("start_ms")
        end = word.get("end_ms")
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < previous_start
            or end < start
            or end > source.duration_ms
        ):
            return False
        previous_start = start
    return observed_ids == expected_ids


@contextmanager
def _transaction(context: JobContext) -> Iterator[Session]:
    """Open one short least-privilege worker transaction for this Job's tenant."""
    with session_scope(
        settings=context.settings,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        runtime_role=RuntimeRole.WORKER,
    ) as session:
        yield session


def production_transcription_dependencies(settings: Settings) -> TranscriptionDependencies:
    """Compose AssemblyAI and private S3 capabilities from fail-closed worker settings."""
    if (
        settings.assemblyai_api_key is None
        or settings.object_store_bucket is None
        or settings.object_store_access_key_id is None
        or settings.object_store_secret_access_key is None
    ):
        raise RuntimeError("transcription worker requires provider and object storage settings")
    store = S3ObjectStore(
        bucket=settings.object_store_bucket,
        endpoint_url=settings.object_store_endpoint,
        access_key_id=settings.object_store_access_key_id.get_secret_value(),
        secret_access_key=settings.object_store_secret_access_key.get_secret_value(),
    )
    transcriber = AssemblyAITranscriber(
        api_key=settings.assemblyai_api_key.get_secret_value(),
        audio_url_resolver=lambda audio: (
            store.sign_download(
                key=audio.key,
                expires_in=timedelta(minutes=5),
            ).url
        ),
    )
    return TranscriptionDependencies(transcriber=transcriber, store=store)


transcribe_stage_runner = TranscribeStageRunner(
    dependencies_factory=production_transcription_dependencies
)
