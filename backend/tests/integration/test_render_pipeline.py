"""Integration contracts for exporting one Edit Revision as a file.

Two halves live here. The first drives the export endpoints against Postgres: admission,
deduplication by composition hash, tenant scoping, and the five-minute capability a member
downloads with. The second runs FFmpeg for real over checked-in fixture media and reads
the result back with ffprobe, because a render that was never executed proves nothing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select, text, update

from clipah.assets.ingest import DownloadedSource
from clipah.assets.storage import FakeObjectStore, ObjectStoreUnavailableError, StoredObject
from clipah.db import RuntimeRole, session_scope
from clipah.editor.models import parse_composition
from clipah.jobs.models import JobCancelledError, JobContext
from clipah.jobs.render_task import RENDER_TARGET_MISSING, RenderStageRunner
from clipah.jobs.use_cases import start_job
from clipah.models import (
    Asset,
    AssetKind,
    AssetSourceType,
    ClipCandidate,
    Job,
    JobKind,
    Project,
    ProjectStatus,
    RenderArtifact,
    RenderRequest,
    Transcript,
    WorkspaceRole,
)
from clipah.renders.compiler import compile_render_plan, input_path
from clipah.renders.ffmpeg_renderer import FFmpegRenderer
from clipah.renders.models import RenderAsset, RenderCompilationError, RenderPreset
from harness import NOW, Browser, Clock, StubGoogleProvider, assert_error, build_app, sign_in
from support import runtime_settings

_STORAGE_OUTAGE = "storage-outage"
_ENCODER_TIMEOUT = "encoder-timeout"
_ENCODER_FAILURE = "encoder-failure"
_UNRENDERABLE = "unsupported-feature"
_SHORT_OUTPUT = "duration-mismatch"
_CORRUPT_UPLOAD = "corrupt-upload"
_CONCURRENT_JOB_LIMIT = runtime_settings().concurrent_jobs_per_workspace


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "media"
LANDSCAPE = FIXTURE_ROOT / "landscape.mp4"
PORTRAIT = FIXTURE_ROOT / "portrait.mp4"


@dataclass(frozen=True, slots=True)
class Stage:
    """One signed-in owner, their ready Project, and the Edit they are exporting."""

    browser: Browser
    store: FakeObjectStore
    provider: StubGoogleProvider
    flow: Any
    engine: Engine
    workspace_id: UUID
    project_id: UUID
    source_asset_id: UUID
    edit_id: UUID
    revision_id: UUID
    composition_hash: bytes


@pytest.mark.integration
def test_requesting_an_export_admits_one_render_job_that_knows_what_to_render(
    engine: Engine,
) -> None:
    """A Job carries only identifiers, so what it renders has to be durable somewhere."""
    stage = _staged(engine)

    accepted = _request(stage, RenderPreset.PORTRAIT)

    assert accepted.status_code == 202
    body = accepted.json()
    assert body["status"] == "rendering"
    job_id = UUID(body["jobId"])
    with _api_session(stage) as session:
        recorded = session.execute(
            select(
                RenderRequest.preset,
                RenderRequest.clip_edit_revision_id,
                RenderRequest.composition_hash,
                Job.kind,
            )
            .join(Job, Job.id == RenderRequest.job_id)
            .where(RenderRequest.job_id == job_id)
        ).one()
    preset, revision_id, composition_hash, kind = recorded
    assert kind is JobKind.RENDER
    assert preset == RenderPreset.PORTRAIT.value
    assert revision_id == stage.revision_id
    assert bytes(composition_hash) == stage.composition_hash


@pytest.mark.integration
def test_an_identical_export_is_handed_back_instead_of_rendered_again(engine: Engine) -> None:
    """The same composition at the same preset is the same file; encoding it twice is waste."""
    stage = _staged(engine)
    artifact_id = _store_artifact(stage, preset=RenderPreset.PORTRAIT)

    reused = _request(stage, RenderPreset.PORTRAIT)

    assert reused.status_code == 200
    assert reused.json()["status"] == "ready"
    assert reused.json()["id"] == str(artifact_id)
    assert reused.json()["durationMs"] == 30_000
    with _api_session(stage) as session:
        assert session.scalars(select(Job.id).where(Job.kind == JobKind.RENDER)).all() == []


@pytest.mark.integration
def test_another_preset_of_the_same_composition_is_a_new_export(engine: Engine) -> None:
    """Deduplication is by composition and preset together, never by composition alone."""
    stage = _staged(engine)
    _store_artifact(stage, preset=RenderPreset.PORTRAIT)

    other = _request(stage, RenderPreset.SQUARE)

    assert other.status_code == 202
    assert other.json()["jobId"] is not None


@pytest.mark.integration
def test_an_export_response_never_carries_the_key_the_file_is_stored_under(
    engine: Engine,
) -> None:
    """A storage key is a capability's target; a member gets the capability instead."""
    stage = _staged(engine)
    artifact_id = _store_artifact(stage, preset=RenderPreset.PORTRAIT)

    shown = stage.browser.get(_path(stage, f"/renders/{artifact_id}"))
    signed = stage.browser.get(_path(stage, f"/renders/{artifact_id}/download-url"))

    assert shown.status_code == 200
    assert shown.json()["preset"] == RenderPreset.PORTRAIT.value
    assert "workspaces/" not in shown.text
    assert signed.status_code == 200
    assert signed.json()["url"].startswith("fake://download/")
    assert signed.json()["expiresAt"]


