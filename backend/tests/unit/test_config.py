"""Configuration contracts for deployment profiles."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from clipah.config import Environment, Settings


@pytest.mark.unit
def test_production_rejects_missing_required_service_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing a production dependency setting must prevent startup."""
    monkeypatch.setenv("CLIPAH_ENVIRONMENT", "production")

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.unit
def test_production_requires_a_separate_migration_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing the admin-only DSN must keep it out of the application runtime pool."""
    production_environment(monkeypatch)
    monkeypatch.delenv("CLIPAH_MIGRATION_DATABASE_URL")

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.unit
def test_production_rejects_shared_runtime_and_migration_database_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One database principal must not pass as two credentials through URL differences."""
    production_environment(monkeypatch)
    monkeypatch.setenv(
        "CLIPAH_MIGRATION_DATABASE_URL",
        "postgresql+psycopg://clipah:different-password@migration-db/other?sslmode=require",
    )

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.unit
def test_production_worker_process_requires_its_own_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting the worker process must not silently reuse API credentials."""
    production_environment(monkeypatch)
    monkeypatch.setenv("CLIPAH_PROCESS_ROLE", "worker")

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.unit
def test_production_worker_process_can_omit_the_api_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A separate worker deployment needs only its own least-privilege runtime DSN."""
    production_environment(monkeypatch)
    monkeypatch.setenv("CLIPAH_PROCESS_ROLE", "worker")
    monkeypatch.delenv("CLIPAH_DATABASE_URL")
    monkeypatch.setenv(
        "CLIPAH_WORKER_DATABASE_URL",
        "postgresql+psycopg://clipah_worker_runtime:worker@db/clipah",
    )

    settings = Settings()

    assert settings.process_role == "worker"
    assert settings.database_url is None
    assert settings.worker_database_url is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("process_role", "foreign_setting"),
    (
        ("api", "CLIPAH_WORKER_DATABASE_URL"),
        ("worker", "CLIPAH_DATABASE_URL"),
    ),
)
def test_production_rejects_holding_the_other_process_database_credentials(
    monkeypatch: pytest.MonkeyPatch,
    process_role: str,
    foreign_setting: str,
) -> None:
    """One process must reach exactly one runtime role group, never both."""
    production_environment(monkeypatch)
    monkeypatch.setenv("CLIPAH_PROCESS_ROLE", process_role)
    monkeypatch.setenv(
        "CLIPAH_WORKER_DATABASE_URL",
        "postgresql+psycopg://clipah_worker_runtime:worker@db/clipah",
    )

    with pytest.raises(ValidationError, match=foreign_setting):
        Settings()


@pytest.mark.unit
def test_production_allows_disabled_unapproved_social_integrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled providers do not require OAuth credentials or audit approval."""
    production_environment(monkeypatch)

    settings = Settings()

    assert settings.youtube_publishing_enabled is False
    assert settings.instagram_publishing_enabled is False
    assert settings.tiktok_publishing_enabled is False
    assert settings.debug is False
    assert settings.session_cookie_name == "__Host-clipah_session"
    assert settings.session_cookie_secure is True
    assert settings.secret_encryption_enabled is True


@pytest.mark.unit
@pytest.mark.parametrize("environment", [Environment.LOCAL, Environment.TEST, Environment.STAGING])
def test_non_production_profiles_are_available_without_deployment_credentials(
    environment: Environment,
) -> None:
    """Local, test, and staging profiles remain usable for isolated development."""
    settings = Settings(environment=environment)

    assert settings.environment is environment


@pytest.mark.unit
def test_local_profile_loads_developer_dotenv_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A local developer can supply non-secret defaults through a nearby ``.env`` file."""
    monkeypatch.delenv("CLIPAH_DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "CLIPAH_DATABASE_URL=postgresql+psycopg://clipah:clipah@localhost/clipah\n",
        encoding="utf-8",
    )

    settings = Settings(environment=Environment.LOCAL)

    assert settings.database_url == "postgresql+psycopg://clipah:clipah@localhost/clipah"


