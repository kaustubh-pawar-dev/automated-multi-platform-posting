"""Repository hygiene: no real credentials in the template, and complete documentation."""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SECRET_KEY_RE = re.compile(r"(TOKEN|PASSWORD|SECRET|WEBHOOK|API_KEY)", re.IGNORECASE)
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")

README_SECTIONS = [
    "Project overview",
    "Problem statement",
    "Key features",
    "Technology stack",
    "Architecture",
    "Folder structure",
    "Platform API research",
    "Platform integration status",
    "Authentication requirements",
    "Installation",
    "Virtual environment setup",
    "Environment variable configuration",
    "Running the application",
    "Running automated tests",
    "API endpoint documentation",
    "Sample request",
    "Successful response example",
    "Partial failure example",
    "Validation error examples",
    "Screenshots",
    "Known limitations",
    "Security considerations",
    "Future improvements",
]

REQUIRED_DOCUMENTS = [
    "README.md",
    "LICENSE",
    "docs/ARCHITECTURE.md",
    "docs/API_EXAMPLES.md",
    "docs/PLATFORM_API_RESEARCH.md",
    "docs/EXECUTION_EVIDENCE.md",
]


def _assignments(path: Path) -> Iterator[tuple[str, str]]:
    """Yield (KEY, value) for every active assignment line in an env-style file."""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        yield key.strip(), value.strip()


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


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


@pytest.mark.parametrize("relative_path", REQUIRED_DOCUMENTS)
def test_required_document_exists_and_is_not_empty(relative_path: str) -> None:
    path = ROOT / relative_path
    assert path.is_file(), f"{relative_path} is missing"
    assert path.read_text(encoding="utf-8").strip(), f"{relative_path} is empty"


def test_readme_has_every_required_section_in_order() -> None:
    text = _readme()
    positions = []
    for number, title in enumerate(README_SECTIONS, start=1):
        heading = f"## {number}. {title}"
        assert heading in text, f"README is missing the heading: {heading}"
        positions.append(text.index(heading))
    assert positions == sorted(positions)


def test_readme_images_exist() -> None:
    images = [target for target in IMAGE_RE.findall(_readme()) if "://" not in target]
    assert images, "the README should embed at least one screenshot"
    missing = [target for target in images if not (ROOT / target).is_file()]
    assert missing == [], f"README links to images that do not exist: {missing}"


def test_readme_has_no_unreplaced_placeholders() -> None:
    assert "YOUR_GITHUB_USERNAME" not in _readme()