"""Durable BROLL_GENERATE runner: ask a model for one picture, and prove what came back.

Generation is the fallback for a beat stock could not illustrate. The stage is written to
survive redelivery without paying twice: the provider request identity is persisted before
the first poll, so a worker that dies mid-generation resumes the request it already started
instead of submitting another one.

Nothing a provider says is trusted as evidence. The output URL is an ephemeral capability
that is downloaded immediately and never stored; the bytes are validated as real media
before an Asset row can exist; and the Asset and its provenance are written in the same
transaction, so a generated picture nobody can trace cannot come into existence.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from time import monotonic, sleep
from typing import Any
from uuid import UUID, uuid4

import PIL.Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.assets.ingest import MediaProcessor, SourceDownloader
from clipah.assets.keys import generated_asset_key
from clipah.assets.probe import MediaValidationError
from clipah.assets.provider_fetch import UnsafeProviderUrlError, validate_provider_media_url
from clipah.assets.storage import ObjectStore, ObjectStoreUnavailableError
from clipah.broll.generation import (
    GenerationHandle,
    GenerationMediaKind,
    GenerationModerationResult,
    GenerationProviderRetryableError,
    GenerationProviderTerminalError,
    GenerationRequest,
    GenerationResult,
    GenerationStatus,
    GenerationUsage,
    GenerativeMediaProvider,
)
from clipah.broll.generation_policy import (
    GenerationProviders,
    GenerationTarget,
    configured_generation_providers,
    generation_request_for,
)
from clipah.broll.models import BrollSourceType, BrollSuggestionStatus
from clipah.config import Settings
from clipah.db import RuntimeRole, session_scope
from clipah.jobs.models import JobCancelledError, JobContext, RetryableJobError, TerminalJobError
from clipah.jobs.use_cases import update_job_progress
from clipah.jobs.workspace import job_workspace
from clipah.models import (
    Asset,
    AssetKind,
    AssetProvenance,
    AssetSourceType,
    BrollSuggestion,
)
from clipah.observability.usage import (
    ProviderCallRecord,
    record_generation_cost,
    record_provider_usage,
)

BROLL_GENERATE_STAGE = "broll_generate"
SUBMITTED_STAGE = "broll_generate_submitted"
REQUEST_NOT_FOUND_CODE = "BROLL_GENERATE_REQUEST_NOT_FOUND"
INTEGRITY_CODE = "BROLL_GENERATE_INTEGRITY"
MEDIA_INVALID_CODE = "BROLL_GENERATE_INVALID_MEDIA"
STORAGE_UNAVAILABLE_CODE = "BROLL_GENERATE_STORAGE_UNAVAILABLE"
PROVIDER_UNAVAILABLE_CODE = "BROLL_GENERATE_PROVIDER_UNAVAILABLE"
POLL_TIMEOUT_CODE = "BROLL_GENERATE_POLL_TIMEOUT"
MEDIA_URL_UNSAFE_CODE = "BROLL_GENERATE_OUTPUT_UNSAFE"

#: Formats a still may arrive in. Anything else is refused before it is decoded.
ALLOWED_IMAGE_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
#: A hard decode ceiling, so a small file claiming enormous dimensions cannot exhaust memory.
MAX_IMAGE_PIXELS = 50_000_000


class BrollGenerationIntegrityError(Exception):
    """Refuse persisted generation evidence this stage cannot trust."""


@dataclass(frozen=True, slots=True)
class GenerationDependencies:
    """Every external capability generation needs, injected so tests can pin all of them."""

    providers: GenerationProviders
    object_store: ObjectStore
    downloader: SourceDownloader
    media: MediaProcessor
    sleep: Callable[[float], None] = sleep
    monotonic: Callable[[], float] = monotonic
    # A generative provider hands back a URL this worker then fetches from inside the
    # deployment's own network, so its destination is proven before anything is read.
    media_url_policy: Callable[[str], str] = validate_provider_media_url


GenerationDependenciesFactory = Callable[[Settings], GenerationDependencies]


@dataclass(frozen=True, slots=True)
class _Pending:
    """The one suggestion this Job was admitted to illustrate, outside any transaction."""

    suggestion_id: UUID
    project_id: UUID
    media_kind: GenerationMediaKind
    request: GenerationRequest
    handle: GenerationHandle | None


@dataclass(frozen=True, slots=True)
class _StoredMedia:
    """One validated generated file, and the facts an Asset row needs about it."""

    asset_id: UUID
    original_key: str
    proxy_key: str | None
    content_type: str
    size_bytes: int
    proxy_size_bytes: int | None
    duration_ms: int | None
    width: int
    height: int
    video_codec: str | None
    audio_codec: str | None
    sha256: bytes


class BrollGenerateStageRunner:
    """Turn one confirmed generation request into one traceable generated Asset."""

    def __init__(
        self,
        *,
        dependencies_factory: GenerationDependenciesFactory,
        clock: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        """Bind production or deterministic capabilities and an injectable clock."""
        self._dependencies_factory = dependencies_factory
        self._clock = clock

    def __call__(self, context: JobContext) -> None:
        """Run one generation attempt through stable retryable and terminal codes."""
        dependencies = self._dependencies_factory(context.settings)
        pending = self._target(context)
        provider = dependencies.providers(pending.media_kind)
        if provider is None:
            raise RetryableJobError(PROVIDER_UNAVAILABLE_CODE)
        try:
            self._run(context, pending=pending, provider=provider, dependencies=dependencies)
        except JobCancelledError:
            self._cancel_with_provider(context, pending=pending, provider=provider)
            raise
        except GenerationProviderRetryableError as error:
            raise RetryableJobError(error.code) from None
        except GenerationProviderTerminalError as error:
            raise TerminalJobError(error.code) from None
        except ObjectStoreUnavailableError:
            raise RetryableJobError(STORAGE_UNAVAILABLE_CODE) from None
        except MediaValidationError:
            raise TerminalJobError(MEDIA_INVALID_CODE) from None
        except BrollGenerationIntegrityError:
            raise TerminalJobError(INTEGRITY_CODE) from None

    def _run(
        self,
        context: JobContext,
        *,
        pending: _Pending,
        provider: GenerativeMediaProvider,
        dependencies: GenerationDependencies,
    ) -> None:
        """Submit or resume one request, then persist exactly what came back."""
        context.raise_if_cancelled()
        self._mark_generating(context, suggestion_id=pending.suggestion_id)
        handle = pending.handle
        if handle is None:
            handle = provider.submit(
                request=pending.request,
                idempotency_key=str(context.job_id),
            )
            self._record_handle(context, suggestion_id=pending.suggestion_id, handle=handle)
            self._report(context, stage=SUBMITTED_STAGE, progress=0.3)

        result = self._await_result(
            context, handle=handle, provider=provider, dependencies=dependencies
        )
        if result.status is not GenerationStatus.SUCCEEDED or result.output is None:
            self._fail_suggestion(context, suggestion_id=pending.suggestion_id)
            raise _terminal_for(result)

        media = self._store_output(
            context, pending=pending, result=result, dependencies=dependencies
        )
        self._persist(context, pending=pending, handle=handle, result=result, media=media)

    def _await_result(
        self,
        context: JobContext,
        *,
        handle: GenerationHandle,
        provider: GenerativeMediaProvider,
        dependencies: GenerationDependencies,
    ) -> GenerationResult:
        """Poll the authoritative provider state until it is terminal or the attempt ends.

        A webhook is only ever a wakeup; the provider's own answer is what this stage acts
        on, so a forged or replayed delivery can never create media.
        """
        attempt_seconds = context.settings.generation_poll_attempt_deadline_seconds
        deadline = dependencies.monotonic() + attempt_seconds
        while True:
            context.raise_if_cancelled()
            result = provider.poll(handle=handle)
            if result.status not in {GenerationStatus.QUEUED, GenerationStatus.RUNNING}:
                return result
            if dependencies.monotonic() >= deadline:
                raise RetryableJobError(POLL_TIMEOUT_CODE)
            dependencies.sleep(context.settings.generation_poll_seconds)

    def _target(self, context: JobContext) -> _Pending:
        """Read the request this Job was admitted for, and any handle it already holds."""
        with _transaction(context) as session:
            row = _suggestion_for_job(session, context)
            if row is None:
                raise TerminalJobError(REQUEST_NOT_FOUND_CODE)
            stored = dict(row.provider_metadata.get("generation", {}))
            try:
                media_kind = GenerationMediaKind(str(stored["media_kind"]))
            except (KeyError, ValueError):
                raise TerminalJobError(REQUEST_NOT_FOUND_CODE) from None
            request = generation_request_for(
                _target_of(row), media_kind=media_kind, settings=context.settings
            )
            return _Pending(
                suggestion_id=row.id,
                project_id=row.project_id,
                media_kind=media_kind,
                request=request,
                handle=_handle_of(stored.get("handle")),
            )

    def _mark_generating(self, context: JobContext, *, suggestion_id: UUID) -> None:
        """Move a requested generation into the state a reviewer can see it working in."""
        with _transaction(context) as session:
            row = _locked(session, context, suggestion_id)
            if row.status is BrollSuggestionStatus.GENERATION_REQUESTED:
                row.status = BrollSuggestionStatus.GENERATING
                session.flush()

    def _record_handle(
        self, context: JobContext, *, suggestion_id: UUID, handle: GenerationHandle
    ) -> None:
        """Persist the opaque provider identity before any poll can be attempted."""
        with _transaction(context) as session:
            row = _locked(session, context, suggestion_id)
            metadata = dict(row.provider_metadata)
            generation = dict(metadata.get("generation", {}))
            generation["handle"] = {
                "provider": handle.provider,
                "provider_request_id": handle.provider_request_id,
                "model": handle.model,
                "model_version": handle.model_version,
                "submitted_at": handle.submitted_at.isoformat(),
                "output_width": handle.output_width,
                "output_height": handle.output_height,
                "output_duration_ms": handle.output_duration_ms,
            }
            metadata["generation"] = generation
            row.provider_metadata = metadata
            session.flush()

    def _store_output(
        self,
        context: JobContext,
        *,
        pending: _Pending,
        result: GenerationResult,
        dependencies: GenerationDependencies,
    ) -> _StoredMedia:
        """Download the ephemeral output once, validate it, and upload what it really is."""
        if result.output is None:  # pragma: no cover - guarded by the caller
            raise BrollGenerationIntegrityError("a success carried no output")
        asset_id = uuid4()
        with job_workspace(context.job_id) as directory:
            original = directory / "original"
            with original.open("wb") as handle:
                downloaded = dependencies.downloader.download(
                    _safe_output_url(result.output.url.get_secret_value(), dependencies),
                    handle,
                    expected_size=0,
                    max_bytes=context.settings.generation_max_output_bytes,
                    cancellation_check=context.raise_if_cancelled,
                )
            if pending.media_kind is GenerationMediaKind.IMAGE:
                content_type, width, height = _validated_image(original)
                original_key = _key(context, pending, asset_id, AssetKind.BROLL)
                stored = _put(dependencies.object_store, original, original_key, content_type)
                return _StoredMedia(
                    asset_id=asset_id,
                    original_key=original_key,
                    proxy_key=None,
                    content_type=content_type,
                    size_bytes=stored.content_length,
                    proxy_size_bytes=None,
                    duration_ms=None,
                    width=width,
                    height=height,
                    video_codec=None,
                    audio_codec=None,
                    sha256=downloaded.sha256,
                )

            metadata = dependencies.media.probe(
                original, cancellation_check=context.raise_if_cancelled
            )
            configured_ms = context.settings.generation_max_duration_ms
            requested_ms = pending.request.duration_ms or configured_ms
            if metadata.duration_ms > max(requested_ms, configured_ms):
                raise MediaValidationError("generated video is longer than the request allowed")
            proxy = directory / "proxy.mp4"
            dependencies.media.generate_proxy(
                original,
                proxy,
                duration_ms=metadata.duration_ms,
                cancellation_check=context.raise_if_cancelled,
                progress=lambda _: None,
            )
            original_key = _key(context, pending, asset_id, AssetKind.BROLL)
            proxy_key = _key(context, pending, asset_id, AssetKind.BROLL_PROXY)
            stored = _put(dependencies.object_store, original, original_key, "video/mp4")
            proxy_stored = _put(dependencies.object_store, proxy, proxy_key, "video/mp4")
            return _StoredMedia(
                asset_id=asset_id,
                original_key=original_key,
                proxy_key=proxy_key,
                content_type="video/mp4",
                size_bytes=stored.content_length,
                proxy_size_bytes=proxy_stored.content_length,
                duration_ms=metadata.duration_ms,
                width=metadata.width,
                height=metadata.height,
                video_codec=metadata.video_codec,
                audio_codec=metadata.audio_codec,
                sha256=downloaded.sha256,
            )

    def _persist(
        self,
        context: JobContext,
        *,
        pending: _Pending,
        handle: GenerationHandle,
        result: GenerationResult,
        media: _StoredMedia,
    ) -> None:
        """Write the Asset, its provenance, the suggestion, and the settlement together."""
        now = self._clock()
        with _transaction(context) as session:
            row = _locked(session, context, pending.suggestion_id)
            if row.asset_id is not None:
                # A redelivery that arrives after the media landed must not buy a second
                # copy of the same picture, or charge the Workspace for it again.
                return
            asset_id = media.asset_id
            session.add(
                _asset(
                    asset_id=asset_id,
                    context=context,
                    project_id=pending.project_id,
                    kind=AssetKind.BROLL,
                    source_type=AssetSourceType.GENERATED,
                    key=media.original_key,
                    content_type=media.content_type,
                    size_bytes=media.size_bytes,
                    media=media,
                )
            )
            if media.proxy_key is not None and media.proxy_size_bytes is not None:
                session.add(
                    _asset(
                        asset_id=uuid4(),
                        context=context,
                        project_id=pending.project_id,
                        kind=AssetKind.BROLL_PROXY,
                        source_type=AssetSourceType.DERIVED,
                        key=media.proxy_key,
                        content_type="video/mp4",
                        size_bytes=media.proxy_size_bytes,
                        media=media,
                    )
                )
            session.flush()
            session.add(
                AssetProvenance(
                    workspace_id=context.workspace_id,
                    asset_id=asset_id,
                    provider=handle.provider,
                    provider_asset_id=handle.provider_request_id,
                    source_url="",
                    author="",
                    author_url="",
                    license_name="Generated media",
                    license_url="",
                    terms_snapshot=_usage_snapshot(result.usage),
                    retrieved_at=now,
                    query="",
                    prompt=pending.request.prompt,
                    model=handle.model,
                    model_version=handle.model_version,
                    seed=None if result.seed is None else str(result.seed),
                    moderation_result=_moderation_of(result),
                    attribution_text="Generated with an AI model",
                    checksum=media.sha256,
                )
            )
            row.asset_id = asset_id
            row.source_type = BrollSourceType.GENERATED
            row.status = BrollSuggestionStatus.PROPOSED
            metadata = dict(row.provider_metadata)
            generation = dict(metadata.get("generation", {}))
            generation["usage"] = _usage_fields(result.usage)
            metadata["generation"] = generation
            row.provider_metadata = metadata
            session.flush()
            settle_generation_usage(
                session,
                workspace_id=context.workspace_id,
                job_id=context.job_id,
                usage=result.usage,
                now=now,
                provider=handle.provider,
                model=handle.model,
                request_id=handle.provider_request_id,
            )
        self._report(context, stage=BROLL_GENERATE_STAGE, progress=0.9)

    def _fail_suggestion(self, context: JobContext, *, suggestion_id: UUID) -> None:
        """Return a refused generation to a state a member can read and act on."""
        with _transaction(context) as session:
            row = _locked(session, context, suggestion_id)
            if row.asset_id is None:
                row.status = BrollSuggestionStatus.FAILED
                session.flush()

    def _cancel_with_provider(
        self, context: JobContext, *, pending: _Pending, provider: GenerativeMediaProvider
    ) -> None:
        """Ask the provider to stop, but never let that request hide the cancellation."""
        handle = pending.handle or _stored_handle(context, suggestion_id=pending.suggestion_id)
        if handle is None:
            return
        try:
            provider.cancel(handle=handle)
        except (GenerationProviderRetryableError, GenerationProviderTerminalError):
            return

    def _report(self, context: JobContext, *, stage: str, progress: float) -> None:
        """Record durable progress with stable stage names and identifiers only."""
        with _transaction(context) as session:
            update_job_progress(
                session,
                workspace_id=context.workspace_id,
                job_id=context.job_id,
                stage=stage,
                progress=progress,
                now=self._clock(),
                detail={"job_id": str(context.job_id)},
            )


def settle_generation_usage(
    session: Session,
    *,
    workspace_id: UUID,
    job_id: UUID,
    usage: GenerationUsage,
    now: datetime,
    provider: str | None = None,
    model: str | None = None,
    request_id: str | None = None,
) -> None:
    """Charge each generation budget the usage a provider actually reported, once.

    The provider identity is optional because a webhook settlement may arrive without the
    handle that produced it; when it is present the same usage is also recorded as a
    Workspace-attributed ledger row and as a cost per exported minute.
    """
    from clipah.jobs.admission import QuotaLedger
    from clipah.models import QuotaResource

    ledger = QuotaLedger(session, limits={})
    actual = {
        QuotaResource.GENERATED_IMAGES: Decimal(usage.generated_images),
        QuotaResource.GENERATED_VIDEOS: Decimal(usage.generated_videos),
        QuotaResource.GENERATED_SECONDS: usage.generated_seconds,
    }
    for resource, units in actual.items():
        ledger.settle_if_reserved(
            workspace_id=workspace_id,
            resource=resource,
            reference_kind="job",
            reference_id=job_id,
            actual_units=units,
            now=now,
        )
    if provider is None or model is None or request_id is None:
        return
    record_provider_usage(
        session,
        workspace_id=workspace_id,
        job_id=job_id,
        call=ProviderCallRecord(
            provider=provider,
            operation="generate",
            model_or_api_version=model,
            request_id=request_id,
            input_units=usage.generated_images + usage.generated_videos,
            output_units=int(usage.generated_seconds),
            estimated_cost_usd=float(usage.cost_usd),
        ),
    )
    record_generation_cost(
        provider=provider,
        cost_usd=float(usage.cost_usd),
        exported_seconds=float(usage.generated_seconds),
    )


def _asset(
    *,
    asset_id: UUID,
    context: JobContext,
    project_id: UUID,
    kind: AssetKind,
    source_type: AssetSourceType,
    key: str,
    content_type: str,
    size_bytes: int,
    media: _StoredMedia,
) -> Asset:
    """Build one Asset row for a validated generated file or its rendition."""
    return Asset(
        id=asset_id,
        workspace_id=context.workspace_id,
        project_id=project_id,
        kind=kind,
        source_type=source_type,
        storage_key=key,
        content_type=content_type,
        size_bytes=size_bytes,
        duration_ms=media.duration_ms,
        width=media.width,
        height=media.height,
        video_codec=media.video_codec,
        audio_codec=media.audio_codec,
        sha256=media.sha256,
    )


def _key(context: JobContext, pending: _Pending, asset_id: UUID, kind: AssetKind) -> str:
    """Name one server-owned location inside the Project's generated prefix."""
    return generated_asset_key(
        workspace_id=context.workspace_id,
        project_id=pending.project_id,
        asset_id=asset_id,
        kind=kind,
    )


