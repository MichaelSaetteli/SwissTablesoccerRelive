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
    # confirm=1 keeps this the classic "one missing poll = removed" check.
    w = MountWatcher(list_roots=lambda: current["roots"], removal_confirmations=1)
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


def test_transient_miss_does_not_report_removal(tmp_path: Path) -> None:
    """A heavy-read stat glitch (one missing poll) must NOT fire a removal."""
    a = tmp_path / "a"
    current = {"roots": [a]}
    w = MountWatcher(list_roots=lambda: current["roots"], removal_confirmations=3)
    w.poll()  # baseline

    current["roots"] = []          # glitch: card briefly unstattable
    assert w.poll().removed == []  # 1 miss - below threshold
    current["roots"] = [a]         # card answers again
    assert w.poll().removed == []  # recovered
    # Still considered present, miss counter reset.
    assert w.present == [a]


def test_removal_fires_only_after_consecutive_misses(tmp_path: Path) -> None:
    a = tmp_path / "a"
    current = {"roots": [a]}
    w = MountWatcher(list_roots=lambda: current["roots"], removal_confirmations=3)
    w.poll()  # baseline

    current["roots"] = []          # card genuinely pulled, stays gone
    assert w.poll().removed == []  # miss 1
    assert w.poll().removed == []  # miss 2
    assert w.poll().removed == [a]  # miss 3 -> confirmed removed
    assert w.present == []
    # Once confirmed, it is not reported again.
    assert w.poll().removed == []


def test_flicker_never_confirms_removal(tmp_path: Path) -> None:
    """Present/absent/present/absent flicker must never confirm a removal."""
    a = tmp_path / "a"
    current = {"roots": [a]}
    w = MountWatcher(list_roots=lambda: current["roots"], removal_confirmations=3)
    w.poll()
    for _ in range(5):
        current["roots"] = []
        assert w.poll().removed == []   # miss
        current["roots"] = [a]
        assert w.poll().removed == []   # recovers, counter resets
    assert w.present == [a]
