"""Discord adapter: posts to a channel through an incoming webhook.

Docs: https://discord.com/developers/docs/resources/webhook#execute-webhook

* Auth: the webhook URL itself is the credential (its last segment is a secret
  token), so it is stored as a ``SecretStr`` and never logged. Errors here never
  include URLs or response bodies, and the application silences ``httpx``
  request logging.
* ``?wait=true`` makes Discord return the created message, which is how a
  publication is confirmed; a response without a usable message is treated as
  ambiguous and is never retried.
* ``allowed_mentions: {"parse": []}`` guarantees the text can never ping
  ``@everyone``, roles or users.
* Discord has no idempotency mechanism, so ``idempotent_retries`` is False.
* Webhook posts land in a server channel that is not publicly accessible, so
  ``public_access`` is always False.
* ``posted_url`` (a jump link) needs the server id, which this endpoint does not
  return. It is built only when ``DISCORD_GUILD_ID`` is configured; otherwise the
  post is reported as successful with no URL. The link only works for members of
  that server.
* Requests are restricted to Discord's own hosts regardless of what the
  configured URL says.
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
    parse_platform_timestamp,
    parse_retry_after,
    raise_for_status,
    send_request,
)

_ALLOWED_HOSTS = frozenset({"discord.com", "discordapp.com"})
_SNOWFLAKE_RE = re.compile(r"\d{1,25}")


def _retry_after(response: httpx.Response) -> float | None:
    """Read ``retry_after`` (seconds) from the JSON body, falling back to the header."""
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        value = data.get("retry_after")
        if isinstance(value, int | float) and not isinstance(value, bool):
            return parse_retry_after(str(value))
    return parse_retry_after(response.headers.get("Retry-After"))


def _snowflake(value: object) -> str | None:
    """Return ``value`` if it is a numeric Discord id string, else None."""
    if isinstance(value, str) and _SNOWFLAKE_RE.fullmatch(value):
        return value
    return None


class DiscordAdapter(PlatformAdapter):
    """Publishes to one Discord channel through a webhook."""

    idempotent_retries = False

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        webhook_url: SecretStr | None,
        guild_id: str | None = None,
    ) -> None:
        self._client = client
        self._webhook_url = webhook_url
        self._guild_id = guild_id

    @property
    def name(self) -> str:
        return "Discord"

    def is_configured(self) -> bool:
        return self._webhook_url is not None and bool(self._webhook_url.get_secret_value())

    async def publish(self, message: str, *, idempotency_key: str) -> PublishedPost:
        if not self.is_configured() or self._webhook_url is None:
            raise PlatformNotConfiguredError("Discord is not configured")

        base_url = self._webhook_url.get_secret_value().rstrip("/")
        response = await send_request(
            self._client,
            "POST",
            f"{base_url}?wait=true",
            allowed_hosts=_ALLOWED_HOSTS,
            json_body={"content": message, "allowed_mentions": {"parse": []}},
        )
        if response.status_code == 404:
            raise PlatformAuthError(
                "Discord webhook not found; it may have been deleted", detail="HTTP 404"
            )
        if response.status_code == 429:
            raise PlatformRateLimitError(retry_after=_retry_after(response), detail="HTTP 429")
        raise_for_status(response)

        data = parse_json_object(response)
        message_id = _snowflake(data.get("id"))
        channel_id = _snowflake(data.get("channel_id"))
        if message_id is None or channel_id is None:
            raise PlatformResponseError(
                ambiguous=True, detail="response has no valid message id and channel id"
            )

        url = None
        if self._guild_id:
            url = f"https://discord.com/channels/{self._guild_id}/{channel_id}/{message_id}"

        return PublishedPost(
            posted_url=url,
            public_access=False,
            posted_at=parse_platform_timestamp(data.get("timestamp")) or datetime.now(UTC),
        )