# Diarized Transcription Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist one retry-safe, diarized, word-timestamp Transcript for every ingested source
Asset using AssemblyAI's locked 1.x SDK.

**Architecture:** Provider-specific SDK values terminate inside `assemblyai_adapter.py`; immutable
provider-neutral values cross the `Transcriber` port. The durable TRANSCRIBE runner uses short
Workspace-scoped transactions around a single external provider call, stores one deterministic raw
JSON object, and converges concurrent retries through a database unique constraint.

**Tech Stack:** Python 3.13, frozen dataclasses, AssemblyAI SDK 1.0.0, SQLAlchemy 2, Alembic,
Postgres RLS, Celery stage runners, S3-compatible object storage, pytest, Ruff, strict mypy.

**Spec:** `docs/superpowers/specs/2026-09-01-diarized-transcription-design.md`

## Global Constraints

- Follow `AGENTS.md`; never commit, push, rebase, or otherwise write Git history.
- Use the canonical terms in `CONTEXT.md`: User, Workspace, Project, Asset, Job, Transcript.
- Run all commands from `backend/` through `uv`; never invoke bare `python` or `pip`.
- Write every behavioral test first and observe the expected failure before production code.
- Provider SDK types stay inside `transcripts/assemblyai_adapter.py`.
- Indonesian (`id`) uses `universal-2`; only `en`, `es`, `de`, `fr`, `pt`, and `it` may use
  `universal-3-pro`.
- Never infer speaker identity; persist only the provider's opaque speaker labels.
- Every tenant read/write uses the live Workspace context and the existing worker runtime role.
- Do not implement windowing, extraction, ranking, or analysis endpoints from Tasks 13 through 16.
- Every module, class, and function has an intent-focused docstring.

---

## File Map

- Create `backend/src/clipah/transcripts/__init__.py`: transcript package marker.
- Create `backend/src/clipah/transcripts/models.py`: immutable normalized and raw transcript values.
- Create `backend/src/clipah/transcripts/provider.py`: provider port, normalized provider errors, fake.
- Create `backend/src/clipah/transcripts/use_cases.py`: normalization, validation, JSON serialization,
  persisted-row validation, and deterministic raw-object keys.
- Create `backend/src/clipah/transcripts/assemblyai_adapter.py`: SDK configuration and result mapping.
- Create `backend/src/clipah/jobs/transcribe_task.py`: durable Job runner and production composition.
- Create `backend/migrations/versions/0007_unique_transcript_asset.py`: one Transcript per source Asset.
- Modify `backend/src/clipah/models.py`: mirror the migration's ORM unique constraint.
- Modify `backend/src/clipah/jobs/tasks.py`: register `JobKind.TRANSCRIBE`.
- Modify `backend/pyproject.toml`: isolate any missing AssemblyAI type information behind the adapter.
- Create `backend/tests/unit/test_transcript_normalization.py`: normalization contract.
- Create `backend/tests/unit/test_assemblyai_adapter.py`: SDK routing and failure translation.
- Create `backend/tests/integration/test_transcription.py`: persistence, RLS, retries, idempotency, smoke.
- Modify `PROGRESS.md`: mark Task 11 landed at `b434f8d`, describe Task 12, and record verification.

---

### Task 1: Provider-neutral transcript normalization

**Files:**
- Create: `backend/src/clipah/transcripts/__init__.py`
- Create: `backend/src/clipah/transcripts/models.py`
- Create: `backend/src/clipah/transcripts/use_cases.py`
- Test: `backend/tests/unit/test_transcript_normalization.py`

**Interfaces:**
- Consumes: raw provider-neutral `RawWord` and `RawUtterance` values.
- Produces: `normalize_transcript(provider, provider_version, model, language, duration_ms, words,
  utterances, raw_result) -> TranscriptResult` with deterministic word IDs,
  millisecond timestamps, confidence, punctuation, opaque speaker labels, utterances, and speaker
  segments.
- Produces: `transcript_document(result) -> dict[str, JsonValue]` and
  `raw_transcript_key(workspace_id, project_id, asset_id) -> str`.

