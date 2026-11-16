"""Application exception hierarchy.

Platform adapters raise these instead of returning ad-hoc error strings. The
posting service turns each one into a normalised failed result. Only
``safe_message`` is ever shown to API clients; ``detail`` is for logs and must
never contain credentials.
"""


class AppError(Exception):
    """Base class for all application-defined errors."""


class PlatformError(AppError):
    """A failure while publishing to a single platform."""

    default_message = "Platform error"
    retryable: bool = False

    def __init__(
        self,
        safe_message: str | None = None,
        *,
        retryable: bool | None = None,
        detail: str | None = None,
    ) -> None:
        self.safe_message = safe_message or self.default_message
        if retryable is not None:
            self.retryable = retryable
        self.detail = detail
        super().__init__(self.safe_message)


class PlatformNotConfiguredError(PlatformError):
    """Required credentials or settings for the platform are missing."""

    default_message = "Platform is not configured"


class PlatformAuthError(PlatformError):
    """The platform rejected our credentials (not retryable)."""

    default_message = "Authentication failed"


class PlatformRejectedError(PlatformError):
    """The platform refused the content or request (4xx other than auth/rate limit)."""

    default_message = "Platform rejected the request"


class PlatformRateLimitError(PlatformError):
    """The platform asked us to slow down (HTTP 429 or equivalent)."""

    default_message = "Rate limit exceeded"
    retryable = True

    def __init__(
        self,
        safe_message: str | None = None,
        *,
        retry_after: float | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(safe_message, detail=detail)
        self.retry_after = retry_after


class PlatformTimeoutError(PlatformError):
    """The request to the platform timed out."""

    default_message = "Request timed out"
    retryable = True


class PlatformUnavailableError(PlatformError):
    """Network failure or 5xx response from the platform."""

    default_message = "Platform temporarily unavailable"
    retryable = True


class PlatformResponseError(PlatformError):
    """The platform answered with a malformed or unexpected payload."""

    default_message = "Invalid response from platform"