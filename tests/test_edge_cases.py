"""Edge-case tests for behaviour the main suites do not reach.

Each test targets a specific branch found with ``pytest --cov --cov-branch``: real
but unusual inputs (non-JSON error bodies, malformed URLs, empty secrets) and the
defensive guards that protect invariants the normal flow already guarantees.
"""

import json

import httpx
import pytest
from pydantic import SecretStr
from respx import MockRouter

from app.api.errors import error_response
from app.core.config import Settings
from app.core.exceptions import (
    PlatformAuthError,
    PlatformError,
    PlatformNotConfiguredError,
    PlatformRateLimitError,
    PlatformUnavailableError,
)
from app.core.logging import REQUEST_ID_HEADER, collect_secrets
from app.platforms.bluesky import BlueskyAdapter, _link_facets
from app.platforms.http import ensure_allowed_url
from app.platforms.registry import PlatformRegistry
from app.platforms.telegram import TelegramAdapter
from app.services.message_validation import validate_message
from app.services.posting_service import PostingService
from app.utils.url_validator import InvalidUrlError, validate_cta_url
from tests.fakes import ScriptedAdapter

BSKY = "https://bsky.test"
SESSION_URL = f"{BSKY}/xrpc/com.atproto.server.createSession"
RECORD_URL = f"{BSKY}/xrpc/com.atproto.repo.createRecord"
SESSION = {"accessJwt": "access-1", "did": "did:plc:abc123", "handle": "user.bsky.test"}
TG_TOKEN = "123456:ABCdefGHIjklMNOpqrsTUVwxyz0123456789"
TG_URL = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
MESSAGE = "Grow your visibility https://example.com/a?x=1"


def make_bluesky(
    client: httpx.AsyncClient, *, handle: str | None = "user.bsky.test"
) -> BlueskyAdapter:
    return BlueskyAdapter(
        client, service_url=BSKY, handle=handle, app_password=SecretStr("app-pass-1234")
    )


def make_telegram(client: httpx.AsyncClient) -> TelegramAdapter:
    return TelegramAdapter(client, bot_token=SecretStr(TG_TOKEN), chat_id="@my_channel")


def test_error_response_without_a_request_context_has_no_request_id() -> None:
    response = error_response(500, "internal_error", "Something went wrong.")

    assert REQUEST_ID_HEADER.lower() not in response.headers
    assert json.loads(response.body)["request_id"] is None


def test_error_response_with_an_explicit_request_id_sets_the_header() -> None:
    response = error_response(400, "validation_error", "Bad.", request_id="explicit-id-12345")

    assert response.headers[REQUEST_ID_HEADER] == "explicit-id-12345"
    assert json.loads(response.body)["request_id"] == "explicit-id-12345"


def test_platform_error_retryable_flag_can_be_overridden_per_instance() -> None:
    assert PlatformError("x").retryable is False
    assert PlatformError("x", retryable=True).retryable is True
    assert PlatformAuthError(retryable=True).retryable is True
    assert PlatformUnavailableError(retryable=False).retryable is False


def test_empty_secret_values_are_not_collected_for_redaction() -> None:
    settings = Settings(_env_file=None, mastodon_access_token=SecretStr(""))

    assert collect_secrets(settings) == []


def test_link_facet_guard_returns_nothing_if_the_url_is_not_in_the_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.platforms.bluesky.find_url_candidates", lambda text: ["https://other.example"]
    )

    assert _link_facets("hello world") == []


async def test_unparsable_error_body_is_not_mistaken_for_an_expired_token(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    session_route = respx_mock.post(SESSION_URL).mock(
        return_value=httpx.Response(200, json=SESSION)
    )
    record_route = respx_mock.post(RECORD_URL).mock(
        return_value=httpx.Response(401, content=b"<html>denied</html>")
    )

    with pytest.raises(PlatformAuthError):
        await make_bluesky(http_client).publish(MESSAGE, idempotency_key="key-abc")

    assert session_route.call_count == 1
    assert record_route.call_count == 1


async def test_bluesky_session_creation_refuses_to_run_unconfigured(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    adapter = make_bluesky(http_client, handle=None)

    with pytest.raises(PlatformNotConfiguredError):
        await adapter._create_session()

    assert len(respx_mock.calls) == 0


@pytest.mark.parametrize("url", ["https://[::1", "https://[invalid"])
def test_malformed_urls_are_blocked_instead_of_crashing(url: str) -> None:
    with pytest.raises(PlatformError, match="blocked"):
        ensure_allowed_url(url, frozenset({"mastodon.test"}))


async def test_telegram_rate_limit_with_a_non_json_body_uses_the_header(
    http_client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    respx_mock.post(TG_URL).mock(
        return_value=httpx.Response(
            429, headers={"Retry-After": "3"}, content=b"<html>slow down</html>"
        )
    )

    with pytest.raises(PlatformRateLimitError) as exc_info:
        await make_telegram(http_client).publish(MESSAGE, idempotency_key="key-abc")

    assert exc_info.value.retry_after == 3.0


@pytest.mark.parametrize("value", ["5", True], ids=["string", "boolean"])
async def test_telegram_rate_limit_ignores_a_non_numeric_body_value(
    http_client: httpx.AsyncClient, respx_mock: MockRouter, value: object
) -> None:
    body = {"ok": False, "parameters": {"retry_after": value}}
    respx_mock.post(TG_URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "2"}, json=body)
    )

    with pytest.raises(PlatformRateLimitError) as exc_info:
        await make_telegram(http_client).publish(MESSAGE, idempotency_key="key-abc")

    assert exc_info.value.retry_after == 2.0


class _Abort(BaseException):
    """Stands in for a failure that escapes the per-platform exception handling."""


async def test_a_task_that_escapes_error_handling_is_reported_as_a_failure() -> None:
    service = PostingService(
        PlatformRegistry([ScriptedAdapter("Aborting", [_Abort()]), ScriptedAdapter("Fine")]),
        max_concurrent=5,
        max_retries=0,
        backoff_base_seconds=0.01,
        platform_deadline_seconds=5.0,
    )

    response = await service.publish(validate_message("Hello world https://example.com"))

    assert [result.status for result in response.results] == ["failed", "success"]
    assert response.results[0].error == "Unexpected error while publishing"
    assert response.failed_count == 1


@pytest.mark.parametrize("url", ["https:example.com", "http:/example.com"])
def test_urls_without_the_double_slash_are_rejected(url: str) -> None:
    with pytest.raises(InvalidUrlError) as exc_info:
        validate_cta_url(url)

    assert exc_info.value.code == "INVALID_URL"


def test_hostnames_longer_than_253_characters_are_rejected() -> None:
    url = "https://" + ".".join(["a" * 50] * 6) + ".com"

    with pytest.raises(InvalidUrlError) as exc_info:
        validate_cta_url(url)

    assert exc_info.value.code == "INVALID_URL"