- [ ] **Step 1: Add the English and Indonesian RED tests**

  Create two tests with explicit raw words and assert the complete public values:

  ```python
  def test_normalizes_english_words_with_deterministic_ids_and_punctuation() -> None:
      result = normalize_transcript(
          provider="assemblyai",
          provider_version="1.0.0",
          model="universal-3-pro",
          language="en",
          duration_ms=2_000,
          words=(
              RawWord("Hello,", 0, 400, 0.99, "A"),
              RawWord("world!", 450, 900, 0.97, "A"),
          ),
          utterances=(RawUtterance("Hello, world!", 0, 900, "A"),),
          raw_result={"id": "provider-id"},
      )
      assert [(word.word_id, word.text, word.punctuation) for word in result.words] == [
          ("w000001", "Hello", ","),
          ("w000002", "world", "!"),
      ]
      assert result.full_text == "Hello, world!"


  def test_normalizes_indonesian_without_changing_provider_text() -> None:
      result = normalize_transcript(
          provider="assemblyai",
          provider_version="1.0.0",
          model="universal-2",
          language="id",
          duration_ms=2_000,
          words=(RawWord("Apa", 0, 300, 0.96, "A"), RawWord("kabar?", 350, 700, 0.95, "A")),
          utterances=(RawUtterance("Apa kabar?", 0, 700, "A"),),
          raw_result={"id": "provider-id"},
      )
      assert result.language == "id"
      assert result.model == "universal-2"
      assert result.full_text == "Apa kabar?"
  ```

- [ ] **Step 2: Run the two focused tests and verify RED**

  Run:
  `UV_CACHE_DIR=/tmp/clipah-task12-uv-cache uv run --no-sync pytest tests/unit/test_transcript_normalization.py -v`

  Expected: collection fails because `clipah.transcripts` does not exist.

- [ ] **Step 3: Add the minimal immutable models and normalizer**

  Define frozen, slotted dataclasses with these exact fields:

  ```python
  JsonScalar = str | int | float | bool | None
  JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

  @dataclass(frozen=True, slots=True)
  class RawWord:
      text: str
      start_ms: int
      end_ms: int
      confidence: float
      speaker: str

  @dataclass(frozen=True, slots=True)
  class RawUtterance:
      text: str
      start_ms: int
      end_ms: int
      speaker: str

  @dataclass(frozen=True, slots=True)
  class TranscriptWord:
      word_id: str
      text: str
      punctuation: str
      start_ms: int
      end_ms: int
      confidence: float
      speaker: str

  @dataclass(frozen=True, slots=True)
  class TranscriptUtterance:
      utterance_id: str
      text: str
      start_ms: int
      end_ms: int
      speaker: str
      word_ids: tuple[str, ...]

  @dataclass(frozen=True, slots=True)
  class SpeakerSegment:
      segment_id: str
      speaker: str
      start_ms: int
      end_ms: int
      word_ids: tuple[str, ...]

  @dataclass(frozen=True, slots=True)
  class TranscriptResult:
      provider: str
      provider_version: str
      model: str
      language: str
      full_text: str
      words: tuple[TranscriptWord, ...]
      speaker_segments: tuple[SpeakerSegment, ...]
      utterances: tuple[TranscriptUtterance, ...]
      duration_ms: int
      raw_result: dict[str, JsonValue]
  ```

  Implement `normalize_transcript` by splitting only trailing Unicode punctuation from each token,
  assigning `w000001` IDs, joining normalized tokens with one space, mapping each utterance to words
  fully contained by its bounds with the same speaker, and coalescing adjacent equal-speaker words
  into `s000001` segments.

- [ ] **Step 4: Run the two tests and verify GREEN**

  Run the Step 2 command. Expected: 2 passed.

- [ ] **Step 5: Add RED tests for speaker changes, missing punctuation, and overlaps**

  Add separate tests proving:

  ```python
  assert [segment.speaker for segment in result.speaker_segments] == ["A", "B", "A"]
  assert result.words[0].punctuation == ""
  overlap = normalize_transcript(
      provider="assemblyai",
      provider_version="1.0.0",
      model="universal-3-pro",
      language="en",
      duration_ms=1_000,
      words=(RawWord("one", 0, 600, 0.9, "A"), RawWord("two", 500, 900, 0.9, "B")),
      utterances=(RawUtterance("one", 0, 600, "A"), RawUtterance("two", 500, 900, "B")),
      raw_result={"id": "overlap"},
  )
  assert overlap.words[1].start_ms == 500
  ```

  The overlap is valid because `500 >= 0`; a speaker change does not force artificial timestamp
  trimming.

