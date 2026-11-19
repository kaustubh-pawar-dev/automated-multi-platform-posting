"""Shared pytest fixtures."""

import io
import logging
import ssl
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.core.logging import remove_app_handlers
from app.main import create_app


@pytest.fixture(autouse=True)
def _isolate_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every setting from the process environment for each test.

    ``Settings(_env_file=None)`` still reads real environment variables, so a
    variable left in the developer's shell (for example DISABLED_PLATFORMS)
    could silently change test results. Tests that need a variable set it
    themselves with ``monkeypatch.setenv``.
    """
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture
def test_settings() -> Settings:
    """Settings that ignore any local .env file."""
    return Settings(_env_file=None, environment="testing")


@pytest.fixture
def app(test_settings: Settings) -> FastAPI:
    """A fresh application instance per test."""
    return create_app(test_settings)


@pytest.fixture(scope="session")
def ssl_context() -> ssl.SSLContext:
    """Build the (slow) SSL context once and share it, instead of once per test."""
    return httpx.create_ssl_context()


@pytest.fixture
async def http_client(ssl_context: ssl.SSLContext) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client configured like the production one (no redirects)."""
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=5.0, verify=ssl_context
    ) as client:
        yield client


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    """A buffer for capturing application log output; logging is restored afterwards."""
    stream = io.StringIO()
    yield stream
    remove_app_handlers()
    logging.getLogger().setLevel(logging.WARNING)