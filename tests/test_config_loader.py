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
    assert cfg.filename_constants.jahr == "2026"
    assert cfg.filename_constants.turniername == "Seetal"


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


def test_load_config_missing_constant_key(tmp_path: Path,
                                          doppel_config_dict: dict) -> None:
    del doppel_config_dict["filename_constants"]["turniername"]
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

def test_parse_folder_name_no_split() -> None:
    assert parse_folder_name("ET01") == ("T01", None)
    assert parse_folder_name("ET03") == ("T03", None)
    assert parse_folder_name("ET18") == ("T18", None)


def test_parse_folder_name_with_split() -> None:
    assert parse_folder_name("ET03_1") == ("T03", "1")
    assert parse_folder_name("ET01_2") == ("T01", "2")
    assert parse_folder_name("ET18_3") == ("T18", "3")


def test_parse_folder_name_invalid() -> None:
    with pytest.raises(ValueError):
        parse_folder_name("XX99")
    with pytest.raises(ValueError):
        parse_folder_name("ET3")
    with pytest.raises(ValueError):
        parse_folder_name("ET03-1")


# ---- build_output_filename ------------------------------------------------

def _consts(**overrides) -> FilenameConstants:
    base = dict(
        jahr="2026",
        sts_nummer="STS2",
        turniername="Seetal",
        disziplin="Doppel",
        part="",
    )
    base.update(overrides)
    return FilenameConstants(**base)


def test_build_output_filename_operator_example() -> None:
    """Matches the operator's example: '2026 STS2 T01 Seetal Doppel'."""
    name = build_output_filename(_consts(), "ET01")
    assert name == "2026 STS2 T01 Seetal Doppel.mp4"


def test_build_output_filename_with_user_part() -> None:
    name = build_output_filename(_consts(part="Part 1"), "ET01")
    assert name == "2026 STS2 T01 Seetal Doppel Part 1.mp4"


def test_build_output_filename_split_overrides_user_part_option_b() -> None:
    """Option B: split index becomes 'Part N' and overrides any user part."""
    consts = _consts(part="Part 99")  # user value should be ignored
    assert (
        build_output_filename(consts, "ET01_1")
        == "2026 STS2 T01 Seetal Doppel Part 1.mp4"
    )
    assert (
        build_output_filename(consts, "ET01_2")
        == "2026 STS2 T01 Seetal Doppel Part 2.mp4"
    )


def test_build_output_filename_split_with_empty_user_part() -> None:
    consts = _consts(part="")
    assert (
        build_output_filename(consts, "ET05_3")
        == "2026 STS2 T05 Seetal Doppel Part 3.mp4"
    )


def test_build_output_filename_filters_empty_constants() -> None:
    name = build_output_filename(_consts(turniername=""), "ET03")
    assert name == "2026 STS2 T03 Doppel.mp4"
    assert "  " not in name


def test_build_output_filename_einzel_discipline() -> None:
    name = build_output_filename(_consts(disziplin="Einzel"), "ET07")
    assert name == "2026 STS2 T07 Seetal Einzel.mp4"
