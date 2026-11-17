"""Tests for structured logging, request-id formatting and secret redaction."""

import json
import logging
import sys
from collections.abc import Iterator

import pytest

from app.core.config import Settings
from app.core.logging import (
    REDACTED,
    JsonFormatter,
    TextFormatter,
    collect_secrets,
    configure_logging,
    redact,
    request_id_var,
    set_known_secrets,
)

TOKEN = "123456:ABCdefGHIjklMNOpqrsTUVwxyz0123456789"
WEBHOOK_TOKEN = "abcdefghijklmnopqrstuvwxyz0123456789"
WEBHOOK = f"https://discord.com/api/webhooks/123456789012345678/{WEBHOOK_TOKEN}"


@pytest.fixture(autouse=True)
def _clean_known_secrets() -> Iterator[None]:
    set_known_secrets(())
    yield
    set_known_secrets(())


def make_record(message: str = "hello", exc_info: object = None) -> logging.LogRecord:
    return logging.LogRecord(
        "app.test", logging.INFO, __file__, 10, message, (), exc_info  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Authorization: Bearer abc.def-123_xyz", "abc.def-123_xyz"),
        ("POST https://api.telegram.org/bot123456:ABCdefGHIjklMNO/sendMessage", "ABCdefGHIjklMNO"),
        (f"POST {WEBHOOK}?wait=true", WEBHOOK_TOKEN),
        ("login failed password=hunter2hunter2 for user", "hunter2hunter2"),
        ("api_key: ABCDEFG1234567", "ABCDEFG1234567"),
        ("token=abc123XYZ789", "abc123XYZ789"),
    ],
    ids=["bearer", "telegram-url", "discord-url", "password", "api-key", "token"],
)
def test_patterns_redact_credentials(text: str, secret: str) -> None:
    result = redact(text)
    assert secret not in result
    assert REDACTED in result


@pytest.mark.parametrize(
    "text",
    [
        "platform_failed platform=Bravo error='Authentication failed' attempts=1",
        "LinkedIn access token has expired; re-run the authorization script",
        "request_completed method=POST path='/posting' status=200 duration_ms=12",
    ],
)
def test_ordinary_log_text_is_left_alone(text: str) -> None:
    assert redact(text) == text


def test_known_secret_values_are_redacted() -> None:
    set_known_secrets(["super-secret-value-1"])
    result = redact("value=super-secret-value-1 end")
    assert "super-secret-value-1" not in result
    assert REDACTED in result


def test_short_secrets_are_ignored_to_avoid_mangling_logs() -> None:
    set_known_secrets(["abc"])
    assert redact("abc def") == "abc def"


def test_longer_secrets_are_replaced_first() -> None:
    set_known_secrets(["abcdefgh", "abcdefghijkl"])
    assert redact("x abcdefghijkl y") == f"x {REDACTED} y"


def test_text_formatter_includes_request_id() -> None:
    token = request_id_var.set("req-12345678")
    try:
        line = TextFormatter().format(make_record("hello world"))
    finally:
        request_id_var.reset(token)
    assert "[req-12345678]" in line
    assert "INFO" in line
    assert line.endswith("app.test: hello world")


def test_text_formatter_uses_dash_outside_requests() -> None:
    assert "[-]" in TextFormatter().format(make_record())


def test_json_formatter_fields() -> None:
    token = request_id_var.set("req-12345678")
    try:
        data = json.loads(JsonFormatter().format(make_record("hello")))
    finally:
        request_id_var.reset(token)
    assert set(data) == {"timestamp", "level", "logger", "request_id", "message"}
    assert data["level"] == "INFO"
    assert data["logger"] == "app.test"
    assert data["request_id"] == "req-12345678"
    assert data["message"] == "hello"
    assert data["timestamp"].endswith("Z")


def test_json_formatter_redacts_messages() -> None:
    set_known_secrets(["super-secret-token-1"])
    data = json.loads(JsonFormatter().format(make_record("leaked super-secret-token-1")))
    assert "super-secret-token-1" not in data["message"]


def test_tracebacks_are_redacted() -> None:
    set_known_secrets(["super-secret-token-1"])
    try:
        raise RuntimeError("boom super-secret-token-1")
    except RuntimeError:
        exc_info = sys.exc_info()
    for formatter in (TextFormatter(), JsonFormatter()):
        output = formatter.format(make_record("failed", exc_info))
        assert "RuntimeError" in output
        assert "super-secret-token-1" not in output


def test_configure_logging_writes_text_lines(log_stream: object) -> None:
    configure_logging("INFO", "text", stream=log_stream)  # type: ignore[arg-type]
    logging.getLogger("app.demo").info("hello %s", "world")
    line = log_stream.getvalue().strip()  # type: ignore[attr-defined]
    assert line.endswith("app.demo: hello world")
    assert " INFO " in line


def test_debug_is_hidden_at_info_level(log_stream: object) -> None:
    configure_logging("INFO", "text", stream=log_stream)  # type: ignore[arg-type]
    logging.getLogger("app.demo").debug("hidden")
    assert log_stream.getvalue() == ""  # type: ignore[attr-defined]


def test_debug_is_shown_at_debug_level(log_stream: object) -> None:
    configure_logging("DEBUG", "text", stream=log_stream)  # type: ignore[arg-type]
    logging.getLogger("app.demo").debug("visible")
    assert "visible" in log_stream.getvalue()  # type: ignore[attr-defined]


def test_configure_logging_is_idempotent(log_stream: object) -> None:
    configure_logging("INFO", "text", stream=log_stream)  # type: ignore[arg-type]
    configure_logging("INFO", "text", stream=log_stream)  # type: ignore[arg-type]
    logging.getLogger("app.demo").info("once")
    assert log_stream.getvalue().count("once") == 1  # type: ignore[attr-defined]


def test_json_output_is_valid_json(log_stream: object) -> None:
    configure_logging("INFO", "json", stream=log_stream)  # type: ignore[arg-type]
    logging.getLogger("app.demo").info("hello %s", "world")
    data = json.loads(log_stream.getvalue().strip())  # type: ignore[attr-defined]
    assert data["message"] == "hello world"


def test_http_client_loggers_stay_quiet_even_at_debug(log_stream: object) -> None:
    configure_logging("DEBUG", "text", stream=log_stream)  # type: ignore[arg-type]
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_configure_logging_registers_secrets_for_redaction(log_stream: object) -> None:
    configure_logging(
        "INFO", "text", secrets=["my-secret-value-123"], stream=log_stream  # type: ignore[arg-type]
    )
    logging.getLogger("app.demo").info("value my-secret-value-123")
    assert "my-secret-value-123" not in log_stream.getvalue()  # type: ignore[attr-defined]


def test_collect_secrets_returns_only_secret_values() -> None:
    settings = Settings(
        _env_file=None,
        mastodon_access_token="mastodon-token-value",
        telegram_bot_token=TOKEN,
        discord_webhook_url=WEBHOOK,
        bluesky_handle="user.bsky.test",
    )
    secrets = collect_secrets(settings)
    assert "mastodon-token-value" in secrets
    assert TOKEN in secrets
    assert WEBHOOK in secrets
    assert WEBHOOK_TOKEN in secrets
    assert "user.bsky.test" not in secrets