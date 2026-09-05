"""Source connections end to end: the flag, the consent, the tenancy, and the leases.

These tests are about a credential that belongs to a person, so they are mostly about
refusals. While the feature is off the routes answer exactly like routes that do not
exist. When it is on, a connection can only be created with both confirmations, can only
be seen and revoked inside the Workspace that owns it, expires within the window this
system will hold one, and stops being leasable the moment it is revoked.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select, text

from clipah.api.routes.source_connections import MAX_ENCODED_COOKIE_CHARS
from clipah.assets.storage import StoredObject
from clipah.assets.youtube import NormalizedYouTubeUrl
from clipah.db import RuntimeRole, session_scope
from clipah.jobs.models import JobContext, TerminalJobError
from clipah.jobs.source_import_task import SourceImportStageRunner
from clipah.models import (
    Job,
    JobStatus,
    Project,
    ProjectStatus,
    SourceConnection,
    SourceConnectionSecret,
    SourceConnectionStatus,
    SourceImport,
    SourceImportStatus,
    SourceKind,
)
from clipah.source_connectors.connections import (
    MAX_CONNECTION_TTL,
    SOURCE_CONNECTION_EXPIRED,
    SOURCE_CONNECTION_REVOKED,
    SourceConnectionNotFoundError,
    SourceConnectionService,
    SourceConnectionUnusableError,
)
from clipah.source_connectors.cookies import REQUIRED_COOKIE_NAMES
from clipah.source_connectors.secrets import local_secret_store
from harness import NOW, Browser, Clock, StubGoogleProvider, assert_error, build_app, sign_in
from support import runtime_settings

ENCRYPTION_KEY = "a-thirty-two-character-secret-key-for-tests"
_SOURCE = NormalizedYouTubeUrl(
    canonical_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    video_id="dQw4w9WgXcQ",
    host="www.youtube.com",
    addresses=frozenset(),
)
COOKIE_VALUE = "the-actual-session-credential"
LIST_PATH = "/api/v1/source-connections"


def jar(*, expiry: int, value: str = COOKIE_VALUE) -> bytes:
    """One cookie jar carrying every cookie YouTube authentication needs."""
    rows = [
        "\t".join([".youtube.com", "TRUE", "/", "TRUE", str(expiry), name, value])
        for name in sorted(REQUIRED_COOKIE_NAMES)
    ]
    return "\n".join(["# Netscape HTTP Cookie File", *rows, ""]).encode("utf-8")


def payload(*, expiry: int | None = None, consent: bool = True, owned: bool = True) -> Any:
    """The body that creates one connection."""
    return {
        "cookiesBase64": base64.b64encode(
            jar(expiry=expiry or int((NOW + timedelta(days=30)).timestamp()))
        ).decode(),
        "consentAcknowledged": consent,
        "ownershipAttested": owned,
    }


def enabled_app(clock: Clock, provider: StubGoogleProvider) -> Any:
    """One application with the feature explicitly enabled for this test."""
    return build_app(
        clock,
        provider,
        authenticated_source_import_enabled=True,
        secret_encryption_key=ENCRYPTION_KEY,
    )


def signed_in(app: Any) -> Browser:
    """One signed-in browser against an application under test."""
    browser = Browser(app[0])
    sign_in(browser, app[1])
    return browser


def workspace_of(browser: Browser) -> UUID:
    """The Workspace this member landed in when they signed in."""
    return UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])


@pytest.mark.integration
def test_every_route_is_invisible_while_the_feature_is_off(
    engine: Engine, clean_database: None
) -> None:
    """A member may not discover a disabled feature by probing for its endpoints."""
    del clean_database, engine
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), secret_encryption_key=ENCRYPTION_KEY)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = workspace_of(browser)

    listed = browser.get(f"{LIST_PATH}?workspace_id={workspace_id}")
    created = browser.request("POST", f"{LIST_PATH}?workspace_id={workspace_id}", json=payload())
    revoked = browser.request("DELETE", f"{LIST_PATH}/{uuid4()}?workspace_id={workspace_id}")

    for response in (listed, created, revoked):
        assert_error(response, status_code=404, code="NOT_FOUND")


@pytest.mark.integration
def test_a_connection_is_created_only_with_both_confirmations(
    engine: Engine, clean_database: None
) -> None:
    """Consent to the risk and a claim of ownership are the terms this feature exists on."""
    del clean_database, engine
    clock = Clock(NOW)
    app = enabled_app(clock, StubGoogleProvider(clock))
    browser = signed_in(app)
    workspace_id = workspace_of(browser)

    without_consent = browser.request(
        "POST", f"{LIST_PATH}?workspace_id={workspace_id}", json=payload(consent=False)
    )
    without_ownership = browser.request(
        "POST", f"{LIST_PATH}?workspace_id={workspace_id}", json=payload(owned=False)
    )

    for response in (without_consent, without_ownership):
        assert_error(response, status_code=422, code="SOURCE_CONNECTION_CONSENT_REQUIRED")


@pytest.mark.integration
def test_a_created_connection_is_described_without_any_part_of_the_credential(
    engine: Engine, clean_database: None
) -> None:
    """A member sees who authorized it and when it ends, and never what it holds."""
    del clean_database
    clock = Clock(NOW)
    app = enabled_app(clock, StubGoogleProvider(clock))
    browser = signed_in(app)
    workspace_id = workspace_of(browser)

    created = browser.request("POST", f"{LIST_PATH}?workspace_id={workspace_id}", json=payload())

    assert created.status_code == 201
    body = created.json()
    assert body["provider"] == "youtube"
    assert body["kind"] == "cookie"
    assert body["status"] == "active"
    assert body["domainScope"] == ".youtube.com,.google.com"
    assert COOKIE_VALUE not in created.text
    with engine.begin() as connection:
        stored = (
            connection.execute(text("SELECT * FROM source_connection_secrets")).mappings().all()
        )
    assert len(stored) == 1
    assert COOKIE_VALUE.encode() not in bytes(stored[0]["ciphertext"])


@pytest.mark.integration
def test_a_connection_never_outlives_the_window_this_system_will_hold_one(
    engine: Engine, clean_database: None
) -> None:
    """A cookie good for a year is still only held for as long as the policy allows."""
    del clean_database, engine
    clock = Clock(NOW)
    app = enabled_app(clock, StubGoogleProvider(clock))
    browser = signed_in(app)
    workspace_id = workspace_of(browser)

    created = browser.request(
        "POST",
        f"{LIST_PATH}?workspace_id={workspace_id}",
        json=payload(expiry=int((NOW + timedelta(days=365)).timestamp())),
    )

    expires_at = datetime.fromisoformat(created.json()["expiresAt"])
    assert expires_at <= NOW + MAX_CONNECTION_TTL


@pytest.mark.integration
def test_a_jar_for_another_site_is_refused_by_its_own_code(
    engine: Engine, clean_database: None
) -> None:
    """The refusal names what was wrong with the file and nothing about its contents."""
    del clean_database, engine
    clock = Clock(NOW)
    app = enabled_app(clock, StubGoogleProvider(clock))
    browser = signed_in(app)
    workspace_id = workspace_of(browser)
    unrelated = "\n".join(
        [
            "# Netscape HTTP Cookie File",
            "\t".join([".example.com", "TRUE", "/", "TRUE", "1900000000", "SID", COOKIE_VALUE]),
            "",
        ]
    ).encode()

    response = browser.request(
        "POST",
        f"{LIST_PATH}?workspace_id={workspace_id}",
        json={
            "cookiesBase64": base64.b64encode(unrelated).decode(),
            "consentAcknowledged": True,
            "ownershipAttested": True,
        },
    )

    assert_error(response, status_code=422, code="COOKIE_FILE_INSUFFICIENT")


@pytest.mark.integration
def test_another_workspace_can_neither_see_nor_revoke_this_connection(
    engine: Engine, clean_database: None
) -> None:
    """A credential belongs to one Workspace, and to nobody who guesses its identifier."""
    del clean_database, engine
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    app = enabled_app(clock, provider)
    owner = signed_in(app)
    owner_workspace = workspace_of(owner)
    created = owner.request("POST", f"{LIST_PATH}?workspace_id={owner_workspace}", json=payload())
    connection_id = created.json()["id"]

    provider.identify(subject="connection-stranger", email="stranger@example.com", name="S")
    stranger = Browser(app[0])
    sign_in(stranger, app[1])
    stranger_workspace = workspace_of(stranger)

    listed = stranger.get(f"{LIST_PATH}?workspace_id={stranger_workspace}")
    guessed = stranger.request(
        "DELETE", f"{LIST_PATH}/{connection_id}?workspace_id={stranger_workspace}"
    )
    missing = stranger.request("DELETE", f"{LIST_PATH}/{uuid4()}?workspace_id={stranger_workspace}")

    assert listed.json()["connections"] == []
    assert_error(guessed, status_code=404, code="NOT_FOUND")
    assert_error(missing, status_code=404, code="NOT_FOUND")
    assert guessed.json()["error"]["message"] == missing.json()["error"]["message"]


@pytest.mark.integration
def test_revoking_a_connection_destroys_the_credential_it_was_holding(
    engine: Engine, clean_database: None
) -> None:
    """Revocation is not a label: there is nothing left to lease afterwards."""
    del clean_database
    clock = Clock(NOW)
    app = enabled_app(clock, StubGoogleProvider(clock))
    browser = signed_in(app)
    workspace_id = workspace_of(browser)
    connection_id = browser.request(
        "POST", f"{LIST_PATH}?workspace_id={workspace_id}", json=payload()
    ).json()["id"]

    revoked = browser.request("DELETE", f"{LIST_PATH}/{connection_id}?workspace_id={workspace_id}")

    assert revoked.status_code == 204
    listed = browser.get(f"{LIST_PATH}?workspace_id={workspace_id}").json()["connections"]
    assert listed[0]["status"] == "revoked"
    assert listed[0]["revokedAt"] is not None
    with engine.begin() as connection:
        remaining = connection.execute(text("SELECT count(*) FROM source_connection_secrets"))
    assert remaining.scalar_one() == 0


@pytest.mark.integration
def test_an_anonymous_caller_never_reaches_a_connection(
    engine: Engine, clean_database: None
) -> None:
    """A credential list is Workspace data, and an anonymous caller has no Workspace."""
    del clean_database, engine
    clock = Clock(NOW)
    app = enabled_app(clock, StubGoogleProvider(clock))
    member = signed_in(app)
    workspace_id = workspace_of(member)

    anonymous = Browser(app[0])
    response = anonymous.get(f"{LIST_PATH}?workspace_id={workspace_id}")

    assert_error(response, status_code=401, code="UNAUTHENTICATED")


@pytest.mark.integration
def test_a_lease_is_refused_once_the_connection_is_revoked_or_expired(
    engine: Engine, clean_database: None
) -> None:
    """A worker asks for a credential by identity, and the answer is a stable refusal."""
    del clean_database
    clock = Clock(NOW)
    app = enabled_app(clock, StubGoogleProvider(clock))
    browser = signed_in(app)
    workspace_id = workspace_of(browser)
    connection_id = UUID(
        browser.request("POST", f"{LIST_PATH}?workspace_id={workspace_id}", json=payload()).json()[
            "id"
        ]
    )
    with session_scope(
        settings=runtime_settings(RuntimeRole.WORKER),
        runtime_role=RuntimeRole.WORKER,
        workspace_id=workspace_id,
        user_id=uuid4(),
    ) as session:
        service = SourceConnectionService(session, store=local_secret_store(ENCRYPTION_KEY))
        leased = service.lease(
            workspace_id=workspace_id, connection_id=connection_id, job_id=uuid4(), now=NOW
        )
        assert COOKIE_VALUE.encode() in leased.plaintext(now=NOW)

        with pytest.raises(SourceConnectionUnusableError) as expired:
            service.lease(
                workspace_id=workspace_id,
                connection_id=connection_id,
                job_id=uuid4(),
                now=NOW + timedelta(days=8),
            )
        assert expired.value.code == SOURCE_CONNECTION_EXPIRED

        with pytest.raises(SourceConnectionNotFoundError):
            service.lease(
                workspace_id=uuid4(), connection_id=connection_id, job_id=uuid4(), now=NOW
            )

    browser.request("DELETE", f"{LIST_PATH}/{connection_id}?workspace_id={workspace_id}")

    with session_scope(
        settings=runtime_settings(RuntimeRole.WORKER),
        runtime_role=RuntimeRole.WORKER,
        workspace_id=workspace_id,
        user_id=uuid4(),
    ) as session:
        service = SourceConnectionService(session, store=local_secret_store(ENCRYPTION_KEY))
        with pytest.raises(SourceConnectionUnusableError) as revoked:
            service.lease(
                workspace_id=workspace_id, connection_id=connection_id, job_id=uuid4(), now=NOW
            )
        assert revoked.value.code == SOURCE_CONNECTION_REVOKED


@pytest.mark.integration
def test_the_api_role_may_write_and_destroy_a_credential_and_never_read_one(
    engine: Engine, clean_database: None
) -> None:
    """Least privilege is the reason the secret lives in its own table."""
    del clean_database
    clock = Clock(NOW)
    app = enabled_app(clock, StubGoogleProvider(clock))
    browser = signed_in(app)
    workspace_id = workspace_of(browser)
    browser.request("POST", f"{LIST_PATH}?workspace_id={workspace_id}", json=payload())
    with session_scope(
        settings=runtime_settings(), workspace_id=workspace_id, user_id=uuid4()
    ) as session:
        with pytest.raises(Exception) as refused:
            session.execute(select(SourceConnectionSecret)).all()
        assert "permission denied" in str(refused.value).lower()

    with session_scope(
        settings=runtime_settings(RuntimeRole.WORKER),
        runtime_role=RuntimeRole.WORKER,
        workspace_id=workspace_id,
        user_id=uuid4(),
    ) as session:
        rows = session.execute(select(SourceConnectionSecret)).all()
    assert len(rows) == 1


@pytest.mark.integration
def test_a_connection_reports_itself_expired_the_moment_its_window_closes(
    engine: Engine, clean_database: None
) -> None:
    """A member should see that a connection is finished without waiting for a sweep."""
    del clean_database
    clock = Clock(NOW)
    app = enabled_app(clock, StubGoogleProvider(clock))
    browser = signed_in(app)
    workspace_id = workspace_of(browser)
    browser.request(
        "POST",
        f"{LIST_PATH}?workspace_id={workspace_id}",
        json=payload(expiry=int((NOW + timedelta(hours=6)).timestamp())),
    )

    # Past the connection's own window, and well inside the member's Session.
    clock.advance(timedelta(hours=7))
    response = browser.get(f"{LIST_PATH}?workspace_id={workspace_id}")
    assert response.status_code == 200, response.text
    listed = response.json()["connections"]

    assert listed[0]["status"] == "expired"
    with engine.begin() as connection:
        stored = connection.execute(
            select(SourceConnection.status).select_from(SourceConnection)
        ).scalar_one()
    assert stored == SourceConnectionStatus.ACTIVE


@pytest.mark.unit
def test_source_connection_openapi_declares_strict_response_schemas() -> None:
    """A generated client needs the real connection fields, not an arbitrary dictionary."""
    clock = Clock(NOW)
    app, _, _ = build_app(
        clock,
        StubGoogleProvider(clock),
        authenticated_source_import_enabled=True,
        secret_encryption_key=ENCRYPTION_KEY,
    )
    paths = app.openapi()["paths"]

    listed = paths["/api/v1/source-connections"]["get"]["responses"]["200"]
    created = paths["/api/v1/source-connections"]["post"]["responses"]["201"]

    assert listed["content"]["application/json"]["schema"]["$ref"].endswith(
        "/SourceConnectionsResponse"
    )
    assert created["content"]["application/json"]["schema"]["$ref"].endswith(
        "/SourceConnectionResponse"
    )


@pytest.mark.integration
def test_a_worker_imports_with_the_leased_jar_and_leaves_nothing_behind(
    engine: Engine, clean_database: None
) -> None:
    """The credential reaches the provider as a file, and the file dies with the attempt."""
    del clean_database
    clock = Clock(NOW)
    app = enabled_app(clock, StubGoogleProvider(clock))
    browser = signed_in(app)
    workspace_id = workspace_of(browser)
    connection_id = UUID(
        browser.request("POST", f"{LIST_PATH}?workspace_id={workspace_id}", json=payload()).json()[
            "id"
        ]
    )
    context, _ = _seed_authenticated_import(engine, workspace_id, connection_id)
    importer = _RecordingImporter()
    runner = SourceImportStageRunner(
        importer_factory=lambda _: importer,
        validator=lambda _: _SOURCE,
        secret_store_factory=lambda _: local_secret_store(ENCRYPTION_KEY),
    )

    runner(context)
    runner(context)

    assert len(importer.jars) == 2
    assert all(COOKIE_VALUE.encode() in jar for jar in importer.jars)
    assert all(path.name == "cookies.txt" for path in importer.paths)
    assert not any(path.exists() for path in importer.paths)


@pytest.mark.integration
def test_a_revoked_connection_ends_the_import_rather_than_falling_back(
    engine: Engine, clean_database: None
) -> None:
    """An authenticated import may not quietly become a public one when the loan ends."""
    del clean_database
    clock = Clock(NOW)
    app = enabled_app(clock, StubGoogleProvider(clock))
    browser = signed_in(app)
    workspace_id = workspace_of(browser)
    connection_id = UUID(
        browser.request("POST", f"{LIST_PATH}?workspace_id={workspace_id}", json=payload()).json()[
            "id"
        ]
    )
    context, _ = _seed_authenticated_import(engine, workspace_id, connection_id)
    browser.request("DELETE", f"{LIST_PATH}/{connection_id}?workspace_id={workspace_id}")
    importer = _RecordingImporter()
    runner = SourceImportStageRunner(
        importer_factory=lambda _: importer,
        validator=lambda _: _SOURCE,
        secret_store_factory=lambda _: local_secret_store(ENCRYPTION_KEY),
    )

    with pytest.raises(TerminalJobError, match=rf"^{SOURCE_CONNECTION_REVOKED}$"):
        runner(context)

    assert importer.jars == []


@pytest.mark.integration
def test_naming_a_connection_is_refused_while_the_feature_is_off(
    engine: Engine, clean_database: None
) -> None:
    """A member cannot reach an authenticated import through the public import route."""
    del clean_database, engine
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), secret_encryption_key=ENCRYPTION_KEY)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = workspace_of(browser)
    created = browser.request(
        "POST",
        f"/api/v1/projects?workspace_id={workspace_id}",
        headers={"Idempotency-Key": "connection-project"},
        json={"name": "Connection project", "sourceKind": "public_url"},
    )
    project_id = created.json()["id"]

    response = browser.request(
        "POST",
        f"/api/v1/projects/{project_id}/youtube-imports?workspace_id={workspace_id}",
        headers={"Idempotency-Key": "authenticated-while-off"},
        json={
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "sourceConnectionId": str(uuid4()),
        },
    )

    assert_error(response, status_code=404, code="NOT_FOUND")


class _RecordingImporter:
    """A provider that reads the jar it was handed and reports one successful import."""

    def __init__(self) -> None:
        """Start with nothing observed."""
        self.jars: list[bytes] = []
        self.paths: list[Path] = []

    def import_source(
        self,
        source: NormalizedYouTubeUrl,
        *,
        workspace: Path,
        object_key: str,
        cancellation_check: Any,
        cookie_file: Path | None = None,
    ) -> StoredObject:
        """Read the credential the way yt-dlp would, then answer as one stored object."""
        del source, cancellation_check
        if cookie_file is not None:
            self.jars.append(cookie_file.read_bytes())
            self.paths.append(cookie_file)
        del workspace
        return StoredObject(
            key=object_key,
            content_type="video/mp4",
            content_length=1234,
            sha256=b"x" * 32,
            duration_ms=42_000,
        )


def _seed_authenticated_import(
    engine: Engine, workspace_id: UUID, connection_id: UUID
) -> tuple[JobContext, UUID]:
    """Create the running Job and source row one authenticated import would have."""
    now = NOW
    with engine.begin() as connection:
        user_id = connection.execute(
            text("SELECT authorized_by_user_id FROM source_connections WHERE id = :id"),
            {"id": connection_id},
        ).scalar_one()
        project_id = uuid4()
        connection.execute(
            Project.__table__.insert().values(
                id=project_id,
                workspace_id=workspace_id,
                created_by_user_id=user_id,
                name="Authenticated import",
                status=ProjectStatus.CREATED,
                source_kind=SourceKind.AUTHENTICATED_SOURCE,
                created_at=now,
                updated_at=now,
            )
        )
        job_id = uuid4()
        source_import_id = uuid4()
        connection.execute(
            Job.__table__.insert().values(
                id=job_id,
                workspace_id=workspace_id,
                project_id=project_id,
                kind="source_import",
                status=JobStatus.RUNNING,
                stage="queued",
                progress=0,
                attempt=1,
                idempotency_key=f"authenticated-{job_id}",
            )
        )
        connection.execute(
            SourceImport.__table__.insert().values(
                id=source_import_id,
                workspace_id=workspace_id,
                project_id=project_id,
                normalized_source_url=_SOURCE.canonical_url,
                source_video_id=_SOURCE.video_id,
                status=SourceImportStatus.QUEUED,
                job_id=job_id,
                source_connection_id=connection_id,
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
        source_import_id,
    )


@pytest.mark.integration
def test_the_browser_is_told_whether_this_deployment_has_the_feature_at_all(
    engine: Engine, clean_database: None
) -> None:
    """The consent dialog may only be offered where the server would actually accept it."""
    del clean_database, engine
    clock = Clock(NOW)
    off_app, off_flow, _ = build_app(clock, StubGoogleProvider(clock))
    off = Browser(off_app)
    sign_in(off, off_flow)

    on_app = enabled_app(clock, StubGoogleProvider(clock))
    on = signed_in(on_app)

    assert off.get("/api/v1/me").json()["capabilities"]["authenticatedYoutubeImport"] is False
    assert on.get("/api/v1/me").json()["capabilities"]["authenticatedYoutubeImport"] is True


@pytest.mark.integration
def test_the_capability_stays_off_without_the_key_that_would_protect_a_credential(
    engine: Engine, clean_database: None
) -> None:
    """A deployment that cannot encrypt a jar must not invite anybody to upload one."""
    del clean_database, engine
    clock = Clock(NOW)
    app, flow, _ = build_app(
        clock, StubGoogleProvider(clock), authenticated_source_import_enabled=True
    )
    browser = Browser(app)
    sign_in(browser, flow)

    assert browser.get("/api/v1/me").json()["capabilities"]["authenticatedYoutubeImport"] is False


@pytest.mark.integration
def test_an_upload_that_is_not_a_readable_body_is_refused_before_it_is_parsed(
    engine: Engine, clean_database: None
) -> None:
    """A body that is not base64, or is larger than any jar, never reaches the parser."""
    del clean_database, engine
    clock = Clock(NOW)
    app = enabled_app(clock, StubGoogleProvider(clock))
    browser = signed_in(app)
    workspace_id = workspace_of(browser)

    oversized = browser.request(
        "POST",
        f"{LIST_PATH}?workspace_id={workspace_id}",
        json={
            "cookiesBase64": "A" * (MAX_ENCODED_COOKIE_CHARS + 4),
            "consentAcknowledged": True,
            "ownershipAttested": True,
        },
    )
    unreadable = browser.request(
        "POST",
        f"{LIST_PATH}?workspace_id={workspace_id}",
        json={
            "cookiesBase64": "not base64 at all!!",
            "consentAcknowledged": True,
            "ownershipAttested": True,
        },
    )

    assert_error(oversized, status_code=422, code="COOKIE_FILE_TOO_LARGE")
    assert_error(unreadable, status_code=422, code="COOKIE_FILE_MALFORMED")


@pytest.mark.integration
def test_a_deployment_without_key_material_refuses_to_hold_a_credential(
    engine: Engine, clean_database: None
) -> None:
    """Storing a jar this deployment cannot encrypt would be worse than refusing it."""
    del clean_database, engine
    clock = Clock(NOW)
    app, flow, _ = build_app(
        clock, StubGoogleProvider(clock), authenticated_source_import_enabled=True
    )
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = workspace_of(browser)

    response = browser.request("POST", f"{LIST_PATH}?workspace_id={workspace_id}", json=payload())

    assert_error(response, status_code=503, code="SERVICE_UNAVAILABLE")


@pytest.mark.integration
def test_a_connection_whose_material_is_gone_is_refused_as_revoked(
    engine: Engine, clean_database: None
) -> None:
    """The material is destroyed on purpose, so its absence is a revocation, not a bug."""
    del clean_database
    clock = Clock(NOW)
    app = enabled_app(clock, StubGoogleProvider(clock))
    browser = signed_in(app)
    workspace_id = workspace_of(browser)
    connection_id = UUID(
        browser.request("POST", f"{LIST_PATH}?workspace_id={workspace_id}", json=payload()).json()[
            "id"
        ]
    )
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM source_connection_secrets"))

    with session_scope(
        settings=runtime_settings(RuntimeRole.WORKER),
        runtime_role=RuntimeRole.WORKER,
        workspace_id=workspace_id,
        user_id=uuid4(),
    ) as session:
        service = SourceConnectionService(session, store=local_secret_store(ENCRYPTION_KEY))
        with pytest.raises(SourceConnectionUnusableError) as refusal:
            service.lease(
                workspace_id=workspace_id, connection_id=connection_id, job_id=uuid4(), now=NOW
            )

    assert refusal.value.code == SOURCE_CONNECTION_REVOKED


@pytest.mark.integration
def test_a_worker_without_key_material_ends_the_import_rather_than_guessing(
    engine: Engine, clean_database: None
) -> None:
    """A worker that cannot open a credential has not failed transiently."""
    del clean_database
    clock = Clock(NOW)
    app = enabled_app(clock, StubGoogleProvider(clock))
    browser = signed_in(app)
    workspace_id = workspace_of(browser)
    connection_id = UUID(
        browser.request("POST", f"{LIST_PATH}?workspace_id={workspace_id}", json=payload()).json()[
            "id"
        ]
    )
    context, _ = _seed_authenticated_import(engine, workspace_id, connection_id)

    def refuse(_: Any) -> Any:
        raise ValueError("no secret encryption key is configured")

    runner = SourceImportStageRunner(
        importer_factory=lambda _: _RecordingImporter(),
        validator=lambda _: _SOURCE,
        secret_store_factory=refuse,
    )

    with pytest.raises(TerminalJobError, match=r"^SOURCE_CONNECTION_UNAVAILABLE$"):
        runner(context)


@pytest.mark.integration
def test_the_database_refuses_to_orphan_the_connection_an_import_names(
    engine: Engine, clean_database: None
) -> None:
    """A queued import always has the credential it was admitted with, or no import at all."""
    del clean_database
    clock = Clock(NOW)
    app = enabled_app(clock, StubGoogleProvider(clock))
    browser = signed_in(app)
    workspace_id = workspace_of(browser)
    connection_id = UUID(
        browser.request("POST", f"{LIST_PATH}?workspace_id={workspace_id}", json=payload()).json()[
            "id"
        ]
    )
    _seed_authenticated_import(engine, workspace_id, connection_id)

    with pytest.raises(Exception) as refused, engine.begin() as connection:
        connection.execute(text("DELETE FROM source_connection_secrets"))
        connection.execute(text("DELETE FROM source_connections"))

    assert "foreign key" in str(refused.value).lower()