- [ ] **Step 6: Run the three focused tests and verify RED for missing segment behavior**

  Run `uv run --no-sync pytest tests/unit/test_transcript_normalization.py -v`. Expected: at least
  the speaker-change assertion fails until segment construction is complete.

- [ ] **Step 7: Implement segment and utterance construction, then verify GREEN**

  Segment word membership must be exact and ordered. Utterance membership must be non-empty; every
  normalized word must belong to exactly one provider utterance. Run the whole normalization module;
  expected: all current tests pass.

- [ ] **Step 8: Add RED validation tests as independent cases**

  Parameterize invalid word results and assert exact `TranscriptValidationError.code` values:

  ```python
  @pytest.mark.parametrize(
      ("words", "code"),
      [
          ((), "TRANSCRIPT_EMPTY"),
          ((RawWord("bad", 100, 99, 0.9, "A"),), "TRANSCRIPT_TIMESTAMP_INVALID"),
          ((RawWord("one", 500, 700, 0.9, "A"), RawWord("two", 400, 800, 0.9, "A")),
           "TRANSCRIPT_TIMESTAMP_REGRESSION"),
          ((RawWord("late", 1_900, 2_100, 0.9, "A"),), "TRANSCRIPT_DURATION_EXCEEDED"),
      ],
  )
  def test_rejects_invalid_provider_word_sequences(words: tuple[RawWord, ...], code: str) -> None:
      with pytest.raises(TranscriptValidationError) as raised:
          normalize_transcript(
              provider="assemblyai",
              provider_version="1.0.0",
              model="universal-3-pro",
              language="en",
              duration_ms=2_000,
              words=words,
              utterances=(),
              raw_result={"id": "invalid"},
          )
      assert raised.value.code == code
  ```

  Add independent cases for empty speaker, blank text, confidence outside `[0, 1]`, utterance bounds
  that do not cover their words, and a word assigned to zero or multiple utterances.

- [ ] **Step 9: Run validation tests and verify RED**

  Expected: failures show invalid results are currently accepted or the exception type is missing.

- [ ] **Step 10: Implement exact validation and verify GREEN**

  `TranscriptValidationError` stores only a fixed `code`; its message must equal that code. Re-run
  `tests/unit/test_transcript_normalization.py`; expected: all tests pass.

- [ ] **Step 11: Add serialization and deterministic-key RED tests**

  Assert `transcript_document(result)` contains only JSON-native lists/dicts/scalars with camel-free
  persisted keys (`word_id`, `start_ms`, `end_ms`, `speaker`, `confidence`, `punctuation`) and assert:

  ```python
  assert raw_transcript_key(workspace_id, project_id, asset_id) == (
      f"workspaces/{workspace_id}/projects/{project_id}/transcripts/{asset_id}/assemblyai.json"
  )
  ```

- [ ] **Step 12: Implement serialization/key helpers and run the unit module GREEN**

  Use explicit dictionaries rather than `asdict`, keeping the durable schema intentional. Expected:
  the complete normalization module passes.

---

### Task 2: Provider port, deterministic fake, and AssemblyAI 1.x adapter

**Files:**
- Create: `backend/src/clipah/transcripts/provider.py`
- Create: `backend/src/clipah/transcripts/assemblyai_adapter.py`
- Modify: `backend/pyproject.toml`
- Test: `backend/tests/unit/test_assemblyai_adapter.py`

**Interfaces:**
- Consumes: `StoredObject` with a positive `duration_ms` and an injected
  `AudioUrlResolver = Callable[[StoredObject], str]`.
- Produces: `Transcriber.transcribe(*, audio: StoredObject, language: str | None) -> TranscriptResult`.
- Produces: `FakeTranscriber`, `TranscriptionProviderRetryableError`, and
  `TranscriptionProviderTerminalError`.

