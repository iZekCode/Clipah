# Preview Media Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (the repository owner requires inline execution without subagents). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Derive a storyboard and waveform for every ingested source, and expose storyboard, waveform, transcript, and "recently edited" reads, so the redesigned UI can show real media everywhere.

**Architecture:** A new `PREVIEW_MEDIA` Job runs beside transcription after ingest succeeds. It never touches Project status and is never allowed to block the pipeline. Geometry and peak extraction are pure functions in `assets/preview_media.py`; FFmpeg work sits behind the existing runner; the stage runner follows the ingest runner's short-transaction, deterministic-identity pattern. Reads live with the other studio browsing reads and sign five-minute URLs.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL enums and RLS, Celery, FFmpeg 7.1.5 (`fps`, `scale`, `tile` filters), stdlib `wave` and `array`, pytest.

**Spec:** `redesign-plan-v2.md` → Backend Additions. Plan index: `docs/superpowers/plans/2026-09-17-signal-studio-redesign.md`.

## Global Constraints

- Everything in the plan index's "Global constraints" applies — in particular, **integration tests run only against a disposable PostgreSQL cluster**.
- Start from commit `5f61b7b` or later and re-read each file before editing it.
- Storyboard v1 (exact): one frame every 2,000 ms at `i × 2000 ms`; tiles scaled so the longer side is 160 px (other side rounded to the nearest even number, minimum 2); 10 columns × 10 rows per sheet; JPEG `-q:v 5`; key segment `storyboard-v1/sheet-NNNN.jpg`; Asset kind `storyboard`; deterministic ID `uuid5(source_asset_id, "storyboard-v1:<index>")`.
- Waveform v1 (exact): 20 peaks per second from the mono 16 kHz PCM transcription audio; each byte is the maximum absolute sample of its window scaled to 0–255; key segment `waveform-v1.bin`; Asset kind `waveform`; content type `application/octet-stream`; deterministic ID `uuid5(source_asset_id, "waveform-v1")`.
- `PREVIEW_MEDIA` routes to the `ingest` queue, charges no quota, is admitted after the `TRANSCRIBE` successor in the same transaction, is never in `NEXT_STAGE` or `STAGE_STATUS`, and a concurrency refusal records nothing.
- Every new read returns the same 404 body for a foreign identifier as for a missing one; responses use strict Pydantic models with camelCase aliases and `extra="forbid"`.
- `read_requests_per_minute` default becomes 300.
- Owner commit message for this plan: `feat: derive storyboard and waveform previews`.

## File map

| File | Responsibility |
| --- | --- |
| `backend/migrations/versions/0023_preview_media.py` | Adds `storyboard` to `asset_kind` and `preview_media` to `job_kind` |
| `backend/src/clipah/models.py` | `AssetKind.STORYBOARD`, `JobKind.PREVIEW_MEDIA` |
| `backend/src/clipah/celery_app.py` | Queue for `PREVIEW_MEDIA` |
| `backend/src/clipah/assets/preview_media.py` | Pure storyboard geometry, sheet naming, waveform peaks |
| `backend/src/clipah/assets/keys.py` | `preview_media_key` |
| `backend/src/clipah/assets/ffmpeg.py` | `FFmpegRunner.generate_storyboard` |
| `backend/src/clipah/assets/ingest.py` | `upload_verified_artifact` extracted from `AssetIngestor._upload` |
| `backend/src/clipah/assets/preview_builder.py` | Downloads inputs, renders sheets and peaks, uploads with integrity checks |
| `backend/src/clipah/jobs/preview_media_task.py` | Stage runner: load inputs, reuse on retry, persist Assets |
| `backend/src/clipah/jobs/pipeline.py` | `SIDE_STAGES`, `admit_side_stages` |
| `backend/src/clipah/jobs/tasks.py` | Admit and dispatch side stages; register the runner |
| `backend/src/clipah/studio/use_cases.py` | `project_storyboard`, `project_waveform`, `project_transcript`, `ClipOrder` |
| `backend/src/clipah/api/routes/studio.py` | Three new reads and the `order` parameter |
| `backend/src/clipah/studio/backfill_preview_media.py` | Backfill use case and CLI |
| `backend/src/clipah/config.py`, `ENVIRONMENT_SETUP.md` | Read limit 300 |
| `contracts/openapi.json`, `frontend/lib/api/generated/**` | Regenerated |
| `frontend/features/uploads/UploadPanel.tsx`, `frontend/features/jobs/job-center.tsx`, `frontend/features/projects/project-detail.tsx` | Pipeline followers ignore preview work; label it |

---

### Task 1: Enum values, migration `0023`, and the queue

**Files:**
- Create: `backend/migrations/versions/0023_preview_media.py`
- Modify: `backend/src/clipah/models.py` (`AssetKind`, `JobKind`)
- Modify: `backend/src/clipah/celery_app.py`
- Modify: `backend/tests/integration/test_schema.py` (`EXPECTED_ENUMS`)
- Modify: `backend/tests/integration/test_jobs.py` (queue assertions)

**Interfaces:**
- Produces: `AssetKind.STORYBOARD = "storyboard"`, `JobKind.PREVIEW_MEDIA = "preview_media"`, `QUEUE_FOR_JOB_KIND[JobKind.PREVIEW_MEDIA] == "ingest"`.

- [ ] **Step 1: Point the test settings at a disposable database**

Confirm `CLIPAH_TEST_DATABASE_URL`, `CLIPAH_TEST_API_RUNTIME_DATABASE_URL`, and `CLIPAH_TEST_WORKER_RUNTIME_DATABASE_URL` name a disposable PostgreSQL cluster (for example on port 55434, provisioned with `infra/postgres/init-runtime.sql`) and `CLIPAH_TEST_REDIS_URL` uses database 15. Run `echo $CLIPAH_TEST_DATABASE_URL` and stop if it names `55433/clipah_rebuild_foundation`.

- [ ] **Step 2: Write the failing expectations**

In `backend/tests/integration/test_schema.py`, append `"storyboard"` as the last value of `EXPECTED_ENUMS["asset_kind"]` and `"preview_media"` as the last value of `EXPECTED_ENUMS["job_kind"]`.

In `backend/tests/integration/test_jobs.py`, beside the existing queue assertions, add:

```python
    assert QUEUE_FOR_JOB_KIND[JobKind.PREVIEW_MEDIA] == "ingest"
```

- [ ] **Step 3: Run them and watch them fail**

Run from `backend/`:

```bash
uv run pytest -q tests/integration/test_schema.py::test_initial_migration_owns_exact_foundational_enum_scope tests/integration/test_jobs.py -k "enum_scope or queue"
```

Expected: FAIL — `AttributeError: PREVIEW_MEDIA` and the enum tuples differ.

- [ ] **Step 4: Add the enum members**

In `backend/src/clipah/models.py`:

```python
class AssetKind(StrEnum):
    SOURCE = "source"
    PROXY = "proxy"
    THUMBNAIL = "thumbnail"
    WAVEFORM = "waveform"
    TRANSCRIPTION_AUDIO = "transcription_audio"
    RENDER = "render"
    # Retrieved or generated footage a suggestion may place over the dialogue, and the
    # normalized rendition the editor plays instead of the original.
    BROLL = "broll"
    BROLL_PROXY = "broll_proxy"
    # One sheet of fixed-interval frames drawn from the proxy, for posters and filmstrips.
    STORYBOARD = "storyboard"
```

and add `PREVIEW_MEDIA = "preview_media"` as the last member of `JobKind`.

In `backend/src/clipah/celery_app.py` add `JobKind.PREVIEW_MEDIA: "ingest",` after `JobKind.INGEST: "ingest",`.

- [ ] **Step 5: Write the migration**

`backend/migrations/versions/0023_preview_media.py`:

```python
"""Let ingest's previews be recorded: storyboard sheets and the job that draws them.

Revision ID: 0023
Revises: 0022
"""

from __future__ import annotations

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the storyboard Asset kind and the preview-media Job kind.

    The worker already inserts Assets and Jobs and the API already reads both, so no grant
    changes: a storyboard sheet is one more derived Asset, and preview work is one more Job.
    """
    op.execute("ALTER TYPE asset_kind ADD VALUE IF NOT EXISTS 'storyboard'")
    op.execute("ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'preview_media'")


def downgrade() -> None:
    """Leave both values in place.

    PostgreSQL cannot remove a value from an enumeration. Both are additive and unused once
    the code that writes them is gone.
    """
```

- [ ] **Step 6: Run the tests, then prove the migration round-trips**

Run: `uv run pytest -q tests/integration/test_schema.py tests/integration/test_jobs.py` → PASS.
Run against the disposable cluster: `uv run alembic downgrade 0022 && uv run alembic upgrade head` → both succeed.

---

### Task 2: Storyboard geometry, sheet names, keys, and waveform peaks

**Files:**
- Create: `backend/src/clipah/assets/preview_media.py`
- Modify: `backend/src/clipah/assets/keys.py`
- Create: `backend/tests/unit/test_preview_media.py`
- Modify: `backend/tests/unit/test_storage_keys.py`

**Interfaces:**
- Produces:
  - `StoryboardPolicy(version: int, interval_ms: int, columns: int, rows: int, long_side_px: int)` with `frames_per_sheet: int`.
  - `STORYBOARD_V1 = StoryboardPolicy(1, 2000, 10, 10, 160)`.
  - `StoryboardLayout(policy, duration_ms, tile_width, tile_height, frame_count, sheet_count)` with `sheet_start_ms(index) -> int`, `sheet_tile_count(index) -> int`, `sheet_duration_ms(index) -> int`.
  - `storyboard_layout(*, duration_ms: int, width: int, height: int, policy: StoryboardPolicy = STORYBOARD_V1) -> StoryboardLayout`.
  - `tile_size(*, width: int, height: int, long_side: int) -> tuple[int, int]`.
  - `storyboard_sheet_name(index: int) -> str`, `storyboard_sheet_index(storage_key: str) -> int | None`.
  - `WAVEFORM_V1_NAME = "waveform-v1.bin"`, `WAVEFORM_PEAKS_PER_SECOND = 20`.
  - `waveform_peaks(source: BinaryIO, *, peaks_per_second: int = WAVEFORM_PEAKS_PER_SECOND) -> bytes`; raises `WaveformInputError`.
  - `preview_media_key(*, workspace_id: UUID, project_id: UUID, source_asset_id: UUID, name: str) -> str`.

- [ ] **Step 1: Write the failing unit tests**

`backend/tests/unit/test_preview_media.py`:

```python
"""Unit contracts for storyboard geometry and waveform peaks.

A poster, a filmstrip, and a timeline waveform are only honest if every tile and every peak
maps to a known moment of the source. These tests pin that mapping.
"""

from __future__ import annotations

import io
import struct
import wave

import pytest

from clipah.assets.preview_media import (
    STORYBOARD_V1,
    WaveformInputError,
    storyboard_layout,
    storyboard_sheet_index,
    storyboard_sheet_name,
    tile_size,
    waveform_peaks,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (1280, 720, (160, 90)),
        (720, 1280, (90, 160)),
        (1080, 1080, (160, 160)),
        (640, 480, (160, 120)),
        (1920, 20, (160, 2)),
    ],
)
def test_a_tile_keeps_the_proxy_shape_with_its_long_side_at_160_pixels(
    width: int, height: int, expected: tuple[int, int]
) -> None:
    """Tiles cropped to the wrong shape would stretch every poster drawn from them."""
    assert tile_size(width=width, height=height, long_side=160) == expected


@pytest.mark.unit
def test_a_layout_counts_one_frame_every_two_seconds_and_one_sheet_per_hundred_frames() -> None:
    """A twelve-minute source needs 362 frames across four sheets, the last one partial."""
    layout = storyboard_layout(duration_ms=722_588, width=1280, height=720)

    assert layout.frame_count == 362
    assert layout.sheet_count == 4
    assert (layout.tile_width, layout.tile_height) == (160, 90)
    assert [layout.sheet_start_ms(index) for index in range(4)] == [0, 200_000, 400_000, 600_000]
    assert [layout.sheet_tile_count(index) for index in range(4)] == [100, 100, 100, 62]
    assert layout.sheet_duration_ms(3) == 122_588


@pytest.mark.unit
def test_a_source_exactly_one_sheet_long_needs_exactly_one_sheet() -> None:
    """The frame at the very end of a source does not exist, so it must not open a sheet."""
    layout = storyboard_layout(duration_ms=200_000, width=1280, height=720)

    assert layout.frame_count == 100
    assert layout.sheet_count == 1


@pytest.mark.unit
def test_a_layout_refuses_an_empty_source() -> None:
    """Zero duration or size is a broken proxy, not a zero-tile storyboard."""
    with pytest.raises(ValueError):
        storyboard_layout(duration_ms=0, width=1280, height=720)


@pytest.mark.unit
def test_sheet_names_round_trip_through_storage_keys() -> None:
    """The read endpoint recovers each sheet's position from its key alone."""
    key = f"workspaces/w/projects/p/derived/s/{storyboard_sheet_name(7)}"

    assert storyboard_sheet_name(7) == "storyboard-v1/sheet-0007.jpg"
    assert storyboard_sheet_index(key) == 7
    assert storyboard_sheet_index("workspaces/w/projects/p/derived/s/thumbnail") is None
    with pytest.raises(ValueError):
        storyboard_sheet_name(10_000)


def _wav(samples: list[int], *, rate: int = 16_000, channels: int = 1, width: int = 2) -> io.BytesIO:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        writer.writeframes(struct.pack(f"<{len(samples)}h", *samples) if width == 2 else bytes(len(samples)))
    buffer.seek(0)
    return buffer


@pytest.mark.unit
def test_peaks_are_one_byte_per_fifty_milliseconds_scaled_to_the_loudest_sample() -> None:
    """Silence, full scale, and a partial final window each map to a predictable byte."""
    silence = [0] * 800
    loud = [0] * 799 + [-32_768]
    half = [16_384] * 400

    peaks = waveform_peaks(_wav(silence + loud + half))

    assert peaks == bytes([0, 255, 128])


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rate", "channels", "width"),
    [(16_000, 2, 2), (16_000, 1, 1), (44_100, 1, 2)],
)
def test_peaks_refuse_audio_that_is_not_the_mono_16_bit_ingest_output(
    rate: int, channels: int, width: int
) -> None:
    """Silently accepting another layout would draw a waveform at the wrong speed."""
    with pytest.raises(WaveformInputError):
        waveform_peaks(_wav([0] * 800, rate=rate, channels=channels, width=width))


@pytest.mark.unit
def test_peaks_refuse_bytes_that_are_not_a_wav_file() -> None:
    """A corrupted artifact is a terminal input problem, not an empty waveform."""
    with pytest.raises(WaveformInputError):
        waveform_peaks(io.BytesIO(b"not audio"))


@pytest.mark.unit
def test_the_version_one_policy_is_the_one_the_spec_names() -> None:
    """Changing geometry must be a deliberate new version, never an edit to this one."""
    assert (STORYBOARD_V1.interval_ms, STORYBOARD_V1.columns, STORYBOARD_V1.rows) == (2_000, 10, 10)
    assert STORYBOARD_V1.long_side_px == 160
    assert STORYBOARD_V1.frames_per_sheet == 100
```

