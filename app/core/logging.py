"""Structured logging with request correlation ids and secret redaction.

* Every record carries the current request id (``-`` outside a request).
* ``TextFormatter`` is readable during development; ``JsonFormatter`` is for
  production log pipelines (``ENVIRONMENT=production``).
* Redaction is a safety net, not the primary defence: application code already
  keeps credentials out of messages. Every formatted line is scrubbed of the
  exact secret values from the settings and of common credential shapes
  (bearer tokens, Telegram bot URLs, Discord webhook URLs, ``password=...``).
  Tracebacks are scrubbed too.
"""

import contextvars
import json
import logging
import re
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import IO, Literal

from pydantic import SecretStr

from app.core.config import Settings

REDACTED = "[REDACTED]"
REQUEST_ID_HEADER = "X-Request-ID"
MIN_SECRET_LENGTH = 8
_HANDLER_FLAG = "_multi_platform_posting_handler"

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), f"Bearer {REDACTED}"),
    (re.compile(r"/bot\d+:[A-Za-z0-9_-]+"), f"/bot{REDACTED}"),
    (re.compile(r"/webhooks/\d+/[A-Za-z0-9_-]+"), f"/webhooks/{REDACTED}"),
    (
        re.compile(
            r"(?i)\b(token|password|passwd|secret|api[_-]?key|authorization)"
            r"(\s*[=:]\s*)[^\s,;&\"']+"
        ),
        rf"\1\2{REDACTED}",
    ),
)

_known_secrets: tuple[str, ...] = ()


def current_request_id() -> str | None:
    """Return the id of the request being handled, if any."""
    return request_id_var.get()


def set_known_secrets(values: Iterable[str]) -> None:
    """Register exact secret values to scrub from all log output (longest first)."""
    global _known_secrets
    unique = {value for value in values if len(value) >= MIN_SECRET_LENGTH}
    _known_secrets = tuple(sorted(unique, key=len, reverse=True))


def redact(text: str) -> str:
    """Remove known secret values and common credential shapes from ``text``."""
    for secret in _known_secrets:
        text = text.replace(secret, REDACTED)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def collect_secrets(settings: Settings) -> list[str]:
    """Return every secret value in ``settings`` (plus the Discord token segment)."""
    values: list[str] = []
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        if not isinstance(value, SecretStr):
            continue
        secret = value.get_secret_value()
        if not secret:
            continue
        values.append(secret)
        if name == "discord_webhook_url":
            values.append(secret.rstrip("/").rsplit("/", 1)[-1])
    return values


def _timestamp(record: logging.LogRecord) -> str:
    moment = datetime.fromtimestamp(record.created, UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class _SafeFormatter(logging.Formatter):
    """Base class: formats the message and traceback with redaction applied."""

    def _message(self, record: logging.LogRecord) -> str:
        return redact(record.getMessage())

    def _exception(self, record: logging.LogRecord) -> str | None:
        if not record.exc_info:
            return None
        return redact(self.formatException(record.exc_info))


class TextFormatter(_SafeFormatter):
    """One readable line per record: time, level, request id, logger, message."""

    def format(self, record: logging.LogRecord) -> str:
        request_id = request_id_var.get() or "-"
        line = (
            f"{_timestamp(record)} {record.levelname:<8} [{request_id}] "
            f"{record.name}: {self._message(record)}"
        )
        exception = self._exception(record)
        return f"{line}\n{exception}" if exception else line


class JsonFormatter(_SafeFormatter):
    """One JSON object per record, for log aggregation in production."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, str | None] = {
            "timestamp": _timestamp(record),
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_var.get(),
            "message": self._message(record),
        }
        exception = self._exception(record)
        if exception:
            payload["exception"] = exception
        return json.dumps(payload, ensure_ascii=False)


def remove_app_handlers() -> None:
    """Remove the handlers installed by ``configure_logging`` (not other handlers)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_FLAG, False):
            root.removeHandler(handler)


def configure_logging(
    level: str,
    log_format: Literal["text", "json"] = "text",
    *,
    secrets: Iterable[str] = (),
    stream: IO[str] | None = None,
) -> None:
    """Install the application's log handler on the root logger (idempotent)."""
    set_known_secrets(secrets)
    remove_app_handlers()

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter() if log_format == "json" else TextFormatter())
    setattr(handler, _HANDLER_FLAG, True)

    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(level)

    # httpx logs full request URLs at INFO, and Telegram/Discord put credentials in
    # the URL path. Force these loggers quiet whatever the application level is.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)

    # Route uvicorn's own messages through our formatter. Its access log is replaced
    # by the request logging in the middleware, which also carries the request id.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True