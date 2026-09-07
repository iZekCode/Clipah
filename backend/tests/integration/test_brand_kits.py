"""Integration contracts for Brand Kits and Workspace-owned templates.

Three promises are held here. A published version never changes, so a clip approved under
one set of rules is never re-judged by another. A kit is archived rather than destroyed,
so a Revision that names one of its versions keeps resolving. And a Brand Kit may only
name media its own Workspace owns, because a licensed font or a logo is somebody's
property before it is a design decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import Response
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from clipah.models import (
    Asset,
    AssetKind,
    AssetSourceType,
    BrandKit,
    BrandKitVersion,
    BrandTemplateVersion,
    WorkspaceRole,
)
from harness import NOW, Browser, Clock, StubGoogleProvider, assert_error, build_app, sign_in


@dataclass(frozen=True, slots=True)
class _Fixture:
    """One signed-in member and the Workspace whose brand they are describing."""

    browser: Browser
    workspace_id: UUID
    project_id: UUID


def _kits_path(fixture: _Fixture, suffix: str = "") -> str:
    """Name the Brand Kit collection, or one kit inside it."""
    return f"/api/v1/brand-kits{suffix}?workspace_id={fixture.workspace_id}"


def _templates_path(fixture: _Fixture, suffix: str = "") -> str:
    """Name the template collection, or one template inside it."""
    return f"/api/v1/templates{suffix}?workspace_id={fixture.workspace_id}"


def kit_definition(**overrides: Any) -> dict[str, Any]:
    """One complete Brand Kit definition, as a browser sends it."""
    definition: dict[str, Any] = {
        "logoAssetId": None,
        "fonts": [{"family": "Inter", "assetId": None}],
        "colors": [
            {"name": "Paper", "hex": "#FFFFFF"},
            {"name": "Ink", "hex": "#000000"},
            {"name": "Highlight", "hex": "#FFD166"},
        ],
        "captionRules": {
            "minFontSize": 32,
            "maxFontSize": 72,
            "allowedAlignments": ["center"],
            "reservedPlacements": ["lowerThird"],
        },
        "visualExclusions": ["alcohol"],
        "claimRules": {"requiredAttribution": None, "forbiddenClaimPhrases": ["dijamin untung"]},
    }
    definition.update(overrides)
    return definition


def template_definition(**overrides: Any) -> dict[str, Any]:
    """One complete template definition, as a browser sends it."""
    definition: dict[str, Any] = {
        "kind": "clip_look",
        "captionMode": "karaoke",
        "captionStyle": {
            "fontFamily": "Inter",
            "fontSize": 56,
            "color": "#FFFFFF",
            "highlightColor": "#FFD166",
            "align": "center",
            "weight": 600,
            "italic": False,
            "decoration": "none",
            "letterSpacing": 0.0,
            "lineHeight": 1.2,
            "backgroundEnabled": False,
            "backgroundColor": "#000000",
        },
        "textStyle": {
            "fontFamily": "Inter",
            "fontSize": 42,
            "color": "#FFFFFF",
            "align": "center",
            "weight": 500,
            "italic": False,
            "decoration": "none",
            "letterSpacing": 0.0,
            "lineHeight": 1.2,
            "backgroundEnabled": False,
            "backgroundColor": "#000000",
        },
    }
    definition.update(overrides)
    return definition


def _create_kit(fixture: _Fixture, **overrides: Any) -> Response:
    """Publish the first version of one Brand Kit."""
    body: dict[str, Any] = {"name": "Kanal Utama", "definition": kit_definition()}
    body.update(overrides)
    return fixture.browser.request("POST", _kits_path(fixture), json=body)


def _create_template(fixture: _Fixture, **overrides: Any) -> Response:
    """Publish the first version of one Workspace-owned look."""
    body: dict[str, Any] = {
        "name": "Sorotan",
        "brandKitId": None,
        "definition": template_definition(),
    }
    body.update(overrides)
    return fixture.browser.request("POST", _templates_path(fixture), json=body)


@pytest.mark.integration
def test_publishing_a_brand_kit_returns_its_first_version(
    engine: Engine, clean_database: None
) -> None:
    """A kit exists at a version from the moment it exists at all."""
    del clean_database
    fixture = _signed_in(engine)

    response = _create_kit(fixture)

    assert response.status_code == 201
    body = response.json()
    assert body["version"] == 1
    assert body["name"] == "Kanal Utama"
    assert body["definition"]["captionRules"]["reservedPlacements"] == ["lowerThird"]
    assert body["definition"]["visualExclusions"] == ["alcohol"]
    assert body["definition"]["claimRules"]["forbiddenClaimPhrases"] == ["dijamin untung"]
    assert body["archivedAt"] is None


@pytest.mark.integration
def test_editing_a_brand_kit_publishes_a_new_version_and_keeps_the_old_one(
    engine: Engine, clean_database: None
) -> None:
    """A clip judged under version 1 has to keep being judged under version 1."""
    del clean_database
    fixture = _signed_in(engine)
    kit_id = _create_kit(fixture).json()["id"]

    updated = fixture.browser.request(
        "PATCH",
        _kits_path(fixture, f"/{kit_id}"),
        json={"definition": kit_definition(visualExclusions=["alcohol", "gambling"])},
    )

    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    first = fixture.browser.get(_kits_path(fixture, f"/{kit_id}/versions/1"))
    assert first.status_code == 200
    assert first.json()["definition"]["visualExclusions"] == ["alcohol"]
    with Session(engine) as session:
        versions = session.scalars(
            select(BrandKitVersion.version).order_by(BrandKitVersion.version)
        ).all()
    assert list(versions) == [1, 2]


@pytest.mark.integration
def test_renaming_a_brand_kit_publishes_no_new_version(
    engine: Engine, clean_database: None
) -> None:
    """A name is not a rule, so changing one cannot re-judge anybody's clip."""
    del clean_database
    fixture = _signed_in(engine)
    kit_id = _create_kit(fixture).json()["id"]

    renamed = fixture.browser.request(
        "PATCH", _kits_path(fixture, f"/{kit_id}"), json={"name": "Kanal Kedua"}
    )

    assert renamed.status_code == 200
    assert renamed.json() == {**renamed.json(), "name": "Kanal Kedua", "version": 1}


