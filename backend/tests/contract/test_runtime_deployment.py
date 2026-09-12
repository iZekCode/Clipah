"""Deployment contracts shared by local Compose and production service definitions."""

from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).parents[3]
COMPOSE_FILE = REPOSITORY_ROOT / "infra" / "compose.yaml"
VERIFY_SCRIPT = REPOSITORY_ROOT / "scripts" / "verify-runtime.sh"
RAILWAY_ROOT = REPOSITORY_ROOT / "infra" / "railway"
EXPECTED_SERVICES = {
    "api",
    "frontend",
    "migrate",
    "minio",
    "minio-init",
    "postgres",
    "redis",
    "runtime-smoke",
    "scheduler",
    "source-import",
    "worker-broll",
    "worker-ingest-ai",
    "worker-render",
    "worker-social-publish",
    "worker-social-reconcile",
}
APPLICATION_SERVICES = EXPECTED_SERVICES - {"postgres", "redis", "minio", "minio-init"}
WORKER_SERVICES = {
    "source-import": ("source_import", "1", None),
    "worker-ingest-ai": ("ingest,ai,maintenance", "2", "media-worker"),
    "worker-broll": ("broll_retrieve,broll_generate", "2", "worker"),
    "worker-render": ("render,social_rendition", "1", "media-worker"),
    "worker-social-publish": ("social_publish", "2", "worker"),
    "worker-social-reconcile": ("social_reconcile", "2", "worker"),
}
PROVIDER_PREFIXES = (
    "CLIPAH_ASSEMBLYAI_",
    "CLIPAH_GROQ_",
    "CLIPAH_PEXELS_",
    "CLIPAH_PIXABAY_",
    "CLIPAH_FAL_",
    "CLIPAH_RUNWAY_",
    "CLIPAH_YOUTUBE_",
    "CLIPAH_INSTAGRAM_",
    "CLIPAH_TIKTOK_",
)
RAILWAY_SERVICES = {
    "api.toml": ("api", None),
    "worker-ingest-ai.toml": ("media-worker", "ingest,ai,maintenance"),
    "worker-source-import.toml": ("runtime", "source_import"),
    "worker-broll.toml": ("worker", "broll_retrieve,broll_generate"),
    "worker-render.toml": ("media-worker", "render,social_rendition"),
    "worker-social-publish.toml": ("worker", "social_publish"),
    "worker-social-reconcile.toml": ("worker", "social_reconcile"),
    "scheduler.toml": ("scheduler", None),
}


def _render_compose() -> dict[str, Any]:
    """Render the effective Compose model through the CLI used by operators."""
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--profile",
            "smoke",
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    rendered: dict[str, Any] = json.loads(completed.stdout)
    return rendered


def test_compose_exposes_every_independently_operated_process() -> None:
    """Dropping a process from Compose must not silently make a Task 46 workflow unavailable."""
    services = set(_render_compose()["services"])

    assert services >= EXPECTED_SERVICES


def test_compose_application_services_are_bounded_and_observable() -> None:
    """Every long-running application process gets the same hard deployment floor."""
    services = _render_compose()["services"]

    for name in APPLICATION_SERVICES - {"migrate", "runtime-smoke"}:
        service = services[name]
        assert service["user"] == "10001:10001"
        assert service["read_only"] is True
        assert service["init"] is True
        assert service["stop_grace_period"] == "45s"
        assert service["healthcheck"]["test"]
        assert service["deploy"]["resources"]["limits"]["cpus"] > 0
        assert int(service["deploy"]["resources"]["limits"]["memory"]) > 0
        writable = " ".join(service.get("tmpfs", ()))
        assert "/tmp" in writable
        assert "/var/lib/clipah/job-workspaces" in writable


def test_compose_workers_own_explicit_queues_and_concurrency() -> None:
    """Queue ownership is independently tunable and never falls back to Celery defaults."""
    services = _render_compose()["services"]

    for name, (queues, concurrency, target) in WORKER_SERVICES.items():
        service = services[name]
        environment = service["environment"]
        if name == "source-import":
            assert environment["CLIPAH_SOURCE_IMPORT_CONCURRENCY"] == concurrency
        else:
            assert environment["CLIPAH_WORKER_QUEUES"] == queues
            assert environment["CLIPAH_WORKER_CONCURRENCY"] == concurrency
            assert service["build"]["target"] == target


