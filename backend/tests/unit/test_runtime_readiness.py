"""Unit contracts for fail-closed media worker startup readiness."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

import pytest
from httpx import ASGITransport, AsyncClient

from clipah.runtime.api import app
from clipah.runtime.readiness import MediaCapabilityVerifier, RuntimeReadinessError
from clipah.runtime.worker import parse_worker_queues


class RecordingRunner:
    """Return deterministic command output and retain the commands readiness required."""

    def __init__(self, outputs: Mapping[tuple[str, ...], str]) -> None:
        """Bind complete command vectors to their simulated output."""
        self.outputs = dict(outputs)
        self.calls: list[tuple[str, ...]] = []

    def run(self, arguments: Sequence[str]) -> str:
        """Return one configured result without accepting an approximate command."""
        command = tuple(arguments)
        self.calls.append(command)
        return self.outputs[command]


def valid_outputs() -> dict[tuple[str, ...], str]:
    """Return hand-written output containing every reviewed runtime capability."""
    return {
        ("ffmpeg", "-version"): "ffmpeg version 7.1.5-0+deb13u1 Copyright\n",
        ("ffprobe", "-version"): "ffprobe version 7.1.5-0+deb13u1 Copyright\n",
        ("ffmpeg", "-hide_banner", "-encoders"): (
            "Encoders:\n V..... libx264 H.264\n A..... aac AAC\n"
        ),
        ("ffmpeg", "-hide_banner", "-filters"): (
            "Filters:\n ... subtitles V->V\n ... drawtext V->V\n ... zoompan V->V\n"
        ),
        ("fc-match", "--format", "%{family}\n", "Noto Sans"): "Noto Sans\n",
    }


@pytest.mark.unit
def test_media_readiness_accepts_only_the_complete_reviewed_runtime() -> None:
    """A worker may start only after every renderer dependency is observed together."""
    runner = RecordingRunner(valid_outputs())

    MediaCapabilityVerifier(runner=runner).verify()

    assert runner.calls == list(valid_outputs())


@pytest.mark.unit
@pytest.mark.parametrize(
    ("command", "output"),
    [
        (("ffmpeg", "-version"), "ffmpeg version 7.1.6\n"),
        (("ffprobe", "-version"), "ffprobe version 7.1.6\n"),
        (
            ("ffmpeg", "-hide_banner", "-encoders"),
            "Encoders:\n A..... aac AAC\n",
        ),
        (
            ("ffmpeg", "-hide_banner", "-encoders"),
            "Encoders:\n V..... libx264 H.264\n",
        ),
        (
            ("ffmpeg", "-hide_banner", "-filters"),
            "Filters:\n ... drawtext V->V\n ... zoompan V->V\n",
        ),
        (
            ("ffmpeg", "-hide_banner", "-filters"),
            "Filters:\n ... subtitles V->V\n ... zoompan V->V\n",
        ),
        (
            ("ffmpeg", "-hide_banner", "-filters"),
            "Filters:\n ... subtitles V->V\n ... drawtext V->V\n",
        ),
        (("fc-match", "--format", "%{family}\n", "Noto Sans"), "DejaVu Sans\n"),
    ],
)
def test_media_readiness_rejects_version_or_capability_drift(
    command: tuple[str, ...], output: str
) -> None:
    """A partial native runtime must fail with one stable message and no command output."""
    outputs = valid_outputs()
    outputs[command] = output

    with pytest.raises(RuntimeReadinessError, match=r"^media runtime unavailable$"):
        MediaCapabilityVerifier(runner=RecordingRunner(outputs)).verify()


@pytest.mark.unit
def test_general_worker_accepts_only_explicit_non_source_queue_ownership() -> None:
    """A deployment typo must not make a broad worker consume unreviewed work."""
    assert parse_worker_queues("ingest,ai,maintenance") == ("ingest", "ai", "maintenance")

    for invalid in ("", "ingest,ingest", "source_import", "unknown"):
        with pytest.raises(
            RuntimeReadinessError, match=r"^worker queue configuration unavailable$"
        ):
            parse_worker_queues(invalid)


@pytest.mark.unit
def test_api_composition_root_serves_liveness_without_external_work() -> None:
    """The container entry point must construct the real API without probing dependencies."""

    async def request_liveness() -> tuple[int, dict[str, str]]:
        """Call the ASGI application in process without opening a network socket."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://clipah.test"
        ) as client:
            response = await client.get("/health/live")
        return response.status_code, response.json()

    status_code, body = asyncio.run(request_liveness())

    assert status_code == 200
    assert body == {"status": "ok", "version": "0.1.0"}
