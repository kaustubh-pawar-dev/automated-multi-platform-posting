"""Tests for the multi-platform posting service (all adapters are test doubles)."""

import datetime as dt
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
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
from app.main import create_app
from app.platforms.base import PlatformAdapter, PublishedPost
from app.platforms.registry import PlatformRegistry
from app.schemas.response import PostingResponse
from app.services.message_validation import validate_message
from app.services.posting_service import PostingService
from tests.fakes import ConcurrencyTracker, ScriptedAdapter, ok_post

MESSAGE = validate_message("Hello world https://example.com")


class Sleeper:
    """Records requested backoff delays instead of actually sleeping."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def make_service(
    adapters: list[PlatformAdapter], sleeper: Sleeper | None = None, **overrides: Any
) -> PostingService:
    params: dict[str, Any] = {
        "max_concurrent": 5,
        "max_retries": 2,
        "backoff_base_seconds": 0.5,
        "platform_deadline_seconds": 30.0,
        "sleep": sleeper or Sleeper(),
        "jitter": lambda: 1.0,
    }
    params.update(overrides)
    return PostingService(PlatformRegistry(adapters), **params)


async def test_all_platforms_succeed() -> None:
    adapters = [ScriptedAdapter("A"), ScriptedAdapter("B"), ScriptedAdapter("C")]
    response = await make_service(adapters).publish(MESSAGE)

    assert response.success is True
    assert (response.total_platforms, response.successful_count, response.failed_count) == (3, 3, 0)
    assert [r.platform for r in response.results] == ["A", "B", "C"]
    assert all(r.status == "success" and r.date and r.time for r in response.results)
    assert all(adapter.calls[0][0] == MESSAGE.text for adapter in adapters)
    assert len({adapter.calls[0][1] for adapter in adapters}) == 3


async def test_one_failure_does_not_stop_the_others() -> None:
    a = ScriptedAdapter("A")
    b = ScriptedAdapter("B", [PlatformAuthError()])
    c = ScriptedAdapter("C")
    response = await make_service([a, b, c]).publish(MESSAGE)

    assert response.success is False
    assert (response.successful_count, response.failed_count) == (2, 1)
    assert [r.status for r in response.results] == ["success", "failed", "success"]
    failed = response.results[1]
    assert failed.error == "Authentication failed"
    assert (failed.posted_url, failed.public_access, failed.date, failed.time) == (
        None,
        False,
        None,
        None,
    )
    assert len(a.calls) == 1
    assert len(c.calls) == 1


async def test_all_platforms_fail() -> None:
    adapters = [ScriptedAdapter(n, [PlatformRejectedError()]) for n in ("A", "B")]
    response = await make_service(adapters).publish(MESSAGE)
    assert response.success is False
    assert (response.successful_count, response.failed_count) == (0, 2)


async def test_unexpected_exception_is_isolated_and_sanitised() -> None:
    broken = ScriptedAdapter("Broken", [RuntimeError("token=SECRET123")])
    healthy = ScriptedAdapter("Healthy")
    response = await make_service([broken, healthy]).publish(MESSAGE)

    assert response.results[0].error == "Unexpected error while publishing"
    assert response.results[1].status == "success"
    assert "SECRET123" not in response.model_dump_json()


async def test_unconfigured_platforms_are_skipped_and_reported() -> None:
    idle = ScriptedAdapter("Idle", configured=False)
    ready = ScriptedAdapter("Ready")
    response = await make_service([idle, ready]).publish(MESSAGE)

    assert response.skipped_platforms == ["Idle"]
    assert response.total_platforms == 1
    assert response.success is True
    assert idle.calls == []


async def test_nothing_configured_is_not_a_success() -> None:
    response = await make_service([ScriptedAdapter("Idle", configured=False)]).publish(MESSAGE)
    assert response.success is False
    assert response.total_platforms == 0
    assert response.skipped_platforms == ["Idle"]


async def test_transient_failure_is_retried_with_the_same_key() -> None:
    sleeper = Sleeper()
    adapter = ScriptedAdapter("A", [PlatformUnavailableError(), ok_post()])
    response = await make_service([adapter], sleeper).publish(MESSAGE)

    assert response.results[0].status == "success"
    assert len(adapter.calls) == 2
    assert adapter.calls[0][1] == adapter.calls[1][1]
    assert sleeper.delays == [0.5]


async def test_retries_are_limited_and_backoff_doubles() -> None:
    sleeper = Sleeper()
    adapter = ScriptedAdapter("A", [PlatformUnavailableError()])
    response = await make_service([adapter], sleeper).publish(MESSAGE)

    assert len(adapter.calls) == 3
    assert sleeper.delays == [0.5, 1.0]
    assert response.results[0].error == "Platform temporarily unavailable (after 3 attempts)"


async def test_zero_retries_means_one_attempt() -> None:
    sleeper = Sleeper()
    adapter = ScriptedAdapter("A", [PlatformUnavailableError()])
    response = await make_service([adapter], sleeper, max_retries=0).publish(MESSAGE)

    assert len(adapter.calls) == 1
    assert sleeper.delays == []
    assert response.results[0].error == "Platform temporarily unavailable"


@pytest.mark.parametrize(
    ("error", "idempotent", "expected_calls"),
    [
        (PlatformUnavailableError(), False, 3),
        (PlatformUnavailableError(ambiguous=True), False, 1),
        (PlatformUnavailableError(ambiguous=True), True, 3),
        (PlatformTimeoutError(), False, 3),
        (PlatformTimeoutError(ambiguous=True), False, 1),
        (PlatformRateLimitError(), False, 3),
        (PlatformAuthError(), True, 1),
        (PlatformRejectedError(), True, 1),
        (PlatformResponseError(ambiguous=True), True, 1),
        (PlatformNotConfiguredError(), True, 1),
    ],
    ids=[
        "transient",
        "ambiguous-not-idempotent",
        "ambiguous-idempotent",
        "connect-timeout",
        "read-timeout-not-idempotent",
        "rate-limit",
        "auth",
        "rejected",
        "invalid-response",
        "not-configured",
    ],
)
async def test_retry_decisions(
    error: PlatformError, idempotent: bool, expected_calls: int
) -> None:
    adapter = ScriptedAdapter("A", [error], idempotent=idempotent)
    await make_service([adapter]).publish(MESSAGE)
    assert len(adapter.calls) == expected_calls


async def test_retry_after_longer_than_backoff_is_honoured() -> None:
    sleeper = Sleeper()
    adapter = ScriptedAdapter("A", [PlatformRateLimitError(retry_after=3.0), ok_post()])
    await make_service([adapter], sleeper).publish(MESSAGE)
    assert sleeper.delays == [3.0]


async def test_retry_after_shorter_than_backoff_uses_backoff() -> None:
    sleeper = Sleeper()
    adapter = ScriptedAdapter("A", [PlatformRateLimitError(retry_after=0.1), ok_post()])
    await make_service([adapter], sleeper).publish(MESSAGE)
    assert sleeper.delays == [0.5]


async def test_retry_is_skipped_when_the_wait_exceeds_the_deadline() -> None:
    sleeper = Sleeper()
    adapter = ScriptedAdapter("A", [PlatformRateLimitError(retry_after=5.0), ok_post()])
    response = await make_service([adapter], sleeper, platform_deadline_seconds=1.0).publish(
        MESSAGE
    )
    assert len(adapter.calls) == 1
    assert sleeper.delays == []
    assert response.results[0].error == "Rate limit exceeded"


async def test_backoff_is_capped() -> None:
    sleeper = Sleeper()
    adapter = ScriptedAdapter("A", [PlatformUnavailableError(), ok_post()])
    service = make_service(
        [adapter],
        sleeper,
        max_retries=1,
        backoff_base_seconds=100.0,
        platform_deadline_seconds=1000.0,
    )
    await service.publish(MESSAGE)
    assert sleeper.delays == [10.0]


async def test_jitter_reduces_the_delay() -> None:
    sleeper = Sleeper()
    adapter = ScriptedAdapter("A", [PlatformUnavailableError(), ok_post()])
    await make_service([adapter], sleeper, jitter=lambda: 0.0).publish(MESSAGE)
    assert sleeper.delays == [0.25]


async def test_a_hanging_platform_hits_its_deadline_without_blocking_others() -> None:
    slow = ScriptedAdapter("Slow", delay=5.0)
    fast = ScriptedAdapter("Fast")
    response = await make_service([slow, fast], platform_deadline_seconds=0.05).publish(MESSAGE)

    assert response.results[0].error == "Platform did not respond in time"
    assert response.results[1].status == "success"
    assert response.success is False


async def test_concurrency_is_bounded() -> None:
    tracker = ConcurrencyTracker()
    adapters = [ScriptedAdapter(f"P{i}", delay=0.02, tracker=tracker) for i in range(6)]
    response = await make_service(adapters, max_concurrent=2).publish(MESSAGE)

    assert response.successful_count == 6
    assert tracker.peak == 2


async def test_result_order_follows_registration_not_completion() -> None:
    slow, fast = ScriptedAdapter("Slow", delay=0.05), ScriptedAdapter("Fast")
    response = await make_service([slow, fast]).publish(MESSAGE)
    assert [r.platform for r in response.results] == ["Slow", "Fast"]


async def test_naive_timestamp_from_an_adapter_is_treated_as_utc() -> None:
    naive = PublishedPost(
        posted_url="https://x.example/1",
        public_access=True,
        posted_at=dt.datetime(2026, 11, 15, 10, 30),
    )
    response = await make_service([ScriptedAdapter("A", [naive])]).publish(MESSAGE)
    result = response.results[0]
    assert result.status == "success"
    assert (result.date, result.time) == (dt.date(2026, 11, 15), dt.time(10, 30))


async def test_missing_url_and_private_visibility_are_passed_through() -> None:
    private = PublishedPost(
        posted_url=None, public_access=False, posted_at=dt.datetime.now(dt.UTC)
    )
    response = await make_service([ScriptedAdapter("A", [private])]).publish(MESSAGE)
    result = response.results[0]
    assert result.status == "success"
    assert (result.posted_url, result.public_access) == (None, False)


def test_from_settings_wires_values() -> None:
    settings = Settings(
        _env_file=None,
        max_concurrent_platforms=3,
        max_retries=1,
        http_timeout_seconds=7,
        retry_backoff_base_seconds=0.25,
    )
    service = PostingService.from_settings(PlatformRegistry(), settings)
    assert service.max_concurrent == 3
    assert service.max_retries == 1
    assert service.platform_deadline_seconds == 14.0
    assert service.backoff_base_seconds == 0.25


@pytest.mark.parametrize(
    "override",
    [
        {"max_concurrent": 0},
        {"max_retries": -1},
        {"backoff_base_seconds": 0},
        {"platform_deadline_seconds": 0},
    ],
)
def test_invalid_configuration_is_rejected(override: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="must"):
        make_service([], **override)


def test_response_lists_skipped_platforms_and_defaults_to_none() -> None:
    assert PostingResponse.from_results([]).skipped_platforms == []
    assert PostingResponse.from_results([], skipped=["X"]).skipped_platforms == ["X"]


def test_service_is_created_at_application_startup(test_settings: Settings) -> None:
    app = create_app(test_settings)
    with TestClient(app):
        assert isinstance(app.state.posting_service, PostingService)