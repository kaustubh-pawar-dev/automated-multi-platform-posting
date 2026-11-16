"""Bluesky (AT Protocol) adapter.

Docs: https://docs.bsky.app/

Flow: ``com.atproto.server.createSession`` (handle + app password) returns a
short-lived access token; ``com.atproto.repo.createRecord`` then writes an
``app.bsky.feed.post`` record into the account's repository.

* The session is cached and shared, because ``createSession`` is rate limited
  more strictly than other calls. When a request reports an expired token the
  adapter logs in again once and retries. That is safe because an expired token
  means nothing was created.
* ``createRecord`` has no idempotency mechanism, so ``idempotent_retries`` is
  False and ambiguous failures must not be retried.
* The link is added as a rich-text facet (UTF-8 byte offsets) so it is clickable.
* ``posted_url`` is derived from the handle and record key of the ``at://`` URI
  the server returned. AT Protocol records are public, so ``public_access`` is
  True; an account may still hide itself from logged-out viewers on bsky.app,
  which cannot be detected.
* The server returns no timestamp, so ``posted_at`` is the ``createdAt`` value
  written into the record.
"""

import asyncio
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from app.core.exceptions import (
    PlatformAuthError,
    PlatformNotConfiguredError,
    PlatformResponseError,
)
from app.platforms.base import PlatformAdapter, PublishedPost
from app.platforms.http import (
    parse_json_object,
    raise_for_status,
    send_request,
)
from app.utils.url_validator import find_url_candidates

COLLECTION = "app.bsky.feed.post"
_RKEY_RE = re.compile(r"[A-Za-z0-9._~:-]{1,512}")
_HANDLE_RE = re.compile(r"[A-Za-z0-9.-]{1,253}")
_EXPIRED_TOKEN_ERRORS = frozenset({"ExpiredToken", "InvalidToken"})


@dataclass(frozen=True, slots=True)
class _Session:
    """An authenticated session. The token is excluded from ``repr``."""

    access_jwt: str = field(repr=False)
    did: str
    handle: str


class _SessionExpiredError(Exception):
    """Internal signal: the access token was rejected as expired or invalid."""


def _format_timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _link_facets(message: str) -> list[dict[str, Any]]:
    """Build a link facet for the message's single URL, using UTF-8 byte offsets."""
    candidates = find_url_candidates(message)
    if len(candidates) != 1:
        return []
    url = candidates[0]
    start_char = message.find(url)
    if start_char < 0:
        return []
    start = len(message[:start_char].encode("utf-8"))
    end = start + len(url.encode("utf-8"))
    return [
        {
            "index": {"byteStart": start, "byteEnd": end},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
        }
    ]


def _build_post_url(uri: str, handle: str) -> str | None:
    """Turn ``at://<did>/app.bsky.feed.post/<rkey>`` into a bsky.app link, if well-formed."""
    parts = uri.removeprefix("at://").split("/")
    if len(parts) != 3 or parts[1] != COLLECTION:
        return None
    rkey = parts[2]
    if not _RKEY_RE.fullmatch(rkey) or not _HANDLE_RE.fullmatch(handle):
        return None
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


def _is_expired_token(response: httpx.Response) -> bool:
    if response.status_code not in (400, 401):
        return False
    try:
        data = response.json()
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("error") in _EXPIRED_TOKEN_ERRORS


class BlueskyAdapter(PlatformAdapter):
    """Publishes to one Bluesky account."""

    idempotent_retries = False

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        service_url: str,
        handle: str | None,
        app_password: SecretStr | None,
    ) -> None:
        self._client = client
        self._service_url = service_url
        self._handle = handle
        self._app_password = app_password
        host = urlsplit(service_url).hostname
        self._allowed_hosts = frozenset({host.lower()}) if host else frozenset()
        self._session: _Session | None = None
        self._session_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return "Bluesky"

    def is_configured(self) -> bool:
        return bool(
            self._handle
            and self._app_password is not None
            and self._app_password.get_secret_value()
        )

    async def publish(self, message: str, *, idempotency_key: str) -> PublishedPost:
        if not self.is_configured():
            raise PlatformNotConfiguredError("Bluesky is not configured")

        stale: _Session | None = None
        for _ in range(2):
            session = await self._get_session(replacing=stale)
            try:
                return await self._create_post(session, message)
            except _SessionExpiredError:
                stale = session
        raise PlatformAuthError(detail="session rejected after logging in again")

    async def _get_session(self, *, replacing: _Session | None = None) -> _Session:
        async with self._session_lock:
            if self._session is None or self._session is replacing:
                self._session = await self._create_session()
            return self._session

    async def _create_session(self) -> _Session:
        if self._handle is None or self._app_password is None:
            raise PlatformNotConfiguredError("Bluesky is not configured")
        response = await send_request(
            self._client,
            "POST",
            f"{self._service_url}/xrpc/com.atproto.server.createSession",
            allowed_hosts=self._allowed_hosts,
            json_body={
                "identifier": self._handle,
                "password": self._app_password.get_secret_value(),
            },
        )
        raise_for_status(response)
        data = parse_json_object(response)
        access_jwt, did, handle = data.get("accessJwt"), data.get("did"), data.get("handle")
        if not isinstance(access_jwt, str) or not access_jwt or not isinstance(did, str) or not did:
            raise PlatformResponseError(detail="session response is missing required fields")
        return _Session(
            access_jwt=access_jwt,
            did=did,
            handle=handle if isinstance(handle, str) and handle else self._handle,
        )

    async def _create_post(self, session: _Session, message: str) -> PublishedPost:
        created_at = datetime.now(UTC)
        record: dict[str, Any] = {
            "$type": COLLECTION,
            "text": message,
            "createdAt": _format_timestamp(created_at),
        }
        facets = _link_facets(message)
        if facets:
            record["facets"] = facets

        response = await send_request(
            self._client,
            "POST",
            f"{self._service_url}/xrpc/com.atproto.repo.createRecord",
            allowed_hosts=self._allowed_hosts,
            headers={"Authorization": f"Bearer {session.access_jwt}"},
            json_body={"repo": session.did, "collection": COLLECTION, "record": record},
        )
        if _is_expired_token(response):
            raise _SessionExpiredError
        raise_for_status(response)
        data = parse_json_object(response)

        uri = data.get("uri")
        if not isinstance(uri, str) or not uri.startswith("at://"):
            raise PlatformResponseError(detail="response has no valid record uri")

        return PublishedPost(
            posted_url=_build_post_url(uri, session.handle),
            public_access=True,
            posted_at=created_at,
        )