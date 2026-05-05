"""
SwasthaAI Application Configuration.

All settings are loaded from environment variables using pydantic-settings.
No hardcoded secrets or environment-specific values anywhere in code.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central settings object for the SwasthaAI Layer 0 ingestion service.

    Loaded entirely from environment variables (or a .env file in development).
    Validate on startup — bad config fails fast rather than at runtime.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ────────────────────────────────────────────────────────────
    environment: Literal["development", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    secret_key: str = Field(..., min_length=32)

    # ── PostgreSQL ──────────────────────────────────────────────────────────────
    database_url: str = Field(
        ...,
        description="asyncpg-compatible DSN: postgresql+asyncpg://user:pass@host/db",
    )

    # ── MinIO ───────────────────────────────────────────────────────────────────
    minio_endpoint: str = Field(..., description="host:port, no scheme")
    minio_access_key: str = Field(...)
    minio_secret_key: str = Field(...)
    minio_bucket_raw: str = "swastha-ai-raw-documents"
    minio_use_ssl: bool = False

    # ── Kafka ───────────────────────────────────────────────────────────────────
    kafka_bootstrap_servers: str = Field(
        ..., description="Comma-separated broker list: host:port,host:port"
    )

    # ── Redis ───────────────────────────────────────────────────────────────────
    redis_url: str = Field(..., description="redis://:password@host:port/db")

    # ── Keycloak ────────────────────────────────────────────────────────────────
    keycloak_url: str = Field(..., description="Base URL, no trailing slash")
    keycloak_realm: str = "swastha-ai"
    keycloak_client_id: str = "swastha-ai-api"

    # ── Upload Limits ────────────────────────────────────────────────────────────
    max_upload_size_mb: int = Field(default=100, ge=1, le=500)

    allowed_mime_types: list[str] = [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/xml",
        "text/csv",
        "application/json",
        "application/zip",
    ]

    # ── Rate Limiting ────────────────────────────────────────────────────────────
    rate_limit_per_minute: int = Field(default=60, ge=1)
    rate_limit_user_per_minute: int = Field(default=20, ge=1)
    rate_limit_api_client_per_minute: int = Field(default=30, ge=1)
    rate_limit_whitelist: list[str] = ["127.0.0.1", "::1"]

    # ── ClamAV ───────────────────────────────────────────────────────────────────
    clamav_host: str | None = None
    clamav_port: int = 3310
    virus_scan_webhook_url: str | None = None

    # ── API Keys (M2M) ────────────────────────────────────────────────────────────
    # Format: "key:role:description,key2:role2:description2"
    api_keys: str = ""

    # ── Adapters ─────────────────────────────────────────────────────────────────
    sugam_api_base_url: str = "https://sugam.cdsco.gov.in/api/v1"
    sugam_api_key: str = ""
    md_online_api_base_url: str = "https://mdonline.cdsco.gov.in/api/v1"
    md_online_api_key: str = ""
    adapter_poll_interval_seconds: int = Field(default=60, ge=10)

    # ── Derived Properties ───────────────────────────────────────────────────────
    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def keycloak_jwks_url(self) -> str:
        return (
            f"{self.keycloak_url}/realms/{self.keycloak_realm}"
            f"/protocol/openid-connect/certs"
        )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def parsed_api_keys(self) -> dict[str, dict[str, str]]:
        """
        Parse the API_KEYS env var into a dict: {key: {role, description}}.
        Input format: "key:role:description,key2:role2:description2"
        """
        result: dict[str, dict[str, str]] = {}
        if not self.api_keys:
            return result
        for entry in self.api_keys.split(","):
            parts = entry.strip().split(":", 2)
            if len(parts) == 3:
                key, role, description = parts
                result[key.strip()] = {"role": role.strip(), "description": description.strip()}
        return result

    @field_validator("allowed_mime_types", mode="before")
    @classmethod
    def parse_mime_types(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [m.strip() for m in v.split(",") if m.strip()]
        return v

    @field_validator("rate_limit_whitelist", mode="before")
    @classmethod
    def parse_whitelist(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [ip.strip() for ip in v.split(",") if ip.strip()]
        return v

    @model_validator(mode="after")
    def validate_production_requirements(self) -> "Settings":
        if self.environment == "production":
            if self.secret_key == "change_me_to_a_very_long_random_secret_key_at_least_64_chars":
                raise ValueError("SECRET_KEY must be changed from the default in production")
            if self.minio_use_ssl is False:
                import warnings
                warnings.warn(
                    "MINIO_USE_SSL=false in production is a security risk",
                    stacklevel=2,
                )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the cached application settings instance.

    The LRU cache ensures the .env file is only parsed once per process.
    In tests, call get_settings.cache_clear() before patching settings.
    """
    return Settings()
