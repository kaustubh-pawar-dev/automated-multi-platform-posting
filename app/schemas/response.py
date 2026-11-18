"""Response schemas for the posting API."""

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class PlatformResult(BaseModel):
    """Outcome of one publishing attempt on one platform.

    ``date`` and ``time`` are the moment the platform accepted the post, in UTC.
    They are null for failed attempts.
    """

    platform: str = Field(..., description="Platform name.", examples=["Mastodon"])
    status: Literal["success", "failed"] = Field(..., description="Attempt outcome.")
    posted_url: str | None = Field(
        default=None,
        description="URL of the published post, as reported by the platform. Null if unavailable.",
    )
    public_access: bool = Field(
        ...,
        description="True only when the post is known to be publicly visible.",
    )
    date: dt.date | None = Field(default=None, description="Publication date (UTC).")
    time: dt.time | None = Field(default=None, description="Publication time (UTC).")
    error: str | None = Field(default=None, description="Safe error message when failed.")

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.status == "success":
            if self.error is not None:
                raise ValueError("successful result must not contain an error")
            if self.date is None or self.time is None:
                raise ValueError("successful result requires date and time")
        else:
            if not self.error:
                raise ValueError("failed result requires an error message")
            if (
                self.posted_url is not None
                or self.public_access
                or self.date is not None
                or self.time is not None
            ):
                raise ValueError("failed result must not contain publication details")
        return self

    @classmethod
    def success(
        cls,
        platform: str,
        *,
        posted_url: str | None,
        public_access: bool,
        posted_at: dt.datetime,
    ) -> Self:
        """Build a successful result from a timezone-aware publication timestamp."""
        if posted_at.tzinfo is None or posted_at.utcoffset() is None:
            raise ValueError("posted_at must be timezone-aware")
        utc = posted_at.astimezone(dt.UTC)
        return cls(
            platform=platform,
            status="success",
            posted_url=posted_url,
            public_access=public_access,
            date=utc.date(),
            time=utc.time().replace(microsecond=0),
            error=None,
        )

    @classmethod
    def failure(cls, platform: str, error: str) -> Self:
        """Build a failed result carrying a safe error message."""
        return cls(
            platform=platform,
            status="failed",
            posted_url=None,
            public_access=False,
            date=None,
            time=None,
            error=error,
        )


class PostingResponse(BaseModel):
    """Aggregate result of publishing to all platforms."""

    success: bool = Field(
        ...,
        description=(
            "True only when at least one platform was attempted and every "
            "attempted platform succeeded."
        ),
    )
    total_platforms: int = Field(..., ge=0, description="Platforms attempted.")
    successful_count: int = Field(..., ge=0, description="Platforms that published.")
    failed_count: int = Field(..., ge=0, description="Platforms that failed.")
    results: list[PlatformResult] = Field(..., description="One entry per platform attempted.")
    skipped_platforms: list[str] = Field(
        default_factory=list,
        description="Platforms that are not configured and were therefore not attempted.",
    )
    total_duration_ms: int = Field(
        default=0, ge=0, description="Wall-clock time of the whole publication, in milliseconds."
    )
    platform_durations_ms: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Milliseconds each attempted platform took, including retries and waits, by "
            "platform name. A platform whose task failed unexpectedly has no entry."
        ),
    )

    @classmethod
    def from_results(
        cls,
        results: Sequence[PlatformResult],
        skipped: Sequence[str] = (),
        *,
        durations_ms: Mapping[str, int] | None = None,
        total_duration_ms: int = 0,
    ) -> Self:
        """Aggregate individual results into the overall response."""
        successful = sum(1 for result in results if result.status == "success")
        failed = len(results) - successful
        return cls(
            success=bool(results) and failed == 0,
            total_platforms=len(results),
            successful_count=successful,
            failed_count=failed,
            results=list(results),
            skipped_platforms=list(skipped),
            total_duration_ms=total_duration_ms,
            platform_durations_ms=dict(durations_ms or {}),
        )