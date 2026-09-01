# Durable Media Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate a private source Asset and durably produce a max-720p proxy, JPEG thumbnail,
and mono 16 kHz transcription-audio Asset through the existing INGEST Job.

**Architecture:** Keep untrusted-media parsing, FFmpeg process control, filesystem/object-store
orchestration, and durable Job persistence behind four focused modules. The orchestrator uses
injected download and process boundaries, deterministic derivative identities, and the existing
Job workspace and worker transaction conventions so tests stay offline and retries converge.

**Tech Stack:** Python 3.13, python-magic, ffprobe/FFmpeg, HTTPX, SQLAlchemy/Postgres, boto3/MinIO,
Celery, pytest.

**Spec:** `docs/superpowers/specs/2026-09-01-media-ingest-design.md`

## Global Constraints

- Accept at most 2 GiB and four hours, exactly one video stream, at least one audio stream, at most
  4K input, H.264/H.265/VP8/VP9/AV1 video, and AAC/Opus/MP3 audio.
- Use argument arrays, explicit timeouts, a separate process group, bounded stderr, and
  `-progress pipe:1`; never use a shell.
- Never persist or expose signed URLs, local paths, commands, raw stderr, stack traces, or provider
  exception text.
- Use only server-owned Workspace/Project/Asset object keys and private five-minute downloads.
- Every new module, class, and function has an intent docstring; all tests have behavioral names
  and rationale docstrings.
- Tests precede production code and each new behavior is observed failing for the expected reason.
- The agent does not commit. The owner commit message is `feat: add durable media ingest pipeline`.

---

### Task 1: Deterministic Media Fixtures

**Files:**
- Create: `backend/tests/fixtures/media/generate.sh`
- Create: `backend/tests/fixtures/media/landscape.mp4`
- Create: `backend/tests/fixtures/media/portrait.mp4`
- Create: `backend/tests/fixtures/media/no-audio.mp4`
- Create: `backend/tests/fixtures/media/audio-only.m4a`
- Create: `backend/tests/fixtures/media/corrupt.bin`
- Create: `backend/tests/fixtures/media/unsupported-codec.mkv`
- Create: `backend/tests/fixtures/media/variable-frame-rate.mp4`
- Create: `backend/tests/fixtures/media/oversized-metadata.json`

**Interfaces:**
- Consumes: FFmpeg/ffprobe available on the development worker image.
- Produces: Small, checked-in, byte-stable fixtures whose names state the validation case.

- [ ] **Step 1: Add a fixture-generation contract test**

Add a test to `backend/tests/unit/test_probe_validation.py` that enumerates the required fixture
names, runs ffprobe on each media fixture, and asserts the intended stream/codec/orientation/VFR
property. The production change that makes it pass is the generated fixture set.

- [ ] **Step 2: Run the contract and observe RED**

Run: `cd backend && uv run pytest -q tests/unit/test_probe_validation.py -k fixture`

Expected: FAIL because the fixture files do not exist.

- [ ] **Step 3: Add the generator and generate fixtures**

Use a shell script containing fixed lavfi inputs, fixed durations, `-map_metadata -1`, explicit
codecs/pixel formats, and `-y`. Use H.264/AAC for the valid inputs, FFV1/PCM for the unsupported
input, two concatenated frame-rate segments for VFR, literal corrupt bytes, and JSON representing
a probe result just beyond each unreasonably expensive real-media boundary.

- [ ] **Step 4: Regenerate twice and prove byte stability**

Run the script twice and compare SHA-256 manifests. If the installed encoder cannot produce stable
container bytes, check in the generated fixtures and have the script verify semantic ffprobe
properties instead of claiming byte-for-byte regeneration.

- [ ] **Step 5: Run the fixture contract and observe GREEN**

Run: `cd backend && uv run pytest -q tests/unit/test_probe_validation.py -k fixture`

Expected: PASS.

---

### Task 2: Typed Probe Parsing and Media Validation

**Files:**
- Create: `backend/src/clipah/assets/probe.py`
- Create/extend: `backend/tests/unit/test_probe_validation.py`

**Interfaces:**
- Produces: `SourceMetadata`, `MediaValidationError.code`, `sniff_mime(path)`,
  `parse_probe(payload)`, and `validate_source(declared_mime, sniffed_mime, metadata)`.

```python
@dataclass(frozen=True, slots=True)
class SourceMetadata:
    duration_ms: int
    width: int
    height: int
    video_codec: str
    audio_codec: str
    video_stream_count: int
    audio_stream_count: int
    variable_frame_rate: bool

class MediaValidationError(Exception):
    code: str

def sniff_mime(path: Path) -> str: ...
def parse_probe(payload: Mapping[str, object]) -> SourceMetadata: ...
def validate_source(*, declared_mime: str, sniffed_mime: str,
                    metadata: SourceMetadata, size_bytes: int) -> None: ...
```

- [ ] **Step 1: Write RED tests for valid parsing**

Cover landscape, portrait, and VFR probe JSON. Assert exact integer milliseconds, normalized codec
names, stream counts, dimensions, and the VFR flag. Run the focused tests and observe import or
assertion failures caused by the missing production API.

- [ ] **Step 2: Implement minimal typed parsing and reach GREEN**

Reject missing, non-finite, negative, boolean-as-number, or malformed values with
`ASSET_INVALID_MEDIA`. Derive VFR by comparing normalized `avg_frame_rate` and `r_frame_rate`
fractions without using floating-point equality.

- [ ] **Step 3: Write RED tests for every Section 7 boundary**

Assert these exact codes:

```python
EXPECTED_CODES = {
    "too_large": "ASSET_TOO_LARGE",
    "mime_mismatch": "ASSET_MIME_MISMATCH",
    "corrupt": "ASSET_INVALID_MEDIA",
    "too_long": "ASSET_TOO_LONG",
    "no_video": "ASSET_VIDEO_STREAM_REQUIRED",
    "multiple_video": "ASSET_VIDEO_STREAM_COUNT_INVALID",
    "no_audio": "ASSET_AUDIO_STREAM_REQUIRED",
    "too_large_resolution": "ASSET_RESOLUTION_UNSUPPORTED",
    "video_codec": "ASSET_INVALID_VIDEO_CODEC",
    "audio_codec": "ASSET_INVALID_AUDIO_CODEC",
}
```

Test exact acceptance at 2 GiB, four hours, and 3840x2160; reject values one unit beyond. Treat
portrait 2160x3840 as valid 4K by bounding the long and short edges, rather than assuming landscape.

- [ ] **Step 4: Implement the fixed validation table and reach GREEN**

Keep limits and codec aliases in `probe.py`; canonicalize `hevc` to H.265 and `avc1` to H.264 only
where ffprobe actually reports those values. MIME comparison accepts the narrow MP4 aliases emitted
by libmagic and stored upload metadata, but never an audio declaration for video bytes.

- [ ] **Step 5: Run the whole probe suite**

Run: `cd backend && uv run pytest -q tests/unit/test_probe_validation.py`

Expected: PASS.

---

### Task 3: Safe ffprobe and FFmpeg Adapter

**Files:**
- Create: `backend/src/clipah/assets/ffmpeg.py`
- Create: `backend/tests/unit/test_ffmpeg.py`

**Interfaces:**
- Consumes: `SourceMetadata` and a cancellation callback.
- Produces: `FFmpegRunner.probe`, `generate_proxy`, `generate_thumbnail`,
  `generate_transcription_audio`, and `parse_progress`.

```python
class FFmpegRunner:
    def probe(self, source: Path, *, cancellation_check: Callable[[], None]) -> SourceMetadata: ...
    def generate_proxy(self, source: Path, output: Path, *, duration_ms: int,
                       cancellation_check: Callable[[], None],
                       progress: Callable[[float], None]) -> None: ...
    def generate_thumbnail(self, source: Path, output: Path, *,
                           cancellation_check: Callable[[], None]) -> None: ...
    def generate_transcription_audio(self, source: Path, output: Path, *, duration_ms: int,
                                     cancellation_check: Callable[[], None],
                                     progress: Callable[[float], None]) -> None: ...
```

- [ ] **Step 1: Write RED command-contract tests**

