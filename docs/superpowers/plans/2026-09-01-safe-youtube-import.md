# Safe YouTube Import Implementation Plan

> **For Clipah maintainers:** Execute this plan in order. Follow `AGENTS.md`: test first, do not
> let the agent commit, and stop after the four gates and review are green so the owner can commit.

**Goal:** Import one public, non-live YouTube video through a durable Workspace-scoped job without
accepting unsafe URLs, private-network destinations, playlists, authenticated sources, or unbounded
media.

**Architecture:** Keep pure URL/DNS policy in `assets/source_validation.py`, provider-neutral values
and errors in `assets/youtube.py`, and all yt-dlp/process details in
`source_connectors/yt_dlp_adapter.py`. The HTTP route creates a `SourceImport` and `SOURCE_IMPORT`
job transactionally, commits before dispatch, and passes only UUID strings to Celery. The worker
reloads tenant state, uses a per-job workspace, uploads to a deterministic key, and converges retries
onto one Asset row.

**Runtime:** Python 3.13, FastAPI, SQLAlchemy/Postgres RLS, Celery, S3-compatible storage, yt-dlp
2026.08.19, yt-dlp-ejs 0.8.0, Deno 2.9.5, Debian 13 FFmpeg 7:7.1.5-0+deb13u1.

**Design:** `docs/superpowers/specs/2026-08-31-safe-youtube-import-design.md`

---

## Task 10a — Provider-neutral contracts and source validation

### Step 1: Write the URL, DNS, redirect, and rebind tests

**Files:**

- Create: `backend/tests/unit/test_source_validation.py`
- Create: `backend/src/clipah/assets/youtube.py`
- Create: `backend/src/clipah/assets/source_validation.py`

Write table-driven tests before implementation. Use an injected resolver returning literal
`ipaddress`-parseable strings; tests must never query public DNS.

Required accepted cases and canonical result:

```python
@pytest.mark.parametrize(
    ("url", "video_id"),
    [
        ("https://youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ],
)
def test_allowed_video_urls_are_canonicalized(url: str, video_id: str) -> None:
    normalized = validate_youtube_url(url, resolver=public_resolver)
    assert normalized.canonical_url == f"https://www.youtube.com/watch?v={video_id}"
```

Add rejection tables for:

- `http`, `ftp`, missing scheme, fragments that pretend to carry `v`, explicit ports, userinfo;
- deceptive suffix/prefix hosts, Unicode/IDNA lookalikes, trailing-dot handling, empty host;
- missing/duplicate/malformed video IDs, extra path segments, `/playlist`, and any `list` parameter;
- IPv4 and IPv6 private, loopback, link-local, multicast, reserved, unspecified, mapped-private IPv6,
  empty DNS answers, and mixed public/private answers;
- redirect chains longer than five hops, disallowed redirect hosts, unsafe redirect DNS, and a
  worker re-resolution whose complete address set differs from the established snapshot.

Run the red test:

```bash
cd backend
uv run pytest tests/unit/test_source_validation.py -v
```

Expected: collection fails because the modules do not exist.

### Step 2: Implement the domain values and stable errors

In `assets/youtube.py`, implement immutable, slotted values and protocols:

```python
MAX_SOURCE_DURATION_SECONDS = 4 * 60 * 60
MAX_SOURCE_SIZE_BYTES = 2 * 1024 * 1024 * 1024

@dataclass(frozen=True, slots=True)
class NormalizedYouTubeUrl:
    canonical_url: str
    video_id: str
    host: str
    addresses: frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]

@dataclass(frozen=True, slots=True)
class SourceMetadata:
    video_id: str
    duration_seconds: int
    content_type: str
    extension: str

class SourceImportError(Exception):
    code: ClassVar[str]
    retryable: ClassVar[bool] = False

class SourceUnsupportedError(SourceImportError):
    code = "SOURCE_UNSUPPORTED"

class SourcePrivateError(SourceImportError):
    code = "SOURCE_PRIVATE"

class SourceTooLongError(SourceImportError):
    code = "SOURCE_TOO_LONG"

class SourceTlsError(SourceImportError):
    code = "SOURCE_TLS_FAILED"

class SourceUnavailableError(SourceImportError):
    code = "SOURCE_UNAVAILABLE"
    retryable = True

class PoTokenProvider(Protocol):
    def token_for(self, *, video_id: str) -> str | None: ...

class SourceImporter(Protocol):
    def import_source(
        self,
        source: NormalizedYouTubeUrl,
        *,
        workspace: Path,
        object_key: str,
        cancellation_check: Callable[[], None],
    ) -> StoredObject: ...
```

