"""Application entry point: login -> discover tournament -> show window.

Ties the GUI to the core. Upload work runs in the manager's thread pool;
the window only polls. Mount discovery is the heuristic ``discover_card_roots``
for now (event-based auto-detect is a later block).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict

from PySide6.QtWidgets import QApplication, QMessageBox

from upload_client.api_client import ApiError
from upload_client.card_scanner import scan_card
from upload_client.marker import MarkerError, has_marker, read_marker
from upload_client.mounts import discover_card_roots
from upload_client.state_store import StateStore
from upload_client.ui.login_dialog import LoginDialog
from upload_client.ui.main_window import MainWindow
from upload_client.upload_engine import UploadEngine
from upload_client.upload_manager import UploadManager

STATE_FILE = Path.home() / ".sts_upload" / "state.json"


def _expected_and_name(active: dict) -> tuple[Dict[str, int], Dict[str, int], str]:
    """Return (expected-per-discipline, active-id-per-discipline, name)."""
    disciplines = active.get("disciplines", {})
    expected: Dict[str, int] = {}
    active_ids: Dict[str, int] = {}
    name = ""
    for disc in ("Einzel", "Doppel"):
        info = disciplines.get(disc)
        if not info:
            continue
        active_ids[disc] = int(info.get("id", 0))
        key = f"expected_cards_{disc.lower()}"
        expected[disc] = int(info.get(key, 0))
        name = name or info.get("name", "")
    return expected, active_ids, name


def _make_scan_fn(active_ids: Dict[str, int]):
    """Scan discovered roots, locking cards from a non-active tournament."""

    def scan() -> Dict[str, object]:
        inventory: Dict[str, object] = {}
        for root in discover_card_roots():
            active_id = None
            if has_marker(root):
                try:
                    active_id = active_ids.get(read_marker(root).discipline)
                except MarkerError:
                    active_id = None
            card = scan_card(root, active_tournament_id=active_id)
            inventory[card.key] = card
        return inventory

    return scan


def run(argv=None) -> int:
    app = QApplication.instance() or QApplication(argv or sys.argv)

    login = LoginDialog()
    if login.exec() != LoginDialog.Accepted or login.api is None:
        return 0
    api = login.api

    try:
        active = api.active_tournament()
    except ApiError as exc:
        QMessageBox.critical(None, "Fehler", f"Aktives Turnier laden: {exc}")
        return 1

    expected, active_ids, name = _expected_and_name(active)
    engine = UploadEngine(api, StateStore(STATE_FILE))
    manager = UploadManager(engine, max_parallel=4, expected=expected)

    window = MainWindow(
        manager, scan_fn=_make_scan_fn(active_ids), tournament_name=name,
    )
    window.resize(900, 600)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