Inject a recording process factory. Assert argument arrays contain `-nostdin`, explicit input and
stream mapping, max-720p scale without upscaling, H.264/AAC/yuv420p/faststart proxy settings, one
JPEG frame, mono PCM 16 kHz audio, `-progress pipe:1`, and no shell-bearing string.

- [ ] **Step 2: Implement minimal command construction and reach GREEN**

Centralize executable paths and timeouts in constructor parameters so readiness/version checks can
reuse the adapter later without adding Task 12 behavior.

- [ ] **Step 3: Write RED lifecycle and progress tests**

Cover fragmented stdout progress records, malformed progress values, monotonic clamped ratios,
bounded stderr, nonzero exits, timeout, cancellation, graceful group termination, and kill fallback.
Name exact sanitized codes `FFPROBE_FAILED`, `FFMPEG_FAILED`, and `MEDIA_PROCESS_TIMEOUT`.

- [ ] **Step 4: Implement process-group lifecycle and reach GREEN**

Use `subprocess.Popen(..., start_new_session=True)` with concurrent stdout/stderr draining so neither
pipe can deadlock. Keep only the final fixed-size stderr suffix. Poll cancellation while waiting;
terminate with `os.killpg`, wait a bounded grace period, then kill. Convert probe JSON into
`parse_probe` input and never include diagnostics in raised exception text.

- [ ] **Step 5: Run the adapter suite**

Run: `cd backend && uv run pytest -q tests/unit/test_ffmpeg.py`

Expected: PASS without leaked child processes.

---

### Task 4: AssetIngestor Orchestration

**Files:**
- Modify: `backend/src/clipah/assets/keys.py`
- Create: `backend/src/clipah/assets/ingest.py`
- Create: `backend/tests/unit/test_ingest.py`

**Interfaces:**
- Consumes: `ObjectStore.sign_download`, an injected streaming downloader, `FFmpegRunner`, and a
  Job workspace path.
- Produces: `AssetIngestor.ingest(...) -> IngestResult`.

```python
@dataclass(frozen=True, slots=True)
class IngestArtifact:
    asset_id: UUID
    kind: AssetKind
    storage_key: str
    content_type: str
    size_bytes: int
    sha256: bytes
    duration_ms: int | None
    width: int | None
    height: int | None
    video_codec: str | None
    audio_codec: str | None

@dataclass(frozen=True, slots=True)
class IngestResult:
    source: IngestArtifact
    proxy: IngestArtifact
    thumbnail: IngestArtifact
    transcription_audio: IngestArtifact

class AssetIngestor:
    def ingest(self, *, source: IngestArtifact, workspace: Path,
               cancellation_check: Callable[[], None],
               progress: Callable[[str, float], None]) -> IngestResult: ...
```

- [ ] **Step 1: Write RED download and hashing tests**

Use an injected downloader yielding fixed chunks. Assert the exact signed URL is consumed only in
memory, SHA-256 is calculated during streaming, a short declared object is rejected when observed
bytes differ, more than 2 GiB aborts without probe, and cancellation stops between chunks.

- [ ] **Step 2: Implement bounded streaming and reach GREEN**

`HttpxSourceDownloader` follows no redirects and applies explicit connect/read/write/pool timeouts.
It writes only the fixed source path inside the supplied Job workspace. The signed URL is never
stored on the ingestor or included in an exception.

- [ ] **Step 3: Write RED orchestration tests**

Assert stage ordering, MIME comparison before probe, cancellation boundaries, exact progress stage
names, deterministic UUIDv5 derivative IDs, deterministic keys, content types, local hashes and
sizes, upload-after-completion only, and complete `IngestResult` metadata.

- [ ] **Step 4: Add deterministic derivative keys**

Add `derived_asset_key(workspace_id, project_id, source_asset_id, kind)` that accepts only PROXY,
THUMBNAIL, or TRANSCRIPTION_AUDIO and returns
`workspaces/{workspace}/projects/{project}/derived/{source}/{kind}`.

- [ ] **Step 5: Implement orchestration and reach GREEN**

Probe the source, generate one derivative at a time, validate each output exists and is a regular
direct child of the Job workspace, hash it, upload it, and compare returned object key/length with
the local observation. Delete a mismatched uploaded object before raising a terminal integrity
error.