`PoTokenProvider` is only a port. Do not create a default implementation, setting, plugin, cookie
path, or browser-profile integration.

### Step 3: Implement strict normalization and network policy

In `assets/source_validation.py`:

- parse with `urllib.parse.urlsplit` and reject before normalization if credentials, fragments,
  invalid ports, non-HTTPS schemes, or ambiguous query/path forms exist;
- lowercase and strip one terminal dot from the hostname, then compare against the exact frozen
  host set;
- accept only ASCII video IDs matching `^[A-Za-z0-9_-]{11}$`;
- canonicalize every accepted form to `https://www.youtube.com/watch?v=<id>`;
- resolve with an injected `Resolver` protocol, defaulting to `socket.getaddrinfo`;
- parse every unique address with `ipaddress.ip_address`, explicitly unwrap IPv4-mapped IPv6, and
  require `is_global` while also rejecting every special category named in the tests;
- return a `frozenset` so order changes do not look like rebinding;
- implement `validate_redirect_chain` with a maximum of five normalized hops;
- implement `revalidate_youtube_url` by comparing the new complete set with `source.addresses`.

Do not perform HTTP requests in this module. It validates URL and resolver observations supplied by
the adapter.

Run green and local quality checks:

```bash
cd backend
uv run pytest tests/unit/test_source_validation.py -v
uv run ruff check src/clipah/assets/youtube.py src/clipah/assets/source_validation.py tests/unit/test_source_validation.py
uv run mypy src/clipah/assets/youtube.py src/clipah/assets/source_validation.py
```

Expected: all pass.

---

## Task 10b — yt-dlp adapter, exact-file storage, and isolated runtime

### Step 4: Write adapter and exact-file storage tests

**Files:**

- Create: `backend/src/clipah/source_connectors/__init__.py`
- Create: `backend/src/clipah/source_connectors/yt_dlp_adapter.py`
- Create: `backend/tests/unit/test_yt_dlp_adapter.py`
- Modify: `backend/src/clipah/assets/storage.py`
- Modify: `backend/tests/integration/test_multipart_uploads.py`

Build a `RecordingCommandRunner`, fake source-preflight transport, deterministic resolver, temporary
workspace, and `FakeObjectStore`. Test these contracts before implementation:

- metadata arguments contain `--no-config`, `--no-playlist`, `--skip-download`,
  `--dump-single-json`, and the canonical URL;
- download arguments contain `--no-config`, `--no-playlist`, `--max-filesize 2147483648`, a fixed
  output template beneath the workspace, and no cookies/browser profile/certificate bypass/remote
  components/shell;
- environment is an allowlist (`PATH`, locale, controlled Deno cache) and never inherits secrets;
- command timeout, bounded stdout/stderr, and `shell=False` are observable;
- source preflight follows zero-to-five redirects manually, closes each response without consuming
  its body, and validates every hop;
- DNS changes before metadata or download raise `SOURCE_UNSUPPORTED`;
- extractor mismatch, playlist/multiple entries, live/upcoming/post-live, malformed metadata,
  private, members-only, age-restricted, and over-four-hour cases map to stable errors;
- TLS failures map to `SOURCE_TLS_FAILED`; timeouts/transient provider failures map to retryable
  `SOURCE_UNAVAILABLE`; raw stderr never becomes an exception string;
- a reported output path outside the workspace, a symlink, more than one final file, or a file over
  two GiB is rejected;
- upload computes SHA-256 while streaming, uses the supplied deterministic key, and returns enriched
  provider-neutral metadata;
- cancellation checks run before preflight, metadata, download, and upload;
- the fake importer returns deterministic results without network or process execution.

Extend `StoredObject` without breaking multipart callers:

```python
@dataclass(frozen=True)
class StoredObject:
    key: str
    content_type: str
    content_length: int
    sha256: bytes | None = None
    duration_ms: int | None = None
```

Extend `ObjectStore` with:

```python
def put_file(self, *, key: str, content_type: str, file: BinaryIO) -> StoredObject:
    """Upload one exact server-selected stream without any prefix or listing operation."""
```

S3 uses `upload_fileobj` followed by `head_object`. The fake reads the stream, stores exact bytes in
a separate `object_bodies` dictionary, and derives length itself. Add regression assertions showing
all multipart behavior is unchanged.

