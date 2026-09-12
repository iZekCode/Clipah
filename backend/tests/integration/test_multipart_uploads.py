"""Integration contracts for Workspace-scoped multipart upload HTTP lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from queue import Queue
from threading import Event, Thread
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from clipah.assets.keys import source_upload_key
from clipah.assets.storage import (
    CompletedPart,
    FakeObjectStore,
    ObjectStoreUnavailableError,
    S3ObjectStore,
)
from clipah.assets.uploads import (
    CreateUploadCommand,
    UploadConflictError,
    abort_upload,
    complete_upload,
    create_upload,
)
from clipah.models import (
    MultipartUpload,
    MultipartUploadStatus,
    Project,
    PublishingRolePolicy,
    WorkspaceRole,
)
from clipah.workspaces.models import WorkspaceAccess
from harness import NOW, Browser, Clock, StubGoogleProvider, assert_error, build_app, sign_in

MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024


class CountingFakeObjectStore(FakeObjectStore):
    """Make terminal fake provider operations observable across concurrent transactions."""

    def __init__(self, *, now: Clock, block_first_terminal_operation: bool = False) -> None:
        """Initialize deterministic storage plus an exact completion-operation ledger."""
        super().__init__(now=now)
        self.completions: list[tuple[str, str]] = []
        self.terminal_operations: list[str] = []
        self.first_terminal_started = Event()
        self.second_terminal_started = Event()
        self.release_first_terminal = Event()
        self._block_first_terminal_operation = block_first_terminal_operation

    def complete_multipart_upload(
        self, *, upload_id: str, key: str, parts: list[CompletedPart]
    ) -> object:
        """Record and optionally pause completion before delegating to fake storage."""
        self._before_terminal_operation("complete")
        self.completions.append((upload_id, key))
        return super().complete_multipart_upload(upload_id=upload_id, key=key, parts=parts)

    def abort_multipart_upload(self, *, upload_id: str, key: str) -> None:
        """Record and optionally pause abort before delegating to fake storage."""
        self._before_terminal_operation("abort")
        super().abort_multipart_upload(upload_id=upload_id, key=key)

    def _before_terminal_operation(self, operation: str) -> None:
        """Hold the first terminal call so a racing transaction exposes absent row locking."""
        self.terminal_operations.append(operation)
        if not self._block_first_terminal_operation:
            return
        if len(self.terminal_operations) == 1:
            self.first_terminal_started.set()
            assert self.release_first_terminal.wait(timeout=3)
            return
        self.second_terminal_started.set()


class CreateGateFakeObjectStore(FakeObjectStore):
    """Hold storage creation after the active Project lookup acquires its database lock."""

    def __init__(self, *, now: Clock) -> None:
        """Initialize deterministic storage and create-operation synchronization gates."""
        super().__init__(now=now)
        self.create_started = Event()
        self.release_create = Event()

    def create_multipart_upload(self, *, key: str, content_type: str) -> object:
        """Pause the provider allocation until the competing archive has attempted its write."""
        self.create_started.set()
        assert self.release_create.wait(timeout=3)
        return super().create_multipart_upload(key=key, content_type=content_type)


def _workspace_id(browser: Browser) -> UUID:
    """Return the signed-in User's selected personal Workspace identifier."""
    return UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])


def _create_project(browser: Browser, workspace_id: UUID, *, key: str) -> dict[str, str]:
    """Create one upload-backed Project through the public API."""
    response = browser.request(
        "POST",
        f"/api/v1/projects?workspace_id={workspace_id}",
        headers={"Idempotency-Key": key},
        json={"name": "Source media", "sourceKind": "upload"},
    )
    assert response.status_code == 201
    return response.json()


def _uploads_path(workspace_id: UUID, project_id: str) -> str:
    """Build the collection route bound to an authorized Workspace selection."""
    return f"/api/v1/projects/{project_id}/uploads?workspace_id={workspace_id}"


def _upload_path(workspace_id: UUID, project_id: str, upload_id: object) -> str:
    """Build one item route while retaining the verified Workspace selection query."""
    return f"/api/v1/projects/{project_id}/uploads/{upload_id}?workspace_id={workspace_id}"


def _upload_action_path(workspace_id: UUID, project_id: str, upload_id: object, action: str) -> str:
    """Build an item subresource route with its query string placed after the action path."""
    return f"/api/v1/projects/{project_id}/uploads/{upload_id}/{action}?workspace_id={workspace_id}"


