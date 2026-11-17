"""Repository hygiene: the committed template must never contain real credentials."""

import re
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET_KEY_RE = re.compile(r"(TOKEN|PASSWORD|SECRET|WEBHOOK|API_KEY)", re.IGNORECASE)


def _assignments(path: Path) -> Iterator[tuple[str, str]]:
    """Yield (KEY, value) for every active assignment line in an env-style file."""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        yield key.strip(), value.strip()


def test_env_example_contains_no_secret_values() -> None:
    leaked = [
        key
        for key, value in _assignments(ROOT / ".env.example")
        if SECRET_KEY_RE.search(key) and value
    ]
    assert leaked == [], f"real values found for: {leaked}"


def test_env_example_lists_every_platform_credential() -> None:
    keys = {key for key, _ in _assignments(ROOT / ".env.example")}
    assert {
        "MASTODON_ACCESS_TOKEN",
        "BLUESKY_APP_PASSWORD",
        "TELEGRAM_BOT_TOKEN",
        "LINKEDIN_ACCESS_TOKEN",
        "DISCORD_WEBHOOK_URL",
    } <= keys


def test_gitignore_excludes_the_private_env_file() -> None:
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in lines
    assert "!.env.example" in lines