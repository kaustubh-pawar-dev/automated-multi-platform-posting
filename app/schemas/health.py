"""Response schemas for the operational endpoints."""

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Liveness information. Does not depend on any external platform."""

    status: Literal["ok"] = Field(default="ok", description="Always 'ok' while the process runs.")
    version: str = Field(..., description="Application version.", examples=["0.1.0"])
    uptime_seconds: float = Field(..., ge=0, description="Seconds since the app was created.")


class PlatformStatus(BaseModel):
    """Configuration state of one platform. Never contains credential values."""

    name: str = Field(..., examples=["Mastodon"])
    configured: bool = Field(..., description="True when the platform can publish.")
    missing_settings: list[str] = Field(
        default_factory=list,
        description="Names of the environment variables that are not set.",
        examples=[["MASTODON_INSTANCE_URL", "MASTODON_ACCESS_TOKEN"]],
    )
    idempotent_retries: bool = Field(
        ..., description="True when retrying an ambiguous failure cannot duplicate a post."
    )


class PlatformsResponse(BaseModel):
    """Configuration overview of every platform that is switched on."""

    platforms: list[PlatformStatus]
    configured_count: int = Field(..., ge=0)
    total_count: int = Field(..., ge=0)
    disabled_platforms: list[str] = Field(
        default_factory=list,
        description="Platforms switched off through DISABLED_PLATFORMS.",
    )