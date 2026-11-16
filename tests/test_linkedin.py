"""Tests for the LinkedIn adapter using mocked HTTP responses (nothing is published)."""

import datetime as dt
import json

import httpx
import pytest
from pydantic import SecretStr
from respx import MockRouter

from app.core.exceptions import (
    PlatformAuthError,
    PlatformError,
    PlatformNotConfiguredError,
    PlatformRateLimitError,
    PlatformRejectedError,
    PlatformResponseError,
    PlatformTimeoutError,
    PlatformUnavailableError,
)
from app.platforms.linkedin import LinkedInAdapter, escape_little_text

TOKEN = "li-token-abc"
URN = "urn:li:person:AbC_123"
POSTS_URL = "https://api.linkedin.com/rest/posts"
POST_URN = "urn:li:share:7300000000000000000"
MESSAGE = "Grow your visibility (now!) https://example.com/a_b?x=1#top"
KEY = "key-abc"


def make_adapter(
    client: httpx.AsyncClient,
    *,
    token: str | None = TOKEN,
    urn: str | None = URN,
    version: str = "202608",
    expires: dt.date | None = None,
) -> LinkedInAdapter:
    return LinkedInAdapter(
        client,
        access_token=SecretStr(token) if token else None,
        person_urn=urn,
        api_version=version,
        token_expires_at=expires,
    )


def created(urn: str = POST_URN) -> httpx.Response:
    return httpx.Response(201, headers={"x-restli-id": urn})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Q3 results (up 40%) #growth", r"Q3 results \(up 40%\) \#growth"),
        ("a_b*c~d", r"a\_b\*c\~d"),
        ("back\\slash", "back\\\\slash"),
        ("plain text", "plain text"),
        ("https://example.com/a_b?x=1#top", r"https://example.com/a\_b?x=1\#top"),
        ("@name|{x}[y]<z>", r"\@name\|\{x\}\[y\]\<z\>"),
    ],
)
def test_escape_little_text(text: str, expected: str) -> None:
    assert escape_little_text(text) == expected


def test_adapter_identity(http_client: httpx.AsyncClient) -> None:
    adapter = make_adapter(http_client)
    assert adapter.name == "LinkedIn"
    assert adapter.idempotent_retries is False


def test_configured_with_token_and_urn(http_client: httpx.AsyncClient) -> None:
    assert make_adapter(http_client).is_configured() is True


@pytest.mark.parametrize(
    ("token", "urn"), [(None, URN), (TOKEN, None)], ids=["no-token", "no-urn"]
)
def test_not_configured_when_missing(
    http_client: httpx.AsyncClient, token: str | None, urn: str | None
) -> None:
    assert make_adapter(http_client, token=token, urn=urn).is_configured() is False


async def test_unconfigured_publish_makes_no_request(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    with pytest.raises(PlatformNotConfiguredError):
        await make_adapter(http_client, token=None).publish(MESSAGE, idempotency_key=KEY)
    assert len(respx_mock.calls) == 0


async def test_successful_publish(http_client: httpx.AsyncClient, respx_mock: MockRouter) -> None:
    route = respx_mock.post(POSTS_URL).mock(return_value=created())
    post = await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)

    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["LinkedIn-Version"] == "202608"
    assert request.headers["X-Restli-Protocol-Version"] == "2.0.0"
    body = json.loads(request.content)
    assert body["author"] == URN
    assert body["commentary"] == escape_little_text(MESSAGE)
    assert body["visibility"] == "PUBLIC"
    assert body["lifecycleState"] == "PUBLISHED"
    assert body["distribution"]["feedDistribution"] == "MAIN_FEED"

    assert post.posted_url == f"https://www.linkedin.com/feed/update/{POST_URN}/"
    assert post.public_access is True
    assert abs(dt.datetime.now(dt.UTC) - post.posted_at) < dt.timedelta(seconds=30)


async def test_ugc_post_urn_is_accepted(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(POSTS_URL).mock(return_value=created("urn:li:ugcPost:123456"))
    post = await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert post.posted_url == "https://www.linkedin.com/feed/update/urn:li:ugcPost:123456/"


async def test_configured_api_version_is_sent(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    route = respx_mock.post(POSTS_URL).mock(return_value=created())
    await make_adapter(http_client, version="202701").publish(MESSAGE, idempotency_key=KEY)
    assert route.calls.last.request.headers["LinkedIn-Version"] == "202701"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"x-restli-id": ""},
        {"x-restli-id": "not-a-urn"},
        {"x-restli-id": "urn:li:share:abc"},
        {"x-restli-id": "urn:li:person:1"},
    ],
    ids=["missing", "empty", "garbage", "non-numeric", "wrong-type"],
)
async def test_success_without_valid_post_id_is_an_ambiguous_error(
    http_client: httpx.AsyncClient, respx_mock: MockRouter, headers: dict[str, str]
) -> None:
    respx_mock.post(POSTS_URL).mock(return_value=httpx.Response(201, headers=headers))
    with pytest.raises(PlatformResponseError) as exc_info:
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert exc_info.value.ambiguous is True
    assert exc_info.value.retryable is False


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, PlatformAuthError),
        (403, PlatformAuthError),
        (422, PlatformRejectedError),
        (426, PlatformRejectedError),
        (500, PlatformUnavailableError),
    ],
)
async def test_http_errors_are_normalised(
    http_client: httpx.AsyncClient,
    respx_mock: MockRouter,
    status: int,
    expected: type[PlatformError],
) -> None:
    respx_mock.post(POSTS_URL).mock(return_value=httpx.Response(status, json={"message": "x"}))
    with pytest.raises(expected):
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)


async def test_rate_limit_exposes_retry_after(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(POSTS_URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "9"})
    )
    with pytest.raises(PlatformRateLimitError) as exc_info:
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert exc_info.value.retry_after == 9.0


async def test_expired_token_fails_fast_without_a_request(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    adapter = make_adapter(http_client, expires=dt.date(2020, 1, 1))
    with pytest.raises(PlatformAuthError, match="expired"):
        await adapter.publish(MESSAGE, idempotency_key=KEY)
    assert len(respx_mock.calls) == 0


async def test_token_expiring_today_counts_as_expired(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    adapter = make_adapter(http_client, expires=dt.datetime.now(dt.UTC).date())
    with pytest.raises(PlatformAuthError, match="expired"):
        await adapter.publish(MESSAGE, idempotency_key=KEY)
    assert len(respx_mock.calls) == 0


async def test_future_expiry_allows_publishing(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(POSTS_URL).mock(return_value=created())
    adapter = make_adapter(http_client, expires=dt.date(2099, 1, 1))
    post = await adapter.publish(MESSAGE, idempotency_key=KEY)
    assert post.posted_url is not None


async def test_read_timeout_is_ambiguous(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(POSTS_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(PlatformTimeoutError) as exc_info:
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert exc_info.value.ambiguous is True


async def test_token_never_appears_in_errors(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(POSTS_URL).mock(
        return_value=httpx.Response(401, json={"message": f"bad token {TOKEN}"})
    )
    with pytest.raises(PlatformAuthError) as exc_info:
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    error = exc_info.value
    assert TOKEN not in error.safe_message
    assert TOKEN not in (error.detail or "")
    assert TOKEN not in repr(error)