"""Tests for the bootstrapping helpers in web.app (Schritt 5).

Covers config discovery and the env-var precedence the Docker entrypoint
relies on. The actual ``_main`` function is not invoked end-to-end (it
would block on a real WSGI server); we exercise the helpers directly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator

import pytest

from web.app import _load_configs_from, _resolve_data_dir


def _write_config(path: Path, discipline: str) -> None:
    path.write_text(json.dumps({
        "discipline": discipline,
        "enabled": True,
        "paths": {
            "eingang": str(path.parent / f"eingang_{discipline.lower()}"),
            "work": str(path.parent / f"work_{discipline.lower()}"),
            "output": str(path.parent / f"output_{discipline.lower()}"),
            "logs": str(path.parent / "logs"),
        },
        "filename_constants": {
            "jahr": "2026", "sts_nummer": "STS2", "turniername": "Seetal",
            "disziplin": discipline, "part": "",
        },
        "ffmpeg": {"max_workers": 2, "max_files_per_folder": 24},
        "youtube": {},
    }), encoding="utf-8")


@pytest.fixture
def env_var(monkeypatch) -> Iterator[None]:
    """Ensure VIDEO_PIPELINE_DATA_DIR is unset between tests."""
    monkeypatch.delenv("VIDEO_PIPELINE_DATA_DIR", raising=False)
    yield
    monkeypatch.delenv("VIDEO_PIPELINE_DATA_DIR", raising=False)


# ---------------------------------------------------------------------------
# _resolve_data_dir
# ---------------------------------------------------------------------------

def test_resolve_data_dir_uses_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VIDEO_PIPELINE_DATA_DIR", str(tmp_path))
    assert _resolve_data_dir() == tmp_path


def test_resolve_data_dir_falls_back_to_repo_config(env_var) -> None:
    resolved = _resolve_data_dir()
    assert resolved.name == "config"
    assert resolved.parent.name == "SwissTablesoccerRelive"


# ---------------------------------------------------------------------------
# _load_configs_from
# ---------------------------------------------------------------------------

def test_load_configs_from_loads_both_when_present(tmp_path: Path) -> None:
    _write_config(tmp_path / "config_doppel.json", "Doppel")
    _write_config(tmp_path / "config_einzel.json", "Einzel")

    configs = _load_configs_from(tmp_path)
    assert sorted(configs.keys()) == ["Doppel", "Einzel"]
    assert configs["Doppel"].discipline == "Doppel"
    assert configs["Einzel"].discipline == "Einzel"


def test_load_configs_from_partial(tmp_path: Path) -> None:
    """Only one config -> only that discipline is loaded (briefing s.4)."""
    _write_config(tmp_path / "config_doppel.json", "Doppel")
    configs = _load_configs_from(tmp_path)
    assert list(configs.keys()) == ["Doppel"]


def test_load_configs_from_empty(tmp_path: Path) -> None:
    assert _load_configs_from(tmp_path) == {}


def test_load_configs_from_missing_dir(tmp_path: Path) -> None:
    """Non-existent dir -> empty mapping, not an error."""
    missing = tmp_path / "no_such"
    assert _load_configs_from(missing) == {}
