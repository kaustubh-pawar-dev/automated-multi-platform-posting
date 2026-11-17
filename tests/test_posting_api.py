"""Tests for POST /posting using httpx.AsyncClient against the ASGI app (no network)."""

import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.core.exceptions import PlatformAuthError
from app.platforms.base import PlatformAdapter
from app.platforms.registry import PlatformRegistry
from app.services.posting_service import PostingService
from tests.fakes import ScriptedAdapter

URL = "https://example.com"
VALID = f"Check out our latest business platform and grow your online visibility. {URL}"
RESULT_KEYS = {"platform", "status", "posted_url", "public_access", "date", "time", "error"}
JSON_HEADERS = {"Content-Type": "application/json"}


def install_service(app: FastAPI, adapters: list[PlatformAdapter]) -> None:
    """Give the app a posting service wired to test doubles (the lifespan is not run)."""
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


async def test_valid_request_publishes_everywhere(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    alpha, bravo = ScriptedAdapter("Alpha"), ScriptedAdapter("Bravo")
    install_service(app, [alpha, bravo])

    response = await client.post("/posting", json={"message": VALID})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["success"] is True
    assert (body["total_platforms"], body["successful_count"], body["failed_count"]) == (2, 2, 0)
    assert body["skipped_platforms"] == []
    for result in body["results"]:
        assert set(result) == RESULT_KEYS
        assert result["status"] == "success"
        assert re.fullmatch(r"\d{4}-\d\d-\d\d", result["date"])
        assert re.fullmatch(r"\d\d:\d\d:\d\d", result["time"])
        assert result["error"] is None
    assert alpha.calls[0][0] == VALID
    assert bravo.calls[0][0] == VALID


async def test_partial_failure_is_reported_per_platform(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    install_service(
        app, [ScriptedAdapter("Alpha"), ScriptedAdapter("Bravo", [PlatformAuthError()])]
    )

    response = await client.post("/posting", json={"message": VALID})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert (body["successful_count"], body["failed_count"]) == (1, 1)
    failed = body["results"][1]
    assert set(failed) == RESULT_KEYS
    assert failed == {
        "platform": "Bravo",
        "status": "failed",
        "posted_url": None,
        "public_access": False,
        "date": None,
        "time": None,
        "error": "Authentication failed",
    }


async def test_all_platforms_failing_still_returns_the_full_report(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    install_service(app, [ScriptedAdapter(n, [PlatformAuthError()]) for n in ("A", "B")])

    response = await client.post("/posting", json={"message": VALID})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert (body["successful_count"], body["failed_count"]) == (0, 2)


async def test_unconfigured_platforms_are_listed_as_skipped(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    install_service(app, [ScriptedAdapter("Ready"), ScriptedAdapter("Idle", configured=False)])

    body = (await client.post("/posting", json={"message": VALID})).json()

    assert body["skipped_platforms"] == ["Idle"]
    assert body["total_platforms"] == 1
    assert body["success"] is True


async def test_no_configured_platform_returns_503(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    install_service(
        app,
        [ScriptedAdapter("A", configured=False), ScriptedAdapter("B", configured=False)],
    )

    response = await client.post("/posting", json={"message": VALID})

    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "no_platforms_configured"
    assert [d["code"] for d in body["details"]] == ["PLATFORM_NOT_CONFIGURED"] * 2
    messages = [d["message"] for d in body["details"]]
    assert messages == ["A is not configured", "B is not configured"]


async def test_empty_registry_returns_503(app: FastAPI, client: httpx.AsyncClient) -> None:
    install_service(app, [])

    response = await client.post("/posting", json={"message": VALID})

    assert response.status_code == 503
    assert response.json()["details"] == []


@pytest.mark.parametrize(
    ("message", "codes"),
    [
        ("", ["EMPTY_MESSAGE"]),
        ("hello world", ["MISSING_CTA_URL"]),
        ("a https://a.com b https://b.com", ["MULTIPLE_CTA_URLS"]),
        ("click javascript:alert(1)", ["UNSUPPORTED_URL_SCHEME"]),
        ("a" * 195 + f" {URL}", ["MESSAGE_TOO_LONG"]),
        ("visit http://localhost:3000", ["URL_HOST_NOT_PUBLIC"]),
    ],
    ids=["empty", "no-link", "two-links", "bad-scheme", "too-long", "localhost"],
)
async def test_business_rule_violations_return_400_and_publish_nothing(
    app: FastAPI, client: httpx.AsyncClient, message: str, codes: list[str]
) -> None:
    adapter = ScriptedAdapter("A")
    install_service(app, [adapter])

    response = await client.post("/posting", json={"message": message})

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "validation_error"
    assert [detail["code"] for detail in body["details"]] == codes
    assert adapter.calls == []


async def test_exactly_200_characters_is_accepted(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    install_service(app, [ScriptedAdapter("A")])
    message = "x" * (200 - len(URL) - 1) + " " + URL
    assert len(message) == 200

    response = await client.post("/posting", json={"message": message})

    assert response.status_code == 200


async def test_surrounding_whitespace_is_trimmed_before_publishing(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    adapter = ScriptedAdapter("A")
    install_service(app, [adapter])

    response = await client.post("/posting", json={"message": f"  {VALID}\n"})

    assert response.status_code == 200
    assert adapter.calls[0][0] == VALID


async def test_clients_cannot_choose_platforms(app: FastAPI, client: httpx.AsyncClient) -> None:
    adapter = ScriptedAdapter("A")
    install_service(app, [adapter])

    response = await client.post("/posting", json={"message": VALID, "platforms": ["A"]})

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "request_validation_error"
    assert body["details"] == [
        {"code": "UNKNOWN_FIELD", "message": "platforms: Extra inputs are not permitted"}
    ]
    assert adapter.calls == []


async def test_missing_message_field_message_format(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    install_service(app, [ScriptedAdapter("A")])

    response = await client.post("/posting", json={})

    assert response.status_code == 422
    assert response.json()["details"] == [
        {"code": "FIELD_REQUIRED", "message": "message: Field required"}
    ]


@pytest.mark.parametrize(
    ("request_kwargs", "code"),
    [
        ({"json": {}}, "FIELD_REQUIRED"),
        ({"json": {"message": 123}}, "INVALID_TYPE"),
        ({"json": []}, None),
        ({"content": b"{not json", "headers": JSON_HEADERS}, "INVALID_JSON"),
        ({"headers": JSON_HEADERS}, None),
        ({"json": {"message": "a" * 10_001}}, "VALUE_TOO_LONG"),
    ],
    ids=["no-field", "wrong-type", "list-body", "invalid-json", "no-body", "huge-message"],
)
async def test_malformed_requests_return_422(
    app: FastAPI,
    client: httpx.AsyncClient,
    request_kwargs: dict[str, Any],
    code: str | None,
) -> None:
    adapter = ScriptedAdapter("A")
    install_service(app, [adapter])

    response = await client.post("/posting", **request_kwargs)

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "request_validation_error"
    assert set(body) == {"error", "message", "details", "request_id"}
    assert body["details"]
    if code is not None:
        assert body["details"][0]["code"] == code
    assert adapter.calls == []


async def test_422_responses_never_echo_submitted_input(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    install_service(app, [ScriptedAdapter("A")])

    response = await client.post(
        "/posting", json={"message": VALID, "apikey": "SECRETVALUE-123"}
    )

    assert response.status_code == 422
    assert "SECRETVALUE" not in response.text


async def test_unexpected_internal_error_returns_generic_500(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    class ExplodingService:
        async def publish(self, message: object) -> None:
            raise RuntimeError("SECRET-INTERNAL-DETAIL")

    app.state.posting_service = ExplodingService()

    response = await client.post("/posting", json={"message": VALID})

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "internal_error"
    assert body["message"] == "An unexpected error occurred."
    assert body["details"] == []
    assert body["request_id"] == response.headers["x-request-id"]
    assert "SECRET-INTERNAL-DETAIL" not in response.text
    assert "Traceback" not in response.text


async def test_a_crashing_adapter_does_not_leak_its_error_through_the_api(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    install_service(
        app,
        [ScriptedAdapter("Broken", [RuntimeError("token=SECRET")]), ScriptedAdapter("Fine")],
    )

    response = await client.post("/posting", json={"message": VALID})

    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["error"] == "Unexpected error while publishing"
    assert body["results"][1]["status"] == "success"
    assert "SECRET" not in response.text


async def test_unknown_path_returns_structured_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/nope")

    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
    assert set(response.json()) == {"error", "message", "details", "request_id"}


async def test_wrong_method_returns_structured_405(client: httpx.AsyncClient) -> None:
    response = await client.get("/posting")

    assert response.status_code == 405
    assert response.json()["error"] == "method_not_allowed"
    assert "POST" in response.headers["allow"]


async def test_openapi_documents_the_endpoint(client: httpx.AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()

    assert set(schema["paths"]["/posting"]) == {"post"}
    operation = schema["paths"]["/posting"]["post"]
    assert {"200", "400", "422", "500", "503"} <= set(operation["responses"])
    assert operation["responses"]["422"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("ErrorResponse")
    assert "examples" in operation["responses"]["200"]["content"]["application/json"]

    components = schema["components"]["schemas"]
    assert {"PostingRequest", "PostingResponse", "PlatformResult", "ErrorResponse"} <= set(
        components
    )
    assert set(components["PostingRequest"]["properties"]) == {"message"}
    assert components["PostingRequest"]["required"] == ["message"]


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
async def test_documentation_pages_load(client: httpx.AsyncClient, path: str) -> None:
    assert (await client.get(path)).status_code == 200