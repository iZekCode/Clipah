"""The environment inventory the cutover depends on stays true to the code.

Task 48 asks for an inventory of every environment variable and a map from each legacy key
to its replacement. An inventory written once and never checked is worse than none at all:
it is read as authority while it quietly goes stale. These tests make the documents fail
the build when a setting is added, renamed, or removed without being written down.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from clipah.config import Settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ENVIRONMENT_SETUP = REPOSITORY_ROOT / "ENVIRONMENT_SETUP.md"
CUTOVER_RUNBOOK = REPOSITORY_ROOT / "docs" / "operations" / "cutover.md"

#: Every unprefixed variable the legacy Flask stack read. The list is written here rather
#: than parsed out of ``app.py`` because the legacy module is deleted at the end of the
#: cutover, and the map from these names to their replacements has to outlive it.
LEGACY_ENVIRONMENT_VARIABLES = (
    "ASSEMBLYAI_API_KEY",
    "GROQ_API_KEY",
    "FLASK_DEBUG",
    "FLASK_ENV",
    "FLASK_HOST",
    "FLASK_PORT",
)

#: Variables that are read by a process, an image, or a test rather than by ``Settings``.
#: They are as real as any setting, so the inventory documents them, but they can never
#: appear in ``Settings.model_fields`` and the drift check has to know that. Adding a name
#: here is a deliberate statement that it belongs to the operational surface.
OPERATIONAL_VARIABLES = frozenset(
    {
        "CLIPAH_API_ORIGIN",
        "CLIPAH_BROLL_CONCURRENCY",
        "CLIPAH_CURL_BIN",
        "CLIPAH_DOCKER_BIN",
        "CLIPAH_E2E_BASE_URL",
        "CLIPAH_FORWARDED_ALLOW_IPS",
        "CLIPAH_IMAGE_TARGET",
        "CLIPAH_INGEST_AI_CONCURRENCY",
        "CLIPAH_JOB_WORKSPACE_ROOT",
        "CLIPAH_MEDIA_RUNTIME_REQUIRED",
        "CLIPAH_RENDER_CONCURRENCY",
        "CLIPAH_RUNTIME_ROLE",
        "CLIPAH_SECRET_MANAGER_KEY_NAME",
        "CLIPAH_SMOKE_CSRF_TOKEN",
        "CLIPAH_SMOKE_IDENTITY",
        "CLIPAH_SMOKE_SESSION_COOKIE",
        "CLIPAH_SOCIAL_PUBLISH_CONCURRENCY",
        "CLIPAH_SOCIAL_RECONCILE_CONCURRENCY",
        "CLIPAH_SOURCE_IMPORT_CONCURRENCY",
        "CLIPAH_TEST_API_RUNTIME_DATABASE_URL",
        "CLIPAH_TEST_DATABASE_URL",
        "CLIPAH_TEST_REDIS_URL",
        "CLIPAH_TEST_WORKER_RUNTIME_DATABASE_URL",
        "CLIPAH_WORKER_CONCURRENCY",
        "CLIPAH_WORKER_QUEUES",
    }
)


def _documented_variables(document: Path) -> set[str]:
    """Return every ``CLIPAH_``-prefixed name the document mentions anywhere."""
    return set(re.findall(r"\bCLIPAH_[A-Z0-9_]+\b", document.read_text(encoding="utf-8")))


def _settings_variables() -> set[str]:
    """Return the environment variable name of every configurable setting."""
    return {f"CLIPAH_{name.upper()}" for name in Settings.model_fields}


@pytest.mark.unit
def test_every_setting_appears_in_the_environment_inventory() -> None:
    """A setting nobody wrote down is a setting an operator cannot set deliberately."""
    undocumented = _settings_variables() - _documented_variables(ENVIRONMENT_SETUP)

    assert undocumented == set(), (
        "these settings exist in config.py but appear in no environment document: "
        f"{sorted(undocumented)}"
    )


@pytest.mark.unit
def test_the_inventory_documents_no_variable_that_nothing_reads() -> None:
    """A removed or misspelled key left in the documentation sends an operator nowhere."""
    invented = (
        _documented_variables(ENVIRONMENT_SETUP) - _settings_variables() - OPERATIONAL_VARIABLES
    )

    assert invented == set(), (
        "these variables are documented but are neither settings nor declared operational "
        f"variables: {sorted(invented)}"
    )


@pytest.mark.unit
def test_every_declared_operational_variable_is_documented() -> None:
    """A process or test variable is part of the inventory too, not a folk secret."""
    undocumented = OPERATIONAL_VARIABLES - _documented_variables(ENVIRONMENT_SETUP)

    assert undocumented == set(), (
        f"these operational variables appear in no environment document: {sorted(undocumented)}"
    )


@pytest.mark.unit
def test_the_cutover_runbook_maps_every_legacy_variable() -> None:
    """The cutover is auditable only if each legacy key names its replacement."""
    runbook = CUTOVER_RUNBOOK.read_text(encoding="utf-8")
    unmapped = [name for name in LEGACY_ENVIRONMENT_VARIABLES if name not in runbook]

    assert unmapped == [], f"these legacy variables are mapped nowhere: {unmapped}"


@pytest.mark.unit
def test_the_cutover_runbook_names_the_rollout_flag() -> None:
    """The flag that hides the new UI is the one control the rollout stages turn."""
    assert "NEW_CLIPAH_ENABLED" in CUTOVER_RUNBOOK.read_text(encoding="utf-8")
