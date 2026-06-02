"""Offscreen tests for the DCIM sub-folder selection dialog."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from upload_client.dcim import DcimFolder  # noqa: E402
from upload_client.ui.dcim_dialog import DcimSelectionDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _folder(name: str, dt: datetime) -> DcimFolder:
    return DcimFolder(path=Path(name), name=name, created=dt,
                      video_count=2, video_bytes=20)


def test_dialog_preselects_and_reads_back(qapp):
    folders = [
        _folder("OLD", datetime(2026, 1, 1, 9, 0)),
        _folder("NEW", datetime(2026, 5, 22, 17, 0)),
    ]
    dlg = DcimSelectionDialog(folders, selected={"NEW"})
    # Pre-selection reflects the passed set.
    assert dlg.selected_names() == {"NEW"}
    # Operator also ticks OLD.
    dlg._boxes[0].setCheckState(Qt.Checked)
    assert dlg.selected_names() == {"OLD", "NEW"}
    # And unticks NEW.
    dlg._boxes[1].setCheckState(Qt.Unchecked)
    assert dlg.selected_names() == {"OLD"}
