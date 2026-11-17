"""Tests for application creation, lifecycle and dependency wiring."""

import logging
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_registry
from app.core.config import Settings
from app.platforms.registry import PlatformRegistry
from tests.fakes import FakeAdapter

REAL_PLATFORMS = ["Mastodon", "Bluesky", "Telegram", "LinkedIn", "Discord"]


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_documentation_pages_available(app: FastAPI, path: str) -> None:
    with TestClient(app) as client:
        assert client.get(path).status_code == 200


def test_openapi_metadata(app: FastAPI, test_settings: Settings) -> None:
    with TestClient(app) as client:
        info = client.get("/openapi.json").json()["info"]
    assert info["title"] == test_settings.app_name
    assert info["version"] == test_settings.app_version


def test_lifespan_creates_and_closes_shared_client(app: FastAPI) -> None:
    with TestClient(app):
        http_client = app.state.http_client
        assert not http_client.is_closed
        assert isinstance(app.state.registry, PlatformRegistry)
    assert http_client.is_closed


def test_http_client_security_and_timeouts(app: FastAPI, test_settings: Settings) -> None:
    with TestClient(app):
        http_client = app.state.http_client
        assert http_client.follow_redirects is False
        assert http_client.timeout.connect == test_settings.http_connect_timeout_seconds
        assert http_client.timeout.read == test_settings.http_timeout_seconds


def test_registry_available_through_dependency(app: FastAPI) -> None:
    @app.get("/_test/platforms")
    def list_platforms(registry: Annotated[PlatformRegistry, Depends(get_registry)]) -> list[str]:
        return registry.names()

    with TestClient(app) as client:
        app.state.registry.register(FakeAdapter("Alpha"))
        response = client.get("/_test/platforms")
    assert response.json() == [*REAL_PLATFORMS, "Alpha"]


def test_real_adapters_registered_but_unconfigured_without_credentials(app: FastAPI) -> None:
    with TestClient(app):
        registry = app.state.registry
        assert registry.names() == REAL_PLATFORMS
        assert registry.configured() == []


def test_at_least_five_platforms_are_integrated(app: FastAPI) -> None:
    with TestClient(app):
        assert len(app.state.registry) >= 5


def test_http_request_logging_is_silenced_to_protect_url_secrets(app: FastAPI) -> None:
    """Telegram's bot token and Discord's webhook token are in URLs; httpx logs URLs at INFO."""
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING