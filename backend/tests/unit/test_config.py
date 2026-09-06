"""Configuration contracts for deployment profiles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
        "CLIPAH_FRONTEND_ORIGIN",
        "CLIPAH_GOOGLE_OIDC_CLIENT_ID",
        "CLIPAH_GOOGLE_OIDC_CLIENT_SECRET",
        "CLIPAH_GOOGLE_OIDC_REDIRECT_URI",
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


@pytest.mark.unit
@pytest.mark.parametrize(
    "origin",
    ["http://app.clipah.test", "https://app.clipah.test/", "https://app.clipah.test/app"],
)
def test_production_requires_a_bare_https_frontend_origin(
    monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    """Only a scheme-and-host https origin can be compared against a browser Origin header."""
    production_environment(monkeypatch)
    monkeypatch.setenv("CLIPAH_FRONTEND_ORIGIN", origin)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.unit
def test_production_requires_an_https_oidc_redirect_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    """An authorization code must never be returned over plaintext HTTP."""
    production_environment(monkeypatch)
    monkeypatch.setenv(
        "CLIPAH_GOOGLE_OIDC_REDIRECT_URI", "http://api.clipah.test/api/v1/auth/google/callback"
    )

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.unit
def test_production_companion_cookies_inherit_the_host_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CSRF and login-ceremony cookies are host-locked wherever the Session cookie is."""
    production_environment(monkeypatch)

    settings = Settings()

    assert settings.session_cookie_name == "__Host-clipah_session"
    assert settings.csrf_cookie_name == "__Host-clipah_csrf"
    assert settings.oidc_state_cookie_name == "__Host-clipah_oidc"


@pytest.mark.unit
def test_local_companion_cookies_drop_the_host_prefix() -> None:
    """Local development over plain HTTP cannot satisfy the __Host- prefix rules."""
    settings = Settings(environment=Environment.TEST)

    assert settings.csrf_cookie_name == "clipah_csrf"
    assert settings.oidc_state_cookie_name == "clipah_oidc"


@pytest.mark.unit
def test_plan_limits_are_configuration_rather_than_route_code() -> None:
    """Section 7 processing limits must ship as settings a deployment can retune."""
    settings = Settings(environment=Environment.TEST)

    assert settings.read_requests_per_minute == 60
    assert settings.write_requests_per_minute == 20
    assert settings.analyses_per_hour == 3
    assert settings.concurrent_jobs_per_workspace == 5


@pytest.mark.unit
def test_monthly_workspace_budgets_are_configuration() -> None:
    """Every metered resource carries its own retunable monthly Workspace budget."""
    settings = Settings(environment=Environment.TEST)

    assert settings.monthly_analyses == 30
    assert settings.monthly_stock_requests == 200
    assert settings.monthly_generated_images == 50
    assert settings.monthly_generated_videos == 10
    assert settings.monthly_generated_seconds == 300
    assert settings.monthly_social_publications == 100


@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides",
    [
        {"analysis_window_target_min_ms": 181_000},
        {"analysis_window_overlap_ms": 120_000},
        {"analysis_candidate_min_duration_ms": 91_000},
        {"analysis_candidates_exposed": 31},
    ],
)
def test_analysis_policy_settings_reject_contradictory_ranges(
    overrides: dict[str, int],
) -> None:
    """Workers must never start with an impossible window, duration, or exposure policy."""
    with pytest.raises(ValidationError):
        Settings(environment=Environment.TEST, **overrides)  # type: ignore[arg-type]


@pytest.mark.unit
def test_generation_configuration_uses_approved_fail_closed_defaults() -> None:
    """A new deployment must select approved fal models while leaving video and audio off."""
    settings = Settings(environment=Environment.TEST)

    assert settings.generated_image_provider == "fal"
    assert settings.generated_video_provider == "fal"
    assert settings.fal_image_model_alias == "image-default"
    assert settings.fal_image_model_id == "fal-ai/nano-banana-2"
    assert settings.fal_video_model_alias == "video-default"
    assert settings.fal_video_model_id == "fal-ai/kling-video/v2.6/pro/text-to-video"
    assert settings.generative_video_enabled is False
    assert settings.generated_audio_enabled is False
    assert settings.generation_poll_seconds >= 5


@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides",
    (
        {"generation_max_duration_ms": 0},
        {"generation_max_output_bytes": 0},
        {"generation_http_timeout_seconds": 0},
        {"generation_poll_seconds": 4.99},
        {"generation_poll_attempt_deadline_seconds": 0},
        {"generation_adapter_retry_count": 0},
        {"generation_circuit_failure_threshold": 0},
        {"generation_circuit_cooldown_seconds": 0},
        {"generation_estimate_token_ttl_seconds": 0},
    ),
)
def test_generation_configuration_rejects_unsafe_numeric_bounds(
    overrides: dict[str, int | float],
) -> None:
    """A zero, negative, or provider-hostile bound must prevent worker startup."""
    with pytest.raises(ValidationError):
        Settings(environment=Environment.TEST, **overrides)  # type: ignore[arg-type]


