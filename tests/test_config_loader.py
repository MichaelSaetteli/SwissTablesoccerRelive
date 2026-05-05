"""Tests for pipeline.config_loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.config_loader import (
    ConfigError,
    FilenameConstants,
    build_output_filename,
    ensure_pipeline_dirs,
    load_config,
    parse_folder_name,
)


# ---- load_config ----------------------------------------------------------

def test_load_config_happy_path(doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)
    assert cfg.discipline == "Doppel"
    assert cfg.enabled is True
    assert cfg.max_workers == 2
    assert cfg.max_files_per_folder == 24
    assert cfg.source_path == doppel_config_path
    assert cfg.filename_constants.k1 == "2026"


def test_load_config_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.json")


def test_load_config_missing_top_level_key(tmp_path: Path,
                                           doppel_config_dict: dict) -> None:
    del doppel_config_dict["filename_constants"]
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(doppel_config_dict), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_missing_path_key(tmp_path: Path,
                                      doppel_config_dict: dict) -> None:
    del doppel_config_dict["paths"]["eingang"]
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(doppel_config_dict), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_invalid_discipline(tmp_path: Path,
                                        doppel_config_dict: dict) -> None:
    doppel_config_dict["discipline"] = "Mixed"
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(doppel_config_dict), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_ensure_pipeline_dirs_creates_all(doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)
    for directory in cfg.paths.all():
        assert not directory.exists()
    ensure_pipeline_dirs(cfg)
    for directory in cfg.paths.all():
        assert directory.is_dir()


# ---- parse_folder_name ----------------------------------------------------

def test_parse_folder_name_4chars() -> None:
    assert parse_folder_name("ET03") == ("T03", None)
    assert parse_folder_name("ET18") == ("T18", None)


def test_parse_folder_name_6chars() -> None:
    assert parse_folder_name("ET03_1") == ("T03", "1")
    assert parse_folder_name("ET18_2") == ("T18", "2")


def test_parse_folder_name_invalid() -> None:
    with pytest.raises(ValueError):
        parse_folder_name("XX99")
    with pytest.raises(ValueError):
        parse_folder_name("ET3")
    with pytest.raises(ValueError):
        parse_folder_name("ET03-1")


# ---- build_output_filename ------------------------------------------------

def _consts(**overrides) -> FilenameConstants:
    base = dict(k1="2026", k2="STS02", k4="Doppel", k5="Part", k6="")
    base.update(overrides)
    return FilenameConstants(**base)


def test_build_output_filename_briefing_example() -> None:
    """Matches the example in PROJEKT_BRIEFING.md section 1."""
    name = build_output_filename(_consts(), "ET03_1")
    assert name == "2026 STS02 T03 Doppel Part 1.mp4"


def test_build_output_filename_4char_short_schema() -> None:
    name = build_output_filename(_consts(), "ET05")
    # k6 empty + variableB absent -> filtered out, no double spaces
    assert name == "2026 STS02 T05 Doppel Part.mp4"


def test_build_output_filename_filters_empty_constants() -> None:
    name = build_output_filename(_consts(k5=""), "ET03_2")
    assert name == "2026 STS02 T03 Doppel 2.mp4"
    assert "  " not in name


def test_build_output_filename_includes_k6_when_set() -> None:
    name = build_output_filename(_consts(k6="Final"), "ET03_3")
    assert name == "2026 STS02 T03 Doppel Part Final 3.mp4"
