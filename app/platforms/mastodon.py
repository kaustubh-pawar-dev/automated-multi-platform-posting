"""Mastodon adapter: publishes a public status with ``POST /api/v1/statuses``.

Docs: https://docs.joinmastodon.org/methods/statuses/

* Auth: bearer access token created under Preferences > Development with only
  the ``write:statuses`` scope.
* Idempotency: the ``Idempotency-Key`` header makes retries safe (keys are kept
  for up to one hour), so ``idempotent_retries`` is True.
* The status is requested with ``visibility: public`` and ``public_access`` is
  taken from the visibility the server reports back.
* ``posted_url`` is the ``url`` the instance returned; it is never constructed.
"""

from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from app.core.exceptions import PlatformNotConfiguredError, PlatformResponseError
from app.platforms.base import PlatformAdapter, PublishedPost
from app.platforms.http import (
    parse_json_object,
    parse_platform_timestamp,
    raise_for_status,
    send_request,
)


class MastodonAdapter(PlatformAdapter):
    """Publishes to one Mastodon instance."""

    idempotent_retries = True

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        instance_url: str | None,
        access_token: SecretStr | None,
    ) -> None:
        self._client = client
        self._instance_url = instance_url
        self._access_token = access_token
        host = urlsplit(instance_url).hostname if instance_url else None
        self._allowed_hosts = frozenset({host.lower()}) if host else frozenset()

    @property
    def name(self) -> str:
        return "Mastodon"

    def is_configured(self) -> bool:
        return bool(
            self._instance_url
            and self._access_token is not None
            and self._access_token.get_secret_value()
        )

    async def publish(self, message: str, *, idempotency_key: str) -> PublishedPost:
        if not self.is_configured() or self._instance_url is None or self._access_token is None:
            raise PlatformNotConfiguredError("Mastodon is not configured")

        response = await send_request(
            self._client,
            "POST",
            f"{self._instance_url}/api/v1/statuses",
            allowed_hosts=self._allowed_hosts,
            headers={
                "Authorization": f"Bearer {self._access_token.get_secret_value()}",
                "Idempotency-Key": idempotency_key,
            },
            json_body={"status": message, "visibility": "public"},
        )
        raise_for_status(response)
        data = parse_json_object(response)

        url = data.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise PlatformResponseError(detail="response has no valid status url")

        return PublishedPost(
            posted_url=url,
            public_access=data.get("visibility") == "public",
            posted_at=parse_platform_timestamp(data.get("created_at")) or datetime.now(UTC),
        )