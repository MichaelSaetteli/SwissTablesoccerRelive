"""Application entry point: login -> discover tournament -> show window.

Ties the GUI to the core. Upload work runs in the manager's thread pool;
the window only polls. Mount discovery is the heuristic ``discover_card_roots``
for now (event-based auto-detect is a later block).

Ingest model (2026-06-02): the table comes from the card's volume name,
the discipline from the operator's batch choice (held in ``IngestState``,
overridable per card), and the tournament from the server's active
tournament per discipline. No pre-written marker is required.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Set

from PySide6.QtWidgets import QApplication, QMessageBox

from upload_client.api_client import ApiError
from upload_client.card_scanner import scan_mounts
from upload_client.mount_watcher import MountWatcher
from upload_client.mounts import discover_card_roots
from upload_client.state_store import StateStore
from upload_client.ui.login_dialog import LoginDialog
from upload_client.ui.main_window import MainWindow
from upload_client.upload_engine import UploadEngine
from upload_client.upload_manager import UploadManager

STATE_FILE = Path.home() / ".sts_upload" / "state.json"


@dataclass
class IngestState:
    """Mutable operator choices that drive a scan.

    The window updates these (batch discipline selector, per-card override,
    DCIM sub-folder selection); the scan closure reads them. Overrides are
    keyed by the card's stable id so they survive re-scans and slot changes.
    """

    discipline: Optional[str] = None
    discipline_overrides: Dict[str, str] = field(default_factory=dict)
    subdir_overrides: Dict[str, Set[str]] = field(default_factory=dict)


def _expected_and_name(active: dict) -> tuple[Dict[str, int], str]:
    """Return (expected-cards-per-discipline, tournament name)."""
    disciplines = active.get("disciplines", {})
    expected: Dict[str, int] = {}
    name = ""
    for disc in ("Einzel", "Doppel"):
        info = disciplines.get(disc)
        if not info:
            continue
        expected[disc] = int(info.get(f"expected_cards_{disc.lower()}", 0))
        name = name or info.get("name", "")
    return expected, name


def _active_tournaments(active: dict) -> Dict[str, dict]:
    """{discipline: {"id", "name"}} for disciplines with an active tournament."""
    disciplines = active.get("disciplines", {})
    out: Dict[str, dict] = {}
    for disc in ("Einzel", "Doppel"):
        info = disciplines.get(disc)
        if info and info.get("id"):
            out[disc] = {"id": int(info["id"]), "name": info.get("name", "")}
    return out


def _make_scan_fn(active_tournaments: Dict[str, dict], state: IngestState):
    """Scan discovered roots with the operator's current ingest choices."""

    def scan() -> Dict[str, object]:
        return scan_mounts(
            discover_card_roots(),
            discipline=state.discipline,
            active_tournaments=active_tournaments,
            discipline_overrides=state.discipline_overrides,
            subdir_overrides=state.subdir_overrides,
        )

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

    expected, name = _expected_and_name(active)
    active_tournaments = _active_tournaments(active)
    state = IngestState(discipline=next(iter(active_tournaments), None))

    engine = UploadEngine(api, StateStore(STATE_FILE))
    manager = UploadManager(engine, max_parallel=4, expected=expected)
    resume_hint = len(engine.resumable())

    window = MainWindow(
        manager,
        scan_fn=_make_scan_fn(active_tournaments, state),
        watcher=MountWatcher(),
        tournament_name=name,
        resume_hint=resume_hint,
        ingest_state=state,
        disciplines=list(active_tournaments.keys()),
    )
    window.resize(900, 600)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
