"""Tests for the platform registry."""

import pytest

from app.platforms.registry import PlatformRegistry
from tests.fakes import FakeAdapter


def test_register_and_lookup_is_case_insensitive() -> None:
    adapter = FakeAdapter("Mastodon")
    registry = PlatformRegistry([adapter])
    assert registry.get("mastodon") is adapter
    assert "MASTODON" in registry


def test_duplicate_registration_rejected() -> None:
    registry = PlatformRegistry([FakeAdapter("Bluesky")])
    with pytest.raises(ValueError, match="already registered"):
        registry.register(FakeAdapter("bluesky"))


def test_configured_excludes_unconfigured_adapters() -> None:
    ready = FakeAdapter("Ready")
    idle = FakeAdapter("Idle", configured=False)
    registry = PlatformRegistry([ready, idle])
    assert registry.configured() == [ready]
    assert registry.all() == [ready, idle]


def test_unknown_platform_raises_key_error() -> None:
    with pytest.raises(KeyError):
        PlatformRegistry().get("nope")


def test_len_and_names_preserve_registration_order() -> None:
    registry = PlatformRegistry([FakeAdapter("B"), FakeAdapter("A")])
    assert len(registry) == 2
    assert registry.names() == ["B", "A"]