def _create_upload(
    browser: Browser, workspace_id: UUID, project_id: str, *, content_length: int = 1024
) -> dict[str, object]:
    """Create a declared-size upload using display-only client filename metadata."""
    response = browser.request(
        "POST",
        _uploads_path(workspace_id, project_id),
        json={
            "filename": "../../display-name.mp4",
            "contentType": "video/mp4",
            "contentLength": content_length,
        },
    )
    assert response.status_code == 201
    return response.json()


def _ensure_local_minio_bucket(bucket: str) -> None:
    """Provision the disposable loopback MinIO test bucket outside the production adapter."""
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3",
        endpoint_url="http://127.0.0.1:59001",
        aws_access_key_id="clipah_local",
        aws_secret_access_key="clipah_local_secret",
        config=Config(s3={"addressing_style": "path"}),
    )
    try:
        client.head_bucket(Bucket=bucket)
    except client.exceptions.ClientError:
        client.create_bucket(Bucket=bucket)


@pytest.mark.integration
def test_multipart_upload_lifecycle_uses_tenant_safe_storage_and_five_minute_download(
    engine: Engine,
) -> None:
    """A permitted User can create, sign, complete, and receive only a short-lived URL."""
    clock = Clock(NOW)
    store = FakeObjectStore(now=clock)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), object_store=store)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    project = _create_project(browser, workspace_id, key="upload-project")

    upload = _create_upload(browser, workspace_id, project["id"])
    assert "storageKey" not in upload
    part = browser.request(
        "POST",
        _upload_action_path(workspace_id, project["id"], upload["id"], "parts/1"),
    )
    assert part.status_code == 200
    assert part.json()["url"].startswith("fake://multipart/")

    storage_upload_id = next(iter(store.upload_keys))
    store.put_multipart_part(upload_id=storage_upload_id, part_number=1, size_bytes=1024)
    complete = browser.request(
        "POST",
        _upload_action_path(workspace_id, project["id"], upload["id"], "complete"),
        json={"parts": [{"partNumber": 1, "etag": "etag-1"}]},
    )
    assert complete.status_code == 200
    assert complete.json()["downloadUrl"].startswith("fake://download/")
    assert complete.json()["downloadExpiresAt"] == (NOW + timedelta(minutes=5)).isoformat()


@pytest.mark.integration
def test_multipart_upload_rejects_wrong_workspace_invalid_part_and_duplicate_completion(
    engine: Engine,
) -> None:
    """Workspace boundaries and terminal transitions do not disclose storage state."""
    clock = Clock(NOW)
    store = FakeObjectStore(now=clock)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), object_store=store)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    project = _create_project(browser, workspace_id, key="invalid-upload-project")
    upload = _create_upload(browser, workspace_id, project["id"])

    invalid = browser.request(
        "POST",
        _upload_action_path(workspace_id, project["id"], upload["id"], "parts/0"),
    )
    assert_error(invalid, status_code=422, code="VALIDATION_ERROR")

    other = browser.request("POST", "/api/v1/workspaces", json={"name": "Other", "kind": "team"})
    assert other.status_code == 201
    wrong_workspace = browser.request(
        "POST",
        _upload_action_path(UUID(other.json()["id"]), project["id"], upload["id"], "parts/1"),
    )
    assert_error(wrong_workspace, status_code=404, code="NOT_FOUND")

    storage_upload_id = next(iter(store.upload_keys))
    store.put_multipart_part(upload_id=storage_upload_id, part_number=1, size_bytes=1024)
    body = {"parts": [{"partNumber": 1, "etag": "etag-1"}]}
    first = browser.request(
        "POST",
        _upload_action_path(workspace_id, project["id"], upload["id"], "complete"),
        json=body,
    )
    assert first.status_code == 200
    duplicate = browser.request(
        "POST",
        _upload_action_path(workspace_id, project["id"], upload["id"], "complete"),
        json=body,
    )
    assert_error(duplicate, status_code=409, code="CONFLICT")