@pytest.mark.unit
def test_generation_duration_cannot_exceed_the_broll_shot_ceiling() -> None:
    """A generated video must fit the placement whose visual intent authorized it."""
    with pytest.raises(ValidationError):
        Settings(
            environment=Environment.TEST,
            broll_max_shot_ms=5_000,
            generation_max_duration_ms=5_001,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides",
    (
        {"fal_api_key": "secret", "fal_webhook_base_url": None},
        {"fal_api_key": "secret", "fal_image_model_alias": ""},
        {"fal_api_key": "secret", "fal_image_model_id": ""},
        {"fal_api_key": "secret", "fal_video_model_alias": ""},
        {"fal_api_key": "secret", "fal_video_model_id": ""},
        {"runway_api_secret": "secret", "runway_video_model_alias": None},
        {"runway_api_secret": "secret", "runway_video_model_id": None},
        {"runway_video_model_alias": "runway-default", "runway_api_secret": None},
        {"runway_video_model_id": "gen4_turbo", "runway_api_secret": None},
    ),
)
def test_generation_configuration_rejects_partial_provider_pairs(
    overrides: dict[str, str | None],
) -> None:
    """A half-configured provider must be unavailable at startup, not fail after admission."""
    with pytest.raises(ValidationError):
        Settings(environment=Environment.TEST, **overrides)  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize(
    "webhook_base_url",
    (
        "http://api.clipah.test",
        "https://api.clipah.test/path",
        "https://api.clipah.test?secret=value",
        "https://webhook-user:webhook-password@api.clipah.test",
    ),
)
def test_fal_credentials_require_a_bare_https_webhook_origin(webhook_base_url: str) -> None:
    """Webhook construction must not inherit an insecure scheme, path, query, or credential."""
    with pytest.raises(ValidationError):
        Settings(
            environment=Environment.TEST,
            fal_api_key="secret",
            fal_webhook_base_url=webhook_base_url,
        )


@pytest.mark.unit
def test_generation_validation_errors_hide_signed_webhook_input() -> None:
    """A rejected signed webhook URL must not echo its secret token into logs or responses."""
    secret_token = "q7X9"

    with pytest.raises(ValidationError) as raised:
        Settings(
            environment=Environment.TEST,
            fal_api_key="secret",
            fal_webhook_base_url=f"https://api.clipah.test?token={secret_token}",
        )

    assert secret_token not in str(raised.value)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("fal_image_model_alias", "SoRa-still"),
        ("fal_image_model_id", "openai/sora-image"),
        ("fal_video_model_alias", "SORA"),
        ("fal_video_model_id", "openai/Sora-2"),
        ("runway_video_model_alias", "sora-compatible"),
        ("runway_video_model_id", "vendor/SORA"),
    ),
)
def test_generation_configuration_rejects_sora_in_aliases_and_provider_model_ids(
    field: str,
    value: str,
) -> None:
    """The retired Sora integration must not return through capitalization or indirection."""
    values: dict[str, Any] = {field: value}
    if field.startswith("runway_"):
        values.update(
            runway_api_secret="secret",
            runway_video_model_alias="runway-default",
            runway_video_model_id="gen4_turbo",
        )
        values[field] = value

    with pytest.raises(ValidationError, match="Sora"):
        Settings(environment=Environment.TEST, **values)


@pytest.mark.unit
def test_selecting_runway_for_video_requires_its_own_credentials() -> None:
    """Naming a provider whose credentials are absent would fail only after admission."""
    with pytest.raises(ValidationError):
        Settings(environment=Environment.TEST, generated_video_provider="runway")


@pytest.mark.unit
def test_selecting_runway_for_video_is_valid_once_it_is_fully_configured() -> None:
    """A complete Runway configuration is the one way its video provider becomes selectable."""
    settings = Settings(
        environment=Environment.TEST,
        generated_video_provider="runway",
        runway_api_secret="secret",
        runway_video_model_alias="runway-default",
        runway_video_model_id="gen4_turbo",
    )

    assert settings.generated_video_provider == "runway"


@pytest.mark.unit
def test_generated_audio_cannot_be_enabled() -> None:
    """Generated B-roll must remain visually interchangeable and discard provider audio."""
    with pytest.raises(ValidationError):
        Settings(environment=Environment.TEST, generated_audio_enabled=True)


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
        "CLIPAH_FRONTEND_ORIGIN": "https://app.clipah.test",
        "CLIPAH_GOOGLE_OIDC_CLIENT_ID": "google-client-id",
        "CLIPAH_GOOGLE_OIDC_CLIENT_SECRET": "google-client-secret",
        "CLIPAH_GOOGLE_OIDC_REDIRECT_URI": "https://api.clipah.test/api/v1/auth/google/callback",
        "CLIPAH_ASSEMBLYAI_API_KEY": "assemblyai-key",
        "CLIPAH_GROQ_API_KEY": "groq-key",
        "CLIPAH_SESSION_SECRET": "s" * 32,
        "CLIPAH_SECRET_ENCRYPTION_KEY": "e" * 32,
    }