@pytest.mark.unit
def test_dotenv_cannot_select_production_or_supply_production_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only an explicit local process or constructor profile may opt into dotenv."""
    for name in production_environment_values():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(f"{name}={value}" for name, value in production_environment_values().items()),
        encoding="utf-8",
    )

    settings = Settings()

    assert settings.environment is Environment.LOCAL
    assert settings.database_url is None


@pytest.mark.unit
def test_production_accepts_a_secret_manager_instead_of_an_encryption_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A managed encryption key is a valid production secret-encryption mechanism."""
    production_environment(monkeypatch)
    monkeypatch.delenv("CLIPAH_SECRET_ENCRYPTION_KEY")
    monkeypatch.setenv(
        "CLIPAH_SECRET_MANAGER_KEY_NAME",
        "projects/clipah/locations/global/keyRings/app/cryptoKeys/secrets",
    )

    settings = Settings()

    assert settings.secret_manager_key_name is not None


@pytest.mark.unit
def test_production_rejects_retired_provider_api_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retired provider API version cannot reach a production deployment."""
    production_environment(monkeypatch)
    monkeypatch.setenv("CLIPAH_INSTAGRAM_API_VERSION", "v1")

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.unit
@pytest.mark.parametrize(
    "setting",
    [
        "CLIPAH_DATABASE_URL",
        "CLIPAH_MIGRATION_DATABASE_URL",
        "CLIPAH_REDIS_URL",
        "CLIPAH_OBJECT_STORE_ENDPOINT",
        "CLIPAH_OBJECT_STORE_BUCKET",
        "CLIPAH_OBJECT_STORE_ACCESS_KEY_ID",
        "CLIPAH_OBJECT_STORE_SECRET_ACCESS_KEY",
        "CLIPAH_GOOGLE_OIDC_CLIENT_ID",
        "CLIPAH_GOOGLE_OIDC_CLIENT_SECRET",
        "CLIPAH_ASSEMBLYAI_API_KEY",
        "CLIPAH_GROQ_API_KEY",
        "CLIPAH_SESSION_SECRET",
        "CLIPAH_SECRET_ENCRYPTION_KEY",
    ],
)
def test_production_rejects_each_missing_required_setting(
    monkeypatch: pytest.MonkeyPatch,
    setting: str,
) -> None:
    """Each foundational production dependency is independently required."""
    production_environment(monkeypatch)
    monkeypatch.delenv(setting)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.unit
@pytest.mark.parametrize(
    "setting",
    [
        "CLIPAH_OBJECT_STORE_ACCESS_KEY_ID",
        "CLIPAH_OBJECT_STORE_SECRET_ACCESS_KEY",
        "CLIPAH_GOOGLE_OIDC_CLIENT_SECRET",
        "CLIPAH_ASSEMBLYAI_API_KEY",
        "CLIPAH_GROQ_API_KEY",
        "CLIPAH_SESSION_SECRET",
        "CLIPAH_SECRET_ENCRYPTION_KEY",
    ],
)
@pytest.mark.parametrize("blank_value", ["", " \t "])
def test_production_rejects_blank_required_secrets(
    monkeypatch: pytest.MonkeyPatch,
    setting: str,
    blank_value: str,
) -> None:
    """Blank SecretStr values cannot satisfy production dependency requirements."""
    production_environment(monkeypatch)
    monkeypatch.setenv(setting, blank_value)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.unit
@pytest.mark.parametrize(
    "enabled_setting",
    [
        "CLIPAH_YOUTUBE_PUBLISHING_ENABLED",
        "CLIPAH_INSTAGRAM_PUBLISHING_ENABLED",
        "CLIPAH_TIKTOK_PUBLISHING_ENABLED",
    ],
)
def test_enabled_social_provider_requires_its_credentials_and_audit_approval(
    monkeypatch: pytest.MonkeyPatch,
    enabled_setting: str,
) -> None:
    """An enabled social integration cannot bypass its credentials or approval gate."""
    production_environment(monkeypatch)
    monkeypatch.setenv(enabled_setting, "true")

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.unit
@pytest.mark.parametrize(
    "provider_environment",
    [
        {
            "CLIPAH_YOUTUBE_PUBLISHING_ENABLED": "true",
            "CLIPAH_YOUTUBE_OAUTH_CLIENT_ID": "youtube-client-id",
            "CLIPAH_YOUTUBE_OAUTH_CLIENT_SECRET": "youtube-client-secret",
            "CLIPAH_YOUTUBE_API_KEY": "youtube-api-key",
        },
        {
            "CLIPAH_INSTAGRAM_PUBLISHING_ENABLED": "true",
            "CLIPAH_INSTAGRAM_OAUTH_CLIENT_ID": "instagram-client-id",
            "CLIPAH_INSTAGRAM_OAUTH_CLIENT_SECRET": "instagram-client-secret",
        },
        {
            "CLIPAH_TIKTOK_PUBLISHING_ENABLED": "true",
            "CLIPAH_TIKTOK_CLIENT_KEY": "tiktok-client-key",
            "CLIPAH_TIKTOK_CLIENT_SECRET": "tiktok-client-secret",
        },
    ],
)
def test_enabled_social_provider_requires_audit_approval_after_credentials_are_present(
    monkeypatch: pytest.MonkeyPatch,
    provider_environment: dict[str, str],
) -> None:
    """Credentials alone cannot enable a social provider without its audit gate."""
    production_environment(monkeypatch)
    for name, value in provider_environment.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("provider_environment", "blank_secret_setting"),
    [
        (
            {
                "CLIPAH_YOUTUBE_PUBLISHING_ENABLED": "true",
                "CLIPAH_YOUTUBE_OAUTH_CLIENT_ID": "youtube-client-id",
                "CLIPAH_YOUTUBE_OAUTH_CLIENT_SECRET": "youtube-client-secret",
                "CLIPAH_YOUTUBE_API_KEY": "youtube-api-key",
                "CLIPAH_YOUTUBE_AUDIT_APPROVED": "true",
            },
            "CLIPAH_YOUTUBE_OAUTH_CLIENT_SECRET",
        ),
        (
            {
                "CLIPAH_YOUTUBE_PUBLISHING_ENABLED": "true",
                "CLIPAH_YOUTUBE_OAUTH_CLIENT_ID": "youtube-client-id",
                "CLIPAH_YOUTUBE_OAUTH_CLIENT_SECRET": "youtube-client-secret",
                "CLIPAH_YOUTUBE_API_KEY": "youtube-api-key",
                "CLIPAH_YOUTUBE_AUDIT_APPROVED": "true",
            },
            "CLIPAH_YOUTUBE_API_KEY",
        ),
        (
            {
                "CLIPAH_INSTAGRAM_PUBLISHING_ENABLED": "true",
                "CLIPAH_INSTAGRAM_OAUTH_CLIENT_ID": "instagram-client-id",
                "CLIPAH_INSTAGRAM_OAUTH_CLIENT_SECRET": "instagram-client-secret",
                "CLIPAH_INSTAGRAM_AUDIT_APPROVED": "true",
            },
            "CLIPAH_INSTAGRAM_OAUTH_CLIENT_SECRET",
        ),
        (
            {
                "CLIPAH_TIKTOK_PUBLISHING_ENABLED": "true",
                "CLIPAH_TIKTOK_CLIENT_KEY": "tiktok-client-key",
                "CLIPAH_TIKTOK_CLIENT_SECRET": "tiktok-client-secret",
                "CLIPAH_TIKTOK_AUDIT_APPROVED": "true",
            },
            "CLIPAH_TIKTOK_CLIENT_SECRET",
        ),
    ],
)
def test_enabled_social_provider_rejects_blank_secrets(
    monkeypatch: pytest.MonkeyPatch,
    provider_environment: dict[str, str],
    blank_secret_setting: str,
) -> None:
    """An approved provider still requires non-blank OAuth/API secrets."""
    production_environment(monkeypatch)
    for name, value in provider_environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(blank_secret_setting, " \t ")

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.unit
@pytest.mark.parametrize("blank_value", ["", " \t "])
def test_production_rejects_blank_secret_manager_when_encryption_key_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    blank_value: str,
) -> None:
    """A blank Secret Manager identifier cannot replace an encryption key."""
    production_environment(monkeypatch)
    monkeypatch.delenv("CLIPAH_SECRET_ENCRYPTION_KEY")
    monkeypatch.setenv("CLIPAH_SECRET_MANAGER_KEY_NAME", blank_value)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("CLIPAH_DEBUG", "true"),
        ("CLIPAH_SESSION_COOKIE_NAME", "clipah_session"),
        ("CLIPAH_SESSION_COOKIE_SECURE", "false"),
        ("CLIPAH_SECRET_ENCRYPTION_ENABLED", "false"),
    ],
)
def test_production_rejects_unsafe_security_overrides(
    monkeypatch: pytest.MonkeyPatch,
    setting: str,
    value: str,
) -> None:
    """Explicit environment values cannot override production security defaults."""
    production_environment(monkeypatch)
    monkeypatch.setenv(setting, value)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.unit
def test_production_rejects_retired_model_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """A retired provider model cannot reach a production deployment."""
    production_environment(monkeypatch)
    monkeypatch.setenv("CLIPAH_GROQ_EXTRACTION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.unit
def test_gpt_oss_aliases_follow_the_configured_groq_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stable GPT-OSS aliases expose the selected provider model IDs."""
    monkeypatch.setenv("CLIPAH_GROQ_EXTRACTION_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setenv("CLIPAH_GROQ_RERANKING_MODEL", "openai/gpt-oss-120b")

    settings = Settings(environment=Environment.TEST)

    assert settings.gpt_oss_extraction_model == "openai/gpt-oss-20b"
    assert settings.gpt_oss_reranking_model == "openai/gpt-oss-120b"


def production_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Populate the smallest complete, intentionally provider-disabled production environment."""
    for name, value in production_environment_values().items():
        monkeypatch.setenv(name, value)


def production_environment_values() -> dict[str, str]:
    """Return the smallest complete, intentionally provider-disabled production environment."""
    return {
        "CLIPAH_ENVIRONMENT": "production",
        "CLIPAH_DATABASE_URL": "postgresql+psycopg://clipah:clipah@db/clipah",
        "CLIPAH_MIGRATION_DATABASE_URL": ("postgresql+psycopg://clipah_migrator:admin@db/clipah"),
        "CLIPAH_REDIS_URL": "redis://redis:6379/0",
        "CLIPAH_OBJECT_STORE_ENDPOINT": "https://storage.example.test",
        "CLIPAH_OBJECT_STORE_BUCKET": "clipah-production",
        "CLIPAH_OBJECT_STORE_ACCESS_KEY_ID": "access-key",
        "CLIPAH_OBJECT_STORE_SECRET_ACCESS_KEY": "secret-key",
        "CLIPAH_GOOGLE_OIDC_CLIENT_ID": "google-client-id",
        "CLIPAH_GOOGLE_OIDC_CLIENT_SECRET": "google-client-secret",
        "CLIPAH_ASSEMBLYAI_API_KEY": "assemblyai-key",
        "CLIPAH_GROQ_API_KEY": "groq-key",
        "CLIPAH_SESSION_SECRET": "s" * 32,
        "CLIPAH_SECRET_ENCRYPTION_KEY": "e" * 32,
    }
