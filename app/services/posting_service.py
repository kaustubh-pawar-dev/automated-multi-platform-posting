"""Orchestrates publishing one validated message to every configured platform.

Guarantees:

* Platforms run concurrently, bounded by ``max_concurrent`` (a semaphore).
* Each platform is isolated: every outcome, including unexpected exceptions,
  becomes that platform's result, and one failure never affects the others.
* Each platform has an overall deadline covering all of its attempts and waits.
* Transient failures are retried with exponential backoff and jitter, honouring
  ``Retry-After``. A retry is allowed only when the error is retryable AND
  either the failure is not ambiguous or the adapter supports idempotent
  retries. A failure is *ambiguous* when the post may already exist (read
  timeout, 5xx, dropped connection); retrying one without idempotency could
  publish a duplicate, so those are reported as failures instead.
* One idempotency key is generated per platform per publication and reused on
  every retry.
* Platforms that are not configured are not attempted; they are listed in
  ``skipped_platforms`` so the response never hides them.
* Client-facing messages come only from ``PlatformError.safe_message`` or fixed
  text. Logs never contain the message text or credentials.
"""

import asyncio
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC

from app.core.config import Settings
from app.core.exceptions import PlatformError, PlatformRateLimitError
from app.platforms.base import PlatformAdapter, PublishedPost
from app.platforms.registry import PlatformRegistry
from app.schemas.response import PlatformResult, PostingResponse
from app.services.message_validation import ValidatedMessage

logger = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 10.0
UNEXPECTED_ERROR = "Unexpected error while publishing"
DEADLINE_ERROR = "Platform did not respond in time"

SleepFn = Callable[[float], Awaitable[None]]
JitterFn = Callable[[], float]
KeyFactory = Callable[[], str]

_system_random = random.SystemRandom()


def _default_jitter() -> float:
    """Return a float in [0, 1)."""
    return _system_random.random()


def _default_key() -> str:
    return uuid.uuid4().hex


def _with_attempts(message: str, attempts: int) -> str:
    """Mention the attempt count when more than one attempt was made."""
    return f"{message} (after {attempts} attempts)" if attempts > 1 else message


@dataclass(slots=True)
class _Progress:
    """Mutable attempt counter shared between the retry loop and its caller."""

    attempts: int = 0


