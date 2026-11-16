"""Tests for the Bluesky adapter using mocked HTTP responses (nothing is published)."""

import asyncio
import datetime as dt
import json
import re
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
from app.platforms.bluesky import BlueskyAdapter

SERVICE = "https://bsky.test"
SESSION_URL = f"{SERVICE}/xrpc/com.atproto.server.createSession"
RECORD_URL = f"{SERVICE}/xrpc/com.atproto.repo.createRecord"
HANDLE = "user.bsky.test"
PASSWORD = "app-pass-1234"
DID = "did:plc:abc123"
MESSAGE = "Grow your visibility https://example.com/a?x=1"
KEY = "key-abc"

SESSION: dict[str, Any] = {
    "accessJwt": "access-1",
    "refreshJwt": "refresh-1",
    "did": DID,
    "handle": HANDLE,
}
RECORD: dict[str, Any] = {"uri": f"at://{DID}/app.bsky.feed.post/3kabc", "cid": "bafyrei"}
EXPIRED = {"error": "ExpiredToken", "message": "Token has expired"}


def make_adapter(
    client: httpx.AsyncClient,
    *,
    handle: str | None = HANDLE,
    password: str | None = PASSWORD,
) -> BlueskyAdapter:
    return BlueskyAdapter(
        client,
        service_url=SERVICE,
        handle=handle,
        app_password=SecretStr(password) if password else None,
    )


def test_adapter_identity(http_client: httpx.AsyncClient) -> None:
    adapter = make_adapter(http_client)
    assert adapter.name == "Bluesky"
    assert adapter.idempotent_retries is False
    assert adapter.is_configured() is True


@pytest.mark.parametrize(
    ("handle", "password"), [(None, PASSWORD), (HANDLE, None)], ids=["no-handle", "no-password"]
)
def test_not_configured_when_credentials_missing(
    http_client: httpx.AsyncClient, handle: str | None, password: str | None
) -> None:
    assert make_adapter(http_client, handle=handle, password=password).is_configured() is False


async def test_unconfigured_publish_raises_without_any_request(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    adapter = make_adapter(http_client, handle=None)
    with pytest.raises(PlatformNotConfiguredError):
        await adapter.publish(MESSAGE, idempotency_key=KEY)
    assert len(respx_mock.calls) == 0


async def test_successful_publish(http_client: httpx.AsyncClient, respx_mock: MockRouter) -> None:
    session_route = respx_mock.post(SESSION_URL).mock(
        return_value=httpx.Response(200, json=SESSION)
    )
    record_route = respx_mock.post(RECORD_URL).mock(return_value=httpx.Response(200, json=RECORD))

    post = await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)

    assert json.loads(session_route.calls.last.request.content) == {
        "identifier": HANDLE,
        "password": PASSWORD,
    }
    request = record_route.calls.last.request
    assert request.headers["Authorization"] == "Bearer access-1"
    body = json.loads(request.content)
    assert body["repo"] == DID
    assert body["collection"] == "app.bsky.feed.post"
    record = body["record"]
    assert record["$type"] == "app.bsky.feed.post"
    assert record["text"] == MESSAGE
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", record["createdAt"])

    assert post.posted_url == f"https://bsky.app/profile/{HANDLE}/post/3kabc"
    assert post.public_access is True
    assert post.posted_at.tzinfo is not None
    assert abs(dt.datetime.now(dt.UTC) - post.posted_at) < dt.timedelta(seconds=30)


