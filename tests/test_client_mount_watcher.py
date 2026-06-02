"""Tests for upload_client.mount_watcher (poll-based insert/remove diff)."""

from __future__ import annotations

from pathlib import Path

from upload_client.mount_watcher import MountWatcher


def test_first_poll_reports_everything_inserted(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    w = MountWatcher(list_roots=lambda: [a, b])
    change = w.poll()
    assert set(change.inserted) == {a, b}
    assert change.removed == []
    assert change.changed is True


def test_no_change_on_steady_state(tmp_path: Path) -> None:
    a = tmp_path / "a"
    w = MountWatcher(list_roots=lambda: [a])
    w.poll()
    change = w.poll()
    assert change.inserted == [] and change.removed == []
    assert change.changed is False


def test_removal_then_reinsertion(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    current = {"roots": [a, b]}
    w = MountWatcher(list_roots=lambda: current["roots"])
    w.poll()  # baseline a, b

    current["roots"] = [a]  # b pulled
    change = w.poll()
    assert change.removed == [b]
    assert change.inserted == []

    current["roots"] = [a, b]  # b re-inserted
    change = w.poll()
    assert change.inserted == [b]
    assert change.removed == []
    assert w.present == sorted([a, b])
