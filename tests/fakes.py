"""Test doubles. Never registered in production code and never touch the network."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.exceptions import PlatformError
from app.platforms.base import PlatformAdapter, PublishedPost


def ok_post(url: str | None = "https://fake.example/post/1") -> PublishedPost:
    """A successful publication happening now."""
    return PublishedPost(posted_url=url, public_access=True, posted_at=datetime.now(UTC))


class FakeAdapter(PlatformAdapter):
    """Adapter that succeeds or raises a chosen error, recording what it was asked to publish."""

    def __init__(
        self,
        name: str = "Fake",
        *,
        configured: bool = True,
        error: PlatformError | None = None,
        posted_url: str | None = "https://fake.example/post/1",
    ) -> None:
        self._name = name
        self._configured = configured
        self._error = error
        self._posted_url = posted_url
        self.published_messages: list[str] = []
        self.idempotency_keys: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    def is_configured(self) -> bool:
        return self._configured

    async def publish(self, message: str, *, idempotency_key: str) -> PublishedPost:
        self.published_messages.append(message)
        self.idempotency_keys.append(idempotency_key)
        if self._error is not None:
            raise self._error
        return ok_post(self._posted_url)


@dataclass
class ConcurrencyTracker:
    """Shared counter that records how many adapters were publishing at once."""

    current: int = 0
    peak: int = 0


class ScriptedAdapter(PlatformAdapter):
    """Adapter that plays back a script of outcomes, one per call.

    Each outcome is a ``PublishedPost`` (returned) or an exception (raised). When
    the script runs out, the last outcome repeats. With no script it succeeds.
    ``calls`` records ``(message, idempotency_key)`` for every call.
    """

    def __init__(
        self,
        name: str = "Scripted",
        outcomes: Sequence[PublishedPost | BaseException] = (),
        *,
        idempotent: bool = False,
        delay: float = 0.0,
        configured: bool = True,
        tracker: ConcurrencyTracker | None = None,
    ) -> None:
        self._name = name
        self._outcomes = list(outcomes)
        self._delay = delay
        self._configured = configured
        self._tracker = tracker
        self.idempotent_retries = idempotent
        self.calls: list[tuple[str, str]] = []

    @property
    def name(self) -> str:
        return self._name

    def is_configured(self) -> bool:
        return self._configured

    async def publish(self, message: str, *, idempotency_key: str) -> PublishedPost:
        self.calls.append((message, idempotency_key))
        index = len(self.calls) - 1
        if self._tracker is not None:
            self._tracker.current += 1
            self._tracker.peak = max(self._tracker.peak, self._tracker.current)
        try:
            if self._delay:
                await asyncio.sleep(self._delay)
        finally:
            if self._tracker is not None:
                self._tracker.current -= 1
        if not self._outcomes:
            return ok_post()
        outcome = self._outcomes[min(index, len(self._outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome