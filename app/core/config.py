"""Application configuration loaded from environment variables.

Settings are validated at startup by Pydantic, so a misconfigured environment
fails fast with a clear message instead of failing later during a request.
Platform credentials are added in later steps; they will be stored as
``SecretStr`` so they never appear in logs, reprs, or API responses.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed, validated application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---
    app_name: str = "Automated Multi-Platform Posting System"
    app_version: str = "0.1.0"
    environment: Literal["development", "testing", "production"] = "development"
    host: str = "127.0.0.1"
    port: int = Field(default=4000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # --- Outbound HTTP behaviour ---
    http_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    http_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    max_concurrent_platforms: int = Field(default=5, ge=1, le=20)
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_base_seconds: float = Field(default=0.5, gt=0, le=10)


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings instance."""
    return Settings()