- [ ] **Step 6: Run orchestration tests**

Run: `cd backend && uv run pytest -q tests/unit/test_ingest.py tests/unit/test_storage_keys.py`

Expected: PASS.

---

### Task 5: Durable INGEST Job Runner

**Files:**
- Create: `backend/src/clipah/jobs/ingest_task.py`
- Modify: `backend/src/clipah/jobs/tasks.py`
- Create: `backend/tests/integration/test_ingest_pipeline.py`

**Interfaces:**
- Consumes: one source Asset belonging to `JobContext.project_id`, `AssetIngestor`, and worker
  Workspace-scoped sessions.
- Produces: a registered `JobKind.INGEST` stage runner and three durable derived Asset rows.

- [ ] **Step 1: Write RED persistence and registration tests**

Create a source Asset and INGEST Job through database fixtures. Assert `stage_runners()` contains
the production ingest runner, the runner loads only the source Asset in its Workspace/Project,
persists proxy/thumbnail/transcription-audio metadata in one transaction, and never changes the
source Asset's immutable identity.

- [ ] **Step 2: Implement source resolution and atomic persistence**

Open one short worker transaction to select the source Asset, then close it before external work.
After ingest, open a second transaction, lock all deterministic derived IDs in sorted order, verify
every immutable field on existing rows, add missing rows, and flush once. Map media validation and
process failures to `TerminalJobError(code)` and transport availability to
`RetryableJobError("ASSET_SOURCE_UNAVAILABLE")`.

- [ ] **Step 3: Write RED retry and isolation tests**

Run the same context twice and assert exactly one row per derivative and identical object keys.
Assert a conflicting existing row fails without inserting the remaining set, another Workspace's
source is indistinguishable from missing, cancellation maps through the existing Job runner, and no
transaction stays open while the fake ingestor performs external work.

- [ ] **Step 4: Implement retry verification and register the runner**

Compose production dependencies from Settings, S3ObjectStore, HttpxSourceDownloader, and
FFmpegRunner. Register with `_STAGE_RUNNERS.setdefault(JobKind.INGEST, ingest_stage_runner)` beside
the source-import registration.

- [ ] **Step 5: Run focused integration tests**

Run: `cd backend && uv run pytest -q tests/integration/test_ingest_pipeline.py`

Expected: PASS with Postgres/MinIO available.

---

### Task 6: Real Artifact Proof, Full Gates, and Progress Ledger

**Files:**
- Modify: `backend/PROGRESS.md` only if it exists there; otherwise modify repository-root
  `PROGRESS.md`.
- Modify tests or Task 11 modules only when a gate exposes a Task 11 defect.

**Interfaces:**
- Produces: verified Task 11 completion evidence and an honest owner handoff.

- [ ] **Step 1: Exercise the real pipeline on landscape, portrait, and VFR fixtures**

Run the integration test with real FFmpeg/ffprobe. Inspect all outputs with
`ffprobe -v error -show_streams -show_format -of json`. Assert proxy long edge is at most 1280,
proxy short edge is at most 720, thumbnail is JPEG, and transcription audio is one mono 16 kHz
stream.

- [ ] **Step 2: Run focused Task 11 tests**

Run: `cd backend && uv run pytest -q tests/unit/test_probe_validation.py tests/unit/test_ffmpeg.py tests/unit/test_ingest.py tests/integration/test_ingest_pipeline.py`

Expected: PASS.

- [ ] **Step 3: Run all four repository gates**

```bash
cd backend
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q --cov=clipah --cov-fail-under=90
```

Expected: all commands exit zero and coverage stays at or above 90%.

- [ ] **Step 4: Run supplementary integrity checks**

Run: `git diff --check` and inspect `git status --short`. Confirm no generated workspace files,
signed URLs, stderr captures, caches, or unrelated edits are present.

- [ ] **Step 5: Update PROGRESS.md**

Mark Task 11 complete pending owner commit. Record the actual test count, coverage percentage,
artifact inspection result, gate outcomes, and only deliberate deferrals owned by later numbered
tasks.

- [ ] **Step 6: Hand over without committing**

Report changed behavior, exact gate output, material omissions, and the owner commit message:
`feat: add durable media ingest pipeline`.