@pytest.mark.integration
def test_another_workspace_export_is_indistinguishable_from_one_that_never_existed(
    engine: Engine,
) -> None:
    """A guessed export identifier answers exactly like an unissued one."""
    stage = _staged(engine)
    artifact_id = _store_artifact(stage, preset=RenderPreset.PORTRAIT)
    stage.provider.identify(
        subject="render-stranger", email="stranger@example.com", name="Stranger"
    )
    stranger = Browser(stage.browser.app)
    sign_in(stranger, stage.flow)
    stranger_workspace = UUID(stranger.get("/api/v1/workspaces").json()["workspaces"][0]["id"])

    guessed = stranger.get(
        f"/api/v1/renders/{artifact_id}?workspace_id={stranger_workspace}",
    )
    missing = stranger.get(f"/api/v1/renders/{uuid4()}?workspace_id={stranger_workspace}")
    guessed_edit = stranger.request(
        "POST",
        f"/api/v1/edits/{stage.edit_id}/renders?workspace_id={stranger_workspace}",
        headers={"Idempotency-Key": "stranger-render"},
        json={"preset": RenderPreset.PORTRAIT.value},
    )

    for response in (guessed, missing, guessed_edit):
        assert_error(response, status_code=404, code="NOT_FOUND")
    assert guessed.json()["error"]["message"] == missing.json()["error"]["message"]


@pytest.mark.integration
def test_an_export_request_without_csrf_proof_is_refused(engine: Engine) -> None:
    """Rendering spends real capacity, so it is a state-changing request like any other."""
    stage = _staged(engine)

    refused = stage.browser.request(
        "POST",
        _path(stage, f"/edits/{stage.edit_id}/renders"),
        csrf_token="",
        headers={"Idempotency-Key": "no-csrf"},
        json={"preset": RenderPreset.PORTRAIT.value},
    )

    assert_error(refused, status_code=403, code="CSRF_FAILED")


@pytest.mark.integration
def test_a_reviewer_may_read_an_export_but_not_ask_for_one(engine: Engine) -> None:
    """Review authority is not editing authority, and an export is editing output."""
    stage = _staged(engine)
    artifact_id = _store_artifact(stage, preset=RenderPreset.PORTRAIT)
    _set_role(stage, WorkspaceRole.REVIEWER)

    read = stage.browser.get(_path(stage, f"/renders/{artifact_id}"))
    refused = _request(stage, RenderPreset.SQUARE)

    assert read.status_code == 200
    assert_error(refused, status_code=403, code="FORBIDDEN")


@pytest.mark.integration
def test_a_preset_this_product_does_not_export_is_refused(engine: Engine) -> None:
    """Export presets are a closed set; an unknown one is a client defect."""
    stage = _staged(engine)

    refused = stage.browser.request(
        "POST",
        _path(stage, f"/edits/{stage.edit_id}/renders"),
        headers={"Idempotency-Key": "bad-preset"},
        json={"preset": "4096x4096"},
    )

    assert_error(refused, status_code=422, code="VALIDATION_ERROR")


@pytest.mark.integration
def test_a_workspace_at_its_concurrency_limit_is_refused_an_export(engine: Engine) -> None:
    """Rendering is capacity, so it queues behind the same limit every other Job does."""
    stage = _staged(engine)
    for index in range(_CONCURRENT_JOB_LIMIT):
        admitted = stage.browser.request(
            "POST",
            _path(stage, f"/edits/{stage.edit_id}/renders"),
            headers={"Idempotency-Key": f"limit-{index}"},
            json={"preset": list(RenderPreset)[index % len(RenderPreset)].value},
        )
        assert admitted.status_code == 202

    refused = stage.browser.request(
        "POST",
        _path(stage, f"/edits/{stage.edit_id}/renders"),
        headers={"Idempotency-Key": "limit-over"},
        json={"preset": RenderPreset.PORTRAIT.value},
    )

    assert_error(refused, status_code=429, code="CONCURRENCY_LIMIT")


@pytest.mark.integration
def test_a_download_capability_for_an_unknown_export_is_refused(engine: Engine) -> None:
    """Signing a URL for an export that is not there would leak that it is not there."""
    stage = _staged(engine)

    refused = stage.browser.get(_path(stage, f"/renders/{uuid4()}/download-url"))

    assert_error(refused, status_code=404, code="NOT_FOUND")


