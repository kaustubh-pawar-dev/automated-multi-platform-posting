"""Application configuration loaded from environment variables.

Settings are validated at startup by Pydantic, so a misconfigured environment
fails fast with a clear message instead of failing later during a request.
Platform credentials are ``SecretStr`` so they never appear in logs, reprs, or
API responses. Blank values (``MASTODON_ACCESS_TOKEN=``) mean "not configured".
"""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ORIGIN_HELP = "must be an https URL with a host and no path, e.g. https://mastodon.social"


def _normalize_https_origin(value: str) -> str:
    """Return ``https://host[:port]`` or raise ValueError for anything else."""
    try:
        parts = urlsplit(value.strip())
        port = parts.port
        host = (parts.hostname or "").lower()
    except ValueError:
        raise ValueError(_ORIGIN_HELP) from None
    if (
        parts.scheme != "https"
        or not host
        or ":" in host
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or parts.path not in ("", "/")
    ):
        raise ValueError(_ORIGIN_HELP)
    return f"https://{host}" + (f":{port}" if port else "")


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

    # --- Mastodon ---
    mastodon_instance_url: str | None = None
    mastodon_access_token: SecretStr | None = None

    # --- Bluesky ---
    bluesky_service_url: str = "https://bsky.social"
    bluesky_handle: str | None = None
    bluesky_app_password: SecretStr | None = None

    @field_validator(
        "mastodon_instance_url",
        "mastodon_access_token",
        "bluesky_handle",
        "bluesky_app_password",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        """Treat blank or whitespace-only values as unset and strip the rest."""
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("mastodon_instance_url", mode="after")
    @classmethod
    def _normalize_mastodon_url(cls, value: str | None) -> str | None:
        return None if value is None else _normalize_https_origin(value)

    @field_validator("bluesky_service_url", mode="after")
    @classmethod
    def _normalize_bluesky_url(cls, value: str) -> str:
        return _normalize_https_origin(value)


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings instance."""
    return Settings()