async def test_link_facet_uses_utf8_byte_offsets(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    url = "https://example.com/x?a=1"
    message = f"café ✓ {url}"
    respx_mock.post(SESSION_URL).mock(return_value=httpx.Response(200, json=SESSION))
    record_route = respx_mock.post(RECORD_URL).mock(return_value=httpx.Response(200, json=RECORD))

    await make_adapter(http_client).publish(message, idempotency_key=KEY)

    record = json.loads(record_route.calls.last.request.content)["record"]
    facet = record["facets"][0]
    start, end = facet["index"]["byteStart"], facet["index"]["byteEnd"]
    assert message.encode("utf-8")[start:end].decode("utf-8") == url
    assert facet["features"] == [{"$type": "app.bsky.richtext.facet#link", "uri": url}]


async def test_no_facet_when_message_has_no_link(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(SESSION_URL).mock(return_value=httpx.Response(200, json=SESSION))
    record_route = respx_mock.post(RECORD_URL).mock(return_value=httpx.Response(200, json=RECORD))

    await make_adapter(http_client).publish("no link here", idempotency_key=KEY)

    record = json.loads(record_route.calls.last.request.content)["record"]
    assert "facets" not in record
    assert record["text"] == "no link here"


async def test_session_is_reused_between_publications(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    session_route = respx_mock.post(SESSION_URL).mock(
        return_value=httpx.Response(200, json=SESSION)
    )
    record_route = respx_mock.post(RECORD_URL).mock(return_value=httpx.Response(200, json=RECORD))
    adapter = make_adapter(http_client)

    await adapter.publish(MESSAGE, idempotency_key="a")
    await adapter.publish(MESSAGE, idempotency_key="b")

    assert session_route.call_count == 1
    assert record_route.call_count == 2


async def test_concurrent_publications_share_one_session(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    session_route = respx_mock.post(SESSION_URL).mock(
        return_value=httpx.Response(200, json=SESSION)
    )
    record_route = respx_mock.post(RECORD_URL).mock(return_value=httpx.Response(200, json=RECORD))
    adapter = make_adapter(http_client)

    await asyncio.gather(
        adapter.publish(MESSAGE, idempotency_key="a"),
        adapter.publish(MESSAGE, idempotency_key="b"),
    )

    assert session_route.call_count == 1
    assert record_route.call_count == 2


async def test_expired_token_triggers_one_new_login_and_retry(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    session_route = respx_mock.post(SESSION_URL).mock(
        side_effect=[
            httpx.Response(200, json=SESSION),
            httpx.Response(200, json={**SESSION, "accessJwt": "access-2"}),
        ]
    )
    record_route = respx_mock.post(RECORD_URL).mock(
        side_effect=[httpx.Response(400, json=EXPIRED), httpx.Response(200, json=RECORD)]
    )

    post = await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)

    assert session_route.call_count == 2
    assert [call.request.headers["Authorization"] for call in record_route.calls] == [
        "Bearer access-1",
        "Bearer access-2",
    ]
    assert post.posted_url == f"https://bsky.app/profile/{HANDLE}/post/3kabc"


async def test_token_rejected_again_after_new_login_is_an_auth_error(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    session_route = respx_mock.post(SESSION_URL).mock(
        side_effect=[
            httpx.Response(200, json=SESSION),
            httpx.Response(200, json={**SESSION, "accessJwt": "access-2"}),
        ]
    )
    record_route = respx_mock.post(RECORD_URL).mock(
        side_effect=[httpx.Response(400, json=EXPIRED), httpx.Response(401, json=EXPIRED)]
    )

    with pytest.raises(PlatformAuthError):
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)

    assert session_route.call_count == 2
    assert record_route.call_count == 2


async def test_bad_credentials_fail_before_any_post_is_attempted(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(SESSION_URL).mock(
        return_value=httpx.Response(401, json={"error": "AuthenticationRequired"})
    )
    with pytest.raises(PlatformAuthError):
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)


async def test_login_rate_limit_exposes_retry_after(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(SESSION_URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "12"})
    )
    with pytest.raises(PlatformRateLimitError) as exc_info:
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert exc_info.value.retry_after == 12.0


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, {"error": "AuthMissing"}, PlatformAuthError),
        (429, {"error": "RateLimitExceeded"}, PlatformRateLimitError),
        (500, {"error": "InternalServerError"}, PlatformUnavailableError),
        (400, {"error": "InvalidRequest"}, PlatformRejectedError),
    ],
)
async def test_post_errors_are_normalised(
    http_client: httpx.AsyncClient,
    respx_mock: MockRouter,
    status: int,
    body: dict[str, str],
    expected: type[PlatformError],
) -> None:
    respx_mock.post(SESSION_URL).mock(return_value=httpx.Response(200, json=SESSION))
    respx_mock.post(RECORD_URL).mock(return_value=httpx.Response(status, json=body))
    with pytest.raises(expected):
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)


async def test_read_timeout_on_post_is_ambiguous(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(SESSION_URL).mock(return_value=httpx.Response(200, json=SESSION))
    respx_mock.post(RECORD_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(PlatformTimeoutError) as exc_info:
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert exc_info.value.ambiguous is True


@pytest.mark.parametrize(
    "payload",
    [{}, {"did": DID}, {"accessJwt": "access-1"}],
    ids=["empty", "no-token", "no-did"],
)
async def test_incomplete_session_response_is_rejected(
    http_client: httpx.AsyncClient, respx_mock: MockRouter, payload: dict[str, Any]
) -> None:
    respx_mock.post(SESSION_URL).mock(return_value=httpx.Response(200, json=payload))
    with pytest.raises(PlatformResponseError):
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)


@pytest.mark.parametrize(
    "payload",
    [{}, {"uri": 5}, {"uri": "https://example.com/not-an-at-uri"}],
    ids=["empty", "non-string", "wrong-scheme"],
)
async def test_invalid_record_response_is_rejected(
    http_client: httpx.AsyncClient, respx_mock: MockRouter, payload: dict[str, Any]
) -> None:
    respx_mock.post(SESSION_URL).mock(return_value=httpx.Response(200, json=SESSION))
    respx_mock.post(RECORD_URL).mock(return_value=httpx.Response(200, json=payload))
    with pytest.raises(PlatformResponseError):
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)


@pytest.mark.parametrize(
    "uri",
    [
        f"at://{DID}/app.bsky.feed.like/3kabc",
        f"at://{DID}/app.bsky.feed.post",
        f"at://{DID}/app.bsky.feed.post/bad key!",
    ],
    ids=["other-collection", "too-few-parts", "invalid-rkey"],
)
async def test_unexpected_uri_shape_succeeds_without_a_url(
    http_client: httpx.AsyncClient, respx_mock: MockRouter, uri: str
) -> None:
    respx_mock.post(SESSION_URL).mock(return_value=httpx.Response(200, json=SESSION))
    respx_mock.post(RECORD_URL).mock(return_value=httpx.Response(200, json={"uri": uri}))
    post = await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert post.posted_url is None
    assert post.public_access is True


async def test_credentials_never_appear_in_errors(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(SESSION_URL).mock(
        return_value=httpx.Response(401, json={"error": PASSWORD, "message": PASSWORD})
    )
    with pytest.raises(PlatformAuthError) as exc_info:
        await make_adapter(http_client).publish(MESSAGE, idempotency_key=KEY)
    assert PASSWORD not in exc_info.value.safe_message
    assert PASSWORD not in (exc_info.value.detail or "")
    assert PASSWORD not in repr(exc_info.value)