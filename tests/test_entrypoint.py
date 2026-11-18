"""Tests for the ``python -m app`` entry point and graceful-shutdown timing."""

from pathlib import Path
from runpy import run_path
from typing import Any

import pytest

from app.__main__ import graceful_shutdown_seconds, main
from app.core.config import Settings

MODULE = Path(__file__).resolve().parents[1] / "app" / "__main__.py"


def fake_settings() -> Settings:
    return Settings(
        _env_file=None, host="127.0.0.1", port=4100, max_retries=1, http_timeout_seconds=7
    )


@pytest.mark.parametrize(
    ("retries", "timeout", "expected"),
    [(2, 10, 35), (0, 5.5, 11), (5, 60, 365)],
    ids=["defaults", "fractional-timeout", "maximum"],
)
def test_graceful_shutdown_outlasts_the_longest_publication(
    retries: int, timeout: float, expected: int
) -> None:
    settings = Settings(_env_file=None, max_retries=retries, http_timeout_seconds=timeout)

    assert graceful_shutdown_seconds(settings) == expected


def test_main_starts_uvicorn_with_the_configured_address(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr("app.__main__.uvicorn.run", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr("app.__main__.get_settings", fake_settings)

    main()

    assert calls == [
        (
            ("app.main:app",),
            {
                "host": "127.0.0.1",
                "port": 4100,
                "timeout_graceful_shutdown": 19,
                "log_config": None,
            },
        )
    ]


def test_running_the_module_as_a_script_starts_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: calls.append(kwargs))
    monkeypatch.setattr("app.core.config.get_settings", fake_settings)

    run_path(str(MODULE), run_name="__main__")

    assert len(calls) == 1
    assert calls[0]["port"] == 4100