class PostingService:
    """Publishes a validated message to all configured platforms."""

    def __init__(
        self,
        registry: PlatformRegistry,
        *,
        max_concurrent: int,
        max_retries: int,
        backoff_base_seconds: float,
        platform_deadline_seconds: float,
        sleep: SleepFn = asyncio.sleep,
        jitter: JitterFn = _default_jitter,
        key_factory: KeyFactory = _default_key,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        if max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if backoff_base_seconds <= 0:
            raise ValueError("backoff_base_seconds must be positive")
        if platform_deadline_seconds <= 0:
            raise ValueError("platform_deadline_seconds must be positive")
        self._registry = registry
        self.max_concurrent = max_concurrent
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.platform_deadline_seconds = platform_deadline_seconds
        self._sleep = sleep
        self._jitter = jitter
        self._key_factory = key_factory

    @classmethod
    def from_settings(cls, registry: PlatformRegistry, settings: Settings) -> "PostingService":
        """Build a service from application settings.

        The per-platform deadline is ``(max_retries + 1) * http_timeout_seconds``:
        enough for every attempt to use its full HTTP timeout, and never more.
        """
        return cls(
            registry,
            max_concurrent=settings.max_concurrent_platforms,
            max_retries=settings.max_retries,
            backoff_base_seconds=settings.retry_backoff_base_seconds,
            platform_deadline_seconds=(settings.max_retries + 1) * settings.http_timeout_seconds,
        )

    async def publish(self, message: ValidatedMessage) -> PostingResponse:
        """Publish ``message`` everywhere it can be published and aggregate the results."""
        attempted: list[PlatformAdapter] = []
        skipped: list[str] = []
        for adapter in self._registry.all():
            if adapter.is_configured():
                attempted.append(adapter)
            else:
                skipped.append(adapter.name)

        logger.info(
            "posting_started platforms=%d skipped=%s message_chars=%d",
            len(attempted),
            skipped,
            len(message.text),
        )
        started = time.monotonic()
        semaphore = asyncio.Semaphore(self.max_concurrent)
        outcomes = await asyncio.gather(
            *(self._run_platform(adapter, message.text, semaphore) for adapter in attempted),
            return_exceptions=True,
        )

        results: list[PlatformResult] = []
        for adapter, outcome in zip(attempted, outcomes, strict=True):
            if isinstance(outcome, PlatformResult):
                results.append(outcome)
                continue
            logger.error(
                "platform_task_crashed platform=%s error_type=%s",
                adapter.name,
                type(outcome).__name__,
            )
            results.append(PlatformResult.failure(adapter.name, UNEXPECTED_ERROR))

        response = PostingResponse.from_results(results, skipped=skipped)
        logger.info(
            "posting_finished success=%s attempted=%d succeeded=%d failed=%d duration_ms=%d",
            response.success,
            response.total_platforms,
            response.successful_count,
            response.failed_count,
            int((time.monotonic() - started) * 1000),
        )
        return response

    async def _run_platform(
        self, adapter: PlatformAdapter, text: str, semaphore: asyncio.Semaphore
    ) -> PlatformResult:
        """Publish to one platform. Always returns a result; never raises for platform errors."""
        async with semaphore:
            started = time.monotonic()
            progress = _Progress()
            logger.info("platform_attempt platform=%s", adapter.name)
            try:
                async with asyncio.timeout(self.platform_deadline_seconds):
                    post = await self._publish_with_retries(
                        adapter, text, self._key_factory(), progress
                    )
            except PlatformError as exc:
                logger.warning(
                    "platform_failed platform=%s error=%r retryable=%s ambiguous=%s "
                    "attempts=%d detail=%s",
                    adapter.name,
                    exc.safe_message,
                    exc.retryable,
                    exc.ambiguous,
                    progress.attempts,
                    exc.detail,
                )
                result = PlatformResult.failure(
                    adapter.name, _with_attempts(exc.safe_message, progress.attempts)
                )
            except TimeoutError:
                logger.warning(
                    "platform_deadline platform=%s attempts=%d deadline_s=%s",
                    adapter.name,
                    progress.attempts,
                    self.platform_deadline_seconds,
                )
                result = PlatformResult.failure(
                    adapter.name, _with_attempts(DEADLINE_ERROR, progress.attempts)
                )
            except Exception as exc:
                logger.error(
                    "platform_unexpected_error platform=%s error_type=%s",
                    adapter.name,
                    type(exc).__name__,
                )
                logger.debug("unexpected platform error traceback", exc_info=True)
                result = PlatformResult.failure(adapter.name, UNEXPECTED_ERROR)
            else:
                result = self._success_result(adapter, post)

            logger.info(
                "platform_finished platform=%s status=%s attempts=%d duration_ms=%d",
                adapter.name,
                result.status,
                progress.attempts,
                int((time.monotonic() - started) * 1000),
            )
            return result

    async def _publish_with_retries(
        self, adapter: PlatformAdapter, text: str, key: str, progress: _Progress
    ) -> PublishedPost:
        loop = asyncio.get_running_loop()
        started = loop.time()
        retry_number = 0
        while True:
            progress.attempts += 1
            try:
                return await adapter.publish(text, idempotency_key=key)
            except PlatformError as exc:
                if retry_number >= self.max_retries or not self._may_retry(adapter, exc):
                    raise
                delay = self._retry_delay(exc, retry_number)
                remaining = self.platform_deadline_seconds - (loop.time() - started)
                if delay >= remaining:
                    logger.info(
                        "retry_skipped platform=%s reason=deadline delay_s=%.2f",
                        adapter.name,
                        delay,
                    )
                    raise
                logger.warning(
                    "platform_retry platform=%s error=%r delay_s=%.2f retry=%d",
                    adapter.name,
                    exc.safe_message,
                    delay,
                    retry_number + 1,
                )
                await self._sleep(delay)
                retry_number += 1

    @staticmethod
    def _may_retry(adapter: PlatformAdapter, exc: PlatformError) -> bool:
        """Retry transient errors; retry ambiguous ones only if the platform deduplicates."""
        if not exc.retryable:
            return False
        return adapter.idempotent_retries or not exc.ambiguous

    def _retry_delay(self, exc: PlatformError, retry_number: int) -> float:
        """Exponential backoff with jitter, never shorter than ``Retry-After``."""
        backoff = self.backoff_base_seconds * (2**retry_number)
        backoff *= 0.5 + 0.5 * self._jitter()
        backoff = min(backoff, MAX_BACKOFF_SECONDS)
        if isinstance(exc, PlatformRateLimitError) and exc.retry_after is not None:
            return max(exc.retry_after, backoff)
        return backoff

    @staticmethod
    def _success_result(adapter: PlatformAdapter, post: PublishedPost) -> PlatformResult:
        posted_at = post.posted_at
        if posted_at.tzinfo is None or posted_at.utcoffset() is None:
            logger.warning("platform=%s returned a naive timestamp; assuming UTC", adapter.name)
            posted_at = posted_at.replace(tzinfo=UTC)
        return PlatformResult.success(
            adapter.name,
            posted_url=post.posted_url,
            public_access=post.public_access,
            posted_at=posted_at,
        )