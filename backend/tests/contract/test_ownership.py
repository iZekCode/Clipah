"""Ownership contracts: a guessed identifier must reveal nothing a missing one would not.

Only Workspace routes exist at this point in the build, so the tenant tables that
later tasks expose (projects, jobs, edits, renders) are proven at the row level
instead: with another Workspace's context installed they are as empty as a
Workspace that never existed.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from clipah.config import Settings
from clipah.db import RuntimeRole, session_scope
from clipah.models import WorkspaceRole
from harness import (
    NOW,
    Browser,
    Clock,
    StubGoogleProvider,
    assert_error,
    build_app,
    sign_in,
)
from support import runtime_settings

TENANT_TABLES = ("projects", "jobs", "clip_edits", "render_artifacts")


def sign_in_as(
    app: Any, flow: Any, provider: StubGoogleProvider, *, subject: str, email: str, name: str
) -> Browser:
    """Sign one distinct Google account into its own browser."""
    provider.identify(subject=subject, email=email, name=name)
    browser = Browser(app)
    sign_in(browser, flow)
    return browser


def only_workspace(browser: Browser) -> dict[str, Any]:
    """Return the single Workspace a freshly bootstrapped User belongs to."""
    workspaces = browser.get("/api/v1/workspaces").json()["workspaces"]
    assert len(workspaces) == 1
    return dict(workspaces[0])


def add_member(engine: Engine, *, workspace_id: str, user_id: str, role: WorkspaceRole) -> None:
    """Grant one User a role in a Workspace the way an invite acceptance will."""
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO workspace_memberships (workspace_id, user_id, role)
                VALUES (:workspace_id, :user_id, :role)
                """
            ),
            {"workspace_id": workspace_id, "user_id": user_id, "role": role.value},
        )


@pytest.mark.integration
def test_a_new_user_sees_only_the_personal_workspace_login_bootstrapped(
    engine: Engine, clean_database: None
) -> None:
    """Signing in for the first time must expose exactly one owned Workspace."""
    del engine, clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)

    workspace = only_workspace(browser)

    assert workspace["kind"] == "personal"
    assert workspace["role"] == "owner"
    assert workspace["publishingRolePolicy"] == "owner_admin_editor"


@pytest.mark.integration
def test_creating_a_team_workspace_makes_the_creator_its_owner(
    engine: Engine, clean_database: None
) -> None:
    """A team Workspace must be usable by its creator the moment it exists."""
    del engine, clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)

    created = browser.request("POST", "/api/v1/workspaces", json={"name": "Studio Team"})

    assert created.status_code == 201
    body = created.json()
    assert body["kind"] == "team"
    assert body["role"] == "owner"
    assert browser.get(f"/api/v1/workspaces/{body['id']}").json()["name"] == "Studio Team"
    members = browser.get(f"/api/v1/workspaces/{body['id']}/members").json()["members"]
    assert [member["role"] for member in members] == ["owner"]


@pytest.mark.integration
def test_a_second_personal_workspace_is_refused(engine: Engine, clean_database: None) -> None:
    """Login already bootstrapped the one personal Workspace a User may have."""
    del engine, clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock))
    browser = Browser(app)
    sign_in(browser, flow)

    response = browser.request(
        "POST", "/api/v1/workspaces", json={"name": "Second Personal", "kind": "personal"}
    )

    assert_error(response, status_code=409, code="CONFLICT")
    assert len(browser.get("/api/v1/workspaces").json()["workspaces"]) == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    "path_template",
    ["/api/v1/workspaces/{workspace_id}", "/api/v1/workspaces/{workspace_id}/members"],
)
def test_a_guessed_workspace_uuid_is_answered_like_a_missing_one(
    engine: Engine, clean_database: None, path_template: str
) -> None:
    """A non-member must not be able to tell a real Workspace from an invented one."""
    del engine, clean_database
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    app, flow, _ = build_app(clock, provider)
    owner = sign_in_as(
        app, flow, provider, subject="owner-1", email="owner@example.com", name="Owner Example"
    )
    stranger = sign_in_as(
        app,
        flow,
        provider,
        subject="stranger-1",
        email="stranger@example.com",
        name="Stranger Example",
    )
    owned_id = only_workspace(owner)["id"]

    guessed = stranger.get(path_template.format(workspace_id=owned_id))
    missing = stranger.get(path_template.format(workspace_id=uuid4()))

    assert_error(guessed, status_code=404, code="NOT_FOUND")
    assert_error(missing, status_code=404, code="NOT_FOUND")
    assert guessed.json()["error"]["message"] == missing.json()["error"]["message"]


