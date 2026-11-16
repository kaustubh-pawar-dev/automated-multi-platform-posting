"""Tests for application creation, lifecycle and dependency wiring."""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_registry
from app.core.config import Settings
from app.platforms.registry import PlatformRegistry
from tests.fakes import FakeAdapter


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
    assert response.json() == ["Alpha"]