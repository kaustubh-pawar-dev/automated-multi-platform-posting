"""Tests for total and per-platform execution timing."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.core.exceptions import PlatformAuthError, PlatformUnavailableError
from app.platforms.base import PlatformAdapter
from app.platforms.registry import PlatformRegistry
from app.schemas.response import PostingResponse
from app.services.message_validation import validate_message
from app.services.posting_service import PostingService
from tests.fakes import ScriptedAdapter, ok_post

MESSAGE = validate_message("Hello world https://example.com")
RESULT_KEYS = {"platform", "status", "posted_url", "public_access", "date", "time", "error"}


class _Abort(BaseException):
    """Stands in for a failure that escapes the per-platform error handling."""


def make_service(adapters: list[PlatformAdapter], **overrides: Any) -> PostingService:
    params: dict[str, Any] = {
        "max_concurrent": 5,
        "max_retries": 2,
        "backoff_base_seconds": 0.5,
        "platform_deadline_seconds": 30.0,
    }
    params.update(overrides)
    return PostingService(PlatformRegistry(adapters), **params)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def test_durations_are_reported_for_attempted_platforms_only() -> None:
    adapters = [
        ScriptedAdapter("Fast"),
        ScriptedAdapter("Slow", delay=0.1),
        ScriptedAdapter("Idle", configured=False),
    ]

    response = await make_service(adapters).publish(MESSAGE)

    durations = response.platform_durations_ms
    assert set(durations) == {"Fast", "Slow"}
    assert durations["Slow"] >= 50
    assert durations["Slow"] > durations["Fast"]
    assert response.total_duration_ms >= durations["Slow"]


async def test_failed_platforms_have_durations_too() -> None:
    adapter = ScriptedAdapter("Denied", [PlatformAuthError()])

    response = await make_service([adapter]).publish(MESSAGE)

    assert response.results[0].status == "failed"
    assert set(response.platform_durations_ms) == {"Denied"}


async def test_retry_waits_are_included_in_the_platform_duration() -> None:
    adapter = ScriptedAdapter("Flaky", [PlatformUnavailableError(), ok_post()])
    service = make_service([adapter], backoff_base_seconds=0.1, jitter=lambda: 1.0)

    response = await service.publish(MESSAGE)

    assert len(adapter.calls) == 2
    assert response.platform_durations_ms["Flaky"] >= 50


async def test_a_task_that_escapes_error_handling_has_no_duration_entry() -> None:
    adapters = [ScriptedAdapter("Aborting", [_Abort()]), ScriptedAdapter("Fine")]

    response = await make_service(adapters, max_retries=0).publish(MESSAGE)

    assert set(response.platform_durations_ms) == {"Fine"}
    assert [result.status for result in response.results] == ["failed", "success"]


def test_timings_default_to_empty_when_not_provided() -> None:
    response = PostingResponse.from_results([])

    assert response.total_duration_ms == 0
    assert response.platform_durations_ms == {}


async def test_api_response_includes_timings_and_keeps_the_mandated_fields(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    app.state.posting_service = make_service([ScriptedAdapter("A")])

    response = await client.post("/posting", json={"message": "Hello https://example.com"})

    body = response.json()
    assert response.status_code == 200
    assert isinstance(body["total_duration_ms"], int)
    assert set(body["platform_durations_ms"]) == {"A"}
    assert set(body["results"][0]) == RESULT_KEYS