@pytest.mark.integration
def test_multipart_upload_aborts_and_expiry_cleanup_addresses_only_recorded_object(
    engine: Engine,
) -> None:
    """Abort and expiry cleanup cannot broaden their object-store target beyond one record."""
    clock = Clock(NOW)
    store = FakeObjectStore(now=clock)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), object_store=store)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    project = _create_project(browser, workspace_id, key="cleanup-upload-project")
    upload = _create_upload(browser, workspace_id, project["id"])

    aborted = browser.request("DELETE", _upload_path(workspace_id, project["id"], upload["id"]))
    assert aborted.status_code == 204
    storage_upload_id = next(iter(store.upload_keys))
    assert store.aborted == [(storage_upload_id, store.upload_keys[storage_upload_id])]

    response = browser.request(
        "POST",
        _uploads_path(workspace_id, project["id"]),
        json={"filename": "expired.mp4", "contentType": "video/mp4", "contentLength": 1024},
    )
    expired_upload = response.json()
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE multipart_uploads "
                "SET created_at = :created, expires_at = :expired WHERE id = :id"
            ),
            {
                "created": NOW - timedelta(days=2),
                "expired": NOW - timedelta(seconds=1),
                "id": UUID(expired_upload["id"]),
            },
        )
    expired = browser.request(
        "POST",
        _upload_action_path(workspace_id, project["id"], expired_upload["id"], "parts/1"),
    )
    assert_error(expired, status_code=409, code="CONFLICT")
    expired_storage_upload_id = list(store.upload_keys)[-1]
    assert (
        expired_storage_upload_id,
        store.upload_keys[expired_storage_upload_id],
    ) in store.aborted


@pytest.mark.integration
def test_multipart_upload_enforces_declared_and_completed_object_lengths(engine: Engine) -> None:
    """The API rejects oversized declarations and object metadata that disagrees at completion."""
    clock = Clock(NOW)
    store = FakeObjectStore(now=clock)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), object_store=store)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    project = _create_project(browser, workspace_id, key="length-upload-project")

    over_cap = browser.request(
        "POST",
        _uploads_path(workspace_id, project["id"]),
        json={
            "filename": "large.mp4",
            "contentType": "video/mp4",
            "contentLength": MAX_UPLOAD_BYTES + 1,
        },
    )
    assert_error(over_cap, status_code=422, code="VALIDATION_ERROR")

    upload = _create_upload(browser, workspace_id, project["id"])
    storage_upload_id = next(iter(store.upload_keys))
    store.put_multipart_part(upload_id=storage_upload_id, part_number=1, size_bytes=1023)
    mismatch = browser.request(
        "POST",
        _upload_action_path(workspace_id, project["id"], upload["id"], "complete"),
        json={"parts": [{"partNumber": 1, "etag": "etag-1"}]},
    )
    assert_error(mismatch, status_code=422, code="VALIDATION_ERROR")


@pytest.mark.integration
def test_completion_canonicalizes_client_part_order_and_sanitizes_missing_part(
    engine: Engine,
) -> None:
    """Multipart completion must not leak provider errors for unordered or absent parts."""
    clock = Clock(NOW)
    store = CountingFakeObjectStore(now=clock)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), object_store=store)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    project = _create_project(browser, workspace_id, key="ordered-completion-project")
    upload = _create_upload(browser, workspace_id, project["id"])
    storage_upload_id = next(iter(store.upload_keys))
    store.put_multipart_part(upload_id=storage_upload_id, part_number=1, size_bytes=512)
    store.put_multipart_part(upload_id=storage_upload_id, part_number=2, size_bytes=512)

    ordered = browser.request(
        "POST",
        _upload_action_path(workspace_id, project["id"], upload["id"], "complete"),
        json={
            "parts": [
                {"partNumber": 2, "etag": "etag-2"},
                {"partNumber": 1, "etag": "etag-1"},
            ]
        },
    )
    assert ordered.status_code == 200
    assert store.completed_parts == [(1, 2)]

    invalid_upload = _create_upload(browser, workspace_id, project["id"])
    invalid = browser.request(
        "POST",
        _upload_action_path(workspace_id, project["id"], invalid_upload["id"], "complete"),
        json={"parts": [{"partNumber": 1, "etag": "missing-etag"}]},
    )
    assert_error(invalid, status_code=422, code="VALIDATION_ERROR")


@pytest.mark.integration
def test_create_upload_locks_active_project_against_a_concurrent_archive(engine: Engine) -> None:
    """A Project archive cannot pass active validation before its upload intent is durable."""
    clock = Clock(NOW)
    store = CreateGateFakeObjectStore(now=clock)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), object_store=store)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    user_id = UUID(browser.get("/api/v1/me").json()["id"])
    project = _create_project(browser, workspace_id, key="create-archive-race-project")
    project_id = UUID(project["id"])
    access = WorkspaceAccess(
        workspace_id=workspace_id,
        user_id=user_id,
        role=WorkspaceRole.OWNER,
        publishing_role_policy=PublishingRolePolicy.OWNER_ADMIN_EDITOR,
    )
    outcomes: Queue[str] = Queue()
    archive_done = Event()

    def create_in_transaction() -> None:
        """Create an upload while holding the Project lock through provider allocation and flush."""
        with Session(engine) as session:
            with session.begin():
                create_upload(
                    session,
                    store,
                    access=access,
                    project_id=project_id,
                    command=CreateUploadCommand(
                        filename="race.mp4", content_type="video/mp4", content_length=1024
                    ),
                    now=NOW,
                )
            outcomes.put("created")

    def archive_in_transaction() -> None:
        """Archive the same Project from a separate transaction after creation has locked it."""
        with Session(engine) as session:
            with session.begin():
                session.execute(
                    text("UPDATE projects SET archived_at = :now WHERE id = :project_id"),
                    {"now": NOW, "project_id": project_id},
                )
            archive_done.set()

    creator = Thread(target=create_in_transaction)
    creator.start()
    assert store.create_started.wait(timeout=3)
    archiver = Thread(target=archive_in_transaction)
    archiver.start()
    try:
        assert not archive_done.wait(timeout=0.3)
    finally:
        store.release_create.set()
    creator.join(timeout=5)
    archiver.join(timeout=5)

    assert not creator.is_alive()
    assert not archiver.is_alive()
    assert outcomes.get_nowait() == "created"
    with Session(engine) as session:
        upload_count = session.scalar(
            select(func.count())
            .select_from(MultipartUpload)
            .where(MultipartUpload.project_id == project_id)
        )
        archived_at = session.scalar(select(Project.archived_at).where(Project.id == project_id))
    assert upload_count == 1
    assert archived_at == NOW


@pytest.mark.integration
def test_archived_project_refuses_every_mutating_upload_transition_without_storage_calls(
    engine: Engine,
) -> None:
    """A soft-deleted Project must block every upload mutation before storage is touched."""
    clock = Clock(NOW)
    store = CountingFakeObjectStore(now=clock)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), object_store=store)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    project = _create_project(browser, workspace_id, key="archived-upload-project")
    upload = _create_upload(browser, workspace_id, project["id"])
    archived = browser.request(
        "DELETE", f"/api/v1/projects/{project['id']}?workspace_id={workspace_id}"
    )
    assert archived.status_code == 204

    sign = browser.request(
        "POST", _upload_action_path(workspace_id, project["id"], upload["id"], "parts/1")
    )
    complete = browser.request(
        "POST",
        _upload_action_path(workspace_id, project["id"], upload["id"], "complete"),
        json={"parts": [{"partNumber": 1, "etag": "etag-1"}]},
    )
    abort = browser.request("DELETE", _upload_path(workspace_id, project["id"], upload["id"]))

    assert_error(sign, status_code=404, code="NOT_FOUND")
    assert_error(complete, status_code=404, code="NOT_FOUND")
    assert_error(abort, status_code=404, code="NOT_FOUND")
    assert store.completions == []
    assert store.aborted == []


