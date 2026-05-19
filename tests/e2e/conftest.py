"""Shared fixtures for the e2e suite.

E2e tests use a *real* ffmpeg invocation rather than the FakeRunner the
unit tests rely on, so they can catch bugs in our concat-demuxer logic
that unit tests would mask.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterator

import pytest

# Make the repo importable when pytest is invoked from anywhere.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


FIXTURE = Path(__file__).parent / "fixtures" / "sample_clip.mp4"


@pytest.fixture(scope="session", autouse=True)
def _require_ffmpeg() -> None:
    """Skip the entire e2e suite if ffmpeg is not on PATH."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed - e2e tests need a real ffmpeg",
                    allow_module_level=True)


@pytest.fixture(scope="session", autouse=True)
def _require_fixture() -> None:
    if not FIXTURE.is_file():
        pytest.skip(
            f"missing fixture {FIXTURE} - regenerate per tests/e2e/README_e2e.md",
            allow_module_level=True,
        )


def _make_config(tmp_path: Path, discipline: str) -> Path:
    """Write a temp config_<discipline>.json with paths under tmp_path."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "discipline": discipline,
        "enabled": True,
        "paths": {
            "eingang": str(tmp_path / f"eingang_{discipline.lower()}"),
            "work":    str(tmp_path / f"work_{discipline.lower()}"),
            "output":  str(tmp_path / f"output_{discipline.lower()}"),
            "logs":    str(tmp_path / "logs"),
        },
        "filename_constants": {
            "jahr": "2026",
            "sts_nummer": "STS2",
            "turniername": "E2eTest",
            "disziplin": discipline,
            "part": "",
        },
        "ffmpeg": {"max_workers": 2, "max_files_per_folder": 24},
        "youtube": {},
    }
    path = cfg_dir / f"config_{discipline.lower()}.json"
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def doppel_config_path(tmp_path: Path) -> Path:
    return _make_config(tmp_path, "Doppel")


@pytest.fixture
def einzel_config_path(tmp_path: Path) -> Path:
    return _make_config(tmp_path, "Einzel")


def seed_camera_folder(eingang: Path, folder_name: str, n_files: int = 3) -> Path:
    """Place ``n_files`` copies of the fixture into ``eingang/<folder_name>/``."""
    target = eingang / folder_name
    target.mkdir(parents=True, exist_ok=True)
    for i in range(1, n_files + 1):
        dst = target / f"src_{i:03d}.mp4"
        shutil.copy2(FIXTURE, dst)
    return target


def ffprobe_duration(path: Path) -> float:
    """Return the duration of a media file in seconds (float)."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())
