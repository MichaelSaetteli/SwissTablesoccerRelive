"""Tests for upload_client.dcim (sub-folder date clustering + stale alarm)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict

from upload_client.dcim import (
    DcimFolder,
    cluster,
    default_selected_names,
    has_stale_alarm,
    scan_dcim,
)


def _touch(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _fixed_dates(mapping: Dict[str, datetime]):
    """A created_fn returning a canned datetime per folder name."""
    return lambda p: mapping[p.name]


def test_scan_dcim_lists_only_video_folders(tmp_path: Path) -> None:
    _touch(tmp_path / "DCIM" / "100PANA" / "a.mp4", b"x" * 5)
    _touch(tmp_path / "DCIM" / "100PANA" / "b.mp4", b"y" * 7)
    _touch(tmp_path / "DCIM" / "EMPTYDIR" / "notes.txt", b"junk")  # no video
    _touch(tmp_path / "DCIM" / "BACKUP.HST", b"junk")              # loose file
    base = datetime(2026, 5, 22, 17, 0)
    folders = scan_dcim(tmp_path, created_fn=_fixed_dates({"100PANA": base}))
    assert [f.name for f in folders] == ["100PANA"]
    assert folders[0].video_count == 2
    assert folders[0].video_bytes == 12


def test_no_dcim_returns_empty(tmp_path: Path) -> None:
    assert scan_dcim(tmp_path) == []


def _folder(name: str, dt: datetime) -> DcimFolder:
    return DcimFolder(path=Path(name), name=name, created=dt,
                      video_count=1, video_bytes=1)


def test_close_folders_are_one_cluster_no_alarm() -> None:
    base = datetime(2026, 5, 22, 17, 31)
    folders = [
        _folder("227XDPHH", base),
        _folder("228XDPHH", base + timedelta(minutes=1)),
    ]
    assert len(cluster(folders)) == 1
    assert has_stale_alarm(folders) is False
    assert default_selected_names(folders) == {"227XDPHH", "228XDPHH"}


def test_far_apart_folders_alarm_and_select_newest() -> None:
    old = datetime(2026, 1, 10, 9, 0)
    new = datetime(2026, 5, 22, 17, 0)
    folders = [
        _folder("OLD1", old),
        _folder("NEW1", new),
        _folder("NEW2", new + timedelta(hours=1)),
    ]
    clusters = cluster(folders)
    assert len(clusters) == 2
    assert has_stale_alarm(folders) is True
    # Default: only the newest cluster is on.
    assert default_selected_names(folders) == {"NEW1", "NEW2"}


def test_exactly_three_days_is_still_one_cluster() -> None:
    # "more than 3 days" -> a 3-day gap is NOT an alarm.
    a = datetime(2026, 5, 1, 12, 0)
    folders = [_folder("A", a), _folder("B", a + timedelta(days=3))]
    assert has_stale_alarm(folders) is False


def test_just_over_three_days_alarms() -> None:
    a = datetime(2026, 5, 1, 12, 0)
    folders = [_folder("A", a),
               _folder("B", a + timedelta(days=3, minutes=1))]
    assert has_stale_alarm(folders) is True
