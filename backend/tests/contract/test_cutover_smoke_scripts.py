"""The two cutover smoke scripts observe the same things in a comparable shape.

Task 48 compares the legacy stack and the rebuilt stack on the same fixture input. A
comparison is only worth running if both sides report the same fields, and only worth
believing if a side that could not run says so instead of reporting an empty pass. These
tests drive both scripts through a fake HTTP client, so the request sequence and the
observation they write are checked without either stack being up.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
NEW_STACK_SMOKE = REPOSITORY_ROOT / "scripts" / "new-stack-smoke.sh"
LEGACY_SMOKE = REPOSITORY_ROOT / "scripts" / "legacy-smoke.sh"
FIXTURE = REPOSITORY_ROOT / "backend" / "tests" / "fixtures" / "media" / "landscape.mp4"

#: The fields both stacks report, so the two observations can be compared field by field.
COMPARABLE_FIELDS = ("stack", "status", "fixture", "candidates", "subtitles", "finalMedia")

NEW_STACK_CREDENTIALS = {
    "CLIPAH_SMOKE_SESSION_COOKIE": "clipah_session=smoke",
    "CLIPAH_SMOKE_CSRF_TOKEN": "smoke-csrf",
}


def _fake_curl(tmp_path: Path, responses: dict[str, str]) -> tuple[Path, Path]:
    """Write a curl stand-in that answers by URL substring and records every call.

    The real script is never given a URL it has not been taught to answer: an unmatched
    request exits non-zero, so a silently changed request sequence fails the test rather
    than falling through to an empty observation.
    """
    executable = tmp_path / "curl"
    body_directory = tmp_path / "bodies"
    body_directory.mkdir()
    calls = tmp_path / "calls.log"

    matcher_lines = []
    for index, (fragment, body) in enumerate(responses.items()):
        body_file = body_directory / f"{index}.json"
        body_file.write_text(body, encoding="utf-8")
        matcher_lines.append(f"  *'{fragment}'*) cat '{body_file}' ;;")

    executable.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$CLIPAH_FAKE_CURL_CALLS"\n'
        "CLIPAH_FAKE_CURL_ARGS=$*\n"
        # Honour --output the way curl does, so a script that saves a body to a file is
        # exercised rather than quietly reading an empty one.
        "destination=''\n"
        "while [ $# -gt 0 ]; do\n"
        '  [ "$1" = "--output" ] && destination=$2\n'
        "  shift\n"
        "done\n"
        "body() {\n"
        'case " $CLIPAH_FAKE_CURL_ARGS " in\n' + "\n".join(matcher_lines) + "\n"
        "  *) echo 'unexpected request' >&2; exit 7 ;;\n"
        "esac\n"
        "}\n"
        'if [ -n "$destination" ]; then body > "$destination"; else body; fi\n'
    )
    executable.chmod(0o755)
    return executable, calls


def _script_environment(**overrides: str) -> dict[str, str]:
    """Build the environment a script runs in.

    The scripts call `python3`, which resolves to this suite's own interpreter. pytest-cov
    auto-starts coverage in any such subprocess, and those subprocesses run from the
    repository root where the branch-coverage configuration is not found — which produces
    statement-only data files that then refuse to combine with the suite's own. Dropping the
    handover variables keeps the coverage run coherent.
    """
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("COV_CORE")
    }
    for key in NEW_STACK_CREDENTIALS:
        environment.pop(key, None)
    environment.update(overrides)
    return environment


def _run(
    script: Path,
    fake: Path,
    calls: Path,
    output: Path,
    *arguments: str,
    **overrides: str,
) -> subprocess.CompletedProcess[str]:
    environment = _script_environment(
        CLIPAH_CURL_BIN=str(fake),
        CLIPAH_FAKE_CURL_CALLS=str(calls),
        **overrides,
    )
    return subprocess.run(
        [str(script), "--fixture", str(FIXTURE), "--output", str(output), *arguments],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _new_stack_responses() -> dict[str, str]:
    return {
        "/api/v1/projects ": json.dumps({"id": "project-1"}),
        "/uploads ": json.dumps({"id": "upload-1", "url": "https://store.test/part"}),
        "https://store.test/part": "",
        "/complete": json.dumps({"assetId": "asset-1", "jobId": "job-1"}),
        "/jobs/job-1": json.dumps({"status": "succeeded", "stage": "analyze"}),
        "/analyses": json.dumps({"jobId": "job-1"}),
        "/candidates": json.dumps(
            {
                "items": [
                    {
                        "rank": 1,
                        "startMs": 1000,
                        "endMs": 31000,
                        "hook": "the one good bit",
                        "excerpt": "a sentence from the transcript",
                    }
                ]
            }
        ),
        "/renders": json.dumps({"items": []}),
    }


@pytest.mark.unit
def test_both_scripts_are_executable() -> None:
    """A runbook step that is not executable is a runbook step nobody runs."""
    assert os.access(NEW_STACK_SMOKE, os.X_OK)
    assert os.access(LEGACY_SMOKE, os.X_OK)


@pytest.mark.unit
def test_the_new_stack_smoke_reports_every_comparable_field(tmp_path: Path) -> None:
    """A comparison needs both sides to answer the same questions."""
    fake, calls = _fake_curl(tmp_path, _new_stack_responses())
    output = tmp_path / "new.json"

    completed = _run(NEW_STACK_SMOKE, fake, calls, output, **NEW_STACK_CREDENTIALS)

    assert completed.returncode == 0, completed.stderr
    observation = json.loads(output.read_text())
    assert set(COMPARABLE_FIELDS) <= observation.keys()
    assert observation["stack"] == "new"
    assert observation["status"] == "measured"
    assert observation["candidates"][0]["startMs"] == 1000
    assert observation["candidates"][0]["title"] == "the one good bit"


@pytest.mark.unit
def test_the_new_stack_smoke_sends_the_csrf_token_on_every_unsafe_request(
    tmp_path: Path,
) -> None:
    """An unsafe request without the double-submit token is refused by the API."""
    fake, calls = _fake_curl(tmp_path, _new_stack_responses())

    _run(NEW_STACK_SMOKE, fake, calls, tmp_path / "new.json", **NEW_STACK_CREDENTIALS)

    unsafe = [call for call in calls.read_text().splitlines() if "POST" in call]
    assert unsafe, "the smoke never wrote anything"
    assert all("X-CSRF-Token: smoke-csrf" in call for call in unsafe)


@pytest.mark.unit
def test_the_new_stack_smoke_reports_unmeasured_without_credentials(tmp_path: Path) -> None:
    """A smoke that cannot authenticate has not passed; it has not run."""
    fake, calls = _fake_curl(tmp_path, _new_stack_responses())
    output = tmp_path / "new.json"

    completed = _run(NEW_STACK_SMOKE, fake, calls, output)

    assert completed.returncode != 0
    assert json.loads(output.read_text())["status"] == "unmeasured"


@pytest.mark.unit
def test_the_legacy_smoke_reports_unmeasured_when_no_deployment_is_given(
    tmp_path: Path,
) -> None:
    """After the legacy deployment is gone this is the only honest answer it can give."""
    fake, calls = _fake_curl(tmp_path, {})
    output = tmp_path / "legacy.json"

    completed = _run(LEGACY_SMOKE, fake, calls, output)

    assert completed.returncode != 0
    observation = json.loads(output.read_text())
    assert observation["status"] == "unmeasured"
    assert observation["stack"] == "legacy"
    assert set(COMPARABLE_FIELDS) <= observation.keys()


@pytest.mark.unit
def test_the_legacy_smoke_reports_every_comparable_field(tmp_path: Path) -> None:
    """The legacy side of the comparison answers in the same shape as the new side."""
    fake, calls = _fake_curl(
        tmp_path,
        {
            "/process": json.dumps({"status": "started"}),
            "/status": json.dumps(
                {
                    "status": "complete",
                    "clips": [
                        {
                            "start_time": 1.0,
                            "end_time": 31.0,
                            "title": "the one good bit",
                            "transcript": "a sentence from the transcript",
                        }
                    ],
                    "subtitles": True,
                }
            ),
            "/download": "PK",
        },
    )
    output = tmp_path / "legacy.json"

    completed = _run(LEGACY_SMOKE, fake, calls, output, "--base-url", "http://legacy.test")

    assert completed.returncode == 0, completed.stderr
    observation = json.loads(output.read_text())
    assert set(COMPARABLE_FIELDS) <= observation.keys()
    assert observation["stack"] == "legacy"
    assert observation["status"] == "measured"
    assert observation["candidates"][0]["startMs"] == 1000
    assert observation["candidates"][0]["title"] == "the one good bit"
    assert observation["subtitles"] is True
    assert observation["finalMedia"]["looksLikeZip"] is True


@pytest.mark.unit
def test_a_missing_fixture_is_refused_by_both_scripts(tmp_path: Path) -> None:
    """Comparing two stacks on different inputs would prove nothing at all."""
    fake, calls = _fake_curl(tmp_path, {})

    for script in (NEW_STACK_SMOKE, LEGACY_SMOKE):
        completed = subprocess.run(
            [
                str(script),
                "--fixture",
                str(tmp_path / "absent.mp4"),
                "--output",
                str(tmp_path / "out.json"),
            ],
            cwd=REPOSITORY_ROOT,
            env=_script_environment(
                CLIPAH_CURL_BIN=str(fake),
                CLIPAH_FAKE_CURL_CALLS=str(calls),
                **NEW_STACK_CREDENTIALS,
            ),
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode != 0
        assert "fixture" in completed.stderr.lower()