@pytest.mark.integration
def test_the_render_stage_stores_one_artifact_and_a_redelivery_stores_no_second_one(
    engine: Engine,
) -> None:
    """A redelivered Job must converge on the export the first delivery produced."""
    stage = _staged(engine)
    job_id = UUID(_request(stage, RenderPreset.PORTRAIT).json()["jobId"])
    runner = _fake_runner(stage)
    context = _context(stage, job_id)
    _start(stage, job_id)

    runner(context)
    runner(context)

    with _api_session(stage) as session:
        artifacts = session.execute(
            select(
                RenderArtifact.composition_hash,
                RenderArtifact.preset,
                RenderArtifact.size_bytes,
                RenderArtifact.storage_key,
                RenderArtifact.sha256,
            )
        ).all()
    assert len(artifacts) == 1
    composition_hash, preset, size_bytes, storage_key, artifact_sha256 = artifacts[0]
    assert bytes(composition_hash) == stage.composition_hash
    assert preset == RenderPreset.PORTRAIT.value
    assert size_bytes > 0
    assert storage_key in stage.store.objects
    assert bytes(artifact_sha256) == stage.store.objects[storage_key].sha256


@pytest.mark.integration
def test_a_render_job_with_no_recorded_target_fails_terminally(engine: Engine) -> None:
    """A Job that cannot say what it renders is a defect, not something to guess at."""
    stage = _staged(engine)
    job_id = UUID(_request(stage, RenderPreset.PORTRAIT).json()["jobId"])
    # Only the migrator may delete a render request; the API role may not, by design.
    with stage.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM render_requests WHERE job_id = :job_id"), {"job_id": job_id}
        )

    with pytest.raises(Exception) as failure:
        _fake_runner(stage)(_context(stage, job_id))

    assert RENDER_TARGET_MISSING in str(failure.value)


@pytest.mark.integration
def test_an_export_that_breaks_its_declared_brand_kit_fails_terminally(engine: Engine) -> None:
    """The editor showed a member these rules, so the exporter holds the clip to them."""
    stage = _staged(engine)
    _declare_brand_kit(stage, allowed_background="#123456")
    job_id = UUID(_request(stage, RenderPreset.PORTRAIT).json()["jobId"])
    _start(stage, job_id)

    with pytest.raises(Exception) as raised:
        _fake_runner(stage)(_context(stage, job_id))

    assert "RENDER_BRAND_VIOLATION" in str(raised.value)


@pytest.mark.integration
def test_an_export_that_honours_its_declared_brand_kit_still_renders(engine: Engine) -> None:
    """A gate that refused a compliant clip would stop every branded export this makes."""
    stage = _staged(engine)
    _declare_brand_kit(stage, allowed_background="#000000")
    job_id = UUID(_request(stage, RenderPreset.PORTRAIT).json()["jobId"])
    _start(stage, job_id)

    _fake_runner(stage)(_context(stage, job_id))

    with _api_session(stage) as session:
        assert session.scalars(select(RenderArtifact)).all()


def _declare_brand_kit(stage: Stage, *, allowed_background: str) -> UUID:
    """Publish one Brand Kit and save a Revision that declares it was judged by it."""
    brand_kit_id = uuid4()
    definition = {
        "logoAssetId": None,
        "fonts": [{"family": "Montserrat", "assetId": None}],
        "colors": [
            {"name": "Allowed", "hex": allowed_background},
            {"name": "Paper", "hex": "#FFFFFF"},
            {"name": "Highlight", "hex": "#FFD166"},
        ],
        "captionRules": {
            "minFontSize": 12,
            "maxFontSize": 200,
            "allowedAlignments": ["left", "center", "right"],
            "reservedPlacements": [],
        },
        "visualExclusions": [],
        "claimRules": {"requiredAttribution": None, "forbiddenClaimPhrases": []},
    }
    created = stage.browser.request(
        "POST",
        f"/api/v1/brand-kits?workspace_id={stage.workspace_id}",
        json={"name": "Kanal", "definition": definition},
    )
    assert created.status_code == 201
    brand_kit_id = UUID(created.json()["id"])

    edit = stage.browser.get(f"/api/v1/edits/{stage.edit_id}?workspace_id={stage.workspace_id}")
    composition = edit.json()["composition"]
    composition["brandKit"] = {"id": str(brand_kit_id), "version": 1, "logoAssetId": None}
    saved = stage.browser.request(
        "PUT",
        f"/api/v1/edits/{stage.edit_id}?workspace_id={stage.workspace_id}",
        json={
            "expectedRevision": edit.json()["currentRevision"],
            "composition": composition,
        },
    )
    assert saved.status_code == 200
    return brand_kit_id


@pytest.mark.integration
@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        pytest.param(_STORAGE_OUTAGE, "ASSET_STORAGE_UNAVAILABLE", id="storage-outage"),
        pytest.param(_ENCODER_TIMEOUT, "MEDIA_PROCESS_TIMEOUT", id="encoder-timeout"),
        pytest.param(_ENCODER_FAILURE, "RENDER_FAILED", id="encoder-failure"),
        pytest.param(_UNRENDERABLE, "RENDER_FEATURE_UNSUPPORTED", id="unsupported-feature"),
        pytest.param(_SHORT_OUTPUT, "RENDER_DURATION_MISMATCH", id="duration-mismatch"),
    ],
)
def test_every_render_failure_reaches_the_job_as_its_own_stable_code(
    engine: Engine, failure: str, expected: str
) -> None:
    """A member reads a code, so each way a render can fail has to have exactly one."""
    stage = _staged(engine)
    job_id = UUID(_request(stage, RenderPreset.PORTRAIT).json()["jobId"])
    _start(stage, job_id)

    with pytest.raises(Exception) as raised:
        _failing_runner(stage, failure)(_context(stage, job_id))

    assert expected in str(raised.value)