@pytest.mark.integration
def test_a_published_version_can_never_be_rewritten_in_place(
    engine: Engine, clean_database: None
) -> None:
    """Every edit appends; nothing here offers a way to change what a version said."""
    del clean_database
    fixture = _signed_in(engine)
    kit_id = _create_kit(fixture).json()["id"]
    original = fixture.browser.get(_kits_path(fixture, f"/{kit_id}/versions/1")).json()

    fixture.browser.request(
        "PATCH",
        _kits_path(fixture, f"/{kit_id}"),
        json={"definition": kit_definition(visualExclusions=[])},
    )

    assert fixture.browser.get(_kits_path(fixture, f"/{kit_id}/versions/1")).json() == original


@pytest.mark.integration
@pytest.mark.parametrize(
    "definition",
    (
        kit_definition(colors=[{"name": "Paper", "hex": "white"}]),
        kit_definition(colors=[{"name": "Paper", "hex": "#FFF"}]),
        kit_definition(colors=[]),
        kit_definition(fonts=[]),
        kit_definition(fonts=[{"family": "Comic Sans", "assetId": None}]),
        kit_definition(
            captionRules={
                "minFontSize": 80,
                "maxFontSize": 40,
                "allowedAlignments": ["center"],
                "reservedPlacements": [],
            }
        ),
        kit_definition(
            captionRules={
                "minFontSize": 32,
                "maxFontSize": 72,
                "allowedAlignments": [],
                "reservedPlacements": [],
            }
        ),
    ),
)
def test_a_definition_nobody_could_satisfy_is_refused(
    engine: Engine, clean_database: None, definition: dict[str, Any]
) -> None:
    """A kit that constrains nothing, or contradicts itself, is worse than no kit at all."""
    del clean_database
    fixture = _signed_in(engine)

    response = _create_kit(fixture, definition=definition)

    assert response.status_code == 422
    with Session(engine) as session:
        assert session.scalars(select(BrandKit)).all() == []


