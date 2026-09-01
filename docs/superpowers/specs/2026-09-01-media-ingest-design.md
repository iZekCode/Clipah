# Durable Media Ingest Design

## Scope

Task 11 turns one private source Asset into validated metadata and three deterministic derived
Assets: a playback proxy, a JPEG thumbnail, and mono 16 kHz transcription audio. It registers the
existing `JobKind.INGEST` worker path and makes retrying the same Job reuse compatible persisted
artifacts instead of creating duplicates.

Waveform data, scene boundaries, transcription, API analysis routes, and render compilation belong
to later tasks and are not part of this change.

## Architecture

The ingest subsystem has four boundaries:

- `assets/probe.py` converts libmagic and ffprobe output into trusted source metadata. It owns all
  first-release media limits and stable sanitized validation errors.
- `assets/ffmpeg.py` invokes ffprobe and FFmpeg with argument arrays. It owns timeouts,
  process-group cancellation, bounded diagnostic capture, progress parsing, and derivative file
  generation.
- `assets/ingest.py` coordinates one isolated filesystem run. It downloads the exact private source
  while hashing and enforcing the byte limit, compares declared and sniffed MIME types, validates
  probe results, generates derivatives, uploads them to deterministic keys, and returns an
  `IngestResult` containing source metadata plus proxy, thumbnail, and transcription-audio results.
- `jobs/ingest_task.py` adapts the pure ingest boundary to the durable Job system. It resolves the
  source Asset under the worker's Workspace context, checks cancellation between external stages,
  persists compatible Asset rows in one transaction, and refuses immutable-metadata conflicts.

The design reuses `ObjectStore`, `job_workspace`, `JobContext`, and the existing stage-runner
registry. Provider payloads, signed URLs, commands, filesystem paths, and raw stderr never enter
durable Job metadata or public errors.

## Data Flow

1. The INGEST worker loads and detaches the source Asset in a short tenant-scoped transaction.
2. It creates a random, Job-scoped `0700` workspace.
3. The ingestor obtains a five-minute private download and streams the source to a fixed local
   filename, calculating SHA-256 and stopping above 2 GiB.
4. Libmagic identifies the bytes. A material mismatch with the source Asset's declared MIME is
   rejected before ffprobe.
5. ffprobe JSON is parsed into typed metadata and validated: duration no greater than four hours,
   exactly one video stream, at least one audio stream, dimensions no greater than 4K, and only
   H.264/H.265/VP8/VP9/AV1 video plus AAC/Opus/MP3 audio.
6. FFmpeg creates a max-720p proxy without upscaling, a JPEG thumbnail, and mono 16 kHz audio. Each
   invocation is cancellable and reports normalized progress.
7. Completed files are uploaded under deterministic Workspace/Project/source-Asset derivative
   keys. Upload metadata is verified against local size and digest.
8. The worker locks existing derived Asset identities, verifies immutable metadata when present,
   inserts missing rows together, and completes the durable Job through the existing job runner.

No partially generated local file is uploaded. A retry may overwrite the same deterministic object
with identical bytes before database reconciliation, but it may never silently accept conflicting
persisted metadata.

## Errors and Cancellation

Validation failures use stable internal exceptions carrying exact public-safe codes for size,
MIME, corrupt/unprobeable media, duration, stream count, missing audio, resolution, and codec
violations. Process timeout, cancellation, and derivative failure remain distinct. Raw exception
text and bounded stderr are diagnostic inputs only and are not included in client or durable Job
messages.

Subprocesses start in their own process group. Timeout or cancellation terminates the group, waits
briefly, then kills the group if necessary. Cancellation is checked before download, after probe,
and between derivative stages, matching the existing `JobContext` contract.

## Determinism and Retry Safety

Fixture generation pins codecs, dimensions, duration, frame-rate behavior, and metadata so checked-
in fixture bytes can be regenerated deliberately. Production commands explicitly select streams,
codecs, pixel/sample formats, metadata behavior, and output paths.

Derived Asset IDs and object keys are functions of the source Asset and derivative kind. Retrying
the same Job therefore converges on the same identities. Existing rows are reused only when every
immutable field matches the newly observed result.

## Testing

Unit tests begin red against the desired probe and process interfaces. Generated fixtures cover
valid landscape and portrait MP4, no-audio video, audio-only input, corrupt bytes, oversized
metadata, unsupported codec, and variable-frame-rate video. Limit tests assert the exact code for
every Section 7 rejection.

Integration tests exercise the complete ingest path with the fake object store and real
FFmpeg/ffprobe executables, verify persisted tenant-scoped Asset metadata, inspect generated
artifacts with ffprobe, prove idempotent retry, and prove cancellation and failure leave no
inconsistent Asset set. Final verification runs Ruff check, Ruff format check, strict mypy, and the
full pytest suite with at least 90% coverage.