@pytest.mark.integration
def test_an_export_whose_stored_bytes_do_not_match_is_deleted_rather_than_recorded(
    engine: Engine,
) -> None:
    """An artifact row is a promise that the file behind it is the one that was rendered."""
    stage = _staged(engine)
    job_id = UUID(_request(stage, RenderPreset.PORTRAIT).json()["jobId"])
    _start(stage, job_id)

    with pytest.raises(Exception) as raised:
        _failing_runner(stage, _CORRUPT_UPLOAD)(_context(stage, job_id))

    assert "RENDER_INTEGRITY" in str(raised.value)
    with _api_session(stage) as session:
        assert session.scalars(select(RenderArtifact.id)).all() == []


@pytest.mark.integration
def test_a_composition_naming_media_the_project_no_longer_owns_fails_terminally(
    engine: Engine,
) -> None:
    """A render reads media; media that is gone is a defect, not something to improvise."""
    stage = _staged(engine)
    job_id = UUID(_request(stage, RenderPreset.PORTRAIT).json()["jobId"])
    _start(stage, job_id)
    elsewhere = stage.browser.request(
        "POST",
        f"/api/v1/projects?workspace_id={stage.workspace_id}",
        headers={"Idempotency-Key": f"render-elsewhere-{uuid4().hex[:8]}"},
        json={"name": "Elsewhere", "sourceKind": "upload"},
    )
    assert elsewhere.status_code == 201
    with stage.engine.begin() as connection:
        connection.execute(
            text("UPDATE assets SET project_id = :other WHERE id = :asset_id"),
            {"other": UUID(elsewhere.json()["id"]), "asset_id": stage.source_asset_id},
        )

    with pytest.raises(Exception) as raised:
        _fake_runner(stage)(_context(stage, job_id))

    assert "RENDER_INTEGRITY" in str(raised.value)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("scenario", "preset"),
    [
        ("no-broll", RenderPreset.PORTRAIT),
        ("smart-crop", RenderPreset.PORTRAIT),
        ("stock-image", RenderPreset.PORTRAIT),
        ("stock-video", RenderPreset.LANDSCAPE),
        ("generated-video", RenderPreset.SQUARE),
    ],
)
def test_real_ffmpeg_renders_each_fixture_composition_into_a_playable_export(
    tmp_path: Path, scenario: str, preset: RenderPreset
) -> None:
    """The plan has to survive contact with FFmpeg, and the file has to survive ffprobe."""
    _require_media_tools()
    workspace = tmp_path / "job"
    (workspace / "inputs").mkdir(parents=True)
    assets = _stage_inputs(workspace, scenario, tmp_path)
    composition = parse_composition(_fixture_composition(scenario))

    plan = compile_render_plan(
        composition,
        assets=assets,
        preset=preset,
        workspace=workspace,
    )
    output = FFmpegRenderer(duration_probe=_probe_duration_ms).render(
        plan,
        workspace=workspace,
        cancellation_check=lambda: None,
        progress=lambda _ratio: None,
    )

    probed = _probe(output.path)
    video = next(entry for entry in probed["streams"] if entry["codec_type"] == "video")
    audio = [entry for entry in probed["streams"] if entry["codec_type"] == "audio"]
    assert (video["width"], video["height"]) == (plan.width, plan.height)
    assert video["codec_name"] == "h264"
    assert audio, "dialogue audio must still be mapped into the export"
    assert abs(output.duration_ms - composition.duration_ms) <= 250
    assert output.size_bytes > 0


@pytest.mark.integration
def test_cancelling_a_render_stops_the_ffmpeg_process_group(tmp_path: Path) -> None:
    """Cancellation is only real if the encoder actually stops when a member asks."""
    _require_media_tools()
    workspace = tmp_path / "job"
    (workspace / "inputs").mkdir(parents=True)
    assets = _stage_inputs(workspace, "no-broll", tmp_path)
    composition = parse_composition(_fixture_composition("no-broll"))
    plan = compile_render_plan(
        composition, assets=assets, preset=RenderPreset.PORTRAIT, workspace=workspace
    )
    polls = {"count": 0}

    def cancel_after_first_poll() -> None:
        polls["count"] += 1
        if polls["count"] > 1:
            raise JobCancelledError("cancelled")

    before = _ffmpeg_process_count()
    started = time.monotonic()
    with pytest.raises(JobCancelledError):
        FFmpegRenderer(duration_probe=_probe_duration_ms).render(
            plan,
            workspace=workspace,
            cancellation_check=cancel_after_first_poll,
            progress=lambda _ratio: None,
        )
    elapsed = time.monotonic() - started

    assert elapsed < 10
    time.sleep(0.5)
    assert _ffmpeg_process_count() <= before


