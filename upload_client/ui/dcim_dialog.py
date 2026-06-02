"""Dialog to pick which DCIM sub-folders of a card get uploaded.

Opened when a card's recording folders are more than 3 days apart (a stale
alarm): the operator sees each ``DCIM/<sub>`` folder with its creation date
and video count, and ticks the ones to ingest. The newest cluster is
pre-selected; older folders are off by default.

Kept thin and Qt-only-at-the-edges so the selection logic is testable
offscreen: construct it, toggle the check boxes, read ``selected_names()``
- no modal ``exec()`` needed in tests.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Set

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class DcimSelectionDialog(QDialog):
    """Checkbox list of DCIM folders; returns the chosen folder names."""

    def __init__(
        self,
        folders: Iterable,           # Iterable[dcim.DcimFolder]
        selected: Optional[Set[str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Aufnahme-Ordner waehlen")
        selected = set(selected or set())
        self._boxes: List[QCheckBox] = []

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Die Aufnahme-Ordner liegen zeitlich weit auseinander - evtl. "
            "alte Daten. Waehle, welche eingelesen werden:"
        ))
        for folder in folders:
            date = folder.created.strftime("%d.%m.%Y %H:%M")
            box = QCheckBox(
                f"{folder.name}  ({date}, {folder.video_count} Videos)"
            )
            box.setChecked(folder.name in selected)
            box.setProperty("folder_name", folder.name)
            self._boxes.append(box)
            layout.addWidget(box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_names(self) -> Set[str]:
        """The folder names whose box is ticked."""
        return {
            str(b.property("folder_name"))
            for b in self._boxes
            if b.checkState() == Qt.Checked
        }
