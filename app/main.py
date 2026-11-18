"""FastAPI application factory and lifecycle management."""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.errors import register_exception_handlers
from app.api.middleware import RequestContextMiddleware
from app.api.routes.health import router as health_router
from app.api.routes.posting import router as posting_router
from app.core.config import Settings, get_settings
from app.core.logging import collect_secrets, configure_logging
from app.platforms.registry import build_registry
from app.services.posting_service import PostingService

logger = logging.getLogger(__name__)

API_DESCRIPTION = (
    "Publishes a single message containing one call-to-action link to multiple "
    "third-party platforms. Platform selection is internal, each platform runs "
    "independently, and one platform failing never stops the others."
)


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
        app.state.posting_service = PostingService.from_settings(app.state.registry, settings)
        configured = [adapter.name for adapter in app.state.registry.configured()]
        not_configured = [n for n in app.state.registry.names() if n not in configured]
        logger.info(
            "application_started environment=%s configured=%s not_configured=%s",
            settings.environment,
            configured,
            not_configured,
        )
        yield
    logger.info("application_stopped http_client_closed=True")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application. Tests pass their own settings."""
    resolved = settings or get_settings()
    configure_logging(
        resolved.log_level,
        "json" if resolved.environment == "production" else "text",
        secrets=collect_secrets(resolved),
    )
    app = FastAPI(
        title=resolved.app_name,
        version=resolved.app_version,
        description=API_DESCRIPTION,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.started_monotonic = time.monotonic()
    register_exception_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(posting_router)
    app.include_router(health_router)
    return app


app = create_app()