- [ ] **Step 1: Add the provider-port and fake RED test**

  Construct a normalized result, configure `FakeTranscriber(result=result)`, call it twice, and
  assert `calls == [(audio, "id"), (audio, "id")]` plus identity of the returned result.

- [ ] **Step 2: Run the fake test and verify RED**

  Expected: import fails because `transcripts.provider` does not exist.

- [ ] **Step 3: Implement the Protocol, fixed provider errors, and fake**

  Use this exact port:

  ```python
  class Transcriber(Protocol):
      def transcribe(self, *, audio: StoredObject, language: str | None) -> TranscriptResult:
          """Return one normalized transcript for the exact private audio object."""

  class FakeTranscriber:
      def __init__(self, *, result: TranscriptResult | None = None,
                   error: Exception | None = None) -> None:
          self.result = result
          self.error = error
          self.calls = []
      calls: list[tuple[StoredObject, str | None]]
  ```

  `FakeTranscriber.transcribe` appends its call, raises the configured error, or returns the required
  configured result. A missing result is a test-construction error, not production fallback.

- [ ] **Step 4: Run the fake test and verify GREEN**

  Expected: pass.

- [ ] **Step 5: Add RED routing tests for all language classes**

  Inject a recording SDK transcriber factory and assert exact `TranscriptionConfig` fields:

  ```python
  @pytest.mark.parametrize("language", ["en", "es", "de", "fr", "pt", "it"])
  def test_supported_u3_languages_use_only_universal_3_pro(language: str) -> None:
      assert recorded_config.speech_models == ["universal-3-pro"]
      assert recorded_config.language_code == language
      assert recorded_config.speaker_labels is True

  def test_indonesian_uses_only_universal_2() -> None:
      assert recorded_config.speech_models == ["universal-2"]
      assert recorded_config.language_code == "id"

  def test_unspecified_language_uses_detected_u3_then_u2_routing() -> None:
      assert recorded_config.speech_models == ["universal-3-pro", "universal-2"]
      assert recorded_config.language_detection is True
      assert recorded_config.language_code is None
  ```

  Add one unsupported-but-valid language such as `ja`, expecting only `universal-2`.

- [ ] **Step 6: Run routing tests and verify RED**

  Expected: adapter import or constructor fails because it is not implemented.

- [ ] **Step 7: Implement SDK construction and explicit routing**

  `AssemblyAITranscriber` accepts `api_key`, `audio_url_resolver`, and an injectable SDK factory.
  Production construction calls `aai.Transcriber(api_key=api_key)` and passes a per-call
  `aai.TranscriptionConfig(speech_models=speech_models, language_code=language_code,
  language_detection=language_code is None, speaker_labels=True, punctuate=True,
  format_text=True)`. Never mutate `aai.settings.api_key`.

  Add `module = ["assemblyai.*"]` with `ignore_missing_imports = true` to a dedicated mypy override;
  do not weaken typing outside the adapter.

- [ ] **Step 8: Run routing tests and verify GREEN**

  Expected: all routing tests pass.

- [ ] **Step 9: Add RED conversion and error tests**

  Build SDK-shaped response doubles containing `status`, `words`, `utterances`, `language_code`,
  `speech_model_used`, and `json_response`. Assert normalized word IDs, punctuation, speaker labels,
  SDK version `1.0.0`, and provider name `assemblyai`.

  Add separate tests proving SDK transport/timeout exceptions become
  `TranscriptionProviderRetryableError("TRANSCRIPTION_PROVIDER_UNAVAILABLE")`, while provider status
  `error` becomes `TranscriptionProviderTerminalError("TRANSCRIPTION_PROVIDER_REJECTED")`. Assert no
  provider exception text survives.

- [ ] **Step 10: Run conversion/error tests and verify RED**

  Expected: conversion fields or error translation fail.

- [ ] **Step 11: Implement conversion/error translation and verify GREEN**

  Read `speech_model_used`, detected `language_code`, words, utterances, and `json_response`; convert
  immediately to provider-neutral raw values and call `normalize_transcript`. Reject missing model,
  language, words, utterances, audio duration, or speaker labels through stable terminal errors.
  Re-run both Task 1 and Task 2 unit modules; expected: all pass.

