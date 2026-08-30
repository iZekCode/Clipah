"""Typed, fail-closed configuration for Clipah services."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from sqlalchemy.engine import make_url


class Environment(StrEnum):
    """Deployment profiles supported by the backend."""

    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Load application configuration from the environment and local ``.env`` files only."""

    model_config = SettingsConfigDict(
        env_prefix="CLIPAH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Environment.LOCAL
    debug: bool = True

    database_url: str | None = None
    migration_database_url: str | None = None
    redis_url: str | None = None

    object_store_endpoint: str | None = None
    object_store_bucket: str | None = None
    object_store_access_key_id: SecretStr | None = None
    object_store_secret_access_key: SecretStr | None = None

    google_oidc_client_id: str | None = None
    google_oidc_client_secret: SecretStr | None = None

    assemblyai_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    groq_extraction_model: str = "openai/gpt-oss-20b"
    groq_reranking_model: str = "openai/gpt-oss-120b"

    session_secret: SecretStr | None = None
    session_cookie_name: str = "clipah_session"
    session_cookie_secure: bool = False
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    secret_encryption_key: SecretStr | None = None
    secret_manager_key_name: str | None = None
    secret_encryption_enabled: bool = False

    authenticated_source_import_enabled: bool = False
    generative_video_enabled: bool = False
    social_publishing_enabled: bool = False

    youtube_publishing_enabled: bool = False
    youtube_oauth_client_id: str | None = None
    youtube_oauth_client_secret: SecretStr | None = None
    youtube_api_key: SecretStr | None = None
    youtube_api_version: str = "v3"
    youtube_audit_approved: bool = False

    instagram_publishing_enabled: bool = False
    instagram_oauth_client_id: str | None = None
    instagram_oauth_client_secret: SecretStr | None = None
    instagram_api_version: str = "v22.0"
    instagram_audit_approved: bool = False

    tiktok_publishing_enabled: bool = False
    tiktok_client_key: str | None = None
    tiktok_client_secret: SecretStr | None = None
    tiktok_api_version: str = "v2"
    tiktok_audit_approved: bool = False

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Allow dotenv only after a trusted source explicitly selects the local profile."""
        del cls, settings_cls, file_secret_settings
        source_profile = init_settings().get("environment", env_settings().get("environment"))
        if _is_local_profile(source_profile):
            return init_settings, env_settings, dotenv_settings
        return init_settings, env_settings

    @property
    def gpt_oss_extraction_model(self) -> str:
        """Expose the configured GPT-OSS candidate-extraction model alias."""
        return self.groq_extraction_model

    @property
    def gpt_oss_reranking_model(self) -> str:
        """Expose the configured GPT-OSS global-reranking model alias."""
        return self.groq_reranking_model

    @model_validator(mode="before")
    @classmethod
    def apply_production_defaults(cls, data: Any) -> Any:
        """Apply safe defaults before production settings are validated."""
        if not isinstance(data, dict):
            return data

        values = dict(data)
        is_production = values.get("environment") in (Environment.PRODUCTION, "production")
        if is_production:
            values.setdefault("debug", False)
            values.setdefault("session_cookie_name", "__Host-clipah_session")
            values.setdefault("session_cookie_secure", True)
            values.setdefault("secret_encryption_enabled", True)
            values.setdefault("authenticated_source_import_enabled", False)
            values.setdefault("generative_video_enabled", False)
            values.setdefault("social_publishing_enabled", False)
        return values

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        """Fail closed for missing production dependencies and unsafe provider choices."""
        if self.environment is not Environment.PRODUCTION:
            return self

        self._validate_production_requirements()
        self._validate_production_security_defaults()
        self._validate_provider_versions()
        self._validate_enabled_social_providers()
        return self

    def _validate_production_requirements(self) -> None:
        required_values: dict[str, object | None] = {
            "CLIPAH_DATABASE_URL": self.database_url,
            "CLIPAH_MIGRATION_DATABASE_URL": self.migration_database_url,
            "CLIPAH_REDIS_URL": self.redis_url,
            "CLIPAH_OBJECT_STORE_ENDPOINT": self.object_store_endpoint,
            "CLIPAH_OBJECT_STORE_BUCKET": self.object_store_bucket,
            "CLIPAH_OBJECT_STORE_ACCESS_KEY_ID": self.object_store_access_key_id,
            "CLIPAH_OBJECT_STORE_SECRET_ACCESS_KEY": self.object_store_secret_access_key,
            "CLIPAH_GOOGLE_OIDC_CLIENT_ID": self.google_oidc_client_id,
            "CLIPAH_GOOGLE_OIDC_CLIENT_SECRET": self.google_oidc_client_secret,
            "CLIPAH_ASSEMBLYAI_API_KEY": self.assemblyai_api_key,
            "CLIPAH_GROQ_API_KEY": self.groq_api_key,
            "CLIPAH_SESSION_SECRET": self.session_secret,
        }
        missing = [name for name, value in required_values.items() if _is_blank(value)]
        if _is_blank(self.secret_encryption_key) and _is_blank(self.secret_manager_key_name):
            missing.append("CLIPAH_SECRET_ENCRYPTION_KEY or CLIPAH_SECRET_MANAGER_KEY_NAME")
        if missing:
            raise ValueError(f"missing required production settings: {', '.join(missing)}")

        if self.database_url is None or self.migration_database_url is None:
            raise ValueError("production database URLs are required")
        runtime_principal = make_url(self.database_url).username
        migration_principal = make_url(self.migration_database_url).username
        if (
            runtime_principal is None
            or migration_principal is None
            or runtime_principal == migration_principal
        ):
            raise ValueError(
                "CLIPAH_DATABASE_URL and CLIPAH_MIGRATION_DATABASE_URL must use separate "
                "database principals"
            )

        if self.session_secret is not None and len(self.session_secret.get_secret_value()) < 32:
            raise ValueError("CLIPAH_SESSION_SECRET must contain at least 32 characters")
        if (
            self.secret_encryption_key is not None
            and len(self.secret_encryption_key.get_secret_value()) < 32
        ):
            raise ValueError("CLIPAH_SECRET_ENCRYPTION_KEY must contain at least 32 characters")

    def _validate_production_security_defaults(self) -> None:
        if self.debug:
            raise ValueError("production debug must be disabled")
        if self.session_cookie_name != "__Host-clipah_session":
            raise ValueError("production session cookies must use the __Host- prefix")
        if not self.session_cookie_secure:
            raise ValueError("production session cookies must require HTTPS")
        if not self.secret_encryption_enabled:
            raise ValueError("production secret encryption must be enabled")

    def _validate_provider_versions(self) -> None:
        retired_models = {
            "llama-4-scout-17b-16e-instruct",
            "meta-llama/llama-4-scout-17b-16e-instruct",
        }
        configured_models = {self.groq_extraction_model, self.groq_reranking_model}
        if retired_models & configured_models:
            raise ValueError("retired model configuration is not permitted")

        retired_api_versions = {"v1", "v1.0", "legacy"}
        configured_api_versions = {
            self.youtube_api_version.lower(),
            self.instagram_api_version.lower(),
            self.tiktok_api_version.lower(),
        }
        if retired_api_versions & configured_api_versions:
            raise ValueError("retired provider API configuration is not permitted")

    def _validate_enabled_social_providers(self) -> None:
        providers = (
            (
                "YouTube",
                self.youtube_publishing_enabled,
                (
                    self.youtube_oauth_client_id,
                    self.youtube_oauth_client_secret,
                    self.youtube_api_key,
                ),
                self.youtube_audit_approved,
            ),
            (
                "Instagram",
                self.instagram_publishing_enabled,
                (self.instagram_oauth_client_id, self.instagram_oauth_client_secret),
                self.instagram_audit_approved,
            ),
            (
                "TikTok",
                self.tiktok_publishing_enabled,
                (self.tiktok_client_key, self.tiktok_client_secret),
                self.tiktok_audit_approved,
            ),
        )
        for provider, enabled, credentials, audit_approved in providers:
            if not enabled:
                continue
            if any(_is_blank(credential) for credential in credentials):
                raise ValueError(f"{provider} publishing requires its OAuth/API credentials")
            if not audit_approved:
                raise ValueError(f"{provider} publishing requires audit approval")


def _is_local_profile(value: object | None) -> bool:
    """Return whether a process or constructor source selected the local profile."""
    if isinstance(value, Environment):
        return value is Environment.LOCAL
    return isinstance(value, str) and value.strip().lower() == Environment.LOCAL.value


def _is_blank(value: object | None) -> bool:
    """Treat absent, empty, and whitespace-only plain or secret values as missing."""
    if value is None:
        return True
    if isinstance(value, SecretStr):
        return not value.get_secret_value().strip()
    return isinstance(value, str) and not value.strip()
