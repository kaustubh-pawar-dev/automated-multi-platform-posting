"""Shared HTTP plumbing for platform adapters.

* ``send_request`` refuses any host that is not on the adapter's allow-list
  (HTTPS only), which blocks accidental server-side request forgery.
* Transport failures and bad statuses are mapped to ``PlatformError`` subclasses
  carrying safe messages. Response bodies and exception texts are never copied
  into messages, because they can echo request data or credentials.
* Failures that happen before the request is sent are not ambiguous; failures
  after it was sent (read timeouts, dropped connections, 5xx) are.
"""

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.exceptions import (
    PlatformAuthError,
    PlatformError,
    PlatformRateLimitError,
    PlatformRejectedError,
    PlatformResponseError,
    PlatformTimeoutError,
    PlatformUnavailableError,
)

MAX_RETRY_AFTER_SECONDS = 60.0


def parse_retry_after(value: str | None) -> float | None:
    """Parse a numeric ``Retry-After`` header, clamped to a sane maximum."""
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return min(seconds, MAX_RETRY_AFTER_SECONDS)


def parse_platform_timestamp(value: object) -> datetime | None:
    """Parse an ISO 8601 timestamp into UTC; None if missing, invalid or naive."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def ensure_allowed_url(url: str, allowed_hosts: frozenset[str]) -> None:
    """Raise unless ``url`` is HTTPS and its host is exactly one of ``allowed_hosts``."""
    try:
        parts = urlsplit(url)
    except ValueError:
        raise PlatformError("Outbound request blocked", detail="malformed url") from None
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or host not in allowed_hosts:
        raise PlatformError(
            "Outbound request blocked", detail=f"host not allowed: {host or 'none'}"
        )


async def send_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    allowed_hosts: frozenset[str],
    headers: Mapping[str, str] | None = None,
    json_body: Mapping[str, Any] | None = None,
) -> httpx.Response:
    """Send one request and return the response without checking its status.

    Raises:
        PlatformError: the host is not allowed, or the request failed in transit.
    """
    ensure_allowed_url(url, allowed_hosts)
    try:
        return await client.request(method, url, headers=headers, json=json_body)
    except (httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
        raise PlatformTimeoutError(
            "Connection to the platform timed out", detail=type(exc).__name__
        ) from None
    except httpx.TimeoutException as exc:
        raise PlatformTimeoutError(
            "Request to the platform timed out", ambiguous=True, detail=type(exc).__name__
        ) from None
    except httpx.ConnectError as exc:
        raise PlatformUnavailableError(
            "Could not connect to the platform", detail=type(exc).__name__
        ) from None
    except httpx.RequestError as exc:
        raise PlatformUnavailableError(
            "Network error while contacting the platform",
            ambiguous=True,
            detail=type(exc).__name__,
        ) from None


def raise_for_status(response: httpx.Response) -> None:
    """Raise the matching ``PlatformError`` for any non-2xx response."""
    status = response.status_code
    if 200 <= status < 300:
        return
    detail = f"HTTP {status}"
    if status in (401, 403):
        raise PlatformAuthError(detail=detail)
    if status == 429:
        raise PlatformRateLimitError(
            retry_after=parse_retry_after(response.headers.get("Retry-After")), detail=detail
        )
    if 300 <= status < 400:
        raise PlatformResponseError("Unexpected redirect from the platform", detail=detail)
    if status >= 500:
        raise PlatformUnavailableError(
            f"Platform temporarily unavailable (HTTP {status})", ambiguous=True, detail=detail
        )
    raise PlatformRejectedError(f"Platform rejected the request (HTTP {status})", detail=detail)


def parse_json_object(response: httpx.Response) -> dict[str, Any]:
    """Return the response body as a JSON object or raise ``PlatformResponseError``."""
    try:
        data = response.json()
    except ValueError:
        raise PlatformResponseError(detail="response body is not valid JSON") from None
    if not isinstance(data, dict):
        raise PlatformResponseError(detail="response body is not a JSON object")
    return data