@pytest.mark.integration
def test_complete_and_abort_in_separate_transactions_perform_only_one_terminal_storage_operation(
    engine: Engine,
) -> None:
    """Row locking serializes racing terminal actions before either provider operation runs."""
    clock = Clock(NOW)
    store = CountingFakeObjectStore(now=clock, block_first_terminal_operation=True)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), object_store=store)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    user_id = UUID(browser.get("/api/v1/me").json()["id"])
    project = _create_project(browser, workspace_id, key="concurrent-upload-project")
    upload = _create_upload(browser, workspace_id, project["id"])
    storage_upload_id = next(iter(store.upload_keys))
    store.put_multipart_part(upload_id=storage_upload_id, part_number=1, size_bytes=1024)
    access = WorkspaceAccess(
        workspace_id=workspace_id,
        user_id=user_id,
        role=WorkspaceRole.OWNER,
        publishing_role_policy=PublishingRolePolicy.OWNER_ADMIN_EDITOR,
    )
    outcomes: Queue[str] = Queue()
    project_id = UUID(project["id"])
    upload_id = UUID(upload["id"])

    def complete_in_transaction(session: Session) -> None:
        """Attempt completion from one independent transaction at the common start barrier."""
        complete_upload(
            session,
            store,
            access=access,
            project_id=project_id,
            upload_id=upload_id,
            parts=[CompletedPart(part_number=1, etag="etag-1")],
            now=NOW,
        )

    def abort_in_transaction(session: Session) -> None:
        """Attempt abort from a separate transaction at the same common start barrier."""
        abort_upload(
            session,
            store,
            access=access,
            project_id=project_id,
            upload_id=upload_id,
            now=NOW,
        )

    complete_thread = Thread(
        target=_run_terminal_attempt, args=(engine, outcomes, complete_in_transaction)
    )
    abort_thread = Thread(
        target=_run_terminal_attempt, args=(engine, outcomes, abort_in_transaction)
    )
    complete_thread.start()
    assert store.first_terminal_started.wait(timeout=3)
    abort_thread.start()
    try:
        assert not store.second_terminal_started.wait(timeout=0.3)
    finally:
        store.release_first_terminal.set()
    complete_thread.join(timeout=5)
    abort_thread.join(timeout=5)

    assert not complete_thread.is_alive()
    assert not abort_thread.is_alive()
    assert sorted(outcomes.get_nowait() for _ in range(2)) == ["conflict", "success"]
    assert len(store.completions) + len(store.aborted) == 1
    with Session(engine) as session:
        status = session.scalar(
            select(MultipartUpload.status).where(MultipartUpload.id == upload_id)
        )
    assert status in (MultipartUploadStatus.ABORTED, MultipartUploadStatus.COMPLETED)
    assert (status == MultipartUploadStatus.COMPLETED) == bool(store.completions)


def _run_terminal_attempt(
    engine: Engine,
    outcomes: Queue[str],
    operation: Callable[[Session], None],
) -> None:
    """Run one terminal use case in its own transaction and record its public outcome type."""
    with Session(engine) as session:
        try:
            with session.begin():
                operation(session)
        except UploadConflictError:
            outcomes.put("conflict")
        else:
            outcomes.put("success")


