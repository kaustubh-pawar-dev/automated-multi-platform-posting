"""Tests for the Telegram adapter using mocked HTTP responses (nothing is published)."""

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
from app.platforms.telegram import TelegramAdapter

TOKEN = "123456:ABCdefGHIjklMNOpqrsTUVwxyz0123456789"
CHAT = "@my_channel"
SEND_URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
MESSAGE = "Grow your visibility https://example.com/a?x=1"
KEY = "key-abc"
DATE = 1_795_000_000


def make_adapter(
    client: httpx.AsyncClient, *, token: str | None = TOKEN, chat: str | None = CHAT
) -> TelegramAdapter:
    return TelegramAdapter(
        client, bot_token=SecretStr(token) if token else None, chat_id=chat
    )


def ok_body(**result: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "message_id": 42,
        "date": DATE,
        "chat": {
            "id": -1001234567890,
            "title": "My channel",
            "username": "my_channel",
            "type": "channel",
        },
    }
    base.update(result)
    return {"ok": True, "result": base}


def test_adapter_identity(http_client: httpx.AsyncClient) -> None:
    adapter = make_adapter(http_client)
    assert adapter.name == "Telegram"
    assert adapter.idempotent_retries is False


def test_configured_with_token_and_chat(http_client: httpx.AsyncClient) -> None:
    assert make_adapter(http_client).is_configured() is True


@pytest.mark.parametrize(
    ("token", "chat"), [(None, CHAT), (TOKEN, None)], ids=["no-token", "no-chat"]
)
def test_not_configured_when_missing(
    http_client: httpx.AsyncClient, token: str | None, chat: str | None
) -> None:
    assert make_adapter(http_client, token=token, chat=chat).is_configured() is False


async def test_unconfigured_publish_makes_no_request(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    with pytest.raises(PlatformNotConfiguredError):
        await make_adapter(http_client, token=None).publish(MESSAGE, idempotency_key=KEY)
    assert len(respx_mock.calls) == 0


async def test_successful_publish(http_client: httpx.AsyncClient, respx_mock: MockRouter) -> None:
    route = respx_mock.post(SEND_URL).mock(return_value=httpx.Response(200, json=ok_body()))
    post = await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)

    assert json.loads(route.calls.last.request.content) == {"chat_id": CHAT, "text": MESSAGE}
    assert post.posted_url == "https://t.me/my_channel/42"
    assert post.public_access is True
    assert post.posted_at == dt.datetime.fromtimestamp(DATE, dt.UTC)


async def test_message_is_sent_unchanged(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    message = "Héllo <b>wörld</b> ✓ *x* _y_\nhttps://example.com/x_y"
    route = respx_mock.post(SEND_URL).mock(return_value=httpx.Response(200, json=ok_body()))
    await make_adapter(http_client).publish(message, idempotency_key=KEY)
    body = json.loads(route.calls.last.request.content)
    assert body["text"] == message
    assert "parse_mode" not in body


async def test_private_chat_succeeds_without_public_url(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    body = ok_body(chat={"id": -100123456, "title": "Private", "type": "channel"})
    respx_mock.post(SEND_URL).mock(return_value=httpx.Response(200, json=body))
    post = await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert post.posted_url is None
    assert post.public_access is False


@pytest.mark.parametrize("username", ["bad/name", "x"])
async def test_invalid_username_in_response_gives_no_url(
    http_client: httpx.AsyncClient, respx_mock: MockRouter, username: str
) -> None:
    body = ok_body(chat={"id": -100123456, "username": username, "type": "channel"})
    respx_mock.post(SEND_URL).mock(return_value=httpx.Response(200, json=body))
    post = await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert post.posted_url is None
    assert post.public_access is False


@pytest.mark.parametrize("date", [None, "x", 0, -5])
async def test_missing_or_invalid_date_falls_back_to_now(
    http_client: httpx.AsyncClient, respx_mock: MockRouter, date: object
) -> None:
    respx_mock.post(SEND_URL).mock(return_value=httpx.Response(200, json=ok_body(date=date)))
    post = await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert abs(dt.datetime.now(dt.UTC) - post.posted_at) < dt.timedelta(seconds=30)


@pytest.mark.parametrize(
    "body",
    [
        {"ok": False},
        {"ok": True},
        {"ok": True, "result": []},
        ok_body(message_id=None),
        ok_body(message_id=0),
        ok_body(message_id="5"),
        ok_body(message_id=True),
    ],
    ids=["not-ok", "no-result", "result-list", "no-id", "zero-id", "string-id", "bool-id"],
)
async def test_invalid_success_responses_are_ambiguous_errors(
    http_client: httpx.AsyncClient, respx_mock: MockRouter, body: dict[str, Any]
) -> None:
    respx_mock.post(SEND_URL).mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(PlatformResponseError) as exc_info:
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert exc_info.value.ambiguous is True
    assert exc_info.value.retryable is False


@pytest.mark.parametrize("content", [b"not json", b"[]"])
async def test_unparsable_response_is_rejected(
    http_client: httpx.AsyncClient, respx_mock: MockRouter, content: bytes
) -> None:
    respx_mock.post(SEND_URL).mock(return_value=httpx.Response(200, content=content))
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
    respx_mock.post(SEND_URL).mock(
        return_value=httpx.Response(status, json={"ok": False, "description": "x"})
    )
    with pytest.raises(expected):
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)


async def test_rate_limit_reads_retry_after_from_body(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    body = {
        "ok": False,
        "error_code": 429,
        "description": "Too Many Requests: retry after 5",
        "parameters": {"retry_after": 5},
    }
    respx_mock.post(SEND_URL).mock(return_value=httpx.Response(429, json=body))
    with pytest.raises(PlatformRateLimitError) as exc_info:
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert exc_info.value.retry_after == 5.0
    assert exc_info.value.retryable is True


async def test_rate_limit_without_parameters_has_no_retry_after(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(SEND_URL).mock(return_value=httpx.Response(429, json={"ok": False}))
    with pytest.raises(PlatformRateLimitError) as exc_info:
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert exc_info.value.retry_after is None


async def test_read_timeout_is_ambiguous(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(SEND_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(PlatformTimeoutError) as exc_info:
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert exc_info.value.ambiguous is True


async def test_token_never_appears_in_errors(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    body = {"ok": False, "description": f"Unauthorized {TOKEN}"}
    respx_mock.post(SEND_URL).mock(return_value=httpx.Response(401, json=body))
    with pytest.raises(PlatformAuthError) as exc_info:
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    error = exc_info.value
    assert TOKEN not in error.safe_message
    assert TOKEN not in (error.detail or "")
    assert TOKEN not in repr(error)