def _path(stage: Stage, suffix: str) -> str:
    """Build one URL carrying the Workspace selection the caller is authorized for."""
    separator = "&" if "?" in suffix else "?"
    return f"/api/v1{suffix}{separator}workspace_id={stage.workspace_id}"


def _request(stage: Stage, preset: RenderPreset) -> Any:
    """Ask for one export of the Edit's current Revision."""
    return stage.browser.request(
        "POST",
        _path(stage, f"/edits/{stage.edit_id}/renders"),
        headers={"Idempotency-Key": f"render-{preset.value}"},
        json={"preset": preset.value},
    )


def _store_artifact(stage: Stage, *, preset: RenderPreset) -> UUID:
    """Record one healthy export of the staged Revision, as a finished Job would."""
    artifact_id = uuid4()
    key = (
        f"workspaces/{stage.workspace_id}/projects/{stage.project_id}"
        f"/renders/{stage.revision_id}/{preset.value}.mp4"
    )
    with _worker_session(stage) as session:
        session.add(
            RenderArtifact(
                id=artifact_id,
                workspace_id=stage.workspace_id,
                clip_edit_revision_id=stage.revision_id,
                job_id=None,
                preset=preset.value,
                composition_hash=stage.composition_hash,
                storage_key=key,
                size_bytes=4_096,
                duration_ms=30_000,
            )
        )
    stage.store.objects[key] = StoredObject(key=key, content_type="video/mp4", content_length=4_096)
    return artifact_id


def _set_role(stage: Stage, role: WorkspaceRole) -> None:
    """Move the signed-in member to another role inside their own Workspace."""
    user_id = stage.browser.get("/api/v1/me").json()["id"]
    with _api_session(stage) as session:
        session.execute(
            text(
                "UPDATE workspace_memberships SET role = :role "
                "WHERE workspace_id = :workspace_id AND user_id = :user_id"
            ),
            {"role": role.value, "workspace_id": stage.workspace_id, "user_id": user_id},
        )


def _start(stage: Stage, job_id: UUID) -> None:
    """Move one admitted Job into the running state its worker reports progress from."""
    with _worker_session(stage) as session:
        start_job(session, workspace_id=stage.workspace_id, job_id=job_id, now=NOW)


def _context(stage: Stage, job_id: UUID) -> JobContext:
    """Build the identifiers one worker stage is allowed to see."""
    user_id = UUID(stage.browser.get("/api/v1/me").json()["id"])
    return JobContext(
        job_id=job_id,
        workspace_id=stage.workspace_id,
        project_id=stage.project_id,
        user_id=user_id,
        attempt=1,
        settings=runtime_settings(RuntimeRole.WORKER),
    )


class _FakeRenderer:
    """Produce a file the way FFmpeg would, without running an encoder."""

    def render(
        self,
        plan: Any,
        *,
        workspace: Path,
        cancellation_check: Callable[[], None],
        progress: Callable[[float], None],
    ) -> Any:
        """Write a deterministic stand-in artifact and report it like the real renderer."""
        from clipah.renders.ffmpeg_renderer import OUTPUT_NAME, _digest, _write_plan_files
        from clipah.renders.models import RenderOutput

        _write_plan_files(plan, workspace)
        output = workspace / OUTPUT_NAME
        output.write_bytes(b"rendered" * 128)
        progress(1.0)
        return RenderOutput(
            path=output,
            duration_ms=plan.duration_ms,
            size_bytes=output.stat().st_size,
            sha256=_digest(output),
        )


class _FakeDownloader:
    """Answer one signed capability with bytes, without leaving the process."""

    def download(
        self,
        url: str,
        destination: BinaryIO,
        *,
        expected_size: int,
        max_bytes: int,
        cancellation_check: Callable[[], None],
    ) -> DownloadedSource:
        """Write deterministic placeholder media the fake renderer never reads."""
        assert url.startswith("fake://download/")
        body = b"media" * 16
        destination.write(body)
        return DownloadedSource(size_bytes=len(body), sha256=b"m" * 32)


class _BrokenRenderer:
    """Fail one render the way a real encoder fails, without running one."""

    def __init__(self, failure: str) -> None:
        """Bind the one failure this renderer reproduces."""
        self._failure = failure

    def render(
        self,
        plan: Any,
        *,
        workspace: Path,
        cancellation_check: Callable[[], None],
        progress: Callable[[float], None],
    ) -> Any:
        """Raise exactly what the corresponding real failure would raise."""
        from clipah.assets.ffmpeg import FFMPEG_FAILED, MEDIA_PROCESS_TIMEOUT, MediaProcessError
        from clipah.renders.ffmpeg_renderer import RenderExecutionError
        from clipah.renders.models import DURATION_MISMATCH, FEATURE_UNSUPPORTED

        if self._failure == _ENCODER_TIMEOUT:
            raise MediaProcessError(MEDIA_PROCESS_TIMEOUT)
        if self._failure == _ENCODER_FAILURE:
            raise MediaProcessError(FFMPEG_FAILED)
        if self._failure == _UNRENDERABLE:
            raise RenderCompilationError(FEATURE_UNSUPPORTED, "an effect nothing can reproduce")
        raise RenderExecutionError(DURATION_MISMATCH, "the file is shorter than the composition")


