"""Public contracts for comments and decisions bound to immutable Edit Revisions."""

from __future__ import annotations

import copy

import pytest
from sqlalchemy import Engine

from harness import assert_error
from integration.test_edit_revisions import _create_edit, _path, _reviewed_project, _save


@pytest.mark.integration
def test_a_comment_is_anchored_to_one_exact_revision_and_timestamp(engine: Engine) -> None:
    """Review feedback must keep meaning after later edits move the timeline."""
    stage = _reviewed_project(engine, collaboration_enabled=True)
    edit = _create_edit(stage.browser, stage.fixture).json()
    revision = stage.browser.get(_path(f"/edits/{edit['id']}/revisions", stage.fixture)).json()[
        "revisions"
    ][0]

    response = stage.browser.request(
        "POST",
        _path(f"/edits/{edit['id']}/review-comments", stage.fixture),
        json={
            "revisionId": revision["id"],
            "text": "Shorten this pause.",
            "anchor": {"kind": "timestamp", "timestampMs": 1_000},
        },
    )

    assert response.status_code == 201
    assert response.json()["revisionId"] == revision["id"]
    assert response.json()["anchor"] == {"kind": "timestamp", "timestampMs": 1_000}
    assert response.json()["resolved"] is False


@pytest.mark.integration
def test_approval_becomes_stale_when_a_new_revision_is_saved(engine: Engine) -> None:
    """Approval of old bytes must never authorize the composition that replaced them."""
    stage = _reviewed_project(engine, collaboration_enabled=True)
    edit = _create_edit(stage.browser, stage.fixture).json()
    revision_id = stage.browser.get(_path(f"/edits/{edit['id']}/revisions", stage.fixture)).json()[
        "revisions"
    ][0]["id"]
    approved = stage.browser.request(
        "POST",
        _path(f"/edits/{edit['id']}/reviews", stage.fixture),
        json={"revisionId": revision_id, "decision": "approve"},
    )
    assert approved.status_code == 201
    assert approved.json()["current"] is True

    changed = copy.deepcopy(edit["composition"])
    changed["audio"]["gainDb"] = -3.0
    assert (
        _save(
            stage.browser,
            stage.fixture,
            edit["id"],
            expected_revision=1,
            composition=changed,
        ).status_code
        == 200
    )

    history = stage.browser.get(_path(f"/edits/{edit['id']}/reviews", stage.fixture))

    assert history.status_code == 200
    assert history.json()["approved"] is False
    assert history.json()["decisions"][0]["revisionId"] == revision_id
    assert history.json()["decisions"][0]["current"] is False


@pytest.mark.integration
def test_request_changes_overrides_approval_for_the_same_revision(engine: Engine) -> None:
    """The latest decision on current bytes is the review state members must see."""
    stage = _reviewed_project(engine, collaboration_enabled=True)
    edit = _create_edit(stage.browser, stage.fixture).json()
    revision_id = stage.browser.get(_path(f"/edits/{edit['id']}/revisions", stage.fixture)).json()[
        "revisions"
    ][0]["id"]
    path = _path(f"/edits/{edit['id']}/reviews", stage.fixture)
    stage.browser.request("POST", path, json={"revisionId": revision_id, "decision": "approve"})

    changed = stage.browser.request(
        "POST", path, json={"revisionId": revision_id, "decision": "request_changes"}
    )

    assert changed.status_code == 201
    assert changed.json()["current"] is True
    history = stage.browser.get(path).json()
    assert history["approved"] is False
    assert [decision["decision"] for decision in history["decisions"]] == [
        "request_changes",
        "approve",
    ]


@pytest.mark.integration
def test_comment_resolution_and_reopening_preserve_audit_history(engine: Engine) -> None:
    """Resolving feedback changes derived state without rewriting the original comment."""
    stage = _reviewed_project(engine, collaboration_enabled=True)
    edit = _create_edit(stage.browser, stage.fixture).json()
    revision_id = stage.browser.get(_path(f"/edits/{edit['id']}/revisions", stage.fixture)).json()[
        "revisions"
    ][0]["id"]
    comment = stage.browser.request(
        "POST",
        _path(f"/edits/{edit['id']}/review-comments", stage.fixture),
        json={
            "revisionId": revision_id,
            "text": "Keep this title visible.",
            "anchor": {"kind": "item", "itemId": "scene-1"},
        },
    ).json()
    path = _path(f"/edit-review-comments/{comment['id']}/resolution", stage.fixture)

    assert stage.browser.request("POST", path, json={"resolved": True}).json()["resolved"] is True
    assert stage.browser.request("POST", path, json={"resolved": False}).json()["resolved"] is False

    summary = stage.browser.get(_path(f"/edits/{edit['id']}/reviews", stage.fixture)).json()
    assert summary["comments"][0]["text"] == "Keep this title visible."
    assert summary["comments"][0]["resolved"] is False


@pytest.mark.integration
def test_a_comment_cannot_anchor_to_an_item_outside_its_revision(engine: Engine) -> None:
    """A dangling item anchor would make review feedback silently point nowhere."""
    stage = _reviewed_project(engine, collaboration_enabled=True)
    edit = _create_edit(stage.browser, stage.fixture).json()
    revision_id = stage.browser.get(_path(f"/edits/{edit['id']}/revisions", stage.fixture)).json()[
        "revisions"
    ][0]["id"]

    refused = stage.browser.request(
        "POST",
        _path(f"/edits/{edit['id']}/review-comments", stage.fixture),
        json={
            "revisionId": revision_id,
            "text": "This item does not exist.",
            "anchor": {"kind": "item", "itemId": "unknown-item"},
        },
    )

    assert_error(refused, status_code=422, code="REVIEW_INVALID")


@pytest.mark.integration
def test_accessibility_quality_is_measured_for_one_immutable_revision(engine: Engine) -> None:
    """The editor reads deterministic advice for the exact bytes on screen."""
    stage = _reviewed_project(engine)
    edit = _create_edit(stage.browser, stage.fixture).json()
    revision_id = stage.browser.get(_path(f"/edits/{edit['id']}/revisions", stage.fixture)).json()[
        "revisions"
    ][0]["id"]

    response = stage.browser.get(
        _path(
            f"/edits/{edit['id']}/accessibility?revision_id={revision_id}&platform=tiktok",
            stage.fixture,
        )
    )

    assert response.status_code == 200
    assert response.json()["revisionId"] == revision_id
    assert [warning["code"] for warning in response.json()["warnings"]] == ["geometry_unavailable"]
