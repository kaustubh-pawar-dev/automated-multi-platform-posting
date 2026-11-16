"""Tests for application settings loading and validation."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


def test_defaults_match_assignment_port() -> None:
    settings = Settings(_env_file=None)
    assert settings.port == 4000
    assert settings.environment == "development"


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "4100")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    settings = Settings(_env_file=None)
    assert settings.port == 4100
    assert settings.log_level == "DEBUG"


def test_invalid_port_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "70000")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_invalid_log_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    assert get_settings() is get_settings()


def test_blank_platform_values_become_none(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MASTODON_INSTANCE_URL",
        "MASTODON_ACCESS_TOKEN",
        "BLUESKY_HANDLE",
        "BLUESKY_APP_PASSWORD",
    ):
        monkeypatch.setenv(name, "   ")
    settings = Settings(_env_file=None)
    assert settings.mastodon_instance_url is None
    assert settings.mastodon_access_token is None
    assert settings.bluesky_handle is None
    assert settings.bluesky_app_password is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://mastodon.social", "https://mastodon.social"),
        ("https://Mastodon.Social/", "https://mastodon.social"),
        ("  https://m.example:8443/ ", "https://m.example:8443"),
        ("HTTPS://M.EXAMPLE", "https://m.example"),
    ],
)
def test_instance_url_is_normalised(raw: str, expected: str) -> None:
    assert Settings(_env_file=None, mastodon_instance_url=raw).mastodon_instance_url == expected


@pytest.mark.parametrize(
    "raw",
    [
        "http://mastodon.social",
        "mastodon.social",
        "https://mastodon.social/@user",
        "https://user:pw@mastodon.social",
        "https://mastodon.social?x=1",
        "https://mastodon.social#frag",
        "https://",
        "https://[::1]",
        "https://mastodon.social:99999",
    ],
)
def test_invalid_instance_url_is_rejected(raw: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, mastodon_instance_url=raw)


def test_bluesky_service_url_defaults_to_bsky_social() -> None:
    assert Settings(_env_file=None).bluesky_service_url == "https://bsky.social"


def test_bluesky_service_url_must_be_https() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, bluesky_service_url="http://bsky.social")


def test_secrets_never_appear_in_repr_or_dumps() -> None:
    settings = Settings(
        _env_file=None,
        mastodon_access_token="super-secret-token",
        bluesky_app_password="super-secret-password",
    )
    rendered = repr(settings) + str(settings.model_dump()) + settings.model_dump_json()
    assert "super-secret-token" not in rendered
    assert "super-secret-password" not in rendered
    assert settings.mastodon_access_token is not None
    assert settings.mastodon_access_token.get_secret_value() == "super-secret-token"


def test_secret_values_are_stripped() -> None:
    settings = Settings(_env_file=None, mastodon_access_token="  abc \n")
    assert settings.mastodon_access_token is not None
    assert settings.mastodon_access_token.get_secret_value() == "abc"