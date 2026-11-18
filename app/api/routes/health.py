"""Operational endpoints: liveness (``/health``) and platform diagnostics (``/platforms``)."""

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_app_settings, get_registry
from app.core.config import Settings
from app.platforms.registry import PlatformRegistry
from app.schemas.health import HealthResponse, PlatformsResponse
from app.services.diagnostics import build_platforms_report

router = APIRouter(tags=["operations"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness check",
    description="Returns 200 while the process is running. It never contacts a platform.",
)
async def health(
    request: Request, settings: Annotated[Settings, Depends(get_app_settings)]
) -> HealthResponse:
    """Report that the service is alive."""
    uptime = time.monotonic() - request.app.state.started_monotonic
    return HealthResponse(version=settings.app_version, uptime_seconds=round(max(uptime, 0.0), 1))


@router.get(
    "/platforms",
    response_model=PlatformsResponse,
    summary="Platform configuration diagnostics",
    description=(
        "Shows which platforms are configured and which environment variables are "
        "missing. It returns variable **names only, never credential values**. In "
        "production, restrict access to this endpoint at the network level."
    ),
)
async def platforms(
    registry: Annotated[PlatformRegistry, Depends(get_registry)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> PlatformsResponse:
    """Report the configuration state of every enabled platform."""
    return build_platforms_report(registry, settings)