class _UnavailableStore:
    """An object store that is simply not answering right now."""

    def sign_download(self, *, key: str, expires_in: Any) -> Any:
        """Refuse the way a provider outage refuses."""
        raise ObjectStoreUnavailableError(key)


def _failing_runner(stage: Stage, failure: str) -> RenderStageRunner:
    """Compose the render stage against one deliberately broken collaborator."""
    if failure == _STORAGE_OUTAGE:
        return RenderStageRunner(
            renderer_factory=lambda _settings: _FakeRenderer(),  # type: ignore[arg-type, return-value]
            store_factory=lambda _settings: _UnavailableStore(),  # type: ignore[arg-type, return-value]
            downloader_factory=lambda _settings: _FakeDownloader(),
        )
    if failure == _CORRUPT_UPLOAD:
        return RenderStageRunner(
            renderer_factory=lambda _settings: _FakeRenderer(),  # type: ignore[arg-type, return-value]
            store_factory=lambda _settings: _CorruptingStore(stage.store),  # type: ignore[arg-type, return-value]
            downloader_factory=lambda _settings: _FakeDownloader(),
        )
    return RenderStageRunner(
        renderer_factory=lambda _settings: _BrokenRenderer(failure),  # type: ignore[arg-type, return-value]
        store_factory=lambda _settings: stage.store,
        downloader_factory=lambda _settings: _FakeDownloader(),
    )


class _CorruptingStore:
    """A store that accepts an upload and then reports different bytes back."""

    def __init__(self, inner: FakeObjectStore) -> None:
        """Wrap the fake store every other operation still goes through."""
        self._inner = inner
        self.deleted: list[str] = []

    def sign_download(self, *, key: str, expires_in: Any) -> Any:
        """Sign the way the wrapped store does."""
        return self._inner.sign_download(key=key, expires_in=expires_in)

    def put_file(
        self, *, key: str, content_type: str, file: Any, sha256: bytes | None = None
    ) -> Any:
        """Accept the upload and record it honestly."""
        return self._inner.put_file(key=key, content_type=content_type, file=file, sha256=sha256)

    def head_object(self, *, key: str) -> StoredObject:
        """Report a length that does not match what was uploaded."""
        stored = self._inner.head_object(key=key)
        return StoredObject(
            key=stored.key,
            content_type=stored.content_type,
            content_length=stored.content_length + 1,
            sha256=stored.sha256,
        )

    def delete_object(self, *, key: str) -> None:
        """Remove the object the caller refused to record."""
        self.deleted.append(key)
        self._inner.delete_object(key=key)


def _fake_runner(stage: Stage) -> RenderStageRunner:
    """Compose the render stage against the fake store, renderer, and downloader."""
    return RenderStageRunner(
        renderer_factory=lambda _settings: _FakeRenderer(),  # type: ignore[arg-type, return-value]
        store_factory=lambda _settings: stage.store,
        downloader_factory=lambda _settings: _FakeDownloader(),
    )


def _api_session(stage: Stage) -> Any:
    """Open one API-role transaction inside the staged Workspace's tenant context."""
    user_id = UUID(stage.browser.get("/api/v1/me").json()["id"])
    return session_scope(
        settings=runtime_settings(),
        workspace_id=stage.workspace_id,
        user_id=user_id,
        runtime_role=RuntimeRole.API,
    )


def _worker_session(stage: Stage) -> Any:
    """Open one worker-role transaction, which is the role that may store an export."""
    user_id = UUID(stage.browser.get("/api/v1/me").json()["id"])
    return session_scope(
        settings=runtime_settings(RuntimeRole.WORKER),
        workspace_id=stage.workspace_id,
        user_id=user_id,
        runtime_role=RuntimeRole.WORKER,
    )