---

### Task 3: Database uniqueness and ORM parity

**Files:**
- Create: `backend/migrations/versions/0007_unique_transcript_asset.py`
- Modify: `backend/src/clipah/models.py`
- Test: `backend/tests/integration/test_transcription.py`

**Interfaces:**
- Produces: database and ORM constraint `uq_transcripts_workspace_asset` over
  `(workspace_id, asset_id)`.

- [ ] **Step 1: Add the duplicate-Transcript RED integration test**

  Seed a Workspace, Project, source Asset, and two otherwise-valid Transcript inserts for the same
  source Asset. Assert the second flush raises `sqlalchemy.exc.IntegrityError` and the database holds
  one row after rollback.

- [ ] **Step 2: Run the focused integration test and verify RED**

  Run:
  `uv run --no-sync pytest tests/integration/test_transcription.py::test_database_allows_only_one_transcript_per_source_asset -v`

  Expected: the second insert succeeds because the constraint does not exist.

- [ ] **Step 3: Add migration 0007 and matching ORM metadata**

  Upgrade:

  ```python
  op.create_unique_constraint(
      "uq_transcripts_workspace_asset",
      "transcripts",
      ["workspace_id", "asset_id"],
  )
  ```

  Downgrade drops that exact constraint. Add the same `UniqueConstraint` to
  `Transcript.__table_args__` in `models.py`.

- [ ] **Step 4: Run migration upgrade and the focused test GREEN**

  Run `uv run --no-sync alembic upgrade head`, then the Step 2 test. Expected: pass.

- [ ] **Step 5: Prove reversible migration behavior**

  Run:

  ```bash
  uv run --no-sync alembic downgrade 0006
  uv run --no-sync alembic upgrade head
  uv run --no-sync alembic check
  ```

  Expected: all exit zero and `alembic check` reports no new upgrade operations.

---

### Task 4: Durable TRANSCRIBE runner and persistence

**Files:**
- Create: `backend/src/clipah/jobs/transcribe_task.py`
- Modify: `backend/src/clipah/jobs/tasks.py`
- Test: `backend/tests/integration/test_transcription.py`

**Interfaces:**
- Consumes: one source Asset plus its deterministic `TRANSCRIPTION_AUDIO` Asset.
- Consumes: `TranscriptionDependencies(transcriber: Transcriber, store: ObjectStore)`.
- Produces: registered `TranscribeStageRunner` for `JobKind.TRANSCRIBE`.

- [ ] **Step 1: Add the registration RED test**

  ```python
  def test_transcription_runner_is_registered_for_the_existing_job_kind() -> None:
      assert JobKind.TRANSCRIBE in stage_runners()
  ```

- [ ] **Step 2: Run the registration test and verify RED**

  Expected: assertion fails because no runner is registered.

- [ ] **Step 3: Add the minimal runner registration**

  Import `transcribe_stage_runner` at the bottom of `jobs/tasks.py` and call
  `_STAGE_RUNNERS.setdefault(JobKind.TRANSCRIBE, transcribe_stage_runner)`. Create the runner module
  with a callable shell that raises only inside execution, so import-time configuration remains
  side-effect free.

- [ ] **Step 4: Run the registration test and verify GREEN**

  Expected: pass.

- [ ] **Step 5: Add the successful persistence RED test**

  Seed a running TRANSCRIBE Job, one fully ingested source Asset, and the deterministic
  transcription-audio Asset. Use `FakeObjectStore` and `FakeTranscriber`. After one runner call,
  assert:

  - one Transcript references the source Asset;
  - provider/model/language/duration/full text match the result;
  - words, speaker segments, and utterances match `transcript_document(result)`;
  - `raw_result_storage_key` equals
    `raw_transcript_key(context.workspace_id, context.project_id, source_id)`;
  - the fake store contains exactly that JSON body with matching SHA-256;
  - the provider was called once with the transcription-audio metadata.

- [ ] **Step 6: Run the persistence test and verify RED**

  Expected: runner shell or missing persistence fails.

