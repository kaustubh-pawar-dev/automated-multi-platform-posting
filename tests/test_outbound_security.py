"""Outbound request security: only HTTPS, only each platform's own host, never the CTA link."""

import httpx
import pytest
from respx import MockRouter

from app.core.config import Settings
from app.core.exceptions import PlatformError
from app.platforms.registry import PlatformRegistry, build_registry

CTA_HOST = "cta.example"
MESSAGE = f"Grow your visibility https://{CTA_HOST}/landing?x=1"
TELEGRAM_TOKEN = "123456:ABCdefGHIjklMNOpqrsTUVwxyz0123456789"
WEBHOOK_TOKEN = "abcdefghijklmnopqrstuvwxyz0123456789"
WEBHOOK = f"https://discord.com/api/webhooks/123456789012345678/{WEBHOOK_TOKEN}"

EXPECTED_HOSTS = {
    "Mastodon": {"mastodon.test"},
    "Bluesky": {"bsky.test"},
    "Telegram": {"api.telegram.org"},
    "LinkedIn": {"api.linkedin.com"},
    "Discord": {"discord.com"},
}


def make_registry(client: httpx.AsyncClient) -> PlatformRegistry:
    settings = Settings(
        _env_file=None,
        mastodon_instance_url="https://mastodon.test",
        mastodon_access_token="mastodon-token-value",
        bluesky_service_url="https://bsky.test",
        bluesky_handle="user.bsky.test",
        bluesky_app_password="bluesky-password-value",
        telegram_bot_token=TELEGRAM_TOKEN,
        telegram_chat_id="@my_channel",
        linkedin_access_token="linkedin-token-value",
        linkedin_person_urn="urn:li:person:AbC_123",
        discord_webhook_url=WEBHOOK,
    )
    return build_registry(client, settings)


@pytest.mark.parametrize("name", list(EXPECTED_HOSTS))
async def test_adapter_contacts_only_its_own_https_host(
    http_client: httpx.AsyncClient, respx_mock: MockRouter, name: str
) -> None:
    respx_mock.route().mock(return_value=httpx.Response(500))
    adapter = make_registry(http_client).get(name)

    with pytest.raises(PlatformError):
        await adapter.publish(MESSAGE, idempotency_key="key-123")

    assert len(respx_mock.calls) >= 1
    for call in respx_mock.calls:
        url = call.request.url
        assert url.scheme == "https"
        assert url.host in EXPECTED_HOSTS[name]
        assert url.host != CTA_HOST


@pytest.mark.parametrize("name", list(EXPECTED_HOSTS))
async def test_redirects_are_never_followed(
    http_client: httpx.AsyncClient, respx_mock: MockRouter, name: str
) -> None:
    redirect = httpx.Response(302, headers={"Location": "https://evil.example/steal"})
    respx_mock.route().mock(return_value=redirect)
    adapter = make_registry(http_client).get(name)

    with pytest.raises(PlatformError):
        await adapter.publish(MESSAGE, idempotency_key="key-123")

    assert len(respx_mock.calls) == 1
    assert all(call.request.url.host != "evil.example" for call in respx_mock.calls)