@pytest.mark.integration
def test_a_kit_may_not_name_media_this_workspace_does_not_own(
    engine: Engine, clean_database: None
) -> None:
    """A logo and a licensed font are somebody's property before they are a design choice."""
    del clean_database
    fixture = _signed_in(engine)
    foreign_asset_id = _foreign_asset(engine)

    logo = _create_kit(fixture, definition=kit_definition(logoAssetId=str(foreign_asset_id)))
    font = _create_kit(
        fixture,
        definition=kit_definition(fonts=[{"family": "Inter", "assetId": str(uuid4())}]),
    )

    assert logo.status_code == font.status_code == 422
    assert logo.json()["error"]["code"] == "BRAND_ASSET_FORBIDDEN"
    with Session(engine) as session:
        assert session.scalars(select(BrandKit)).all() == []


@pytest.mark.integration
def test_a_kit_may_name_media_its_own_workspace_owns(engine: Engine, clean_database: None) -> None:
    """The check exists to refuse other people's media, not the Workspace's own."""
    del clean_database
    fixture = _signed_in(engine)
    logo_asset_id = _workspace_asset(engine, fixture)

    response = _create_kit(fixture, definition=kit_definition(logoAssetId=str(logo_asset_id)))

    assert response.status_code == 201
    assert response.json()["definition"]["logoAssetId"] == str(logo_asset_id)


@pytest.mark.integration
def test_deleting_a_brand_kit_archives_it_and_keeps_every_published_version(
    engine: Engine, clean_database: None
) -> None:
    """Compositions reference versions of this kit, and deletion may not orphan them."""
    del clean_database
    fixture = _signed_in(engine)
    kit_id = _create_kit(fixture).json()["id"]

    deleted = fixture.browser.request("DELETE", _kits_path(fixture, f"/{kit_id}"))

    assert deleted.status_code == 204
    detail = fixture.browser.get(_kits_path(fixture, f"/{kit_id}"))
    assert detail.status_code == 200
    assert detail.json()["archivedAt"] is not None
    assert fixture.browser.get(_kits_path(fixture, f"/{kit_id}/versions/1")).status_code == 200
    with Session(engine) as session:
        assert session.scalars(select(BrandKitVersion)).all()


@pytest.mark.integration
def test_an_archived_kit_is_left_out_of_the_list_unless_it_is_asked_for(
    engine: Engine, clean_database: None
) -> None:
    """A member picks from what they still use, and audits what they used to."""
    del clean_database
    fixture = _signed_in(engine)
    kit_id = _create_kit(fixture).json()["id"]
    fixture.browser.request("DELETE", _kits_path(fixture, f"/{kit_id}"))

    listed = fixture.browser.get(_kits_path(fixture))
    including = fixture.browser.get(
        f"/api/v1/brand-kits?workspace_id={fixture.workspace_id}&include_archived=true"
    )

    assert listed.json()["brandKits"] == []
    assert [kit["id"] for kit in including.json()["brandKits"]] == [kit_id]


@pytest.mark.integration
def test_an_archived_kit_cannot_be_edited_into_a_new_version(
    engine: Engine, clean_database: None
) -> None:
    """Archiving is a decision to stop using a kit, not a slower way of editing one."""
    del clean_database
    fixture = _signed_in(engine)
    kit_id = _create_kit(fixture).json()["id"]
    fixture.browser.request("DELETE", _kits_path(fixture, f"/{kit_id}"))

    response = fixture.browser.request(
        "PATCH", _kits_path(fixture, f"/{kit_id}"), json={"definition": kit_definition()}
    )

    assert_error(response, status_code=409, code="BRAND_KIT_ARCHIVED")