- [ ] **Step 7: Implement load, external call, raw upload, and atomic insert**

  Define:

  ```python
  @dataclass(frozen=True, slots=True)
  class TranscriptionDependencies:
      transcriber: Transcriber
      store: ObjectStore

  DependenciesFactory = Callable[[Settings], TranscriptionDependencies]

  class TranscribeStageRunner:
      def __init__(self, *, dependencies_factory: DependenciesFactory) -> None:
          self._dependencies_factory = dependencies_factory

      def __call__(self, context: JobContext) -> None:
          dependencies = self._dependencies_factory(context.settings)
          self._run(context, dependencies)
  ```

  `_load_assets` reads exactly one source Asset and `uuid5(source.id,
  AssetKind.TRANSCRIPTION_AUDIO.value)` in a short worker transaction, verifies Workspace, Project,
  kind, key, positive duration, size, and 32-byte digest, then returns detached `StoredObject`
  metadata. Call `context.raise_if_cancelled()` at every boundary named in the design.

  Serialize `result.raw_result` with `json.dumps(result.raw_result, ensure_ascii=False, sort_keys=True,
  separators=(",", ":"))`, calculate SHA-256, upload with `application/json`, and require returned
  key, length, and digest to match. Insert one `clipah.models.Transcript` only after validation.

- [ ] **Step 8: Run persistence plus Task 1/2 unit tests GREEN**

  Expected: pass.

- [ ] **Step 9: Add idempotency and concurrent-convergence RED tests**

  Run the same runner twice and assert `len(fake.calls) == 1`, one Transcript row, and one raw key.
  Add a concurrency test using two runner instances and a barrier fake; both calls complete, the
  database holds one compatible row, and no uniqueness exception escapes.

- [ ] **Step 10: Run idempotency tests and verify RED**

  Expected: repeated execution calls the provider twice or concurrent insertion leaks an
  `IntegrityError`.

- [ ] **Step 11: Implement valid-row short-circuit and uniqueness convergence**

  Before provider work, load the Transcript by Workspace and source Asset. Validate its required
  metadata, JSON list shapes, word IDs, timestamps, duration, and deterministic raw key. Return only
  for a compatible valid row. Treat malformed/conflicting existing evidence as
  `TerminalJobError("TRANSCRIPT_INTEGRITY")`.

  Catch the named unique-constraint race after rollback, re-read in a fresh transaction, and accept
  only a compatible row. Re-run idempotency tests; expected: pass.

- [ ] **Step 12: Add RED tests for missing assets and classified failures**

  Add independent tests asserting:

  - missing source -> `TRANSCRIPTION_SOURCE_NOT_FOUND` terminal;
  - missing/incomplete audio -> `TRANSCRIPTION_AUDIO_NOT_FOUND` terminal;
  - `TranscriptionProviderRetryableError` -> `TRANSCRIPTION_PROVIDER_UNAVAILABLE` retryable;
  - `TranscriptionProviderTerminalError` -> its stable terminal code;
  - `ObjectStoreUnavailableError` -> `TRANSCRIPTION_STORAGE_UNAVAILABLE` retryable;
  - normalization or upload metadata conflict -> `TRANSCRIPT_INTEGRITY` terminal;
  - cancellation before/after provider call propagates `JobCancelledError` without a Transcript row.

- [ ] **Step 13: Run failure tests and verify RED**

  Expected: at least one provider/storage/domain exception escapes without the required Job class.

- [ ] **Step 14: Implement sanitized error translation and verify GREEN**

  Catch only known provider-neutral errors. Never use provider exception text as a Job code. Run the
  entire integration module; expected: all tests pass.

- [ ] **Step 15: Add RLS tenant-isolation RED test**

  Seed two Workspaces and prove the worker runtime cannot read or short-circuit against the other
  Workspace's Transcript even when given a guessed identifier. Assert the declared Workspace sees
  only its own row.

- [ ] **Step 16: Run the RLS test, enforce explicit tenant predicates, and verify GREEN**

  Add `Transcript.workspace_id == context.workspace_id`, `Asset.workspace_id ==
  context.workspace_id`, and `Asset.project_id == context.project_id` to their respective SELECTs;
  retain the existing RLS context as defence in depth. Expected: full integration module passes.