def _staged(engine: Engine) -> Stage:
    """Sign in, stage a reviewed candidate, and open the Edit an export is made from."""
    clock = Clock(NOW)
    store = FakeObjectStore(now=lambda: NOW)
    provider = StubGoogleProvider(clock)
    app, flow, _ = build_app(clock, provider, object_store=store)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    created = browser.request(
        "POST",
        f"/api/v1/projects?workspace_id={workspace_id}",
        headers={"Idempotency-Key": f"render-project-{uuid4().hex[:8]}"},
        json={"name": "Render project", "sourceKind": "upload"},
    )
    assert created.status_code == 201
    project_id = UUID(created.json()["id"])
    source_asset_id = uuid4()
    transcript_id = uuid4()
    candidate_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(status=ProjectStatus.READY, updated_at=NOW)
        )
        connection.execute(
            Asset.__table__.insert().values(
                id=source_asset_id,
                workspace_id=workspace_id,
                project_id=project_id,
                kind=AssetKind.SOURCE,
                source_type=AssetSourceType.USER_UPLOAD,
                storage_key=f"workspaces/{workspace_id}/projects/{project_id}/source/original",
                content_type="video/mp4",
                size_bytes=4_096,
                duration_ms=60_000,
                width=1920,
                height=1080,
                sha256=b"s" * 32,
            )
        )
        connection.execute(
            Transcript.__table__.insert().values(
                id=transcript_id,
                workspace_id=workspace_id,
                project_id=project_id,
                asset_id=source_asset_id,
                provider="assemblyai",
                provider_version="1.0.0",
                model="universal-2",
                language="id",
                full_text="Satu dua",
                words=[
                    {
                        "word_id": "w000001",
                        "text": "Satu",
                        "punctuation": "",
                        "start_ms": 5_000,
                        "end_ms": 5_900,
                        "confidence": 0.99,
                        "speaker": "SPEAKER_00",
                    }
                ],
                speaker_segments=[],
                utterances=[],
                duration_ms=60_000,
                raw_result_storage_key=(
                    f"workspaces/{workspace_id}/projects/{project_id}/transcripts/raw.json"
                ),
            )
        )
        connection.execute(
            ClipCandidate.__table__.insert().values(
                id=candidate_id,
                workspace_id=workspace_id,
                project_id=project_id,
                transcript_id=transcript_id,
                rank=1,
                score=0.9,
                hook="Hook",
                payoff="Payoff",
                reason="Reason",
                category="insight",
                tags=["creator"],
                start_ms=5_000,
                end_ms=35_000,
                start_word_id="w000001",
                end_word_id="w000001",
                transcript_excerpt="Satu",
                context_dependencies=[],
                score_breakdown={
                    "hook": 0.9,
                    "payoff": 0.9,
                    "narrative_completeness": 0.9,
                    "context_safety": 0.9,
                    "platform_fit": 0.9,
                    "transcript_confidence": 0.9,
                    "visual_opportunity": 0.9,
                },
                context_warnings=[],
                visual_opportunities=[],
                model_metadata={"exposed": True},
                created_at=NOW,
            )
        )
    opened = browser.request(
        "POST",
        f"/api/v1/projects/{project_id}/candidates/{candidate_id}/edits?workspace_id={workspace_id}",
        json=None,
    )
    assert opened.status_code == 201
    edit_id = UUID(opened.json()["id"])
    with engine.begin() as connection:
        revision_id, composition_hash = connection.execute(
            text(
                "SELECT id, composition_hash FROM clip_edit_revisions "
                "WHERE clip_edit_id = :edit_id AND revision = 1"
            ),
            {"edit_id": edit_id},
        ).one()
    store.objects[f"workspaces/{workspace_id}/projects/{project_id}/source/original"] = (
        StoredObject(
            key=f"workspaces/{workspace_id}/projects/{project_id}/source/original",
            content_type="video/mp4",
            content_length=4_096,
        )
    )
    return Stage(
        browser=browser,
        store=store,
        provider=provider,
        flow=flow,
        engine=engine,
        workspace_id=workspace_id,
        project_id=project_id,
        source_asset_id=source_asset_id,
        edit_id=edit_id,
        revision_id=revision_id,
        composition_hash=bytes(composition_hash),
    )