def test_compose_provider_credentials_are_confined_to_their_workers() -> None:
    """A compromised worker must not inherit credentials for unrelated providers."""
    services = _render_compose()["services"]
    allowed_prefixes = {
        "source-import": (),
        "worker-ingest-ai": ("CLIPAH_ASSEMBLYAI_", "CLIPAH_GROQ_"),
        "worker-broll": (
            "CLIPAH_PEXELS_",
            "CLIPAH_PIXABAY_",
            "CLIPAH_FAL_",
            "CLIPAH_RUNWAY_",
        ),
        "worker-render": (),
        "worker-social-publish": (
            "CLIPAH_YOUTUBE_",
            "CLIPAH_INSTAGRAM_",
            "CLIPAH_TIKTOK_",
        ),
        "worker-social-reconcile": (
            "CLIPAH_YOUTUBE_",
            "CLIPAH_INSTAGRAM_",
            "CLIPAH_TIKTOK_",
        ),
    }

    for name, allowed in allowed_prefixes.items():
        keys = services[name]["environment"]
        provider_keys = {
            key for key in keys if any(key.startswith(prefix) for prefix in PROVIDER_PREFIXES)
        }
        assert all(any(key.startswith(prefix) for prefix in allowed) for key in provider_keys)


def test_compose_storage_credentials_are_only_given_to_artifact_consumers() -> None:
    services = _render_compose()["services"]
    consumers = {
        "api",
        "source-import",
        "worker-ingest-ai",
        "worker-broll",
        "worker-render",
        "worker-social-publish",
    }

    for name in APPLICATION_SERVICES:
        has_storage_secret = (
            "CLIPAH_OBJECT_STORE_SECRET_ACCESS_KEY" in services[name]["environment"]
        )
        assert has_storage_secret is (name in consumers)


def test_compose_infrastructure_ports_are_loopback_only() -> None:
    services = _render_compose()["services"]

    for name in ("postgres", "redis", "minio"):
        assert all(port["host_ip"] == "127.0.0.1" for port in services[name]["ports"])


def _fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    executable = tmp_path / "docker"
    calls = tmp_path / "calls.log"
    executable.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$CLIPAH_FAKE_DOCKER_CALLS"\n'
        'case " $* " in\n'
        "  *' up '*) exit \"${CLIPAH_FAKE_UP_EXIT:-0}\" ;;\n"
        "  *' run '*) exit \"${CLIPAH_FAKE_RUN_EXIT:-0}\" ;;\n"
        "esac\n"
    )
    executable.chmod(0o755)
    return executable, calls


def _run_verifier(fake: Path, calls: Path, **overrides: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(VERIFY_SCRIPT)],
        cwd=REPOSITORY_ROOT,
        env={
            **os.environ,
            "CLIPAH_DOCKER_BIN": str(fake),
            "CLIPAH_FAKE_DOCKER_CALLS": str(calls),
            **overrides,
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_verify_runtime_uses_bounded_wait_and_replays_one_smoke_identity(tmp_path: Path) -> None:
    fake, calls = _fake_docker(tmp_path)

    completed = _run_verifier(fake, calls)

    assert completed.returncode == 0
    recorded = calls.read_text().splitlines()
    assert any("--wait-timeout 300" in call and " up " in f" {call} " for call in recorded)
    assert any("--profile smoke build runtime-smoke" in call for call in recorded)
    smoke_calls = [call for call in recorded if " run " in f" {call} "]
    assert len(smoke_calls) == 2
    assert all("--rm --no-deps runtime-smoke" in call for call in smoke_calls)
    assert not any(" down" in call or "volume rm" in call for call in recorded)


def test_verify_runtime_failure_prints_only_bounded_service_logs(tmp_path: Path) -> None:
    fake, calls = _fake_docker(tmp_path)

    completed = _run_verifier(fake, calls, CLIPAH_FAKE_UP_EXIT="1")

    assert completed.returncode == 1
    recorded = calls.read_text().splitlines()
    assert any("logs --no-color --tail 80" in call for call in recorded)
    assert not any(" down" in call or "volume rm" in call for call in recorded)


def test_railway_processes_select_exact_images_commands_and_shutdown() -> None:
    for filename, (target, queues) in RAILWAY_SERVICES.items():
        configuration = tomllib.loads((RAILWAY_ROOT / filename).read_text())
        build = configuration["build"]
        deploy = configuration["deploy"]
        assert build["builder"] == "DOCKERFILE"
        assert build["dockerfileTarget"] == target
        assert build["dockerfilePath"].startswith("/infra/docker/")
        assert deploy["startCommand"]
        assert deploy["restartPolicyType"] == "ON_FAILURE"
        assert deploy["restartPolicyMaxRetries"] == 10
        assert deploy["drainingSeconds"] == "45"
        assert deploy["healthCommand"]
        if queues is not None:
            assert queues in deploy["startCommand"]


def test_railway_files_name_requirements_without_embedding_credentials() -> None:
    for filename in RAILWAY_SERVICES:
        source = (RAILWAY_ROOT / filename).read_text()
        configuration = tomllib.loads(source)
        required = configuration["clipah"]["requiredVariables"]
        assert all(name.isupper() and name.startswith("CLIPAH_") for name in required)
        if filename != "worker-source-import.toml":
            assert configuration["clipah"]["imageTargetVariable"] == "CLIPAH_IMAGE_TARGET"
        assert "fake-" not in source
        assert "local_secret" not in source
        assert "local-session" not in source
