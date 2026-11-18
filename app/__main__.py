"""Run the server with ``python -m app`` using the host and port from the settings."""

import math

import uvicorn

from app.core.config import Settings, get_settings

SHUTDOWN_MARGIN_SECONDS = 5


def graceful_shutdown_seconds(settings: Settings) -> int:
    """How long uvicorn waits for in-flight requests when the server is stopped.

    A request can legitimately run for the per-platform deadline,
    ``(max_retries + 1) * http_timeout_seconds``. Waiting that long (plus a small
    margin) lets a publication in progress finish instead of being cut off by
    Ctrl+C or a deployment.
    """
    deadline = (settings.max_retries + 1) * settings.http_timeout_seconds
    return math.ceil(deadline) + SHUTDOWN_MARGIN_SECONDS


def main() -> None:
    """Start uvicorn on the configured address."""
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        timeout_graceful_shutdown=graceful_shutdown_seconds(settings),
        log_config=None,
    )


if __name__ == "__main__":
    main()