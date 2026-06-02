"""The main upload window - renders UploadManager state, drives the 3 clicks.

Layout follows the Issue #15 wireframe: a top action bar (Einlesen /
Hochladen / Freigeben + Auto-Release checkbox), one summary line per
discipline, a per-card table, a batch-release button and a success banner.

The window is a thin renderer: it polls ``manager.snapshot()`` on a timer
and updates rows in place (keyed by card, so checkbox selections survive a
refresh). It depends only on a duck-typed manager and a ``scan_fn`` so it
can be smoke-tested offscreen with a stub - the real mount detection is a
later Slice-2 block.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from upload_client.upload_engine import CLIENT_INTERRUPTED, CLIENT_VERIFIED

# columns
_COL_SEL, _COL_TABLE, _COL_DISC, _COL_STATE, _COL_DETAIL, _COL_REMOVE = range(6)
_HEADERS = ["", "Tisch", "Disziplin", "Status", "Fortschritt", "Entfernen"]

_ERROR_BG = QColor(0xFD, 0xE7, 0xE9)
_DONE_BG = QColor(0xE6, 0xF4, 0xEA)
_POLL_MS = 1500


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
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._scan_fn = scan_fn
        self._watcher = watcher
        self._row_of: Dict[str, int] = {}  # card key -> table row index

        title = "STS-Upload"
        if tournament_name:
            title += f" - {tournament_name}"
        self.setWindowTitle(title)
        self._build_ui()

        if resume_hint > 0:
            self.lbl_hint.setText(
                f"{resume_hint} Karte(n) aus einer frueheren Sitzung - "
                f"einstecken und 'Hochladen starten' zum Fortsetzen."
            )
            self.lbl_hint.setVisible(True)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(poll_ms)
        self._tick()

    # -- construction ------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)

        bar = QHBoxLayout()
        self.btn_scan = QPushButton("SD-Karten einlesen")
        self.btn_start = QPushButton("Hochladen starten")
        self.btn_release_all = QPushButton("Alle verifizierten freigeben")
        self.btn_scan.clicked.connect(self.on_scan)
        self.btn_start.clicked.connect(self.on_start)
        self.btn_release_all.clicked.connect(self.on_release_all)
        bar.addWidget(self.btn_scan)
        bar.addWidget(self.btn_start)
        bar.addWidget(self.btn_release_all)
        bar.addStretch(1)
        root.addLayout(bar)

        self.chk_auto = QCheckBox("Auto-Release nach Verifikation (ueberspringt manuellen Klick)")
        root.addWidget(self.chk_auto)

        self.lbl_hint = QLabel("")
        self.lbl_hint.setTextFormat(Qt.PlainText)
        self.lbl_hint.setStyleSheet("color: #1a73e8;")
        self.lbl_hint.setVisible(False)
        root.addWidget(self.lbl_hint)

        self.lbl_warn = QLabel("")
        self.lbl_warn.setTextFormat(Qt.PlainText)
        self.lbl_warn.setStyleSheet(
            "color: #b00020; font-weight: bold; "
            "background: #fde7e9; padding: 6px; border-radius: 4px;"
        )
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setVisible(False)
        root.addWidget(self.lbl_warn)

        self.lbl_summary = QLabel("")
        self.lbl_summary.setTextFormat(Qt.PlainText)
        root.addWidget(self.lbl_summary)

        self.table = QTableWidget(0, len(_HEADERS), self)
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(_COL_DETAIL, QHeaderView.Stretch)
        root.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.btn_release_sel = QPushButton("Markierte freigeben")
        self.btn_release_sel.clicked.connect(self.on_release_selected)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_release_sel)
        root.addLayout(bottom)

        self.lbl_success = QLabel("")
        self.lbl_success.setTextFormat(Qt.PlainText)
        self.lbl_success.setStyleSheet("color: #137333; font-weight: bold;")
        self.lbl_success.setVisible(False)
        root.addWidget(self.lbl_success)

        self.setCentralWidget(central)

    # -- actions -----------------------------------------------------------

    def on_scan(self) -> None:
        try:
            inventory = self._scan_fn()
        except Exception as exc:  # noqa: BLE001 - surface, never crash on scan
            QMessageBox.warning(self, "Einlesen fehlgeschlagen", str(exc))
            return
        self._manager.add_scanned(inventory)
        self.refresh()

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

    def _checked_releasable_keys(self) -> List[str]:
        keys: List[str] = []
        for key, row in self._row_of.items():
            item = self.table.item(row, _COL_SEL)
            state_item = self.table.item(row, _COL_STATE)
            if item is None or state_item is None:
                continue
            if (
                item.checkState() == Qt.Checked
                and state_item.data(Qt.UserRole) == CLIENT_VERIFIED
            ):
                keys.append(key)
        return keys

    # -- rendering ---------------------------------------------------------

    def _tick(self) -> None:
        """Timer step: poll mounts (if watching), then render."""
        if self._watcher is not None:
            change = self._watcher.poll()
            if change.removed:
                self._manager.handle_removed(change.removed)
            if change.changed:
                try:
                    self._manager.add_scanned(self._scan_fn())
                except Exception:  # noqa: BLE001 - never crash the poll loop
                    pass
        self.refresh()

    def refresh(self) -> None:
        snap = self._manager.snapshot()
        for row in snap.rows:
            self._upsert_row(row)

        self.lbl_summary.setText(
            "\n".join(s.header_text() for s in snap.summaries)
        )

        interrupted = [r.table for r in snap.rows if r.state == CLIENT_INTERRUPTED]
        if interrupted:
            self.lbl_warn.setText(
                "⚠ Karte(n) " + ", ".join(sorted(interrupted))
                + " entfernt / unterbrochen - bitte wieder einstecken; "
                "der Upload wird fortgesetzt."
            )
            self.lbl_warn.setVisible(True)
        else:
            self.lbl_warn.setVisible(False)

        if snap.all_done:
            self.lbl_success.setText(
                "✅ Alle erwarteten Karten freigegeben - Pipeline laeuft."
            )
            self.lbl_success.setVisible(True)
        else:
            self.lbl_success.setVisible(False)

    def _upsert_row(self, row) -> None:
        idx = self._row_of.get(row.key)
        if idx is None:
            idx = self.table.rowCount()
            self.table.insertRow(idx)
            self._row_of[row.key] = idx
            sel = QTableWidgetItem()
            sel.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            sel.setCheckState(Qt.Unchecked)
            self.table.setItem(idx, _COL_SEL, sel)
            for col in (_COL_TABLE, _COL_DISC, _COL_STATE, _COL_DETAIL, _COL_REMOVE):
                self.table.setItem(idx, col, QTableWidgetItem(""))

        self.table.item(idx, _COL_TABLE).setText(row.table)
        self.table.item(idx, _COL_DISC).setText(row.discipline)

        state_item = self.table.item(idx, _COL_STATE)
        state_item.setText(row.badge)
        state_item.setData(Qt.UserRole, row.state)

        detail_item = self.table.item(idx, _COL_DETAIL)
        detail_item.setText(row.detail)
        if row.error_detail:
            detail_item.setToolTip(row.error_detail)

        self.table.item(idx, _COL_REMOVE).setText(row.safe_to_remove)

        bg = _ERROR_BG if row.is_error else (
            _DONE_BG if row.state == CLIENT_VERIFIED else QColor(Qt.white)
        )
        for col in range(len(_HEADERS)):
            cell = self.table.item(idx, col)
            if cell is not None:
                cell.setBackground(bg)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._timer.stop()
        shutdown = getattr(self._manager, "shutdown", None)
        if callable(shutdown):
            shutdown(wait=False)
        super().closeEvent(event)