@pytest.mark.integration
def test_multipart_upload_routes_use_real_local_minio_for_signed_part_and_download(
    engine: Engine,
) -> None:
    """The production adapter exercises every Task 6 upload transition against MinIO."""
    clock = Clock(NOW)
    bucket = "clipah-multipart-tests"
    store = S3ObjectStore(
        bucket=bucket,
        endpoint_url="http://127.0.0.1:59001",
        access_key_id="clipah_local",
        secret_access_key="clipah_local_secret",
        now=clock,
    )
    _ensure_local_minio_bucket(bucket)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), object_store=store)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    project = _create_project(browser, workspace_id, key=f"minio-{uuid4().hex}")
    first_part = b"x" * (5 * 1024 * 1024)
    second_part = b"y" * 1024
    upload = _create_upload(
        browser, workspace_id, project["id"], content_length=len(first_part) + len(second_part)
    )
    key = source_upload_key(
        workspace_id=workspace_id,
        project_id=UUID(project["id"]),
        object_id=UUID(upload["id"]),
        client_filename="display-only.mp4",
    )
    try:
        part_one = browser.request(
            "POST", _upload_action_path(workspace_id, project["id"], upload["id"], "parts/1")
        )
        part_two = browser.request(
            "POST", _upload_action_path(workspace_id, project["id"], upload["id"], "parts/2")
        )
        assert part_one.status_code == 200
        assert part_two.status_code == 200

        uploaded_one = httpx.put(str(part_one.json()["url"]), content=first_part, timeout=5)
        uploaded_two = httpx.put(str(part_two.json()["url"]), content=second_part, timeout=5)
        assert uploaded_one.status_code == 200
        assert uploaded_two.status_code == 200
        completed = browser.request(
            "POST",
            _upload_action_path(workspace_id, project["id"], upload["id"], "complete"),
            json={
                "parts": [
                    {"partNumber": 2, "etag": uploaded_two.headers["etag"]},
                    {"partNumber": 1, "etag": uploaded_one.headers["etag"]},
                ]
            },
        )
        assert completed.status_code == 200
        assert completed.json()["downloadExpiresAt"] == (NOW + timedelta(minutes=5)).isoformat()
        downloaded = httpx.get(str(completed.json()["downloadUrl"]), timeout=5)
        assert downloaded.status_code == 200
        assert downloaded.content == first_part + second_part
        duplicate = browser.request(
            "POST",
            _upload_action_path(workspace_id, project["id"], upload["id"], "complete"),
            json={"parts": [{"partNumber": 1, "etag": uploaded_one.headers["etag"]}]},
        )
        assert_error(duplicate, status_code=409, code="CONFLICT")

        invalid = _create_upload(browser, workspace_id, project["id"])
        invalid_part = browser.request(
            "POST", _upload_action_path(workspace_id, project["id"], invalid["id"], "parts/1")
        )
        invalid_put = httpx.put(str(invalid_part.json()["url"]), content=b"invalid", timeout=5)
        assert invalid_put.status_code == 200
        invalid_completion = browser.request(
            "POST",
            _upload_action_path(workspace_id, project["id"], invalid["id"], "complete"),
            json={"parts": [{"partNumber": 1, "etag": "not-an-etag"}]},
        )
        assert_error(invalid_completion, status_code=422, code="VALIDATION_ERROR")
        assert (
            browser.request(
                "DELETE", _upload_path(workspace_id, project["id"], invalid["id"])
            ).status_code
            == 204
        )

        aborted = _create_upload(browser, workspace_id, project["id"])
        assert (
            browser.request(
                "DELETE", _upload_path(workspace_id, project["id"], aborted["id"])
            ).status_code
            == 204
        )

        expired = _create_upload(browser, workspace_id, project["id"])
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE multipart_uploads "
                    "SET created_at = :created, expires_at = :expired WHERE id = :id"
                ),
                {
                    "created": NOW - timedelta(days=2),
                    "expired": NOW - timedelta(seconds=1),
                    "id": UUID(expired["id"]),
                },
            )
        expired_response = browser.request(
            "POST", _upload_action_path(workspace_id, project["id"], expired["id"], "parts/1")
        )
        assert_error(expired_response, status_code=409, code="CONFLICT")

        wrong_workspace = browser.request(
            "POST", "/api/v1/workspaces", json={"name": "MinIO other", "kind": "team"}
        )
        assert wrong_workspace.status_code == 201
        wrong_response = browser.request(
            "POST",
            _upload_action_path(
                UUID(wrong_workspace.json()["id"]), project["id"], upload["id"], "parts/1"
            ),
        )
        assert_error(wrong_response, status_code=404, code="NOT_FOUND")
        invalid_part_number = browser.request(
            "POST", _upload_action_path(workspace_id, project["id"], upload["id"], "parts/0")
        )
        assert_error(invalid_part_number, status_code=422, code="VALIDATION_ERROR")
    finally:
        store.delete_object(key=key)


class UnavailableObjectStore(FakeObjectStore):
    """Fail every provider operation the way an unreachable object store does."""

    def create_multipart_upload(self, *, key: str, content_type: str) -> object:
        """Refuse to allocate an upload, as a store that cannot be reached would."""
        del key, content_type
        raise ObjectStoreUnavailableError("object store upload unavailable")

    def sign_upload_part(self, *, upload_id: str, key: str, part_number: int) -> object:
        """Refuse to sign a part, as a store that cannot be reached would."""
        del upload_id, key, part_number
        raise ObjectStoreUnavailableError("object store signing unavailable")


@pytest.mark.integration
def test_an_unreachable_object_store_is_a_service_outage_rather_than_a_defect(
    engine: Engine, clean_database: None
) -> None:
    """A storage outage is transient, so it must say so rather than report a bug.

    This is drill 11 in `docs/operations/recovery.md`. An internal-error envelope would
    tell a browser, and the member reading it, that the request can never succeed; the
    truth is that it will succeed once the store is reachable again. Nothing durable may
    be written for bytes that were never allocated.
    """
    del clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(
        clock, StubGoogleProvider(clock), object_store=UnavailableObjectStore(now=clock)
    )
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    project = _create_project(browser, workspace_id, key="storage-outage")

    response = browser.request(
        "POST",
        f"/api/v1/projects/{project['id']}/uploads?workspace_id={workspace_id}",
        json={"filename": "drill.mp4", "contentType": "video/mp4", "contentLength": 1_048_576},
    )

    assert_error(response, status_code=503, code="SERVICE_UNAVAILABLE")
    with Session(engine) as session:
        stored = session.scalar(select(func.count()).select_from(MultipartUpload))
    assert stored == 0