@pytest.mark.integration
def test_another_workspace_s_kit_answers_exactly_like_one_that_never_existed(
    engine: Engine, clean_database: None
) -> None:
    """A guessed identifier must be indistinguishable from one that does not exist."""
    del clean_database
    fixture = _signed_in(engine)
    foreign_kit_id = _foreign_kit(engine)

    missing = fixture.browser.get(_kits_path(fixture, f"/{uuid4()}"))
    foreign = fixture.browser.get(_kits_path(fixture, f"/{foreign_kit_id}"))

    assert missing.status_code == foreign.status_code == 404
    assert missing.json()["error"] == {
        **foreign.json()["error"],
        "requestId": missing.json()["error"]["requestId"],
    }


@pytest.mark.integration
def test_one_workspace_never_lists_another_workspace_s_kits(
    engine: Engine, clean_database: None
) -> None:
    """A brand is the Workspace's, and a Workspace is the boundary."""
    del clean_database
    fixture = _signed_in(engine)
    _foreign_kit(engine)
    _create_kit(fixture)

    listed = fixture.browser.get(_kits_path(fixture)).json()["brandKits"]

    assert [kit["name"] for kit in listed] == ["Kanal Utama"]


@pytest.mark.integration
@pytest.mark.parametrize("role", (WorkspaceRole.REVIEWER, WorkspaceRole.VIEWER))
def test_a_member_without_write_rights_may_read_a_brand_but_not_change_it(
    engine: Engine, clean_database: None, role: WorkspaceRole
) -> None:
    """Reading the brand is everybody's; deciding what it is belongs to an editor."""
    del clean_database
    fixture = _signed_in(engine)
    kit_id = _create_kit(fixture).json()["id"]
    _set_role(engine, fixture, role)

    assert fixture.browser.get(_kits_path(fixture)).status_code == 200
    assert_error(_create_kit(fixture), status_code=403, code="FORBIDDEN")
    assert_error(
        fixture.browser.request("DELETE", _kits_path(fixture, f"/{kit_id}")),
        status_code=403,
        code="FORBIDDEN",
    )


@pytest.mark.integration
def test_changing_a_brand_requires_csrf_proof(engine: Engine, clean_database: None) -> None:
    """Publishing a version writes rows, so it is a state-changing method."""
    del clean_database
    fixture = _signed_in(engine)

    response = fixture.browser.request(
        "POST",
        _kits_path(fixture),
        json={"name": "Kanal Utama", "definition": kit_definition()},
        csrf_token="",
    )

    assert response.status_code in {401, 403}


@pytest.mark.integration
def test_publishing_a_template_returns_its_first_version(
    engine: Engine, clean_database: None
) -> None:
    """A look is reusable only once it has an identity and a version to name."""
    del clean_database
    fixture = _signed_in(engine)

    response = _create_template(fixture)

    assert response.status_code == 201
    body = response.json()
    assert body["version"] == 1
    assert body["kind"] == "clip_look"
    assert body["definition"]["captionMode"] == "karaoke"


@pytest.mark.integration
def test_editing_a_template_publishes_a_new_version_and_keeps_the_old_one(
    engine: Engine, clean_database: None
) -> None:
    """A Revision built on version 1 has to keep rendering as version 1 rendered."""
    del clean_database
    fixture = _signed_in(engine)
    template_id = _create_template(fixture).json()["id"]

    updated = fixture.browser.request(
        "PATCH",
        _templates_path(fixture, f"/{template_id}"),
        json={"definition": template_definition(captionMode="block")},
    )

    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    first = fixture.browser.get(_templates_path(fixture, f"/{template_id}/versions/1"))
    assert first.json()["definition"]["captionMode"] == "karaoke"
    with Session(engine) as session:
        versions = session.scalars(
            select(BrandTemplateVersion.version).order_by(BrandTemplateVersion.version)
        ).all()
    assert list(versions) == [1, 2]


@pytest.mark.integration
def test_an_archived_template_still_resolves_for_the_clips_that_used_it(
    engine: Engine, clean_database: None
) -> None:
    """Archiving may not break a saved composition, which is why nothing is deleted."""
    del clean_database
    fixture = _signed_in(engine)
    template_id = _create_template(fixture).json()["id"]

    deleted = fixture.browser.request("DELETE", _templates_path(fixture, f"/{template_id}"))

    assert deleted.status_code == 204
    version = fixture.browser.get(_templates_path(fixture, f"/{template_id}/versions/1"))
    assert version.status_code == 200
    assert version.json()["definition"]["captionMode"] == "karaoke"
    assert fixture.browser.get(_templates_path(fixture)).json()["templates"] == []