def _validated_image(path: Path) -> tuple[str, int, int]:
    """Prove a generated still is one of the formats this product can actually place."""
    data = path.read_bytes()
    try:
        with PIL.Image.open(io.BytesIO(data)) as image:
            image_format = image.format
            width, height = image.size
            image.verify()
    except Exception:
        raise MediaValidationError("generated image could not be decoded") from None
    if image_format not in ALLOWED_IMAGE_FORMATS:
        raise MediaValidationError("generated image format is not supported")
    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise MediaValidationError("generated image dimensions are out of bounds")
    return ALLOWED_IMAGE_FORMATS[image_format], width, height


def _safe_output_url(url: str, dependencies: GenerationDependencies) -> str:
    """Prove the provider's chosen destination before this worker connects to it.

    A generated output URL is ephemeral and provider-controlled, which is exactly the
    value an attacker who reached the provider account would change. A destination inside
    our own network is refused terminally rather than retried.
    """
    try:
        return dependencies.media_url_policy(url)
    except UnsafeProviderUrlError as error:
        raise TerminalJobError(MEDIA_URL_UNSAFE_CODE) from error


def _put(store: ObjectStore, path: Path, key: str, content_type: str) -> Any:
    """Upload one exact local file under one server-chosen key."""
    with path.open("rb") as handle:
        return store.put_file(key=key, content_type=content_type, file=handle)


