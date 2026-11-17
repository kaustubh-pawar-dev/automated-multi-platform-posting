"""Tests for the Discord settings."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz0123456789"
GUILD = "111111111111111111"


@pytest.mark.parametrize(
    "url",
    [
        WEBHOOK,
        WEBHOOK + "/",
        WEBHOOK.replace("discord.com/api", "discordapp.com/api"),
        WEBHOOK.replace("/api/", "/api/v10/"),
    ],
    ids=["plain", "trailing-slash", "legacy-host", "versioned"],
)
def test_valid_webhook_urls_accepted(url: str) -> None:
    settings = Settings(_env_file=None, discord_webhook_url=url)
    assert settings.discord_webhook_url is not None
    assert settings.discord_webhook_url.get_secret_value() == url


@pytest.mark.parametrize(
    "url",
    [
        WEBHOOK.replace("https://", "http://"),
        WEBHOOK.replace("discord.com", "evil.example"),
        WEBHOOK.replace("discord.com", "discord.com.evil.example"),
        "https://discord.com/api/webhooks/123456789012345678",
        "https://discord.com/api/webhooks/123/abc",
        WEBHOOK + "?thread_id=1",
        "https://discord.com/channels/1/2/3",
        "not a url",
    ],
    ids=[
        "http",
        "other-host",
        "lookalike-host",
        "no-token",
        "short-ids",
        "query-string",
        "wrong-path",
        "garbage",
    ],
)
def test_invalid_webhook_urls_rejected(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, discord_webhook_url=url)


def test_guild_id_accepted() -> None:
    assert Settings(_env_file=None, discord_guild_id=GUILD).discord_guild_id == GUILD


@pytest.mark.parametrize("guild", ["abc", "123", "1" * 30])
def test_invalid_guild_id_rejected(guild: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, discord_guild_id=guild)


def test_blank_values_become_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "  ")
    monkeypatch.setenv("DISCORD_GUILD_ID", "")
    settings = Settings(_env_file=None)
    assert settings.discord_webhook_url is None
    assert settings.discord_guild_id is None


def test_webhook_url_never_appears_in_repr_or_dumps() -> None:
    settings = Settings(_env_file=None, discord_webhook_url=WEBHOOK)
    rendered = repr(settings) + str(settings.model_dump()) + settings.model_dump_json()
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in rendered


def test_validation_errors_never_echo_the_webhook_token() -> None:
    bad = "https://discord.com/api/webhooks/1/SECRETTOKENVALUE0123456789"
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, discord_webhook_url=bad)
    assert "SECRETTOKENVALUE" not in str(exc_info.value)