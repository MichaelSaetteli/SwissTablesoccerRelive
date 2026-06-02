"""Tests for upload_client.card_scanner."""

from __future__ import annotations

import json
from pathlib import Path

from upload_client.card_scanner import (
    CARD_EMPTY,
    CARD_NO_MARKER,
    CARD_READY,
    CARD_WRONG_TOURNAMENT,
    merge_scans,
    scan_card,
    scan_mounts,
)


def _make_card(root: Path, *, uuid="u1", tid=42, table="ET01",
               files=None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    marker = {
        "version": 1, "card_uuid": uuid, "tournament_id": tid,
        "tournament_name": "T", "discipline": "Einzel", "table": table,
    }
    (root / ".sts-card.json").write_text(json.dumps(marker), encoding="utf-8")
    for name, content in (files or {"v.mp4": b"x" * 10}).items():
        (root / name).write_bytes(content)
    return root


def test_ready_card(tmp_path: Path) -> None:
    card = scan_card(_make_card(tmp_path / "c"), active_tournament_id=42)
    assert card.status == CARD_READY
    assert card.uploadable is True
    assert card.manifest.total_files == 1


def test_no_marker(tmp_path: Path) -> None:
    (tmp_path / "c").mkdir()
    card = scan_card(tmp_path / "c", active_tournament_id=42)
    assert card.status == CARD_NO_MARKER
    assert card.uploadable is False
    assert card.reason


def test_wrong_tournament_locked(tmp_path: Path) -> None:
    _make_card(tmp_path / "c", tid=99)
    card = scan_card(tmp_path / "c", active_tournament_id=42)
    assert card.status == CARD_WRONG_TOURNAMENT
    assert card.uploadable is False
    assert card.marker is not None  # still shown


def test_empty_card(tmp_path: Path) -> None:
    root = tmp_path / "c"
    root.mkdir()
    (root / ".sts-card.json").write_text(json.dumps({
        "version": 1, "card_uuid": "u", "tournament_id": 42,
        "tournament_name": "T", "discipline": "Einzel", "table": "ET01",
    }), encoding="utf-8")
    card = scan_card(root, active_tournament_id=42)
    assert card.status == CARD_EMPTY
    assert card.uploadable is False


def test_scan_mounts_dedupes_same_card_uuid(tmp_path: Path) -> None:
    # Same card in two different slots -> one inventory entry.
    _make_card(tmp_path / "slotA", uuid="same")
    _make_card(tmp_path / "slotB", uuid="same")
    inv = scan_mounts([tmp_path / "slotA", tmp_path / "slotB"],
                      active_tournament_id=42)
    assert len(inv) == 1


def test_merge_is_additive_and_keeps_existing(tmp_path: Path) -> None:
    _make_card(tmp_path / "a", uuid="a")
    _make_card(tmp_path / "b", uuid="b")
    first = scan_mounts([tmp_path / "a"], active_tournament_id=42)
    second = scan_mounts([tmp_path / "b"], active_tournament_id=42)
    merged = merge_scans(first, second)
    assert set(merged.keys()) == {"uuid:a", "uuid:b"}

    # Re-scanning an already-present card does NOT replace the entry.
    original = merged["uuid:a"]
    rescan = scan_mounts([tmp_path / "a"], active_tournament_id=42)
    merged2 = merge_scans(merged, rescan)
    assert merged2["uuid:a"] is original
