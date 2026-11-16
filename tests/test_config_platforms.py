"""Tests for the Telegram and LinkedIn settings."""

import datetime as dt

import pytest
from pydantic import ValidationError

from app.core.config import Settings

TOKEN = "123456:ABCdefGHIjklMNOpqrsTUVwxyz0123456789"


def test_valid_telegram_settings_accepted() -> None:
    settings = Settings(_env_file=None, telegram_bot_token=TOKEN, telegram_chat_id="@my_channel")
    assert settings.telegram_bot_token is not None
    assert settings.telegram_bot_token.get_secret_value() == TOKEN
    assert settings.telegram_chat_id == "@my_channel"


@pytest.mark.parametrize(
    "token", ["nocolon", "abc:ABCdefGHIjkl", "123456:short", "12:ABCdefGHIjkl"]
)
def test_invalid_telegram_token_rejected(token: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, telegram_bot_token=token)


@pytest.mark.parametrize("chat", ["@my_channel", "-1001234567890", "123456789"])
def test_valid_telegram_chat_ids(chat: str) -> None:
    assert Settings(_env_file=None, telegram_chat_id=chat).telegram_chat_id == chat


@pytest.mark.parametrize(
    "chat", ["my_channel", "@abc", "@bad-name", "-100abc", "https://t.me/channel"]
)
def test_invalid_telegram_chat_ids_rejected(chat: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, telegram_chat_id=chat)


def test_valid_linkedin_urn_accepted() -> None:
    settings = Settings(_env_file=None, linkedin_person_urn="urn:li:person:AbC_123")
    assert settings.linkedin_person_urn == "urn:li:person:AbC_123"


@pytest.mark.parametrize(
    "urn", ["urn:li:organization:123", "urn:li:person:", "person:123", "urn:li:person:a b"]
)
def test_invalid_linkedin_urn_rejected(urn: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, linkedin_person_urn=urn)


@pytest.mark.parametrize("version", ["202608", "202512"])
def test_valid_linkedin_versions(version: str) -> None:
    assert Settings(_env_file=None, linkedin_api_version=version).linkedin_api_version == version


@pytest.mark.parametrize("version", ["202613", "2026-08", "26", "202600"])
def test_invalid_linkedin_versions_rejected(version: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, linkedin_api_version=version)


def test_linkedin_version_defaults_to_a_valid_value() -> None:
    assert Settings(_env_file=None).linkedin_api_version == "202608"


def test_linkedin_expiry_date_is_parsed() -> None:
    settings = Settings(_env_file=None, linkedin_token_expires_at="2027-01-14")
    assert settings.linkedin_token_expires_at == dt.date(2027, 1, 14)


def test_invalid_linkedin_expiry_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, linkedin_token_expires_at="tomorrow")


def test_blank_values_become_none(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "LINKEDIN_ACCESS_TOKEN",
        "LINKEDIN_PERSON_URN",
        "LINKEDIN_TOKEN_EXPIRES_AT",
    ):
        monkeypatch.setenv(name, "  ")
    settings = Settings(_env_file=None)
    assert settings.telegram_bot_token is None
    assert settings.telegram_chat_id is None
    assert settings.linkedin_access_token is None
    assert settings.linkedin_person_urn is None
    assert settings.linkedin_token_expires_at is None


def test_secrets_never_appear_in_repr_or_dumps() -> None:
    settings = Settings(
        _env_file=None, telegram_bot_token=TOKEN, linkedin_access_token="li-secret-xyz"
    )
    rendered = repr(settings) + str(settings.model_dump()) + settings.model_dump_json()
    assert TOKEN not in rendered
    assert "li-secret-xyz" not in rendered


def test_validation_errors_never_echo_the_secret() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, telegram_bot_token="bad-SECRETVALUE-token")
    assert "SECRETVALUE" not in str(exc_info.value)