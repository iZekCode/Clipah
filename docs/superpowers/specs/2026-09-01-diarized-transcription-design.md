# Diarized Transcription Design

## Scope

Task 12 turns the deterministic transcription-audio Asset produced by Task 11 into one durable,
provider-neutral Transcript for the source Asset. The Transcript preserves authoritative word
timestamps, confidence, punctuation, provider speaker labels, utterances, selected language and
model metadata, plus one private raw provider-result document for reproducibility.

Transcript windowing, candidate extraction, deduplication, reranking, and analysis endpoints belong
to Tasks 13 through 16 and are not part of this change. Speaker labels remain provider-assigned
opaque labels; Clipah does not infer a speaker's identity with an LLM.

## Architecture

The transcription subsystem has four boundaries:

- `transcripts/models.py` defines immutable provider-neutral words, utterances, speaker segments,
  and `TranscriptResult`. It owns transcript invariants but imports no AssemblyAI types.
- `transcripts/provider.py` defines `Transcriber.transcribe(...) -> TranscriptResult`, normalized
  transcription errors, and a deterministic fake used by unit and integration tests.
- `transcripts/assemblyai_adapter.py` contains the locked AssemblyAI 1.x SDK. It maps a private
  signed audio capability and requested language into provider configuration, then converts SDK
  results into provider-neutral values without exposing provider payload types.
- `transcripts/use_cases.py` validates and normalizes a `TranscriptResult` into the canonical JSON
  structures persisted by Clipah. It assigns deterministic word IDs and creates punctuation-aware
  full text, utterances, and contiguous speaker segments.
- `jobs/transcribe_task.py` adapts the provider-neutral subsystem to durable Jobs. It resolves the
  source and transcription-audio Assets under the worker's Workspace context, checks for an existing
  valid Transcript, calls the provider outside database transactions, writes the raw result to
  private object storage, and persists the Transcript atomically.

The existing `JobKind.TRANSCRIBE` runner registry entry is used for the transcription stage and
continues to route through the `ai` Celery queue. Later pipeline tasks use their own durable Job
kinds without changing the provider port.

## Language and Model Routing

AssemblyAI pre-recorded transcription runs with speaker labels and word timestamps. Word timestamps
and confidence are read from the provider's normal word response; no second transcription or
diarization request is made.

Routing is explicit:

- Requested Indonesian (`id`) uses only `universal-2`.
- Requested English, Spanish, German, French, Portuguese, or Italian uses only
  `universal-3-pro`.
- Other requested languages use only `universal-2`.
- When no language is requested, the adapter enables language detection and supplies the ordered
  `universal-3-pro`, `universal-2` model list so the provider can select U3 only for a supported
  language and fall back to U2 otherwise.

The Transcript records the detected/requested language, the model reported as actually used, the
provider name, and the locked SDK version. Configuration uses `speech_models`; the retired singular
legacy `SpeechModel.universal` interface is not used.

## Canonical Transcript Rules

Provider words are normalized in their original order. Each word receives a deterministic,
one-based ID such as `w000001`. Every word retains its text, start and end milliseconds, confidence,
punctuation, and opaque provider speaker label.

Validation rejects a result when:

- it contains no usable words or the provider returns an empty audio result;
- a timestamp is negative, an end precedes its start, or word start times regress;
- a word ends after the source Asset duration;
- required text, confidence, or speaker data is malformed.

Overlapping adjacent words are retained when their starts do not regress, because diarized provider
output can legitimately overlap at speaker changes. Missing punctuation is represented explicitly
and does not manufacture sentence-ending punctuation. Full text is assembled deterministically,
attaching provider punctuation without introducing language-specific guesses.

Utterances retain their provider speaker, timestamps, text, and ordered word IDs. Speaker segments
are derived as maximal contiguous runs of normalized words with the same speaker, so persisted
segments and words cannot disagree. Provider utterances whose word membership or bounds conflict
with normalized words are rejected.

## Persistence and Idempotency

The Transcript row remains Workspace- and Project-scoped and references the source Asset, not the
derived transcription-audio Asset. A migration adds a unique `(workspace_id, asset_id)` constraint,
ensuring one canonical Transcript per source Asset under both application logic and concurrent
worker execution.

The worker derives the transcription-audio Asset ID from the source Asset and verifies that it is a
complete `TRANSCRIPTION_AUDIO` derivative in the same Workspace and Project. Before any provider
call, it loads the existing Transcript. A structurally valid row with matching source duration is a
successful idempotent result and prevents another provider request. A conflicting or malformed
existing row is an integrity failure rather than permission to overwrite evidence.

The raw provider result is serialized as bounded JSON and uploaded under a deterministic
Workspace/Project/source-Asset key. Its stored length and SHA-256 are checked before the Transcript
row is inserted. The normalized Postgres JSONB columns remain the authoritative input to later
analysis; the raw object exists for reproducibility and provider debugging, not routine reads.

If concurrent workers both pass the initial read, the database uniqueness constraint makes them
converge on one row. The loser re-reads and accepts the compatible row. It never creates a second
Transcript. Deterministic object keys make repeated identical raw uploads converge on the same
private object identity.

## Errors, Retries, and Cancellation

Provider transport failures, timeouts, temporary provider statuses, and object-storage transport
failures become sanitized retryable Job errors. Unsupported or invalid requested languages,
provider terminal failures, empty results, malformed words, regressing timestamps, out-of-duration
words, incompatible persisted data, and raw-result integrity failures become stable terminal Job
errors. Provider exception text, request payloads, signed URLs, and API keys never enter durable Job
events or client-visible errors.

Cancellation is checked before resolving audio, before the provider call, after it returns, before
object upload, and before persistence. External provider work and object upload run without an open
database transaction. Database reads and the final insert each use short tenant-scoped worker
transactions.

## Testing

Unit normalization tests begin red and cover English, Indonesian, speaker changes, missing
punctuation, overlapping words, invalid timestamp order, words beyond source duration, empty audio,
and malformed utterance membership. Adapter tests prove the exact U2/U3 routing matrix, speaker
labels, SDK-result conversion, terminal provider status, and retryable provider failures without
network access.

Integration tests use a deterministic fake Transcriber and the real Postgres/object-store
boundaries to prove persistence, the unique source-Asset constraint, RLS tenant isolation, raw JSON
metadata, cancellation, retry classification, and idempotent repeated Job execution without a
second provider call. An environment-gated slow smoke test exercises the configured AssemblyAI
account and remains excluded from normal CI.

Final verification runs Ruff check, Ruff format check, strict mypy, and the full pytest suite with
at least 90% coverage. The agent does not commit; handoff includes the required commit message
`feat: persist diarized word transcripts` for the repository owner.
