"""Tests for upload_client.state_store (atomic resume persistence)."""

from __future__ import annotations

from pathlib import Path

from upload_client.state_store import StateStore


def test_put_get_roundtrip(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    store.put("uuid-1", {"state": "uploading", "sent": ["a.mp4"]})
    assert store.get("uuid-1") == {"state": "uploading", "sent": ["a.mp4"]}
    assert store.get("missing") is None


def test_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    StateStore(path).put("uuid-1", {"state": "verified"})
    reopened = StateStore(path)
    assert reopened.get("uuid-1") == {"state": "verified"}


def test_get_returns_a_copy(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    store.put("uuid-1", {"sent": ["a.mp4"]})
    got = store.get("uuid-1")
    got["sent"].append("b.mp4")
    # Mutating the returned dict must not change the store.
    assert store.get("uuid-1")["sent"] == ["a.mp4"]


def test_remove(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    store.put("uuid-1", {"state": "uploading"})
    store.remove("uuid-1")
    assert store.get("uuid-1") is None
    store.remove("uuid-1")  # idempotent


def test_corrupt_file_starts_clean(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{ broken", encoding="utf-8")
    store = StateStore(path)
    assert store.all() == {}
    # And it can still be written to afterwards.
    store.put("uuid-1", {"state": "uploading"})
    assert StateStore(path).get("uuid-1") == {"state": "uploading"}


def test_no_partial_file_left_after_write(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.put("uuid-1", {"state": "uploading"})
    assert path.is_file()
    assert not (tmp_path / "state.json.tmp").exists()
