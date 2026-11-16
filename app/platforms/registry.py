"""Registry of platform adapters.

The registry is how platform selection stays internal: the API never receives a
platform list, it asks the registry which adapters exist.
"""

from collections.abc import Iterable

import httpx

from app.core.config import Settings
from app.platforms.base import PlatformAdapter
from app.platforms.bluesky import BlueskyAdapter
from app.platforms.mastodon import MastodonAdapter


class PlatformRegistry:
    """Ordered, case-insensitive collection of platform adapters."""

    def __init__(self, adapters: Iterable[PlatformAdapter] = ()) -> None:
        self._adapters: dict[str, PlatformAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    @staticmethod
    def _key(name: str) -> str:
        return name.casefold()

    def register(self, adapter: PlatformAdapter) -> None:
        """Add an adapter; platform names must be unique."""
        key = self._key(adapter.name)
        if key in self._adapters:
            raise ValueError(f"Platform '{adapter.name}' is already registered")
        self._adapters[key] = adapter

    def get(self, name: str) -> PlatformAdapter:
        """Return the adapter for ``name`` or raise KeyError."""
        try:
            return self._adapters[self._key(name)]
        except KeyError:
            raise KeyError(f"Unknown platform: {name}") from None

    def all(self) -> list[PlatformAdapter]:
        """Return every registered adapter in registration order."""
        return list(self._adapters.values())

    def configured(self) -> list[PlatformAdapter]:
        """Return only adapters whose credentials and settings are present."""
        return [adapter for adapter in self._adapters.values() if adapter.is_configured()]

    def names(self) -> list[str]:
        """Return display names in registration order."""
        return [adapter.name for adapter in self._adapters.values()]

    def __len__(self) -> int:
        return len(self._adapters)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self._key(name) in self._adapters


def build_registry(http_client: httpx.AsyncClient, settings: Settings) -> PlatformRegistry:
    """Create the application's registry from settings.

    Every adapter is registered, configured or not. Callers use ``configured()``
    to find the ones that can actually publish.
    """
    return PlatformRegistry(
        [
            MastodonAdapter(
                http_client,
                instance_url=settings.mastodon_instance_url,
                access_token=settings.mastodon_access_token,
            ),
            BlueskyAdapter(
                http_client,
                service_url=settings.bluesky_service_url,
                handle=settings.bluesky_handle,
                app_password=settings.bluesky_app_password,
            ),
        ]
    )