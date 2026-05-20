"""Guard test: the Dockerfile must ship every Python package.

This catches the class of bug where a new top-level package (db/,
archive/, ...) is added to the codebase but the Dockerfile's COPY
list is not updated - the image then silently lacks the module and
every endpoint that imports it 500s at runtime with ModuleNotFoundError,
while unit tests (which run against the source tree) stay green.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _top_level_packages() -> list[str]:
    """Every importable top-level package except the test tree."""
    return sorted(
        p.name
        for p in ROOT.iterdir()
        if p.is_dir()
        and (p / "__init__.py").is_file()
        and p.name != "tests"
    )


def test_dockerfile_copies_every_package() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    missing = [
        pkg for pkg in _top_level_packages()
        if f"COPY {pkg}/" not in dockerfile
    ]
    assert not missing, (
        f"Dockerfile is missing COPY for: {missing}. "
        f"The runtime image would lack these modules and every endpoint "
        f"importing them would 500 with ModuleNotFoundError."
    )


def test_known_packages_present() -> None:
    """Sanity: the packages we expect are actually detected."""
    pkgs = _top_level_packages()
    for expected in ("pipeline", "watcher", "web", "youtube", "db", "archive"):
        assert expected in pkgs, f"expected package {expected!r} not found"