def _require_media_tools() -> None:
    """Skip a real render when this host has no FFmpeg to run it with."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("real media tools are unavailable")


def _stage_inputs(workspace: Path, scenario: str, tmp_path: Path) -> dict[UUID, RenderAsset]:
    """Copy the fixture media one scenario needs into the Job workspace."""
    source_id = UUID("11111111-1111-4111-8111-111111111111")
    assets = {
        source_id: RenderAsset(
            asset_id=source_id,
            kind=AssetKind.SOURCE,
            content_type="video/mp4",
            duration_ms=_probe_duration_ms(LANDSCAPE),
            width=640,
            height=360,
        )
    }
    shutil.copy(LANDSCAPE, input_path(workspace, source_id))
    if scenario == "stock-image":
        image_id = UUID("33333333-3333-4333-8333-333333333333")
        still = tmp_path / "still.jpg"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(LANDSCAPE),
                "-frames:v",
                "1",
                "-y",
                str(still),
            ],
            check=True,
        )
        shutil.copy(still, input_path(workspace, image_id))
        assets[image_id] = RenderAsset(
            asset_id=image_id,
            kind=AssetKind.SOURCE,
            content_type="image/jpeg",
            duration_ms=None,
            width=640,
            height=360,
        )
    if scenario in {"stock-video", "generated-video"}:
        clip_id = UUID("22222222-2222-4222-8222-222222222222")
        shutil.copy(PORTRAIT, input_path(workspace, clip_id))
        assets[clip_id] = RenderAsset(
            asset_id=clip_id,
            kind=AssetKind.SOURCE,
            content_type="video/mp4",
            duration_ms=_probe_duration_ms(PORTRAIT),
            width=360,
            height=640,
        )
    return assets


def _fixture_composition(scenario: str) -> dict[str, Any]:
    """One composition per rendered scenario, kept as small as the assertion allows."""
    source_id = "11111111-1111-4111-8111-111111111111"
    document: dict[str, Any] = {
        "schemaVersion": 1,
        "sourceAssetId": source_id,
        "durationMs": 1_000,
        "canvas": {"width": 1080, "height": 1920, "background": "#000000"},
        "sourceRange": {"inMs": 0, "outMs": 1_000},
        "template": None,
        "brandKit": None,
        "tracks": [
            {
                "id": "main-video",
                "type": "video",
                "items": [
                    {
                        "id": "scene-1",
                        "sourceAssetId": source_id,
                        "timelineStartMs": 0,
                        "sourceInMs": 0,
                        "sourceOutMs": 1_000,
                        "transform": {"x": 0.5, "y": 0.5, "scale": 1.0, "rotation": 0.0},
                        "crop": None,
                        "opacity": 1.0,
                        "blendMode": "normal",
                        "motion": "none",
                        "origin": {"type": "source", "suggestionId": None, "provenanceId": None},
                        "keyframes": [],
                    }
                ],
            }
        ],
        # Burned-in captions need an FFmpeg built with libass, which this scenario does not
        # assume; the caption path is covered by the compiler's own tests.
        "captions": {"mode": "off", "words": [], "style": _caption_style()},
        "overlays": [],
        "audio": {"gainDb": 0.0, "musicGainDb": -18.0},
        "bookmarks": [],
    }
    if scenario == "smart-crop":
        # A smart-crop suggestion: the window keeps its size and its centre travels, which
        # is the one thing a keyframe on a base timeline item is allowed to say.
        item = document["tracks"][0]["items"][0]
        item["crop"] = {"x": 0.2, "y": 0.0, "width": 0.5, "height": 1.0}
        item["keyframes"] = [
            {
                "atMs": 0,
                "easing": "easeInOut",
                "transform": {"x": 0.3, "y": 0.5, "scale": 1.0, "rotation": 0.0},
                "opacity": None,
                "style": None,
            },
            {
                "atMs": 1_000,
                "easing": "easeInOut",
                "transform": {"x": 0.7, "y": 0.5, "scale": 1.0, "rotation": 0.0},
                "opacity": None,
                "style": None,
            },
        ]
    if scenario == "stock-image":
        document["overlays"] = [
            {
                "id": "still-1",
                "type": "image",
                "assetId": "33333333-3333-4333-8333-333333333333",
                "timelineStartMs": 200,
                "timelineEndMs": 800,
                "placement": "cover",
                "opacity": 1.0,
                "blendMode": "normal",
                "motion": "kenBurnsIn",
                "origin": {
                    "type": "userAsset",
                    "suggestionId": None,
                    "provenanceId": "88888888-8888-4888-8888-888888888888",
                },
                "keyframes": [],
            }
        ]
    if scenario in {"stock-video", "generated-video"}:
        document["overlays"] = [
            {
                "id": "broll-1",
                "type": "video",
                "assetId": "22222222-2222-4222-8222-222222222222",
                "timelineStartMs": 200,
                "timelineEndMs": 800,
                "sourceInMs": 0,
                "sourceOutMs": 600,
                "placement": "cover",
                "opacity": 1.0,
                "blendMode": "normal",
                "motion": "none",
                "preserveDialogueAudio": scenario == "stock-video",
                "origin": {
                    "type": "brollSuggestion" if scenario == "stock-video" else "generated",
                    "suggestionId": (
                        "44444444-4444-4444-8444-444444444444"
                        if scenario == "stock-video"
                        else None
                    ),
                    "provenanceId": "88888888-8888-4888-8888-888888888888",
                },
                "keyframes": [],
            }
        ]
    return document


def _caption_style() -> dict[str, Any]:
    """The caption type every fixture composition carries, drawn or not."""
    return {
        "fontFamily": "Montserrat",
        "fontSize": 64,
        "color": "#FFFFFF",
        "highlightColor": "#FFD166",
        "align": "center",
        "weight": 700,
        "italic": False,
        "decoration": "none",
        "letterSpacing": 0.0,
        "lineHeight": 1.2,
        "backgroundEnabled": False,
        "backgroundColor": "#000000",
    }


def _probe(path: Path) -> dict[str, Any]:
    """Read one file back with ffprobe, the way an operator would check it."""
    output = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    parsed: dict[str, Any] = json.loads(output.stdout)
    return parsed


def _probe_duration_ms(path: Path) -> int:
    """Read one file's duration in milliseconds."""
    return round(float(_probe(path)["format"]["duration"]) * 1000)


def _ffmpeg_process_count() -> int:
    """Count the FFmpeg processes this host is running right now."""
    listing = subprocess.run(["ps", "-Ao", "comm"], check=True, capture_output=True, text=True)
    return sum(1 for line in listing.stdout.splitlines() if line.strip().endswith("ffmpeg"))


def _unused(*values: object) -> None:
    """Keep intentionally staged values from reading as dead code."""
    del values


_unused(datetime, UTC)
