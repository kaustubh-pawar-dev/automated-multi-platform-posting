"""LinkedIn adapter: publishes a public post to a member profile via ``/rest/posts``.

Docs: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api

* Auth: member access token with scope ``w_member_social`` (self-serve "Share on
  LinkedIn" product), created once with ``scripts/linkedin_authorize.py``.
  Self-serve tokens last about 60 days and cannot be refreshed in the
  background; when the optional expiry date is configured and reached, the
  adapter fails fast with a clear message instead of calling LinkedIn.
* Every call needs ``LinkedIn-Version`` (YYYYMM) and ``X-Restli-Protocol-Version``.
* ``commentary`` uses LinkedIn's "little text" format in which 15 characters are
  markup. They are all backslash-escaped so the post publishes exactly as written.
* LinkedIn has no idempotency mechanism, so ``idempotent_retries`` is False. A 2xx
  response with an unusable header is ambiguous (the post may exist) and is never
  retried.
* ``posted_url`` is built from the post URN LinkedIn returns in ``x-restli-id``.
  ``public_access`` reflects that we requested ``PUBLIC`` visibility; LinkedIn may
  still show a login page to logged-out visitors.
* LinkedIn returns no timestamp, so ``posted_at`` is when we received confirmation.
"""

import re
from datetime import UTC, date, datetime

import httpx
from pydantic import SecretStr

from app.core.exceptions import (
    PlatformAuthError,
    PlatformNotConfiguredError,
    PlatformResponseError,
)
from app.platforms.base import PlatformAdapter, PublishedPost
from app.platforms.http import raise_for_status, send_request

API_BASE = "https://api.linkedin.com"
_ALLOWED_HOSTS = frozenset({"api.linkedin.com"})
_RESERVED_RE = re.compile(r"[\\|{}@\[\]()<>#*_~]")
_POST_URN_RE = re.compile(r"urn:li:(?:share|ugcPost):[0-9]+")


def escape_little_text(text: str) -> str:
    """Backslash-escape every character LinkedIn's little text format reserves."""
    return _RESERVED_RE.sub(lambda match: "\\" + match.group(0), text)


class LinkedInAdapter(PlatformAdapter):
    """Publishes to one LinkedIn member profile."""

    idempotent_retries = False

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        access_token: SecretStr | None,
        person_urn: str | None,
        api_version: str,
        token_expires_at: date | None = None,
    ) -> None:
        self._client = client
        self._access_token = access_token
        self._person_urn = person_urn
        self._api_version = api_version
        self._token_expires_at = token_expires_at

    @property
    def name(self) -> str:
        return "LinkedIn"

    def is_configured(self) -> bool:
        return bool(
            self._person_urn
            and self._access_token is not None
            and self._access_token.get_secret_value()
        )

    def _token_is_expired(self) -> bool:
        """True when an expiry date is configured and today (UTC) has reached it."""
        if self._token_expires_at is None:
            return False
        return datetime.now(UTC).date() >= self._token_expires_at

    async def publish(self, message: str, *, idempotency_key: str) -> PublishedPost:
        if not self.is_configured() or self._access_token is None or self._person_urn is None:
            raise PlatformNotConfiguredError("LinkedIn is not configured")
        if self._token_is_expired():
            raise PlatformAuthError(
                "LinkedIn access token has expired; re-run the authorization script",
                detail="token is past its configured expiry date",
            )

        response = await send_request(
            self._client,
            "POST",
            f"{API_BASE}/rest/posts",
            allowed_hosts=_ALLOWED_HOSTS,
            headers={
                "Authorization": f"Bearer {self._access_token.get_secret_value()}",
                "LinkedIn-Version": self._api_version,
                "X-Restli-Protocol-Version": "2.0.0",
            },
            json_body={
                "author": self._person_urn,
                "commentary": escape_little_text(message),
                "visibility": "PUBLIC",
                "distribution": {
                    "feedDistribution": "MAIN_FEED",
                    "targetEntities": [],
                    "thirdPartyDistributionChannels": [],
                },
                "lifecycleState": "PUBLISHED",
                "isReshareDisabledByAuthor": False,
            },
        )
        if response.status_code == 403:
            raise PlatformAuthError(
                "LinkedIn denied the request; check the token scope and API version",
                detail="HTTP 403",
            )
        raise_for_status(response)

        post_urn = response.headers.get("x-restli-id", "").strip()
        if not _POST_URN_RE.fullmatch(post_urn):
            raise PlatformResponseError(
                ambiguous=True, detail="success response has no valid x-restli-id header"
            )

        return PublishedPost(
            posted_url=f"https://www.linkedin.com/feed/update/{post_urn}/",
            public_access=True,
            posted_at=datetime.now(UTC),
        )