"""FastAPI dependencies that expose application state to routes."""

from fastapi import Request

from app.core.config import Settings
from app.platforms.registry import PlatformRegistry


def get_app_settings(request: Request) -> Settings:
    """Return the settings the application was created with."""
    settings: Settings = request.app.state.settings
    return settings


def get_registry(request: Request) -> PlatformRegistry:
    """Return the platform registry built at startup."""
    registry: PlatformRegistry = request.app.state.registry
    return registry