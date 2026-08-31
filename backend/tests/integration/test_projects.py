"""Integration contracts for Workspace-scoped Project routes."""

from __future__ import annotations

from datetime import timedelta
from threading import Barrier, Thread
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from clipah.models import WorkspaceRole
from clipah.projects.use_cases import ProjectValidationError, normalize_project_name
from harness import NOW, Browser, Clock, StubGoogleProvider, assert_error, build_app, sign_in


def _workspace_id(browser: Browser) -> UUID:
    """Return the signed-in User's initial personal Workspace identifier."""
    response = browser.get("/api/v1/workspaces")
    assert response.status_code == 200
    return UUID(response.json()["workspaces"][0]["id"])


def _projects_path(workspace_id: UUID) -> str:
    """Build a Project collection URL with its authorized Workspace selection."""
    return f"/api/v1/projects?workspace_id={workspace_id}"


def _project_path(workspace_id: UUID, project_id: str) -> str:
    """Build a Project item URL with its authorized Workspace selection."""
    return f"/api/v1/projects/{project_id}?workspace_id={workspace_id}"


def _restore_path(workspace_id: UUID, project_id: str) -> str:
    """Build a Project recovery URL with its authorized Workspace selection."""
    return f"/api/v1/projects/{project_id}/restore?workspace_id={workspace_id}"


def _create_project(browser: Browser, workspace_id: UUID, name: str, *, key: str) -> dict[str, str]:
    """Create one upload-backed Project through the public API."""
    response = browser.request(
        "POST",
        _projects_path(workspace_id),
        headers={"Idempotency-Key": key},
        json={"name": name, "sourceKind": "upload"},
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.integration
def test_projects_are_created_inside_the_authorized_workspace(engine: Engine) -> None:
    """A Project must belong to the Workspace a member selected and authorized."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)

    project = _create_project(
        browser,
        workspace_id,
        f"  {'P' * 200}  ",
        key="create-one",
    )

    assert project["workspaceId"] == str(workspace_id)
    assert project["name"] == "P" * 200
    assert project["status"] == "created"
    assert project["sourceKind"] == "upload"


@pytest.mark.integration
def test_viewers_cannot_create_projects(engine: Engine) -> None:
    """Project creation must honor the Workspace role matrix before setting RLS context."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    user_id = browser.get("/api/v1/me").json()["id"]
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workspace_memberships SET role = :role "
                "WHERE workspace_id = :workspace_id AND user_id = :user_id"
            ),
            {"role": WorkspaceRole.VIEWER.value, "workspace_id": workspace_id, "user_id": user_id},
        )

    response = browser.request(
        "POST",
        _projects_path(workspace_id),
        headers={"Idempotency-Key": "viewer-create"},
        json={"name": "Forbidden", "sourceKind": "upload"},
    )

    assert_error(response, status_code=403, code="FORBIDDEN")


@pytest.mark.integration
def test_project_listing_paginates_only_the_selected_workspace(engine: Engine) -> None:
    """Cursor pagination must not cross a Workspace boundary or duplicate a Project."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    other = browser.request("POST", "/api/v1/workspaces", json={"name": "Other", "kind": "team"})
    assert other.status_code == 201
    other_workspace_id = UUID(other.json()["id"])
    first = _create_project(browser, workspace_id, "First", key="page-1")
    second = _create_project(browser, workspace_id, "Second", key="page-2")
    third = _create_project(browser, workspace_id, "Third", key="page-3")
    _create_project(browser, other_workspace_id, "Elsewhere", key="page-other")

    first_page = browser.get(f"{_projects_path(workspace_id)}&limit=2")
    assert first_page.status_code == 200
    first_body = first_page.json()
    assert len(first_body["projects"]) == 2
    assert first_body["nextCursor"]
    assert str(workspace_id) not in first_body["nextCursor"]

    second_page = browser.get(
        f"{_projects_path(workspace_id)}&limit=2&cursor={first_body['nextCursor']}"
    )
    assert second_page.status_code == 200
    ids = [project["id"] for project in first_body["projects"] + second_page.json()["projects"]]
    assert set(ids) == {first["id"], second["id"], third["id"]}
    assert second_page.json()["nextCursor"] is None


@pytest.mark.integration
def test_project_creation_replays_matching_idempotency_key_and_refuses_payload_mismatch(
    engine: Engine,
) -> None:
    """All Workspace members share a safe replay record for one route and key."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)

    initial = _create_project(browser, workspace_id, "Idempotent", key="same-key")
    flow.stub.claim_overrides.update(
        {"subject": "108422224444555566668", "email": "editor@example.com"}
    )
    other_browser = Browser(app)
    sign_in(other_browser, flow, code="editor-authorization-code")
    other_user_id = other_browser.get("/api/v1/me").json()["id"]
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO workspace_memberships (workspace_id, user_id, role) "
                "VALUES (:workspace_id, :user_id, :role)"
            ),
            {
                "workspace_id": workspace_id,
                "user_id": other_user_id,
                "role": WorkspaceRole.EDITOR.value,
            },
        )
    replay = other_browser.request(
        "POST",
        _projects_path(workspace_id),
        headers={"Idempotency-Key": "same-key"},
        json={"name": "Idempotent", "sourceKind": "upload"},
    )
    mismatch = other_browser.request(
        "POST",
        _projects_path(workspace_id),
        headers={"Idempotency-Key": "same-key"},
        json={"name": "Different", "sourceKind": "upload"},
    )

    assert replay.status_code == 201
    assert replay.json()["id"] == initial["id"]
    assert_error(mismatch, status_code=409, code="CONFLICT")


