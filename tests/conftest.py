"""Shared test fixtures."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def doppel_config_dict(tmp_path: Path) -> dict:
    """Minimal valid config dict for Doppel, with paths under tmp_path."""
    return {
        "discipline": "Doppel",
        "enabled": True,
        "paths": {
            "eingang": str(tmp_path / "eingang_doppel"),
            "work": str(tmp_path / "work_doppel"),
            "output": str(tmp_path / "output_doppel"),
            "logs": str(tmp_path / "logs"),
        },
        "filename_constants": {
            "jahr": "2026",
            "sts_nummer": "STS2",
            "turniername": "Seetal",
            "disziplin": "Doppel",
            "part": "",
        },
        "ffmpeg": {"max_workers": 2, "max_files_per_folder": 24},
        "youtube": {},
    }


@pytest.fixture
def doppel_config_path(tmp_path: Path, doppel_config_dict: dict) -> Path:
    path = tmp_path / "config_doppel.json"
    path.write_text(json.dumps(doppel_config_dict), encoding="utf-8")
    return path


def make_mp4(folder: Path, name: str, content: bytes = b"\x00") -> Path:
    """Create a tiny dummy MP4 (just bytes, FFmpeg is mocked in tests)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(content)
    return path