- [ ] **Step 17: Implement production dependency composition under tests**

  `production_transcription_dependencies(settings)` requires AssemblyAI API key and object-store
  credentials, creates one `S3ObjectStore`, and constructs `AssemblyAITranscriber` with an injected
  resolver that returns a five-minute `store.sign_download(key=audio.key,
  expires_in=timedelta(minutes=5)).url`. Add a unit test proving missing settings fail closed without
  exposing secret values.

---

### Task 5: Opt-in AssemblyAI contract smoke test

**Files:**
- Modify: `backend/tests/integration/test_transcription.py`

**Interfaces:**
- Consumes: `CLIPAH_RUN_ASSEMBLYAI_SMOKE=1`, `CLIPAH_ASSEMBLYAI_API_KEY`,
  `CLIPAH_ASSEMBLYAI_SMOKE_AUDIO_URL`, and `CLIPAH_ASSEMBLYAI_SMOKE_DURATION_MS`.
- Produces: an environment-gated check of locked SDK response fields and diarization.

- [ ] **Step 1: Add the skipped-by-default smoke test**

  Mark it `@pytest.mark.integration` and `@pytest.mark.slow`. Skip unless the opt-in flag equals
  `"1"`; skip with a precise missing-variable message when any required input is absent. Construct a
  `StoredObject` whose key is the supplied HTTPS audio URL and an adapter resolver returning that
  key. Assert non-empty words, at least one utterance and speaker segment, positive timestamps,
  provider `assemblyai`, actual model in `{universal-2, universal-3-pro}`, and language present.

- [ ] **Step 2: Run the smoke node without opt-in**

  Expected: one skipped test and zero failures; no network request occurs.

- [ ] **Step 3: Run all focused Task 12 tests**

  Run:

  ```bash
  uv run --no-sync pytest tests/unit/test_transcript_normalization.py \
    tests/unit/test_assemblyai_adapter.py tests/integration/test_transcription.py -q
  ```

  Expected: all deterministic tests pass and only the provider smoke test is skipped.

---

### Task 6: Progress record and complete verification

**Files:**
- Modify: `PROGRESS.md`

**Interfaces:**
- Produces: an honest Task 12 handoff with gate evidence and owner commit message.

- [ ] **Step 1: Run fast static checks before the full suite**

  From `backend/` run:

  ```bash
  uv run --no-sync ruff check .
  uv run --no-sync ruff format --check .
  uv run --no-sync mypy src
  ```

  Expected: all exit zero. Fix only Task 12 findings and rerun the failing command.

- [ ] **Step 2: Run the full coverage gate**

  Run:

  ```bash
  uv run --no-sync pytest -q --cov=clipah --cov-fail-under=90
  ```

  Expected: all deterministic tests pass, environment-gated smoke tests skip, coverage is at least
  90%, and there are no warnings attributable to Task 12.

- [ ] **Step 3: Re-run migration and whitespace checks**

  Run:

  ```bash
  uv run --no-sync alembic downgrade 0006
  uv run --no-sync alembic upgrade head
  uv run --no-sync alembic check
  git diff --check
  ```

  Expected: all exit zero.

- [ ] **Step 4: Update `PROGRESS.md` without overstating results**

  Change Task 11 to landed at `b434f8d`, mark Task 12 `[~]` because the owner has not committed it,
  set the current position to “Task 12 implementation is complete and pending owner commit. Task 13
  follows,” and add a Task 12 delivery section containing the actual test count, skipped-test count,
  coverage percentage, gate results, routing behavior, persistence/idempotency behavior, and any
  deliberate deferrals.

- [ ] **Step 5: Inspect the final diff and status**

  Run `git status --short --branch`, `git diff --stat`, and `git diff --check`. Confirm only Task 12,
  its design/plan documents, migration, tests, and `PROGRESS.md` are changed.

- [ ] **Step 6: Hand over without committing**

  Report the changed behavior, exact four-gate outputs, migration verification, skipped smoke test,
  and required owner commit message: `feat: persist diarized word transcripts`.