Add to `backend/tests/unit/test_storage_keys.py`:

```python
@pytest.mark.unit
def test_preview_media_keys_live_beside_ingest_derivatives_and_accept_only_known_names() -> None:
    """A preview key must stay inside its Project prefix so retention purges it with the rest."""
    workspace_id, project_id, source_id = uuid4(), uuid4(), uuid4()

    sheet = preview_media_key(
        workspace_id=workspace_id,
        project_id=project_id,
        source_asset_id=source_id,
        name="storyboard-v1/sheet-0000.jpg",
    )
    peaks = preview_media_key(
        workspace_id=workspace_id, project_id=project_id, source_asset_id=source_id, name="waveform-v1.bin"
    )

    prefix = f"workspaces/{workspace_id}/projects/{project_id}/derived/{source_id}/"
    assert sheet == f"{prefix}storyboard-v1/sheet-0000.jpg"
    assert peaks == f"{prefix}waveform-v1.bin"
    for name in ("../escape.jpg", "storyboard-v1/sheet-1.jpg", "waveform-v2.bin", ""):
        with pytest.raises(ValueError):
            preview_media_key(
                workspace_id=workspace_id, project_id=project_id, source_asset_id=source_id, name=name
            )
```

(import `preview_media_key` beside the existing key imports; import `uuid4` if the file does not already.)

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest -q tests/unit/test_preview_media.py tests/unit/test_storage_keys.py`
Expected: FAIL — `ModuleNotFoundError: clipah.assets.preview_media` and `ImportError: preview_media_key`.

- [ ] **Step 3: Implement `backend/src/clipah/assets/preview_media.py`**

```python
"""Previews a creator sees before pressing play: storyboard sheets and waveform peaks.

A storyboard is a grid of small frames taken from the proxy at a fixed interval, so any
moment of a source can be drawn from one cached image by offset. Waveform peaks are one byte
per fixed window of the transcription audio. Both are versioned: the geometry lives here, is
named in every storage key, and changes only by adding a new version.
"""

from __future__ import annotations

import re
import sys
import wave
from array import array
from dataclasses import dataclass
from typing import BinaryIO

WAVEFORM_V1_NAME = "waveform-v1.bin"
WAVEFORM_PEAKS_PER_SECOND = 20
_SAMPLE_WIDTH_BYTES = 2
_FULL_SCALE = 32_767
_SHEET_KEY = re.compile(r"storyboard-v1/sheet-(\d{4})\.jpg$")


class WaveformInputError(ValueError):
    """The transcription audio is not the mono 16-bit PCM WAV ingest produces."""


@dataclass(frozen=True, slots=True)
class StoryboardPolicy:
    """One immutable storyboard geometry."""

    version: int
    interval_ms: int
    columns: int
    rows: int
    long_side_px: int

    @property
    def frames_per_sheet(self) -> int:
        """How many tiles one sheet holds."""
        return self.columns * self.rows


STORYBOARD_V1 = StoryboardPolicy(
    version=1, interval_ms=2_000, columns=10, rows=10, long_side_px=160
)


@dataclass(frozen=True, slots=True)
class StoryboardLayout:
    """Where every frame of one source lands across its storyboard sheets."""

    policy: StoryboardPolicy
    duration_ms: int
    tile_width: int
    tile_height: int
    frame_count: int
    sheet_count: int

    def sheet_start_ms(self, index: int) -> int:
        """The source time of the first tile on one sheet."""
        return index * self.policy.frames_per_sheet * self.policy.interval_ms

    def sheet_tile_count(self, index: int) -> int:
        """How many tiles on one sheet hold a real frame."""
        remaining = self.frame_count - index * self.policy.frames_per_sheet
        return max(0, min(self.policy.frames_per_sheet, remaining))

    def sheet_duration_ms(self, index: int) -> int:
        """How much source time one sheet covers."""
        span = self.policy.frames_per_sheet * self.policy.interval_ms
        return max(0, min(span, self.duration_ms - self.sheet_start_ms(index)))