@pytest.mark.integration
def test_a_guessed_workspace_uuid_cannot_be_mutated(engine: Engine, clean_database: None) -> None:
    """A write against someone else's Workspace must fail like a missing resource."""
    del clean_database
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    app, flow, _ = build_app(clock, provider)
    owner = sign_in_as(
        app, flow, provider, subject="owner-2", email="owner@example.com", name="Owner Example"
    )
    stranger = sign_in_as(
        app,
        flow,
        provider,
        subject="stranger-2",
        email="stranger@example.com",
        name="Stranger Example",
    )
    owned_id = only_workspace(owner)["id"]

    response = stranger.request(
        "PATCH", f"/api/v1/workspaces/{owned_id}", json={"name": "Taken Over"}
    )

    assert_error(response, status_code=404, code="NOT_FOUND")
    with engine.connect() as connection:
        name = connection.execute(
            text("SELECT name FROM workspaces WHERE id = :id"), {"id": owned_id}
        ).scalar_one()
    assert name != "Taken Over"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("role", "expected_status"),
    [
        (WorkspaceRole.OWNER, 200),
        (WorkspaceRole.ADMIN, 200),
        (WorkspaceRole.EDITOR, 403),
        (WorkspaceRole.REVIEWER, 403),
        (WorkspaceRole.VIEWER, 403),
    ],
)
def test_only_owners_and_admins_may_change_workspace_settings(
    engine: Engine, clean_database: None, role: WorkspaceRole, expected_status: int
) -> None:
    """Every member reads the Workspace; only its stewards reconfigure it."""
    del clean_database
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    app, flow, _ = build_app(clock, provider)
    owner = sign_in_as(
        app, flow, provider, subject="owner-3", email="owner@example.com", name="Owner Example"
    )
    member = sign_in_as(
        app, flow, provider, subject="member-3", email="member@example.com", name="Member Example"
    )
    workspace = owner.request("POST", "/api/v1/workspaces", json={"name": "Studio Team"}).json()
    member_id = member.get("/api/v1/me").json()["id"]
    if role is not WorkspaceRole.OWNER:
        add_member(engine, workspace_id=workspace["id"], user_id=member_id, role=role)
    actor = owner if role is WorkspaceRole.OWNER else member

    assert actor.get(f"/api/v1/workspaces/{workspace['id']}").status_code == 200
    response = actor.request(
        "PATCH", f"/api/v1/workspaces/{workspace['id']}", json={"name": "Renamed Studio"}
    )

    assert response.status_code == expected_status
    if expected_status == 403:
        assert response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.integration
def test_the_publishing_role_policy_is_workspace_configurable(
    engine: Engine, clean_database: None
) -> None:
    """Narrowing the policy must be visible to every member reading the Workspace."""
    del engine, clean_database
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    app, flow, _ = build_app(clock, provider)
    owner = sign_in_as(
        app, flow, provider, subject="owner-4", email="owner@example.com", name="Owner Example"
    )
    workspace = owner.request("POST", "/api/v1/workspaces", json={"name": "Studio Team"}).json()

    updated = owner.request(
        "PATCH",
        f"/api/v1/workspaces/{workspace['id']}",
        json={"publishingRolePolicy": "owner_admin"},
    )

    assert updated.status_code == 200
    assert updated.json()["publishingRolePolicy"] == "owner_admin"
    assert (
        owner.get(f"/api/v1/workspaces/{workspace['id']}").json()["publishingRolePolicy"]
        == "owner_admin"
    )


