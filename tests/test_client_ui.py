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

    def handle_removed(self, roots) -> list:
        self.calls.append(("handle_removed", list(roots)))
        return []


class StubWatcher:
    """Returns scripted MountChange values, then steady state."""

    def __init__(self, scripted) -> None:
        from upload_client.mount_watcher import MountChange
        self._queue = list(scripted)
        self._steady = MountChange(inserted=[], removed=[])

    def poll(self):
        return self._queue.pop(0) if self._queue else self._steady


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
    from upload_client.ui.app import _active_tournaments, _expected_and_name
    active = {"disciplines": {
        "Einzel": {"id": 2, "name": "Seetal 2026 Einzel",
                   "expected_cards_einzel": 30, "expected_cards_doppel": 0},
        "Doppel": {"id": 1, "name": "TestSG",
                   "expected_cards_doppel": 24, "expected_cards_einzel": 0},
    }}
    expected, name = _expected_and_name(active)
    assert expected == {"Einzel": 30, "Doppel": 24}
    assert name == "Seetal 2026 Einzel"
    assert _active_tournaments(active) == {
        "Einzel": {"id": 2, "name": "Seetal 2026 Einzel"},
        "Doppel": {"id": 1, "name": "TestSG"},
    }


def test_make_scan_fn_label_based(qapp, tmp_path, monkeypatch):
    from upload_client.card_scanner import CARD_READY
    from upload_client.ui import app as appmod

    # The POSIX volume reader uses the mount-dir name as the volume label,
    # so a folder named "E05" maps to table ET05 (Einzel hint from 'E').
    e05 = tmp_path / "E05"
    (e05 / "DCIM" / "100PANA").mkdir(parents=True)
    (e05 / "DCIM" / "100PANA" / "a.mp4").write_bytes(b"x" * 10)
    monkeypatch.setattr(appmod, "discover_card_roots", lambda: [e05])

    state = appmod.IngestState(discipline="Einzel")
    scan = appmod._make_scan_fn({"Einzel": {"id": 2, "name": "T"}}, state)
    inv = scan()
    (card,) = inv.values()
    assert card.status == CARD_READY
    assert card.marker.table == "ET05"
    assert card.marker.discipline == "Einzel"
    assert card.marker.tournament_id == 2


def test_interrupted_card_shows_red_warning(qapp):
    from upload_client.upload_engine import CLIENT_INTERRUPTED
    rows = [_row("u1", "ET03", CLIENT_INTERRUPTED, is_error=False)]
    win = MainWindow(StubManager(_snapshot(rows)), scan_fn=lambda: {},
                     poll_ms=10_000)
    assert win.lbl_warn.isHidden() is False
    assert "ET03" in win.lbl_warn.text()


def test_tick_polls_watcher_and_handles_removal(qapp):
    from upload_client.mount_watcher import MountChange
    mgr = StubManager(_snapshot([_row("u1", "ET01", CLIENT_VERIFIED)]))
    watcher = StubWatcher([MountChange(inserted=[], removed=["/mnt/card"])])
    win = MainWindow(mgr, scan_fn=lambda: {"k": object()}, watcher=watcher,
                     poll_ms=10_000)
    # __init__ ran one _tick already, which polled the scripted removal.
    assert any(c[0] == "handle_removed" for c in mgr.calls)
    assert any(c[0] == "add" for c in mgr.calls)  # re-scan on change


def test_resume_hint_shown_and_cleared_on_start(qapp):
    mgr = StubManager(_snapshot([_row("u1", "ET01", CLIENT_VERIFIED)]))
    win = MainWindow(mgr, scan_fn=lambda: {}, resume_hint=3, poll_ms=10_000)
    assert win.lbl_hint.isHidden() is False
    assert "3" in win.lbl_hint.text()
    win.on_start()
    assert win.lbl_hint.isHidden() is True


def test_success_banner_when_all_done(qapp):
    rows = [_row("u1", "ET01", CLIENT_RELEASED)]
    win = MainWindow(StubManager(_snapshot(rows, expected=1)), scan_fn=lambda: {},
                     poll_ms=10_000)
    # isHidden() reflects the explicit flag even without showing the window.
    assert win.lbl_success.isHidden() is False
    assert "freigegeben" in win.lbl_success.text().lower()


def test_discipline_selector_updates_state_and_rescans(qapp):
    from upload_client.ui.app import IngestState
    state = IngestState(discipline="Einzel")
    seen = []

    def scan():
        seen.append(state.discipline)
        return {}

    win = MainWindow(StubManager(_snapshot([])), scan_fn=scan,
                     ingest_state=state, disciplines=["Einzel", "Doppel"],
                     poll_ms=10_000)
    win.cmb_discipline.setCurrentText("Doppel")
    assert state.discipline == "Doppel"
    assert "Doppel" in seen  # changing the batch discipline triggered a re-scan


def test_date_and_alarm_rendered_in_row(qapp):
    from upload_client.ui.main_window import _COL_DATE
    row = _row("u1", "ET01", CLIENT_UPLOADING,
               stale_alarm=True, date_range="22.05.2026")
    win = MainWindow(StubManager(_snapshot([row])), scan_fn=lambda: {},
                     poll_ms=10_000)
    idx = win._row_of["u1"]
    text = win.table.item(idx, _COL_DATE).text()
    assert "22.05.2026" in text and "⚠" in text  # warning sign
