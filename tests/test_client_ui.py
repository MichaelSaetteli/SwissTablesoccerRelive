"""Offscreen smoke tests for the PySide6 main window.

Verifies the window builds, renders a manager snapshot into the table, and
wires the action buttons to the manager. Skipped automatically where the Qt
runtime libraries are not installed.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Skip the whole module if the Qt runtime (libEGL etc.) is unavailable.
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from upload_client.presentation import (  # noqa: E402
    DO_NOT_REMOVE,
    SAFE_TO_REMOVE,
    CardRow,
    summarise,
)
from upload_client.ui.main_window import MainWindow  # noqa: E402
from upload_client.upload_engine import (  # noqa: E402
    CLIENT_RELEASED,
    CLIENT_UPLOADING,
    CLIENT_VERIFIED,
)
from upload_client.upload_manager import ManagerSnapshot  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class StubManager:
    def __init__(self, snap: ManagerSnapshot) -> None:
        self._snap = snap
        self.calls = []

    def snapshot(self) -> ManagerSnapshot:
        return self._snap

    def add_scanned(self, inv) -> None:
        self.calls.append(("add", inv))

    def start_all(self, *, auto_release) -> None:
        self.calls.append(("start", auto_release))

    def release_verified(self):
        self.calls.append(("release_all",))
        return []

    def release(self, keys):
        self.calls.append(("release", list(keys)))
        return []

    def shutdown(self, *, wait=True) -> None:
        self.calls.append(("shutdown", wait))


def _row(key, table, state, **kw) -> CardRow:
    base = dict(
        key=key, table=table, discipline="Einzel", state=state,
        badge=state, detail="d", safe_to_remove=(
            SAFE_TO_REMOVE if state in (CLIENT_VERIFIED, CLIENT_RELEASED)
            else DO_NOT_REMOVE
        ),
        can_release=(state == CLIENT_VERIFIED), release_tooltip="",
        is_error=False,
    )
    base.update(kw)
    return CardRow(**base)


def _snapshot(rows, *, expected=2):
    states = [r.state for r in rows]
    return ManagerSnapshot(rows=rows, summaries=[summarise("Einzel", states, expected=expected)])


def test_window_renders_rows_and_summary(qapp):
    rows = [_row("u1", "ET01", CLIENT_VERIFIED), _row("u2", "ET02", CLIENT_UPLOADING)]
    win = MainWindow(StubManager(_snapshot(rows)), scan_fn=lambda: {},
                     tournament_name="Seetal 2026", poll_ms=10_000)
    assert win.table.rowCount() == 2
    assert "Seetal 2026" in win.windowTitle()
    assert "1 / 2" in win.lbl_summary.text()
    # Idempotent refresh: rows are updated in place, not duplicated.
    win.refresh()
    assert win.table.rowCount() == 2


def test_buttons_call_manager(qapp):
    mgr = StubManager(_snapshot([_row("u1", "ET01", CLIENT_VERIFIED)]))
    win = MainWindow(mgr, scan_fn=lambda: {"k": object()}, poll_ms=10_000)

    win.chk_auto.setChecked(True)
    win.on_start()
    assert ("start", True) in mgr.calls

    win.on_release_all()
    assert ("release_all",) in mgr.calls

    win.on_scan()
    assert any(c[0] == "add" for c in mgr.calls)


def test_release_selected_only_releases_checked_verified(qapp):
    rows = [_row("u1", "ET01", CLIENT_VERIFIED), _row("u2", "ET02", CLIENT_UPLOADING)]
    mgr = StubManager(_snapshot(rows))
    win = MainWindow(mgr, scan_fn=lambda: {}, poll_ms=10_000)

    # Check both rows; only the verified one is releasable.
    for key in ("u1", "u2"):
        idx = win._row_of[key]
        win.table.item(idx, 0).setCheckState(Qt.Checked)
    win.on_release_selected()

    release_calls = [c for c in mgr.calls if c[0] == "release"]
    assert release_calls == [("release", ["u1"])]


def test_expected_and_name_parses_active_tournament(qapp):
    from upload_client.ui.app import _expected_and_name
    active = {"disciplines": {
        "Einzel": {"id": 2, "name": "Seetal 2026 Einzel",
                   "expected_cards_einzel": 30, "expected_cards_doppel": 0},
        "Doppel": {"id": 1, "name": "TestSG",
                   "expected_cards_doppel": 24, "expected_cards_einzel": 0},
    }}
    expected, ids, name = _expected_and_name(active)
    assert expected == {"Einzel": 30, "Doppel": 24}
    assert ids == {"Einzel": 2, "Doppel": 1}
    assert name == "Seetal 2026 Einzel"


def test_make_scan_fn_locks_wrong_tournament(qapp, tmp_path, monkeypatch):
    from tests.client_helpers import make_card
    from upload_client.card_scanner import CARD_READY, CARD_WRONG_TOURNAMENT
    from upload_client.ui import app as appmod

    wrong = make_card(tmp_path / "wrong", tournament_id=99, card_uuid="x",
                      discipline="Einzel", table="ET05")
    ok = make_card(tmp_path / "ok", tournament_id=2, card_uuid="y",
                   discipline="Einzel", table="ET06")
    monkeypatch.setattr(appmod, "discover_card_roots", lambda: [wrong, ok])

    inv = appmod._make_scan_fn({"Einzel": 2})()
    by_table = {c.marker.table: c.status for c in inv.values()}
    assert by_table["ET05"] == CARD_WRONG_TOURNAMENT
    assert by_table["ET06"] == CARD_READY


def test_success_banner_when_all_done(qapp):
    rows = [_row("u1", "ET01", CLIENT_RELEASED)]
    win = MainWindow(StubManager(_snapshot(rows, expected=1)), scan_fn=lambda: {},
                     poll_ms=10_000)
    # isHidden() reflects the explicit flag even without showing the window.
    assert win.lbl_success.isHidden() is False
    assert "freigegeben" in win.lbl_success.text().lower()
