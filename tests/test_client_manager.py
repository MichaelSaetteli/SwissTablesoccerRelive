"""Tests for upload_client.upload_manager (orchestration + snapshot).

Runs against the real Slice-1 Flask server. The pool is single-worker here
so the shared Flask test client is not hit from several threads at once;
StateStore thread-safety is covered separately in
test_client_state_store.py.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from db.schema import _reset_connections_for_tests
from tests.client_helpers import (
    FlaskSessionAdapter,
    TEST_PASS,
    TEST_USER,
    make_app,
    make_card,
    make_configs,
    setup_tournament,
)
from upload_client.api_client import ApiClient
from upload_client.card_scanner import scan_mounts
from upload_client.state_store import StateStore
from upload_client.upload_engine import (
    CLIENT_RELEASED,
    CLIENT_VERIFIED,
    UploadEngine,
)
from upload_client.upload_manager import UploadManager


@pytest.fixture(autouse=True)
def _isolated():
    _reset_connections_for_tests()
    yield
    _reset_connections_for_tests()


@pytest.fixture
def env(tmp_path: Path):
    configs = make_configs(tmp_path)
    tid = setup_tournament(configs)
    app = make_app(configs)
    api = ApiClient("http://server", session=FlaskSessionAdapter(app.test_client()))
    api.login(TEST_USER, TEST_PASS)
    engine = UploadEngine(api, StateStore(tmp_path / "state.json"))
    return {"configs": configs, "tid": tid, "engine": engine, "tmp_path": tmp_path}


def _manager(env, **kw) -> UploadManager:
    return UploadManager(env["engine"], max_parallel=1, **kw)


def _active(tid: int) -> dict:
    """active_tournaments map for the label-based scanner (both disciplines)."""
    return {
        "Einzel": {"id": tid, "name": "Seetal 2026"},
        "Doppel": {"id": tid, "name": "Seetal 2026"},
    }


def test_scan_upload_snapshot_and_release(env, tmp_path: Path) -> None:
    # Card dirs named E01/E02/E03 -> POSIX volume reader -> tables ET01..ET03.
    for i in (1, 2, 3):
        make_card(tmp_path / f"E0{i}", tournament_id=env["tid"])
    inv = scan_mounts(
        [tmp_path / f"E0{i}" for i in (1, 2, 3)],
        discipline="Einzel", active_tournaments=_active(env["tid"]),
    )
    mgr = _manager(env, expected={"Einzel": 3})
    mgr.add_scanned(inv)

    mgr.start_all(auto_release=False)
    mgr.shutdown(wait=True)  # block until the pool drained

    snap = mgr.snapshot()
    assert len(snap.rows) == 3
    assert all(r.state == CLIENT_VERIFIED for r in snap.rows)
    summary = snap.summaries[0]
    assert summary.discipline == "Einzel"
    assert summary.done == 3 and summary.expected == 3
    assert "3 / 3" in summary.header_text()

    released = mgr.release_verified()
    assert len(released) == 3
    assert all(p.state == CLIENT_RELEASED for p in released)
    for i in (1, 2, 3):
        assert (env["configs"]["Einzel"].paths.eingang / f"ET0{i}").is_dir()


def test_auto_release_via_manager(env, tmp_path: Path) -> None:
    make_card(tmp_path / "E01", tournament_id=env["tid"])
    inv = scan_mounts([tmp_path / "E01"], discipline="Einzel",
                      active_tournaments=_active(env["tid"]))
    mgr = _manager(env, expected={"Einzel": 1})
    mgr.add_scanned(inv)
    mgr.start_all(auto_release=True)
    mgr.shutdown(wait=True)
    snap = mgr.snapshot()
    assert snap.rows[0].state == CLIENT_RELEASED
    assert snap.all_done is True


def test_add_scanned_is_additive(env, tmp_path: Path) -> None:
    make_card(tmp_path / "E01", tournament_id=env["tid"])
    make_card(tmp_path / "E02", tournament_id=env["tid"])
    mgr = _manager(env)
    at = _active(env["tid"])
    mgr.add_scanned(scan_mounts([tmp_path / "E01"], discipline="Einzel",
                                active_tournaments=at))
    mgr.start_all(auto_release=False)
    mgr.shutdown(wait=True)
    # Second scan adds b without disturbing a's verified state.
    mgr.add_scanned(scan_mounts([tmp_path / "E02"], discipline="Einzel",
                                active_tournaments=at))
    snap = mgr.snapshot()
    states = {r.table: r.state for r in snap.rows}
    assert states["ET01"] == CLIENT_VERIFIED
    assert states["ET02"] != CLIENT_VERIFIED  # b not started yet


def test_locked_card_shown_but_not_uploaded(env, tmp_path: Path) -> None:
    make_card(tmp_path / "E01", tournament_id=env["tid"])           # ok
    make_card(tmp_path / "NONAME", tournament_id=env["tid"])        # no table -> locked
    inv = scan_mounts([tmp_path / "E01", tmp_path / "NONAME"],
                      discipline="Einzel", active_tournaments=_active(env["tid"]))
    mgr = _manager(env, expected={"Einzel": 1})
    mgr.add_scanned(inv)
    mgr.start_all(auto_release=False)
    mgr.shutdown(wait=True)
    snap = mgr.snapshot()
    locked = [r for r in snap.rows if r.is_error]
    assert len(locked) == 1
    assert locked[0].can_release is False


def _seed_progress(store: StateStore, uuid: str, table: str, state: str) -> None:
    from upload_client.upload_engine import CardProgress
    p = CardProgress(card_uuid=uuid, table=table, discipline="Einzel",
                     tournament_id=1, expected_files=2, expected_bytes=200)
    p.state = state
    p.server_card_id = 1
    store.put(uuid, p.to_dict())


def test_mark_interrupted_only_flips_uploading(tmp_path: Path) -> None:
    from tests.client_helpers import FakeSession
    from upload_client.upload_engine import (
        CLIENT_INTERRUPTED, CLIENT_UPLOADING, CLIENT_VERIFIED,
    )
    store = StateStore(tmp_path / "s.json")
    _seed_progress(store, "up", "ET01", CLIENT_UPLOADING)
    _seed_progress(store, "ok", "ET02", CLIENT_VERIFIED)
    engine = UploadEngine(ApiClient("http://x", session=FakeSession()), store)

    assert engine.mark_interrupted("up").state == CLIENT_INTERRUPTED
    assert engine.mark_interrupted("ok").state == CLIENT_VERIFIED  # unchanged
    assert engine.mark_interrupted("missing") is None


def test_manager_handle_removed_interrupts_uploading_card(tmp_path: Path) -> None:
    from tests.client_helpers import FakeSession
    from upload_client.card_scanner import CARD_READY, ScannedCard
    from upload_client.manifest import CardManifest
    from upload_client.marker import CardMarker
    from upload_client.upload_engine import CLIENT_INTERRUPTED, CLIENT_UPLOADING

    store = StateStore(tmp_path / "s.json")
    _seed_progress(store, "u1", "ET01", CLIENT_UPLOADING)
    engine = UploadEngine(ApiClient("http://x", session=FakeSession()), store)
    mgr = UploadManager(engine, max_parallel=1)

    root = tmp_path / "card"
    marker = CardMarker("u1", 1, "T", "Einzel", "ET01")
    scanned = ScannedCard(root=root, status=CARD_READY, marker=marker,
                          manifest=CardManifest(root=root, files=()))
    mgr.add_scanned({scanned.key: scanned})

    affected = mgr.handle_removed([root])
    assert affected == ["ET01"]
    assert engine.load("u1").state == CLIENT_INTERRUPTED
    # A different root removal does nothing.
    assert mgr.handle_removed([tmp_path / "other"]) == []


def test_statestore_parallel_writes_are_safe(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")

    def writer(n: int) -> None:
        for i in range(50):
            store.put(f"u{n}", {"i": i})

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    reopened = StateStore(tmp_path / "state.json")
    assert len(reopened.all()) == 8  # no lost/corrupt keys