@pytest.mark.integration
def test_a_template_may_only_name_a_brand_kit_of_its_own_workspace(
    engine: Engine, clean_database: None
) -> None:
    """A template that borrowed another Workspace's kit would leak that brand's rules."""
    del clean_database
    fixture = _signed_in(engine)
    foreign_kit_id = _foreign_kit(engine)

    response = _create_template(fixture, brandKitId=str(foreign_kit_id))

    assert response.status_code == 404


@pytest.mark.integration
def test_a_version_nobody_published_is_refused_rather_than_approximated(
    engine: Engine, clean_database: None
) -> None:
    """A near miss on a version is a different look, so it may not be guessed at."""
    del clean_database
    fixture = _signed_in(engine)
    template_id = _create_template(fixture).json()["id"]

    assert (
        fixture.browser.get(_templates_path(fixture, f"/{template_id}/versions/2")).status_code
        == 404
    )


@pytest.mark.integration
def test_renaming_a_template_or_repointing_it_publishes_no_new_version(
    engine: Engine, clean_database: None
) -> None:
    """A name and an owning brand are not the look, so changing them re-renders nothing."""
    del clean_database
    fixture = _signed_in(engine)
    template_id = _create_template(fixture).json()["id"]
    kit_id = _create_kit(fixture).json()["id"]

    renamed = fixture.browser.request(
        "PATCH",
        _templates_path(fixture, f"/{template_id}"),
        json={"name": "Sorotan Baru", "brandKitId": kit_id},
    )

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Sorotan Baru"
    assert renamed.json()["brandKitId"] == kit_id
    assert renamed.json()["version"] == 1


@pytest.mark.integration
def test_an_archived_template_is_listed_only_when_it_is_asked_for(
    engine: Engine, clean_database: None
) -> None:
    """A member picks from what they still use, and audits what they used to."""
    del clean_database
    fixture = _signed_in(engine)
    template_id = _create_template(fixture).json()["id"]
    fixture.browser.request("DELETE", _templates_path(fixture, f"/{template_id}"))

    including = fixture.browser.get(
        f"/api/v1/templates?workspace_id={fixture.workspace_id}&include_archived=true"
    )

    assert [template["id"] for template in including.json()["templates"]] == [template_id]
    assert including.json()["templates"][0]["archivedAt"] is not None


@pytest.mark.integration
def test_archiving_a_template_twice_changes_nothing_the_second_time(
    engine: Engine, clean_database: None
) -> None:
    """A repeated click is one decision, not a second one with a later timestamp."""
    del clean_database
    fixture = _signed_in(engine)
    template_id = _create_template(fixture).json()["id"]

    first = fixture.browser.request("DELETE", _templates_path(fixture, f"/{template_id}"))
    archived_at = fixture.browser.get(_templates_path(fixture, f"/{template_id}")).json()[
        "archivedAt"
    ]
    second = fixture.browser.request("DELETE", _templates_path(fixture, f"/{template_id}"))

    assert first.status_code == second.status_code == 204
    assert (
        fixture.browser.get(_templates_path(fixture, f"/{template_id}")).json()["archivedAt"]
        == archived_at
    )


@pytest.mark.integration
def test_an_archived_template_cannot_be_edited_into_a_new_version(
    engine: Engine, clean_database: None
) -> None:
    """Archiving is a decision to stop offering a look, not a slower way of editing one."""
    del clean_database
    fixture = _signed_in(engine)
    template_id = _create_template(fixture).json()["id"]
    fixture.browser.request("DELETE", _templates_path(fixture, f"/{template_id}"))

    response = fixture.browser.request(
        "PATCH",
        _templates_path(fixture, f"/{template_id}"),
        json={"definition": template_definition()},
    )

    assert_error(response, status_code=409, code="TEMPLATE_ARCHIVED")


