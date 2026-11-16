"""Test doubles. Never registered in production code and never touch the network."""

from datetime import UTC, datetime

from app.core.exceptions import PlatformError
from app.platforms.base import PlatformAdapter, PublishedPost


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

    @property
    def name(self) -> str:
        return self._name

    def is_configured(self) -> bool:
        return self._configured

    async def publish(self, message: str) -> PublishedPost:
        self.published_messages.append(message)
        if self._error is not None:
            raise self._error
        return PublishedPost(
            posted_url=self._posted_url,
            public_access=True,
            posted_at=datetime.now(UTC),
        )