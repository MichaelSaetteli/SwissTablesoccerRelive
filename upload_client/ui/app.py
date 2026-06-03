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

import logging
import sys
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Optional, Set

from PySide6.QtCore import QLockFile
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
LOCK_FILE = Path.home() / ".sts_upload" / "sts_upload.lock"
LOG_FILE = Path.home() / ".sts_upload" / "sts_upload.log"


def setup_logging(path: Path = LOG_FILE) -> None:
    """Write a diagnostic log next to the state file.

    The .exe is windowed (no console), so without this an upload failure's
    real cause is invisible. The log captures the per-card step trail and the
    full exception when a card is interrupted (engine logs under
    ``sts_upload.*``). Rotates so it never grows without bound.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        str(path), maxBytes=1_000_000, backupCount=3, encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger("sts_upload")
    root.setLevel(logging.INFO)
    # Avoid stacking duplicate handlers if run() is called more than once.
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        root.addHandler(handler)


def acquire_single_instance_lock(path: Path) -> Optional[QLockFile]:
    """Become the only running instance, or return None if one already runs.

    Two instances would share ``state.json`` and the same SD cards, racing
    each other's upload state and doubling the heavy-read load that makes a
    card's drive root briefly unstattable (the false "card removed" trigger).
    ``QLockFile`` is cross-platform and auto-recovers from a crashed previous
    instance (stale lock), so a clean crash never locks the operator out.

    The returned lock must be kept alive for the whole session; dropping it
    releases the lock.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(path))
    lock.setStaleLockTime(30_000)  # 30 s: reclaim a crashed instance's lock
    return lock if lock.tryLock(100) else None


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
    setup_logging()
    logging.getLogger("sts_upload").info("STS-Upload gestartet")
    app = QApplication.instance() or QApplication(argv or sys.argv)

    # Single-instance guard: refuse a second window with a clear message.
    lock = acquire_single_instance_lock(LOCK_FILE)
    if lock is None:
        QMessageBox.warning(
            None,
            "STS-Upload laeuft bereits",
            "STS-Upload ist bereits geoeffnet.\n\n"
            "Bitte das schon laufende Fenster verwenden. Zwei Instanzen "
            "gleichzeitig teilen sich denselben Status und dieselben SD-Karten "
            "und koennen sich beim Upload gegenseitig stoeren.",
        )
        return 0
    # Keep the lock alive for the whole session (GC would release it).
    app._sts_instance_lock = lock  # type: ignore[attr-defined]

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
