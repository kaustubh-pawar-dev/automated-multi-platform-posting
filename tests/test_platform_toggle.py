"""Tests for DISABLED_PLATFORMS and for the diagnostics settings map."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from respx import MockRouter

from app.core.config import Settings
from app.main import create_app
from app.platforms.registry import build_registry
from app.services.diagnostics import PLATFORM_SETTINGS

TG_TOKEN = "123456:ABCdefGHIjklMNOpqrsTUVwxyz0123456789"
WEBHOOK_TOKEN = "abcdefghijklmnopqrstuvwxyz0123456789"
WEBHOOK = f"https://discord.com/api/webhooks/123456789012345678/{WEBHOOK_TOKEN}"
MESSAGE = "Grow your online visibility today. https://example.com/landing"


@asynccontextmanager
async def running_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


def test_disabled_platforms_default_to_none() -> None:
    assert Settings(_env_file=None).disabled_platforms == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("LinkedIn,Telegram", ["LinkedIn", "Telegram"]),
        (" LinkedIn , , Telegram ", ["LinkedIn", "Telegram"]),
        ("Discord", ["Discord"]),
        ("", []),
        ("   ", []),
    ],
    ids=["two", "messy-spacing", "one", "empty", "blank"],
)
def test_disabled_platforms_parse_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    monkeypatch.setenv("DISABLED_PLATFORMS", raw)

    assert Settings(_env_file=None).disabled_platforms == expected


def test_disabled_platforms_accept_a_list() -> None:
    settings = Settings(_env_file=None, disabled_platforms=["LinkedIn"])

    assert settings.disabled_platforms == ["LinkedIn"]


def test_registry_leaves_out_disabled_platforms_case_insensitively(
    http_client: httpx.AsyncClient,
) -> None:
    settings = Settings(_env_file=None, disabled_platforms=["linkedin", "DISCORD"])

    registry = build_registry(http_client, settings)

    assert registry.names() == ["Mastodon", "Bluesky", "Telegram"]


def test_unknown_platform_names_fail_with_a_clear_message(http_client: httpx.AsyncClient) -> None:
    settings = Settings(_env_file=None, disabled_platforms=["Facebook", "LinkedIn"])

    with pytest.raises(ValueError, match="unknown platform") as exc_info:
        build_registry(http_client, settings)

    assert "facebook" in str(exc_info.value)
    assert "Mastodon" in str(exc_info.value)


async def test_application_startup_fails_on_an_unknown_platform_name() -> None:
    app = create_app(Settings(_env_file=None, environment="testing", disabled_platforms=["Nope"]))

    with pytest.raises(ValueError, match="unknown platform"):
        async with app.router.lifespan_context(app):
            pass


async def test_disabled_platforms_are_never_contacted(respx_mock: MockRouter) -> None:
    settings = Settings(
        _env_file=None,
        environment="testing",
        mastodon_instance_url="https://mastodon.test",
        mastodon_access_token="mastodon-token-value",
        bluesky_handle="user.bsky.test",
        bluesky_app_password="bluesky-password-value",
        telegram_bot_token=TG_TOKEN,
        telegram_chat_id="@my_channel",
        linkedin_access_token="linkedin-token-value",
        linkedin_person_urn="urn:li:person:AbC_123",
        discord_webhook_url=WEBHOOK,
        disabled_platforms=["mastodon", "Bluesky", "TELEGRAM", "linkedin"],
    )
    reply = {"id": "555555555555555555", "channel_id": "987654321098765432"}
    discord = respx_mock.post(WEBHOOK, params={"wait": "true"}).mock(
        return_value=httpx.Response(200, json=reply)
    )

    async with running_client(settings) as client:
        response = await client.post("/posting", json={"message": MESSAGE})

    assert response.status_code == 200
    body = response.json()
    assert body["total_platforms"] == 1
    assert [result["platform"] for result in body["results"]] == ["Discord"]
    assert body["skipped_platforms"] == []
    assert discord.call_count == 1
    assert len(respx_mock.calls) == 1


def test_every_platform_has_a_diagnostics_settings_entry(http_client: httpx.AsyncClient) -> None:
    registry = build_registry(http_client, Settings(_env_file=None))

    assert set(registry.names()) == set(PLATFORM_SETTINGS)
    for fields in PLATFORM_SETTINGS.values():
        assert all(field in Settings.model_fields for field in fields)