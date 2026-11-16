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