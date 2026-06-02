"""Tests for upload_client.marker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from upload_client.marker import (
    MARKER_NAME,
    CardMarker,
    MarkerError,
    has_marker,
    read_marker,
)


def _write(root: Path, marker: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / MARKER_NAME).write_text(json.dumps(marker), encoding="utf-8")
    return root


def _valid() -> dict:
    return {
        "version": 1,
        "card_uuid": "abc123",
        "tournament_id": 42,
        "tournament_name": "Seetal 2026",
        "discipline": "Einzel",
        "table": "ET01",
    }


def test_has_marker(tmp_path: Path) -> None:
    assert has_marker(tmp_path) is False
    _write(tmp_path, _valid())
    assert has_marker(tmp_path) is True


def test_read_marker_ok(tmp_path: Path) -> None:
    _write(tmp_path, _valid())
    marker = read_marker(tmp_path)
    assert isinstance(marker, CardMarker)
    assert marker.card_uuid == "abc123"
    assert marker.tournament_id == 42
    assert marker.discipline == "Einzel"
    assert marker.table == "ET01"


def test_read_marker_missing(tmp_path: Path) -> None:
    with pytest.raises(MarkerError):
        read_marker(tmp_path)


def test_read_marker_lowercase_table_is_normalised(tmp_path: Path) -> None:
    m = _valid()
    m["table"] = "et07"
    _write(tmp_path, m)
    assert read_marker(tmp_path).table == "ET07"


def test_read_marker_bad_table_rejected(tmp_path: Path) -> None:
    m = _valid()
    m["table"] = "E1"
    _write(tmp_path, m)
    with pytest.raises(MarkerError):
        read_marker(tmp_path)


def test_read_marker_bad_discipline_rejected(tmp_path: Path) -> None:
    m = _valid()
    m["discipline"] = "Mixed"
    _write(tmp_path, m)
    with pytest.raises(MarkerError):
        read_marker(tmp_path)


def test_read_marker_missing_field_rejected(tmp_path: Path) -> None:
    m = _valid()
    del m["card_uuid"]
    _write(tmp_path, m)
    with pytest.raises(MarkerError):
        read_marker(tmp_path)


def test_read_marker_corrupt_json(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / MARKER_NAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(MarkerError):
        read_marker(tmp_path)
