"""FastAPI application factory and lifecycle management."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.core.config import Settings, get_settings
from app.platforms.registry import build_registry

logger = logging.getLogger(__name__)

API_DESCRIPTION = (
    "Publishes a single message containing one call-to-action link to multiple "
    "third-party platforms. Platform selection is internal, each platform runs "
    "independently, and one platform failing never stops the others."
)


def _silence_http_client_logging() -> None:
    """Stop httpx/httpcore from logging request URLs.

    httpx logs every request URL at INFO level. Telegram carries the bot token in
    the URL path, so INFO-level HTTP logging would leak a credential into logs.
    """
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create shared resources at startup and release them at shutdown."""
    settings: Settings = app.state.settings
    timeout = httpx.Timeout(
        settings.http_timeout_seconds,
        connect=settings.http_connect_timeout_seconds,
    )
    limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=False,
        headers={"User-Agent": f"multi-platform-posting/{settings.app_version}"},
    ) as http_client:
        app.state.http_client = http_client
        app.state.registry = build_registry(http_client, settings)
        logger.info("Application started with %d platform(s)", len(app.state.registry))
        yield
    logger.info("Application stopped; HTTP client closed")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application. Tests pass their own settings."""
    _silence_http_client_logging()
    resolved = settings or get_settings()
    app = FastAPI(
        title=resolved.app_name,
        version=resolved.app_version,
        description=API_DESCRIPTION,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    return app


app = create_app()