"""Login dialog: collect server URL + credentials, authenticate once.

The operator has no other input fields in the whole tool (§6a); this is
the single exception, shown at startup. On success the caller gets a
logged-in ApiClient. With "Angemeldet bleiben" the caller persists the
credentials so the next launch auto-logs-in and the dialog never appears
(see ``ui/app.py`` + ``credentials.py``).
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QCheckBox,
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
    def __init__(
        self,
        parent=None,
        *,
        default_server: str = DEFAULT_SERVER,
        prefill: Optional[dict] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("STS-Upload - Anmelden")
        self.api: Optional[ApiClient] = None
        # Exposed for the caller to persist on success.
        self.remember: bool = False
        self.server: str = ""
        self.username: str = ""
        self.password: str = ""

        pf = prefill or {}
        self._server = QLineEdit(pf.get("server") or default_server)
        self._user = QLineEdit(pf.get("username") or DEFAULT_USER)
        self._pw = QLineEdit(pf.get("password") or "")
        self._pw.setEchoMode(QLineEdit.Password)
        self._remember = QCheckBox(
            "Angemeldet bleiben (Passwort auf diesem Rechner merken)"
        )
        # If we pre-filled from a saved login, keep the box ticked by default.
        self._remember.setChecked(bool(pf))
        self._error = QLabel("")
        self._error.setStyleSheet("color: #b00020;")
        self._error.setVisible(False)

        form = QFormLayout(self)
        form.addRow("Server", self._server)
        form.addRow("Benutzer", self._user)
        form.addRow("Passwort", self._pw)
        form.addRow(self._remember)
        form.addRow(self._error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._try_login)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _try_login(self) -> None:
        server = self._server.text().strip()
        user = self._user.text().strip()
        pw = self._pw.text()
        api = ApiClient(server)
        try:
            api.login(user, pw)
        except ApiError as exc:
            self._error.setText(str(exc))
            self._error.setVisible(True)
            return
        self.api = api
        self.remember = self._remember.isChecked()
        self.server, self.username, self.password = server, user, pw
        self.accept()
