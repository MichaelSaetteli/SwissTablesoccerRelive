"""Tests for upload_client.card_scanner (label/discipline ingest model)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

from upload_client.card_scanner import (
    CARD_EMPTY,
    CARD_NO_TABLE,
    CARD_NO_TOURNAMENT,
    CARD_READY,
    merge_scans,
    scan_card,
    scan_mounts,
)

ACTIVE = {
    "Einzel": {"id": 42, "name": "TEST"},
    "Doppel": {"id": 7, "name": "TEST"},
}


def _reader(label: Optional[str], serial: Optional[str] = "S1"):
    return lambda root: (label, serial)


def _card(root: Path, subdirs: Dict[str, list]) -> Path:
    """Create DCIM/<sub>/<files> on *root*. subdirs: {name: [filenames]}."""
    for sub, names in subdirs.items():
        for name in names:
            p = root / "DCIM" / sub / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x" * 10)
    return root


def test_ready_card_from_label(tmp_path: Path) -> None:
    root = _card(tmp_path / "c", {"100PANA": ["a.mp4", "b.mp4"]})
    card = scan_card(root, discipline="Einzel", active_tournaments=ACTIVE,
                     volume_reader=_reader("E01"))
    assert card.status == CARD_READY
    assert card.uploadable
    assert card.marker.table == "ET01"
    assert card.marker.discipline == "Einzel"
    assert card.marker.tournament_id == 42
    assert card.manifest.total_files == 2


def test_no_table_when_label_has_no_number(tmp_path: Path) -> None:
    root = _card(tmp_path / "c", {"100PANA": ["a.mp4"]})
    card = scan_card(root, discipline="Einzel", active_tournaments=ACTIVE,
                     volume_reader=_reader("NO NAME"))
    assert card.status == CARD_NO_TABLE
    assert not card.uploadable
    assert card.reason


def test_no_active_tournament_for_discipline(tmp_path: Path) -> None:
    root = _card(tmp_path / "c", {"100PANA": ["a.mp4"]})
    card = scan_card(root, discipline="Doppel",
                     active_tournaments={"Einzel": {"id": 42, "name": "T"}},
                     volume_reader=_reader("D01"))
    assert card.status == CARD_NO_TOURNAMENT


def test_empty_card_no_video(tmp_path: Path) -> None:
    root = tmp_path / "c"
    root.mkdir()
    (root / "DCIM").mkdir()
    card = scan_card(root, discipline="Einzel", active_tournaments=ACTIVE,
                     volume_reader=_reader("E01"))
    assert card.status == CARD_EMPTY


def test_discipline_hint_from_label_when_none_given(tmp_path: Path) -> None:
    root = _card(tmp_path / "c", {"100PANA": ["a.mp4"]})
    card = scan_card(root, discipline=None, active_tournaments=ACTIVE,
                     volume_reader=_reader("D03"))
    assert card.status == CARD_READY
    assert card.marker.discipline == "Doppel"  # hinted by leading 'D'
    assert card.marker.table == "ET03"


def test_stale_alarm_selects_newest_cluster(tmp_path: Path) -> None:
    root = _card(tmp_path / "c", {"OLD": ["old.mp4"], "NEW": ["new.mp4"]})
    old = datetime(2026, 1, 1, 9, 0)
    new = datetime(2026, 5, 22, 17, 0)
    created = {"OLD": old, "NEW": new}
    card = scan_card(root, discipline="Einzel", active_tournaments=ACTIVE,
                     volume_reader=_reader("E01"),
                     created_fn=lambda p: created[p.name])
    assert card.status == CARD_READY
    assert card.stale_alarm is True
    assert card.selected_subdirs == ("NEW",)          # newest only
    names = [Path(f.relative_name).name for f in card.manifest.files]
    assert names == ["new.mp4"]


def test_close_folders_no_alarm_all_selected(tmp_path: Path) -> None:
    root = _card(tmp_path / "c", {"A": ["a.mp4"], "B": ["b.mp4"]})
    base = datetime(2026, 5, 22, 17, 0)
    created = {"A": base, "B": base + timedelta(minutes=1)}
    card = scan_card(root, discipline="Einzel", active_tournaments=ACTIVE,
                     volume_reader=_reader("E01"),
                     created_fn=lambda p: created[p.name])
    assert card.stale_alarm is False
    assert set(card.selected_subdirs) == {"A", "B"}
    assert card.manifest.total_files == 2


def test_subdir_override_wins(tmp_path: Path) -> None:
    root = _card(tmp_path / "c", {"OLD": ["old.mp4"], "NEW": ["new.mp4"]})
    old = datetime(2026, 1, 1, 9, 0)
    new = datetime(2026, 5, 22, 17, 0)
    created = {"OLD": old, "NEW": new}
    card = scan_card(root, discipline="Einzel", active_tournaments=ACTIVE,
                     volume_reader=_reader("E01"),
                     created_fn=lambda p: created[p.name],
                     subdir_selection={"OLD"})
    names = [Path(f.relative_name).name for f in card.manifest.files]
    assert names == ["old.mp4"]


def test_scan_mounts_dedupes_same_serial(tmp_path: Path) -> None:
    a = _card(tmp_path / "slotA", {"100": ["a.mp4"]})
    b = _card(tmp_path / "slotB", {"100": ["a.mp4"]})
    inv = scan_mounts([a, b], discipline="Einzel", active_tournaments=ACTIVE,
                      volume_reader=_reader("E01", serial="SHARED"))
    assert len(inv) == 1


def test_scan_mounts_discipline_override_per_card(tmp_path: Path) -> None:
    from upload_client.card_identity import card_uuid_from
    root = _card(tmp_path / "c", {"100": ["a.mp4"]})
    uuid = card_uuid_from(serial="S5", label="E05")
    inv = scan_mounts([root], discipline="Einzel", active_tournaments=ACTIVE,
                      discipline_overrides={uuid: "Doppel"},
                      volume_reader=_reader("E05", serial="S5"))
    (card,) = inv.values()
    assert card.marker.discipline == "Doppel"
    assert card.marker.tournament_id == 7


def test_merge_is_additive(tmp_path: Path) -> None:
    a = _card(tmp_path / "a", {"100": ["a.mp4"]})
    first = scan_mounts([a], discipline="Einzel", active_tournaments=ACTIVE,
                        volume_reader=_reader("E01", serial="A"))
    original = next(iter(first.values()))
    rescan = scan_mounts([a], discipline="Einzel", active_tournaments=ACTIVE,
                         volume_reader=_reader("E01", serial="A"))
    merged = merge_scans(first, rescan)
    assert merged[original.key] is original
