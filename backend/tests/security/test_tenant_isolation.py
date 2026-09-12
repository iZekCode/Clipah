"""A member of one Workspace must not be able to reach another Workspace's rows.

Every other suite proves one feature behaves. This one assumes the caller is hostile and
already holds the identifiers: it signs in as a real member of their own Workspace, then
spends that standing on somebody else's Project, Clip Candidate, Job, Edit, Brand Kit, and
Template. The answer must be the one a Workspace-scoped route gives for a UUID that was
never issued at all — same status, same code, same public message — because a difference
between "not yours" and "not here" is an enumeration oracle.

The second half attacks the tenant context itself: a revoked Membership, a forged
`workspace_id`, and a transaction that declares no tenant at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import Response
from sqlalchemy import Engine, delete, text

from clipah.assets.storage import FakeObjectStore, StoredObject
from clipah.db import RuntimeRole, session_scope
from clipah.dev.seed import seed_analysed_project
from clipah.jobs.admission import admission_policy
from clipah.jobs.use_cases import create_job
from clipah.models import (
    Asset,
    AssetKind,
    AssetSourceType,
    JobKind,
    Project,
    PublishingRolePolicy,
    SourceKind,
    WorkspaceMembership,
    WorkspaceRole,
)
from clipah.workspaces.models import WorkspaceAccess
from harness import NOW, Browser, Clock, StubGoogleProvider, build_app, sign_in
from support import RUNTIME_LOGINS, runtime_settings


def _seeding_settings() -> Any:
    """Settings carrying both runtime logins, because a seeded clip is written by both."""
    return runtime_settings(worker_database_url=RUNTIME_LOGINS[RuntimeRole.WORKER][1])


@dataclass(frozen=True, slots=True)
class Targets:
    """Every identifier this suite knows how to aim a route at."""

    project_id: UUID
    candidate_id: UUID
    edit_id: UUID
    job_id: UUID


@dataclass(frozen=True, slots=True)
class Tenant:
    """One signed-in member, the Workspace they own, and the rows they own in it."""

    browser: Browser
    workspace_id: UUID
    user_id: UUID
    targets: Targets


# Each entry is one route reachable with a resource identifier. The identifiers come from
# the victim's rows while `workspace_id` names the attacker's own Workspace, which is the
# shape a real cross-tenant attempt takes: the standing is genuine and only the target is
# stolen.
PROBES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/v1/projects/{project_id}"),
    ("PATCH", "/api/v1/projects/{project_id}"),
    ("DELETE", "/api/v1/projects/{project_id}"),
    ("GET", "/api/v1/projects/{project_id}/assets"),
    ("GET", "/api/v1/projects/{project_id}/proxy"),
    ("GET", "/api/v1/projects/{project_id}/candidates"),
    ("GET", "/api/v1/projects/{project_id}/candidates/{candidate_id}"),
    ("POST", "/api/v1/projects/{project_id}/candidates/{candidate_id}/edits"),
    ("GET", "/api/v1/jobs/{job_id}"),
    ("POST", "/api/v1/jobs/{job_id}/cancel"),
    ("GET", "/api/v1/edits/{edit_id}"),
    ("GET", "/api/v1/edits/{edit_id}/revisions"),
    ("GET", "/api/v1/edits/{edit_id}/reviews"),
    ("GET", "/api/v1/edits/{edit_id}/campaign-outputs"),
    ("GET", "/api/v1/projects/{project_id}/candidates/{candidate_id}/variants"),
    ("GET", "/api/v1/projects/{project_id}/candidates/{candidate_id}/broll-suggestions"),
    ("GET", "/api/v1/projects/{project_id}/candidates/{candidate_id}/claim-evidence"),
)
PROBE_IDS = [f"{method} {path}" for method, path in PROBES]


@pytest.mark.integration
@pytest.mark.parametrize(("method", "template"), PROBES, ids=PROBE_IDS)
def test_a_resource_from_another_workspace_answers_like_one_that_never_existed(
    engine: Engine, clean_database: None, method: str, template: str
) -> None:
    """A stolen identifier must teach an attacker nothing an invented one would not."""
    del clean_database
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    store = FakeObjectStore(now=clock)
    app, flow, _ = build_app(clock, provider, object_store=store, collaboration_enabled=True)
    victim = _tenant(app, flow, provider, clock, store, suffix="victim")
    attacker = _tenant(app, flow, provider, clock, store, suffix="attacker")

    stolen = attacker.browser.request(
        method,
        _aim(template, victim.targets, workspace_id=attacker.workspace_id),
        json=_body(method),
    )
    invented = attacker.browser.request(
        method,
        _aim(template, _invented(), workspace_id=attacker.workspace_id),
        json=_body(method),
    )

    assert stolen.status_code == 404, stolen.text
    assert invented.status_code == 404, invented.text
    _assert_identical_refusal(stolen, invented)


@pytest.mark.integration
@pytest.mark.parametrize(("method", "template"), PROBES, ids=PROBE_IDS)
def test_every_probe_reaches_a_real_route_inside_the_callers_own_workspace(
    engine: Engine, clean_database: None, method: str, template: str
) -> None:
    """A mistyped probe would answer 404 for everyone and prove nothing about isolation.

    This is the control for the test above it: the same request, aimed at the caller's
    own rows, must be answered rather than refused.
    """
    del clean_database
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    store = FakeObjectStore(now=clock)
    app, flow, _ = build_app(clock, provider, object_store=store, collaboration_enabled=True)
    member = _tenant(app, flow, provider, clock, store, suffix="member")

    response = member.browser.request(
        method,
        _aim(template, member.targets, workspace_id=member.workspace_id),
        json=_body(method),
    )

    assert response.status_code != 404, response.text


@pytest.mark.integration
def test_no_identifier_of_another_workspace_appears_in_the_refusal_it_earns(
    engine: Engine, clean_database: None
) -> None:
    """A refusal that echoed the identifier back would confirm the guess it refused."""
    del clean_database
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    store = FakeObjectStore(now=clock)
    app, flow, _ = build_app(clock, provider, object_store=store, collaboration_enabled=True)
    victim = _tenant(app, flow, provider, clock, store, suffix="victim")
    attacker = _tenant(app, flow, provider, clock, store, suffix="attacker")

    for method, template in PROBES:
        response = attacker.browser.request(
            method,
            _aim(template, victim.targets, workspace_id=attacker.workspace_id),
            json=_body(method),
        )
        for identifier in (victim.workspace_id, *_as_tuple(victim.targets)):
            assert str(identifier) not in response.text, f"{method} {template} echoed an identifier"


@pytest.mark.integration
def test_declaring_another_workspace_is_refused_before_any_row_is_read(
    engine: Engine, clean_database: None
) -> None:
    """Naming a Workspace is a claim about Membership, and the claim is proven first."""
    del clean_database
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    store = FakeObjectStore(now=clock)
    app, flow, _ = build_app(clock, provider, object_store=store, collaboration_enabled=True)
    victim = _tenant(app, flow, provider, clock, store, suffix="victim")
    attacker = _tenant(app, flow, provider, clock, store, suffix="attacker")

    response = attacker.browser.get(
        f"/api/v1/projects/{victim.targets.project_id}?workspace_id={victim.workspace_id}"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.integration
def test_a_removed_member_loses_the_workspace_on_the_very_next_request(
    engine: Engine, clean_database: None
) -> None:
    """Access is proven per request, so removal cannot wait for a Session to expire."""
    del clean_database
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    store = FakeObjectStore(now=clock)
    app, flow, _ = build_app(clock, provider, object_store=store, collaboration_enabled=True)
    owner = _tenant(app, flow, provider, clock, store, suffix="owner")
    guest = _tenant(app, flow, provider, clock, store, suffix="guest")
    path = f"/api/v1/projects/{owner.targets.project_id}?workspace_id={owner.workspace_id}"
    _grant(engine, workspace_id=owner.workspace_id, user_id=guest.user_id)

    before = guest.browser.get(path)
    _revoke(engine, workspace_id=owner.workspace_id, user_id=guest.user_id)
    after = guest.browser.get(path)

    assert before.status_code == 200
    assert after.status_code == 404
    assert after.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.integration
@pytest.mark.parametrize(
    "forged",
    [
        "' OR '1'='1",
        "00000000-0000-0000-0000-000000000000; SET clipah.workspace_id = 'x'",
        "*",
    ],
)
def test_a_workspace_identifier_that_is_not_a_uuid_never_reaches_the_tenant_context(
    engine: Engine, clean_database: None, forged: str
) -> None:
    """The tenant context is set from a parsed UUID, so nothing else can reach it."""
    del clean_database
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    store = FakeObjectStore(now=clock)
    app, flow, _ = build_app(clock, provider, object_store=store, collaboration_enabled=True)
    attacker = _tenant(app, flow, provider, clock, store, suffix="attacker")

    response = attacker.browser.get(f"/api/v1/projects?workspace_id={forged}")

    assert response.status_code == 422
    assert "clipah.workspace_id" not in response.text


@pytest.mark.integration
def test_a_transaction_that_declares_no_workspace_reads_no_tenant_row(
    engine: Engine, clean_database: None
) -> None:
    """Row-level security is the floor under the application, so it holds with no context."""
    del clean_database
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    store = FakeObjectStore(now=clock)
    app, flow, _ = build_app(clock, provider, object_store=store, collaboration_enabled=True)
    victim = _tenant(app, flow, provider, clock, store, suffix="victim")

    with session_scope(settings=runtime_settings(RuntimeRole.API)) as session:
        visible = session.execute(text("SELECT count(*) FROM projects")).scalar_one()

    assert visible == 0
    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT count(*) FROM projects WHERE id = :id"),
            {"id": victim.targets.project_id},
        ).scalar_one()
    assert stored == 1


@pytest.mark.integration
def test_one_workspace_context_hides_every_row_outside_it(
    engine: Engine, clean_database: None
) -> None:
    """The policy is symmetric: holding one context hides everything outside it."""
    del engine, clean_database
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    store = FakeObjectStore(now=clock)
    app, flow, _ = build_app(clock, provider, object_store=store, collaboration_enabled=True)
    victim = _tenant(app, flow, provider, clock, store, suffix="victim")
    attacker = _tenant(app, flow, provider, clock, store, suffix="attacker")

    with session_scope(
        settings=runtime_settings(RuntimeRole.API),
        workspace_id=attacker.workspace_id,
        user_id=attacker.user_id,
    ) as session:
        found = session.execute(
            text("SELECT count(*) FROM projects WHERE id = :id"),
            {"id": victim.targets.project_id},
        ).scalar_one()

    assert found == 0


def _tenant(
    app: Any,
    flow: Any,
    provider: StubGoogleProvider,
    clock: Clock,
    store: FakeObjectStore,
    *,
    suffix: str,
) -> Tenant:
    """Sign one distinct member in and give them every kind of row this suite probes."""
    provider.identify(
        subject=f"{suffix}-subject", email=f"{suffix}@example.com", name=f"{suffix.title()} Example"
    )
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    user_id = UUID(browser.get("/api/v1/me").json()["id"])

    seeded = seed_analysed_project(
        settings=_seeding_settings(),
        workspace_id=workspace_id,
        user_id=user_id,
        name=f"{suffix} project",
        now=clock(),
    )
    edit = browser.request(
        "POST",
        f"/api/v1/projects/{seeded.project_id}/candidates/{seeded.candidate_id}/edits"
        f"?workspace_id={workspace_id}",
    )
    assert edit.status_code in {200, 201}, edit.text
    _seed_proxy(store, workspace_id=workspace_id, user_id=user_id, project_id=seeded.project_id)

    return Tenant(
        browser=browser,
        workspace_id=workspace_id,
        user_id=user_id,
        targets=Targets(
            project_id=seeded.project_id,
            candidate_id=seeded.candidate_id,
            edit_id=UUID(edit.json()["id"]),
            job_id=_queued_job(workspace_id, user_id, clock, key=f"{suffix}-job"),
        ),
    )


def _seed_proxy(
    store: FakeObjectStore, *, workspace_id: UUID, user_id: UUID, project_id: UUID
) -> None:
    """Give the Project the proxy a playback capability is signed against.

    Without it the playback route answers 404 for everyone, and the cross-tenant probe
    against it would pass while proving nothing.
    """
    key = f"workspaces/{workspace_id}/projects/{project_id}/derived/proxy"
    store.objects[key] = StoredObject(key=key, content_type="video/mp4", content_length=1_024)
    with session_scope(
        settings=runtime_settings(RuntimeRole.WORKER),
        runtime_role=RuntimeRole.WORKER,
        workspace_id=workspace_id,
        user_id=user_id,
    ) as session:
        session.add(
            Asset(
                workspace_id=workspace_id,
                project_id=project_id,
                kind=AssetKind.PROXY,
                source_type=AssetSourceType.DERIVED,
                storage_key=key,
                content_type="video/mp4",
                size_bytes=1_024,
                duration_ms=120_000,
                width=720,
                height=1_280,
                sha256=b"p" * 32,
            )
        )


def _queued_job(workspace_id: UUID, user_id: UUID, clock: Clock, *, key: str) -> UUID:
    """Admit one queued Job the way the API admits work for its own Workspace."""
    project_id = uuid4()
    with session_scope(
        settings=runtime_settings(), workspace_id=workspace_id, user_id=user_id
    ) as session:
        session.add(
            Project(
                id=project_id,
                workspace_id=workspace_id,
                created_by_user_id=user_id,
                name="Isolation",
                source_kind=SourceKind.UPLOAD,
            )
        )
        session.flush()
        return create_job(
            session,
            policy=admission_policy(runtime_settings()),
            access=WorkspaceAccess(
                workspace_id=workspace_id,
                user_id=user_id,
                role=WorkspaceRole.OWNER,
                publishing_role_policy=PublishingRolePolicy.OWNER_ADMIN_EDITOR,
            ),
            project_id=project_id,
            kind=JobKind.INGEST,
            idempotency_key=key,
            now=clock(),
        ).job_id


def _invented() -> Targets:
    """Identifiers that were invented rather than stolen, for the comparison refusal."""
    return Targets(project_id=uuid4(), candidate_id=uuid4(), edit_id=uuid4(), job_id=uuid4())


def _as_tuple(targets: Targets) -> tuple[UUID, ...]:
    """Every identifier one tenant holds, for searching a response body."""
    return (targets.project_id, targets.candidate_id, targets.edit_id, targets.job_id)


def _aim(template: str, targets: Targets, *, workspace_id: UUID) -> str:
    """Aim one route at the given rows while declaring the attacker's own Workspace."""
    filled = template.format(
        project_id=targets.project_id,
        candidate_id=targets.candidate_id,
        edit_id=targets.edit_id,
        job_id=targets.job_id,
    )
    return f"{filled}?workspace_id={workspace_id}"


def _body(method: str) -> dict[str, object] | None:
    """Send the smallest well-formed body each writing probe needs to get past parsing."""
    return {"name": "Taken over"} if method == "PATCH" else None


def _assert_identical_refusal(stolen: Response, invented: Response) -> None:
    """Two refusals may differ only in the request identifier each carries."""
    assert stolen.json()["error"]["code"] == invented.json()["error"]["code"]
    assert stolen.json()["error"]["message"] == invented.json()["error"]["message"]


def _grant(engine: Engine, *, workspace_id: UUID, user_id: UUID) -> None:
    """Add one Membership the way an accepted invite does."""
    with engine.begin() as connection:
        connection.execute(
            WorkspaceMembership.__table__.insert().values(
                workspace_id=workspace_id, user_id=user_id, role=WorkspaceRole.VIEWER
            )
        )


def _revoke(engine: Engine, *, workspace_id: UUID, user_id: UUID) -> None:
    """Remove one Membership the way an owner removing a member does."""
    with engine.begin() as connection:
        connection.execute(
            delete(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
            )
        )