def storyboard_layout(
    *, duration_ms: int, width: int, height: int, policy: StoryboardPolicy = STORYBOARD_V1
) -> StoryboardLayout:
    """Lay out one source's storyboard from its proxy duration and frame size."""
    if duration_ms <= 0 or width <= 0 or height <= 0:
        raise ValueError("a storyboard needs a positive duration and frame size")
    tile_width, tile_height = tile_size(width=width, height=height, long_side=policy.long_side_px)
    frame_count = -(-duration_ms // policy.interval_ms)
    sheet_count = -(-frame_count // policy.frames_per_sheet)
    return StoryboardLayout(
        policy=policy,
        duration_ms=duration_ms,
        tile_width=tile_width,
        tile_height=tile_height,
        frame_count=frame_count,
        sheet_count=sheet_count,
    )


def tile_size(*, width: int, height: int, long_side: int) -> tuple[int, int]:
    """Scale a frame so its longer side is `long_side`, keeping both sides even."""
    if width >= height:
        return long_side, _even(height * long_side, width)
    return _even(width * long_side, height), long_side


def storyboard_sheet_name(index: int) -> str:
    """The version-one storage name of one sheet."""
    if not 0 <= index <= 9_999:
        raise ValueError("a storyboard sheet index must fit four digits")
    return f"storyboard-v1/sheet-{index:04d}.jpg"


def storyboard_sheet_index(storage_key: str) -> int | None:
    """Recover a sheet's index from its storage key, or nothing for any other object."""
    match = _SHEET_KEY.search(storage_key)
    return None if match is None else int(match.group(1))


def waveform_peaks(
    source: BinaryIO, *, peaks_per_second: int = WAVEFORM_PEAKS_PER_SECOND
) -> bytes:
    """Read mono 16-bit PCM and return one loudness byte per window, in order."""
    try:
        reader = wave.open(source, "rb")
    except (wave.Error, EOFError) as error:
        raise WaveformInputError("the audio is not a readable WAV file") from error
    with reader:
        if reader.getnchannels() != 1 or reader.getsampwidth() != _SAMPLE_WIDTH_BYTES:
            raise WaveformInputError("expected mono 16-bit PCM audio")
        rate = reader.getframerate()
        if rate <= 0 or rate % peaks_per_second != 0:
            raise WaveformInputError("the sample rate does not divide into whole windows")
        window = rate // peaks_per_second
        peaks = bytearray()
        while frames := reader.readframes(window):
            samples = array("h")
            samples.frombytes(frames[: len(frames) - len(frames) % _SAMPLE_WIDTH_BYTES])
            if sys.byteorder == "big":
                samples.byteswap()
            loudest = max(max(samples, default=0), -min(samples, default=0))
            peaks.append(min(255, (loudest * 255 + _FULL_SCALE // 2) // _FULL_SCALE))
        return bytes(peaks)


def _even(numerator: int, denominator: int) -> int:
    """Round `numerator / denominator` to the nearest even integer, never below two."""
    return max(2, 2 * ((numerator + denominator) // (2 * denominator)))
```

Check the peak arithmetic against the test: `16_384 → (16_384 × 255 + 16_383) // 32_767 = 128`; `32_768 → 255`; `0 → 0`.

- [ ] **Step 4: Add `preview_media_key` to `backend/src/clipah/assets/keys.py`**

```python
import re

_PREVIEW_MEDIA_NAME = re.compile(r"(?:storyboard-v1/sheet-\d{4}\.jpg|waveform-v1\.bin)")


def preview_media_key(
    *, workspace_id: UUID, project_id: UUID, source_asset_id: UUID, name: str
) -> str:
    """Return the deterministic private key of one versioned preview beside its source's derivatives."""
    if not all(isinstance(value, UUID) for value in (workspace_id, project_id, source_asset_id)):
        raise TypeError("storage identifiers must be UUID values")
    if _PREVIEW_MEDIA_NAME.fullmatch(name) is None:
        raise ValueError("unsupported preview media name")
    return f"workspaces/{workspace_id}/projects/{project_id}/derived/{source_asset_id}/{name}"
```

(`import re` joins the module's imports at the top.)

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -q tests/unit/test_preview_media.py tests/unit/test_storage_keys.py` → PASS.
Run: `uv run mypy src/clipah/assets/preview_media.py src/clipah/assets/keys.py` → no errors.

---

### Task 3: The storyboard FFmpeg command

**Files:**
- Modify: `backend/src/clipah/assets/ffmpeg.py`
- Modify: `backend/tests/unit/test_ffmpeg.py`

**Interfaces:**
- Produces: `FFmpegRunner.generate_storyboard(source: Path, workspace: Path, *, tile_width: int, tile_height: int, interval_ms: int, columns: int, rows: int, cancellation_check: CancellationCheck) -> None`, writing `workspace/sheet-0000.jpg`, `sheet-0001.jpg`, ….

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_ffmpeg.py`:

```python
@pytest.mark.unit
def test_runner_tiles_fixed_interval_frames_into_numbered_jpeg_sheets(tmp_path: Path) -> None:
    """One shell-free command must produce every sheet the storyboard layout expects."""
    executor = RecordingExecutor()
    runner = FFmpegRunner(executor=executor, ffmpeg_timeout_seconds=12.0)

    runner.generate_storyboard(
        tmp_path / "proxy.mp4",
        tmp_path,
        tile_width=160,
        tile_height=90,
        interval_ms=2_000,
        columns=10,
        rows=10,
        cancellation_check=lambda: None,
    )

    arguments, timeout, progress_duration = executor.calls[-1]
    assert arguments == (
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(tmp_path / "proxy.mp4"),
        "-map",
        "0:v:0",
        "-vf",
        "fps=1000/2000,scale=160:90,setsar=1,tile=10x10",
        "-q:v",
        "5",
        "-start_number",
        "0",
        "-map_metadata",
        "-1",
        "-y",
        str(tmp_path / "sheet-%04d.jpg"),
    )
    assert timeout == 12.0
    assert progress_duration is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest -q tests/unit/test_ffmpeg.py -k storyboard`
Expected: FAIL — `AttributeError: 'FFmpegRunner' object has no attribute 'generate_storyboard'`.

- [ ] **Step 3: Implement the command**

Add to `FFmpegRunner` after `generate_thumbnail`:

```python
    def generate_storyboard(
        self,
        source: Path,
        workspace: Path,
        *,
        tile_width: int,
        tile_height: int,
        interval_ms: int,
        columns: int,
        rows: int,
        cancellation_check: CancellationCheck,
    ) -> None:
        """Tile frames taken at a fixed interval into numbered JPEG sheets in the workspace."""
        if min(tile_width, tile_height, interval_ms, columns, rows) <= 0:
            raise ValueError("storyboard geometry must be positive")
        filters = (
            f"fps=1000/{interval_ms},scale={tile_width}:{tile_height},setsar=1,"
            f"tile={columns}x{rows}"
        )
        arguments = (
            self._ffmpeg_path,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-vf",
            filters,
            "-q:v",
            "5",
            "-start_number",
            "0",
            "-map_metadata",
            "-1",
            "-y",
            str(workspace / "sheet-%04d.jpg"),
        )
        self._executor.run(
            arguments,
            timeout_seconds=self._ffmpeg_timeout_seconds,
            cancellation_check=cancellation_check,
        )
```

- [ ] **Step 4: Run it**

Run: `uv run pytest -q tests/unit/test_ffmpeg.py` → PASS.

- [ ] **Step 5: Prove the filter chain against real FFmpeg once**

Inside the pinned media image (the tool version test requires 7.1.5), run:

```bash
docker compose -f infra/compose.yaml run --rm --no-deps worker-ingest-ai sh -c '
  cd /tmp && ffmpeg -v error -f lavfi -i testsrc=size=1280x720:rate=30:duration=205 -c:v libx264 -pix_fmt yuv420p proxy.mp4 &&
  ffmpeg -nostdin -v error -i proxy.mp4 -map 0:v:0 -vf "fps=1000/2000,scale=160:90,setsar=1,tile=10x10" -q:v 5 -start_number 0 -map_metadata -1 -y sheet-%04d.jpg &&
  ls -l sheet-*.jpg && ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 sheet-0000.jpg'
```

Expected: exactly `sheet-0000.jpg` and `sheet-0001.jpg` (103 frames: one full sheet and a partial one), each `1600,900`, each under 300 KB. Record the sizes in the task notes. If the partial last sheet is missing, stop and report it — the builder in Task 4 must then treat a missing final partial sheet as acceptable, and that decision needs the owner.

---

### Task 4: Extract verified upload and build previews

**Files:**
- Modify: `backend/src/clipah/assets/ingest.py` (extract `upload_verified_artifact`)
- Create: `backend/src/clipah/assets/preview_builder.py`
- Create: `backend/tests/unit/test_preview_builder.py`

**Interfaces:**
- Produces in `ingest.py`: `upload_verified_artifact(store: ObjectStore, *, path: Path, workspace: Path, key: str, content_type: str) -> tuple[int, bytes]` (size, SHA-256); raises `IngestIntegrityError`. `AssetIngestor._upload` calls it.
- Produces in `preview_builder.py`:
  - `PreviewInputs(source_asset_id: UUID, workspace_id: UUID, project_id: UUID, proxy_key: str, proxy_size: int, proxy_sha256: bytes, proxy_width: int, proxy_height: int, duration_ms: int, audio_key: str, audio_size: int, audio_sha256: bytes)`.
  - `PreviewMediaResult(sheets: tuple[IngestArtifact, ...], waveform: IngestArtifact)`.
  - `PreviewMediaMaker` protocol: `build(*, inputs: PreviewInputs, workspace: Path, cancellation_check: CancellationCheck) -> PreviewMediaResult`.
  - `StoryboardRenderer` protocol: the `generate_storyboard` signature from Task 3.
  - `PreviewMediaBuilder(store: ObjectStore, downloader: SourceDownloader, media: StoryboardRenderer)` implementing `PreviewMediaMaker`.
  - `storyboard_asset_id(source_asset_id: UUID, index: int) -> UUID`, `waveform_asset_id(source_asset_id: UUID) -> UUID`.

- [ ] **Step 1: Write the failing builder test**

`backend/tests/unit/test_preview_builder.py`:

```python
"""Unit contracts for turning a proxy and its audio into uploaded preview artifacts."""

from __future__ import annotations

import hashlib
import io
import struct
import wave
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

import pytest

from clipah.assets.ingest import DownloadedSource, IngestIntegrityError
from clipah.assets.preview_builder import (
    PreviewInputs,
    PreviewMediaBuilder,
    storyboard_asset_id,
    waveform_asset_id,
)
from clipah.assets.storage import FakeObjectStore
from clipah.models import AssetKind, AssetSourceType

NOW = datetime(2026, 9, 17, tzinfo=UTC)


def _wav_bytes(seconds: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(struct.pack("<h", 1_000) * 16_000 * seconds)
    return buffer.getvalue()


class BodyDownloader:
    """Serve fixed bodies by signed URL, the way a private download would."""

    def __init__(self, bodies: dict[str, bytes]) -> None:
        self.bodies = bodies

    def download(
        self,
        url: str,
        destination: BinaryIO,
        *,
        expected_size: int,
        max_bytes: int,
        cancellation_check: Callable[[], None],
    ) -> DownloadedSource:
        cancellation_check()
        key = url.removeprefix("fake://download/")
        body = self.bodies[key]
        destination.write(body)
        return DownloadedSource(size_bytes=len(body), sha256=hashlib.sha256(body).digest())


class SheetRenderer:
    """Write the number of sheets real FFmpeg would for the requested geometry."""

    def __init__(self, sheets: int) -> None:
        self.sheets = sheets
        self.calls: list[tuple[int, int, int]] = []

    def generate_storyboard(
        self,
        source: Path,
        workspace: Path,
        *,
        tile_width: int,
        tile_height: int,
        interval_ms: int,
        columns: int,
        rows: int,
        cancellation_check: Callable[[], None],
    ) -> None:
        cancellation_check()
        assert source.is_file()
        self.calls.append((tile_width, tile_height, interval_ms))
        for index in range(self.sheets):
            (workspace / f"sheet-{index:04d}.jpg").write_bytes(f"jpeg-{index}".encode())


def _inputs(store: FakeObjectStore, bodies: dict[str, bytes], *, duration_ms: int) -> PreviewInputs:
    workspace_id, project_id, source_id = uuid4(), uuid4(), uuid4()
    prefix = f"workspaces/{workspace_id}/projects/{project_id}/derived/{source_id}"
    proxy, audio = b"proxy-bytes", _wav_bytes(3)
    bodies[f"{prefix}/proxy"] = proxy
    bodies[f"{prefix}/transcription_audio"] = audio
    # The fake store only signs objects it holds, exactly like a real bucket.
    store.put_file(key=f"{prefix}/proxy", content_type="video/mp4", file=io.BytesIO(proxy))
    store.put_file(key=f"{prefix}/transcription_audio", content_type="audio/wav", file=io.BytesIO(audio))
    return PreviewInputs(
        source_asset_id=source_id,
        workspace_id=workspace_id,
        project_id=project_id,
        proxy_key=f"{prefix}/proxy",
        proxy_size=len(proxy),
        proxy_sha256=hashlib.sha256(proxy).digest(),
        proxy_width=1280,
        proxy_height=720,
        duration_ms=duration_ms,
        audio_key=f"{prefix}/transcription_audio",
        audio_size=len(audio),
        audio_sha256=hashlib.sha256(audio).digest(),
    )


@pytest.mark.unit
def test_the_builder_uploads_every_expected_sheet_and_one_waveform(tmp_path: Path) -> None:
    """Each artifact carries its deterministic identity, key, geometry, and verified digest."""
    store = FakeObjectStore(now=lambda: NOW)
    bodies: dict[str, bytes] = {}
    inputs = _inputs(store, bodies, duration_ms=205_000)
    renderer = SheetRenderer(sheets=2)
    builder = PreviewMediaBuilder(store=store, downloader=BodyDownloader(bodies), media=renderer)

    result = builder.build(inputs=inputs, workspace=tmp_path, cancellation_check=lambda: None)

    assert renderer.calls == [(160, 90, 2_000)]
    assert [sheet.asset_id for sheet in result.sheets] == [
        storyboard_asset_id(inputs.source_asset_id, 0),
        storyboard_asset_id(inputs.source_asset_id, 1),
    ]
    first, last = result.sheets
    assert first.kind is AssetKind.STORYBOARD
    assert first.source_type is AssetSourceType.DERIVED
    assert first.storage_key.endswith("/storyboard-v1/sheet-0000.jpg")
    assert (first.content_type, first.width, first.height) == ("image/jpeg", 1600, 900)
    assert (first.duration_ms, last.duration_ms) == (200_000, 5_000)
    assert result.waveform.asset_id == waveform_asset_id(inputs.source_asset_id)
    assert result.waveform.storage_key.endswith("/waveform-v1.bin")
    assert result.waveform.content_type == "application/octet-stream"
    assert result.waveform.duration_ms == 205_000
    assert store.object_bodies[result.waveform.storage_key] == bytes([8]) * 60
    assert hashlib.sha256(b"jpeg-1").digest() == last.sha256


@pytest.mark.unit
def test_extra_sheets_are_ignored_and_a_short_render_keeps_what_exists(tmp_path: Path) -> None:
    """FFmpeg's frame rounding may add or drop a final partial sheet; neither is fatal."""
    store = FakeObjectStore(now=lambda: NOW)
    bodies: dict[str, bytes] = {}
    inputs = _inputs(store, bodies, duration_ms=205_000)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()

    extra = PreviewMediaBuilder(store=store, downloader=BodyDownloader(bodies), media=SheetRenderer(3))
    assert len(extra.build(inputs=inputs, workspace=tmp_path / "a", cancellation_check=lambda: None).sheets) == 2

    short = PreviewMediaBuilder(store=store, downloader=BodyDownloader(bodies), media=SheetRenderer(1))
    assert len(short.build(inputs=inputs, workspace=tmp_path / "b", cancellation_check=lambda: None).sheets) == 1


@pytest.mark.unit
def test_no_sheet_at_all_is_an_integrity_failure(tmp_path: Path) -> None:
    """A proxy FFmpeg could not tile must not become a storyboard with nothing in it."""
    store = FakeObjectStore(now=lambda: NOW)
    bodies: dict[str, bytes] = {}
    inputs = _inputs(store, bodies, duration_ms=205_000)
    builder = PreviewMediaBuilder(store=store, downloader=BodyDownloader(bodies), media=SheetRenderer(0))

    with pytest.raises(IngestIntegrityError):
        builder.build(inputs=inputs, workspace=tmp_path, cancellation_check=lambda: None)


@pytest.mark.unit
def test_an_input_whose_bytes_changed_is_refused_before_any_render(tmp_path: Path) -> None:
    """Previews drawn from a different proxy than the one recorded would lie about the source."""
    store = FakeObjectStore(now=lambda: NOW)
    bodies: dict[str, bytes] = {}
    inputs = _inputs(store, bodies, duration_ms=205_000)
    bodies[inputs.proxy_key] = b"tampered"
    renderer = SheetRenderer(2)
    builder = PreviewMediaBuilder(store=store, downloader=BodyDownloader(bodies), media=renderer)

    with pytest.raises(IngestIntegrityError):
        builder.build(inputs=inputs, workspace=tmp_path, cancellation_check=lambda: None)
    assert renderer.calls == []
```

The builder never creates its workspace (the runner always passes an existing `job_workspace`), which is why the test makes `a` and `b` first. Check the waveform expectation: 3 s of constant 1,000 → 60 windows of `(1000 × 255 + 16383) // 32767 = 8`.

`FakeObjectStore.sign_download` returns `fake://download/<key>` and refuses keys it does not hold, which is why `_inputs` stores both inputs first.

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest -q tests/unit/test_preview_builder.py`
Expected: FAIL — `ModuleNotFoundError: clipah.assets.preview_builder`.

- [ ] **Step 3: Extract `upload_verified_artifact` in `ingest.py`**

Add as a module-level function below `AssetIngestor`:

```python
def upload_verified_artifact(
    store: ObjectStore, *, path: Path, workspace: Path, key: str, content_type: str
) -> tuple[int, bytes]:
    """Upload one complete direct-child artifact and prove the stored object is those bytes."""
    _require_regular_workspace_file(path, workspace)
    size_bytes, digest = _hash_file(path)
    with path.open("rb") as artifact_file:
        stored = store.put_file(key=key, content_type=content_type, file=artifact_file, sha256=digest)
    observed = store.head_object(key=key)
    if any(
        candidate.key != key
        or candidate.content_length != size_bytes
        or candidate.sha256 != digest
        for candidate in (stored, observed)
    ):
        store.delete_object(key=key)
        raise IngestIntegrityError("derived Asset upload metadata mismatch")
    return size_bytes, digest
```

and reduce `AssetIngestor._upload` to call it:

```python
        kind, path, content_type, duration, width, height, video_codec, audio_codec = specification
        key = derived_asset_key(
            workspace_id=source.workspace_id,
            project_id=source.project_id,
            source_asset_id=source.asset_id,
            kind=kind,
        )
        size_bytes, digest = upload_verified_artifact(
            self._store, path=path, workspace=workspace, key=key, content_type=content_type
        )
        return IngestArtifact(
            asset_id=uuid5(source.asset_id, kind.value),
            kind=kind,
            source_type=AssetSourceType.DERIVED,
            storage_key=key,
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=digest,
            duration_ms=duration,
            width=width,
            height=height,
            video_codec=video_codec,
            audio_codec=audio_codec,
        )
```

Run: `uv run pytest -q tests/unit/test_ingest.py tests/integration/test_ingest_pipeline.py` → PASS (the refactor changes no behaviour).

- [ ] **Step 4: Implement `backend/src/clipah/assets/preview_builder.py`**

```python
"""Turn one source's proxy and transcription audio into uploaded preview artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid5

from clipah.assets.ffmpeg import CancellationCheck
from clipah.assets.ingest import (
    SIGNED_DOWNLOAD_TTL,
    IngestArtifact,
    IngestIntegrityError,
    SourceDownloader,
    upload_verified_artifact,
)
from clipah.assets.keys import preview_media_key
from clipah.assets.preview_media import (
    WAVEFORM_V1_NAME,
    storyboard_layout,
    storyboard_sheet_name,
    waveform_peaks,
)
from clipah.assets.probe import MAX_MEDIA_BYTES
from clipah.assets.storage import ObjectStore
from clipah.models import AssetKind, AssetSourceType


@dataclass(frozen=True, slots=True)
class PreviewInputs:
    """The recorded identity of the proxy and audio one preview is drawn from."""

    source_asset_id: UUID
    workspace_id: UUID
    project_id: UUID
    proxy_key: str
    proxy_size: int
    proxy_sha256: bytes
    proxy_width: int
    proxy_height: int
    duration_ms: int
    audio_key: str
    audio_size: int
    audio_sha256: bytes


@dataclass(frozen=True, slots=True)
class PreviewMediaResult:
    """Every uploaded sheet in order, and the waveform."""

    sheets: tuple[IngestArtifact, ...]
    waveform: IngestArtifact


class StoryboardRenderer(Protocol):
    """The one media operation previews need."""

    def generate_storyboard(
        self,
        source: Path,
        workspace: Path,
        *,
        tile_width: int,
        tile_height: int,
        interval_ms: int,
        columns: int,
        rows: int,
        cancellation_check: CancellationCheck,
    ) -> None:
        """Write numbered JPEG sheets into the workspace."""
        ...


class PreviewMediaMaker(Protocol):
    """What the stage runner asks of whatever builds previews."""

    def build(
        self, *, inputs: PreviewInputs, workspace: Path, cancellation_check: CancellationCheck
    ) -> PreviewMediaResult:
        """Build, upload, and describe every preview artifact."""
        ...


def storyboard_asset_id(source_asset_id: UUID, index: int) -> UUID:
    """The deterministic Asset identity of one version-one sheet."""
    return uuid5(source_asset_id, f"storyboard-v1:{index}")


def waveform_asset_id(source_asset_id: UUID) -> UUID:
    """The deterministic Asset identity of the version-one waveform."""
    return uuid5(source_asset_id, "waveform-v1")


class PreviewMediaBuilder:
    """Download verified inputs, render sheets and peaks, and upload them verified."""

    def __init__(
        self, *, store: ObjectStore, downloader: SourceDownloader, media: StoryboardRenderer
    ) -> None:
        """Bind storage, private download, and media rendering boundaries."""
        self._store = store
        self._downloader = downloader
        self._media = media

    def build(
        self, *, inputs: PreviewInputs, workspace: Path, cancellation_check: CancellationCheck
    ) -> PreviewMediaResult:
        """Produce the storyboard sheets and waveform for one source."""
        layout = storyboard_layout(
            duration_ms=inputs.duration_ms, width=inputs.proxy_width, height=inputs.proxy_height
        )
        proxy_path = workspace / "preview-proxy.mp4"
        self._download(inputs.proxy_key, inputs.proxy_size, inputs.proxy_sha256, proxy_path, cancellation_check)
        cancellation_check()
        self._media.generate_storyboard(
            proxy_path,
            workspace,
            tile_width=layout.tile_width,
            tile_height=layout.tile_height,
            interval_ms=layout.policy.interval_ms,
            columns=layout.policy.columns,
            rows=layout.policy.rows,
            cancellation_check=cancellation_check,
        )
        rendered = [
            index
            for index in range(layout.sheet_count)
            if (workspace / f"sheet-{index:04d}.jpg").is_file()
        ]
        if not rendered or rendered != list(range(len(rendered))):
            raise IngestIntegrityError("storyboard sheets are missing or out of order")

        cancellation_check()
        audio_path = workspace / "preview-audio.wav"
        self._download(inputs.audio_key, inputs.audio_size, inputs.audio_sha256, audio_path, cancellation_check)
        peaks_path = workspace / "waveform.bin"
        with audio_path.open("rb") as audio:
            peaks_path.write_bytes(waveform_peaks(audio))

        sheets = tuple(
            self._upload_sheet(inputs, workspace, index, layout.tile_width * layout.policy.columns, layout.tile_height * layout.policy.rows, layout.sheet_duration_ms(index))
            for index in rendered
        )
        cancellation_check()
        return PreviewMediaResult(sheets=sheets, waveform=self._upload_waveform(inputs, workspace, peaks_path))

    def _download(
        self, key: str, size: int, sha256: bytes, destination: Path, cancellation_check: CancellationCheck
    ) -> None:
        """Stream one recorded object into the workspace and refuse it if its bytes changed."""
        signed = self._store.sign_download(key=key, expires_in=SIGNED_DOWNLOAD_TTL)
        with destination.open("wb") as output:
            downloaded = self._downloader.download(
                signed.url,
                output,
                expected_size=size,
                max_bytes=MAX_MEDIA_BYTES,
                cancellation_check=cancellation_check,
            )
        if downloaded.sha256 != sha256:
            raise IngestIntegrityError("a preview input no longer matches its recorded digest")

    def _upload_sheet(
        self, inputs: PreviewInputs, workspace: Path, index: int, width: int, height: int, duration_ms: int
    ) -> IngestArtifact:
        """Upload one sheet under its deterministic key and describe it."""
        key = preview_media_key(
            workspace_id=inputs.workspace_id,
            project_id=inputs.project_id,
            source_asset_id=inputs.source_asset_id,
            name=storyboard_sheet_name(index),
        )
        size_bytes, digest = upload_verified_artifact(
            self._store, path=workspace / f"sheet-{index:04d}.jpg", workspace=workspace, key=key, content_type="image/jpeg"
        )
        return IngestArtifact(
            asset_id=storyboard_asset_id(inputs.source_asset_id, index),
            kind=AssetKind.STORYBOARD,
            source_type=AssetSourceType.DERIVED,
            storage_key=key,
            content_type="image/jpeg",
            size_bytes=size_bytes,
            sha256=digest,
            duration_ms=duration_ms,
            width=width,
            height=height,
            video_codec=None,
            audio_codec=None,
        )

    def _upload_waveform(self, inputs: PreviewInputs, workspace: Path, path: Path) -> IngestArtifact:
        """Upload the waveform peaks under their deterministic key and describe them."""
        key = preview_media_key(
            workspace_id=inputs.workspace_id,
            project_id=inputs.project_id,
            source_asset_id=inputs.source_asset_id,
            name=WAVEFORM_V1_NAME,
        )
        size_bytes, digest = upload_verified_artifact(
            self._store, path=path, workspace=workspace, key=key, content_type="application/octet-stream"
        )
        return IngestArtifact(
            asset_id=waveform_asset_id(inputs.source_asset_id),
            kind=AssetKind.WAVEFORM,
            source_type=AssetSourceType.DERIVED,
            storage_key=key,
            content_type="application/octet-stream",
            size_bytes=size_bytes,
            sha256=digest,
            duration_ms=inputs.duration_ms,
            width=None,
            height=None,
            video_codec=None,
            audio_codec=None,
        )
```

Run `uv run ruff format src/clipah/assets/preview_builder.py` after writing it; the long call lines above are wrapped by the formatter. mypy runs strict with no implicit re-exports, so import each name from the module that defines it (`MAX_MEDIA_BYTES` from `clipah.assets.probe`, `CancellationCheck` from `clipah.assets.ffmpeg`).

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -q tests/unit/test_preview_builder.py tests/unit/test_ingest.py` → PASS.
Run: `uv run mypy src` → no errors.

---

### Task 5: The `PREVIEW_MEDIA` stage runner

**Files:**
- Create: `backend/src/clipah/jobs/preview_media_task.py`
- Modify: `backend/src/clipah/jobs/tasks.py` (register the runner)
- Create: `backend/tests/integration/test_preview_media_pipeline.py`

**Interfaces:**
- Consumes: `PreviewInputs`, `PreviewMediaResult`, `PreviewMediaMaker`, `PreviewMediaBuilder`, `storyboard_asset_id`, `waveform_asset_id` (Task 4); `job_workspace`; `JobContext`.
- Produces: `PreviewMediaStageRunner(maker_factory: Callable[[Settings], PreviewMediaMaker])`, `preview_media_stage_runner`, stable error codes `PREVIEW_MEDIA_INPUT_MISSING`, `PREVIEW_MEDIA_INTEGRITY`, `PREVIEW_MEDIA_INVALID_AUDIO`.

- [ ] **Step 1: Write the failing integration tests**

`backend/tests/integration/test_preview_media_pipeline.py`:

```python
"""Integration contracts for durable, retry-safe preview media.

Previews are decoration: a creator's clips must never wait on them, and a retry must never
draw them twice. These tests hold the runner to the same standard ingest is held to.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from clipah.assets.ingest import IngestArtifact, IngestIntegrityError
from clipah.assets.preview_builder import (
    PreviewInputs,
    PreviewMediaResult,
    storyboard_asset_id,
    waveform_asset_id,
)
from clipah.assets.preview_media import WaveformInputError
from clipah.assets.storage import ObjectStoreUnavailableError
from clipah.db import RuntimeRole
from clipah.jobs.models import JobContext, RetryableJobError, TerminalJobError
from clipah.jobs.preview_media_task import PreviewMediaStageRunner
from clipah.jobs.tasks import stage_runners
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
)
from support import provision_identity, runtime_settings


class StaticMaker:
    """Return two sheets and a waveform without touching FFmpeg or storage."""

    def __init__(self) -> None:
        self.inputs: list[PreviewInputs] = []

    def build(
        self, *, inputs: PreviewInputs, workspace: Path, cancellation_check: Callable[[], None]
    ) -> PreviewMediaResult:
        cancellation_check()
        assert workspace.is_dir()
        self.inputs.append(inputs)
        prefix = (
            f"workspaces/{inputs.workspace_id}/projects/{inputs.project_id}/derived/"
            f"{inputs.source_asset_id}"
        )
        sheets = tuple(
            IngestArtifact(
                asset_id=storyboard_asset_id(inputs.source_asset_id, index),
                kind=AssetKind.STORYBOARD,
                source_type=AssetSourceType.DERIVED,
                storage_key=f"{prefix}/storyboard-v1/sheet-{index:04d}.jpg",
                content_type="image/jpeg",
                size_bytes=40,
                sha256=hashlib.sha256(f"sheet-{index}".encode()).digest(),
                duration_ms=200_000 if index == 0 else 5_000,
                width=1600,
                height=900,
                video_codec=None,
                audio_codec=None,
            )
            for index in range(2)
        )
        waveform = IngestArtifact(
            asset_id=waveform_asset_id(inputs.source_asset_id),
            kind=AssetKind.WAVEFORM,
            source_type=AssetSourceType.DERIVED,
            storage_key=f"{prefix}/waveform-v1.bin",
            content_type="application/octet-stream",
            size_bytes=4_100,
            sha256=hashlib.sha256(b"peaks").digest(),
            duration_ms=205_000,
            width=None,
            height=None,
            video_codec=None,
            audio_codec=None,
        )
        return PreviewMediaResult(sheets=sheets, waveform=waveform)


class FailingMaker(StaticMaker):
    """Raise one boundary failure on build."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def build(self, **kwargs: object) -> PreviewMediaResult:  # type: ignore[override]
        del kwargs
        raise self.error


@pytest.mark.integration
def test_the_preview_runner_is_registered_for_its_job_kind() -> None:
    """A queued preview Job must never fall through to the unsupported-kind failure."""
    assert JobKind.PREVIEW_MEDIA in stage_runners()


@pytest.mark.integration
def test_previews_are_recorded_once_and_a_redelivery_reuses_them(engine: Engine) -> None:
    """Two deliveries of one Job converge on one sheet set and one waveform."""
    context, source_id = _seed(engine, suffix=f"preview-{uuid4().hex[:8]}")
    maker = StaticMaker()
    runner = PreviewMediaStageRunner(maker_factory=lambda _settings: maker)

    runner(context)
    runner(context)

    assert len(maker.inputs) == 1
    recorded = maker.inputs[0]
    assert (recorded.proxy_width, recorded.proxy_height, recorded.duration_ms) == (1280, 720, 205_000)
    with Session(engine) as session:
        kinds = session.scalars(
            select(Asset.kind).where(
                Asset.workspace_id == context.workspace_id,
                Asset.kind.in_([AssetKind.STORYBOARD, AssetKind.WAVEFORM]),
            )
        ).all()
        project = session.get(Project, context.project_id)
    assert sorted(kind.value for kind in kinds) == ["storyboard", "storyboard", "waveform"]
    assert project is not None and project.status is ProjectStatus.TRANSCRIBING


@pytest.mark.integration
def test_a_project_without_ingest_outputs_fails_terminally(engine: Engine) -> None:
    """Previews of a source ingest never finished would be previews of nothing."""
    context, _ = _seed(engine, suffix=f"preview-missing-{uuid4().hex[:8]}", derivatives=False)

    with pytest.raises(TerminalJobError, match=r"^PREVIEW_MEDIA_INPUT_MISSING$"):
        PreviewMediaStageRunner(maker_factory=lambda _settings: StaticMaker())(context)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("error", "expected", "code"),
    [
        (ObjectStoreUnavailableError("secret detail"), RetryableJobError, "ASSET_STORAGE_UNAVAILABLE"),
        (IngestIntegrityError("detail"), TerminalJobError, "PREVIEW_MEDIA_INTEGRITY"),
        (WaveformInputError("detail"), TerminalJobError, "PREVIEW_MEDIA_INVALID_AUDIO"),
    ],
)
def test_boundary_failures_map_to_stable_codes(
    engine: Engine, error: Exception, expected: type[Exception], code: str
) -> None:
    """No provider or file detail may leave the runner as a Job error code."""
    context, _ = _seed(engine, suffix=f"preview-fail-{uuid4().hex[:8]}")

    with pytest.raises(expected, match=rf"^{code}$"):
        PreviewMediaStageRunner(maker_factory=lambda _settings: FailingMaker(error))(context)


@pytest.mark.integration
def test_a_conflicting_existing_sheet_rolls_back_the_whole_set(engine: Engine) -> None:
    """One sheet recorded with other bytes must stop every insert, not leave a partial set."""
    context, source_id = _seed(engine, suffix=f"preview-conflict-{uuid4().hex[:8]}")
    with engine.begin() as connection:
        connection.execute(
            Asset.__table__.insert().values(
                id=storyboard_asset_id(source_id, 1),
                workspace_id=context.workspace_id,
                project_id=context.project_id,
                kind=AssetKind.STORYBOARD,
                source_type=AssetSourceType.DERIVED,
                storage_key="conflicting-key",
                content_type="image/jpeg",
                size_bytes=1,
                sha256=b"z" * 32,
            )
        )

    with pytest.raises(TerminalJobError, match=r"^PREVIEW_MEDIA_INTEGRITY$"):
        PreviewMediaStageRunner(maker_factory=lambda _settings: StaticMaker())(context)

    with Session(engine) as session:
        waveform = session.get(Asset, waveform_asset_id(source_id))
    assert waveform is None


def _seed(engine: Engine, *, suffix: str, derivatives: bool = True) -> tuple[JobContext, UUID]:
    """Create one running PREVIEW_MEDIA Job and the source, proxy, and audio ingest recorded."""
    user_id, workspace_id = provision_identity(engine, suffix=suffix)
    project_id, job_id, source_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(tz=UTC)
    prefix = f"workspaces/{workspace_id}/projects/{project_id}"
    with engine.begin() as connection:
        connection.execute(
            Project.__table__.insert().values(
                id=project_id,
                workspace_id=workspace_id,
                created_by_user_id=user_id,
                name=f"Preview {suffix}",
                status=ProjectStatus.TRANSCRIBING,
                source_kind=SourceKind.UPLOAD,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            Job.__table__.insert().values(
                id=job_id,
                workspace_id=workspace_id,
                project_id=project_id,
                kind=JobKind.PREVIEW_MEDIA,
                status=JobStatus.RUNNING,
                stage="queued",
                progress=0,
                attempt=1,
                idempotency_key=f"preview-{suffix}",
            )
        )
        rows = [
            dict(id=source_id, kind=AssetKind.SOURCE, source_type=AssetSourceType.USER_UPLOAD, storage_key=f"{prefix}/source/{source_id}", content_type="video/mp4", size_bytes=6, duration_ms=205_000, width=1920, height=1080, video_codec="h264", audio_codec="aac"),
        ]
        if derivatives:
            rows += [
                dict(id=uuid5(source_id, "proxy"), kind=AssetKind.PROXY, source_type=AssetSourceType.DERIVED, storage_key=f"{prefix}/derived/{source_id}/proxy", content_type="video/mp4", size_bytes=100, duration_ms=205_000, width=1280, height=720, video_codec="h264", audio_codec="aac"),
                dict(id=uuid5(source_id, "transcription_audio"), kind=AssetKind.TRANSCRIPTION_AUDIO, source_type=AssetSourceType.DERIVED, storage_key=f"{prefix}/derived/{source_id}/transcription_audio", content_type="audio/wav", size_bytes=80, duration_ms=205_000, width=None, height=None, video_codec=None, audio_codec="pcm_s16le"),
            ]
        for row in rows:
            connection.execute(
                Asset.__table__.insert().values(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    sha256=hashlib.sha256(str(row["kind"]).encode()).digest(),
                    **row,
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
    )
```

Run `uv run ruff format tests/integration/test_preview_media_pipeline.py` after writing it.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest -q tests/integration/test_preview_media_pipeline.py`
Expected: FAIL — `ModuleNotFoundError: clipah.jobs.preview_media_task`.

- [ ] **Step 3: Implement `backend/src/clipah/jobs/preview_media_task.py`**

```python
"""Durable PREVIEW_MEDIA stage runner: storyboard sheets and waveform peaks for one source.

It follows the ingest runner's shape — short tenant transactions around external work,
deterministic identities, and reuse on redelivery — and it only ever adds derived Assets,
so nothing a creator is waiting on can be delayed or changed by it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache
from uuid import uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from clipah.assets.ffmpeg import MEDIA_PROCESS_TIMEOUT, FFmpegRunner, MediaProcessError
from clipah.assets.ingest import (
    HttpxSourceDownloader,
    IngestArtifact,
    IngestIntegrityError,
    SourceDownloadError,
)
from clipah.assets.preview_builder import (
    PreviewInputs,
    PreviewMediaBuilder,
    PreviewMediaMaker,
    PreviewMediaResult,
    waveform_asset_id,
)
from clipah.assets.preview_media import WaveformInputError
from clipah.assets.storage import ObjectStoreUnavailableError, observed_s3_store
from clipah.config import Settings
from clipah.db import RuntimeRole, session_scope
from clipah.jobs.models import JobCancelledError, JobContext, RetryableJobError, TerminalJobError
from clipah.jobs.workspace import job_workspace
from clipah.models import Asset, AssetKind

MakerFactory = Callable[[Settings], PreviewMediaMaker]
INPUT_MISSING_CODE = "PREVIEW_MEDIA_INPUT_MISSING"
INTEGRITY_CODE = "PREVIEW_MEDIA_INTEGRITY"
INVALID_AUDIO_CODE = "PREVIEW_MEDIA_INVALID_AUDIO"


class PreviewMediaStageRunner:
    """Build previews outside transactions and record them atomically."""

    def __init__(self, *, maker_factory: MakerFactory) -> None:
        """Bind production or deterministic preview construction."""
        self._maker_factory = maker_factory

    def __call__(self, context: JobContext) -> None:
        """Process one source and expose only stable retryable or terminal codes."""
        try:
            context.raise_if_cancelled()
            inputs = self._load_inputs(context)
            if self._already_complete(context, inputs):
                return
            with job_workspace(context.job_id) as workspace:
                result = self._maker_factory(context.settings).build(
                    inputs=inputs, workspace=workspace, cancellation_check=context.raise_if_cancelled
                )
            context.raise_if_cancelled()
            self._persist(context, result)
        except JobCancelledError:
            raise
        except SourceDownloadError as error:
            raise RetryableJobError(str(error) or "ASSET_SOURCE_UNAVAILABLE") from None
        except ObjectStoreUnavailableError:
            raise RetryableJobError("ASSET_STORAGE_UNAVAILABLE") from None
        except MediaProcessError as error:
            if error.code == MEDIA_PROCESS_TIMEOUT:
                raise RetryableJobError(error.code) from None
            raise TerminalJobError(error.code) from None
        except WaveformInputError:
            raise TerminalJobError(INVALID_AUDIO_CODE) from None
        except IngestIntegrityError:
            raise TerminalJobError(INTEGRITY_CODE) from None

    def _load_inputs(self, context: JobContext) -> PreviewInputs:
        """Read the single source and its recorded proxy and transcription audio."""
        with _transaction(context) as session:
            sources = session.scalars(
                select(Asset)
                .where(
                    Asset.workspace_id == context.workspace_id,
                    Asset.project_id == context.project_id,
                    Asset.kind == AssetKind.SOURCE,
                )
                .limit(2)
            ).all()
            if len(sources) != 1:
                raise TerminalJobError(INPUT_MISSING_CODE)
            source = sources[0]
            proxy = session.get(Asset, uuid5(source.id, AssetKind.PROXY.value))
            audio = session.get(Asset, uuid5(source.id, AssetKind.TRANSCRIPTION_AUDIO.value))
            if (
                proxy is None
                or audio is None
                or proxy.project_id != context.project_id
                or audio.project_id != context.project_id
                or proxy.width is None
                or proxy.height is None
                or proxy.duration_ms is None
            ):
                raise TerminalJobError(INPUT_MISSING_CODE)
            return PreviewInputs(
                source_asset_id=source.id,
                workspace_id=context.workspace_id,
                project_id=context.project_id,
                proxy_key=proxy.storage_key,
                proxy_size=proxy.size_bytes,
                proxy_sha256=proxy.sha256,
                proxy_width=proxy.width,
                proxy_height=proxy.height,
                duration_ms=proxy.duration_ms,
                audio_key=audio.storage_key,
                audio_size=audio.size_bytes,
                audio_sha256=audio.sha256,
            )

    def _already_complete(self, context: JobContext, inputs: PreviewInputs) -> bool:
        """A recorded waveform and at least one sheet mean an earlier delivery finished."""
        with _transaction(context) as session:
            waveform = session.get(Asset, waveform_asset_id(inputs.source_asset_id))
            sheet = session.scalar(
                select(Asset.id)
                .where(
                    Asset.workspace_id == context.workspace_id,
                    Asset.project_id == context.project_id,
                    Asset.kind == AssetKind.STORYBOARD,
                )
                .limit(1)
            )
            return waveform is not None and sheet is not None

    def _persist(self, context: JobContext, result: PreviewMediaResult) -> None:
        """Verify existing identities, then insert every missing artifact in one transaction."""
        artifacts = (*result.sheets, result.waveform)
        identifiers = sorted((artifact.asset_id for artifact in artifacts), key=str)
        with _transaction(context) as session:
            existing = {
                row.id: row
                for row in session.scalars(
                    select(Asset)
                    .where(Asset.workspace_id == context.workspace_id, Asset.id.in_(identifiers))
                    .order_by(Asset.id)
                    .with_for_update()
                )
            }
            for artifact in artifacts:
                row = existing.get(artifact.asset_id)
                if row is not None and not _matches(row, artifact):
                    raise IngestIntegrityError("preview Asset metadata mismatch")
            for artifact in artifacts:
                if artifact.asset_id not in existing:
                    session.add(_asset_row(context, artifact))
            session.flush()


def _matches(row: Asset, artifact: IngestArtifact) -> bool:
    """Whether a recorded row describes exactly this artifact."""
    return (
        row.kind is artifact.kind
        and row.storage_key == artifact.storage_key
        and row.content_type == artifact.content_type
        and row.size_bytes == artifact.size_bytes
        and row.sha256 == artifact.sha256
        and row.duration_ms == artifact.duration_ms
        and row.width == artifact.width
        and row.height == artifact.height
    )


def _asset_row(context: JobContext, artifact: IngestArtifact) -> Asset:
    """Convert one artifact description into its tenant-scoped row."""
    return Asset(
        id=artifact.asset_id,
        workspace_id=context.workspace_id,
        project_id=context.project_id,
        kind=artifact.kind,
        source_type=artifact.source_type,
        storage_key=artifact.storage_key,
        content_type=artifact.content_type,
        size_bytes=artifact.size_bytes,
        sha256=artifact.sha256,
        duration_ms=artifact.duration_ms,
        width=artifact.width,
        height=artifact.height,
        video_codec=artifact.video_codec,
        audio_codec=artifact.audio_codec,
    )


@contextmanager
def _transaction(context: JobContext) -> Iterator[Session]:
    """Open one least-privilege worker transaction for this Job's tenant."""
    with session_scope(
        settings=context.settings,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        runtime_role=RuntimeRole.WORKER,
    ) as session:
        yield session


def production_preview_maker(settings: Settings) -> PreviewMediaMaker:
    """Compose object storage, private download, and the pinned FFmpeg runner."""
    if (
        settings.object_store_bucket is None
        or settings.object_store_access_key_id is None
        or settings.object_store_secret_access_key is None
    ):
        raise RuntimeError("preview worker requires configured object storage")
    store = observed_s3_store(
        bucket=settings.object_store_bucket,
        endpoint_url=settings.object_store_endpoint,
        access_key_id=settings.object_store_access_key_id.get_secret_value(),
        secret_access_key=settings.object_store_secret_access_key.get_secret_value(),
    )
    return PreviewMediaBuilder(store=store, downloader=HttpxSourceDownloader(), media=_media_runner())


@lru_cache(maxsize=1)
def _media_runner() -> FFmpegRunner:
    """One runner per worker process; ingest readiness already validated the tools."""
    return FFmpegRunner()


preview_media_stage_runner = PreviewMediaStageRunner(maker_factory=production_preview_maker)
```

Note `_persist` raises `IngestIntegrityError` inside the transaction; the `__call__` handler maps it to `PREVIEW_MEDIA_INTEGRITY` after `session_scope` rolls back, which is what the conflict test proves.

- [ ] **Step 4: Register the runner in `backend/src/clipah/jobs/tasks.py`**

Add beside the other late imports and registrations:

```python
from clipah.jobs.preview_media_task import preview_media_stage_runner  # noqa: E402
```

```python
_STAGE_RUNNERS.setdefault(JobKind.PREVIEW_MEDIA, preview_media_stage_runner)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -q tests/integration/test_preview_media_pipeline.py` → PASS.
Run: `uv run mypy src` → no errors.

---

### Task 6: Admit previews beside transcription

**Files:**
- Modify: `backend/src/clipah/jobs/pipeline.py`
- Modify: `backend/src/clipah/jobs/tasks.py` (`_run_stage`)
- Modify: `backend/tests/integration/test_pipeline_chaining.py`
- Modify: `backend/tests/integration/test_jobs.py`

**Interfaces:**
- Produces: `SIDE_STAGES: Mapping[JobKind, tuple[JobKind, ...]]` (`{INGEST: (PREVIEW_MEDIA,)}`), `admit_side_stages(session, *, policy, access, project_id, completed_kind, completed_job_id, now) -> tuple[JobSnapshot, ...]`.

- [ ] **Step 1: Write the failing pipeline tests**

Append to `backend/tests/integration/test_pipeline_chaining.py` (it already has `_workspace_with_project`, `_finished_job`, `_worker_session`, `_access`, `_project_status`, `_job_kinds`):

```python
@pytest.mark.integration
def test_finished_ingest_admits_previews_beside_transcription_without_moving_the_project(
    engine: Engine,
) -> None:
    """Previews decorate a Project; only the belt decides where it is."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="side")
    completed = _finished_job(workspace_id, user_id, project_id, kind=JobKind.INGEST)

    following, side = _advance_with_side(workspace_id, user_id, project_id, completed, JobKind.INGEST)

    assert following is not None
    assert [entry.kind for entry in side] == [JobKind.PREVIEW_MEDIA]
    assert _project_status(workspace_id, user_id, project_id) is ProjectStatus.TRANSCRIBING
    assert _job_kinds(workspace_id, user_id, project_id).count(JobKind.PREVIEW_MEDIA) == 1


@pytest.mark.integration
def test_a_replayed_ingest_completion_admits_previews_once(engine: Engine) -> None:
    """The preview Job is keyed by the ingest Job it follows, like every successor."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="side-replay")
    completed = _finished_job(workspace_id, user_id, project_id, kind=JobKind.INGEST)

    _advance_with_side(workspace_id, user_id, project_id, completed, JobKind.INGEST)
    _, side = _advance_with_side(workspace_id, user_id, project_id, completed, JobKind.INGEST)

    assert len(side) == 1
    assert _job_kinds(workspace_id, user_id, project_id).count(JobKind.PREVIEW_MEDIA) == 1


@pytest.mark.integration
def test_a_full_workspace_refuses_previews_silently_and_still_transcribes(engine: Engine) -> None:
    """A concurrency slot must go to the pipeline, never to decoration."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="side-full")
    completed = _finished_job(workspace_id, user_id, project_id, kind=JobKind.INGEST)
    settings = runtime_settings(concurrent_jobs_per_workspace=1)

    with _worker_session(workspace_id, user_id) as session:
        access = _access(session, user_id=user_id, workspace_id=workspace_id)
        following = advance_after(
            session,
            policy=admission_policy(settings),
            access=access,
            project_id=project_id,
            completed_kind=JobKind.INGEST,
            completed_job_id=completed,
            now=NOW,
        )
        side = admit_side_stages(
            session,
            policy=admission_policy(settings),
            access=access,
            project_id=project_id,
            completed_kind=JobKind.INGEST,
            completed_job_id=completed,
            now=NOW,
        )

    assert following is not None and following.kind is JobKind.TRANSCRIBE
    assert side == ()
    assert JobKind.PREVIEW_MEDIA not in _job_kinds(workspace_id, user_id, project_id)


@pytest.mark.integration
@pytest.mark.parametrize("finished", [JobKind.SOURCE_IMPORT, JobKind.TRANSCRIBE, JobKind.ANALYZE])
def test_no_other_stage_admits_previews(engine: Engine, finished: JobKind) -> None:
    """Only a finished ingest has a proxy and audio to draw from."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="side-other")
    completed = _finished_job(workspace_id, user_id, project_id, kind=finished)

    _, side = _advance_with_side(workspace_id, user_id, project_id, completed, finished)

    assert side == ()


@pytest.mark.integration
def test_a_deleted_project_gets_no_previews(engine: Engine) -> None:
    """Deleted work stays deleted, decoration included."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="side-deleted")
    completed = _finished_job(workspace_id, user_id, project_id, kind=JobKind.INGEST)
    with _api_session(workspace_id, user_id) as session:
        session.execute(update(Project).where(Project.id == project_id).values(archived_at=NOW))

    _, side = _advance_with_side(workspace_id, user_id, project_id, completed, JobKind.INGEST)

    assert side == ()


def _advance_with_side(
    workspace_id: UUID, user_id: UUID, project_id: UUID, completed: UUID, kind: JobKind
) -> tuple[object | None, tuple[JobSnapshot, ...]]:
    """Advance and admit side stages in one worker transaction, as a finishing Job does."""
    with _worker_session(workspace_id, user_id) as session:
        policy = admission_policy(runtime_settings())
        access = _access(session, user_id=user_id, workspace_id=workspace_id)
        following = advance_after(
            session, policy=policy, access=access, project_id=project_id,
            completed_kind=kind, completed_job_id=completed, now=NOW,
        )
        side = admit_side_stages(
            session, policy=policy, access=access, project_id=project_id,
            completed_kind=kind, completed_job_id=completed, now=NOW,
        )
        return following, side
```

Update the imports: `from clipah.jobs.pipeline import admit_side_stages, advance_after, pipeline_key, start_stage` and `from clipah.jobs.models import JobSnapshot`.

Confirm `runtime_settings(concurrent_jobs_per_workspace=1)` is accepted (the helper forwards overrides to `Settings`); `_finished_job` leaves the ingest Job succeeded, so the one slot goes to transcription.

Add to `backend/tests/integration/test_jobs.py` beside `test_an_eager_task_runs_its_registered_stage_and_succeeds`:

```python
@pytest.mark.integration
def test_a_finished_ingest_dispatches_transcription_and_previews(
    engine: Engine, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both successors are committed with the completion and both are woken afterwards."""
    user_id, workspace_id, project_id = _workspace_with_project(engine, suffix="dispatch-side")
    job_id = _queued_job(workspace_id, user_id, project_id, clock, key="dispatch-side")
    dispatched: list[JobKind] = []
    monkeypatch.setattr(
        task_module, "_dispatch_next", lambda **kwargs: dispatched.append(kwargs["kind"])
    )

    with _eager_celery():
        stage_runners()[JobKind.INGEST] = lambda context: None
        run_job.apply(args=(str(job_id), str(workspace_id), str(user_id))).get()

    assert dispatched == [JobKind.TRANSCRIBE, JobKind.PREVIEW_MEDIA]
```

(`task_module` is the file's existing alias for `clipah.jobs.tasks`; `_queued_job` already creates an INGEST Job.)

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest -q tests/integration/test_pipeline_chaining.py tests/integration/test_jobs.py -k "side or previews or dispatches"`
Expected: FAIL — `ImportError: admit_side_stages`.

- [ ] **Step 3: Implement side stages in `backend/src/clipah/jobs/pipeline.py`**

```python
from clipah.jobs.admission import AdmissionPolicy, ConcurrencyLimitError

#: Work a finished stage adds beside the belt: it decorates a Project and never moves it.
SIDE_STAGES = MappingProxyType({JobKind.INGEST: (JobKind.PREVIEW_MEDIA,)})


def admit_side_stages(
    session: Session,
    *,
    policy: AdmissionPolicy,
    access: WorkspaceAccess,
    project_id: UUID,
    completed_kind: JobKind,
    completed_job_id: UUID,
    now: datetime,
) -> tuple[JobSnapshot, ...]:
    """Admit decoration after one finished stage, yielding every refusal to the belt.

    Call this after `advance_after`, in the same transaction, so the belt's successor has
    already taken its concurrency slot. A refusal records nothing: a later backfill can ask
    again, while a pipeline stage that waited for decoration would be a Project that stalled.
    """
    kinds = SIDE_STAGES.get(completed_kind, ())
    if not kinds:
        return ()
    if _live_project(session, workspace_id=access.workspace_id, project_id=project_id) is None:
        return ()
    admitted: list[JobSnapshot] = []
    for kind in kinds:
        try:
            admitted.append(
                create_job(
                    session,
                    policy=policy,
                    access=access,
                    project_id=project_id,
                    kind=kind,
                    idempotency_key=pipeline_key(after_job_id=completed_job_id, kind=kind),
                    now=now,
                )
            )
        except ConcurrencyLimitError:
            continue
    return tuple(admitted)
```

`admit_job` holds its checks in a savepoint, so a `ConcurrencyLimitError` leaves the surrounding transaction and the successor intact.

- [ ] **Step 4: Dispatch side stages in `backend/src/clipah/jobs/tasks.py`**

Replace the completion block in `_run_stage` with:

```python
    with _transaction(settings, workspace, user) as session:
        completed = complete_job_after_runner(
            session,
            workspace_id=workspace,
            job_id=job,
            now=_now(),
        )
        # The successor is committed in the same transaction as the completion, so a
        # Project can never be recorded as finished with one stage and stranded before
        # the next. Decoration is admitted after it and may be refused without harm.
        policy = admission_policy(settings)
        access = DatabaseWorkspaceAuthorizer(session).access_for(
            user_id=user, workspace_id=workspace
        )
        following = advance_after(
            session,
            policy=policy,
            access=access,
            project_id=snapshot.project_id,
            completed_kind=snapshot.kind,
            completed_job_id=job,
            now=_now(),
        )
        side = admit_side_stages(
            session,
            policy=policy,
            access=access,
            project_id=snapshot.project_id,
            completed_kind=snapshot.kind,
            completed_job_id=job,
            now=_now(),
        )
        woken = tuple(
            (entry.job_id, entry.kind) for entry in (*(() if following is None else (following,)), *side)
        )
    _announce(notifier, workspace_id=workspace, job_id=job)
    for woken_id, woken_kind in woken:
        _dispatch_next(job_id=woken_id, workspace_id=workspace, user_id=user, kind=woken_kind)
        _announce(notifier, workspace_id=workspace, job_id=woken_id)
    _record_stage(snapshot, outcome="succeeded", code="OK", started=started)
    return completed.status.value
```

and import `admit_side_stages` beside `advance_after`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -q tests/integration/test_pipeline_chaining.py tests/integration/test_jobs.py` → PASS.

---

### Task 7: Storyboard, waveform, and transcript reads

**Files:**
- Modify: `backend/src/clipah/studio/use_cases.py`
- Modify: `backend/src/clipah/api/routes/studio.py`
- Modify: `backend/tests/integration/test_studio_browsing.py`

**Interfaces:**
- Produces use cases: `project_storyboard(session, store, *, access, project_id) -> StoryboardView`, `project_waveform(session, store, *, access, project_id) -> WaveformView`, `project_transcript(session, *, access, project_id) -> TranscriptView`; each raises `StudioNotFoundError`.
- Produces routes and schemas:
  - `GET /api/v1/projects/{project_id}/storyboard` → `StoryboardResponse { version: int, intervalMs: int, tileWidth: int, tileHeight: int, columns: int, rows: int, durationMs: int, sheets: StoryboardSheetResponse[], expiresAt: str }`, `StoryboardSheetResponse { index: int, startMs: int, tileCount: int, url: str }`.
  - `GET /api/v1/projects/{project_id}/waveform` → `WaveformResponse { version: int, peaksPerSecond: int, durationMs: int, url: str, expiresAt: str }`.
  - `GET /api/v1/projects/{project_id}/transcript` → `TranscriptResponse { language: str, durationMs: int, words: TranscriptWordResponse[] }`, `TranscriptWordResponse { id: str, text: str, punctuation: str, startMs: int, endMs: int, speaker: str }`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/integration/test_studio_browsing.py`:

```python
@pytest.mark.integration
def test_a_storyboard_describes_every_sheet_and_signs_each_one(engine: Engine) -> None:
    """A poster is drawn by offset, so every sheet must say where its frames start."""
    stage = _staged(engine)
    before = stage.browser.get(_path(stage, f"/projects/{stage.project_id}/storyboard"))
    _preview_asset(stage, name="storyboard-v1/sheet-0001.jpg", kind=AssetKind.STORYBOARD, duration_ms=5_000)
    _preview_asset(stage, name="storyboard-v1/sheet-0000.jpg", kind=AssetKind.STORYBOARD, duration_ms=200_000)

    response = stage.browser.get(_path(stage, f"/projects/{stage.project_id}/storyboard"))

    assert_error(before, status_code=404, code="NOT_FOUND")
    assert response.status_code == 200
    body = response.json()
    assert {key: body[key] for key in ("version", "intervalMs", "tileWidth", "tileHeight", "columns", "rows", "durationMs")} == {
        "version": 1,
        "intervalMs": 2_000,
        "tileWidth": 160,
        "tileHeight": 90,
        "columns": 10,
        "rows": 10,
        "durationMs": 205_000,
    }
    assert [(sheet["index"], sheet["startMs"], sheet["tileCount"]) for sheet in body["sheets"]] == [
        (0, 0, 100),
        (1, 200_000, 3),
    ]
    assert all(sheet["url"].startswith("fake://download/") for sheet in body["sheets"])
    assert "storageKey" not in str(body)


@pytest.mark.integration
def test_a_waveform_is_a_short_lived_capability_with_its_geometry(engine: Engine) -> None:
    """The timeline must know how many peaks make a second before it can draw them."""
    stage = _staged(engine)
    _preview_asset(stage, name="waveform-v1.bin", kind=AssetKind.WAVEFORM, duration_ms=60_000)

    body = stage.browser.get(_path(stage, f"/projects/{stage.project_id}/waveform")).json()

    assert body["version"] == 1
    assert body["peaksPerSecond"] == 20
    assert body["durationMs"] == 60_000
    assert body["url"].startswith("fake://download/")


@pytest.mark.integration
def test_a_transcript_lists_its_words_in_order_with_speakers(engine: Engine) -> None:
    """Review mode and the Project page show what was said around every moment."""
    stage = _staged(engine)

    body = stage.browser.get(_path(stage, f"/projects/{stage.project_id}/transcript")).json()

    assert body == {
        "language": "id",
        "durationMs": 60_000,
        "words": [
            {
                "id": "w000001",
                "text": "Satu",
                "punctuation": "",
                "startMs": 5_000,
                "endMs": 5_900,
                "speaker": "SPEAKER_00",
            }
        ],
    }


@pytest.mark.integration
@pytest.mark.parametrize("suffix", ["storyboard", "waveform", "transcript"])
def test_previews_and_transcripts_of_another_workspace_answer_like_missing_ones(
    engine: Engine, suffix: str
) -> None:
    """A guessed Project must not reveal that its media exists."""
    stage = _staged(engine)
    _preview_asset(stage, name="storyboard-v1/sheet-0000.jpg", kind=AssetKind.STORYBOARD, duration_ms=200_000)
    _preview_asset(stage, name="waveform-v1.bin", kind=AssetKind.WAVEFORM, duration_ms=60_000)
    stranger, stranger_workspace = _stranger(stage)

    guessed = stranger.get(f"/api/v1/projects/{stage.project_id}/{suffix}?workspace_id={stranger_workspace}")
    missing = stranger.get(f"/api/v1/projects/{uuid4()}/{suffix}?workspace_id={stranger_workspace}")

    assert_error(guessed, status_code=404, code="NOT_FOUND")
    assert_error(missing, status_code=404, code="NOT_FOUND")
    assert guessed.json()["error"]["message"] == missing.json()["error"]["message"]


def _preview_asset(stage: Stage, *, name: str, kind: AssetKind, duration_ms: int) -> UUID:
    """Record one preview artifact the way the preview runner would have."""
    asset_id = uuid4()
    key = f"workspaces/{stage.workspace_id}/projects/{stage.project_id}/derived/{stage.source_asset_id}/{name}"
    content_type = "image/jpeg" if kind is AssetKind.STORYBOARD else "application/octet-stream"
    with stage.engine.begin() as connection:
        connection.execute(
            Asset.__table__.insert().values(
                id=asset_id,
                workspace_id=stage.workspace_id,
                project_id=stage.project_id,
                kind=kind,
                source_type=AssetSourceType.DERIVED,
                storage_key=key,
                content_type=content_type,
                size_bytes=512,
                duration_ms=duration_ms,
                width=1600 if kind is AssetKind.STORYBOARD else None,
                height=900 if kind is AssetKind.STORYBOARD else None,
                sha256=b"p" * 32,
            )
        )
    stage.store.objects[key] = StoredObject(key=key, content_type=content_type, content_length=512)
    return asset_id
```

and extend `test_the_studio_reads_declare_strict_response_schemas`'s `expected` mapping with:

```python
        "/api/v1/projects/{project_id}/storyboard": "StoryboardResponse",
        "/api/v1/projects/{project_id}/waveform": "WaveformResponse",
        "/api/v1/projects/{project_id}/transcript": "TranscriptResponse",
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest -q tests/integration/test_studio_browsing.py -k "storyboard or waveform or transcript or strict"`
Expected: FAIL — 404 for every new path and missing schema components.

- [ ] **Step 3: Implement the use cases**

Append to `backend/src/clipah/studio/use_cases.py` (import `Transcript` from `clipah.models` and the helpers from `clipah.assets.preview_media`):

```python
@dataclass(frozen=True, slots=True)
class StoryboardSheet:
    """One signed sheet and the frames it holds."""

    index: int
    start_ms: int
    tile_count: int
    download: SignedUrl


@dataclass(frozen=True, slots=True)
class StoryboardView:
    """Everything a browser needs to draw any moment of a source from its sheets."""

    version: int
    interval_ms: int
    tile_width: int
    tile_height: int
    columns: int
    rows: int
    duration_ms: int
    sheets: tuple[StoryboardSheet, ...]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class WaveformView:
    """A signed waveform and how to read it."""

    version: int
    peaks_per_second: int
    duration_ms: int
    download: SignedUrl


@dataclass(frozen=True, slots=True)
class TranscriptWordView:
    """One transcribed word with its timing and speaker."""

    word_id: str
    text: str
    punctuation: str
    start_ms: int
    end_ms: int
    speaker: str


@dataclass(frozen=True, slots=True)
class TranscriptView:
    """One Project's canonical transcript, in spoken order."""

    language: str
    duration_ms: int
    words: tuple[TranscriptWordView, ...]


def project_storyboard(
    session: Session, store: ObjectStore, *, access: WorkspaceAccess, project_id: UUID
) -> StoryboardView:
    """Sign every version-one sheet of one active Project, in order."""
    rows = session.execute(
        select(Asset.storage_key, Asset.width, Asset.height, Asset.duration_ms)
        .join(Project, _active_project(Asset.workspace_id, Asset.project_id))
        .where(
            Asset.workspace_id == access.workspace_id,
            Asset.project_id == project_id,
            Asset.kind == AssetKind.STORYBOARD,
        )
        .order_by(Asset.storage_key)
    ).all()
    policy = STORYBOARD_V1
    sheets: list[StoryboardSheet] = []
    tile_width = tile_height = 0
    duration_ms = 0
    for key, width, height, sheet_duration in rows:
        index = storyboard_sheet_index(key)
        if index is None or width is None or height is None or sheet_duration is None:
            continue
        tile_width, tile_height = width // policy.columns, height // policy.rows
        start_ms = index * policy.frames_per_sheet * policy.interval_ms
        duration_ms = max(duration_ms, start_ms + sheet_duration)
        sheets.append(
            StoryboardSheet(
                index=index,
                start_ms=start_ms,
                tile_count=max(1, -(-sheet_duration // policy.interval_ms)),
                download=store.sign_download(key=key, expires_in=SIGNED_URL_TTL),
            )
        )
    if not sheets:
        raise StudioNotFoundError(str(project_id))
    return StoryboardView(
        version=policy.version,
        interval_ms=policy.interval_ms,
        tile_width=tile_width,
        tile_height=tile_height,
        columns=policy.columns,
        rows=policy.rows,
        duration_ms=duration_ms,
        sheets=tuple(sheets),
        expires_at=min(sheet.download.expires_at for sheet in sheets),
    )


def project_waveform(
    session: Session, store: ObjectStore, *, access: WorkspaceAccess, project_id: UUID
) -> WaveformView:
    """Sign the version-one waveform of one active Project."""
    row = session.execute(
        select(Asset.storage_key, Asset.duration_ms)
        .join(Project, _active_project(Asset.workspace_id, Asset.project_id))
        .where(
            Asset.workspace_id == access.workspace_id,
            Asset.project_id == project_id,
            Asset.kind == AssetKind.WAVEFORM,
            Asset.storage_key.endswith(f"/{WAVEFORM_V1_NAME}"),
        )
        .order_by(Asset.created_at.desc(), Asset.id.desc())
        .limit(1)
    ).first()
    if row is None or row.duration_ms is None:
        raise StudioNotFoundError(str(project_id))
    return WaveformView(
        version=1,
        peaks_per_second=WAVEFORM_PEAKS_PER_SECOND,
        duration_ms=row.duration_ms,
        download=store.sign_download(key=row.storage_key, expires_in=SIGNED_URL_TTL),
    )


def project_transcript(
    session: Session, *, access: WorkspaceAccess, project_id: UUID
) -> TranscriptView:
    """Read the newest canonical transcript of one active Project."""
    row = session.execute(
        select(Transcript.language, Transcript.duration_ms, Transcript.words)
        .join(Project, _active_project(Transcript.workspace_id, Transcript.project_id))
        .where(Transcript.workspace_id == access.workspace_id, Transcript.project_id == project_id)
        .order_by(Transcript.created_at.desc(), Transcript.id.desc())
        .limit(1)
    ).first()
    if row is None:
        raise StudioNotFoundError(str(project_id))
    return TranscriptView(
        language=row.language,
        duration_ms=row.duration_ms,
        words=tuple(
            TranscriptWordView(
                word_id=str(word["word_id"]),
                text=str(word["text"]),
                punctuation=str(word["punctuation"]),
                start_ms=int(word["start_ms"]),
                end_ms=int(word["end_ms"]),
                speaker=str(word["speaker"]),
            )
            for word in row.words
        ),
    )
```

- [ ] **Step 4: Implement the routes**

Append the response models and routes to `backend/src/clipah/api/routes/studio.py`:

```python
class StoryboardSheetResponse(BaseModel):
    """One signed sheet and the first moment it shows."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    index: int
    start_ms: int = Field(alias="startMs")
    tile_count: int = Field(alias="tileCount")
    url: str


class StoryboardResponse(BaseModel):
    """Sheet geometry and signed sheets for one Project."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: int
    interval_ms: int = Field(alias="intervalMs")
    tile_width: int = Field(alias="tileWidth")
    tile_height: int = Field(alias="tileHeight")
    columns: int
    rows: int
    duration_ms: int = Field(alias="durationMs")
    sheets: tuple[StoryboardSheetResponse, ...]
    expires_at: datetime = Field(alias="expiresAt")

    @field_serializer("expires_at")
    def serialize_expires_at(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return _timestamp(value)


class WaveformResponse(BaseModel):
    """A signed waveform and how many peaks make a second."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: int
    peaks_per_second: int = Field(alias="peaksPerSecond")
    duration_ms: int = Field(alias="durationMs")
    url: str
    expires_at: datetime = Field(alias="expiresAt")

    @field_serializer("expires_at")
    def serialize_expires_at(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return _timestamp(value)


class TranscriptWordResponse(BaseModel):
    """One transcribed word."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    text: str
    punctuation: str
    start_ms: int = Field(alias="startMs")
    end_ms: int = Field(alias="endMs")
    speaker: str


class TranscriptResponse(BaseModel):
    """One Project's transcript in spoken order."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    language: str
    duration_ms: int = Field(alias="durationMs")
    words: tuple[TranscriptWordResponse, ...]


@router.get("/projects/{project_id}/storyboard", response_model=StoryboardResponse)
def storyboard(
    project_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
    store: Annotated[ObjectStore, Depends(object_store_for)],
) -> StoryboardResponse:
    """Sign five minutes of access to every storyboard sheet of one Project."""
    try:
        view = project_storyboard(session, store, access=workspace.access, project_id=project_id)
    except StudioNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return StoryboardResponse(
        version=view.version,
        intervalMs=view.interval_ms,
        tileWidth=view.tile_width,
        tileHeight=view.tile_height,
        columns=view.columns,
        rows=view.rows,
        durationMs=view.duration_ms,
        sheets=tuple(
            StoryboardSheetResponse(
                index=sheet.index,
                startMs=sheet.start_ms,
                tileCount=sheet.tile_count,
                url=sheet.download.url,
            )
            for sheet in view.sheets
        ),
        expiresAt=view.expires_at,
    )


@router.get("/projects/{project_id}/waveform", response_model=WaveformResponse)
def waveform(
    project_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
    store: Annotated[ObjectStore, Depends(object_store_for)],
) -> WaveformResponse:
    """Sign five minutes of access to one Project's waveform peaks."""
    try:
        view = project_waveform(session, store, access=workspace.access, project_id=project_id)
    except StudioNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return WaveformResponse(
        version=view.version,
        peaksPerSecond=view.peaks_per_second,
        durationMs=view.duration_ms,
        url=view.download.url,
        expiresAt=view.download.expires_at,
    )


@router.get("/projects/{project_id}/transcript", response_model=TranscriptResponse)
def transcript(
    project_id: UUID, session: DatabaseSession, workspace: ReadableWorkspace
) -> TranscriptResponse:
    """Read one Project's transcript for review and the Project page."""
    try:
        view = project_transcript(session, access=workspace.access, project_id=project_id)
    except StudioNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return TranscriptResponse(
        language=view.language,
        durationMs=view.duration_ms,
        words=tuple(
            TranscriptWordResponse(
                id=word.word_id,
                text=word.text,
                punctuation=word.punctuation,
                startMs=word.start_ms,
                endMs=word.end_ms,
                speaker=word.speaker,
            )
            for word in view.words
        ),
    )
```

Before adding the routes, check no other router already serves `/projects/{project_id}/storyboard`, `/waveform`, or `/transcript`: `grep -rn '"/projects/{project_id}/\(storyboard\|waveform\|transcript\)"' backend/src/clipah/api/routes`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -q tests/integration/test_studio_browsing.py` → PASS.

---

### Task 8: `order=recent` for Home

**Files:**
- Modify: `backend/src/clipah/studio/use_cases.py` (`ClipOrder`, `browse_clips`)
- Modify: `backend/src/clipah/api/routes/studio.py` (`browse_clip_collection`)
- Modify: `backend/tests/integration/test_studio_browsing.py`

**Interfaces:**
- Produces: `ClipOrder(StrEnum)` with `CREATED = "created"`, `RECENT = "recent"`; `browse_clips(..., order: ClipOrder = ClipOrder.CREATED)`; query parameter `order` on `GET /api/v1/clips`.
- Produces: `ClipSummary.edit_updated_at: datetime | None` and `ClipSummaryResponse.editUpdatedAt: str | None` (the first Edit's `updated_at`, explicit UTC offset), so Home can say when a clip was last saved.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.integration
def test_recent_order_puts_the_most_recently_edited_clip_first_as_a_single_page(
    engine: Engine,
) -> None:
    """Home continues the work touched last, which is not the analysis order."""
    stage = _staged(engine)
    first = _candidate_id(stage)
    second = _second_candidate(stage)
    opened = stage.browser.request(
        "POST",
        _path(stage, f"/projects/{stage.project_id}/candidates/{second}/edits"),
        json=None,
    )
    assert opened.status_code == 201

    recent = stage.browser.get(_path(stage, "/clips?stage=edited&order=recent&limit=5")).json()
    created = stage.browser.get(_path(stage, "/clips?stage=edited&limit=5")).json()

    assert [clip["id"] for clip in recent["clips"]] == [str(second), str(first)]
    assert recent["nextCursor"] is None
    assert {clip["id"] for clip in created["clips"]} == {str(first), str(second)}
    assert all(clip["editUpdatedAt"] is not None for clip in recent["clips"])
    suggested = stage.browser.get(_path(stage, "/clips?stage=suggested")).json()
    assert all(clip["editUpdatedAt"] is None for clip in suggested["clips"])


@pytest.mark.integration
def test_recent_order_refuses_a_cursor(engine: Engine) -> None:
    """Recent is a top-N read; paging it would promise an order edits can reshuffle."""
    stage = _staged(engine)
    first = stage.browser.get(_path(stage, "/clips?limit=1")).json()

    refused = stage.browser.get(_path(stage, f"/clips?order=recent&cursor={first['nextCursor'] or 'x'}"))

    assert_error(refused, status_code=422, code="VALIDATION_ERROR")
```

The staged Edit is created at `NOW` by the harness clock; if the second Edit's `updated_at` equals the first's because the clock is frozen, advance the harness clock before opening the second Edit (the `Stage` exposes the browser's app; use the same `Clock` pattern the render tests use) so the ordering is observable.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest -q tests/integration/test_studio_browsing.py -k recent`
Expected: FAIL — the `order` parameter is ignored.

- [ ] **Step 3: Implement**

In `use_cases.py`:

```python
class ClipOrder(StrEnum):
    """How a browsed clip list is ordered."""

    CREATED = "created"
    RECENT = "recent"
```

Add `edit_updated_at: datetime | None` as the last field of `ClipSummary`, and a matching last parameter `edit_updated_at: datetime | None` to `_clip_summary` that it passes through. In `browse_clips`, add `order: ClipOrder = ClipOrder.CREATED`, define `edit_updated_at = _first_edit_column(ClipEdit.updated_at)` beside `edit_id`, add it as the sixth selected column in the existing query (and unpack it in the existing comprehension), and, before the `after` condition is appended, add:

```python
    if order is ClipOrder.RECENT:
        recent_rows = session.execute(
            select(ClipCandidate, Project.name, edit_id, current_revision, export_count, edit_updated_at)
            .join(Project, _active_project(ClipCandidate.workspace_id, ClipCandidate.project_id))
            .where(*conditions)
            .order_by(edit_updated_at.desc().nulls_last(), ClipCandidate.id)
            .limit(limit)
        ).all()
        return ClipPage(
            clips=tuple(
                _clip_summary(candidate, project_name, found_edit, revision, count, updated)
                for candidate, project_name, found_edit, revision, count, updated in recent_rows
            ),
            next_boundary=None,
        )
```

In `studio.py`, add `edit_updated_at: datetime | None = Field(alias="editUpdatedAt")` to `ClipSummaryResponse` with a serializer (`None` stays `None`, otherwise `_timestamp(value)`), and pass `editUpdatedAt=clip.edit_updated_at` in `_clip_body`.

In `studio.py` `browse_clip_collection`, add `order: ClipOrder = ClipOrder.CREATED` and:

```python
    if order is ClipOrder.RECENT and cursor is not None:
        raise ApiError(status_code=422, code="VALIDATION_ERROR")
```

then pass `order=order` to `browse_clips`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q tests/integration/test_studio_browsing.py` → PASS.

---

### Task 9: Backfill command

**Files:**
- Create: `backend/src/clipah/studio/backfill_preview_media.py`
- Create: `backend/tests/integration/test_backfill_preview_media.py`

**Interfaces:**
- Consumes: `JobDispatcher`, `CeleryJobDispatcher`, `RecordingJobDispatcher` (`clipah.source_imports.dispatch`), `create_job`, `admission_policy`, `ConcurrencyLimitError`, `DatabaseWorkspaceAuthorizer.require`, `WorkspaceAction.PROJECT_WRITE`.
- Produces: `BackfillOutcome(StrEnum)` (`admitted`, `would_admit`, `already_running`, `previously_failed`, `busy`); `BackfillResult(project_id: UUID, outcome: BackfillOutcome, job_id: UUID | None)`; `backfill_preview_media(*, settings: Settings, dispatcher: JobDispatcher, workspace_id: UUID, user_id: UUID, now: datetime, dry_run: bool = False, retry_failed: bool = False) -> tuple[BackfillResult, ...]`; `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/integration/test_backfill_preview_media.py`:

```python
"""Integration contracts for backfilling previews onto Projects ingested before they existed."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from clipah.models import Asset, AssetKind, AssetSourceType, Job, JobKind, JobStatus, Project, ProjectStatus, SourceKind
from clipah.source_imports.dispatch import RecordingJobDispatcher
from clipah.studio.backfill_preview_media import BackfillOutcome, backfill_preview_media, main
from clipah.workspaces.models import WorkspaceNotFoundError
from support import provision_identity, runtime_settings

NOW = datetime(2026, 9, 17, tzinfo=UTC)


@pytest.mark.integration
def test_backfill_admits_only_projects_with_a_proxy_and_no_storyboard(engine: Engine) -> None:
    """Projects still ingesting, or already previewed, are left alone."""
    user_id, workspace_id = provision_identity(engine, suffix=f"backfill-{uuid4().hex[:8]}")
    eligible = _project(engine, workspace_id, user_id, proxy=True)
    _project(engine, workspace_id, user_id, proxy=False)
    previewed = _project(engine, workspace_id, user_id, proxy=True, storyboard=True)
    dispatcher = RecordingJobDispatcher()

    results = backfill_preview_media(
        settings=runtime_settings(), dispatcher=dispatcher, workspace_id=workspace_id, user_id=user_id, now=NOW
    )

    assert [(result.project_id, result.outcome) for result in results] == [(eligible, BackfillOutcome.ADMITTED)]
    assert dispatcher.kinds == [JobKind.PREVIEW_MEDIA]
    assert previewed not in {result.project_id for result in results}


@pytest.mark.integration
def test_backfill_is_idempotent_and_a_dry_run_changes_nothing(engine: Engine) -> None:
    """Running the command twice, or rehearsing it, never buys a second Job."""
    user_id, workspace_id = provision_identity(engine, suffix=f"backfill-idem-{uuid4().hex[:8]}")
    project_id = _project(engine, workspace_id, user_id, proxy=True)

    rehearsal = backfill_preview_media(settings=runtime_settings(), dispatcher=RecordingJobDispatcher(), workspace_id=workspace_id, user_id=user_id, now=NOW, dry_run=True)
    first = backfill_preview_media(settings=runtime_settings(), dispatcher=RecordingJobDispatcher(), workspace_id=workspace_id, user_id=user_id, now=NOW)
    second = backfill_preview_media(settings=runtime_settings(), dispatcher=RecordingJobDispatcher(), workspace_id=workspace_id, user_id=user_id, now=NOW)

    assert [result.outcome for result in rehearsal] == [BackfillOutcome.WOULD_ADMIT]
    assert [result.outcome for result in first] == [BackfillOutcome.ADMITTED]
    assert [result.outcome for result in second] == [BackfillOutcome.ALREADY_RUNNING]
    assert _preview_jobs(engine, project_id) == 1


@pytest.mark.integration
def test_a_failed_preview_is_retried_only_when_asked(engine: Engine) -> None:
    """An automatic retry of a broken source would fail the same way forever."""
    user_id, workspace_id = provision_identity(engine, suffix=f"backfill-fail-{uuid4().hex[:8]}")
    project_id = _project(engine, workspace_id, user_id, proxy=True)
    backfill_preview_media(settings=runtime_settings(), dispatcher=RecordingJobDispatcher(), workspace_id=workspace_id, user_id=user_id, now=NOW)
    with engine.begin() as connection:
        connection.execute(
            Job.__table__.update().where(Job.project_id == project_id).values(status=JobStatus.FAILED, error_code="PREVIEW_MEDIA_INTEGRITY")
        )

    skipped = backfill_preview_media(settings=runtime_settings(), dispatcher=RecordingJobDispatcher(), workspace_id=workspace_id, user_id=user_id, now=NOW)
    retried = backfill_preview_media(settings=runtime_settings(), dispatcher=RecordingJobDispatcher(), workspace_id=workspace_id, user_id=user_id, now=NOW, retry_failed=True)

    assert [result.outcome for result in skipped] == [BackfillOutcome.PREVIOUSLY_FAILED]
    assert [result.outcome for result in retried] == [BackfillOutcome.ADMITTED]
    assert _preview_jobs(engine, project_id) == 2


@pytest.mark.integration
def test_a_full_workspace_reports_busy_instead_of_failing(engine: Engine) -> None:
    """The operator sees which Projects to run again later."""
    user_id, workspace_id = provision_identity(engine, suffix=f"backfill-busy-{uuid4().hex[:8]}")
    _project(engine, workspace_id, user_id, proxy=True)

    results = backfill_preview_media(
        settings=runtime_settings(concurrent_jobs_per_workspace=0),
        dispatcher=RecordingJobDispatcher(),
        workspace_id=workspace_id,
        user_id=user_id,
        now=NOW,
    )

    assert [result.outcome for result in results] == [BackfillOutcome.BUSY]


@pytest.mark.integration
def test_backfill_refuses_a_user_who_is_not_a_member(engine: Engine) -> None:
    """The command never reaches into a Workspace the named User has no standing in."""
    _, workspace_id = provision_identity(engine, suffix=f"backfill-owner-{uuid4().hex[:8]}")
    outsider, _ = provision_identity(engine, suffix=f"backfill-outsider-{uuid4().hex[:8]}")

    with pytest.raises(WorkspaceNotFoundError):
        backfill_preview_media(settings=runtime_settings(), dispatcher=RecordingJobDispatcher(), workspace_id=workspace_id, user_id=outsider, now=NOW)


@pytest.mark.unit
def test_the_command_requires_both_identifiers() -> None:
    """A backfill with no named tenant is refused before any settings are read."""
    with pytest.raises(SystemExit):
        main(["--dry-run"])


def _project(engine: Engine, workspace_id: UUID, user_id: UUID, *, proxy: bool, storyboard: bool = False) -> UUID:
    project_id, source_id = uuid4(), uuid4()
    prefix = f"workspaces/{workspace_id}/projects/{project_id}"
    with engine.begin() as connection:
        connection.execute(
            Project.__table__.insert().values(
                id=project_id, workspace_id=workspace_id, created_by_user_id=user_id, name="Backfill",
                status=ProjectStatus.READY, source_kind=SourceKind.UPLOAD, created_at=NOW, updated_at=NOW,
            )
        )
        rows = [(source_id, AssetKind.SOURCE, f"{prefix}/source/{source_id}")]
        if proxy:
            rows.append((uuid5(source_id, "proxy"), AssetKind.PROXY, f"{prefix}/derived/{source_id}/proxy"))
        if storyboard:
            rows.append((uuid4(), AssetKind.STORYBOARD, f"{prefix}/derived/{source_id}/storyboard-v1/sheet-0000.jpg"))
        for asset_id, kind, key in rows:
            connection.execute(
                Asset.__table__.insert().values(
                    id=asset_id, workspace_id=workspace_id, project_id=project_id, kind=kind,
                    source_type=AssetSourceType.DERIVED if kind is not AssetKind.SOURCE else AssetSourceType.USER_UPLOAD,
                    storage_key=key, content_type="video/mp4", size_bytes=10, sha256=b"b" * 32,
                )
            )
    return project_id


def _preview_jobs(engine: Engine, project_id: UUID) -> int:
    with Session(engine) as session:
        return len(session.scalars(select(Job.id).where(Job.project_id == project_id, Job.kind == JobKind.PREVIEW_MEDIA)).all())
```

Run `uv run ruff format` on the file. If `concurrent_jobs_per_workspace=0` is rejected by settings validation, use `1` and pre-create one queued Job in the Workspace instead. `WorkspaceNotFoundError` is defined in `clipah.workspaces.models`.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest -q tests/integration/test_backfill_preview_media.py`
Expected: FAIL — `ModuleNotFoundError: clipah.studio.backfill_preview_media`.

- [ ] **Step 3: Implement `backend/src/clipah/studio/backfill_preview_media.py`**

```python
"""Give Projects ingested before previews existed their storyboard and waveform.

The command works inside one named Workspace as one named member, through ordinary Job
admission, so it can never reach another tenant and never skips a limit the product enforces.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select

from clipah.config import Settings
from clipah.db import session_scope
from clipah.jobs.admission import ConcurrencyLimitError, admission_policy
from clipah.jobs.use_cases import create_job
from clipah.models import Asset, AssetKind, Job, JobKind, JobStatus, Project
from clipah.source_imports.dispatch import JobDispatcher
from clipah.workspaces.authorization import DatabaseWorkspaceAuthorizer
from clipah.workspaces.models import WorkspaceAction

_UNFINISHED = (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYING, JobStatus.CANCEL_REQUESTED)


class BackfillOutcome(StrEnum):
    """What the command did, or would do, for one Project."""

    ADMITTED = "admitted"
    WOULD_ADMIT = "would_admit"
    ALREADY_RUNNING = "already_running"
    PREVIOUSLY_FAILED = "previously_failed"
    BUSY = "busy"


@dataclass(frozen=True, slots=True)
class BackfillResult:
    """One Project's outcome."""

    project_id: UUID
    outcome: BackfillOutcome
    job_id: UUID | None


def backfill_preview_media(
    *,
    settings: Settings,
    dispatcher: JobDispatcher,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
    dry_run: bool = False,
    retry_failed: bool = False,
) -> tuple[BackfillResult, ...]:
    """Admit preview work for every active Project with a proxy and no storyboard."""
    with session_scope(settings=settings, workspace_id=workspace_id, user_id=user_id) as session:
        DatabaseWorkspaceAuthorizer(session).require(
            user_id=user_id, workspace_id=workspace_id, action=WorkspaceAction.PROJECT_WRITE
        )
        proxied = select(Asset.project_id).where(
            Asset.workspace_id == workspace_id, Asset.kind == AssetKind.PROXY
        )
        previewed = select(Asset.project_id).where(
            Asset.workspace_id == workspace_id, Asset.kind == AssetKind.STORYBOARD
        )
        project_ids = session.scalars(
            select(Project.id)
            .where(
                Project.workspace_id == workspace_id,
                Project.archived_at.is_(None),
                Project.id.in_(proxied),
                Project.id.not_in(previewed),
            )
            .order_by(Project.created_at, Project.id)
        ).all()

    return tuple(
        _backfill_one(
            settings=settings,
            dispatcher=dispatcher,
            workspace_id=workspace_id,
            user_id=user_id,
            project_id=project_id,
            now=now,
            dry_run=dry_run,
            retry_failed=retry_failed,
        )
        for project_id in project_ids
    )


def _backfill_one(
    *,
    settings: Settings,
    dispatcher: JobDispatcher,
    workspace_id: UUID,
    user_id: UUID,
    project_id: UUID,
    now: datetime,
    dry_run: bool,
    retry_failed: bool,
) -> BackfillResult:
    """Decide and admit one Project in its own transaction, so one refusal stops nothing else."""
    with session_scope(settings=settings, workspace_id=workspace_id, user_id=user_id) as session:
        latest = session.execute(
            select(Job.id, Job.status)
            .where(
                Job.workspace_id == workspace_id,
                Job.project_id == project_id,
                Job.kind == JobKind.PREVIEW_MEDIA,
            )
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
        ).first()
        key = f"backfill:{project_id}:preview-media-v1"
        if latest is not None:
            if latest.status in _UNFINISHED:
                return BackfillResult(project_id, BackfillOutcome.ALREADY_RUNNING, latest.id)
            if not retry_failed:
                return BackfillResult(project_id, BackfillOutcome.PREVIOUSLY_FAILED, latest.id)
            key = f"{key}:after:{latest.id}"
        if dry_run:
            return BackfillResult(project_id, BackfillOutcome.WOULD_ADMIT, None)
        access = DatabaseWorkspaceAuthorizer(session).require(
            user_id=user_id, workspace_id=workspace_id, action=WorkspaceAction.PROJECT_WRITE
        )
        try:
            snapshot = create_job(
                session,
                policy=admission_policy(settings),
                access=access,
                project_id=project_id,
                kind=JobKind.PREVIEW_MEDIA,
                idempotency_key=key,
                now=now,
            )
        except ConcurrencyLimitError:
            return BackfillResult(project_id, BackfillOutcome.BUSY, None)
    try:
        dispatcher.dispatch(
            job_id=snapshot.job_id, workspace_id=workspace_id, user_id=user_id, kind=JobKind.PREVIEW_MEDIA
        )
    except Exception:  # noqa: BLE001 - the committed Job is the record; a wakeup is best-effort.
        pass
    return BackfillResult(project_id, BackfillOutcome.ADMITTED, snapshot.job_id)


def main(argv: list[str] | None = None) -> int:
    """Run the backfill for one Workspace and print one line per Project."""
    parser = argparse.ArgumentParser(description="Backfill storyboard and waveform previews.")
    parser.add_argument("--workspace-id", type=UUID, required=True)
    parser.add_argument("--user-id", type=UUID, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    arguments = parser.parse_args(argv)

    from clipah.celery_app import configure_celery
    from clipah.jobs.tasks import celery_app
    from clipah.source_imports.dispatch import CeleryJobDispatcher

    settings = Settings()
    configure_celery(celery_app, settings)
    for result in backfill_preview_media(
        settings=settings,
        dispatcher=CeleryJobDispatcher(),
        workspace_id=arguments.workspace_id,
        user_id=arguments.user_id,
        now=datetime.now(tz=UTC),
        dry_run=arguments.dry_run,
        retry_failed=arguments.retry_failed,
    ):
        print(f"{result.project_id} {result.outcome.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

The existing `create_job` returns the first submission's Job for a repeated key; after a first admission, the second run finds that Job unfinished and reports `already_running` before reaching `create_job`, which is what the idempotency test asserts. If Ruff rejects the bare `except Exception` pattern despite the `noqa`, use `contextlib.suppress(Exception)` around the dispatch, matching `jobs/tasks.py::_dispatch_next`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q tests/integration/test_backfill_preview_media.py` → PASS.

---

### Task 10: Retention proof and the read limit

**Files:**
- Modify: `backend/tests/integration/test_retention.py`
- Modify: `backend/src/clipah/config.py`
- Modify: `backend/tests/unit/test_config.py`, `backend/tests/integration/test_limits.py`
- Modify: `ENVIRONMENT_SETUP.md`

- [ ] **Step 1: Write the failing expectations**

In `test_retention.py` `test_discharging_a_project_tombstone_removes_its_media_and_its_rows`, extend the store keys:

```python
    store = _store(
        (
            f"{prefix}source/{uuid4()}",
            f"{prefix}renders/{uuid4()}",
            f"{prefix}derived/{uuid4()}/storyboard-v1/sheet-0000.jpg",
            f"{prefix}derived/{uuid4()}/waveform-v1.bin",
        )
    )
```

(`assert store.objects == {}` already proves every one is purged.)

In `tests/unit/test_config.py` change `assert settings.read_requests_per_minute == 60` to `== 300`. In `tests/integration/test_limits.py` `test_read_requests_are_limited_per_user_and_state_their_retry_delay`, change `60` to `300`, `range(61)` to `range(301)`, `responses[:60]` to `responses[:300]`, `[200] * 60` to `[200] * 300`, and `responses[60]` to `responses[300]`.

- [ ] **Step 2: Run them and observe the result**

Run: `uv run pytest -q tests/integration/test_retention.py -k discharging tests/unit/test_config.py -k plan_limits tests/integration/test_limits.py -k read_requests`
Expected: the retention test PASSES immediately (the preview keys sit under the Project prefix — record this as proof, not new behaviour); the two limit tests FAIL on `60 != 300`.

- [ ] **Step 3: Raise the limit**

In `backend/src/clipah/config.py`: `read_requests_per_minute: int = 300`.
In `ENVIRONMENT_SETUP.md`, change the `CLIPAH_READ_REQUESTS_PER_MINUTE` row's default to `300` and its note to `Per User. A dashboard page load issues several reads.`

- [ ] **Step 4: Run the tests**

Run the Step 2 command → PASS. If the 301-request loop exceeds the 15-second per-test timeout on this machine, mark only that test `@pytest.mark.slow` and report it; do not lower the limit.

---

### Task 11: Contracts, frontend followers, gates, and handover

**Files:**
- Modify: `contracts/openapi.json`, `frontend/lib/api/generated/**` (generated)
- Modify: `frontend/features/uploads/UploadPanel.tsx`, `frontend/features/jobs/job-center.tsx`
- Modify: `frontend/tests/uploads.test.tsx`, `frontend/tests/jobs.test.tsx`
- Modify: `PROGRESS.md`

**Interfaces:**
- Produces generated client functions (names as orval emits them; confirm after generation): `storyboardApiV1ProjectsProjectIdStoryboardGet`, `waveformApiV1ProjectsProjectIdWaveformGet`, `transcriptApiV1ProjectsProjectIdTranscriptGet` in `lib/api/generated/studio/studio.ts`, and model types `StoryboardResponse`, `StoryboardSheetResponse`, `WaveformResponse`, `TranscriptResponse`, `TranscriptWordResponse`, `ClipOrder`. Plans 3–5 consume these names.

- [ ] **Step 1: Regenerate contracts**

Run from the repository root:

```bash
scripts/export-openapi.sh
pnpm --dir frontend generate:api
scripts/check-contracts-clean.sh
grep -n "export const .*StoryboardGet\|export const .*WaveformGet\|export const .*TranscriptGet" frontend/lib/api/generated/studio/studio.ts
```

Expected: the check reports contracts up to date and the grep prints three function names. Write the exact names into the task notes; Plans 3–5 use them.

`ClipSummaryResponse` now requires `editUpdatedAt`. Add `editUpdatedAt: null,` to the `clip()` fixture in `frontend/tests/creator-studio.test.tsx` (the only fixture that builds one) so `pnpm typecheck` stays green.

- [ ] **Step 2: Write the failing frontend tests**

In `frontend/tests/uploads.test.tsx`, beside "names each stage the work is really in", add:

```tsx
  test('keeps showing the pipeline stage while previews are prepared beside it', async () => {
    const user = userEvent.setup()
    signedInApi()

    renderPanel()
    await user.upload(await screen.findByLabelText(/video file/i), realFile())
    const stream = await openStream()

    act(() => stream.emit('progress', jobEvent({ kind: 'transcribe' })))
    act(() =>
      stream.emit(
        'started',
        jobEvent({ jobId: '99999999-9999-4999-8999-999999999999', kind: 'preview_media' }),
      ),
    )

    expect(await screen.findByRole('status')).toHaveTextContent('Transcribing audio')
    expect(screen.getByText(/transcribe \(in progress\)/i)).toBeInTheDocument()
  })
```

In `frontend/tests/jobs.test.tsx`, beside "announces running work", add:

```tsx
  test('names preview work in plain words', async () => {
    signedInApi()

    renderWithApi(
      <WorkspaceProvider>
        <JobCenter />
      </WorkspaceProvider>,
    )

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    act(() =>
      FakeEventSource.instances[0]?.emit(
        'succeeded',
        jobEvent({ kind: 'preview_media', status: 'succeeded', stage: 'preview_media' }),
      ),
    )

    expect(await screen.findByText('Preparing previews')).toBeInTheDocument()
  })
```

If Plan 1's shared-component work renamed the stage pill's screen-reader suffix, match the current accessible text in the first test instead of `transcribe (in progress)`.

- [ ] **Step 3: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/uploads.test.tsx tests/jobs.test.tsx`
Expected: FAIL — the panel shows `preview_media`, and the center shows the raw kind.

- [ ] **Step 4: Implement**

In `features/uploads/UploadPanel.tsx`, add below `KIND_LABELS`:

```ts
/** Work that decorates a Project without being a stage the member is waiting on. */
const BACKGROUND_KINDS = new Set(['preview_media'])
```

and in `readJob`, after the Project check:

```ts
  if (typeof fields['kind'] === 'string' && BACKGROUND_KINDS.has(fields['kind'])) {
    return null
  }
```

In `features/jobs/job-center.tsx`, add `preview_media: 'Preparing previews',` to `JOB_KIND_LABELS`.

- [ ] **Step 5: Run the frontend tests**

Run: `pnpm --dir frontend exec vitest run tests/uploads.test.tsx tests/jobs.test.tsx` → PASS.

- [ ] **Step 6: Run every gate**

From `backend/` (disposable database):

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q --cov=clipah --cov-fail-under=90
```

From the repository root:

```bash
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

Expected: all PASS; record counts and coverage.

- [ ] **Step 7: Prove it live once**

Rebuild the worker and API images (`docker compose -f infra/compose.yaml up -d --build api worker-ingest-ai`), apply migrations to the application database (`cd backend && uv run alembic upgrade head` with the application's migration URL), then run a dry-run backfill for the owner's own Workspace:

```bash
docker compose -f infra/compose.yaml exec api python -m clipah.studio.backfill_preview_media --workspace-id <workspace uuid> --user-id <user uuid> --dry-run
```

Report the printed lines to the owner and ask before running without `--dry-run`. After an approved run, check one Project: `GET /api/v1/projects/<id>/storyboard?workspace_id=<ws>` returns sheets and opening one sheet URL shows a 1600×900 grid.

- [ ] **Step 8: Record progress**

Append a "Signal Studio redesign — Plan 2, preview media" entry to `PROGRESS.md`: migration `0023`, the job, geometry, reads, `order=recent`, backfill, retention proof, the 300 read limit, contract regeneration, gate output, live proof or why it was not run, and the owner commit message `feat: derive storyboard and waveform previews`. Do not run `git commit`.
