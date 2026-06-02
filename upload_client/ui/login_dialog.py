"""Login dialog: collect server URL + credentials, authenticate once.

The operator has no other input fields in the whole tool (§6a); this is
the single exception, shown at startup. On success the caller gets a
logged-in ApiClient.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
)

from upload_client.api_client import ApiClient, ApiError

DEFAULT_SERVER = "http://192.168.1.159:8080"
DEFAULT_USER = "admin"


class LoginDialog(QDialog):
    def __init__(self, parent=None, *, default_server: str = DEFAULT_SERVER) -> None:
        super().__init__(parent)
        self.setWindowTitle("STS-Upload - Anmelden")
        self.api: Optional[ApiClient] = None

        self._server = QLineEdit(default_server)
        self._user = QLineEdit(DEFAULT_USER)
        self._pw = QLineEdit()
        self._pw.setEchoMode(QLineEdit.Password)
        self._error = QLabel("")
        self._error.setStyleSheet("color: #b00020;")
        self._error.setVisible(False)

        form = QFormLayout(self)
        form.addRow("Server", self._server)
        form.addRow("Benutzer", self._user)
        form.addRow("Passwort", self._pw)
        form.addRow(self._error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._try_login)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _try_login(self) -> None:
        api = ApiClient(self._server.text().strip())
        try:
            api.login(self._user.text().strip(), self._pw.text())
        except ApiError as exc:
            self._error.setText(str(exc))
            self._error.setVisible(True)
            return
        self.api = api
        self.accept()
