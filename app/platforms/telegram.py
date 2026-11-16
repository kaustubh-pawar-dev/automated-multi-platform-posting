"""Telegram adapter: posts to a channel or group with the Bot API ``sendMessage``.

Docs: https://core.telegram.org/bots/api#sendmessage

* Auth: the bot token goes in the URL path (``/bot<token>/sendMessage``), so it
  must never be logged. Errors here never include URLs or response bodies, and
  the application silences ``httpx`` request logging.
* The bot must be an administrator of the channel with "Post Messages".
* The text is sent as plain text (no ``parse_mode``), so nothing needs escaping.
* Telegram has no idempotency mechanism, so ``idempotent_retries`` is False and
  ambiguous failures are never retried.
* ``posted_url`` exists only when the chat has a public username
  (``https://t.me/<username>/<message_id>``). Posts in private chats are real
  but have no public link: they are reported as successful with no URL and
  ``public_access`` False.
"""

import re
from datetime import UTC, datetime

import httpx
from pydantic import SecretStr

from app.core.exceptions import (
    PlatformAuthError,
    PlatformNotConfiguredError,
    PlatformRateLimitError,
    PlatformResponseError,
)
from app.platforms.base import PlatformAdapter, PublishedPost
from app.platforms.http import (
    parse_json_object,
    parse_retry_after,
    raise_for_status,
    send_request,
)

API_BASE = "https://api.telegram.org"
_ALLOWED_HOSTS = frozenset({"api.telegram.org"})
_USERNAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,31}")
_MAX_UNIX_TIME = 4_102_444_800  # year 2100; guards against absurd values


def _message_time(value: object) -> datetime:
    """Use Telegram's unix ``date`` when valid, otherwise the current UTC time."""
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value < _MAX_UNIX_TIME:
        return datetime.fromtimestamp(value, UTC)
    return datetime.now(UTC)


def _retry_after(response: httpx.Response) -> float | None:
    """Read ``parameters.retry_after`` from the body, falling back to the header."""
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        parameters = data.get("parameters")
        if isinstance(parameters, dict):
            value = parameters.get("retry_after")
            if isinstance(value, int | float) and not isinstance(value, bool):
                return parse_retry_after(str(value))
    return parse_retry_after(response.headers.get("Retry-After"))


class TelegramAdapter(PlatformAdapter):
    """Publishes to one Telegram chat as a bot."""

    idempotent_retries = False

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        bot_token: SecretStr | None,
        chat_id: str | None,
    ) -> None:
        self._client = client
        self._bot_token = bot_token
        self._chat_id = chat_id

    @property
    def name(self) -> str:
        return "Telegram"

    def is_configured(self) -> bool:
        return bool(
            self._chat_id and self._bot_token is not None and self._bot_token.get_secret_value()
        )

    async def publish(self, message: str, *, idempotency_key: str) -> PublishedPost:
        if not self.is_configured() or self._bot_token is None or self._chat_id is None:
            raise PlatformNotConfiguredError("Telegram is not configured")

        response = await send_request(
            self._client,
            "POST",
            f"{API_BASE}/bot{self._bot_token.get_secret_value()}/sendMessage",
            allowed_hosts=_ALLOWED_HOSTS,
            json_body={"chat_id": self._chat_id, "text": message},
        )
        self._raise_for_telegram_status(response)

        data = parse_json_object(response)
        result = data.get("result")
        if data.get("ok") is not True or not isinstance(result, dict):
            raise PlatformResponseError(ambiguous=True, detail="response is not an ok result")
        message_id = result.get("message_id")
        if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0:
            raise PlatformResponseError(ambiguous=True, detail="response has no valid message_id")

        chat = result.get("chat")
        username = chat.get("username") if isinstance(chat, dict) else None
        url = None
        if isinstance(username, str) and _USERNAME_RE.fullmatch(username):
            url = f"https://t.me/{username}/{message_id}"

        return PublishedPost(
            posted_url=url,
            public_access=url is not None,
            posted_at=_message_time(result.get("date")),
        )

    @staticmethod
    def _raise_for_telegram_status(response: httpx.Response) -> None:
        status = response.status_code
        if status == 404:
            raise PlatformAuthError(detail="HTTP 404 (bot token not recognised)")
        if status == 403:
            raise PlatformAuthError(
                "Telegram refused: the bot cannot post to this chat", detail="HTTP 403"
            )
        if status == 429:
            raise PlatformRateLimitError(retry_after=_retry_after(response), detail="HTTP 429")
        raise_for_status(response)