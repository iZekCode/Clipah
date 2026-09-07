"""Public contracts for Workspace invitations, roles, removal, and ownership."""

from __future__ import annotations

from hashlib import sha256
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from harness import NOW, Browser, Clock, StubGoogleProvider, assert_error, build_app, sign_in


@pytest.mark.integration
def test_collaboration_routes_are_invisible_while_the_feature_is_disabled(engine: Engine) -> None:
    """A plan without collaboration must not expose a discoverable management surface."""
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    app, flow, _ = build_app(clock, provider)
    owner = Browser(app)
    sign_in(owner, flow)
    workspace_id = _team_workspace(owner)

    response = owner.request(
        "POST",
        f"/api/v1/workspaces/{workspace_id}/invites",
        json={"email": "reviewer@example.test", "role": "reviewer"},
    )

    assert_error(response, status_code=404, code="NOT_FOUND")


@pytest.mark.integration
def test_an_invite_is_stored_as_a_hash_and_acceptance_does_not_trust_email(
    engine: Engine,
) -> None:
    """The bearer token authenticates the invitation while login authenticates the User."""
    owner, invitee, workspace_id = _two_members(engine)

    created = owner.request(
        "POST",
        f"/api/v1/workspaces/{workspace_id}/invites",
        json={"email": "delivery-only@example.test", "role": "reviewer"},
    )

    assert created.status_code == 201
    token = created.json()["token"]
    assert created.json()["role"] == "reviewer"
    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT token_hash FROM workspace_invites WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        ).scalar_one()
    assert stored == sha256(token.encode()).digest()
    assert token.encode() not in stored

    accepted = invitee.request("POST", f"/api/v1/workspace-invites/{token}/accept")

    assert accepted.status_code == 200
    assert accepted.json()["role"] == "reviewer"
    listed = invitee.get(f"/api/v1/workspaces/{workspace_id}")
    assert listed.status_code == 200


@pytest.mark.integration
def test_pending_invites_can_be_listed_without_disclosing_bearer_tokens(engine: Engine) -> None:
    """Team settings may redisplay delivery metadata, never an invitation credential."""
    owner, _, workspace_id = _two_members(engine)
    created = owner.request(
        "POST",
        f"/api/v1/workspaces/{workspace_id}/invites",
        json={"email": "reviewer@example.test", "role": "reviewer"},
    ).json()

    listed = owner.get(f"/api/v1/workspaces/{workspace_id}/invites")

    assert listed.status_code == 200
    assert listed.json() == {
        "invites": [
            {
                "id": created["id"],
                "email": "reviewer@example.test",
                "role": "reviewer",
                "expiresAt": created["expiresAt"],
            }
        ]
    }
    assert created["token"] not in listed.text


@pytest.mark.integration
def test_removing_a_member_revokes_their_existing_session_immediately(engine: Engine) -> None:
    """An open browser must lose Workspace access on its very next authorization read."""
    owner, invitee, workspace_id = _two_members(engine)
    created = owner.request(
        "POST",
        f"/api/v1/workspaces/{workspace_id}/invites",
        json={"email": "invitee@example.test", "role": "viewer"},
    )
    token = created.json()["token"]
    accepted = invitee.request("POST", f"/api/v1/workspace-invites/{token}/accept")
    member_user_id = UUID(accepted.json()["userId"])

    removed = owner.request("DELETE", f"/api/v1/workspaces/{workspace_id}/members/{member_user_id}")

    assert removed.status_code == 204
    refused = invitee.get(f"/api/v1/workspaces/{workspace_id}")
    assert_error(refused, status_code=404, code="NOT_FOUND")


@pytest.mark.integration
def test_revoked_and_replayed_invitations_are_indistinguishable_from_missing(
    engine: Engine,
) -> None:
    """A revoked or spent bearer token must not reveal invitation history."""
    owner, invitee, workspace_id = _two_members(engine)
    first = owner.request(
        "POST",
        f"/api/v1/workspaces/{workspace_id}/invites",
        json={"email": "first@example.test", "role": "viewer"},
    ).json()
    revoked = owner.request("DELETE", f"/api/v1/workspaces/{workspace_id}/invites/{first['id']}")
    assert revoked.status_code == 204
    assert_error(
        invitee.request("POST", f"/api/v1/workspace-invites/{first['token']}/accept"),
        status_code=404,
        code="NOT_FOUND",
    )

    second = owner.request(
        "POST",
        f"/api/v1/workspaces/{workspace_id}/invites",
        json={"email": "second@example.test", "role": "reviewer"},
    ).json()
    assert (
        invitee.request("POST", f"/api/v1/workspace-invites/{second['token']}/accept").status_code
        == 200
    )
    assert_error(
        invitee.request("POST", f"/api/v1/workspace-invites/{second['token']}/accept"),
        status_code=404,
        code="NOT_FOUND",
    )


@pytest.mark.integration
def test_the_last_owner_cannot_be_removed_or_demoted(engine: Engine) -> None:
    """Every Workspace must retain one active owner through every mutation path."""
    owner, _, workspace_id = _two_members(engine)
    owner_id = UUID(owner.get("/api/v1/me").json()["id"])

    removed = owner.request("DELETE", f"/api/v1/workspaces/{workspace_id}/members/{owner_id}")
    demoted = owner.request(
        "PATCH",
        f"/api/v1/workspaces/{workspace_id}/members/{owner_id}",
        json={"role": "admin"},
    )

    assert_error(removed, status_code=409, code="CONFLICT")
    assert_error(demoted, status_code=409, code="CONFLICT")


@pytest.mark.integration
def test_ownership_transfer_is_atomic_and_actor_attributed(engine: Engine) -> None:
    """Transfer must promote its recipient and demote its actor in one audited transaction."""
    owner, invitee, workspace_id = _two_members(engine)
    invitation = owner.request(
        "POST",
        f"/api/v1/workspaces/{workspace_id}/invites",
        json={"email": "new-owner@example.test", "role": "admin"},
    ).json()
    accepted = invitee.request(
        "POST", f"/api/v1/workspace-invites/{invitation['token']}/accept"
    ).json()
    recipient_id = UUID(accepted["userId"])
    actor_id = UUID(owner.get("/api/v1/me").json()["id"])

    transferred = owner.request(
        "POST",
        f"/api/v1/workspaces/{workspace_id}/ownership-transfers",
        json={"userId": str(recipient_id)},
    )

    assert transferred.status_code == 200
    roles = {member["userId"]: member["role"] for member in transferred.json()["members"]}
    assert roles[str(recipient_id)] == "owner"
    assert roles[str(actor_id)] == "admin"
    with engine.connect() as connection:
        event = connection.execute(
            text(
                "SELECT actor_user_id, member_user_id FROM workspace_membership_events "
                "WHERE workspace_id = :workspace_id AND kind = 'ownership_transferred'"
            ),
            {"workspace_id": workspace_id},
        ).one()
    assert event == (actor_id, recipient_id)


def _two_members(engine: Engine) -> tuple[Browser, Browser, UUID]:
    """Create two authenticated Users and a team Workspace owned by the first."""
    del engine
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    app, flow, _ = build_app(clock, provider, collaboration_enabled=True)
    owner = Browser(app)
    sign_in(owner, flow)
    workspace_id = _team_workspace(owner)

    provider.identify(
        subject="invitee-subject-0001",
        email="signed-in-person@example.test",
        name="Invited Person",
    )
    invitee = Browser(app)
    sign_in(invitee, flow, code="invitee-authorization-code")
    return owner, invitee, workspace_id


def _team_workspace(owner: Browser) -> UUID:
    """Create one team Workspace through the public API."""
    response = owner.request(
        "POST", "/api/v1/workspaces", json={"name": "Review Team", "kind": "team"}
    )
    assert response.status_code == 201
    return UUID(response.json()["id"])
