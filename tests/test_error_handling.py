"""Edge cases for the exception handlers and the request middleware."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.middleware import RequestContextMiddleware


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def test_unknown_status_codes_get_a_generic_phrase(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    @app.get("/_test/odd-status")
    def odd_status() -> None:
        raise StarletteHTTPException(status_code=599)

    response = await client.get("/_test/odd-status")

    assert response.status_code == 599
    assert response.json()["error"] == "http_error"
    assert response.json()["message"] == "HTTP error"


async def test_http_exception_headers_are_preserved(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    @app.get("/_test/protected")
    def protected() -> None:
        raise StarletteHTTPException(status_code=401, headers={"WWW-Authenticate": "Bearer"})

    response = await client.get("/_test/protected")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"] == "unauthorized"
    assert response.json()["request_id"] == response.headers["x-request-id"]


async def test_unexpected_errors_on_any_route_return_a_generic_500(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    @app.get("/_test/crash")
    def crash() -> None:
        raise RuntimeError("SECRET-CRASH-DETAIL")

    response = await client.get("/_test/crash")

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "internal_error"
    assert body["request_id"] == response.headers["x-request-id"]
    assert "SECRET-CRASH-DETAIL" not in response.text


async def test_non_http_scopes_pass_through_the_middleware() -> None:
    seen: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    middleware = RequestContextMiddleware(inner)
    await middleware({"type": "websocket"}, None, None)  # type: ignore[arg-type]

    assert seen == ["websocket"]


@pytest.mark.parametrize(
    ("raw_value", "too_large"),
    [
        (b"70000", True),
        (b"65536", False),
        (b"abc", False),
        (b"\xb2\xb3", False),
        (b"", False),
    ],
    ids=["over-limit", "exactly-at-limit", "not-a-number", "superscript-digits", "empty"],
)
def test_declared_content_length_check(raw_value: bytes, too_large: bool) -> None:
    """Header bytes are decoded as latin-1, where characters like '²' pass isdigit()
    but make int() raise. The check must treat them as 'not declared', not crash."""

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        return None

    middleware = RequestContextMiddleware(inner)
    headers = Headers(raw=[(b"content-length", raw_value)])
    assert middleware._declared_too_large(headers) is too_large