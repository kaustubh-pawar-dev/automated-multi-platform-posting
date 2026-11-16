"""Tests for the shared platform HTTP helpers."""

import datetime as dt

import httpx
import pytest
from respx import MockRouter

from app.core.exceptions import (
    PlatformAuthError,
    PlatformError,
    PlatformRateLimitError,
    PlatformRejectedError,
    PlatformResponseError,
    PlatformTimeoutError,
    PlatformUnavailableError,
)
from app.platforms.http import (
    ensure_allowed_url,
    parse_json_object,
    parse_platform_timestamp,
    parse_retry_after,
    raise_for_status,
    send_request,
)

ALLOWED = frozenset({"mastodon.test"})
URL = "https://mastodon.test/api"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("3", 3.0),
        ("0", 0.0),
        ("1.5", 1.5),
        ("999", 60.0),
        (None, None),
        ("soon", None),
        ("-5", None),
        ("nan", None),
        ("inf", None),
    ],
)
def test_parse_retry_after(value: str | None, expected: float | None) -> None:
    assert parse_retry_after(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-15T10:30:00.123Z", dt.datetime(2026, 11, 15, 10, 30, 0, 123000, tzinfo=dt.UTC)),
        ("2026-11-15T16:00:00+05:30", dt.datetime(2026, 11, 15, 10, 30, tzinfo=dt.UTC)),
        ("2026-11-15T10:30:00", None),
        ("garbage", None),
        (None, None),
        (1234, None),
    ],
)
def test_parse_platform_timestamp(value: object, expected: dt.datetime | None) -> None:
    assert parse_platform_timestamp(value) == expected


def test_parse_json_object_returns_dict() -> None:
    assert parse_json_object(httpx.Response(200, json={"a": 1})) == {"a": 1}


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b""])
def test_parse_json_object_rejects_invalid_bodies(body: bytes) -> None:
    with pytest.raises(PlatformResponseError):
        parse_json_object(httpx.Response(200, content=body))


def test_allowed_url_passes() -> None:
    ensure_allowed_url("https://mastodon.test/api/v1/statuses", ALLOWED)


@pytest.mark.parametrize(
    "url",
    [
        "http://mastodon.test/x",
        "https://evil.test/x",
        "https://mastodon.test.evil.test/x",
        "ftp://mastodon.test",
        "not a url",
        "https://mastodon.test@evil.test/",
    ],
)
def test_unapproved_urls_are_blocked(url: str) -> None:
    with pytest.raises(PlatformError, match="blocked"):
        ensure_allowed_url(url, ALLOWED)


async def test_blocked_host_makes_no_request(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    with pytest.raises(PlatformError, match="blocked"):
        await send_request(http_client, "POST", "https://evil.test/x", allowed_hosts=ALLOWED)
    assert len(respx_mock.calls) == 0


async def test_send_request_returns_response(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(URL).mock(return_value=httpx.Response(200, json={"ok": True}))
    response = await send_request(
        http_client, "POST", URL, allowed_hosts=ALLOWED, json_body={"a": 1}
    )
    assert response.json() == {"ok": True}


@pytest.mark.parametrize(
    ("error", "expected", "ambiguous"),
    [
        (httpx.ConnectTimeout("x"), PlatformTimeoutError, False),
        (httpx.PoolTimeout("x"), PlatformTimeoutError, False),
        (httpx.ReadTimeout("x"), PlatformTimeoutError, True),
        (httpx.WriteTimeout("x"), PlatformTimeoutError, True),
        (httpx.ConnectError("x"), PlatformUnavailableError, False),
        (httpx.ReadError("x"), PlatformUnavailableError, True),
        (httpx.RemoteProtocolError("x"), PlatformUnavailableError, True),
    ],
)
async def test_network_errors_are_normalised(
    http_client: httpx.AsyncClient,
    respx_mock: MockRouter,
    error: Exception,
    expected: type[PlatformError],
    ambiguous: bool,
) -> None:
    respx_mock.post(URL).mock(side_effect=error)
    with pytest.raises(expected) as exc_info:
        await send_request(http_client, "POST", URL, allowed_hosts=ALLOWED)
    assert exc_info.value.retryable is True
    assert exc_info.value.ambiguous is ambiguous


async def test_exception_text_is_never_copied_into_errors(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(URL).mock(side_effect=httpx.ConnectError("token=secret-in-url"))
    with pytest.raises(PlatformUnavailableError) as exc_info:
        await send_request(http_client, "POST", URL, allowed_hosts=ALLOWED)
    assert "secret-in-url" not in exc_info.value.safe_message
    assert "secret-in-url" not in (exc_info.value.detail or "")


@pytest.mark.parametrize(
    ("status", "expected", "retryable", "ambiguous"),
    [
        (401, PlatformAuthError, False, False),
        (403, PlatformAuthError, False, False),
        (429, PlatformRateLimitError, True, False),
        (500, PlatformUnavailableError, True, True),
        (502, PlatformUnavailableError, True, True),
        (503, PlatformUnavailableError, True, True),
        (504, PlatformUnavailableError, True, True),
        (400, PlatformRejectedError, False, False),
        (404, PlatformRejectedError, False, False),
        (422, PlatformRejectedError, False, False),
        (302, PlatformResponseError, False, False),
    ],
)
def test_status_codes_map_to_errors(
    status: int, expected: type[PlatformError], retryable: bool, ambiguous: bool
) -> None:
    with pytest.raises(expected) as exc_info:
        raise_for_status(httpx.Response(status))
    assert exc_info.value.retryable is retryable
    assert exc_info.value.ambiguous is ambiguous


def test_success_statuses_do_not_raise() -> None:
    for status in (200, 201, 204):
        raise_for_status(httpx.Response(status))


def test_rate_limit_carries_retry_after() -> None:
    response = httpx.Response(429, headers={"Retry-After": "7"})
    with pytest.raises(PlatformRateLimitError) as exc_info:
        raise_for_status(response)
    assert exc_info.value.retry_after == 7.0


def test_rejected_message_includes_status() -> None:
    with pytest.raises(PlatformRejectedError, match="HTTP 422"):
        raise_for_status(httpx.Response(422))


def test_response_body_is_never_copied_into_errors() -> None:
    response = httpx.Response(422, json={"error": "echo secret-body-value"})
    with pytest.raises(PlatformRejectedError) as exc_info:
        raise_for_status(response)
    assert "secret-body-value" not in exc_info.value.safe_message
    assert "secret-body-value" not in (exc_info.value.detail or "")