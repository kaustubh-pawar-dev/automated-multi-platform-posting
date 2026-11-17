"""End-to-end secret safety: credentials must not reach API responses or logs."""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.core.exceptions import PlatformUnavailableError
from app.core.logging import REDACTED, collect_secrets, configure_logging
from app.platforms.registry import PlatformRegistry
from app.services.posting_service import PostingService
from tests.fakes import ScriptedAdapter

SECRET = "sk-live-super-secret-value-123456"
VALID = "Check out our latest business platform and grow your online visibility. https://example.com"

SECRET_SETTINGS: dict[str, str] = {
    "mastodon_access_token": "mastodon-token-value",
    "bluesky_app_password": "bluesky-password-value",
    "telegram_bot_token": "123456:ABCdefGHIjklMNOpqrsTUVwxyz0123456789",
    "linkedin_access_token": "linkedin-token-value",
    "discord_webhook_url": (
        "https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz0123456789"
    ),
}


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def test_secrets_never_reach_responses_or_logs(
    app: FastAPI, client: httpx.AsyncClient, log_stream: object
) -> None:
    leak_detail = f"upstream echoed token={SECRET} Bearer {SECRET}"
    leaky = ScriptedAdapter("Leaky", [PlatformUnavailableError("Down", detail=leak_detail)])
    crashing = ScriptedAdapter("Crashing", [RuntimeError(f"boom {SECRET}")])
    app.state.posting_service = PostingService(
        PlatformRegistry([leaky, crashing, ScriptedAdapter("Fine")]),
        max_concurrent=5,
        max_retries=0,
        backoff_base_seconds=0.01,
        platform_deadline_seconds=5.0,
    )
    configure_logging("DEBUG", "text", secrets=[SECRET], stream=log_stream)  # type: ignore[arg-type]

    response = await client.post("/posting", json={"message": VALID})

    assert response.status_code == 200
    logs = log_stream.getvalue()  # type: ignore[attr-defined]
    assert SECRET not in response.text
    assert SECRET not in logs
    assert REDACTED in logs
    assert "RuntimeError" in logs


def test_every_secret_setting_is_collected_for_redaction() -> None:
    secret_fields = {
        name
        for name, field in Settings.model_fields.items()
        if "SecretStr" in str(field.annotation)
    }
    assert secret_fields <= set(SECRET_SETTINGS), (
        "a new secret setting exists: add it to SECRET_SETTINGS in this test"
    )
    collected = collect_secrets(Settings(_env_file=None, **SECRET_SETTINGS))
    for value in SECRET_SETTINGS.values():
        assert value in collected