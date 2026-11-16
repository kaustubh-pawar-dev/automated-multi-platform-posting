"""The contract every platform integration implements (Strategy / Adapter).

Adapters know how to talk to exactly one platform's API. They do NOT handle
retries, timeouts across platforms, aggregation, or response normalisation;
the posting service does that, so adding a platform never changes the service.

Rules for adapters:
* Return a ``PublishedPost`` only for a real publication confirmed by the API.
* Report failure by raising a ``PlatformError`` subclass with a safe message.
* Never log or raise anything containing credentials.
* Only call the platform's own API hosts.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PublishedPost:
    """Facts about a post the platform confirmed.

    ``posted_url`` is None when the platform provides no public link (for
    example a post in a private Telegram channel). ``posted_at`` must be
    timezone-aware.
    """

    posted_url: str | None
    public_access: bool
    posted_at: datetime


class PlatformAdapter(ABC):
    """Abstract publishing interface for one platform."""

    #: True when the platform deduplicates repeated requests that carry the same
    #: idempotency key, which makes retrying an ambiguous failure safe.
    idempotent_retries: bool = False

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable platform name used in API results."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True when all required credentials and settings are present."""

    @abstractmethod
    async def publish(self, message: str, *, idempotency_key: str) -> PublishedPost:
        """Publish ``message`` unchanged and return what the platform confirmed.

        ``idempotency_key`` is generated once per publication and reused on every
        retry of that publication. Adapters whose platform supports idempotency
        must send it; the others ignore it.

        Raises:
            PlatformError: for any failure, with a client-safe message.
        """