"""Thin HTTP wrapper around the STS-Upload server contract (Slice 1).

One method per endpoint in ``docs/UPLOAD_CLIENT_HOWTO.md``. The wrapper
owns *only* request shaping and response parsing; all upload
orchestration / resume logic lives in ``upload_engine``. The HTTP session
is injectable (any object with ``requests``-style ``get``/``post``) so
the engine and the GUI can be tested without a network or a live NAS.

Auth uses the same login form + session cookie the web UI uses, so no new
credentials are needed (Issue #15, server contract).
"""

from __future__ import annotations

from typing import Any, BinaryIO, Dict, List, Optional

# (connect timeout, read timeout). Reads can be long: a 24 GB card chunk
# is one HTTP request, so the read timeout is generous.
DEFAULT_TIMEOUT = (10, 1800)


class ApiError(RuntimeError):
    """Any non-success response from the server."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        payload: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class AuthError(ApiError):
    """Login failed - bad credentials or unreachable login form."""


class ManifestRejected(ApiError):
    """``finish`` returned 422: file-count / byte manifest mismatch.

    ``card`` holds the server's card dict (state == 'failed') so the
    caller can surface a friendly retry message.
    """

    def __init__(self, card: Dict[str, Any]) -> None:
        msg = card.get("error_message") or "Manifest-Verifikation fehlgeschlagen"
        super().__init__(msg, status_code=422, payload=card)
        self.card = card


class ApiClient:
    """Client for the ``/api/upload/*`` endpoints."""

    def __init__(
        self,
        base_url: str,
        *,
        session: Optional[Any] = None,
        timeout: Any = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        if session is None:
            import requests  # local import: keeps the module importable
            session = requests.Session()
        self._session = session

    # -- helpers -----------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    @staticmethod
    def _payload(resp: Any) -> Optional[Any]:
        try:
            return resp.json()
        except Exception:  # noqa: BLE001 - non-JSON error body is fine
            return None

    def _error(self, resp: Any, *, what: str) -> ApiError:
        payload = self._payload(resp)
        detail = ""
        if isinstance(payload, dict) and payload.get("error"):
            detail = f": {payload['error']}"
        return ApiError(
            f"{what} fehlgeschlagen (HTTP {resp.status_code}){detail}",
            status_code=resp.status_code,
            payload=payload,
        )

    def _expect(self, resp: Any, *, what: str, codes) -> Any:
        if resp.status_code not in codes:
            raise self._error(resp, what=what)
        return self._payload(resp)

    # -- endpoints ---------------------------------------------------------

    def login(self, username: str, password: str) -> None:
        """POST the login form; success is a 302/303 redirect."""
        resp = self._session.post(
            self._url("/login"),
            data={"username": username, "password": password},
            allow_redirects=False,
            timeout=self.timeout,
        )
        if resp.status_code not in (302, 303):
            raise AuthError(
                f"Login fehlgeschlagen (HTTP {resp.status_code})",
                status_code=resp.status_code,
            )

    def active_tournament(self) -> Dict[str, Any]:
        """GET the active tournament per discipline + expected card counts."""
        resp = self._session.get(
            self._url("/api/upload/active-tournament"),
            timeout=self.timeout,
        )
        return self._expect(resp, what="Aktives Turnier laden", codes=(200,))

    def start_upload(
        self,
        *,
        tournament_id: int,
        discipline: str,
        table_name: str,
        expected_files: int,
        expected_bytes: int,
        auto_release: bool = False,
        card_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Reserve a staging dir + DB row. Returns the card dict (201)."""
        body: Dict[str, Any] = {
            "tournament_id": tournament_id,
            "discipline": discipline,
            "table_name": table_name,
            "expected_files": expected_files,
            "expected_bytes": expected_bytes,
            "auto_release": auto_release,
        }
        if card_uuid is not None:
            body["card_uuid"] = card_uuid
        resp = self._session.post(
            self._url("/api/upload/start"), json=body, timeout=self.timeout,
        )
        return self._expect(resp, what="Upload starten", codes=(201,))

    def upload_chunk(
        self,
        card_id: int,
        *,
        relative_name: str,
        fileobj: BinaryIO,
        filename: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upload one file (one chunk == one file in the MVP)."""
        files = {
            "file": (
                filename or relative_name,
                fileobj,
                "application/octet-stream",
            )
        }
        resp = self._session.post(
            self._url(f"/api/upload/{card_id}/chunk"),
            data={"relative_name": relative_name},
            files=files,
            timeout=self.timeout,
        )
        return self._expect(resp, what="Datei hochladen", codes=(200,))

    def finish_upload(self, card_id: int) -> Dict[str, Any]:
        """Verify the manifest. 200 -> verified/released; 422 -> raises."""
        resp = self._session.post(
            self._url(f"/api/upload/{card_id}/finish"), timeout=self.timeout,
        )
        if resp.status_code == 422:
            raise ManifestRejected(self._payload(resp) or {})
        return self._expect(resp, what="Upload abschliessen", codes=(200,))

    def release(self, card_ids: List[int]) -> List[Dict[str, Any]]:
        """Atomic rename for one or many verified cards. Returns the cards."""
        resp = self._session.post(
            self._url("/api/upload/release"),
            json={"card_ids": list(card_ids)},
            timeout=self.timeout,
        )
        data = self._expect(resp, what="Freigeben", codes=(200,))
        return list(data.get("released", []))

    def cancel(self, card_id: int) -> Dict[str, Any]:
        """Wipe staging + mark cancelled."""
        resp = self._session.post(
            self._url(f"/api/upload/{card_id}/cancel"), timeout=self.timeout,
        )
        return self._expect(resp, what="Abbrechen", codes=(200,))

    def status(
        self,
        *,
        discipline: Optional[str] = None,
        tournament_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Snapshot of all cards (optionally filtered) for UI polling."""
        params: Dict[str, Any] = {}
        if discipline is not None:
            params["discipline"] = discipline
        if tournament_id is not None:
            params["tournament_id"] = tournament_id
        resp = self._session.get(
            self._url("/api/upload/status"),
            params=params or None,
            timeout=self.timeout,
        )
        data = self._expect(resp, what="Status laden", codes=(200,))
        return list(data.get("cards", []))