@pytest.mark.integration
def test_a_template_or_a_kit_that_never_existed_is_absent_rather_than_refused(
    engine: Engine, clean_database: None
) -> None:
    """Every read of a brand answers a guess exactly as it answers a typo."""
    del clean_database
    fixture = _signed_in(engine)
    unknown = uuid4()

    assert fixture.browser.get(_templates_path(fixture, f"/{unknown}")).status_code == 404
    assert (
        fixture.browser.get(_templates_path(fixture, f"/{unknown}/versions/1")).status_code == 404
    )
    assert fixture.browser.get(_kits_path(fixture, f"/{unknown}/versions/1")).status_code == 404
    assert (
        fixture.browser.request(
            "PATCH", _templates_path(fixture, f"/{unknown}"), json={"name": "Apa pun"}
        ).status_code
        == 404
    )
    assert (
        fixture.browser.request("DELETE", _templates_path(fixture, f"/{unknown}")).status_code
        == 404
    )
    assert fixture.browser.request("DELETE", _kits_path(fixture, f"/{unknown}")).status_code == 404


def _signed_in(engine: Engine, **overrides: Any) -> _Fixture:
    """Sign one member in and give them a Workspace with one Project."""
    clock = Clock(NOW)
    app, flow, _ = build_app(clock, StubGoogleProvider(clock), **overrides)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    project_id = _project(browser, workspace_id, key="brand-project")
    del engine
    return _Fixture(browser=browser, workspace_id=workspace_id, project_id=project_id)


def _foreign_kit(engine: Engine) -> UUID:
    """Publish one Brand Kit inside a Workspace this member has no standing in."""
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    provider.identify(subject="5" * 21, email="foreign@example.com", name="Foreign Example")
    app, flow, _ = build_app(clock, provider)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    created = browser.request(
        "POST",
        f"/api/v1/brand-kits?workspace_id={workspace_id}",
        json={"name": "Brand Lain", "definition": kit_definition()},
    )
    assert created.status_code == 201
    del engine
    return UUID(created.json()["id"])


def _foreign_asset(engine: Engine) -> UUID:
    """Create one asset owned by a Workspace this member has no standing in."""
    clock = Clock(NOW)
    provider = StubGoogleProvider(clock)
    provider.identify(subject="6" * 21, email="other@example.com", name="Other Example")
    app, flow, _ = build_app(clock, provider)
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    project_id = _project(browser, workspace_id, key="foreign-brand-project")
    return _asset(engine, workspace_id, project_id)


def _workspace_asset(engine: Engine, fixture: _Fixture) -> UUID:
    """Create one asset the signed-in member's own Workspace owns."""
    return _asset(engine, fixture.workspace_id, fixture.project_id)


def _asset(engine: Engine, workspace_id: UUID, project_id: UUID) -> UUID:
    """Write one stored object a Brand Kit could name as its logo or its font file."""
    asset_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            Asset.__table__.insert().values(
                id=asset_id,
                workspace_id=workspace_id,
                project_id=project_id,
                kind=AssetKind.BROLL,
                source_type=AssetSourceType.USER_UPLOAD,
                storage_key=f"workspaces/{workspace_id}/projects/{project_id}/brand/{asset_id}",
                content_type="image/png",
                size_bytes=1_024,
                sha256=b"l" * 32,
            )
        )
    return asset_id


def _project(browser: Browser, workspace_id: UUID, *, key: str) -> UUID:
    """Create one Project through the API."""
    created = browser.request(
        "POST",
        f"/api/v1/projects?workspace_id={workspace_id}",
        headers={"Idempotency-Key": key},
        json={"name": key, "sourceKind": "upload"},
    )
    assert created.status_code == 201
    return UUID(created.json()["id"])


def _set_role(engine: Engine, fixture: _Fixture, role: WorkspaceRole) -> None:
    """Move the signed-in member to another role inside their own Workspace."""
    user_id = fixture.browser.get("/api/v1/me").json()["id"]
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workspace_memberships SET role = :role "
                "WHERE workspace_id = :workspace_id AND user_id = :user_id"
            ),
            {"role": role.value, "workspace_id": fixture.workspace_id, "user_id": user_id},
        )