def _usage_snapshot(usage: GenerationUsage) -> str:
    """Record what the provider charged, in a form an audit can read a year later."""
    return (
        f"generated_images={usage.generated_images}; "
        f"generated_videos={usage.generated_videos}; "
        f"generated_seconds={usage.generated_seconds}; "
        f"provider_credits={usage.provider_credits}; "
        f"cost_usd={usage.cost_usd}"
    )


def _usage_fields(usage: GenerationUsage) -> dict[str, str | int]:
    """Record the same settlement facts beside the suggestion, without provider text."""
    return {
        "generated_images": usage.generated_images,
        "generated_videos": usage.generated_videos,
        "generated_seconds": str(usage.generated_seconds),
        "provider_credits": str(usage.provider_credits),
        "cost_usd": str(usage.cost_usd),
    }


def _moderation_of(result: GenerationResult) -> str:
    """Report the safety evidence a success is only allowed to exist with."""
    if result.moderation is not GenerationModerationResult.APPROVED:
        raise BrollGenerationIntegrityError("a success carried no approval")
    return GenerationModerationResult.APPROVED.value


def _terminal_for(result: GenerationResult) -> Exception:
    """Map one terminal provider outcome onto the stage's own stable codes."""
    if result.status is GenerationStatus.MODERATION_REJECTED:
        return TerminalJobError("GENERATION_MODERATION_REJECTED")
    if result.status is GenerationStatus.CANCELED:
        return TerminalJobError("GENERATION_CANCELED")
    if result.status is GenerationStatus.REJECTED:
        return TerminalJobError("GENERATION_PROVIDER_REJECTED")
    return RetryableJobError("GENERATION_PROVIDER_FAILED")


