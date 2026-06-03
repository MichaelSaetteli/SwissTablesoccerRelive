"""The main upload window - guides the operator through 4 steps.

Design goals (2026-06-03 revision):
* Step-by-step guided: numbered steps at the top show what to do next.
* Background scan: mount discovery runs in a thread so the UI never freezes.
* Live progress: per-card QProgressBar with MB/s speed and ETA.
* Modern, clean look: stylesheet-driven, compact columns, clear labels.

The window is a thin renderer: it polls ``manager.snapshot()`` on a 1.5 s
timer and updates rows in-place (keyed by card uuid). All Qt mutations
happen on the main thread via the timer.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from upload_client.upload_engine import (
    CLIENT_FAILED,
    CLIENT_INTERRUPTED,
    CLIENT_PENDING,
    CLIENT_RELEASED,
    CLIENT_UPLOADING,
    CLIENT_VERIFIED,
)

# Table columns. "Datum" is last so it absorbs the slack (stretch-last);
# every column is user-resizable (Interactive).
(_COL_SEL, _COL_TABLE, _COL_DISC, _COL_PROG, _COL_DURATION, _COL_STATUS,
 _COL_SAFE, _COL_DATE) = range(8)
_HEADERS = ["Freigabe", "Tisch", "Disziplin", "Fortschritt", "Dauer",
            "Status", "", "Datum"]

_ERROR_BG = QColor(0xFD, 0xE7, 0xE9)
_DONE_BG = QColor(0xE6, 0xF4, 0xEA)
_WARN_BG = QColor(0xFF, 0xF4, 0xCE)
_UPLOAD_BG = QColor(0xE8, 0xF0, 0xFE)

_POLL_MS = 1500

_STYLESHEET = """
QMainWindow, QWidget#central {
    background: #f8f9fa;
}
QLabel#step_label_active {
    color: #1a73e8;
    font-weight: bold;
    font-size: 13px;
}
QLabel#step_label_done {
    color: #34a853;
    font-weight: bold;
    font-size: 13px;
}
QLabel#step_label_inactive {
    color: #9aa0a6;
    font-size: 13px;
}
QLabel#step_sep {
    color: #9aa0a6;
    font-size: 13px;
}
QPushButton#btn_primary {
    background: #1a73e8;
    color: white;
    border: none;
    border-radius: 4px;
    padding: 8px 18px;
    font-size: 13px;
    font-weight: bold;
    min-width: 150px;
}
QPushButton#btn_primary:hover { background: #1557b0; }
QPushButton#btn_primary:disabled { background: #a8c7fa; }
QPushButton#btn_secondary {
    background: white;
    color: #1a73e8;
    border: 1.5px solid #1a73e8;
    border-radius: 4px;
    padding: 7px 16px;
    font-size: 13px;
    min-width: 130px;
}
QPushButton#btn_secondary:hover { background: #e8f0fe; }
QPushButton#btn_secondary:disabled { color: #9aa0a6; border-color: #9aa0a6; }
QPushButton#btn_quiet {
    background: transparent;
    color: #5f6368;
    border: none;
    padding: 7px 10px;
    font-size: 12px;
}
QPushButton#btn_quiet:hover { color: #b00020; text-decoration: underline; }
QPushButton#btn_success {
    background: #34a853;
    color: white;
    border: none;
    border-radius: 4px;
    padding: 8px 18px;
    font-size: 13px;
    font-weight: bold;
    min-width: 150px;
}
QPushButton#btn_success:hover { background: #1e8e3e; }
QPushButton#btn_success:disabled { background: #a8d5b5; }
QTableWidget {
    background: white;
    gridline-color: #e0e0e0;
    border: 1px solid #e0e0e0;
    border-radius: 4px;
    font-size: 12px;
}
QTableWidget::item { padding: 4px 6px; }
QHeaderView::section {
    background: #f1f3f4;
    border: none;
    border-bottom: 1px solid #e0e0e0;
    padding: 5px 6px;
    font-size: 12px;
    font-weight: bold;
    color: #5f6368;
}
QProgressBar {
    border: none;
    border-radius: 3px;
    background: #e8eaed;
    height: 16px;
    text-align: center;
    font-size: 11px;
}
QProgressBar::chunk { background: #1a73e8; border-radius: 3px; }
QComboBox {
    border: 1px solid #dadce0;
    border-radius: 3px;
    padding: 2px 6px;
    font-size: 12px;
    background: white;
}
QCheckBox { font-size: 12px; color: #5f6368; }
QLabel#lbl_warn {
    color: #b00020;
    font-weight: bold;
    background: #fde7e9;
    padding: 8px 12px;
    border-radius: 4px;
    font-size: 13px;
}
QLabel#lbl_success {
    color: #137333;
    font-weight: bold;
    background: #e6f4ea;
    padding: 8px 12px;
    border-radius: 4px;
    font-size: 13px;
}
QLabel#lbl_hint {
    color: #1a73e8;
    background: #e8f0fe;
    padding: 8px 12px;
    border-radius: 4px;
    font-size: 12px;
}
QLabel#lbl_summary {
    color: #3c4043;
    font-size: 13px;
    padding: 4px 0px;
}
QFrame#divider {
    color: #e0e0e0;
}
"""


def _human_bytes(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _default_dcim_dialog(folders, selected, parent):
    from upload_client.ui.dcim_dialog import DcimSelectionDialog
    return DcimSelectionDialog(folders, selected, parent)


class _StepBar(QWidget):
    """Horizontal step indicator: 1 → 2 → 3 → 4."""

    _STEPS = [
        ("1", "SD-Karte einstecken"),
        ("2", "Disziplin wählen"),
        ("3", "Hochladen starten"),
        ("4", "Freigeben"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(4)
        self._labels: List[QLabel] = []
        for i, (num, text) in enumerate(self._STEPS):
            lbl = QLabel(f"  {num}. {text}  ")
            lbl.setObjectName("step_label_inactive")
            layout.addWidget(lbl)
            self._labels.append(lbl)
            if i < len(self._STEPS) - 1:
                sep = QLabel("→")
                sep.setObjectName("step_sep")
                layout.addWidget(sep)
        layout.addStretch(1)

    def set_step(self, active: int) -> None:
        """Highlight step *active* (1-based); earlier steps shown as done."""
        for i, lbl in enumerate(self._labels):
            step_num = i + 1
            if step_num < active:
                lbl.setObjectName("step_label_done")
            elif step_num == active:
                lbl.setObjectName("step_label_active")
            else:
                lbl.setObjectName("step_label_inactive")
            # Force style refresh after objectName change.
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)


class MainWindow(QMainWindow):
    def __init__(
        self,
        manager,
        *,
        scan_fn: Callable[[], Dict[str, object]],
        watcher=None,
        tournament_name: str = "",
        resume_hint: int = 0,
        poll_ms: int = _POLL_MS,
        ingest_state=None,
        disciplines: Optional[List[str]] = None,
        dcim_dialog_factory=None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._scan_fn = scan_fn
        self._watcher = watcher
        self._ingest_state = ingest_state
        self._disciplines = disciplines or ["Einzel", "Doppel"]
        self._dcim_dialog_factory = dcim_dialog_factory or _default_dcim_dialog

        # Row tracking
        self._row_of: Dict[str, int] = {}
        self._disc_combos: Dict[str, QComboBox] = {}
        self._prog_bars: Dict[str, QProgressBar] = {}
        self._rows_by_key: Dict[str, object] = {}

        # Background scan state (written by worker, read by main thread)
        self._scan_thread: Optional[threading.Thread] = None
        self._scan_result: Optional[Dict] = None
        self._scan_error: Optional[str] = None
        self._is_update_scan: bool = False  # True = update_pending instead of add

        # Speed tracking: key -> (monotonic_time, sent_bytes)
        self._speed_data: Dict[str, Tuple[float, int]] = {}
        self._speed_cache: Dict[str, float] = {}  # key -> bytes/s (smoothed)

        # Per-card duration: wall-clock start when the card first uploads, and
        # the frozen end time once it is verified/released. So the "Dauer"
        # column runs live while uploading and then stops at the final value.
        self._dur_start: Dict[str, float] = {}   # key -> time.time() at start
        self._dur_end: Dict[str, float] = {}     # key -> time.time() when done

        title = "STS-Upload"
        if tournament_name:
            title += f"  —  {tournament_name}"
        self.setWindowTitle(title)
        self.setStyleSheet(_STYLESHEET)
        self._build_ui()

        if resume_hint > 0:
            self.lbl_hint.setText(
                f"  {resume_hint} Karte(n) aus einer früheren Sitzung vorhanden. "
                "SD-Karte einstecken → 'Hochladen starten' zum Fortsetzen."
            )
            self.lbl_hint.setVisible(True)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(poll_ms)
        self._tick()

    # -- construction -------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("central")
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        # Step indicator
        self._step_bar = _StepBar()
        root.addWidget(self._step_bar)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFrameShape(QFrame.HLine)
        root.addWidget(divider)

        # Action bar
        bar = QHBoxLayout()
        bar.setSpacing(10)

        if self._disciplines:
            lbl_disc = QLabel("Disziplin:")
            lbl_disc.setAlignment(Qt.AlignVCenter)
            bar.addWidget(lbl_disc)
            self.cmb_discipline = QComboBox()
            self.cmb_discipline.setMinimumWidth(100)
            for disc in self._disciplines:
                self.cmb_discipline.addItem(disc)
            if self._ingest_state is not None and self._ingest_state.discipline:
                i = self.cmb_discipline.findText(self._ingest_state.discipline)
                if i >= 0:
                    self.cmb_discipline.setCurrentIndex(i)
            self.cmb_discipline.currentTextChanged.connect(self.on_discipline_changed)
            bar.addWidget(self.cmb_discipline)
            bar.addSpacing(8)

        self.btn_scan = QPushButton("📂  SD-Karten einlesen")
        self.btn_scan.setObjectName("btn_secondary")
        self.btn_scan.setToolTip("Eingesteckte SD-Karten einlesen und Tabelle aktualisieren")
        self.btn_scan.clicked.connect(self.on_scan)
        bar.addWidget(self.btn_scan)

        self.btn_start = QPushButton("▶  Hochladen starten")
        self.btn_start.setObjectName("btn_primary")
        self.btn_start.setToolTip("Upload für alle erkannten Karten starten (oder fortsetzen)")
        self.btn_start.clicked.connect(self.on_start)
        bar.addWidget(self.btn_start)

        self.btn_release_all = QPushButton("✅  Alle freigeben")
        self.btn_release_all.setObjectName("btn_success")
        self.btn_release_all.setToolTip("Alle verifizierten Karten auf einmal freigeben")
        self.btn_release_all.clicked.connect(self.on_release_all)
        bar.addWidget(self.btn_release_all)

        bar.addStretch(1)

        self.chk_auto = QCheckBox("Auto-Freigabe nach Verifikation")
        self.chk_auto.setToolTip(
            "Karten werden nach erfolgreicher Verifikation automatisch freigegeben,\n"
            "ohne dass 'Alle freigeben' geklickt werden muss."
        )
        bar.addWidget(self.chk_auto)

        root.addLayout(bar)

        # Hint / warn / success banners
        self.lbl_hint = QLabel("")
        self.lbl_hint.setObjectName("lbl_hint")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setVisible(False)
        root.addWidget(self.lbl_hint)

        self.lbl_warn = QLabel("")
        self.lbl_warn.setObjectName("lbl_warn")
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setVisible(False)
        root.addWidget(self.lbl_warn)

        # Summary line
        self.lbl_summary = QLabel("")
        self.lbl_summary.setObjectName("lbl_summary")
        root.addWidget(self.lbl_summary)

        # Card table
        self.table = QTableWidget(0, len(_HEADERS), self)
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        header = self.table.horizontalHeader()
        # Every column is user-resizable; "Datum" (last) stretches to absorb
        # any slack so there is no awkward trailing gap.
        for col in (_COL_SEL, _COL_TABLE, _COL_DISC, _COL_PROG, _COL_DURATION,
                    _COL_STATUS, _COL_SAFE, _COL_DATE):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        header.setStretchLastSection(True)
        self.table.setColumnWidth(_COL_SEL,     70)
        self.table.setColumnWidth(_COL_TABLE,   60)
        self.table.setColumnWidth(_COL_DISC,   100)
        self.table.setColumnWidth(_COL_PROG,   300)  # sensible default, draggable
        self.table.setColumnWidth(_COL_DURATION, 75)
        self.table.setColumnWidth(_COL_STATUS, 110)
        self.table.setColumnWidth(_COL_SAFE,    32)
        self.table.setColumnWidth(_COL_DATE,    95)
        self.table.setRowHeight(0, 36)
        self.table.cellDoubleClicked.connect(self.on_cell_double_clicked)
        # The "Freigabe" checkbox is only for picking which verified cards to
        # release - it is NOT needed to upload. Spell that out on hover.
        sel_header = self.table.horizontalHeaderItem(_COL_SEL)
        if sel_header is not None:
            sel_header.setToolTip(
                "Nur zum gezielten Freigeben einzelner Karten.\n"
                "Zum Hochladen NICHT noetig — 'Hochladen starten' nimmt alle Karten."
            )
        root.addWidget(self.table, 1)

        # Bottom bar: reset (left) + selective release + success label
        bottom = QHBoxLayout()

        self.btn_reset = QPushButton("↺  Zurücksetzen")
        self.btn_reset.setObjectName("btn_quiet")
        self.btn_reset.setToolTip(
            "Alle lokalen Upload-Spuren vergessen und neu beginnen.\n"
            "Ersetzt das manuelle Löschen von state.json. Lädt nichts vom\n"
            "Server, löscht keine Videos — nur den lokalen Fortschritts-Status."
        )
        self.btn_reset.clicked.connect(self.on_reset)
        bottom.addWidget(self.btn_reset)

        self.lbl_success = QLabel("")
        self.lbl_success.setObjectName("lbl_success")
        self.lbl_success.setWordWrap(True)
        self.lbl_success.setVisible(False)
        bottom.addWidget(self.lbl_success, 1)

        self.btn_select_all = QPushButton("Alle markieren")
        self.btn_select_all.setObjectName("btn_quiet")
        self.btn_select_all.setToolTip(
            "Setzt bei allen Karten das Freigabe-Häkchen (oder entfernt es wieder)."
        )
        self.btn_select_all.clicked.connect(self.on_toggle_select_all)
        bottom.addWidget(self.btn_select_all)

        self.btn_release_sel = QPushButton("Markierte freigeben")
        self.btn_release_sel.setObjectName("btn_secondary")
        self.btn_release_sel.setToolTip("Nur markierte (☑) verifizierte Karten freigeben")
        self.btn_release_sel.clicked.connect(self.on_release_selected)
        bottom.addWidget(self.btn_release_sel)
        root.addLayout(bottom)

        self.setCentralWidget(central)

    # -- background scan ----------------------------------------------------

    def _start_scan_thread(self, *, update_pending: bool = False) -> None:
        """Start a background mount scan. Noop if one is already running."""
        if self._scan_thread is not None and self._scan_thread.is_alive():
            return
        self._is_update_scan = update_pending
        self._scan_result = None
        self._scan_error = None
        self.btn_scan.setEnabled(False)
        self.btn_scan.setText("Einlesen…")

        def _worker():
            try:
                self._scan_result = self._scan_fn()
            except Exception as exc:  # noqa: BLE001
                self._scan_error = str(exc)

        self._scan_thread = threading.Thread(target=_worker, daemon=True)
        self._scan_thread.start()

    def _check_scan_result(self) -> None:
        """Pick up a finished background scan (called from _tick)."""
        if self._scan_thread is None or self._scan_thread.is_alive():
            return
        self._scan_thread = None
        self.btn_scan.setEnabled(True)
        self.btn_scan.setText("📂  SD-Karten einlesen")

        if self._scan_error:
            QMessageBox.warning(self, "Einlesen fehlgeschlagen", self._scan_error)
            self._scan_error = None
            return

        if self._scan_result is not None:
            if self._is_update_scan:
                updater = getattr(self._manager, "update_pending", None)
                (updater or self._manager.add_scanned)(self._scan_result)
            else:
                self._manager.add_scanned(self._scan_result)
            self._scan_result = None

    # -- actions ------------------------------------------------------------

    def on_discipline_changed(self, text: str) -> None:
        if self._ingest_state is not None:
            self._ingest_state.discipline = text or None
        self._start_scan_thread(update_pending=False)

    @staticmethod
    def _uuid_of(key: str) -> str:
        return key[len("uuid:"):] if key.startswith("uuid:") else key

    def on_card_discipline_changed(self, key: str, text: str) -> None:
        if self._ingest_state is None or not text:
            return
        self._ingest_state.discipline_overrides[self._uuid_of(key)] = text
        self._start_scan_thread(update_pending=True)

    def on_cell_double_clicked(self, row: int, col: int) -> None:
        for key, idx in self._row_of.items():
            if idx == row:
                self.open_dcim_selection(key)
                return

    def open_dcim_selection(self, key: str) -> None:
        card_row = self._rows_by_key.get(key)
        if card_row is None or not card_row.dcim_folders:
            return
        dlg = self._dcim_dialog_factory(
            card_row.dcim_folders, set(card_row.selected_subdirs), self,
        )
        from PySide6.QtWidgets import QDialog
        if dlg.exec() != QDialog.Accepted or self._ingest_state is None:
            return
        self._ingest_state.subdir_overrides[self._uuid_of(key)] = \
            dlg.selected_names()
        self._start_scan_thread(update_pending=True)

    def on_scan(self) -> None:
        self._start_scan_thread(update_pending=False)

    def on_start(self) -> None:
        self.lbl_hint.setVisible(False)
        self._manager.start_all(auto_release=self.chk_auto.isChecked())
        self.refresh()

    def on_release_all(self) -> None:
        self._manager.release_verified()
        self.refresh()

    def on_release_selected(self) -> None:
        keys = self._checked_releasable_keys()
        if keys:
            self._manager.release(keys)
        self.refresh()

    def on_toggle_select_all(self) -> None:
        """Check every Freigabe box, or clear them all if all are already set."""
        items = [
            self.table.item(idx, _COL_SEL) for idx in self._row_of.values()
        ]
        items = [it for it in items if it is not None]
        if not items:
            return
        all_checked = all(it.checkState() == Qt.Checked for it in items)
        new_state = Qt.Unchecked if all_checked else Qt.Checked
        for it in items:
            it.setCheckState(new_state)
        self.btn_select_all.setText(
            "Markierung aufheben" if new_state == Qt.Checked else "Alle markieren"
        )

    def on_reset(self) -> None:
        """Forget all local upload state after a confirmation prompt.

        The in-app replacement for deleting ``state.json`` by hand. Clears
        the manager's tracked cards + local resume records, then wipes the
        table so the next scan starts from a clean slate.
        """
        confirm = QMessageBox.question(
            self,
            "Zurücksetzen?",
            "Alle lokalen Upload-Spuren werden vergessen und das Tool beginnt "
            "neu.\n\nEs werden KEINE Videos und KEINE Server-Daten gelöscht — "
            "nur der lokale Fortschritts-Status (state.json).\n\nFortfahren?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        reset = getattr(self._manager, "reset", None)
        if callable(reset):
            reset()
        # Drop all table rows + per-row widgets so nothing stale lingers.
        self.table.setRowCount(0)
        self._row_of.clear()
        self._disc_combos.clear()
        self._prog_bars.clear()
        self._rows_by_key.clear()
        self._speed_data.clear()
        self._speed_cache.clear()
        self._dur_start.clear()
        self._dur_end.clear()
        self.lbl_hint.setVisible(False)
        self.refresh()

    def _checked_releasable_keys(self) -> List[str]:
        keys: List[str] = []
        for key, row_idx in self._row_of.items():
            item = self.table.item(row_idx, _COL_SEL)
            if item and item.checkState() == Qt.Checked:
                row_obj = self._rows_by_key.get(key)
                if row_obj and row_obj.can_release:
                    keys.append(key)
        return keys

    # -- rendering ----------------------------------------------------------

    def _tick(self) -> None:
        """Timer callback: check scan result, poll watcher, then render."""
        self._check_scan_result()

        if self._watcher is not None:
            change = self._watcher.poll()
            if change.removed:
                self._manager.handle_removed(change.removed)
            if change.changed:
                self._start_scan_thread(update_pending=False)

        self.refresh()

    def _compute_speed(self, key: str, sent_bytes: int) -> Optional[float]:
        """Return smoothed bytes/s for *key*, or None if not enough data."""
        now = time.monotonic()
        if key in self._speed_data:
            t_prev, b_prev = self._speed_data[key]
            dt = now - t_prev
            if dt >= 0.5 and sent_bytes > b_prev:
                raw_speed = (sent_bytes - b_prev) / dt
                # Exponential smoothing (alpha 0.4) for steadier display.
                prev = self._speed_cache.get(key, raw_speed)
                self._speed_cache[key] = 0.6 * prev + 0.4 * raw_speed
        if sent_bytes > 0:
            self._speed_data[key] = (now, sent_bytes)
        return self._speed_cache.get(key)

    @staticmethod
    def _fmt_hms(seconds: float) -> str:
        s = max(0, int(seconds))
        h, rem = divmod(s, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    def _duration_text(self, row) -> str:
        """Elapsed time for a card: live while uploading, frozen when done."""
        key = row.key
        done_states = (CLIENT_VERIFIED, CLIENT_RELEASED)
        if row.state == CLIENT_UPLOADING:
            # Start the clock the first time we see the card uploading.
            self._dur_start.setdefault(key, time.time())
            self._dur_end.pop(key, None)
            return self._fmt_hms(time.time() - self._dur_start[key])
        if row.state in done_states and key in self._dur_start:
            # Freeze at the moment it first reached a done state.
            end = self._dur_end.setdefault(key, time.time())
            return self._fmt_hms(end - self._dur_start[key])
        # Pending / interrupted / failed / never-started -> no running clock.
        if key in self._dur_start and key in self._dur_end:
            return self._fmt_hms(self._dur_end[key] - self._dur_start[key])
        return ""

    def _format_progress(self, row) -> Tuple[int, str]:
        """Return (percent 0-100, label text) for the progress bar."""
        if row.state == CLIENT_VERIFIED:
            return 100, row.detail
        if row.state == CLIENT_RELEASED:
            return 100, row.detail
        if row.expected_bytes <= 0 or row.state != CLIENT_UPLOADING:
            return 0, row.detail

        pct = min(int(100 * row.sent_bytes / row.expected_bytes), 100)
        speed = self._compute_speed(row.key, row.sent_bytes)
        remaining = row.expected_bytes - row.sent_bytes
        parts = [f"{_human_bytes(row.sent_bytes)} / {_human_bytes(row.expected_bytes)}"]
        if speed and speed > 0:
            parts.append(f"{_human_bytes(int(speed))}/s")
            eta_s = remaining / speed
            if eta_s < 60:
                parts.append(f"~{int(eta_s)}s")
            elif eta_s < 3600:
                parts.append(f"~{int(eta_s / 60)}min")
            else:
                parts.append(f"~{eta_s / 3600:.1f}h")
        return pct, "  ".join(parts)

    def _current_step(self, snap) -> int:
        """Determine which step (1-4) the operator is currently on."""
        if not snap.rows:
            return 1
        states = [r.state for r in snap.rows if not r.is_error]
        if not states:
            return 1
        if any(s == CLIENT_UPLOADING for s in states):
            return 3
        if any(s == CLIENT_PENDING or s == CLIENT_INTERRUPTED for s in states):
            return 2
        if any(s == CLIENT_VERIFIED for s in states):
            return 4
        if all(s == CLIENT_RELEASED for s in states):
            return 4
        return 2

    def refresh(self) -> None:
        snap = self._manager.snapshot()
        for row in snap.rows:
            self._rows_by_key[row.key] = row
            self._upsert_row(row)

        # Summary
        self.lbl_summary.setText(
            "   ".join(s.header_text() for s in snap.summaries)
            if snap.summaries else ""
        )

        # Interrupted warning banner — show the REAL cause per card, not a
        # blanket "card removed" (which hid server-side rejections).
        interrupted = [r for r in snap.rows if r.state == CLIENT_INTERRUPTED]
        if interrupted:
            lines = []
            for r in sorted(interrupted, key=lambda r: r.table):
                if r.error_detail:
                    lines.append(f"Karte {r.table}: {r.error_detail}")
                else:
                    lines.append(
                        f"Karte {r.table}: entfernt — bitte wieder einstecken."
                    )
            self.lbl_warn.setText(
                "⚠  Upload unterbrochen:\n" + "\n".join(lines)
                + "\n\nDetails im Log: %USERPROFILE%\\.sts_upload\\sts_upload.log"
            )
            self.lbl_warn.setVisible(True)
        else:
            self.lbl_warn.setVisible(False)

        # Success banner
        if snap.all_done:
            self.lbl_success.setText(
                "✅  Alle Karten freigegeben — Pipeline läuft."
            )
            self.lbl_success.setVisible(True)
        else:
            self.lbl_success.setVisible(False)

        # Step indicator
        self._step_bar.set_step(self._current_step(snap))

        # Button enable/disable
        any_verified = any(r.state == CLIENT_VERIFIED for r in snap.rows)
        self.btn_release_all.setEnabled(any_verified)
        self.btn_release_sel.setEnabled(any_verified)

    def _upsert_row(self, row) -> None:
        idx = self._row_of.get(row.key)
        if idx is None:
            idx = self.table.rowCount()
            self.table.insertRow(idx)
            self.table.setRowHeight(idx, 36)
            self._row_of[row.key] = idx

            # Checkbox cell
            sel = QTableWidgetItem()
            sel.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            sel.setCheckState(Qt.Unchecked)
            self.table.setItem(idx, _COL_SEL, sel)

            # Plain-text cells
            for col in (_COL_TABLE, _COL_DURATION, _COL_STATUS, _COL_DATE,
                        _COL_SAFE):
                self.table.setItem(idx, col, QTableWidgetItem(""))

            # Discipline dropdown
            combo = QComboBox()
            combo.addItems(self._disciplines)
            combo.currentTextChanged.connect(
                lambda text, k=row.key: self.on_card_discipline_changed(k, text)
            )
            self._disc_combos[row.key] = combo
            self.table.setCellWidget(idx, _COL_DISC, combo)

            # Progress bar
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setTextVisible(True)
            self._prog_bars[row.key] = bar
            self.table.setCellWidget(idx, _COL_PROG, bar)

        # Table name
        self.table.item(idx, _COL_TABLE).setText(row.table)

        # Duration: runs live while uploading, freezes once verified/released.
        dur_item = self.table.item(idx, _COL_DURATION)
        dur_item.setText(self._duration_text(row))
        dur_item.setTextAlignment(Qt.AlignCenter)

        # Discipline dropdown
        combo = self._disc_combos[row.key]
        combo.blockSignals(True)
        if row.discipline and combo.findText(row.discipline) >= 0:
            combo.setCurrentText(row.discipline)
        combo.setEnabled(row.editable_discipline)
        combo.blockSignals(False)

        # Progress bar
        bar = self._prog_bars[row.key]
        pct, label = self._format_progress(row)
        bar.setValue(pct)
        bar.setFormat(label)
        if row.state == CLIENT_VERIFIED or row.state == CLIENT_RELEASED:
            bar.setStyleSheet("QProgressBar::chunk { background: #34a853; border-radius: 3px; }")
        elif row.state == CLIENT_FAILED or row.is_error:
            bar.setStyleSheet("QProgressBar::chunk { background: #ea4335; border-radius: 3px; }")
        elif row.state == CLIENT_INTERRUPTED:
            bar.setStyleSheet("QProgressBar::chunk { background: #fbbc04; border-radius: 3px; }")
        else:
            bar.setStyleSheet("")  # default blue

        # Status badge
        status_item = self.table.item(idx, _COL_STATUS)
        status_item.setText(row.badge)
        status_item.setData(Qt.UserRole, row.state)
        status_item.setTextAlignment(Qt.AlignCenter)

        # Date
        date_item = self.table.item(idx, _COL_DATE)
        date_text = row.date_range
        if row.stale_alarm:
            date_item.setToolTip(
                "Aufnahme-Ordner liegen mehr als 3 Tage auseinander —\n"
                "alte Daten? Doppelklick zum Auswählen der Ordner."
            )
            date_text = f"⚠ {date_text}".strip()
        date_item.setText(date_text)
        date_item.setTextAlignment(Qt.AlignCenter)

        # Safe-to-remove icon (compact)
        safe_item = self.table.item(idx, _COL_SAFE)
        if row.state in (CLIENT_VERIFIED, CLIENT_RELEASED):
            safe_item.setText("🔌")
            safe_item.setToolTip("Sicher entfernbar")
        elif row.state == CLIENT_UPLOADING:
            safe_item.setText("⛔")
            safe_item.setToolTip("Nicht entfernen — Upload läuft")
        else:
            safe_item.setText("")
            safe_item.setToolTip("")
        safe_item.setTextAlignment(Qt.AlignCenter)

        # Row background
        if row.is_error:
            bg = _ERROR_BG
        elif row.stale_alarm and row.state == CLIENT_PENDING:
            bg = _WARN_BG
        elif row.state == CLIENT_VERIFIED:
            bg = _DONE_BG
        elif row.state == CLIENT_RELEASED:
            bg = _DONE_BG
        elif row.state == CLIENT_UPLOADING:
            bg = _UPLOAD_BG
        else:
            bg = QColor(Qt.white)
        for col in (_COL_SEL, _COL_TABLE, _COL_DURATION, _COL_STATUS,
                    _COL_DATE, _COL_SAFE):
            cell = self.table.item(idx, col)
            if cell is not None:
                cell.setBackground(bg)
        bar.setProperty("bg_color", bg.name())

    def closeEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        shutdown = getattr(self._manager, "shutdown", None)
        if callable(shutdown):
            shutdown(wait=False)
        super().closeEvent(event)
