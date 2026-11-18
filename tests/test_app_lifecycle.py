"""Tests for application creation, lifecycle and dependency wiring."""

import logging
from collections.abc import AsyncIterator
from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI

from app.api.dependencies import get_app_settings, get_registry
from app.core.config import Settings
from app.platforms.registry import PlatformRegistry
from tests.fakes import FakeAdapter

REAL_PLATFORMS = ["Mastodon", "Bluesky", "Telegram", "LinkedIn", "Discord"]


@pytest.fixture
async def running_app(app: FastAPI) -> AsyncIterator[FastAPI]:
    """The application with its lifespan (startup and shutdown) active."""
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(running_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=running_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
async def test_documentation_pages_available(client: httpx.AsyncClient, path: str) -> None:
    assert (await client.get(path)).status_code == 200


async def test_openapi_metadata(client: httpx.AsyncClient, test_settings: Settings) -> None:
    info = (await client.get("/openapi.json")).json()["info"]
    assert info["title"] == test_settings.app_name
    assert info["version"] == test_settings.app_version


async def test_lifespan_creates_and_closes_shared_client(app: FastAPI) -> None:
    async with app.router.lifespan_context(app):
        http_client = app.state.http_client
        assert not http_client.is_closed
        assert isinstance(app.state.registry, PlatformRegistry)
    assert http_client.is_closed


async def test_http_client_security_and_timeouts(
    running_app: FastAPI, test_settings: Settings
) -> None:
    http_client = running_app.state.http_client
    assert http_client.follow_redirects is False
    assert http_client.timeout.connect == test_settings.http_connect_timeout_seconds
    assert http_client.timeout.read == test_settings.http_timeout_seconds


async def test_registry_available_through_dependency(
    running_app: FastAPI, client: httpx.AsyncClient
) -> None:
    @running_app.get("/_test/platforms")
    def list_platforms(registry: Annotated[PlatformRegistry, Depends(get_registry)]) -> list[str]:
        return registry.names()

    running_app.state.registry.register(FakeAdapter("Alpha"))
    response = await client.get("/_test/platforms")
    assert response.json() == [*REAL_PLATFORMS, "Alpha"]


async def test_settings_available_through_dependency(
    running_app: FastAPI, client: httpx.AsyncClient, test_settings: Settings
) -> None:
    @running_app.get("/_test/settings")
    def show_name(settings: Annotated[Settings, Depends(get_app_settings)]) -> str:
        return settings.app_name

    response = await client.get("/_test/settings")
    assert response.json() == test_settings.app_name


async def test_real_adapters_registered_but_unconfigured_without_credentials(
    running_app: FastAPI,
) -> None:
    registry = running_app.state.registry
    assert registry.names() == REAL_PLATFORMS
    assert registry.configured() == []


async def test_at_least_five_platforms_are_integrated(running_app: FastAPI) -> None:
    assert len(running_app.state.registry) >= 5


def test_http_request_logging_is_silenced_to_protect_url_secrets(app: FastAPI) -> None:
    """Telegram's bot token and Discord's webhook token are in URLs; httpx logs URLs at INFO."""
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING