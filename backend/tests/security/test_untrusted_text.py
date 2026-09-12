"""Text a language model wrote, and text a stock catalogue wrote, is data everywhere.

None of it is typed by anyone this deployment can hold responsible: a clip's hook and
reason come from a model reading somebody else's words, and a stock asset's description and
attribution come from a provider's catalogue. Two rules follow, and this suite holds both.

The API returns that text exactly as it was stored — not escaped, not rewritten — because
the browser renders it as React children and escaping it here would corrupt an apostrophe
while protecting nothing. And it never reaches a place where it would be read as syntax:
the render compiler writes it into a sidecar document rather than into an FFmpeg filter
argument, which is the difference between a caption and a command.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from brand_fixtures import SOURCE_ASSET_ID as FIXTURE_SOURCE_ASSET_ID
from brand_fixtures import caption_style, composition_document
from clipah.assets.storage import FakeObjectStore
from clipah.db import RuntimeRole
from clipah.dev.seed import seed_analysed_project
from clipah.editor.models import parse_composition
from clipah.models import AssetKind
from clipah.renders.compiler import compile_render_plan
from clipah.renders.ffmpeg_renderer import build_render_arguments
from clipah.renders.models import RenderAsset, RenderPreset
from harness import NOW, Browser, Clock, StubGoogleProvider, build_app, sign_in
from support import RUNTIME_LOGINS, runtime_settings

# One string carrying every syntax somebody downstream might read: HTML, an ASS override
# block, an FFmpeg filter separator, a quoted option, and a newline.
HOSTILE_TEXT = "<img src=x onerror=alert(1)> {\\an8} 'quoted':drawtext=text='x' [0:v]\nsecond"
SOURCE_ASSET_ID = UUID(str(FIXTURE_SOURCE_ASSET_ID))


@pytest.mark.integration
def test_model_written_text_comes_back_exactly_as_it_was_stored(
    engine: Engine, clean_database: None
) -> None:
    """The browser renders this as text, so the API must not rewrite it on the way out."""
    del clean_database
    clock = Clock(NOW)
    app, flow, _ = build_app(
        clock, StubGoogleProvider(clock), object_store=FakeObjectStore(now=clock)
    )
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    user_id = UUID(browser.get("/api/v1/me").json()["id"])
    seeded = seed_analysed_project(
        settings=runtime_settings(worker_database_url=RUNTIME_LOGINS[RuntimeRole.WORKER][1]),
        workspace_id=workspace_id,
        user_id=user_id,
        name="Untrusted text",
        now=clock(),
    )
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE clip_candidates SET hook = :value, reason = :value WHERE id = :id"),
            {"value": HOSTILE_TEXT, "id": seeded.candidate_id},
        )

    response = browser.get(
        f"/api/v1/projects/{seeded.project_id}/candidates/{seeded.candidate_id}"
        f"?workspace_id={workspace_id}"
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["hook"] == HOSTILE_TEXT
    assert response.json()["reason"] == HOSTILE_TEXT
    # Escaping here would be a silent corruption: `&lt;` is not what the model wrote.
    assert "&lt;" not in response.text
    assert "&amp;" not in response.text


@pytest.mark.unit
def test_untrusted_text_reaches_the_render_as_a_document_never_as_a_filter_argument() -> None:
    """FFmpeg's filter syntax is a language, and none of this text may be parsed as one."""
    plan = compile_render_plan(
        _composition_with(HOSTILE_TEXT),
        assets={
            SOURCE_ASSET_ID: RenderAsset(
                asset_id=SOURCE_ASSET_ID,
                kind=AssetKind.SOURCE,
                content_type="video/mp4",
                duration_ms=600_000,
                width=1920,
                height=1080,
            )
        },
        preset=RenderPreset.PORTRAIT,
        workspace=Path("/tmp/clipah-untrusted-text"),
    )

    assert "drawtext=text=" not in plan.filter_script
    for fragment in ("onerror", "[0:v]\nsecond", "{\\an8}"):
        assert fragment not in plan.filter_script
    written = "\n".join(document.contents for document in plan.files)
    assert "onerror" in written, "the words themselves must still reach the caption document"
    arguments = build_render_arguments(
        plan, output=Path("/tmp/clipah-untrusted-text/out.mp4"), ffmpeg_path="ffmpeg"
    )
    for argument in arguments:
        assert "onerror" not in argument


def _composition_with(caption_text: str) -> Any:
    """One valid composition whose caption words carry the untrusted text."""
    document = composition_document(
        captions={
            "mode": "block",
            "words": [
                {
                    "id": "w1",
                    "startMs": 0,
                    "endMs": 900,
                    "text": caption_text,
                    "speaker": "SPEAKER_00",
                }
            ],
            "style": caption_style(),
        },
        overlays=[],
    )
    return parse_composition(document)