@pytest.mark.integration
def test_every_request_transaction_installs_the_workspace_rls_context(
    engine: Engine, clean_database: None
) -> None:
    """A Workspace-scoped request must reach the database as that Workspace's actor."""
    del engine, clean_database
    clock = Clock(NOW)
    contexts: list[dict[str, str]] = []
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), recorded_contexts=contexts)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace = only_workspace(browser)
    user_id = browser.get("/api/v1/me").json()["id"]

    browser.get(f"/api/v1/workspaces/{workspace['id']}/members")

    assert contexts[-1] == {
        "clipah.workspace_id": workspace["id"],
        "clipah.user_id": user_id,
    }


@pytest.mark.integration
def test_a_request_outside_any_workspace_leaves_the_tenant_context_empty(
    engine: Engine, clean_database: None
) -> None:
    """Listing Workspaces is not scoped to one tenant, so it must claim none."""
    del engine, clean_database
    clock = Clock(NOW)
    contexts: list[dict[str, str]] = []
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), recorded_contexts=contexts)
    browser = Browser(app)
    sign_in(browser, flow)

    browser.get("/api/v1/workspaces")

    assert contexts[-1]["clipah.workspace_id"] == ""
    assert contexts[-1]["clipah.user_id"] != ""


@pytest.mark.integration
@pytest.mark.parametrize("runtime_role", [RuntimeRole.API, RuntimeRole.WORKER])
def test_another_workspaces_rows_are_invisible_to_every_runtime_transaction(
    engine: Engine, clean_database: None, runtime_role: RuntimeRole
) -> None:
    """A transaction scoped to one Workspace must read like the others do not exist.

    Row-level security is scoped by the Workspace the transaction declares, and the
    application decides which Workspace a caller may declare. The pairing is what
    makes a guessed identifier useless: the routes above refuse to install a context
    for a non-member, and here that unowned context reveals nothing anyway.
    """
    del clean_database
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    app, flow, _ = build_app(clock, provider)
    owner = sign_in_as(
        app, flow, provider, subject="owner-5", email="owner@example.com", name="Owner Example"
    )
    stranger = sign_in_as(
        app,
        flow,
        provider,
        subject="stranger-5",
        email="stranger@example.com",
        name="Stranger Example",
    )
    owned = UUID(only_workspace(owner)["id"])
    owner_user_id = UUID(owner.get("/api/v1/me").json()["id"])
    stranger_workspace = UUID(only_workspace(stranger)["id"])
    stranger_user_id = UUID(stranger.get("/api/v1/me").json()["id"])
    seed_project(engine, workspace_id=owned, user_id=owner_user_id)

    settings = runtime_settings(runtime_role=runtime_role)
    elsewhere = count_tenant_rows(settings, runtime_role, stranger_workspace, stranger_user_id)
    missing = count_tenant_rows(settings, runtime_role, uuid4(), stranger_user_id)
    owning = count_tenant_rows(settings, runtime_role, owned, owner_user_id)

    assert elsewhere == missing == dict.fromkeys(TENANT_TABLES, 0)
    assert owning["projects"] == 1


def seed_project(engine: Engine, *, workspace_id: UUID, user_id: UUID) -> None:
    """Give the owned Workspace one tenant row a stranger must never observe."""
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects
                    (workspace_id, created_by_user_id, name, status, source_kind)
                VALUES (:workspace_id, :user_id, 'Owned Project', 'created', 'upload')
                """
            ),
            {"workspace_id": workspace_id, "user_id": user_id},
        )


def count_tenant_rows(
    settings: Settings, runtime_role: RuntimeRole, workspace_id: UUID, user_id: UUID
) -> dict[str, int]:
    """Count what one runtime transaction can see under a Workspace context."""
    with session_scope(
        settings=settings,
        workspace_id=workspace_id,
        user_id=user_id,
        runtime_role=runtime_role,
    ) as session:
        return {table: count_rows(session, table) for table in TENANT_TABLES}


def count_rows(session: Session, table: str) -> int:
    """Count rows in one tenant table; the name comes from this module only."""
    return int(session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())
