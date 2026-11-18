"""Application configuration loaded from environment variables.

Settings are validated at startup by Pydantic, so a misconfigured environment
fails fast with a clear message instead of failing later during a request.
Platform credentials are ``SecretStr`` so they never appear in logs, reprs, or
API responses, and validation errors never echo the offending input
(``hide_input_in_errors``). Blank values (``MASTODON_ACCESS_TOKEN=``) mean
"not configured".
"""

import re
from datetime import date
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_ORIGIN_HELP = "must be an https URL with a host and no path, e.g. https://mastodon.social"
_TELEGRAM_TOKEN_RE = re.compile(r"\d{3,}:[A-Za-z0-9_-]{10,}")
_TELEGRAM_CHAT_RE = re.compile(r"@[A-Za-z][A-Za-z0-9_]{4,31}|-?\d{5,20}")
_LINKEDIN_URN_RE = re.compile(r"urn:li:person:[A-Za-z0-9_-]{1,64}")
_LINKEDIN_VERSION_RE = re.compile(r"20\d{2}(?:0[1-9]|1[0-2])")
_DISCORD_WEBHOOK_RE = re.compile(
    r"https://(?:discord|discordapp)\.com/api/(?:v\d{1,2}/)?webhooks/"
    r"\d{17,20}/[A-Za-z0-9_-]{20,120}/?"
)
_DISCORD_GUILD_RE = re.compile(r"\d{17,20}")


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
        hide_input_in_errors=True,
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

    # --- Telegram ---
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    # --- LinkedIn ---
    linkedin_access_token: SecretStr | None = None
    linkedin_person_urn: str | None = None
    linkedin_api_version: str = "202608"
    linkedin_token_expires_at: date | None = None

    # --- Discord ---
    discord_webhook_url: SecretStr | None = None
    discord_guild_id: str | None = None

    # --- Platform switches ---
    # Comma-separated names of platforms to switch off without deleting credentials.
    disabled_platforms: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator(
        "mastodon_instance_url",
        "mastodon_access_token",
        "bluesky_handle",
        "bluesky_app_password",
        "telegram_bot_token",
        "telegram_chat_id",
        "linkedin_access_token",
        "linkedin_person_urn",
        "linkedin_token_expires_at",
        "discord_webhook_url",
        "discord_guild_id",
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

    @field_validator("telegram_bot_token", mode="after")
    @classmethod
    def _check_telegram_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not _TELEGRAM_TOKEN_RE.fullmatch(value.get_secret_value()):
            raise ValueError("TELEGRAM_BOT_TOKEN has an invalid format (expected 123456:ABC...)")
        return value

    @field_validator("telegram_chat_id", mode="after")
    @classmethod
    def _check_telegram_chat(cls, value: str | None) -> str | None:
        if value is not None and not _TELEGRAM_CHAT_RE.fullmatch(value):
            raise ValueError("TELEGRAM_CHAT_ID must be @channelusername or a numeric chat id")
        return value

    @field_validator("linkedin_person_urn", mode="after")
    @classmethod
    def _check_linkedin_urn(cls, value: str | None) -> str | None:
        if value is not None and not _LINKEDIN_URN_RE.fullmatch(value):
            raise ValueError("LINKEDIN_PERSON_URN must look like urn:li:person:<id>")
        return value

    @field_validator("linkedin_api_version", mode="after")
    @classmethod
    def _check_linkedin_version(cls, value: str) -> str:
        if not _LINKEDIN_VERSION_RE.fullmatch(value):
            raise ValueError("LINKEDIN_API_VERSION must be YYYYMM, e.g. 202608")
        return value

    @field_validator("discord_webhook_url", mode="after")
    @classmethod
    def _check_discord_webhook(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not _DISCORD_WEBHOOK_RE.fullmatch(value.get_secret_value()):
            raise ValueError(
                "DISCORD_WEBHOOK_URL must look like https://discord.com/api/webhooks/<id>/<token>"
            )
        return value

    @field_validator("discord_guild_id", mode="after")
    @classmethod
    def _check_discord_guild(cls, value: str | None) -> str | None:
        if value is not None and not _DISCORD_GUILD_RE.fullmatch(value):
            raise ValueError("DISCORD_GUILD_ID must be the numeric server id")
        return value

    @field_validator("disabled_platforms", mode="before")
    @classmethod
    def _split_disabled_platforms(cls, value: object) -> object:
        """Accept 'LinkedIn, Telegram' from the environment as well as a list."""
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings instance."""
    return Settings()