Run the red tests:

```bash
cd backend
uv run pytest tests/unit/test_yt_dlp_adapter.py tests/integration/test_multipart_uploads.py -v
```

Expected: adapter tests fail to import and storage contract assertions fail.

### Step 5: Implement the adapter in narrow stages

Implement these private boundaries in `yt_dlp_adapter.py`:

```python
@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

class CommandRunner(Protocol):
    def run(self, arguments: Sequence[str], *, cwd: Path, timeout: float) -> CommandResult: ...

class SourcePreflight(Protocol):
    def redirect_chain(self, source: NormalizedYouTubeUrl) -> tuple[str, ...]: ...
```

The production runner uses `subprocess.run` with an argument list, `shell=False`, `check=False`,
`capture_output=True`, `text=True`, a fixed timeout, process-group isolation, and output truncation
before parsing or classification. The production preflight uses `httpx.Client(follow_redirects=False,
verify=True)` and streamed `GET` requests, validates `Location` before the next request, and closes
without iterating response bytes.

`YtDlpSourceImporter.import_source` must execute this order:

1. cancellation check;
2. bounded source preflight and redirect validation;
3. re-resolve against the worker snapshot;
4. metadata command and strict JSON normalization;
5. duration/privacy/live/extractor checks;
6. cancellation check and second re-resolution;
7. download command to `<workspace>/source.%(ext)s`;
8. resolve and validate exactly one regular, non-symlink child file;
9. stat the file and enforce the two-GiB limit independently of yt-dlp;
10. cancellation check, hash while streaming into `ObjectStore.put_file`, and enrich the returned
    `StoredObject` with SHA-256 and duration.

Classify known yt-dlp failures using return code plus a small fixed set of adapter-owned patterns.
Never expose command output, source URLs, paths, or provider messages in domain exception text.

Run green:

```bash
cd backend
uv run pytest tests/unit/test_yt_dlp_adapter.py tests/integration/test_multipart_uploads.py -v
uv run ruff check src/clipah/assets/storage.py src/clipah/source_connectors tests/unit/test_yt_dlp_adapter.py
uv run mypy src/clipah/assets/storage.py src/clipah/source_connectors
```

### Step 6: Pin and verify the independently buildable import runtime

**Files:**

- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Create: `backend/src/clipah/source_connectors/readiness.py`
- Create: `backend/tests/unit/test_source_import_readiness.py`
- Create: `infra/docker/source-import.Dockerfile`
- Create: `infra/docker/source-import-entrypoint.sh`

Add a `source-import` optional dependency set containing exact `yt-dlp==2026.8.19` and
`yt-dlp-ejs==0.8.0`, then regenerate `uv.lock`. Do not install them into unrelated production images.

Write readiness tests first. The readiness module must accept an injected runner and require exact
outputs for yt-dlp, Deno, and FFmpeg; verify the installed `yt-dlp-ejs` distribution version through
`importlib.metadata`; and run a public metadata probe only when
`CLIPAH_SOURCE_IMPORT_NETWORK_PROBE=1`. Mark the live test `slow` and skip unless both that flag and
an explicit fixture URL are present.

The Dockerfile must:

- use an immutable Python 3.13 Debian 13 base reference;
- point apt at an immutable Debian snapshot and install
  `ffmpeg=7:7.1.5-0+deb13u1`, CA certificates, and no recommended packages;
- download Deno 2.9.5 for `amd64` or `arm64` from the immutable GitHub release and verify the
  adjacent official `.sha256sum` before installation;
- install only the locked backend plus the `source-import` extra;
- create a non-root user and writable job/Deno cache directories with restrictive permissions;
- carry no API, login, AI, generation, rendering, social, or cookie credentials;
- use `python -m clipah.source_connectors.readiness` as `HEALTHCHECK` and start a Celery worker bound
  only to `source_import` with concurrency one.

Test locally:

```bash
cd backend
uv lock
uv run pytest tests/unit/test_source_import_readiness.py -v
cd ..
docker build -f infra/docker/source-import.Dockerfile -t clipah-source-import:test .
docker run --rm --entrypoint python clipah-source-import:test -m clipah.source_connectors.readiness
```

Expected: readiness reports only sanitized version/status lines and exits zero. Do not enable the
network probe in normal verification.

---

## Task 10c — Durable route, dispatch, worker, and retry convergence

### Step 7: Write HTTP and worker integration tests

**Files:**

- Create: `backend/src/clipah/source_imports/__init__.py`
- Create: `backend/src/clipah/source_imports/repository.py`
- Create: `backend/src/clipah/source_imports/use_cases.py`
- Create: `backend/src/clipah/jobs/source_import_task.py`
- Create: `backend/src/clipah/api/routes/youtube_imports.py`
- Create: `backend/tests/integration/test_youtube_imports.py`
- Modify: `backend/tests/harness.py`

Add injected `SourceImporter` and `JobDispatcher` seams to the test app. The dispatcher protocol is:

```python
class JobDispatcher(Protocol):
    def dispatch(self, *, job_id: UUID, workspace_id: UUID, user_id: UUID) -> None: ...
```

The production adapter calls `run_job.apply_async` with three UUID strings and
`queue=queue_for(JobKind.SOURCE_IMPORT)`.

Write real-Postgres integration tests first for:

- unauthenticated, missing CSRF, viewer, archived Project, missing/cross-Workspace Project, and
  disabled Workspace behavior using the established indistinguishable 404/403 contracts;
- malformed/unsafe source URLs returning sanitized `SOURCE_UNSUPPORTED` without a Job row;
- valid creation returning 202 with `sourceImportId`, `jobId`, and `queued`;
- one transaction containing a SourceImport linked to one `SOURCE_IMPORT` Job;
- repeated identical key/payload returning identical IDs and redispatching only a still-queued job;
- same key with a different URL, Project, or pre-existing different Job kind returning `CONFLICT`;
- two concurrent identical submissions converging on one Job and SourceImport;
- concurrency admission failure returning `CONCURRENCY_LIMIT` without a SourceImport;
- broker/dispatcher failure still returning the durable queued identifiers;
- broker arguments containing only UUID strings.

Worker tests must prove:

- registered `SOURCE_IMPORT` work reloads only the SourceImport belonging to the context's Workspace,
  Project, and Job;
- repeated delivery/retry creates one deterministic object key and one Asset whose ID equals the
  SourceImport ID;
- the Asset is `VIDEO`/`SOURCE_IMPORT` and stores content type, length, SHA-256, and duration;
- success sets SourceImport `completed`; permanent source errors set it `failed`; cancellation sets
  it `canceled`; retryable failure sets it `failed` for that attempt and a retry moves it back to
  `downloading` before trying again;
- every source code reaches the Job unchanged, with `SOURCE_UNAVAILABLE` retryable and the required
  four policy codes terminal;
- cancellation before metadata/download/upload prevents later stages and never creates an Asset;
- provider error text, paths, and commands never appear in API errors, Job events, or database error
  fields.

Run the red integration test:

```bash
cd backend
uv run pytest tests/integration/test_youtube_imports.py -v
```

Expected: collection fails because the route/use cases/task do not exist.

### Step 8: Implement repository and transactional create/replay

In `source_imports/repository.py`, keep every query scoped by `workspace_id`. Implement:

- a transaction-scoped Postgres advisory lock over `workspace_id + route scope + idempotency key`;
- active Project load with `FOR UPDATE`;
- SourceImport lookup by Job ID and locked lookup by SourceImport ID;
- insert/update helpers;
- deterministic Asset lookup/insert by SourceImport UUID.

In `source_imports/use_cases.py`, implement:

```python
@dataclass(frozen=True, slots=True)
class SourceImportSnapshot:
    source_import_id: UUID
    job_id: UUID
    status: SourceImportStatus

def create_source_import(
    session: Session,
    *,
    policy: AdmissionPolicy,
    access: WorkspaceAccess,
    project_id: UUID,
    source: NormalizedYouTubeUrl,
    idempotency_key: str,
    now: datetime,
) -> SourceImportSnapshot: ...
```

Acquire the advisory lock before replay lookup. If an existing job differs in kind, Project, URL,
or video ID, raise a domain conflict. Otherwise return its linked SourceImport. For a new request,
require an active Project, call existing `create_job`, create the SourceImport with the same
transaction, flush, and return immutable IDs. No network or dispatch happens inside this function.

### Step 9: Implement commit-before-dispatch HTTP behavior

In `youtube_imports.py`:

- use `WritableWorkspace`, `DatabaseSession`, `require_csrf`, and the established idempotency header;
- accept a strict Pydantic body containing only `url`;
- run admission-time `validate_youtube_url` before durable creation;
- map source validation to `422 SOURCE_UNSUPPORTED`, missing Project to 404, idempotency conflict to
  409, and admission exceptions to the existing limit codes;
- explicitly `session.commit()` before calling the injected dispatcher so an eager worker cannot
  race uncommitted rows;
- catch dispatcher/broker exceptions and still return the committed 202 resource;
- dispatch on both new creation and idempotent replay only while the Job remains `queued`;
- never dispatch terminal, running, or retrying work from an HTTP retry.

Add fixed public messages for `SOURCE_UNSUPPORTED`, `SOURCE_PRIVATE`, `SOURCE_TOO_LONG`,
`SOURCE_TLS_FAILED`, and `SOURCE_UNAVAILABLE` to `api/errors.py`. Include the router and dispatcher in
`api/app.py`, and extend `tests/harness.py` with deterministic fakes.

Run route-focused tests until green.

### Step 10: Implement the source-import stage runner

In `jobs/source_import_task.py`, create a callable runner that accepts injected importer/store
factories for tests and a production wrapper for the registry. It must:

1. open a short worker transaction, lock/reload SourceImport by `workspace_id + project_id + job_id`,
   and set `downloading`;
2. close the transaction;
3. enter `job_workspace(context.job_id)`;
4. rebuild/validate the normalized URL and establish the worker DNS snapshot;
5. call the importer with deterministic key
   `workspaces/<workspace>/projects/<project>/source-imports/<source-import>/original`;
6. open a second short transaction and insert-or-verify the deterministic Asset, then mark completed;
7. on cancellation, mark canceled and re-raise `JobCancelledError`;
8. on a source error, mark failed, then raise a coded terminal or retryable Job exception without
   retaining provider text.

Add `TerminalJobError` to `jobs/models.py` and catch it in `jobs/tasks.py` before the generic
exception handler so permanent source codes reach `fail_job`. Initialize the built-in stage registry
with the production source-import runner while preserving test isolation.

When verifying an existing deterministic Asset, compare every immutable field. A mismatch is an
internal integrity failure; never overwrite another row or widen the lookup.

Run focused green tests:

```bash
cd backend
uv run pytest tests/integration/test_youtube_imports.py tests/integration/test_jobs.py -v
uv run ruff check src/clipah/source_imports src/clipah/jobs/source_import_task.py src/clipah/api/routes/youtube_imports.py tests/integration/test_youtube_imports.py
uv run mypy src/clipah/source_imports src/clipah/jobs/source_import_task.py src/clipah/api/routes/youtube_imports.py
```

---

## Final verification, review, and handoff

### Step 11: Run the complete Task 10 matrix

Run focused tests first:

```bash
cd backend
uv run pytest tests/unit/test_source_validation.py tests/unit/test_yt_dlp_adapter.py tests/unit/test_source_import_readiness.py tests/integration/test_youtube_imports.py -v
```

Run the four required backend gates exactly:

```bash
cd backend
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest --cov=clipah --cov-report=term-missing --cov-fail-under=90
```

Run the isolated image proof:

```bash
docker build -f infra/docker/source-import.Dockerfile -t clipah-source-import:test .
docker run --rm --entrypoint python clipah-source-import:test -m clipah.source_connectors.readiness
```

Do not run the public network smoke test unless the owner explicitly opts in.

### Step 12: Review against standards and spec

Use the repository's code-review workflow against the Task 9 commit `011f9c0`, covering both axes:

- **Standards:** `AGENTS.md`, tenant/RLS rules, test-first evidence, provider boundaries, no secret or
  raw-error exposure, docstrings, typing, and deterministic retry behavior.
- **Spec:** every Task 10 checkbox in `plan.md` plus every decision in the approved design.

Resolve all actionable findings and rerun every affected focused test plus all four gates.

### Step 13: Update progress and stop for owner commit

**File:** `PROGRESS.md`

- replace Task 9's pending marker with owner commit `011f9c0`;
- mark Task 10 complete only after focused tests, all four gates, image readiness, and review pass;
- record `pending owner commit` for Task 10 and set Task 11 as next.

Run:

```bash
git diff --check
git status --short
git diff --stat
```

Do not commit. Hand the owner:

- a concise file/change summary;
- exact focused/gate/image results;
- any intentionally skipped opt-in network smoke test;
- review result;
- requested commit message: `fix: secure remote video imports`.
