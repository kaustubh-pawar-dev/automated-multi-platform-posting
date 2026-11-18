"""Tests for the operational endpoints: GET /health and GET /platforms."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from respx import MockRouter

from app.core.config import Settings
from app.main import create_app

TG_TOKEN = "123456:ABCdefGHIjklMNOpqrsTUVwxyz0123456789"
WEBHOOK_TOKEN = "abcdefghijklmnopqrstuvwxyz0123456789"
WEBHOOK = f"https://discord.com/api/webhooks/123456789012345678/{WEBHOOK_TOKEN}"
PLATFORMS = ["Mastodon", "Bluesky", "Telegram", "LinkedIn", "Discord"]
EXPECTED_MISSING = {
    "Mastodon": ["MASTODON_INSTANCE_URL", "MASTODON_ACCESS_TOKEN"],
    "Bluesky": ["BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"],
    "Telegram": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
    "LinkedIn": ["LINKEDIN_ACCESS_TOKEN", "LINKEDIN_PERSON_URN"],
    "Discord": ["DISCORD_WEBHOOK_URL"],
}


@asynccontextmanager
async def running_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def test_health_reports_ok(client: httpx.AsyncClient, test_settings: Settings) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "version", "uptime_seconds"}
    assert body["status"] == "ok"
    assert body["version"] == test_settings.app_version
    assert body["uptime_seconds"] >= 0
    assert response.headers["x-request-id"]


async def test_health_needs_no_credentials_and_makes_no_outbound_requests(
    client: httpx.AsyncClient, respx_mock: MockRouter
) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert len(respx_mock.calls) == 0


async def test_uptime_never_decreases(client: httpx.AsyncClient) -> None:
    first = (await client.get("/health")).json()["uptime_seconds"]
    second = (await client.get("/health")).json()["uptime_seconds"]

    assert second >= first


async def test_health_rejects_other_methods(client: httpx.AsyncClient) -> None:
    response = await client.post("/health")

    assert response.status_code == 405
    assert response.json()["error"] == "method_not_allowed"


async def test_platforms_report_without_credentials(test_settings: Settings) -> None:
    async with running_client(test_settings) as client:
        response = await client.get("/platforms")

    assert response.status_code == 200
    body = response.json()
    assert [p["name"] for p in body["platforms"]] == PLATFORMS
    assert all(p["configured"] is False for p in body["platforms"])
    assert {p["name"]: p["missing_settings"] for p in body["platforms"]} == EXPECTED_MISSING
    assert (body["configured_count"], body["total_count"]) == (0, 5)
    assert body["disabled_platforms"] == []


async def test_platforms_report_configured_platforms_without_leaking_secrets() -> None:
    settings = Settings(
        _env_file=None,
        environment="testing",
        telegram_bot_token=TG_TOKEN,
        telegram_chat_id="@my_channel",
        discord_webhook_url=WEBHOOK,
        linkedin_access_token="linkedin-token-value",
    )

    async with running_client(settings) as client:
        response = await client.get("/platforms")

    body = response.json()
    by_name = {platform["name"]: platform for platform in body["platforms"]}
    assert by_name["Telegram"]["configured"] is True
    assert by_name["Telegram"]["missing_settings"] == []
    assert by_name["Discord"]["configured"] is True
    assert by_name["Discord"]["missing_settings"] == []
    assert by_name["LinkedIn"]["configured"] is False
    assert by_name["LinkedIn"]["missing_settings"] == ["LINKEDIN_PERSON_URN"]
    assert body["configured_count"] == 2
    for secret in (TG_TOKEN, WEBHOOK_TOKEN, "linkedin-token-value"):
        assert secret not in response.text


async def test_platforms_report_which_platforms_support_idempotent_retries(
    test_settings: Settings,
) -> None:
    async with running_client(test_settings) as client:
        body = (await client.get("/platforms")).json()

    flags = {platform["name"]: platform["idempotent_retries"] for platform in body["platforms"]}
    assert flags == {
        "Mastodon": True,
        "Bluesky": False,
        "Telegram": False,
        "LinkedIn": False,
        "Discord": False,
    }


async def test_disabled_platforms_are_left_out_and_reported() -> None:
    settings = Settings(
        _env_file=None, environment="testing", disabled_platforms=["linkedin", "Telegram"]
    )

    async with running_client(settings) as client:
        body = (await client.get("/platforms")).json()

    assert [p["name"] for p in body["platforms"]] == ["Mastodon", "Bluesky", "Discord"]
    assert body["total_count"] == 3
    assert body["disabled_platforms"] == ["linkedin", "Telegram"]


async def test_openapi_documents_the_operational_endpoints(client: httpx.AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]

    assert set(paths["/health"]) == {"get"}
    assert set(paths["/platforms"]) == {"get"}
    assert set(paths["/posting"]) == {"post"}