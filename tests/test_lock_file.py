"""The pinned requirements must describe this project's environment and nothing else."""

import re
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "requirements-lock.txt"
PIN_RE = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9.!+_-]+)")
REQUIREMENT_RE = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*(.*)")


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _active_lines(path: Path) -> list[str]:
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _lock_pins() -> list[tuple[str, str]]:
    return [
        (_normalise(match.group(1)), match.group(2))
        for line in _active_lines(LOCK)
        if (match := PIN_RE.fullmatch(line))
    ]


def _declared_requirements() -> dict[str, SpecifierSet]:
    declared: dict[str, SpecifierSet] = {}
    for filename in ("requirements.txt", "requirements-dev.txt"):
        for line in _active_lines(ROOT / filename):
            if line.startswith("-"):
                continue
            match = REQUIREMENT_RE.fullmatch(line)
            assert match, f"cannot parse requirement: {line!r}"
            declared[_normalise(match.group(1))] = SpecifierSet(match.group(2).strip())
    return declared


def test_lock_file_contains_only_exact_pins() -> None:
    bad = [line for line in _active_lines(LOCK) if not PIN_RE.fullmatch(line)]

    assert bad == [], f"requirements-lock.txt must contain only name==version lines: {bad}"


def test_lock_file_has_no_duplicate_packages() -> None:
    names = [name for name, _ in _lock_pins()]

    assert len(names) == len(set(names))


def test_every_direct_dependency_is_pinned() -> None:
    pinned = {name for name, _ in _lock_pins()}
    missing = sorted(set(_declared_requirements()) - pinned)

    assert missing == [], f"direct dependencies missing from the lock file: {missing}"


def test_pinned_versions_satisfy_the_declared_ranges() -> None:
    pins = dict(_lock_pins())
    outside = [
        f"{name}=={pins[name]} (needs {spec})"
        for name, spec in _declared_requirements().items()
        if name in pins and not spec.contains(Version(pins[name]), prereleases=True)
    ]

    assert outside == [], f"pinned versions outside the declared ranges: {outside}"