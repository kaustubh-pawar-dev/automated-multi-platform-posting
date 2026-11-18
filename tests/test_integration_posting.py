"""End-to-end tests: POST /posting through the real app, registry and all five real adapters.

Only the outbound HTTP layer is mocked (respx). Nothing here is published anywhere.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from respx import MockRouter

from app.core.config import Settings
from app.main import create_app
from app.platforms.linkedin import escape_little_text

MESSAGE = "Grow your online visibility today. https://example.com/landing?ref=test"
MASTODON = "https://mastodon.test"
BSKY = "https://bsky.test"
TG_TOKEN = "123456:ABCdefGHIjklMNOpqrsTUVwxyz0123456789"
WEBHOOK_TOKEN = "abcdefghijklmnopqrstuvwxyz0123456789"
WEBHOOK = f"https://discord.com/api/webhooks/123456789012345678/{WEBHOOK_TOKEN}"
GUILD = "111111111111111111"
CHANNEL = "987654321098765432"
DISCORD_MESSAGE_ID = "555555555555555555"
LINKEDIN_URN = "urn:li:share:7300000000000000000"
SECRETS = [
    "mastodon-token-value",
    "bluesky-password-value",
    TG_TOKEN,
    "linkedin-token-value",
    WEBHOOK_TOKEN,
]
PLATFORMS = ["Mastodon", "Bluesky", "Telegram", "LinkedIn", "Discord"]
RESULT_KEYS = {"platform", "status", "posted_url", "public_access", "date", "time", "error"}


def all_platform_settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="testing",
        max_retries=2,
        retry_backoff_base_seconds=0.01,
        mastodon_instance_url=MASTODON,
        mastodon_access_token=SECRETS[0],
        bluesky_service_url=BSKY,
        bluesky_handle="user.bsky.test",
        bluesky_app_password=SECRETS[1],
        telegram_bot_token=TG_TOKEN,
        telegram_chat_id="@my_channel",
        linkedin_access_token=SECRETS[3],
        linkedin_person_urn="urn:li:person:AbC_123",
        discord_webhook_url=WEBHOOK,
        discord_guild_id=GUILD,
    )


@asynccontextmanager
async def running_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


def mastodon_ok() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "url": "https://mastodon.test/@bot/110",
            "visibility": "public",
            "created_at": "2026-11-15T10:30:00.123Z",
        },
    )


def bluesky_session_ok() -> httpx.Response:
    session = {"accessJwt": "access-1", "did": "did:plc:abc123", "handle": "user.bsky.test"}
    return httpx.Response(200, json=session)


def bluesky_record_ok() -> httpx.Response:
    return httpx.Response(200, json={"uri": "at://did:plc:abc123/app.bsky.feed.post/3kabc"})


def telegram_ok() -> httpx.Response:
    chat = {"id": -1001234567890, "username": "my_channel", "type": "channel"}
    result = {"message_id": 42, "date": 1_795_000_000, "chat": chat}
    return httpx.Response(200, json={"ok": True, "result": result})


def linkedin_ok() -> httpx.Response:
    return httpx.Response(201, headers={"x-restli-id": LINKEDIN_URN})


def discord_ok() -> httpx.Response:
    body = {
        "id": DISCORD_MESSAGE_ID,
        "channel_id": CHANNEL,
        "timestamp": "2026-11-15T10:30:02.000000+00:00",
    }
    return httpx.Response(200, json=body)


def mock_platforms(router: MockRouter, *, bluesky_record: bool = True) -> dict[str, Any]:
    """Register a success response for every platform; returns the routes by name."""
    routes: dict[str, Any] = {
        "mastodon": router.post(f"{MASTODON}/api/v1/statuses").mock(return_value=mastodon_ok()),
        "bsky_session": router.post(f"{BSKY}/xrpc/com.atproto.server.createSession").mock(
            return_value=bluesky_session_ok()
        ),
        "telegram": router.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage").mock(
            return_value=telegram_ok()
        ),
        "linkedin": router.post("https://api.linkedin.com/rest/posts").mock(
            return_value=linkedin_ok()
        ),
        "discord": router.post(WEBHOOK, params={"wait": "true"}).mock(
            return_value=discord_ok()
        ),
    }
    if bluesky_record:
        routes["bsky_record"] = router.post(f"{BSKY}/xrpc/com.atproto.repo.createRecord").mock(
            return_value=bluesky_record_ok()
        )
    return routes


def by_platform(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {result["platform"]: result for result in body["results"]}


async def test_all_five_platforms_publish_through_the_real_stack(respx_mock: MockRouter) -> None:
    routes = mock_platforms(respx_mock)

    async with running_client(all_platform_settings()) as client:
        response = await client.post("/posting", json={"message": MESSAGE})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert (body["total_platforms"], body["successful_count"], body["failed_count"]) == (5, 5, 0)
    assert body["skipped_platforms"] == []
    assert [result["platform"] for result in body["results"]] == PLATFORMS
    assert all(set(result) == RESULT_KEYS for result in body["results"])
    assert all(result["status"] == "success" for result in body["results"])

    results = by_platform(body)
    assert results["Mastodon"]["posted_url"] == "https://mastodon.test/@bot/110"
    assert (results["Mastodon"]["date"], results["Mastodon"]["time"]) == ("2026-11-15", "10:30:00")
    assert results["Bluesky"]["posted_url"] == "https://bsky.app/profile/user.bsky.test/post/3kabc"
    assert results["Telegram"]["posted_url"] == "https://t.me/my_channel/42"
    assert results["LinkedIn"]["posted_url"] == f"https://www.linkedin.com/feed/update/{LINKEDIN_URN}/"
    assert results["Discord"]["posted_url"] == (
        f"https://discord.com/channels/{GUILD}/{CHANNEL}/{DISCORD_MESSAGE_ID}"
    )
    assert {name: r["public_access"] for name, r in results.items()} == {
        "Mastodon": True,
        "Bluesky": True,
        "Telegram": True,
        "LinkedIn": True,
        "Discord": False,
    }

    sent = {
        "mastodon": json.loads(routes["mastodon"].calls.last.request.content)["status"],
        "bluesky": json.loads(routes["bsky_record"].calls.last.request.content)["record"]["text"],
        "telegram": json.loads(routes["telegram"].calls.last.request.content)["text"],
        "discord": json.loads(routes["discord"].calls.last.request.content)["content"],
    }
    assert set(sent.values()) == {MESSAGE}
    linkedin_body = json.loads(routes["linkedin"].calls.last.request.content)
    assert linkedin_body["commentary"] == escape_little_text(MESSAGE)


async def test_partial_failure_retries_only_where_it_is_safe(respx_mock: MockRouter) -> None:
    routes = mock_platforms(respx_mock)
    routes["linkedin"].mock(return_value=httpx.Response(401, json={"message": "bad token"}))
    routes["telegram"].mock(return_value=httpx.Response(500, json={"ok": False}))
    routes["mastodon"].mock(side_effect=[httpx.Response(503), mastodon_ok()])
    routes["discord"].mock(
        side_effect=[httpx.Response(429, json={"retry_after": 0.01}), discord_ok()]
    )

    async with running_client(all_platform_settings()) as client:
        response = await client.post("/posting", json={"message": MESSAGE})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert (body["total_platforms"], body["successful_count"], body["failed_count"]) == (5, 3, 2)
    results = by_platform(body)

    assert results["LinkedIn"]["status"] == "failed"
    assert results["LinkedIn"]["error"] == "Authentication failed"
    assert routes["linkedin"].call_count == 1

    assert results["Telegram"]["status"] == "failed"
    assert results["Telegram"]["error"] == "Platform temporarily unavailable (HTTP 500)"
    assert routes["telegram"].call_count == 1  # ambiguous and not idempotent: never retried

    assert results["Mastodon"]["status"] == "success"
    assert routes["mastodon"].call_count == 2
    keys = [call.request.headers["Idempotency-Key"] for call in routes["mastodon"].calls]
    assert keys[0] == keys[1]

    assert results["Discord"]["status"] == "success"
    assert routes["discord"].call_count == 2
    assert results["Bluesky"]["status"] == "success"

    for secret in SECRETS:
        assert secret not in response.text


async def test_a_platform_that_keeps_timing_out_does_not_affect_the_others(
    respx_mock: MockRouter,
) -> None:
    routes = mock_platforms(respx_mock)
    routes["mastodon"].mock(side_effect=httpx.ReadTimeout("slow"))

    async with running_client(all_platform_settings()) as client:
        response = await client.post("/posting", json={"message": MESSAGE})

    body = response.json()
    assert response.status_code == 200
    assert (body["successful_count"], body["failed_count"]) == (4, 1)
    mastodon = by_platform(body)["Mastodon"]
    assert mastodon["status"] == "failed"
    assert mastodon["error"] == "Request to the platform timed out (after 3 attempts)"
    keys = {call.request.headers["Idempotency-Key"] for call in routes["mastodon"].calls}
    assert routes["mastodon"].call_count == 3
    assert len(keys) == 1


async def test_an_invalid_platform_response_is_reported_and_never_retried(
    respx_mock: MockRouter,
) -> None:
    routes = mock_platforms(respx_mock)
    routes["bsky_record"].mock(return_value=httpx.Response(200, json={}))

    async with running_client(all_platform_settings()) as client:
        response = await client.post("/posting", json={"message": MESSAGE})

    body = response.json()
    assert (body["successful_count"], body["failed_count"]) == (4, 1)
    assert by_platform(body)["Bluesky"]["error"] == "Invalid response from platform"
    assert routes["bsky_record"].call_count == 1
    assert routes["bsky_session"].call_count == 1


async def test_every_platform_failing_still_returns_the_full_report(
    respx_mock: MockRouter,
) -> None:
    routes = mock_platforms(respx_mock, bluesky_record=False)
    for route in routes.values():
        route.mock(return_value=httpx.Response(401, json={"message": "denied"}))

    async with running_client(all_platform_settings()) as client:
        response = await client.post("/posting", json={"message": MESSAGE})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert (body["successful_count"], body["failed_count"]) == (0, 5)
    assert all(result["error"] == "Authentication failed" for result in body["results"])
    assert all(route.call_count == 1 for route in routes.values())


async def test_a_server_without_credentials_reports_503(respx_mock: MockRouter) -> None:
    settings = Settings(_env_file=None, environment="testing")

    async with running_client(settings) as client:
        response = await client.post("/posting", json={"message": MESSAGE})

    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "no_platforms_configured"
    assert [detail["message"] for detail in body["details"]] == [
        f"{name} is not configured" for name in PLATFORMS
    ]
    assert len(respx_mock.calls) == 0