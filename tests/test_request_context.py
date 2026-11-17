"""Tests for request ids, request logging and the request-size limit."""

import re
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from app.core.logging import configure_logging
from app.platforms.base import PlatformAdapter
from app.platforms.registry import PlatformRegistry
from app.services.posting_service import PostingService
from tests.fakes import ScriptedAdapter

VALID = "Check out our latest business platform and grow your online visibility. https://example.com"
GENERATED_ID = re.compile(r"[0-9a-f]{32}")
JSON_HEADERS = {"Content-Type": "application/json"}


def install_service(app: FastAPI, adapters: list[PlatformAdapter]) -> None:
    app.state.posting_service = PostingService(
        PlatformRegistry(adapters),
        max_concurrent=5,
        max_retries=0,
        backoff_base_seconds=0.01,
        platform_deadline_seconds=5.0,
    )


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def test_every_response_carries_a_generated_request_id(client: httpx.AsyncClient) -> None:
    response = await client.get("/nope")

    request_id = response.headers["x-request-id"]
    assert GENERATED_ID.fullmatch(request_id)
    assert response.json()["request_id"] == request_id


async def test_a_valid_client_request_id_is_reused(client: httpx.AsyncClient) -> None:
    response = await client.get("/nope", headers={"X-Request-ID": "client-req-12345"})

    assert response.headers["x-request-id"] == "client-req-12345"
    assert response.json()["request_id"] == "client-req-12345"


@pytest.mark.parametrize(
    "candidate", ["short", "has spaces in it", "x" * 65, "bad/char/here"]
)
async def test_unsafe_client_request_ids_are_replaced(
    client: httpx.AsyncClient, candidate: str
) -> None:
    response = await client.get("/nope", headers={"X-Request-ID": candidate})

    assert response.headers["x-request-id"] != candidate
    assert GENERATED_ID.fullmatch(response.headers["x-request-id"])


async def test_successful_requests_get_distinct_ids(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    install_service(app, [ScriptedAdapter("A")])

    first = await client.post("/posting", json={"message": VALID})
    second = await client.post("/posting", json={"message": VALID})

    assert first.status_code == second.status_code == 200
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


async def test_validation_errors_report_the_request_id(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    install_service(app, [ScriptedAdapter("A")])

    response = await client.post("/posting", json={"message": "no link"})

    assert response.status_code == 400
    assert response.json()["request_id"] == response.headers["x-request-id"]


async def test_no_platform_error_reports_the_request_id(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    install_service(app, [ScriptedAdapter("A", configured=False)])

    response = await client.post("/posting", json={"message": VALID})

    assert response.status_code == 503
    assert response.json()["request_id"] == response.headers["x-request-id"]


async def test_oversized_bodies_are_rejected_before_processing(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    adapter = ScriptedAdapter("A")
    install_service(app, [adapter])

    response = await client.post("/posting", content=b"x" * 70_000, headers=JSON_HEADERS)

    assert response.status_code == 413
    body = response.json()
    assert body["error"] == "payload_too_large"
    assert body["request_id"] == response.headers["x-request-id"]
    assert adapter.calls == []


async def test_request_lines_share_the_response_request_id(
    client: httpx.AsyncClient, log_stream: object
) -> None:
    configure_logging("INFO", "text", stream=log_stream)  # type: ignore[arg-type]

    response = await client.get("/nope")

    request_id = response.headers["x-request-id"]
    lines = log_stream.getvalue().splitlines()  # type: ignore[attr-defined]
    received = [line for line in lines if "request_received" in line]
    completed = [line for line in lines if "request_completed" in line]
    assert len(received) == len(completed) == 1
    assert f"[{request_id}]" in received[0]
    assert f"[{request_id}]" in completed[0]
    assert "status=404" in completed[0]
    assert "duration_ms=" in completed[0]


async def test_service_logs_carry_the_request_id_across_tasks(
    app: FastAPI, client: httpx.AsyncClient, log_stream: object
) -> None:
    install_service(app, [ScriptedAdapter("A"), ScriptedAdapter("B")])
    configure_logging("INFO", "text", stream=log_stream)  # type: ignore[arg-type]

    response = await client.post("/posting", json={"message": VALID})

    request_id = response.headers["x-request-id"]
    lines = log_stream.getvalue().splitlines()  # type: ignore[attr-defined]
    service_lines = [
        line
        for line in lines
        if any(key in line for key in ("posting_started", "platform_attempt", "posting_finished"))
    ]
    assert len(service_lines) >= 4
    assert all(f"[{request_id}]" in line for line in service_lines)


async def test_message_text_is_never_logged(
    app: FastAPI, client: httpx.AsyncClient, log_stream: object
) -> None:
    install_service(app, [ScriptedAdapter("A")])
    configure_logging("DEBUG", "text", stream=log_stream)  # type: ignore[arg-type]

    await client.post("/posting", json={"message": VALID})

    assert "Check out our latest" not in log_stream.getvalue()  # type: ignore[attr-defined]


async def test_query_strings_are_never_logged(
    client: httpx.AsyncClient, log_stream: object
) -> None:
    configure_logging("DEBUG", "text", stream=log_stream)  # type: ignore[arg-type]

    await client.get("/nope?token=SECRETQUERYVALUE")

    assert "SECRETQUERYVALUE" not in log_stream.getvalue()  # type: ignore[attr-defined]


async def test_newlines_in_paths_cannot_forge_log_lines(
    client: httpx.AsyncClient, log_stream: object
) -> None:
    configure_logging("INFO", "text", stream=log_stream)  # type: ignore[arg-type]

    await client.get("/a%0AFORGED-LINE")

    lines = log_stream.getvalue().splitlines()  # type: ignore[attr-defined]
    assert not any(line.startswith("FORGED-LINE") for line in lines)
    assert any("FORGED-LINE" in line and "request_received" in line for line in lines)