@pytest.mark.integration
def test_concurrent_workspace_members_share_one_idempotent_project(engine: Engine) -> None:
    """Concurrent retries must use the database key race to create only one Project."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    first_browser = Browser(app)
    sign_in(first_browser, flow)
    workspace_id = _workspace_id(first_browser)
    flow.stub.claim_overrides.update(
        {"subject": "108422224444555566669", "email": "concurrent@example.com"}
    )
    second_browser = Browser(app)
    sign_in(second_browser, flow, code="concurrent-authorization-code")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO workspace_memberships (workspace_id, user_id, role) "
                "VALUES (:workspace_id, :user_id, :role)"
            ),
            {
                "workspace_id": workspace_id,
                "user_id": second_browser.get("/api/v1/me").json()["id"],
                "role": WorkspaceRole.EDITOR.value,
            },
        )

    matching = _concurrent_creates(
        first_browser,
        second_browser,
        workspace_id,
        key="concurrent-match",
        first_name="Concurrent match",
        second_name="Concurrent match",
    )
    assert [response.status_code for response in matching] == [201, 201]
    assert matching[0].json()["id"] == matching[1].json()["id"]

    mismatched = _concurrent_creates(
        first_browser,
        second_browser,
        workspace_id,
        key="concurrent-mismatch",
        first_name="Concurrent first",
        second_name="Concurrent second",
    )
    assert sorted(response.status_code for response in mismatched) == [201, 409]
    with engine.connect() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM projects WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        ).scalar_one()
    assert count == 2


def _concurrent_creates(
    first_browser: Browser,
    second_browser: Browser,
    workspace_id: UUID,
    *,
    key: str,
    first_name: str,
    second_name: str,
) -> list[object]:
    """Start two independent browser requests at one deterministic synchronization point."""
    barrier = Barrier(2)
    responses: list[object] = []

    def create(browser: Browser, name: str) -> None:
        """Issue one request only after both members are ready to race on the same key."""
        barrier.wait()
        responses.append(
            browser.request(
                "POST",
                _projects_path(workspace_id),
                headers={"Idempotency-Key": key},
                json={"name": name, "sourceKind": "upload"},
            )
        )

    threads = [
        Thread(target=create, args=(first_browser, first_name)),
        Thread(target=create, args=(second_browser, second_name)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return responses


def test_project_name_normalization_enforces_the_canonical_length_for_non_http_callers() -> None:
    """Use-case callers cannot bypass the canonical Project name length boundary."""
    with pytest.raises(ProjectValidationError):
        normalize_project_name("P" * 201)


@pytest.mark.integration
def test_projects_can_be_renamed_soft_deleted_and_restored_within_thirty_days(
    engine: Engine,
) -> None:
    """A recoverable deletion hides a Project until restoration before the recovery cutoff."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    project = _create_project(browser, workspace_id, "Before rename", key="lifecycle")
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE projects SET status = 'ready' WHERE id = :project_id"),
            {"project_id": project["id"]},
        )

    renamed = browser.request(
        "PATCH", _project_path(workspace_id, project["id"]), json={"name": "  After rename  "}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "After rename"
    deleted = browser.request("DELETE", _project_path(workspace_id, project["id"]))
    assert deleted.status_code == 204
    assert_error(
        browser.get(_project_path(workspace_id, project["id"])), status_code=404, code="NOT_FOUND"
    )
    assert_error(
        browser.request(
            "PATCH", _project_path(workspace_id, project["id"]), json={"name": "No processing"}
        ),
        status_code=404,
        code="NOT_FOUND",
    )
    assert browser.get(_projects_path(workspace_id)).json()["projects"] == []

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE projects SET archived_at = :archived_at WHERE id = :project_id"),
            {"archived_at": NOW - timedelta(days=29), "project_id": project["id"]},
        )
    restored = browser.request("POST", _restore_path(workspace_id, project["id"]))
    assert restored.status_code == 200
    assert restored.json()["status"] == "ready"


@pytest.mark.integration
@pytest.mark.parametrize("workspace_status", ["suspended", "deleted"])
def test_projects_reject_archived_or_inactive_workspace_and_expired_project_recovery(
    engine: Engine,
    workspace_status: str,
) -> None:
    """Processing and recovery cannot revive data outside its Workspace retention guarantees."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = _workspace_id(browser)
    project = _create_project(browser, workspace_id, "Recovery deadline", key="deadline")
    assert browser.request("DELETE", _project_path(workspace_id, project["id"])).status_code == 204
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE projects SET archived_at = :archived_at WHERE id = :project_id"),
            {"archived_at": NOW - timedelta(days=31), "project_id": project["id"]},
        )
    expired = browser.request("POST", _restore_path(workspace_id, project["id"]))
    assert_error(expired, status_code=409, code="CONFLICT")

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE workspaces SET status = :status WHERE id = :workspace_id"),
            {"status": workspace_status, "workspace_id": workspace_id},
        )
    blocked = browser.request(
        "POST",
        _projects_path(workspace_id),
        headers={"Idempotency-Key": f"{workspace_status}-workspace"},
        json={"name": "No processing", "sourceKind": "upload"},
    )
    assert_error(blocked, status_code=404, code="NOT_FOUND")
    assert_error(
        browser.request(
            "PATCH", _project_path(workspace_id, project["id"]), json={"name": "Blocked"}
        ),
        status_code=404,
        code="NOT_FOUND",
    )
