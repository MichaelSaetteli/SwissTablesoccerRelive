"""Tests for pipeline.config_loader.save_config (added in Schritt 3)."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.config_loader import load_config, save_config


def test_save_config_round_trip(doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)
    cfg.youtube["tournament_name"] = "Test 2026"
    cfg.youtube["playlist_create_new"] = False
    save_config(cfg)

    reloaded = load_config(doppel_config_path)
    assert reloaded.youtube["tournament_name"] == "Test 2026"
    assert reloaded.youtube["playlist_create_new"] is False
    # Other fields stay intact
    assert reloaded.discipline == cfg.discipline
    assert reloaded.filename_constants.k1 == cfg.filename_constants.k1


def test_save_config_atomic(doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)
    save_config(cfg)
    leftovers = list(doppel_config_path.parent.glob("*.tmp"))
    assert leftovers == []


def test_save_config_preserves_path_strings(doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)
    save_config(cfg)
    raw = json.loads(doppel_config_path.read_text(encoding="utf-8"))
    # Path values must be strings (not POSIX path repr objects).
    for key, value in raw["paths"].items():
        assert isinstance(value, str), key
