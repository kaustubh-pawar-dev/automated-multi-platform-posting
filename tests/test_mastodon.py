"""Tests for the Mastodon adapter using mocked HTTP responses (nothing is published)."""

import datetime as dt
import json
from typing import Any

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
from app.platforms.mastodon import MastodonAdapter

BASE = "https://mastodon.test"
STATUS_URL = f"{BASE}/api/v1/statuses"
TOKEN = "test-token-123"
MESSAGE = "Grow your visibility https://example.com/a?x=1"
KEY = "key-abc"


def make_adapter(client: httpx.AsyncClient, *, configured: bool = True) -> MastodonAdapter:
    return MastodonAdapter(
        client,
        instance_url=BASE if configured else None,
        access_token=SecretStr(TOKEN) if configured else None,
    )


def status_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "110",
        "url": "https://mastodon.test/@bot/110",
        "visibility": "public",
        "created_at": "2026-11-15T10:30:00.123Z",
    }
    payload.update(overrides)
    return payload


def test_adapter_identity(http_client: httpx.AsyncClient) -> None:
    adapter = make_adapter(http_client)
    assert adapter.name == "Mastodon"
    assert adapter.idempotent_retries is True


def test_configured_when_url_and_token_present(http_client: httpx.AsyncClient) -> None:
    assert make_adapter(http_client).is_configured() is True


def test_not_configured_without_credentials(http_client: httpx.AsyncClient) -> None:
    assert make_adapter(http_client, configured=False).is_configured() is False


async def test_unconfigured_publish_raises_without_any_request(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    adapter = make_adapter(http_client, configured=False)
    with pytest.raises(PlatformNotConfiguredError):
        await adapter.publish(MESSAGE, idempotency_key=KEY)
    assert len(respx_mock.calls) == 0


async def test_successful_publish(http_client: httpx.AsyncClient, respx_mock: MockRouter) -> None:
    route = respx_mock.post(STATUS_URL).mock(
        return_value=httpx.Response(200, json=status_payload())
    )
    post = await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)

    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["Idempotency-Key"] == KEY
    assert json.loads(request.content) == {"status": MESSAGE, "visibility": "public"}

    assert post.posted_url == "https://mastodon.test/@bot/110"
    assert post.public_access is True
    assert post.posted_at == dt.datetime(2026, 11, 15, 10, 30, 0, 123000, tzinfo=dt.UTC)


async def test_message_is_sent_unchanged(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    message = "Héllo wörld ✓\nLine two https://example.com/x"
    route = respx_mock.post(STATUS_URL).mock(
        return_value=httpx.Response(200, json=status_payload())
    )
    await make_adapter(http_client).publish(message, idempotency_key=KEY)
    assert json.loads(route.calls.last.request.content)["status"] == message


@pytest.mark.parametrize("visibility", ["unlisted", "private", "direct", None])
async def test_public_access_reflects_reported_visibility(
    http_client: httpx.AsyncClient, respx_mock: MockRouter, visibility: str | None
) -> None:
    respx_mock.post(STATUS_URL).mock(
        return_value=httpx.Response(200, json=status_payload(visibility=visibility))
    )
    post = await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert post.public_access is False


@pytest.mark.parametrize("created_at", [None, "garbage", "2026-11-15T10:30:00"])
async def test_missing_or_invalid_timestamp_falls_back_to_now(
    http_client: httpx.AsyncClient, respx_mock: MockRouter, created_at: str | None
) -> None:
    respx_mock.post(STATUS_URL).mock(
        return_value=httpx.Response(200, json=status_payload(created_at=created_at))
    )
    post = await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert post.posted_at.tzinfo is not None
    assert abs(dt.datetime.now(dt.UTC) - post.posted_at) < dt.timedelta(seconds=30)


@pytest.mark.parametrize("url", [None, "http://mastodon.test/@bot/110", 123])
async def test_response_without_valid_url_is_rejected(
    http_client: httpx.AsyncClient, respx_mock: MockRouter, url: object
) -> None:
    respx_mock.post(STATUS_URL).mock(return_value=httpx.Response(200, json=status_payload(url=url)))
    with pytest.raises(PlatformResponseError):
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)


@pytest.mark.parametrize("body", [b"not json", b"[]"])
async def test_unparsable_response_is_rejected(
    http_client: httpx.AsyncClient, respx_mock: MockRouter, body: bytes
) -> None:
    respx_mock.post(STATUS_URL).mock(return_value=httpx.Response(200, content=body))
    with pytest.raises(PlatformResponseError):
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, PlatformAuthError),
        (403, PlatformAuthError),
        (422, PlatformRejectedError),
        (429, PlatformRateLimitError),
        (503, PlatformUnavailableError),
    ],
)
async def test_http_errors_are_normalised(
    http_client: httpx.AsyncClient,
    respx_mock: MockRouter,
    status: int,
    expected: type[PlatformError],
) -> None:
    respx_mock.post(STATUS_URL).mock(return_value=httpx.Response(status, json={"error": "x"}))
    with pytest.raises(expected):
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)


async def test_rate_limit_exposes_retry_after(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(STATUS_URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "7"})
    )
    with pytest.raises(PlatformRateLimitError) as exc_info:
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert exc_info.value.retry_after == 7.0


async def test_read_timeout_is_ambiguous_timeout_error(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(STATUS_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(PlatformTimeoutError) as exc_info:
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert exc_info.value.ambiguous is True


async def test_retry_reuses_the_same_idempotency_key(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    route = respx_mock.post(STATUS_URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=status_payload())]
    )
    adapter = make_adapter(http_client)
    with pytest.raises(PlatformUnavailableError):
        await adapter.publish(MESSAGE, idempotency_key=KEY)
    post = await adapter.publish(MESSAGE, idempotency_key=KEY)

    assert [call.request.headers["Idempotency-Key"] for call in route.calls] == [KEY, KEY]
    assert post.posted_url == "https://mastodon.test/@bot/110"


async def test_token_never_appears_in_errors(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(STATUS_URL).mock(return_value=httpx.Response(401, json={"error": TOKEN}))
    with pytest.raises(PlatformAuthError) as exc_info:
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert TOKEN not in exc_info.value.safe_message
    assert TOKEN not in (exc_info.value.detail or "")
    assert TOKEN not in repr(exc_info.value)