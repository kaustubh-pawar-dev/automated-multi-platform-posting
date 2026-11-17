"""Tests for the Discord adapter using mocked HTTP responses (nothing is published)."""

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
from app.platforms.discord import DiscordAdapter

TOKEN = "abcdefghijklmnopqrstuvwxyz0123456789"
WEBHOOK = f"https://discord.com/api/webhooks/123456789012345678/{TOKEN}"
GUILD = "111111111111111111"
CHANNEL = "987654321098765432"
MESSAGE_ID = "555555555555555555"
MESSAGE = "Grow your visibility https://example.com/a?x=1"
KEY = "key-abc"
WAIT = {"wait": "true"}


def make_adapter(
    client: httpx.AsyncClient, *, url: str | None = WEBHOOK, guild: str | None = GUILD
) -> DiscordAdapter:
    return DiscordAdapter(
        client, webhook_url=SecretStr(url) if url else None, guild_id=guild
    )


def message_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": MESSAGE_ID,
        "channel_id": CHANNEL,
        "content": MESSAGE,
        "timestamp": "2026-11-15T10:30:00.123456+00:00",
    }
    body.update(overrides)
    return body


def test_adapter_identity(http_client: httpx.AsyncClient) -> None:
    adapter = make_adapter(http_client)
    assert adapter.name == "Discord"
    assert adapter.idempotent_retries is False


def test_configured_with_webhook(http_client: httpx.AsyncClient) -> None:
    assert make_adapter(http_client).is_configured() is True


def test_not_configured_without_webhook(http_client: httpx.AsyncClient) -> None:
    assert make_adapter(http_client, url=None).is_configured() is False


async def test_unconfigured_publish_makes_no_request(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    with pytest.raises(PlatformNotConfiguredError):
        await make_adapter(http_client, url=None).publish(MESSAGE, idempotency_key=KEY)
    assert len(respx_mock.calls) == 0


async def test_successful_publish(http_client: httpx.AsyncClient, respx_mock: MockRouter) -> None:
    route = respx_mock.post(WEBHOOK, params=WAIT).mock(
        return_value=httpx.Response(200, json=message_body())
    )
    post = await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)

    request = route.calls.last.request
    assert request.url.params["wait"] == "true"
    assert json.loads(request.content) == {
        "content": MESSAGE,
        "allowed_mentions": {"parse": []},
    }
    assert post.posted_url == f"https://discord.com/channels/{GUILD}/{CHANNEL}/{MESSAGE_ID}"
    assert post.public_access is False
    assert post.posted_at == dt.datetime(2026, 11, 15, 10, 30, 0, 123456, tzinfo=dt.UTC)


async def test_without_guild_id_the_post_succeeds_with_no_url(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(WEBHOOK, params=WAIT).mock(
        return_value=httpx.Response(200, json=message_body())
    )
    post = await make_adapter(http_client, guild=None).publish(MESSAGE, idempotency_key=KEY)
    assert post.posted_url is None
    assert post.public_access is False


async def test_mentions_are_always_suppressed_and_text_is_unchanged(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    message = "@everyone <@123> héllo ✓ https://example.com/x_y"
    route = respx_mock.post(WEBHOOK, params=WAIT).mock(
        return_value=httpx.Response(200, json=message_body())
    )
    await make_adapter(http_client).publish(message, idempotency_key=KEY)
    body = json.loads(route.calls.last.request.content)
    assert body["content"] == message
    assert body["allowed_mentions"] == {"parse": []}


@pytest.mark.parametrize("timestamp", [None, "garbage", "2026-11-15T10:30:00"])
async def test_missing_or_invalid_timestamp_falls_back_to_now(
    http_client: httpx.AsyncClient, respx_mock: MockRouter, timestamp: str | None
) -> None:
    respx_mock.post(WEBHOOK, params=WAIT).mock(
        return_value=httpx.Response(200, json=message_body(timestamp=timestamp))
    )
    post = await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert abs(dt.datetime.now(dt.UTC) - post.posted_at) < dt.timedelta(seconds=30)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"id": 5, "channel_id": CHANNEL},
        {"id": "abc", "channel_id": CHANNEL},
        {"id": MESSAGE_ID},
        {"id": MESSAGE_ID, "channel_id": "x"},
    ],
    ids=["empty", "numeric-id", "non-numeric-id", "no-channel", "bad-channel"],
)
async def test_invalid_success_responses_are_ambiguous_errors(
    http_client: httpx.AsyncClient, respx_mock: MockRouter, body: dict[str, Any]
) -> None:
    respx_mock.post(WEBHOOK, params=WAIT).mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(PlatformResponseError) as exc_info:
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert exc_info.value.ambiguous is True
    assert exc_info.value.retryable is False