def _handle_of(stored: object) -> GenerationHandle | None:
    """Rebuild the persisted handle, refusing a partial or unreadable one."""
    if stored is None:
        return None
    if not isinstance(stored, dict):
        raise BrollGenerationIntegrityError("persisted generation handle is unreadable")
    try:
        return GenerationHandle(
            provider=str(stored["provider"]),
            provider_request_id=str(stored["provider_request_id"]),
            model=str(stored["model"]),
            model_version=str(stored["model_version"]),
            submitted_at=datetime.fromisoformat(str(stored["submitted_at"])),
            output_width=_optional_int(stored.get("output_width")),
            output_height=_optional_int(stored.get("output_height")),
            output_duration_ms=_optional_int(stored.get("output_duration_ms")),
        )
    except (KeyError, TypeError, ValueError):
        raise BrollGenerationIntegrityError("persisted generation handle is unreadable") from None


def _optional_int(value: object) -> int | None:
    """Read one optional persisted integer without accepting a boolean or text."""
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("persisted geometry must be an integer")
    return value


def _stored_handle(context: JobContext, *, suggestion_id: UUID) -> GenerationHandle | None:
    """Re-read the handle a cancellation may still need to stop provider work."""
    with _transaction(context) as session:
        row = session.scalar(
            select(BrollSuggestion).where(
                BrollSuggestion.workspace_id == context.workspace_id,
                BrollSuggestion.id == suggestion_id,
            )
        )
        if row is None:
            return None
        stored = dict(row.provider_metadata.get("generation", {}))
        return _handle_of(stored.get("handle"))


