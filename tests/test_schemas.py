"""Tests for request and response schemas."""

import datetime as dt

import pytest
from pydantic import ValidationError

from app.schemas.request import PostingRequest
from app.schemas.response import PlatformResult, PostingResponse

POSTED_AT = dt.datetime(2026, 11, 15, 10, 30, 0, 123456, tzinfo=dt.UTC)


def _ok(name: str = "A") -> PlatformResult:
    return PlatformResult.success(
        name, posted_url="https://x.example/1", public_access=True, posted_at=POSTED_AT
    )


def _fail(name: str = "B") -> PlatformResult:
    return PlatformResult.failure(name, "Authentication failed")


def test_success_result_fields() -> None:
    result = _ok("Mastodon")
    assert result.status == "success"
    assert result.date == dt.date(2026, 11, 15)
    assert result.time == dt.time(10, 30, 0)
    assert result.error is None

    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    local = PlatformResult.success(
        "Local",
        posted_url=None,
        public_access=False,
        posted_at=dt.datetime(2026, 11, 15, 16, 0, tzinfo=ist),
    )
    assert local.time == dt.time(10, 30)


def test_failed_result_fields() -> None:
    result = _fail()
    assert result.status == "failed"
    assert result.posted_url is None
    assert result.public_access is False
    assert result.date is None
    assert result.time is None
    assert result.error == "Authentication failed"


def test_success_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        PlatformResult.success(
            "X",
            posted_url=None,
            public_access=False,
            posted_at=dt.datetime(2026, 11, 15, 10, 30),
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "status": "success",
            "error": "boom",
            "date": dt.date(2026, 11, 15),
            "time": dt.time(10, 30),
        },
        {"status": "success"},
        {"status": "failed"},
        {"status": "failed", "error": "boom", "posted_url": "https://x.example/1"},
    ],
    ids=["success-with-error", "success-without-time", "failed-without-error", "failed-with-url"],
)
def test_inconsistent_results_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PlatformResult(platform="X", public_access=False, **kwargs)


def test_json_serialization_format() -> None:
    assert _ok("Mastodon").model_dump(mode="json") == {
        "platform": "Mastodon",
        "status": "success",
        "posted_url": "https://x.example/1",
        "public_access": True,
        "date": "2026-11-15",
        "time": "10:30:00",
        "error": None,
    }


def test_aggregate_all_success() -> None:
    response = PostingResponse.from_results([_ok("A"), _ok("B")])
    assert response.success is True
    assert (response.total_platforms, response.successful_count, response.failed_count) == (2, 2, 0)


def test_aggregate_partial_failure_is_not_overall_success() -> None:
    response = PostingResponse.from_results([_ok("A"), _fail("B")])
    assert response.success is False
    assert (response.total_platforms, response.successful_count, response.failed_count) == (2, 1, 1)


def test_aggregate_empty_is_not_success() -> None:
    response = PostingResponse.from_results([])
    assert response.success is False
    assert response.total_platforms == 0


@pytest.mark.parametrize(
    "payload",
    [{}, {"message": "hello", "platforms": ["Mastodon"]}],
    ids=["missing-message", "client-cannot-select-platforms"],
)
def test_request_rejects_invalid_payloads(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PostingRequest(**payload)