@pytest.mark.parametrize(
    ("status", "content"), [(204, b""), (200, b"not json")], ids=["no-content", "not-json"]
)
async def test_responses_without_a_message_are_rejected(
    http_client: httpx.AsyncClient, respx_mock: MockRouter, status: int, content: bytes
) -> None:
    respx_mock.post(WEBHOOK, params=WAIT).mock(
        return_value=httpx.Response(status, content=content)
    )
    with pytest.raises(PlatformResponseError):
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, PlatformAuthError),
        (403, PlatformAuthError),
        (404, PlatformAuthError),
        (400, PlatformRejectedError),
        (500, PlatformUnavailableError),
    ],
)
async def test_http_errors_are_normalised(
    http_client: httpx.AsyncClient,
    respx_mock: MockRouter,
    status: int,
    expected: type[PlatformError],
) -> None:
    respx_mock.post(WEBHOOK, params=WAIT).mock(
        return_value=httpx.Response(status, json={"code": 0, "message": "x"})
    )
    with pytest.raises(expected):
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)


async def test_rate_limit_reads_retry_after_from_body(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    body = {"message": "You are being rate limited.", "retry_after": 1.5, "global": False}
    respx_mock.post(WEBHOOK, params=WAIT).mock(return_value=httpx.Response(429, json=body))
    with pytest.raises(PlatformRateLimitError) as exc_info:
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert exc_info.value.retry_after == 1.5
    assert exc_info.value.retryable is True


async def test_rate_limit_falls_back_to_the_header(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(WEBHOOK, params=WAIT).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "4"}, content=b"<html>")
    )
    with pytest.raises(PlatformRateLimitError) as exc_info:
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert exc_info.value.retry_after == 4.0


async def test_rate_limit_without_any_hint_has_no_retry_after(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(WEBHOOK, params=WAIT).mock(return_value=httpx.Response(429, json={}))
    with pytest.raises(PlatformRateLimitError) as exc_info:
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert exc_info.value.retry_after is None


async def test_read_timeout_is_ambiguous(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(WEBHOOK, params=WAIT).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(PlatformTimeoutError) as exc_info:
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert exc_info.value.ambiguous is True


async def test_webhook_token_never_appears_in_errors(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    body = {"code": 50027, "message": f"Invalid Webhook Token {TOKEN} {WEBHOOK}"}
    respx_mock.post(WEBHOOK, params=WAIT).mock(return_value=httpx.Response(401, json=body))
    with pytest.raises(PlatformAuthError) as exc_info:
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    error = exc_info.value
    for text in (error.safe_message, error.detail or "", repr(error)):
        assert TOKEN not in text
        assert "webhooks" not in text


async def test_requests_never_leave_discords_own_hosts(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    evil = f"https://evil.example/api/webhooks/123456789012345678/{TOKEN}"
    with pytest.raises(PlatformError, match="blocked"):
        await make_adapter(http_client, url=evil).publish(MESSAGE, idempotency_key=KEY)
    assert len(respx_mock.calls) == 0