def _suggestion_for_job(session: Session, context: JobContext) -> BrollSuggestion | None:
    """Find the one suggestion whose admitted generation names this Job."""
    rows = session.scalars(
        select(BrollSuggestion).where(
            BrollSuggestion.workspace_id == context.workspace_id,
            BrollSuggestion.project_id == context.project_id,
        )
    ).all()
    for row in rows:
        stored = row.provider_metadata.get("generation")
        if isinstance(stored, dict) and stored.get("job_id") == str(context.job_id):
            return row
    return None


def _locked(session: Session, context: JobContext, suggestion_id: UUID) -> BrollSuggestion:
    """Lock one suggestion of this tenant, refusing to invent one that disappeared."""
    row = session.scalar(
        select(BrollSuggestion)
        .where(
            BrollSuggestion.workspace_id == context.workspace_id,
            BrollSuggestion.id == suggestion_id,
        )
        .with_for_update()
    )
    if row is None:
        raise BrollGenerationIntegrityError("suggestion disappeared during generation")
    return row


def _target_of(row: BrollSuggestion) -> GenerationTarget:
    """Read the stored facts the server derives its own request from."""
    return GenerationTarget(
        status=row.status,
        asset_id=row.asset_id,
        relevance_score=None if row.relevance_score is None else float(row.relevance_score),
        visual_intent=dict(row.visual_intent),
        search_terms=dict(row.search_terms),
        exclusions=tuple(row.exclusions),
    )


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


def BrollGenerationDependenciesBuilder(  # noqa: N802 - a factory named for what it returns
    settings: Settings,
) -> GenerationDependencies:
    """Compose the configured generative providers and the pinned media toolchain."""
    from clipah.assets.ingest import HttpxSourceDownloader
    from clipah.assets.storage import observed_s3_store
    from clipah.jobs.ingest_task import _validated_media_runner

    if (
        settings.object_store_bucket is None
        or settings.object_store_access_key_id is None
        or settings.object_store_secret_access_key is None
    ):
        raise RuntimeError("generated B-roll requires configured object storage")

    return GenerationDependencies(  # pragma: no cover - needs the pinned media toolchain
        providers=configured_generation_providers(settings),
        object_store=observed_s3_store(
            bucket=settings.object_store_bucket,
            endpoint_url=settings.object_store_endpoint,
            access_key_id=settings.object_store_access_key_id.get_secret_value(),
            secret_access_key=settings.object_store_secret_access_key.get_secret_value(),
        ),
        downloader=HttpxSourceDownloader(),
        media=_validated_media_runner(),
    )


broll_generate_stage_runner = BrollGenerateStageRunner(
    dependencies_factory=BrollGenerationDependenciesBuilder
)
