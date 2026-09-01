"""Explicitly opted-in live readiness smoke test for the pinned source-import image."""

from __future__ import annotations

import os

import pytest

from clipah.source_connectors.readiness import check_runtime


@pytest.mark.slow
@pytest.mark.skipif(
    os.environ.get("CLIPAH_SOURCE_IMPORT_NETWORK_PROBE") != "1"
    or not os.environ.get("CLIPAH_SOURCE_IMPORT_NETWORK_PROBE_URL"),
    reason="live source-import smoke test is explicitly opt-in",
)
def test_pinned_runtime_extracts_public_metadata_with_ejs_and_deno() -> None:
    """A release operator may prove the full live extractor stack without enabling CI network."""
    versions = check_runtime()

    assert versions["yt-dlp-ejs"] == "0.8.0"
    assert versions["deno"] == "2.9.5"
