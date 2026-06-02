"""Tests for the prepare_card admin CLI."""

from __future__ import annotations

import json

import pytest

from scripts.prepare_card import _make_marker, _write_marker, main


def test_make_marker_normalises_table_to_upper():
    marker = _make_marker(
        tournament_id=1, tournament_name="X",
        discipline="Einzel", table="et01",
    )
    assert marker["table"] == "ET01"


def test_make_marker_rejects_bad_table():
    with pytest.raises(SystemExit, match="ETxx"):
        _make_marker(
            tournament_id=1, tournament_name="X",
            discipline="Einzel", table="E01",
        )


def test_make_marker_rejects_bad_discipline():
    with pytest.raises(SystemExit, match="discipline"):
        _make_marker(
            tournament_id=1, tournament_name="X",
            discipline="Mixed", table="ET01",
        )


def test_make_marker_fresh_uuid_each_call():
    a = _make_marker(
        tournament_id=1, tournament_name="X",
        discipline="Einzel", table="ET01",
    )
    b = _make_marker(
        tournament_id=1, tournament_name="X",
        discipline="Einzel", table="ET01",
    )
    assert a["card_uuid"] != b["card_uuid"]


def test_write_marker_creates_file(tmp_path):
    marker = _make_marker(
        tournament_id=42, tournament_name="Seetal 2026",
        discipline="Einzel", table="ET01",
    )
    target = _write_marker(tmp_path, marker, force=False)
    assert target == tmp_path / ".sts-card.json"
    on_disk = json.loads(target.read_text())
    assert on_disk["table"] == "ET01"
    assert on_disk["tournament_id"] == 42


def test_write_marker_refuses_overwrite_without_force(tmp_path):
    marker = _make_marker(
        tournament_id=1, tournament_name="X",
        discipline="Einzel", table="ET01",
    )
    _write_marker(tmp_path, marker, force=False)
    with pytest.raises(SystemExit, match="already exists"):
        _write_marker(tmp_path, marker, force=False)


def test_write_marker_overwrites_with_force(tmp_path):
    a = _make_marker(
        tournament_id=1, tournament_name="X",
        discipline="Einzel", table="ET01",
    )
    b = _make_marker(
        tournament_id=2, tournament_name="Y",
        discipline="Doppel", table="ET02",
    )
    _write_marker(tmp_path, a, force=False)
    _write_marker(tmp_path, b, force=True)
    on_disk = json.loads((tmp_path / ".sts-card.json").read_text())
    assert on_disk["table"] == "ET02"


def test_main_end_to_end(tmp_path):
    rc = main([
        "--card", str(tmp_path),
        "--tournament-id", "42",
        "--tournament-name", "Seetal 2026 STS2",
        "--discipline", "Einzel",
        "--table", "ET01",
    ])
    assert rc == 0
    on_disk = json.loads((tmp_path / ".sts-card.json").read_text())
    assert on_disk["discipline"] == "Einzel"
